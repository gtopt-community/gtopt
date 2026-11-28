# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Storage valuation: cash default and the opt-in shadow diagnostic.

Three storages, each with a hand solution written as gtopt outputs
(synthetic tests) and the solved gtopt case (integration tests):

* LNG — ``cases/lng_spot_1b`` (README hand solution);
* reservoir — ``cases/reservoir_water_value/spill_free.json`` (water value
  0 while spilling, 50 / fcr when dry);
* battery — one stage of two 1-h blocks (charge at 10, discharge at 50),
  and ``cases/invest_bat_1b/invest_bat_lp.json`` solved.

The reconciliation checked for every storage: Σ ϑ · draw = Σ ϑ ·
(inflow − vent) − Σ ϑ · ΔE − Σ ϑ · loss (the balance row priced at its
dual, block by block), and unit margins + storage margin = one-owner
profit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.evaluate import evaluate
from gtopt_private_eval.main import main
from gtopt_private_eval.params import Params, load_config
from gtopt_private_eval.storage_value import (
    StorageAccount,
    notes,
    valuation_modes,
)

from .conftest import CASES, sim, write_case

LNG = CASES / "lng_spot_1b" / "lng_spot_1b.json"
RSV = CASES / "reservoir_water_value" / "spill_free.json"
BAT = CASES / "invest_bat_1b" / "invest_bat_lp.json"
LNG_SCALE = 8760.0 / 2160.0
RSV_SCALE = 8760.0 / 6.0
FCR = 0.0036


def _rows(d: dict[int, float], uid: int = 1) -> list[tuple]:
    return [(1, t, t, uid, v) for t, v in d.items()]


def _balance(acc: StorageAccount, rel: float = 1e-9) -> None:
    """The storage balance priced at its dual reconciles the accounts."""
    scale = max(abs(acc.shadow_fuel), abs(acc.inflow_value), 1.0)
    assert acc.shadow_fuel == pytest.approx(
        acc.inflow_value - acc.inventory_change_value - acc.loss_value,
        rel=rel,
        abs=rel * scale,
    )
    assert acc.unit_margin + acc.storage_margin == pytest.approx(acc.owner_profit)
    assert acc.economic_profit == pytest.approx(
        acc.owner_profit + acc.closing_value - acc.opening_value
    )


# ── LNG ──────────────────────────────────────────────────────────────


def _lng_case(rdir: Path) -> Path:
    return write_case(
        rdir,
        json.loads(LNG.read_text(encoding="utf-8")),
        {
            "Bus/balance_dual": _rows({1: 4.0, 2: 200.0, 3: 200.0}),
            "Generator/generation_sol": _rows({1: 100.0, 2: 100.0, 3: 58000.0 / 720.0})
            + _rows({3: 100.0 - 58000.0 / 720.0}, 2),
            "LngTerminal/energy_dual": _rows({1: -5.0, 2: 975.0, 3: 975.0}),
            "LngTerminal/energy_sol": _rows({1: 20000.0, 2: 5600.0, 3: 0.0}),
            "LngTerminal/delivery_sol": [(1, 1, 1, 1, 40000.0 / 720.0)],
            "LngTerminal/drain_sol": [(1, 1, 1, 1, 5600.0 / 720.0)],
            "LngTerminal/spot_sol": [(1, 3, 1, 1, 1.0)],
        },
        obj=5248000.0,
    )


def _check_lng(acc: StorageAccount) -> None:
    def yr(x: float) -> Any:
        return pytest.approx(x * LNG_SCALE, rel=1e-6, abs=1e-3)

    assert (acc.kind, acc.units) == ("lng_terminal", ["gas"])
    assert acc.energy_revenue == yr(26_288_000.0)
    assert acc.vom == yr(1_010_000.0)
    assert acc.shadow_fuel == yr(25_278_000.0)
    assert acc.details["contract_volume"] == yr(40_000.0)
    assert acc.details["contract_cost"] == yr(1_200_000.0)
    assert acc.details["spot_cargoes"] == yr(1.0)
    assert acc.details["spot_cost"] == yr(210_000.0)
    assert acc.vent == yr(5_600.0)
    assert acc.vent_cost == yr(28_000.0)
    assert acc.refill_cost == yr(1_410_000.0)
    # the gas unit is marginal in every stage: no margin at shadow value
    assert acc.unit_margin == pytest.approx(0.0, abs=1e-6 * acc.energy_revenue)
    assert acc.storage_margin == yr(23_840_000.0)
    assert acc.owner_profit == yr(23_840_000.0)
    assert acc.inflow_value == yr(5_678_000.0)
    assert acc.inventory_change_value == yr(-19_600_000.0)
    assert acc.closing_value == pytest.approx(0.0, abs=1e-6 * acc.energy_revenue)
    _balance(acc, rel=1e-6)


