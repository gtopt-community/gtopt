# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.sanity_report`."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_shared import sanity_report as sr


def _planning(system: dict[str, Any], **model: Any) -> dict[str, Any]:
    return {
        "options": {"input_directory": ".", "model_options": model},
        "simulation": {"stage_array": [{"uid": 1}, {"uid": 2}]},
        "system": {"name": "t", **system},
    }


# ---------------------------------------------------------------------------
# Guatemala dcirc.dat %-vs-Ω regression fixture
# ---------------------------------------------------------------------------
#
# The NCP/.dat front-end read dcirc.dat X (a % on the 100 MVA base) as Ω
# and divided by the bus kV²: x = X% · 100 / kV².  On buses whose Volt.dat
# kV copied the name suffix (692 instead of 69) that shrank the line
# another (692/69)² ≈ 100x, below the tie threshold.  The report must flag
# both the mistyped kV and the per-class impedance inconsistency.

_X_PCT = [2.0, 3.5, 5.0, 6.0, 7.5, 9.0, 11.0, 14.0, 17.0, 20.0]


def _guatemala_case(*, buggy: bool, with_bus_kv: bool) -> dict[str, Any]:
    buses: list[dict[str, Any]] = [
        {"uid": 1, "name": "ESC-691"},
        {"uid": 2, "name": "ESC-692"},
    ]
    buses += [{"uid": 10 + i, "name": f"N{i}-691"} for i in range(len(_X_PCT))]
    if with_bus_kv:
        for b in buses:
            b["voltage"] = 692.0 if b["name"] == "ESC-692" else 69.0
    lines = []
    for i, xpct in enumerate(_X_PCT):
        # The two shortest circuits end on the mistyped ESC-692 bus.
        on_typo = i < 2
        kv_used = 692.0 if on_typo else 69.0
        x = xpct * 100.0 / kv_used**2 if buggy else xpct / 100.0
        lines.append(
            {
                "uid": i + 1,
                "name": f"ESC{chr(65 + i)}69A_{i + 1}",
                "bus_a": 1,
                "bus_b": 2 if on_typo else 10 + i,
                "reactance": x,
                "resistance": x / 8.0,
            }
        )
    return _planning(
        {"bus_array": buses, "line_array": lines},
        dc_line_reactance_threshold=1e-3,
    )


def test_guatemala_dcirc_percent_bug_is_caught() -> None:
    report = sr.build_sanity_report(_guatemala_case(buggy=True, with_bus_kv=False))
    flagged = report.flagged
    # kV class recovered from the line names (no bus voltage in the case).
    assert [c["kv_class"] for c in report.line_classes] == ["69 kV"]
    assert report.line_classes[0]["kv_source"] == ["name"]
    assert report.line_classes[0]["below_tie"] == 2
    outliers = flagged["line_x_outlier"]
    assert {o["name"] for o in outliers.offenders} == {"ESCA69A_1", "ESCB69A_2"}
    assert flagged["line_below_tie"].count == 2


def test_guatemala_mistyped_bus_kv_is_caught() -> None:
    report = sr.build_sanity_report(_guatemala_case(buggy=True, with_bus_kv=True))
    bus_kv = report.flagged["bus_kv"]
    assert bus_kv.count == 1
    assert bus_kv.offenders[0] | {} == {
        "score": 2.0,
        "name": "ESC-692",
        "kv": 692.0,
        "mistyped": True,
    }


def test_guatemala_fixed_conversion_is_clean() -> None:
    report = sr.build_sanity_report(_guatemala_case(buggy=False, with_bus_kv=False))
    assert "line_x_outlier" not in report.flagged
    assert "line_below_tie" not in report.flagged


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------


