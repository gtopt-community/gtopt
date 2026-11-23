# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case tests for the analyser helpers, the console renderer and CLI."""

from __future__ import annotations

import io
import json
import runpy
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from gtopt_check_topology import _analyzer as an
from gtopt_check_topology._analyzer import (
    AnalysisReport,
    BatteryEiniIssue,
    CycleInfo,
    IslandFeasibility,
    IslandInfo,
    LineRef,
    NegativeImpedance,
    ParallelCircuitIdentical,
    ReservoirCascadeIssue,
    ReserveZoneIssue,
    TimeVaryingCapacityIssue,
    VoltageMix,
    analyse_planning,
)
from gtopt_check_topology._temporal import ResolvedModel
from gtopt_check_topology._render import (
    _cycle_to_dict,
    _fmt_mw,
    _fmt_ratio,
    render_console_report,
    report_to_json,
)
from gtopt_check_topology.main import main

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _render(report: AnalysisReport, **kwargs: Any) -> str:
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, width=200)
    render_console_report(report, console=console, **kwargs)
    return buf.getvalue()


def _ref(uid: int, a: str = "a", b: str = "b") -> LineRef:
    return LineRef(
        uid=uid,
        name=f"L{uid}",
        bus_a=a,
        bus_b=b,
        reactance=0.1,
        resistance=0.0,
        tmax=100.0,
    )


def _cycle(score: int, n_buses: int = 3, asym: float = 2.0) -> CycleInfo:
    buses = [f"b{i}" for i in range(n_buses)]
    return CycleInfo(
        buses=buses,
        edges=[],
        segments=[_ref(1)],
        segments_all=[_ref(1), _ref(1), _ref(2)],
        asym_x=asym,
        asym_tmax=float("inf"),
        min_tmax=50.0,
        n_buses=n_buses,
        score=score,
        kvl_saturation_threshold_mw=float("inf"),
    )


def _big_report() -> AnalysisReport:
    """A report with every category populated beyond the display caps."""
    many = 25
    return AnalysisReport(
        bundle="",
        n_buses=30,
        n_lines=40,
        n_cycles=8,
        islands=[
            IslandInfo(
                buses=[f"x{i}" for i in range(8)], has_generator=True, has_demand=True
            ),
            IslandInfo(buses=["y"], has_generator=False, has_demand=True),
        ],
        isolated_buses=[f"iso{i}" for i in range(many)],
        stubs=[_ref(i) for i in range(3)],
        cycles=[_cycle(20, n_buses=6, asym=150.0)] * 6
        + [_cycle(0, asym=15.0), _cycle(1, asym=3.0)],
        bridges=[_ref(i) for i in range(12)],
        dc_lines=[_ref(i) for i in range(12)],
        tie_lines=[_ref(i) for i in range(12)],
        negative_impedance=[
            NegativeImpedance(uid=1, name="neg", reactance=-1.0, resistance=0.0)
        ],
        voltage_mixing=[
            VoltageMix(
                uid=i,
                name=f"vm{i}",
                bus_a="a110",
                bus_b="b220",
                bus_a_voltage=110,
                bus_b_voltage=220,
            )
            for i in range(12)
        ],
        reserve_zone_coherence=[
            ReserveZoneIssue(zone=f"z{i}", issue="no providers", n_providers=0)
            for i in range(many)
        ],
        island_feasibility=[
            IslandFeasibility(
                island_idx=i,
                n_demand_buses=1,
                n_gen_buses=0,
                sum_pmax=0.0,
                sum_peak_load=10.0,
                issue="demand without generation",
            )
            for i in range(many)
        ],
        battery_eini_issues=[
            BatteryEiniIssue(
                name=f"bat{i}",
                issue="x",
                eini=0.0,
                emin_first=None,
                emax_first=None,
                in_ucs=["u1", "u2", "u3", "u4"] if i == 0 else [],
            )
            for i in range(many)
        ],
        time_varying_capacity_issues=[
            TimeVaryingCapacityIssue(
                uid=i,
                name=f"tv{i}",
                bus_a="a",
                bus_b="b",
                blocks_at_zero=3,
                blocks_near_zero=0,
                blocks_total=4,
                max_tmax=10.0,
            )
            for i in range(many)
        ],
        reservoir_cascade_issues=[
            ReservoirCascadeIssue(name=f"r{i}", issue="i", detail="d" if i else "")
            for i in range(many)
        ],
        parallel_circuit_identical=[
            ParallelCircuitIdentical(
                bus_a="a",
                bus_b="b",
                n_circuits=2,
                reactance=0.1,
                tmax=10.0,
                uids=[1, 2],
                on_dangerous_cycle=i == 0,
            )
            for i in range(many)
        ],
        danger_threshold=5,
    )