def test_lng_hand_accounts(tmp_path: Path) -> None:
    sysd = load_system(_lng_case(tmp_path / "out"), existing=["gas"])
    assert sysd.storage is not None
    (acc,) = sysd.storage.accounts({"lng_terminal"})
    _check_lng(acc)
    assert acc.to_dict()["owner_profit"] == pytest.approx(acc.owner_profit)
    # project rows: VOM (gcost) + shadow fuel 0.2·ϑ
    (gas,) = [p for p in sysd.projects if p.name == "gas"]
    assert gas.storage_kind == "lng_terminal"
    assert [r.fuel_value for r in gas.rows] == pytest.approx([-1.0, 195.0, 195.0])
    # cash (default): VOM + gas at the contract price, 0.2 × 30 = 6 $/MWh
    assert [r.fuel_cash for r in gas.rows] == pytest.approx([6.0, 6.0, 6.0])
    assert gas.variable_cost() == pytest.approx(
        (1_010_000.0 + 6.0 * 202_000.0) * LNG_SCALE
    )
    assert gas.variable_cost(shadow=True) == pytest.approx(
        (1_010_000.0 + 25_278_000.0) * LNG_SCALE
    )


def test_lng_report_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rdir = _lng_case(tmp_path / "out")
    sysd = load_system(rdir, existing=["gas"])
    cfg = load_config(None)
    cfg["storage_valuation"] = {"lng_terminal": "shadow"}
    rep = evaluate(sysd, Params(cfg), "cl")
    (lng,) = [a for a in rep.storages if a["kind"] == "lng_terminal"]
    assert lng["name"] == "tank"
    assert lng["details"]["spot_cargoes"] == pytest.approx(LNG_SCALE)
    assert any("storage_valuation = shadow" in n for n in rep.notes)
    assert rep.to_dict()["storages"] == rep.storages
    # cash (default): the terminal account lists the cargo purchases,
    # the gas unit pays VOM + gas at the purchase price
    rep0 = evaluate(sysd, Params(load_config(None)), "energy_only")
    (acc0,) = rep0.storages
    assert acc0["refill_cost"] == pytest.approx(1_410_000.0 * LNG_SCALE)
    assert not any("storage_valuation = shadow" in n for n in rep0.notes)
    gas0 = {p.name: p for p in rep0.projects}["gas"]
    assert gas0.cash.years[0].costs["variable"] == pytest.approx(
        (1_010_000.0 + 6.0 * 202_000.0) * LNG_SCALE
    )
    args = [str(rdir), "--market", "cl", "--existing", "gas"]
    assert main([*args, "--storage-valuation", "lng_terminal=shadow"]) == 0
    assert "storage lng_terminal tank: units gas" in capsys.readouterr().out
    assert main([*args, "--storage-valuation", "shadow", "-q"]) == 0
    assert main([*args, "--storage-valuation", "lng_terminal=bogus", "-q"]) == 2


# ── reservoir ────────────────────────────────────────────────────────


def _rsv_case(rdir: Path) -> Path:
    q3 = 0.5 / FCR / 2.0  # 69.44 m³/s empty the reservoir in the dry stage
    return write_case(
        rdir,
        json.loads(RSV.read_text(encoding="utf-8")),
        {
            "Bus/balance_dual": _rows({1: 0.0, 2: 0.0, 3: 50.0}),
            "Generator/generation_sol": _rows({1: 100.0, 2: 100.0, 3: q3})
            + _rows({3: 100.0 - q3}, 2),
            "Waterway/flow_sol": _rows({1: 100.0, 2: 100.0, 3: q3}),
            "Reservoir/drain_sol": _rows({1: 400.0, 2: 400.0}),
            "Reservoir/energy_sol": _rows({1: 0.5, 2: 0.5, 3: 0.0}),
            "Reservoir/water_value_dual": _rows({1: 0.0, 2: 0.0, 3: 50.0 / FCR}),
        },
        obj=3055.5555555555557,
    )


def _check_rsv(acc: StorageAccount) -> None:
    def yr(x: float) -> Any:
        return pytest.approx(x * RSV_SCALE, rel=1e-6, abs=1e-3)

    assert (acc.kind, acc.units) == ("reservoir", ["g_hydro"])
    # dry stage: 0.5 hm³ turbined at 50/fcr = 50 $/MWh · 69.44 MW · 2 h
    assert acc.shadow_fuel == yr(50.0 * 0.5 / FCR)
    assert acc.energy_revenue == yr(50.0 * 0.5 / FCR)
    tol = 1e-6 * acc.energy_revenue  # solver noise on the duals
    assert acc.unit_margin == pytest.approx(0.0, abs=tol)
    assert acc.inflow == yr(2 * 500.0 * FCR * 2.0)  # natural inflow, free
    assert acc.refill_cost == 0.0
    assert acc.vent == yr(2 * 400.0 * FCR * 2.0)
    assert acc.inflow_value == pytest.approx(0.0, abs=tol)  # ϑ = 0 when wet
    assert acc.inventory_change_value == yr(-50.0 * 0.5 / FCR)
    assert acc.opening_value == pytest.approx(0.0, abs=tol)
    _balance(acc, rel=1e-6)


