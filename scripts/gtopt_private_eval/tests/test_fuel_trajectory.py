# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fuel-cost trajectories: declared base × IEA WEO index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.evaluate import evaluate
from gtopt_private_eval.fuel_trajectory import (
    IEA_WEO,
    IEA_WEO_INDEX,
    DeclaredCost,
    apply_to_planning,
    declared_cost,
    fuel_ratio,
    index_table,
    scenarios,
    trajectory_config,
)
from gtopt_private_eval.main import main
from gtopt_private_eval.params import Params, load_config, set_path

from .conftest import CONFIGS, golden

CL = "invest_cl_screening_1b/golden/lp"


def _params(cfg_name: str = "invest_screening.json", **sets: object) -> Params:
    cfg = load_config(CONFIGS / cfg_name)
    for k, v in sets.items():
        set_path(cfg, k.replace("__", "."), v)
    return Params(cfg)


# ── index ────────────────────────────────────────────────────────────


def test_iea_weo_2026_table() -> None:
    assert IEA_WEO["base_year"] == 2025
    assert IEA_WEO_INDEX["gas"]["steps"][2035] == pytest.approx(6.8 / 12.6)
    assert IEA_WEO_INDEX["coal"]["nze"][2050] == pytest.approx(51.0 / 117.0)
    assert IEA_WEO_INDEX["oil"]["cps"][2050] == pytest.approx(106.0 / 68.0)
    assert scenarios(IEA_WEO_INDEX) == ["cps", "steps", "arc", "nze"]
    # linear between points, flat after 2050, ratio to any base year
    mid = 0.5 * (1.0 + 6.8 / 12.6)
    assert fuel_ratio("gas", "steps", 2030, 2025) == pytest.approx(mid)
    assert fuel_ratio("gas", "steps", 2060, 2025) == pytest.approx(9.9 / 12.6)
    assert fuel_ratio("gas", "steps", 2026, 2026) == pytest.approx(1.0)
    with pytest.raises(KeyError):
        fuel_ratio("uranium", "steps", 2030, 2025)


def test_user_index_and_config() -> None:
    tab = index_table({"index": {"gas": {"low": {"2026": 1, "2036": "0.5"}}}})
    assert tab == {"gas": {"low": {2026: 1.0, 2036: 0.5}}}
    assert index_table({}) is IEA_WEO_INDEX
    assert fuel_ratio("gas", "low", 2031, 2026, tab) == pytest.approx(0.75)
    assert trajectory_config({}) is None
    assert trajectory_config({"fuel_trajectory": {"enabled": False}}) is None
    assert trajectory_config({"fuel_trajectory": {"scenario": "nze"}}) == {
        "scenario": "nze"
    }
    with pytest.raises(ValueError, match="price_scaling"):
        trajectory_config({"fuel_trajectory": {"price_scaling": "x"}})


# ── declared cost ────────────────────────────────────────────────────


def test_declared_cost_factor() -> None:
    split = DeclaredCost(100.0, 80.0, "declared")
    assert split.factor(0.5) == pytest.approx(0.6)  # 20 + 80 × 0.5
    assert DeclaredCost(100.0, None, "x").factor(0.5) == pytest.approx(0.5)
    assert DeclaredCost(0.0, None, "x").factor(0.5) == 1.0


def _p(project: dict[str, Any], tech: dict[str, Any] | None = None) -> Params:
    return Params({"projects": {"u": project}, "technologies": {"ccgt": tech or {}}})


def test_declared_cost_sources() -> None:
    dc = declared_cost(
        _p({"declared_cost": {"fuel": 60, "nonfuel": 5}}), "cl", "u", "ccgt", 99.0
    )
    assert (dc.total, dc.fuel) == (65.0, 60.0)
    dc = declared_cost(_p({"declared_cost": 70}), "cl", "u", "ccgt", 99.0)
    assert (dc.total, dc.fuel) == (70.0, None)
    dc = declared_cost(_p({}), "cl", "u", "ccgt", 55.0)
    assert dc.total == 55.0 and dc.fuel is None and "stand-in" in dc.source


def test_cl_rejects_estimates_and_missing_costs() -> None:
    with pytest.raises(ValueError, match="declared to the CEN"):
        declared_cost(
            _p({"heat_rate": 7.0, "fuel_price": 9.0}), "cl", "u", "ccgt", 50.0
        )
    with pytest.raises(ValueError, match="needs the variable cost declared"):
        declared_cost(_p({}), "cl", "u", "ccgt", 0.0)