# ---------------------------------------------------------------------------
# analyser helpers
# ---------------------------------------------------------------------------


def test_to_max_float_variants():
    assert an._to_max_float(None) is None
    assert an._to_max_float(True) == 1.0
    assert an._to_max_float(3) == 3.0
    assert an._to_max_float([[1, 5], [2, True, "x"]]) == 5.0
    assert an._to_max_float([["x"]]) is None
    assert an._to_max_float("file.parquet") is None


def test_first_numeric_variants():
    assert an._first_numeric(None) is None
    assert an._first_numeric(False) == 0.0
    assert an._first_numeric(2) == 2.0
    assert an._first_numeric(["x", [None, 4.0], 5]) == 4.0
    assert an._first_numeric(["x"]) is None
    assert an._first_numeric("s") is None


def test_ratio_and_voltage_helpers():
    assert an._ratio([]) == 1.0
    assert an._ratio([0.0, 2.0]) == float("inf")
    assert an._ratio([1.0, 4.0]) == 4.0
    assert an._bus_voltage_from_name("Concepcion110") == 110
    assert an._bus_voltage_from_name("NoDigits") is None


def test_build_graph_skips_bad_lines():
    plan = {
        "system": {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}, {}],
            "line_array": [
                {"uid": 1, "bus_a": "a"},  # missing bus_b
                {"uid": 2, "bus_a": "a", "bus_b": "zz", "reactance": 0.1},
                {"uid": 3, "bus_a": "a", "bus_b": "a", "reactance": 0.1},
                {"uid": 4, "bus_a": "a", "bus_b": "b", "reactance": 0.1},
            ],
        }
    }
    net = an.build_network_graph(plan)
    assert [r.uid for r in net.skipped_unknown_bus] == [1, 2]
    assert [r.uid for r in net.self_loops] == [3]
    assert net.graph.number_of_edges() == 1


_TWO_STAGES: dict[str, Any] = {
    "simulation": {
        "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
        "stage_array": [
            {"uid": 1, "first_block": 0, "count_block": 1},
            {"uid": 2, "first_block": 1, "count_block": 1},
        ],
    }
}


def _resolve(system: dict[str, Any]) -> ResolvedModel:
    return an.resolve_planning({**_TWO_STAGES, "system": system}, ".")


def test_generator_demand_maps_resolve_uids():
    model = _resolve(
        {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2}, {"name": "c"}],
            "generator_array": [
                {"bus": 1},
                {"bus": None},
                {"bus": 99},
                {"bus": "d", "active": 0},  # never active -> not counted
            ],
            "battery_array": [{"bus": "c"}, {"bus": None}],
            "demand_array": [
                {"bus": 1},
                {"bus": "c"},
                {"bus": None},
                {"bus": 42},
                {"bus": "e", "active": [0, 0]},  # inactive in every stage
            ],
        }
    )
    # Known uids resolve to the bus name; unknown uids keep their str()
    # form; bus-less elements are dropped.
    assert an._bus_has_generator_map(model) == {"a": True, "99": True, "c": True}
    assert an._bus_has_demand_map(model) == {"a": True, "c": True, "42": True}