def test_line_classes_xr_and_transformers() -> None:
    buses = [
        {"uid": 1, "name": "A220", "voltage": 220},
        {"uid": 2, "name": "B220", "voltage": 220},
        {"uid": 3, "name": "C110", "voltage": 110},
        {"uid": 4, "name": "D", "voltage": 1.0},
    ]
    lines = [
        {
            "uid": 1,
            "name": "l1",
            "bus_a": "A220",
            "bus_b": "B220",
            "reactance": 20,
            "resistance": 2,
            "voltage": 220,
        },
        {
            "uid": 2,
            "name": "l2",
            "bus_a": 1,
            "bus_b": 2,
            "reactance": 30,
            "resistance": 0.1,
            "voltage": 220,
        },
        {"uid": 3, "name": "tr", "bus_a": 1, "bus_b": 3, "reactance": 0.1},
        {"uid": 4, "name": "pu", "bus_a": 4, "bus_b": 99, "reactance": 0.2},
        {
            "uid": 5,
            "name": "linekv",
            "bus_a": 98,
            "bus_b": 99,
            "reactance": 5,
            "voltage": 66,
        },
        {"uid": 6, "name": "hvdc", "bus_a": 1, "bus_b": 3},
        {
            "uid": 7,
            "name": "off",
            "bus_a": 1,
            "bus_b": 2,
            "reactance": 1,
            "active": False,
        },
        {
            "uid": 8,
            "name": "sched",
            "bus_a": 1,
            "bus_b": 2,
            "reactance": [[20, 22]],
            "voltage": 220,
            "active": [1, 0],
        },
    ]
    report = sr.build_sanity_report(
        _planning({"bus_array": buses, "line_array": lines})
    )
    classes = {c["kv_class"]: c for c in report.line_classes}
    assert set(classes) == {"220 kV", "transformer", "66 kV", "p.u."}
    assert classes["220 kV"]["count"] == 3
    assert classes["220 kV"]["kv_source"] == ["bus"]
    assert classes["66 kV"]["kv_source"] == ["line"]
    assert "r_median" in classes["220 kV"]
    assert "r_median" not in classes["p.u."]
    xr = report.flagged["line_xr"]
    assert [o["name"] for o in xr.offenders] == ["l2"]
    # The D bus at 1.0 kV is a per-unit placeholder, never a kV finding.
    assert "bus_kv" not in report.flagged


def test_outlier_needs_min_class_size() -> None:
    lines = [
        {"uid": i, "name": f"x69_{i}", "bus_a": 1, "bus_b": 2, "reactance": 0.1}
        for i in range(3)
    ]
    lines.append({"uid": 9, "name": "x69_t", "bus_a": 1, "bus_b": 2, "reactance": 1e-6})
    planning = _planning({"line_array": lines})
    assert "line_x_outlier" not in sr.build_sanity_report(planning).flagged
    th = sr.SanityThresholds(line_x_min_class_size=2)
    report = sr.build_sanity_report(planning, thresholds=th)
    assert report.flagged["line_x_outlier"].count == 1


def test_bus_kv_nonstandard_not_mistyped() -> None:
    buses = [
        {"uid": 1, "name": "X-123", "voltage": 123},
        {"uid": 2, "name": "OGP1220", "voltage": 1220},
        {"uid": 3, "name": "Y", "voltage": 220.0},
        {"uid": 4, "name": "Z"},
    ]
    report = sr.build_sanity_report(_planning({"bus_array": buses}))
    fnd = report.flagged["bus_kv"]
    assert [(o["name"], o["mistyped"]) for o in fnd.offenders] == [
        ("X-123", True),
        ("OGP1220", True),
    ]
    assert not sr.mistyped_kv(123.0, "X-12")
    assert not sr.mistyped_kv(0.0, "X-000")
    assert not sr.mistyped_kv(230.0, "X-230")


def test_zero_rated_units_and_commitment(tmp_path: Path) -> None:
    (tmp_path / "Generator").mkdir()
    pd.DataFrame(
        {
            "stage": [1, 1, 2, 2],
            "block": [1, 2, 1, 2],
            "uid": [5, 5, 5, 5],
            "value": [0.0, 0.0, 0.0, 0.0],
        }
    ).to_parquet(tmp_path / "Generator" / "pmax.parquet")
    gens = [
        {"uid": 1, "name": "g_uc", "pmax": 0.0},
        {"uid": 2, "name": "g_pmin", "pmax": [[0, 0]], "pmin": 5},
        {"uid": 3, "name": "g_cap", "capacity": 0.0},
        {"uid": 4, "name": "g_exp", "capacity": 0.0, "expcap": 10},
        {"uid": 5, "name": "g_file", "pmax": "pmax"},
        {"uid": 6, "name": "g_ok", "pmax": 50},
        {"uid": 7, "name": "g_off", "pmax": 0, "active": 0},
        {"uid": 8, "name": "g_nocap"},
    ]
    bats = [
        {
            "uid": 1,
            "name": "b_dead",
            "pmax_charge": 0,
            "pmax_discharge": 0,
            "commitment": True,
            "pmin_discharge": 2,
        },
        {"uid": 2, "name": "b_zero", "pmax_charge": 0, "pmax_discharge": 0},
        {"uid": 3, "name": "b_ok", "pmax_charge": 5, "pmax_discharge": 5},
        {"uid": 4, "name": "b_off", "active": False},
    ]
    system = {
        "generator_array": gens,
        "battery_array": bats,
        "commitment_array": [{"uid": 1, "generator": 1}, {"uid": 2, "generator": 4}],
        "simple_commitment_array": [
            {"uid": 1, "generator": "g_cap"},
            {"uid": 2, "generator": 3, "active": 0},
        ],
    }
    report = sr.build_sanity_report(_planning(system), tmp_path)
    zero = {o["name"] for o in report.flagged["zero_rated_units"].offenders}
    assert zero == {"g_uc", "g_pmin", "g_cap", "g_file", "b_dead", "b_zero"}
    dead = {
        o["name"]: o["reasons"]
        for o in report.flagged["zero_rated_committed"].offenders
    }
    assert dead == {
        "g_uc": ["commitment"],
        "g_pmin": ["pmin > 0"],
        "g_cap": ["commitment"],
        "b_dead": ["commitment", "pmin_discharge > 0"],
    }