def test_generic_markets_accept_estimates() -> None:
    dc = declared_cost(
        _p({"heat_rate": 7.0, "fuel_price": 9.0, "nonfuel_cost": 3.0}),
        "energy_only",
        "u",
        "ccgt",
        0.0,
    )
    assert (dc.total, dc.fuel) == (66.0, 63.0)
    dc = declared_cost(_p({}, {"fuel_share": 0.9}), "energy_only", "u", "ccgt", 50.0)
    assert dc.fuel == pytest.approx(45.0)
    dc = declared_cost(_p({}), "energy_only", "u", "ccgt", 50.0)
    assert dc.fuel is None


# ── evaluation ───────────────────────────────────────────────────────


def _by_name(rep: Any) -> dict[str, Any]:
    return {p.name: p for p in rep.projects}


def test_trajectory_scales_cost_and_thermal_prices() -> None:
    sysd = load_system(golden(CL))
    flat = _by_name(
        evaluate(sysd, _params(finance__fuel_trajectory__enabled=False), "cl")
    )
    traj_rep = evaluate(sysd, _params(), "cl")
    traj = _by_name(traj_rep)
    base_f, base_t = flat["base"], traj["base"]
    y1, y10 = base_t.cash.years[0], base_t.cash.years[9]
    assert y1.costs["variable"] == pytest.approx(base_f.year1.costs["variable"])
    # base = coal: year 10 (2035) at the coal STEPS ratio, whole cost (unsplit)
    ratio = fuel_ratio("coal", "steps", 2035, 2026)
    assert y10.costs["variable"] == pytest.approx(y1.costs["variable"] * ratio)
    # thermal-marginal prices follow the setter's fuel: gas falls to 2035
    assert y10.revenues["energy"] < y1.revenues["energy"]
    assert base_t.cash.npv != pytest.approx(base_f.cash.npv)
    sens = base_t.sensitivity["npv_vs_fuel_scenario"]
    assert set(sens) == {"cps", "steps", "arc", "nze"}
    assert sens["steps"] == pytest.approx(base_t.cash.npv)
    notes = " ".join(traj_rep.notes)
    assert "IEA WEO 2026" in notes and "thermal-marginal" in notes
    assert "not split" in notes and "base" in notes


def test_price_scaling_none_keeps_the_cmg() -> None:
    sysd = load_system(golden(CL))
    rep = evaluate(sysd, _params(finance__fuel_trajectory__price_scaling="none"), "cl")
    peak = _by_name(rep)["peak"]
    energy = [y.revenues["energy"] for y in peak.cash.years]
    assert energy[0] == pytest.approx(energy[-1])
    assert "price_scaling none" in " ".join(rep.notes)


def test_declared_split_applies_the_index_to_fuel_only() -> None:
    sysd = load_system(golden(CL))
    rep = evaluate(
        sysd,
        _params(projects__base__declared_cost={"fuel": 40.0, "nonfuel": 5.0}),
        "cl",
    )
    base = _by_name(rep)["base"]
    e = base.energy_mwh
    ratio = fuel_ratio("coal", "steps", 2035, 2026)
    assert base.cash.years[0].costs["variable"] == pytest.approx(45.0 * e)
    assert base.cash.years[9].costs["variable"] == pytest.approx(
        (5.0 + 40.0 * ratio) * e
    )
    unsplit = next(n for n in rep.notes if "not split" in n)
    assert "base" not in unsplit.split(":")[-1].split(", ")


def test_cl_validation_rejects_estimated_cost() -> None:
    sysd = load_system(golden(CL))
    with pytest.raises(ValueError, match="declared to the CEN"):
        evaluate(sysd, _params(projects__base__heat_rate=9.0), "cl")
    # also without a trajectory: the declared-cost rule is a cl rule
    with pytest.raises(ValueError, match="declared to the CEN"):
        evaluate(
            sysd,
            _params(
                projects__base__fuel_price=9.0,
                finance__fuel_trajectory__enabled=False,
            ),
            "cl",
        )
    # other markets accept it
    evaluate(sysd, _params(projects__base__heat_rate=9.0), "energy_only")