def test_pmax_and_peak_per_bus():
    model = _resolve(
        {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2}],
            "generator_array": [
                {"bus": 1, "pmax": 10},
                {"bus": None, "pmax": 5},
                {"bus": 7, "pmax": 5},
                {"bus": "a", "pmax": 0},
                {"bus": "a"},  # no capacity field -> contributes 0
            ],
            "battery_array": [
                {"bus": "a", "pmax_discharge": [1, 3]},
                {"bus": None, "pmax_discharge": 3},
                {"bus": "b", "pmax_discharge": 0},
            ],
            "demand_array": [
                {"bus": 1, "lmax": 4},
                {"bus": "a", "lmax": [6, 2]},
                {"bus": "a", "lmax": 1},
                {"bus": 9, "lmax": 4},
                {"bus": None, "lmax": 4},
                {"bus": "b", "lmax": 0},
                # ``capacity`` fallback, per-block -> max over blocks/stages
                {"bus": "b", "capacity": [[1, 7], [2, 3]]},
            ],
        }
    )
    assert an._pmax_per_bus(model) == {"a": 13.0, "7": 5.0, "b": 0.0}
    assert an._peak_load_per_bus(model) == {"a": 11.0, "9": 4.0, "b": 7.0}


def test_voltage_mixing_skips_and_transformer_hint():
    buses: list[dict[str, Any]] = [
        {"uid": 1, "name": "a110"},
        {"name": "b220"},
        {"name": "c110"},
        {},
        {"name": "x"},
    ]
    model = _resolve(
        {
            "bus_array": buses,
            "line_array": [
                {"uid": 1, "bus_a": 1, "bus_b": "b220"},  # uid ref resolved
                {"uid": 2, "bus_a": "a110", "bus_b": "c110"},  # same voltage
                {"uid": 3, "bus_a": "a110", "bus_b": "b220", "voltage": 1.05},
                {"uid": 4, "bus_a": "a110", "bus_b": "b220", "voltage": 0},
                # scheduled tap: non-zero in some stage -> transformer
                {"uid": 5, "bus_a": "a110", "bus_b": "b220", "voltage": [0, 1]},
                {"uid": 6, "bus_a": "x", "bus_b": "b220"},  # no kV in name
            ],
        }
    )
    out = an._collect_voltage_mixing(buses, model.lines)
    assert [m.uid for m in out] == [1, 4]
    assert (out[0].bus_a, out[0].bus_a_voltage, out[0].bus_b_voltage) == (
        "a110",
        110,
        220,
    )


def test_cycles_size_cap_zero_tmax_and_bridge_dedup():
    buses = ["a", "b", "c", "d"]
    lines = [
        {"uid": 1, "bus_a": "a", "bus_b": "b", "reactance": 0.05, "tmax_ab": 0},
        {"uid": 2, "bus_a": "b", "bus_b": "c", "reactance": 0.1, "tmax_ab": 10},
        {"uid": 3, "bus_a": "c", "bus_b": "a", "reactance": 0.1, "tmax_ab": 10},
        {"uid": 4, "bus_a": "c", "bus_b": "d", "reactance": 0.1, "tmax_ab": 10},
        {"uid": 4, "bus_a": "d", "bus_b": "c", "reactance": 0.2, "tmax_ab": 10},
    ]
    plan = {
        "system": {
            "bus_array": [{"uid": i, "name": n} for i, n in enumerate(buses)],
            "line_array": lines,
        }
    }
    rep = analyse_planning(plan)
    assert len(rep.cycles) == 1
    cyc = rep.cycles[0]
    # min-X edge has tmax 0 -> no saturation estimate, asym_tmax infinite.
    assert cyc.kvl_saturation_threshold_mw is None
    assert cyc.asym_tmax == float("inf")
    # duplicated uid on the bridge/stub only reported once.
    assert [r.uid for r in rep.bridges] == [4]
    assert [r.uid for r in rep.stubs] == [4]
    # max_cycle_size=2 drops the triangle.
    assert not analyse_planning(plan, max_cycle_size=2).cycles


def test_sos1_candidates_dedup():
    rep = AnalysisReport(
        bundle="b",
        n_buses=0,
        n_lines=0,
        n_cycles=1,
        cycles=[_cycle(10)],
    )
    assert [r.uid for r in rep.sos1_candidates] == [1, 2]


