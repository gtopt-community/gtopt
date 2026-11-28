# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Investor risk across scenarios (hydrologies)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.evaluate import evaluate
from gtopt_private_eval.main import main
from gtopt_private_eval.params import Params, load_config, set_path
from gtopt_private_eval.risk import cvar, measure, risk_config, tail_order, trimmed_mean

from .conftest import Rows, golden, write_case

# ── measures ─────────────────────────────────────────────────────────


def test_config() -> None:
    assert risk_config({}, {}) is None
    assert risk_config({"risk": {"mode": "none"}}, {}) is None
    cfg = risk_config({"risk": {"mode": "cvar"}}, {"risk": {"alpha": 0.5}})
    assert cfg is not None
    assert (cfg["mode"], cfg["tail"], cfg["alpha"], cfg["trim"]) == (
        "cvar",
        "auto",
        0.5,
        1,
    )
    for bad in (
        {"mode": "x"},
        {"mode": "cvar", "tail": "x"},
        {"mode": "cvar", "alpha": 0.0},
        {"mode": "trim", "trim": -1},
    ):
        with pytest.raises(ValueError, match="risk"):
            risk_config({"risk": bad}, {})


def test_cvar_trim_and_order() -> None:
    probs = {1: 0.25, 2: 0.25, 3: 0.5}
    npv = {1: -10.0, 2: 20.0, 3: 0.0}
    order = tail_order(npv, "low_profit", None)
    assert order == [1, 3, 2]
    # 0.4 mass: all of scenario 1 (0.25) + 0.15 of scenario 3
    value, used = cvar(order, probs, npv, 0.4)
    assert value == pytest.approx((0.25 * -10.0 + 0.15 * 0.0) / 0.4)
    assert used == [1, 3]
    tm, cut = trimmed_mean(order, probs, npv, 1)
    assert cut == [1] and tm == pytest.approx((0.25 * 20.0) / 0.75)
    # never trims every scenario
    tm, cut = trimmed_mean(order, probs, npv, 9)
    assert cut == [1, 3] and tm == pytest.approx(20.0)
    # hydrology ranking: dry = driest first, wet = wettest first
    wet = {1: 3.0, 2: 1.0, 3: 2.0}
    assert tail_order(npv, "dry", wet) == [2, 3, 1]
    assert tail_order(npv, "wet", wet) == [1, 3, 2]


def test_measure_fallbacks() -> None:
    cfg = {"mode": "trim", "tail": "dry", "alpha": 0.5, "trim": 1}
    res = measure(cfg, {1: 1.0}, {1: 5.0}, {1: 0.1}, 0.1, None)
    assert res.tail == "low_profit"
    assert any("no hydrology index" in n for n in res.notes)
    assert any("one scenario" in n for n in res.notes)
    assert res.expectation == res.cvar == res.trimmed_mean == 5.0
    assert res.headline == 5.0
    d = res.to_dict()
    assert d["scenarios"]["1"]["npv"] == 5.0 and d["headline"] == 5.0


# ── a three-hydrology case ───────────────────────────────────────────

#: (price, hydro MW, ccgt MW) per hydrology: dry, median, wet
_HYD = {1: (100.0, 20.0, 50.0), 2: (60.0, 40.0, 50.0), 3: (20.0, 60.0, 0.0)}


def _hydro_case(tmp_path: Path) -> Path:
    """One bus, one 8760-h block, three equiprobable hydrologies.

    A new hydro plant (no variable cost) and a new CCGT (60 $/MWh, sets
    the price in the median year).  Dry year: little water, high price;
    wet year: much water, low price.  Hydro revenue per hour
    20×100 = 2000 (dry), 40×60 = 2400 (median), 60×20 = 1200 (wet): its
    worst hydrology is the WET one -- the price effect beats the volume
    effect.  The CCGT earns 40×50 in the dry year and nothing otherwise.
    """
    gens = [
        {
            "uid": 1,
            "name": "hydro_new",
            "bus": "b1",
            "gcost": 0.0,
            "capacity": 0,
            "expcap": 10,
            "expmod": 10,
            "overnight_capcost": 1.0e6,
            "economic_life": 30,
        },
        {
            "uid": 2,
            "name": "ccgt_new",
            "bus": "b1",
            "gcost": 60.0,
            "capacity": 0,
            "expcap": 10,
            "expmod": 10,
            "overnight_capcost": 0.2e6,
            "economic_life": 25,
        },
    ]
    planning = {
        "options": {"annual_discount_rate": 0.08},
        "simulation": {
            "block_array": [{"uid": 1, "duration": 8760.0}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
            "scenario_array": [
                {"uid": s, "probability_factor": 1.0, "hydrology": s - 1} for s in _HYD
            ],
        },
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": gens,
            "demand_array": [{"uid": 1, "name": "d", "bus": "b1", "lmax": 200}],
        },
    }
    price: Rows = [(s, 1, 1, 1, p) for s, (p, _h, _c) in _HYD.items()]
    gen: Rows = [(s, 1, 1, 1, h) for s, (_p, h, _c) in _HYD.items()]
    gen += [(s, 1, 1, 2, c) for s, (_p, _h, c) in _HYD.items()]
    streams = {
        "Bus/balance_dual": price,
        "Generator/generation_sol": gen,
        "Generator/capainst_sol": [(0, 1, 0, 1, 60.0), (0, 1, 0, 2, 50.0)],
        "Generator/capacost_sol": [(0, 1, 0, 1, 1.0), (0, 1, 0, 2, 1.0)],
        "Demand/load_sol": [(s, 1, 1, 1, h + c) for s, (_p, h, c) in _HYD.items()],
    }
    return write_case(tmp_path / "hyd", planning, streams)


