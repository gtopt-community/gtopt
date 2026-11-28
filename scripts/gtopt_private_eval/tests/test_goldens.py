# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""End to end on the committed ``cases/invest_*`` HiGHS goldens.

* Zero-profit theorem, private side: with no fixed O&M, the planning
  rate as private WACC and the energy-only market, every project the LP
  built has NPV 0 (its margin is exactly its annuity).
* Chile: sufficiency + SSCC lift every project above the planning view;
  a regulated line earns the grossed-up regulated rate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.evaluate import evaluate
from gtopt_private_eval.params import Params, load_config, set_path

from .conftest import CONFIGS, golden

CASES = [
    ("invest_screening_1b/golden/lp", "invest_screening.json"),
    ("invest_screening_1b/golden/private", "invest_screening_private.json"),
    ("invest_bat_1b/golden/lp", "invest_bat.json"),
    ("invest_garver_6b/golden/lp", "invest_garver.json"),
    ("invest_wacc_1b/golden/project_lp", "invest_wacc.json"),
    ("invest_wacc_1b/golden/uniform_lp", "invest_wacc.json"),
    ("invest_cl_screening_1b/golden/lp", "invest_screening.json"),
    ("invest_cl_bat_1b/golden/lp", "invest_bat.json"),
]


def _params(cfg_name: str, **sets: object) -> Params:
    cfg = load_config(CONFIGS / cfg_name)
    for k, v in sets.items():
        set_path(cfg, k.replace("__", "."), v)
    return Params(cfg)


@pytest.mark.parametrize(("rel", "cfg"), CASES)
def test_lp_zero_npv_planning_equivalent(rel: str, cfg: str) -> None:
    sysd = load_system(golden(rel))
    # the theorem holds for the plan's flat (base-year) costs and prices
    prm = _params(
        cfg,
        finance__fom_pct_capex=0.0,
        finance__risk_costs=False,
        finance__fuel_trajectory__enabled=False,
    )
    rep = evaluate(sysd, prm, "energy_only")
    assert rep.projects
    for p in rep.projects:
        # the private engine reproduces the planning margin and annuity
        assert p.bridge()["energy reconciliation"] == pytest.approx(
            0.0, abs=1e-6 * p.capex + 1.0
        )
        assert p.private_annuity == pytest.approx(p.planning_annuity, rel=1e-4)
        assert abs(p.cash.npv) <= 1e-4 * p.capex, p.name
        if p.cash.irr is not None:
            assert p.cash.irr == pytest.approx(p.wacc, abs=1e-4)


def test_screening_chile_lifts_every_project() -> None:
    sysd = load_system(golden("invest_screening_1b/golden/lp"))
    prm = _params("invest_screening.json")
    eo = {p.name: p for p in evaluate(sysd, prm, "energy_only").projects}
    cl = {p.name: p for p in evaluate(sysd, prm, "cl").projects}
    assert set(cl) == {"base", "mid", "peak"}
    for name, p in cl.items():
        assert p.year1.revenues["sufficiency"] > 0
        assert p.cash.npv > eo[name].cash.npv
        # energy only with 2 % fixed O&M: the LP annuity no longer pays
        assert eo[name].cash.npv < 0
    assert cl["peak"].year1.costs.get("green_tax", 0.0) > 0


def test_battery_chile_revenue_stack() -> None:
    sysd = load_system(golden("invest_bat_1b/golden/lp"))
    prm = _params("invest_bat.json")
    (p,) = evaluate(sysd, prm, "cl").projects
    rev = p.year1.revenues
    assert p.capex == pytest.approx(200000 * p.new_mwh + 100000 * p.new_mw)
    assert p.life == 15
    assert rev["sufficiency"] > 0.5 * rev["energy"]
    assert p.co2_avoided_t > 0  # charges on solar, discharges on gas
    sens = p.sensitivity["npv_vs_co2_price"]
    assert sens["100"] > sens["0"]
    assert set(p.sensitivity["npv_vs_capex_scenario"]) == {
        "conservative 2030",
        "moderate 2030",
        "advanced 2030",
    }


def test_garver_regulated_vs_merchant() -> None:
    sysd = load_system(golden("invest_garver_6b/golden/lp"))
    prm = _params("invest_garver.json")
    cl = evaluate(sysd, prm, "cl").projects
    eo = evaluate(sysd, prm, "energy_only").projects
    for p in cl:
        assert p.cash.irr == pytest.approx(0.07 / 0.73, abs=1e-4)
    for p in eo:
        assert p.cash.irr is not None and p.cash.irr < p.wacc


def test_cod_year_trajectory(tmp_path: Path) -> None:
    sysd = load_system(golden("invest_bat_1b/golden/lp"))
    base = evaluate(sysd, _params("invest_bat.json"), "cl").projects[0]
    later = evaluate(
        sysd, _params("invest_bat.json", projects__bess__cod_year=2030), "cl"
    ).projects[0]
    assert later.capex < base.capex
    assert later.cash.npv > base.cash.npv
    none = evaluate(
        sysd,
        _params(
            "invest_bat.json",
            finance__capex_trajectory__cod_year=None,
            finance__capex_trajectory__sensitivity_year=None,
        ),
        "cl",
    ).projects[0]
    assert none.capex == pytest.approx(base.capex)
    assert "npv_vs_capex_scenario" not in none.sensitivity
    del tmp_path


def test_chile_case_no_failure_sufficiency_pays_the_peaker() -> None:
    """Chile demo: no unserved energy; the CMg (capped at the costliest
    unit) gives the peaker no margin; its scarcity rent in the LP price
    equals its annuity, and the sufficiency payment replaces it."""
    sysd = load_system(golden("invest_cl_screening_1b/golden/lp"))
    assert sysd.unserved_mwh == 0.0
    assert sysd.max_srmc == pytest.approx(110.0)
    prm = _params("invest_screening.json")
    rep = evaluate(sysd, prm, "cl")
    assert not any("WARNING" in n for n in rep.notes)
    peak = next(p for p in rep.projects if p.name == "peak")
    y1 = peak.year1
    margin = (
        y1.revenues["energy"] + y1.revenues["scarcity_excluded"] - y1.costs["variable"]
    )
    assert margin == pytest.approx(0.0, abs=1.0)
    assert -y1.revenues["scarcity_excluded"] == pytest.approx(
        peak.planning_annuity, rel=1e-4
    )
    assert y1.revenues["sufficiency"] > peak.planning_annuity
    bat = evaluate(
        load_system(golden("invest_cl_bat_1b/golden/lp")),
        _params("invest_bat.json"),
        "cl",
    ).projects[0]
    assert "scarcity_excluded" not in bat.year1.revenues