def test_reservoir_hand_accounts(tmp_path: Path) -> None:
    sysd = load_system(_rsv_case(tmp_path / "out"), existing=["g_hydro"])
    assert sysd.storage is not None
    (acc,) = sysd.storage.accounts({"reservoir"})
    _check_rsv(acc)
    (hyd,) = [p for p in sysd.projects if p.name == "g_hydro"]
    assert hyd.storage_kind == "reservoir"
    assert [r.fuel_value for r in hyd.rows] == pytest.approx([0.0, 0.0, 50.0])


def _npvs(rep: Any) -> dict[str, float]:
    return {p.name: p.cash.npv for p in rep.projects}


def test_cl_default_is_cash_and_unchanged(tmp_path: Path) -> None:
    """cl default: hydro with a reservoir pays only its VOM (water is free);
    the report equals the one without any storage model (master)."""
    rdir = _rsv_case(tmp_path / "out")
    sysd = load_system(rdir, existing=["g_hydro", "g_thermal"])
    cfg = load_config(None)
    rep = evaluate(sysd, Params(cfg), "cl")
    assert not rep.storages
    assert not any("storage" in n for n in rep.notes)
    hyd = {p.name: p for p in rep.projects}["g_hydro"]
    assert hyd.cash.years[0].costs.get("variable", 0.0) == pytest.approx(0.0)
    # master behaviour: no storage model at all
    ref = load_system(rdir, existing=["g_hydro", "g_thermal"])
    ref.storage = None
    for proj in ref.projects:
        proj.storage_kind = None
        for row in proj.rows:
            row.fuel_value = row.fuel_cash = 0.0
    assert _npvs(rep) == pytest.approx(_npvs(evaluate(ref, Params(cfg), "cl")))
    # opt-in shadow diagnostic: the water value is charged to the turbine
    cfg["storage_valuation"] = {"reservoir": "shadow"}
    rep_s = evaluate(sysd, Params(cfg), "cl")
    assert [a["kind"] for a in rep_s.storages] == ["reservoir"]
    hyd_s = {p.name: p for p in rep_s.projects}["g_hydro"]
    var = hyd_s.cash.years[0].costs.get("variable", 0.0)
    assert var == pytest.approx(50.0 * 0.5 / FCR * RSV_SCALE, rel=1e-6)


# ── battery ──────────────────────────────────────────────────────────


def _bat_planning() -> dict[str, Any]:
    return {
        "options": {"model_options": {"use_single_bus": True}},
        "simulation": sim([1.0, 1.0]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [
                {"uid": 1, "name": "bess_gen", "bus": "b1", "gcost": 0.0},
            ],
            "demand_array": [{"uid": 1, "name": "bess_dem", "bus": "b1"}],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "bess",
                    "bus": "b1",
                    "input_efficiency": 0.9,
                    "output_efficiency": 0.9,
                    "annual_standing_loss": 0.0,
                    "eini": 0.0,
                    "emax": 100.0,
                }
            ],
        },
    }


def test_battery_hand_accounts(tmp_path: Path) -> None:
    """Charge 10 MW at 10 $/MWh, discharge 8.1 MW at 50 $/MWh."""
    rdir = write_case(
        tmp_path / "out",
        _bat_planning(),
        {
            "Bus/balance_dual": [(1, 1, 1, 1, 10.0), (1, 1, 2, 1, 50.0)],
            "Generator/generation_sol": [(1, 1, 2, 1, 8.1)],
            "Demand/load_sol": [(1, 1, 1, 1, 10.0)],
            "Battery/finp_sol": [(1, 1, 1, 1, 10.0)],
            "Battery/fout_sol": [(1, 1, 2, 1, 8.1)],
            "Battery/energy_sol": [(1, 1, 1, 1, 9.0), (1, 1, 2, 1, 0.0)],
            "Battery/energy_dual": [(1, 1, 1, 1, 10.0 / 0.9), (1, 1, 2, 1, 45.0)],
        },
    )
    sysd = load_system(rdir, existing=["bess"])
    assert sysd.storage is not None
    (acc,) = sysd.storage.accounts({"battery"})
    yr = 8760.0 / 2.0
    assert acc.units == ["bess_gen"]
    assert acc.energy_revenue == pytest.approx(405.0 * yr)
    assert acc.shadow_fuel == pytest.approx(405.0 * yr)
    assert acc.refill_cost == pytest.approx(100.0 * yr)  # grid charge at CMg
    assert acc.inflow == pytest.approx(9.0 * yr)
    assert acc.inflow_value == pytest.approx(100.0 * yr)
    assert acc.inventory_change_value == pytest.approx(-305.0 * yr)
    assert acc.unit_margin == pytest.approx(0.0, abs=1e-6)
    assert acc.owner_profit == pytest.approx(305.0 * yr)
    _balance(acc)
    text = notes({"battery"}, [acc])
    assert len(text) == 2 and "battery bess: units bess_gen" in text[1]