def test_profiles_inline_and_file(tmp_path: Path) -> None:
    (tmp_path / "GeneratorProfile").mkdir()
    pd.DataFrame(
        {"stage": [1, 2], "block": [1, 1], "uid": [2, 2], "value": [0.5, 55.0]}
    ).to_parquet(tmp_path / "GeneratorProfile" / "profile.parquet")
    system = {
        "generator_profile_array": [
            {"uid": 1, "name": "ok", "generator": 1, "profile": [[0.2, 0.9]]},
            {"uid": 2, "name": "mw_file", "generator": 2, "profile": "profile"},
            {"uid": 3, "name": "missing_uid", "generator": 3, "profile": "profile"},
            {"uid": 4, "name": "off", "generator": 4, "profile": 9.0, "active": 0},
            {"uid": 5, "name": "empty", "generator": 5, "profile": []},
        ],
        "demand_profile_array": [
            {"uid": 1, "name": "neg", "demand": 1, "profile": -0.5}
        ],
        "capacity_profile_array": [
            {"uid": 1, "name": "cp", "owner": 1, "profile": 1.0}
        ],
    }
    report = sr.build_sanity_report(_planning(system), tmp_path)
    fnd = report.flagged["profile_range"]
    assert [o["name"] for o in fnd.offenders] == ["mw_file", "neg"]
    assert fnd.offenders[0]["max"] == pytest.approx(55.0)
    # Without a case dir file-backed profiles are not inspected.
    assert sr.build_sanity_report(_planning(system)).flagged["profile_range"].count == 1


def test_reservoir_floor() -> None:
    rule = {"reservoir": "laja", "segments": [{"volume": 0}, {"volume": 1370}]}
    system = {
        "reservoir_array": [
            {"uid": 1, "name": "laja"},
            {"uid": 2, "name": "maule", "emin": 100},
            {"uid": 3, "name": "colbun", "emin": 0},
            {"uid": 4, "name": "filebacked", "emin": "emin"},
            {"uid": 5, "name": "off", "active": False},
        ],
        "flow_right_array": [
            {"uid": 1, "name": "laja_riego", "bound_rule": rule},
            {"uid": 2, "name": "laja_2", "bound_rule": rule},
            {
                "uid": 3,
                "name": "maule_r",
                "bound_rule": {"reservoir": 2, "segments": [{"volume": 10}]},
            },
            {
                "uid": 4,
                "name": "month",
                "bound_rule": {
                    "reservoir": 1,
                    "axis": "stage_month",
                    "segments": [{"volume": 5}],
                },
            },
            {
                "uid": 5,
                "name": "zero_level",
                "bound_rule": {"reservoir": 3, "segments": [{"volume": 0}]},
            },
            {
                "uid": 6,
                "name": "dangling",
                "bound_rule": {"reservoir": "nope", "segments": [{"volume": 5}]},
            },
            {"uid": 7, "name": "no_rule"},
        ],
        "volume_right_array": [
            {
                "uid": 1,
                "name": "colbun_vr",
                "bound_rule": {"reservoir": 3, "segments": [{"volume": 200}]},
            },
            {
                "uid": 2,
                "name": "fb_vr",
                "bound_rule": {"reservoir": 4, "segments": [{"volume": 200}]},
            },
            {
                "uid": 3,
                "name": "off_vr",
                "bound_rule": {"reservoir": 5, "segments": [{"volume": 200}]},
            },
        ],
    }
    fnd = sr.build_sanity_report(_planning(system)).flagged["reservoir_floor"]
    assert [(o["name"], o["reserve_level"]) for o in fnd.offenders] == [
        ("laja", 1370.0),
        ("colbun", 200.0),
    ]
    assert fnd.offenders[0]["rights"] == [
        "FlowRight 'laja_riego'",
        "FlowRight 'laja_2'",
    ]