def _params(**sets: Any) -> Params:
    cfg = load_config(None)
    set_path(cfg, "finance.fuel_trajectory.enabled", False)
    set_path(cfg, "finance.risk_costs", False)
    for k, v in sets.items():
        set_path(cfg, k.replace("__", "."), v)
    return Params(cfg)


def _eval(tmp_path: Path, **sets: Any) -> Any:
    rep = evaluate(load_system(_hydro_case(tmp_path)), _params(**sets), "energy_only")
    return rep, {p.name: p for p in rep.projects}


def test_profit_by_hydrology_sets_the_tail(tmp_path: Path) -> None:
    _rep, by = _eval(tmp_path, risk={"mode": "cvar", "alpha": 1 / 3})
    hydro, ccgt = by["hydro_new"].risk, by["ccgt_new"].risk
    assert hydro is not None and ccgt is not None
    npv = {s: v["npv"] for s, v in hydro.scenarios.items()}
    # volume × price: the hydro plant's worst hydrology is the wet one
    assert npv[3] < npv[1] < npv[2]
    assert hydro.order == [3, 1, 2] and hydro.tail == "low_profit"
    assert hydro.cvar == pytest.approx(npv[3])
    assert hydro.trimmed_mean == pytest.approx(0.5 * (npv[1] + npv[2]))
    # expectation of the scenario NPVs = the one-year evaluation
    assert hydro.expectation == pytest.approx(by["hydro_new"].cash.npv, rel=1e-9)
    assert ccgt.expectation == pytest.approx(by["ccgt_new"].cash.npv, rel=1e-9)
    # the CCGT earns only in the dry year
    assert ccgt.order[-1] == 1
    assert ccgt.scenarios[1]["index"] == pytest.approx(20.0 * 8760.0)


def test_dry_tail_is_an_explicit_choice(tmp_path: Path) -> None:
    _rep, by = _eval(tmp_path, risk={"mode": "trim", "tail": "dry", "trim": 1})
    hydro = by["hydro_new"].risk
    assert hydro is not None
    # ranked by the wetness index (hydro energy): driest first
    assert hydro.order == [1, 2, 3] and hydro.trimmed_scenarios == [1]
    npv = {s: v["npv"] for s, v in hydro.scenarios.items()}
    assert hydro.headline == pytest.approx(0.5 * (npv[2] + npv[3]))
    # a user index wins
    _rep, by = _eval(
        tmp_path,
        risk={
            "mode": "trim",
            "tail": "wet",
            "hydrology_index": {"1": 9, "2": 5, "3": 1},
        },
    )
    hydro = by["hydro_new"].risk
    assert hydro is not None and hydro.order == [1, 2, 3]


def test_no_index_falls_back_and_stream_warning(tmp_path: Path) -> None:
    rep = evaluate(
        load_system(golden("invest_screening_1b/golden/lp")),
        _params(
            risk={"mode": "cvar", "tail": "wet"},
            finance__stream_rates={"rates": {"energy": 0.1}},
        ),
        "energy_only",
    )
    notes = " ".join(rep.notes)
    assert "no hydrology index" in notes and "one scenario" in notes
    assert "count it once" in notes
    del tmp_path


def test_cli_prints_risk(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rdir = _hydro_case(tmp_path)
    cfg = tmp_path / "risk.json"
    cfg.write_text(
        json.dumps(
            {
                "risk": {"mode": "cvar", "alpha": 0.34},
                "finance": {"fuel_trajectory": {"enabled": False}},
                "projects": {"ccgt_new": {"risk": {"mode": "none"}}},
            }
        ),
        encoding="utf-8",
    )
    jout = tmp_path / "r.json"
    assert main([str(rdir), "--config", str(cfg), "--json-out", str(jout)]) == 0
    out = capsys.readouterr().out
    assert "risk (cvar, tail low_profit)" in out and "NPV by scenario" in out
    assert "risk NPV" in out
    ccgt_row = next(ln for ln in out.splitlines() if ln.startswith("  ccgt_new "))
    assert ccgt_row.rstrip().endswith("-")
    data = json.loads(jout.read_text(encoding="utf-8"))
    by = {p["name"]: p for p in data["projects"]}
    assert by["hydro_new"]["risk"]["order"] == [3, 1, 2]
    assert by["ccgt_new"]["risk"] is None


def test_private_cvar_verification(tmp_path: Path) -> None:
    """The CCGT pays at the expectation but fails the private CVaR: it
    earns only in the dry year -- unpaid private risk."""
    rep, by = _eval(tmp_path, risk={"mode": "cvar", "alpha": 1 / 3})
    ccgt, hydro = by["ccgt_new"].risk, by["hydro_new"].risk
    assert ccgt is not None and hydro is not None
    assert ccgt.passes_expectation and not ccgt.passes_cvar
    assert hydro.passes_expectation == (hydro.expectation >= 0.0)
    note = next(n for n in rep.notes if n.startswith("ccgt_new: FAILS"))
    assert "expected NPV is positive" in note and "contract" in note
    d = by["ccgt_new"].to_dict()["risk"]
    assert d["passes_expectation"] is True and d["passes_cvar"] is False