def test_reserve_zone_coherence_variants():
    plan = {
        "system": {
            "reserve_zone_array": [{"name": 3}, {"name": "z1"}, {"name": "z2"}],
            "reserve_provision_array": [
                {"reserve_zones": ["z1", 7], "urmax": 0, "drmax": [0, 5]},
                {"reserve_zones": "z2", "up_max": None},
                {"reserve_zones": "z2", "dn_max": 2},
                {"reserve_zones": None},
            ],
        }
    }
    assert not an._collect_reserve_zone_coherence(plan)


def test_island_feasibility_variants():
    islands = [
        IslandInfo(buses=["g"], has_generator=True, has_demand=False),
        IslandInfo(buses=["s1", "s2"], has_generator=True, has_demand=True),
        IslandInfo(buses=["o1", "o2"], has_generator=True, has_demand=True),
        IslandInfo(buses=["z1", "z2"], has_generator=True, has_demand=True),
        IslandInfo(buses=["e"], has_generator=False, has_demand=False),
    ]
    pmax = {"g": 10.0, "s2": 5.0, "o2": 100.0, "z2": 1.0}
    peak = {"s1": 10.0, "o1": 10.0}
    has_dem = {"s1": True, "o1": True, "z1": True}
    out = an._collect_island_feasibility(islands, pmax, peak, has_dem)
    assert [(i.island_idx, i.issue) for i in out] == [
        (0, "generation without demand (curtailment-only)"),
        (1, "gen capacity below peak load + 10% reserve"),
    ]


def test_batteries_in_uc_expressions_and_pampl(tmp_path: Path):
    (tmp_path / "uc.pampl").write_text(
        'battery("B0") + 1 <= 2\n'
        "constraint c1:\n"
        '  battery("B1") <= 3\n'
        '  battery("B1") >= 0\n'
        "  other line\n",
        encoding="utf-8",
    )
    plan = {
        "_pampl_dir": str(tmp_path),
        "system": {
            "user_constraint_array": [
                {"name": "u1", "expression": 'battery("B1") <= 1'},
                {"name": "u2", "expression": 5},
                {"expression": None},
            ],
            "user_constraint_files": [3, "missing.pampl", "uc.pampl"],
        },
    }
    out = an._batteries_in_uc_expressions(plan)
    assert out == {"B1": ["u1", "c1"], "B0": ["<uc.pampl>"]}


def test_battery_eini_issue_variants():
    plan = {
        "system": {
            "battery_array": [
                {"name": 1},
                {"name": "no_eini_bad", "emin": 5, "emax": 1},
                {"name": "no_eini_ok", "emin": 0, "emax": 1},
                {"name": "low", "eini": 0.5, "emin": [1, 0], "emax": 5},
                {"name": "daily0", "eini": 0, "emax": 5, "daily_cycle": True},
                {"name": "daily_ok", "eini": 2, "emax": 5, "daily_cycle": True},
                {"name": "fine", "eini": 2, "emax": 5},
            ]
        }
    }
    out = an._collect_battery_eini_issues(plan)
    assert [(b.name, b.issue) for b in out] == [
        ("no_eini_bad", "emin > emax (data error)"),
        ("low", "eini < emin (initial SoC below floor)"),
        ("daily0", "daily_cycle=true with inconsistent eini"),
    ]


def test_time_varying_capacity_variants():
    model = _resolve(
        {
            "bus_array": [{"name": "a"}, {"name": "b"}],
            "line_array": [
                {"uid": 1, "tmax_ab": 5},  # scalar -> out of scope
                {"uid": 2, "tmax_ab": ["x"]},  # no numeric leaf
                {"uid": 3, "tmax_ab": [0, 0]},  # all zero
                {"uid": 4, "tmax_ab": [0, 0, 0, 100, 1]},
                {"uid": 5, "tmax_ab": [0, 100, 100]},  # mostly non-zero
            ],
        }
    )
    out = an._collect_time_varying_capacity_issues(model.lines)
    assert [(i.uid, i.max_tmax, i.blocks_near_zero) for i in out] == [
        (3, 0.0, 2),
        (4, 100.0, 1),
    ]


