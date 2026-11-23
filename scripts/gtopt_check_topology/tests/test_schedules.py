# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Schedule-aware topology: file-backed fields and per-stage islands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_check_topology._analyzer import analyse_planning, build_network_graph
from gtopt_check_topology._render import report_to_json
from gtopt_check_topology.main import main
from gtopt_shared.field_sched import StageSchedResolver, build_table_path

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_long(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if path.suffix == ".csv":
        df.to_csv(path, index=False)
    else:
        df.to_parquet(path, index=False)


def _case(
    buses: list[str],
    lines: list[dict[str, Any]],
    *,
    n_stages: int = 3,
    generators: list[dict[str, Any]] | None = None,
    demands: list[dict[str, Any]] | None = None,
    bus_extra: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bus_extra = bus_extra or {}
    return {
        "options": {"input_directory": "input", "input_format": "parquet"},
        "simulation": {
            "block_array": [{"uid": s + 1, "duration": 1} for s in range(n_stages)],
            "stage_array": [
                {"uid": s + 1, "first_block": s, "count_block": 1}
                for s in range(n_stages)
            ],
        },
        "system": {
            "bus_array": [
                {"uid": i + 1, "name": b, **bus_extra.get(b, {})}
                for i, b in enumerate(buses)
            ],
            "line_array": lines,
            "generator_array": generators or [],
            "demand_array": demands or [],
        },
    }


def _line(uid: int, a: str, b: str, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "uid": uid,
        "name": f"{a}->{b}",
        "bus_a": a,
        "bus_b": b,
        "reactance": 0.1,
        "tmax_ab": 100.0,
        "tmax_ba": 100.0,
    }
    out.update(kw)
    return out


def _gen(uid: int, bus: str, pmax: Any = 50.0, **kw: Any) -> dict[str, Any]:
    return {"uid": uid, "name": f"g{uid}", "bus": bus, "pmax": pmax, **kw}


def _dem(uid: int, bus: str, lmax: Any = 20.0, **kw: Any) -> dict[str, Any]:
    return {"uid": uid, "name": f"d{uid}", "bus": bus, "lmax": lmax, **kw}


def _write_case(tmp_path: Path, plan: dict[str, Any]) -> Path:
    path = tmp_path / "case.json"
    path.write_text(json.dumps(plan))
    return path


# ---------------------------------------------------------------------------
# StageSchedResolver
# ---------------------------------------------------------------------------


def test_resolver_scalar_inline_and_nested():
    res = StageSchedResolver(".", [1, 2, 3])
    assert res.per_stage("Line", 0.5, 1) == [0.5, 0.5, 0.5]
    assert res.per_stage("Line", True, 1) == [1.0, 1.0, 1.0]
    assert res.per_stage("Line", [1, 0, 1], 1) == [1.0, 0.0, 1.0]
    nested = [[10, 20], [0, 0], [5, 30]]
    assert res.per_stage("Line", nested, 1) == [20.0, 0.0, 30.0]
    assert res.per_stage("Line", nested, 1, reduce="min") == [10.0, 0.0, 5.0]
    assert res.per_stage("Line", None, 1, default=7.0) == [7.0, 7.0, 7.0]


def test_resolver_file_long_layout_with_blocks(tmp_path: Path):
    _write_long(
        tmp_path / "Line" / "tmax.parquet",
        [
            {"stage": 1, "block": 1, "uid": 5, "value": 10.0},
            {"stage": 1, "block": 2, "uid": 5, "value": 30.0},
            {"stage": 2, "block": 3, "uid": 5, "value": 0.0},
        ],
    )
    res = StageSchedResolver(tmp_path, [1, 2, 3])
    assert res.per_stage("Line", "tmax", 5) == [30.0, 0.0, None]
    assert res.per_stage("Line", "tmax", 5, reduce="min") == [10.0, 0.0, None]
    # uid missing from the table -> default + recorded.
    assert res.per_stage("Line", "tmax", 6, default=1.0) == [1.0, 1.0, 1.0]
    assert res.missing_uids


def test_resolver_csv_class_redirect_and_missing_file(tmp_path: Path):
    _write_long(
        tmp_path / "Other" / "x.csv",
        [{"stage": 1, "uid": 1, "value": 2.0}, {"stage": 2, "uid": 1, "value": 3.0}],
    )
    res = StageSchedResolver(tmp_path, [1, 2])
    assert res.per_stage("Line", "Other@x", 1) == [2.0, 3.0]
    assert res.per_stage("Line", "nope", 1, default=0.0) == [0.0, 0.0]
    assert build_table_path(tmp_path, "Line", "nope") in res.missing_files


def test_resolver_from_planning_uses_input_directory(tmp_path: Path):
    plan = _case(["A"], [])
    res = StageSchedResolver.from_planning(plan, tmp_path)
    assert res.input_root == tmp_path / "input"
    assert res.stage_uids == [1, 2, 3]


# ---------------------------------------------------------------------------
# File-backed line fields (the original crash)
# ---------------------------------------------------------------------------


def test_file_backed_reactance_active_tmax_resolved(tmp_path: Path):
    inp = tmp_path / "input"
    _write_long(
        inp / "Line" / "x.parquet",
        [{"stage": s, "uid": 1, "value": v} for s, v in ((1, 0.3), (2, 0.2), (3, 0.4))],
    )
    _write_long(
        inp / "Line" / "act.parquet",
        [{"stage": s, "uid": 1, "value": v} for s, v in ((1, 1), (2, 0), (3, 1))],
    )
    _write_long(
        inp / "Line" / "tmax.parquet",
        [
            {"stage": s, "block": s, "uid": 1, "value": v}
            for s, v in ((1, 80.0), (2, 90.0), (3, 120.0))
        ],
    )
    plan = _case(
        ["A", "B"],
        [_line(1, "A", "B", reactance="x", active="act", tmax_ab="tmax")],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B")],
    )
    report = analyse_planning(plan, case_dir=tmp_path)
    # Union topology: the stage-2 value (0.2) is excluded -- line inactive.
    assert report.stubs and report.stubs[0].reactance == pytest.approx(0.3)
    assert report.stubs[0].tmax == pytest.approx(120.0)
    topo = report.stage_topology
    assert topo is not None
    labels = [(p.first_stage, p.last_stage, len(p.islands)) for p in topo.periods]
    assert labels == [(1, 1, 1), (2, 2, 2), (3, 3, 1)]
    assert not report.unresolved_fields


def test_cli_does_not_crash_on_string_reactance(tmp_path: Path, capsys):
    inp = tmp_path / "input"
    _write_long(
        inp / "Line" / "x.parquet",
        [{"stage": s, "uid": 1, "value": 0.1} for s in (1, 2, 3)],
    )
    plan = _case(
        ["A", "B"],
        [_line(1, "A", "B", reactance="x")],
        generators=[_gen(10, "A")],
    )
    rc = main([str(_write_case(tmp_path, plan)), "--no-color"])
    assert rc == 0
    assert "Per-stage topology" in capsys.readouterr().out


def test_unresolved_file_reference_is_reported(tmp_path: Path):
    plan = _case(["A", "B"], [_line(1, "A", "B", reactance="missing_x")])
    report = analyse_planning(plan, case_dir=tmp_path)
    assert any("missing_x" in u for u in report.unresolved_fields)


def test_negative_reactance_in_one_stage_is_flagged():
    plan = _case(["A", "B"], [_line(1, "A", "B", reactance=[0.1, -0.1, 0.1])])
    report = analyse_planning(plan)
    assert report.negative_impedance
    assert report.negative_impedance[0].reactance == pytest.approx(-0.1)


# ---------------------------------------------------------------------------
# Per-stage islands
# ---------------------------------------------------------------------------


def test_zero_capacity_stage_disconnects():
    plan = _case(
        ["A", "B"],
        [_line(1, "A", "B", tmax_ab=[100, 0, 100], tmax_ba=[100, 0, 100])],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B")],
    )
    topo = analyse_planning(plan).stage_topology
    assert topo is not None
    assert [len(p.islands) for p in topo.periods] == [1, 2, 1]
    # One direction open is enough to connect.
    plan["system"]["line_array"][0]["tmax_ba"] = 100.0
    topo = analyse_planning(plan).stage_topology
    assert topo is not None and len(topo.periods) == 1


def test_bus_inactive_drops_out_and_future_line_joins():
    plan = _case(
        ["A", "B", "C"],
        [
            _line(1, "A", "B"),
            _line(2, "B", "C", active=[0, 0, 1]),
        ],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B")],
        bus_extra={"C": {"active": [0, 1, 1]}},
    )
    topo = analyse_planning(plan).stage_topology
    assert topo is not None
    got = [
        (p.first_stage, p.last_stage, len(p.islands), p.n_inactive_buses)
        for p in topo.periods
    ]
    assert got == [(1, 1, 1, 1), (2, 2, 2, 0), (3, 3, 1, 0)]


def test_isolated_bus_without_elements_is_benign(tmp_path: Path):
    plan = _case(
        ["A", "B", "C"],
        [_line(1, "A", "B")],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B")],
    )
    report = analyse_planning(plan)
    assert "C" in report.isolated_buses
    assert not report.islands_unsupplied
    topo = report.stage_topology
    assert topo is not None
    (period,) = topo.periods
    assert len(period.islands) == 2
    assert len(period.islands_with_elements) == 1
    rc = main([str(_write_case(tmp_path, plan)), "--no-color", "--expect-islands", "1"])
    assert rc == 0


def test_isolated_bus_with_demand_is_fatal(tmp_path: Path):
    plan = _case(
        ["A", "B", "C"],
        [_line(1, "A", "B")],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B"), _dem(21, "C", lmax=7.0)],
    )
    report = analyse_planning(plan, expect_islands=1)
    assert [i.buses for i in report.islands_unsupplied] == [["C"]]
    topo = report.stage_topology
    assert topo is not None and topo.expectation is not None
    (vio,) = topo.expectation.violations
    (off,) = vio.offenders
    assert off.buses == ["C"]
    assert off.elements[0].name == "d21"
    assert off.elements[0].capacity_mw == pytest.approx(7.0)
    assert main([str(_write_case(tmp_path, plan)), "--no-color"]) == 2


def test_inert_zero_mw_element_does_not_count():
    plan = _case(
        ["A", "B", "C"],
        [_line(1, "A", "B")],
        generators=[_gen(10, "A"), _gen(11, "C", pmax=0.0)],
        demands=[_dem(20, "B")],
    )
    report = analyse_planning(plan, expect_islands=1)
    topo = report.stage_topology
    assert topo is not None and topo.expectation is not None
    assert topo.expectation.ok
    small = topo.periods[0].islands[-1]
    assert small.buses == ["C"] and small.n_inert == 1
    # ... unless it is expandable.
    plan["system"]["generator_array"][1]["expcap"] = 100.0
    report = analyse_planning(plan, expect_islands=1)
    assert report.expectation_failed


def test_expect_zones_two_countries_joined_by_link(tmp_path: Path):
    """Two zones start apart and get joined by an inter-zone link."""
    plan = _case(
        ["CL_A", "CL_B", "PE_A", "PE_B", "PE_X"],
        [
            _line(1, "CL_A", "CL_B"),
            _line(2, "PE_A", "PE_B"),
            # DC link (no reactance) active from stage 2.
            _line(3, "CL_B", "PE_A", reactance=None, active=[0, 1, 1]),
            # PE_X is a future bus: its line arrives in stage 3.
            _line(4, "PE_B", "PE_X", active=[0, 0, 1]),
        ],
        generators=[_gen(10, "CL_A"), _gen(11, "PE_A"), _gen(12, "PE_X")],
        demands=[_dem(20, "CL_B"), _dem(21, "PE_B")],
    )
    plan["system"]["generator_array"][2]["active"] = [0, 0, 1]
    path = _write_case(tmp_path, plan)
    report = analyse_planning(plan, case_dir=tmp_path, expect_zones=["CL", "PE"])
    topo = report.stage_topology
    assert topo is not None
    assert [len(p.islands_with_elements) for p in topo.periods] == [2, 1, 1]
    assert topo.periods[1].islands_with_elements[0].zones == ["CL", "PE"]
    (link,) = topo.inter_zone_lines
    assert (link.zone_a, link.zone_b, link.first_stage) == ("CL", "PE", 2)
    assert topo.expectation is not None and topo.expectation.ok
    assert main([str(path), "--no-color", "--expect-zones", "CL,PE"]) == 0

    # A stray generator on PE_X before its line exists -> exit 3.
    plan["system"]["generator_array"][2]["active"] = 1
    path.write_text(json.dumps(plan))
    assert main([str(path), "--no-color", "--expect-zones", "CL,PE"]) == 3
    report = analyse_planning(plan, expect_zones=["CL", "PE"])
    topo = report.stage_topology
    assert topo is not None and topo.expectation is not None
    vio = topo.expectation.violations
    # Two periods (the link joins CL and PE at stage 2); PE_X offends in both.
    assert [(v.first_stage, v.last_stage) for v in vio] == [(1, 1), (2, 2)]
    assert all(v.offenders[0].buses == ["PE_X"] for v in vio)


def test_explicit_zone_field_wins_over_prefix():
    plan = _case(
        ["CL_A", "CL_B"],
        [],
        generators=[_gen(10, "CL_A"), _gen(11, "CL_B")],
        bus_extra={"CL_B": {"zone": "south"}},
    )
    topo = analyse_planning(plan, expect_zones=["CL", "south"]).stage_topology
    assert topo is not None and topo.expectation is not None
    assert topo.expectation.ok


def test_never_in_service_line_excluded_from_graph():
    plan = _case(
        ["A", "B"],
        [_line(1, "A", "B", tmax_ab=0.0, tmax_ba=0.0)],
    )
    net = build_network_graph(plan)
    assert net.graph.number_of_edges() == 0
    assert [r.uid for r in net.never_in_service] == [1]


def test_stage_topology_in_json_report(tmp_path: Path):
    plan = _case(
        ["A", "B"],
        [_line(1, "A", "B", active=[1, 0, 1])],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B")],
    )
    payload = report_to_json(analyse_planning(plan, expect_islands=1))
    topo = payload["stage_topology"]
    assert [p["n_islands"] for p in topo["periods"]] == [1, 2, 1]
    assert topo["expectation"]["ok"] is False
    json.dumps(payload)  # serialisable


def test_element_on_inactive_bus_is_reported():
    plan = _case(
        ["A", "B"],
        [_line(1, "A", "B")],
        generators=[_gen(10, "A")],
        demands=[_dem(20, "B", lmax=5.0)],
        bus_extra={"B": {"active": [1, 0, 0]}},
    )
    topo = analyse_planning(plan).stage_topology
    assert topo is not None
    (dead,) = topo.elements_on_inactive_bus
    assert (dead.name, dead.bus, dead.stages) == ("d20", "B", [(2, 3)])
    assert dead.capacity_mw == pytest.approx(5.0)