def test_rewards_vs_fail_cost() -> None:
    system = {
        "flow_right_array": [
            {"uid": 1, "name": "maule_105", "uvalue": 1100},
            {"uid": 2, "name": "neg", "fcost": [[-1500, 0]]},
            {"uid": 3, "name": "cheap", "uvalue": 900},
            {"uid": 4, "name": "off", "uvalue": 9e9, "active": False},
        ],
    }
    report = sr.build_sanity_report(_planning(system, demand_fail_cost=1000))
    fnd = report.flagged["reward_vs_fail_cost"]
    assert [(o["name"], o["field"]) for o in fnd.offenders] == [
        ("neg", "fcost"),
        ("maule_105", "uvalue"),
    ]
    assert "(1000)" in fnd.message

    # Per-demand fcost is the reference without a global cost.
    demands = [{"uid": 1, "name": "d", "fcost": 2000}, {"uid": 2, "name": "e"}]
    planning = _planning({**system, "demand_array": demands}, hydro_use_value=3000)
    fnd = sr.build_sanity_report(planning).flagged["reward_vs_fail_cost"]
    assert [o["name"] for o in fnd.offenders] == ["model_options"]

    # No fail cost anywhere: nothing to compare against.
    report = sr.build_sanity_report(_planning(system))
    assert "reward_vs_fail_cost" not in report.flagged
    assert "n/a" in report.findings["reward_vs_fail_cost"].message

    th = sr.SanityThresholds(reward_fail_cost_ratio=2.0)
    report = sr.build_sanity_report(
        _planning(system, demand_fail_cost=1000), thresholds=th
    )
    assert "reward_vs_fail_cost" not in report.flagged


# ---------------------------------------------------------------------------
# logging, JSON, converter hook, CLI
# ---------------------------------------------------------------------------