def test_cfd_keeps_the_strike_revenue() -> None:
    sysd = load_system(golden(CL))
    rep = evaluate(sysd, _params(), "cfd")
    for p in rep.projects:
        rows = [y.revenues for y in p.cash.years if y.revenues.get("cfd_difference")]
        if len(rows) < 2:
            continue
        totals = [r["energy"] + r["cfd_difference"] for r in rows]
        assert totals[-1] == pytest.approx(totals[0], rel=1e-9)


def test_battery_has_no_fuel_but_sees_scaled_prices() -> None:
    sysd = load_system(golden("invest_cl_bat_1b/golden/lp"))
    rep = evaluate(sysd, _params("invest_bat.json"), "cl")
    bess = _by_name(rep)["bess"]
    assert "npv_vs_fuel_scenario" in bess.sensitivity


# ── gtopt stage schedules and CLI ────────────────────────────────────


def _planning() -> dict[str, Any]:
    return {
        "system": {
            "fuel_array": [
                {"uid": 1, "name": "lng", "price": 10.0},
                {"uid": 2, "name": "sched", "price": [1.0, 2.0]},
            ],
            "generator_array": [
                {"uid": 1, "name": "ccgt", "gcost": 60.0},
                {"uid": 2, "name": "coal1", "gcost": 40.0},
                {"uid": 3, "name": "file", "gcost": "gcost.parquet"},
                {"uid": 4, "name": "solar", "gcost": 0.0},
            ],
        }
    }


def test_apply_to_planning() -> None:
    plan = _planning()
    changed = apply_to_planning(
        plan,
        {"lng": "gas", "sched": "gas", "ccgt": "gas", "coal1": "coal", "file": "gas"},
        [2026, 2035],
        "steps",
        2026,
        declared={"coal1": (30.0, 10.0)},
    )
    assert changed == ["lng", "ccgt", "coal1"]
    sysm = plan["system"]
    r_gas = fuel_ratio("gas", "steps", 2035, 2026)
    r_coal = fuel_ratio("coal", "steps", 2035, 2026)
    assert sysm["fuel_array"][0]["price"] == pytest.approx([10.0, 10.0 * r_gas])
    assert sysm["fuel_array"][1]["price"] == [1.0, 2.0]  # schedules untouched
    gens = {g["name"]: g for g in sysm["generator_array"]}
    assert gens["ccgt"]["gcost"] == pytest.approx([60.0, 60.0 * r_gas])
    assert gens["coal1"]["gcost"] == pytest.approx([40.0, 10.0 + 30.0 * r_coal])
    assert gens["solar"]["gcost"] == 0.0


def test_cli_table_json_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["fuel-trajectory", "--fuel", "gas", "--years", "2025", "2035"]) == 0
    out = capsys.readouterr().out
    assert "world-energy-outlook-2026" in out and "0.540" in out
    assert (
        main(["fuel-trajectory", "--json", "--fuel", "coal", "--scenario", "nze"]) == 0
    )
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["fuel"] == "coal" and rows[0]["scenario"] == "nze"
    assert main(["fuel-trajectory", "--fuel", "uranium"]) == 2
    assert "error" in capsys.readouterr().err
    idx = tmp_path / "idx.json"
    idx.write_text(json.dumps({"gas": {"pelp": {"2026": 1, "2036": 2}}}))
    assert (
        main(
            [
                "fuel-trajectory",
                "--index-json",
                str(idx),
                "--scenario",
                "pelp",
                "--base-year",
                "2026",
                "--years",
                "2031",
            ]
        )
        == 0
    )
    assert "1.500" in capsys.readouterr().out


def test_cli_planning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "case.json"
    src.write_text(json.dumps(_planning()), encoding="utf-8")
    assert main(["fuel-trajectory", "--planning", str(src)]) == 2
    assert "--stage-years" in capsys.readouterr().err
    rc = main(
        [
            "fuel-trajectory",
            "--planning",
            str(src),
            "--stage-years",
            "2026",
            "2035",
            "--base-year",
            "2026",
            "--map",
            "ccgt=gas",
            "--map",
            "coal1=coal",
            "--declared",
            "coal1=30,10",
        ]
    )
    assert rc == 0
    out = tmp_path / "case_fuel.json"
    assert "ccgt, coal1" in capsys.readouterr().out
    gens = {
        g["name"]: g for g in json.loads(out.read_text())["system"]["generator_array"]
    }
    assert gens["coal1"]["gcost"][0] == pytest.approx(40.0)