# ── option parsing / topology ────────────────────────────────────────


def test_valuation_modes() -> None:
    cash = dict.fromkeys(("lng_terminal", "reservoir", "battery"), "cash")
    assert valuation_modes(None, "energy_only") == cash
    assert valuation_modes(None, "cl") == cash
    assert set(valuation_modes("shadow", "cl").values()) == {"shadow"}
    assert valuation_modes({"battery": "shadow"}, "cl") == {
        "lng_terminal": "cash",
        "reservoir": "cash",
        "battery": "shadow",
    }
    with pytest.raises(ValueError, match="unknown storage type"):
        valuation_modes({"tank": "shadow"}, "cl")
    with pytest.raises(ValueError, match="expected one of"):
        valuation_modes("declared", "cl")


def test_run_of_river_and_links(tmp_path: Path) -> None:
    """A turbine without an upstream reservoir is not storage-valued;
    LNG links by uid / name, unknown names are ignored."""
    plan = json.loads(RSV.read_text(encoding="utf-8"))
    system = plan["system"]
    system["junction_array"].append({"uid": 3, "name": "j_ror"})
    system["waterway_array"].append(
        {"uid": 2, "name": "ww_ror", "junction_a": "j_ror", "junction_b": "j_sea"}
    )
    system["generator_array"].append({"uid": 3, "name": "g_ror", "bus": 1})
    system["turbine_array"].append(
        {"uid": 2, "name": "t_ror", "waterway": "ww_ror", "generator": "g_ror"}
    )
    system["lng_terminal_array"] = [
        {
            "uid": 7,
            "name": "t",
            "generators": [
                {"generator": "g_thermal", "heat_rate": 0.2},
                {"generator": 3},
                {"generator": "missing"},
            ],
        }
    ]
    rdir = write_case(tmp_path / "out", plan, {"Bus/balance_dual": _rows({1: 1.0})})
    sysd = load_system(rdir)
    model = sysd.storage
    assert model is not None
    assert model.kind_of(1) == "reservoir"
    assert model.kind_of(2) == "lng_terminal"
    assert model.kind_of(3) == "lng_terminal"  # LNG link, not the ROR turbine
    accs = model.accounts({"lng_terminal", "reservoir", "battery"})
    assert sorted(a.kind for a in accs) == ["lng_terminal", "reservoir"]
    lng = next(a for a in accs if a.kind == "lng_terminal")
    assert lng.units == ["g_ror", "g_thermal"]


# ── solved cases ─────────────────────────────────────────────────────


def _gtopt_bin() -> str | None:
    env = os.environ.get("GTOPT_BIN")
    if env and Path(env).exists():
        return env
    return shutil.which("gtopt")


def _solve(case: Path, tmp_path: Path) -> Path:
    gtopt = _gtopt_bin()
    if gtopt is None:
        pytest.skip("gtopt binary not found (GTOPT_BIN)")
    work = tmp_path / "case"
    work.mkdir()
    shutil.copy(case, work / case.name)
    solver = os.environ.get("GTOPT_SOLVER", "highs")
    if solver == "clp":
        solver = "highs"  # integer spot slots: never CLP for a MIP
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'ulimit -c unlimited; exec "{gtopt}" {case.name} --solver {solver}'
            " --set output_directory=out --set output_format=csv",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return work / "out"


@pytest.mark.integration
def test_solved_lng_case(tmp_path: Path) -> None:
    """lng_spot_1b solved: the output tank dual is the hand LP dual."""
    sysd = load_system(_solve(LNG, tmp_path), existing=["gas"])
    assert sysd.storage is not None
    (acc,) = sysd.storage.accounts({"lng_terminal"})
    _check_lng(acc)


@pytest.mark.integration
def test_solved_reservoir_case(tmp_path: Path) -> None:
    sysd = load_system(_solve(RSV, tmp_path), existing=["g_hydro"])
    assert sysd.storage is not None
    (acc,) = sysd.storage.accounts({"reservoir"})
    _check_rsv(acc)


@pytest.mark.integration
def test_solved_battery_case(tmp_path: Path) -> None:
    sysd = load_system(_solve(BAT, tmp_path))
    assert sysd.storage is not None
    (acc,) = sysd.storage.accounts({"battery"})
    assert acc.shadow_fuel > 0.0
    _balance(acc, rel=1e-6)