def test_log_and_write(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    report = sr.build_sanity_report(_guatemala_case(buggy=True, with_bus_kv=False))
    with caplog.at_level(logging.DEBUG, logger="t"):
        sr.log_sanity_report(report, logging.getLogger("t"), max_offenders=1)
    text = caplog.text
    assert "sanity: lines 69 kV" in text
    assert "(+1 more)" in text
    assert "sanity: line_x_outlier:" in text  # debug detail of the rest
    out = sr.write_sanity_report(report, tmp_path / "sub" / "r.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["findings"]["line_x_outlier"]["count"] == 2
    # Effective floor = max(absolute 1e-3, 0.01 x system median |X|/V²).
    xs = sorted(c["x"] for c in _guatemala_line_x(buggy=True))
    assert data["tie_threshold"] == pytest.approx(
        max(1e-3, 0.01 * sr._percentile(xs, 0.5))  # pylint: disable=protected-access
    )
    assert data["tie_threshold"] > 1e-3


def _guatemala_line_x(*, buggy: bool) -> list[dict[str, float]]:
    """Per-line ``x = |X|/V²`` of the Guatemala fixture (V = 1)."""
    case = _guatemala_case(buggy=buggy, with_bus_kv=False)
    return [{"x": abs(ln["reactance"])} for ln in case["system"]["line_array"]]


def test_tie_threshold_relative_and_absolute() -> None:
    """``tiny_reactance_rel_threshold`` raises the floor; ``0`` disables it."""
    lines = [
        {"uid": i, "name": f"l{i}_69", "bus_a": 1, "bus_b": 2, "reactance": 0.1}
        for i in range(6)
    ]
    # x = 5e-4 is above the absolute 1e-6 floor but below 0.01 x 0.1.
    lines.append(
        {"uid": 9, "name": "tie_69", "bus_a": 1, "bus_b": 2, "reactance": 5e-4}
    )
    report = sr.build_sanity_report(_planning({"line_array": lines}))
    assert report.tie_threshold == pytest.approx(1e-3)
    assert report.flagged["line_below_tie"].count == 1
    legacy = _planning({"line_array": lines}, tiny_reactance_rel_threshold=0.0)
    report = sr.build_sanity_report(legacy)
    assert report.tie_threshold == pytest.approx(sr.DEFAULT_TIE_THRESHOLD)
    assert "line_below_tie" not in report.flagged


def test_xr_skips_ties_and_lossless_lines() -> None:
    """X/R is not checked on ties nor on lines below the R floor."""
    lines = [
        {"uid": i, "name": f"l{i}_69", "bus_a": 1, "bus_b": 2, "reactance": 0.1}
        for i in range(6)
    ]
    # R/V² = 5.2e-8 < 1e-7: made lossless by gtopt, X/R = 1.9e6 is moot.
    lines.append(
        {
            "uid": 7,
            "name": "lossless",
            "bus_a": 1,
            "bus_b": 2,
            "reactance": 0.1,
            "resistance": 5.2e-8,
        }
    )
    # A tie (x = 1e-7) with X/R = 1000.
    lines.append(
        {
            "uid": 8,
            "name": "tie",
            "bus_a": 1,
            "bus_b": 2,
            "reactance": 1e-7,
            "resistance": 1e-10,
        }
    )
    # A genuine high X/R line.
    lines.append(
        {
            "uid": 9,
            "name": "hv",
            "bus_a": 1,
            "bus_b": 2,
            "reactance": 0.1,
            "resistance": 1e-4,
        }
    )
    report = sr.build_sanity_report(_planning({"line_array": lines}))
    assert [o["name"] for o in report.flagged["line_xr"].offenders] == ["hv"]


def test_log_small_classes_and_clean(caplog: pytest.LogCaptureFixture) -> None:
    lines = [{"uid": 1, "name": "a", "bus_a": 1, "bus_b": 2, "reactance": 0.1}]
    report = sr.build_sanity_report(_planning({"line_array": lines}), case="c")
    with caplog.at_level(logging.INFO, logger="u"):
        sr.log_sanity_report(report, logging.getLogger("u"))
    assert "1 small kV class(es)" in caplog.text
    assert "c — no data-quality issue found" in caplog.text


def test_run_sanity_report_from_path(tmp_path: Path) -> None:
    case = tmp_path / "case.json"
    case.write_text(json.dumps(_guatemala_case(buggy=True, with_bus_kv=True)))
    out = tmp_path / "report.json"
    report = sr.run_sanity_report(case, json_path=out)
    assert report is not None
    assert report.case == "case"
    assert out.exists()
    report = sr.run_sanity_report(_planning({}), thresholds=sr.SanityThresholds())
    assert report is not None and not report.flagged


def test_run_sanity_report_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert sr.run_sanity_report(tmp_path / "missing.json") is None
    assert "sanity: report skipped (FileNotFoundError" in caplog.text


def test_add_argument_and_main(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    sr.add_sanity_report_argument(parser)
    assert parser.parse_args(["--sanity-report", "x.json"]).sanity_report == "x.json"
    assert parser.parse_args([]).sanity_report is None

    case = tmp_path / "c.json"
    case.write_text(json.dumps(_guatemala_case(buggy=True, with_bus_kv=False)))
    out = tmp_path / "o.json"
    assert sr.main([str(case), "--sanity-report", str(out), "-v"]) == 0
    assert json.loads(out.read_text())["findings"]["line_below_tie"]["count"] == 2
    assert sr.main([str(tmp_path / "nope.json")]) == 1


def test_helpers() -> None:
    assert sr._percentile([3.0], 0.5) == 3.0  # pylint: disable=protected-access
    assert sr._ref_key(True) is None  # pylint: disable=protected-access
    assert sr._ref_key(None) is None  # pylint: disable=protected-access
    assert sr._is_active({"active": "sched"})  # pylint: disable=protected-access
    assert sr._num(float("nan")) is None  # pylint: disable=protected-access
    values = sr._Values(None)  # pylint: disable=protected-access
    assert values.extrema("Line", {"x": "file"}, "x") is None
    assert values.magnitude("Line", {}, "x") is None
    assert sr._class_sort_key("transformer") == (1, 0.0, "transformer")  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "module",
    [
        "plp2gtopt.main",
        "plexos2gtopt.main",
        "sddp2gtopt.main",
        "pp2gtopt.main",
        "psse2gtopt.main",
    ],
)
def test_converters_expose_flag(module: str) -> None:
    mod = __import__(module, fromlist=["make_parser"])
    parser = mod.make_parser()
    ns, _ = parser.parse_known_args(["--sanity-report", "r.json"])
    assert ns.sanity_report == "r.json"


# ---------------------------------------------------------------------------
# Maule Res. 105 (review R3): reward above a coupled shortfall penalty
# ---------------------------------------------------------------------------

_MAULE_PAMPL = """\
# Penalty for unserved Resolution 105 ecological flow [$/hm3]
param costo_riego_ns_res105 = 1000.0;
constraint dist_retiro_Melado "retiro_Melado: 12.65% of the Res. 105 delivery":
  flow_right('retiro_Melado').flow + flow_right('deficit_Melado').flow
    = 0.1265 * flow_right('maule_resolucion_105').flow;
constraint unrelated "no reward here":
  flow_right('retiro_Melado').flow <= 10;
"""


def _maule_r3(deficit_uvalue: float) -> dict[str, Any]:
    # plp_2y: demand_fail_cost = 0, so only the coupled penalty can flag it.
    return _planning(
        {
            "flow_right_array": [
                {
                    "uid": 1005,
                    "name": "maule_resolucion_105",
                    "target": 0,
                    "uvalue": 1100.0,
                },
                {
                    "uid": 1006,
                    "name": "deficit_Melado",
                    "target": 0,
                    "uvalue": deficit_uvalue,
                },
                {"uid": 1007, "name": "retiro_Melado"},
                {"uid": 1008, "name": "expensive", "fcost": 5000.0},
                {"uid": 1009, "name": "cheap_fcost", "fcost": 10.0},
            ],
            "user_constraint_files": ["maule.pampl", "missing.pampl"],
            "user_constraint_array": [
                {
                    "uid": 1,
                    "name": "inline_link",
                    "expression": 'flow_right(1009).flow = flow_right("maule_resolucion_105").flow',
                },
                {
                    "uid": 2,
                    "name": "off_link",
                    "active": False,
                    "expression": "flow_right(1008).flow = flow_right(1005).flow",
                },
            ],
        },
        demand_fail_cost=0.0,
    )


def test_maule_r3_phantom_reward_is_caught(tmp_path: Path) -> None:
    (tmp_path / "maule.pampl").write_text(_MAULE_PAMPL, encoding="utf-8")
    report = sr.build_sanity_report(_maule_r3(-1000.0), tmp_path)
    assert "reward_vs_fail_cost" not in report.flagged
    fnd = report.flagged["reward_vs_coupled_penalty"]
    pairs = {(o["penalised"], o["constraint"]) for o in fnd.offenders}
    assert pairs == {
        ("deficit_Melado", "dist_retiro_Melado"),
        ("cheap_fcost", "inline_link"),
    }
    top = fnd.offenders[0]
    assert top["name"] == "maule_resolucion_105"
    assert top["penalised"] == "cheap_fcost"  # largest margin first


def test_maule_r3_fix_clears_the_flag(tmp_path: Path) -> None:
    (tmp_path / "maule.pampl").write_text(_MAULE_PAMPL, encoding="utf-8")
    planning = _maule_r3(-2100.0)  # CostoRiegoNS105 + ValorRiego105
    planning["system"]["flow_right_array"].pop()  # drop cheap_fcost
    report = sr.build_sanity_report(planning, tmp_path)
    assert "reward_vs_coupled_penalty" not in report.flagged


def test_coupled_rewards_json_constraint_file(tmp_path: Path) -> None:
    (tmp_path / "Input").mkdir()
    (tmp_path / "Input" / "uc.json").write_text(
        json.dumps(
            [{"name": "j", "expression": "flow_right('a').flow = flow_right('b').flow"}]
        )
    )
    planning = _planning(
        {
            "flow_right_array": [
                {"uid": 1, "name": "a", "uvalue": 50.0},
                {"uid": 2, "name": "b", "fcost": [[5.0, 7.0]]},
            ],
            "user_constraint_file": "uc.json",
        }
    )
    planning["options"]["input_directory"] = "Input"
    report = sr.build_sanity_report(planning, tmp_path)
    assert report.flagged["reward_vs_coupled_penalty"].offenders[0]["penalty"] == 5.0
    # Without a case dir the file cannot be located: nothing to couple.
    assert "reward_vs_coupled_penalty" not in sr.build_sanity_report(planning).flagged
    # Absolute paths are used as-is.
    planning["system"]["user_constraint_file"] = str(tmp_path / "Input" / "uc.json")
    assert "reward_vs_coupled_penalty" in sr.build_sanity_report(planning).flagged