def test_reservoir_cascade_variants():
    plan = {
        "system": {
            "junction_array": [{"name": "j1"}, {"name": "j2"}, {"name": 3}],
            "waterway_array": [
                {"uid": 9, "junction_a": "j1"},
                {"name": "w1", "junction_a": "jx", "junction_b": "jy"},
                {"name": "w2", "junction_a": "j1", "junction_b": "j2"},
            ],
            "reservoir_array": [
                {"name": 5},
                {"name": "r_nojunc"},
                {"name": "r_up", "junction": "j1", "eini": 10},
                {"name": "r_down", "junction": "j2"},
                {"name": "r_far", "junction": "j9", "eini": [0]},
            ],
        }
    }
    out = an._collect_reservoir_cascade_issues(plan)
    got = [(i.name, i.issue) for i in out]
    assert got == [
        ("9", "waterway missing junction_a/junction_b"),
        ("w1", "waterway references unknown junction"),
        ("w1", "waterway references unknown junction"),
        ("r_nojunc", "reservoir missing junction reference"),
        ("r_down", "no outflow path (water stranded)"),
        ("r_far", "no inflow source and no initial volume"),
        ("r_far", "no outflow path (water stranded)"),
    ]


def test_parallel_identical_skips_non_identical():
    plan = {
        "system": {
            "bus_array": [{"name": "a"}, {"name": "b"}],
            "line_array": [
                {"uid": 1, "bus_a": "a", "bus_b": "b", "reactance": 0.1},
                {"uid": 2, "bus_a": "a", "bus_b": "b", "reactance": 0.2},
            ],
        }
    }
    assert not analyse_planning(plan).parallel_circuit_identical


# ---------------------------------------------------------------------------
# renderer
# ---------------------------------------------------------------------------


def test_format_helpers():
    assert _fmt_ratio(float("inf")) == "inf"
    assert _fmt_ratio(150.0) == "150x"
    assert _fmt_ratio(15.0) == "15.0x"
    assert _fmt_ratio(1.5) == "1.50x"
    assert _fmt_mw(float("nan")) == "n/a"
    assert _fmt_mw(12.4) == "12 MW"


def test_render_everything_flagged():
    text = _render(_big_report(), include_benign_loops=True)
    assert "<planning>" in text
    assert "(no generator)" in text
    assert "... (+2)" in text  # island bus preview cap
    assert "(+5)" in text  # 25 items capped at 20
    assert "(+2)" in text  # 12 items capped at 10
    assert "Top 5 by danger score" in text
    assert "(6 buses)" in text
    assert "B3. Benign loops: 2" in text
    assert "[in: u1, u2, u3...]" in text
    assert "-- d" in text
    assert "(on dangerous cycle)" in text
    for word in (
        "dangerous cycles",
        "bridges",
        "voltage-mixing",
        "islands without generator",
        "negative-R/X",
        "reserve-zone",
        "island-feasibility",
        "battery-eini",
        "time-varying",
        "reservoir-cascade",
        "identical-parallel",
    ):
        assert word in text


def test_render_quiet_clean_report():
    rep = AnalysisReport(bundle="x.json", n_buses=0, n_lines=0, n_cycles=0)
    assert "ok" not in _render(rep, quiet=True).split("Summary")[0].lower().split()
    text = _render(rep)
    assert "1 connected component, 0/0" in text
    assert "no issues" in text


def test_render_quiet_flagged_stubs_and_benign_only():
    rep = AnalysisReport(
        bundle="x",
        n_buses=2,
        n_lines=1,
        n_cycles=1,
        islands=[
            IslandInfo(buses=["a"], has_generator=True, has_demand=True),
            IslandInfo(buses=["b"], has_generator=True, has_demand=True),
        ],
        stubs=[_ref(1)],
        cycles=[_cycle(0)],
        parallel_circuit_identical=[
            ParallelCircuitIdentical(
                bus_a="a",
                bus_b="b",
                n_circuits=2,
                reactance=0.1,
                tmax=1.0,
                uids=[1, 2],
                on_dangerous_cycle=False,
            )
        ],
    )
    quiet = _render(rep, quiet=True)
    assert "Stub lines" not in quiet
    assert "No dangerous loops" not in quiet
    loud = _render(rep)
    assert "Stub lines (degree-1 endpoint): 1" in loud
    assert "No dangerous loops" in loud
    assert "info B6." in loud


def test_render_flagged_below_display_caps():
    big = _big_report()
    small = AnalysisReport(
        bundle="s",
        n_buses=1,
        n_lines=1,
        n_cycles=0,
        islands=[IslandInfo(buses=["y"], has_generator=False, has_demand=True)],
        isolated_buses=["y"],
        bridges=big.bridges[:1],
        dc_lines=big.dc_lines[:1],
        tie_lines=big.tie_lines[:1],
        voltage_mixing=big.voltage_mixing[:1],
        reserve_zone_coherence=big.reserve_zone_coherence[:1],
        island_feasibility=big.island_feasibility[:1],
        battery_eini_issues=big.battery_eini_issues[:1],
        time_varying_capacity_issues=big.time_varying_capacity_issues[:1],
        reservoir_cascade_issues=big.reservoir_cascade_issues[:1],
        parallel_circuit_identical=big.parallel_circuit_identical[:1],
    )
    text = _render(small, quiet=True)
    assert "(+" not in text
    assert "island(s) have NO generator" in text


def test_render_default_console(capsys):
    render_console_report(AnalysisReport(bundle="z", n_buses=0, n_lines=0, n_cycles=0))
    assert "TOPOLOGY ANALYSIS: z" in capsys.readouterr().out


def test_cycle_to_dict_infinities():
    d = _cycle_to_dict(_cycle(1, asym=float("inf")))
    assert d["asym_X"] is None
    assert d["asym_tmax"] is None
    assert d["kvl_saturation_threshold_mw"] is None
    payload = report_to_json(_big_report())
    json.dumps(payload)
    assert payload["negative_impedance"][0]["name"] == "neg"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_plan(path: Path, plan: Any) -> Path:
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_cli_missing_and_invalid_files(tmp_path: Path, capsys):
    assert main([str(tmp_path / "nope.json")]) == 1
    assert "file not found" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main([str(bad)]) == 1
    lst = _write_plan(tmp_path / "list.json", [1, 2])
    assert main([str(lst)]) == 1
    assert "not an object" in capsys.readouterr().err


def _fatal_plan() -> dict[str, Any]:
    return {
        "system": {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}],
            "line_array": [
                {"uid": 1, "bus_a": "a", "bus_b": "b", "reactance": -0.1},
            ],
        }
    }


def test_cli_quiet_outputs_and_fatal_exit(tmp_path: Path, capsys):
    plan = _write_plan(tmp_path / "p.json", _fatal_plan())
    out_json = tmp_path / "r.json"
    strict = tmp_path / "s.json"
    legacy = tmp_path / "l.json"
    with pytest.warns(DeprecationWarning):
        rc = main(
            [
                str(plan),
                "--quiet",
                "--no-color",
                "--json-out",
                str(out_json),
                "--strict-direction-lines",
                str(strict),
                "--emit-sos1",
                str(legacy),
            ]
        )
    assert rc == 2
    assert out_json.exists() and strict.exists() and legacy.exists()
    assert "Wrote" not in capsys.readouterr().out


def test_cli_both_strict_flags_verbose(tmp_path: Path, capsys):
    plan = _write_plan(tmp_path / "p.json", _fatal_plan())
    with pytest.warns(DeprecationWarning):
        main(
            [
                str(plan),
                "--strict-direction-lines",
                str(tmp_path / "s.json"),
                "--emit-sos1",
                str(tmp_path / "l.json"),
            ]
        )
    assert "via deprecated --emit-sos1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "module", ["gtopt_check_topology", "gtopt_check_topology.main"]
)
def test_module_entry_points(tmp_path: Path, monkeypatch, module: str):
    plan = _write_plan(tmp_path / "p.json", {"system": {}})
    monkeypatch.setattr(sys, "argv", ["prog", str(plan), "--quiet"])
    with warnings.catch_warnings():
        # runpy warns when re-executing an already-imported module.
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module(module, run_name="__main__")
    assert exc.value.code == 0
