# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The theorems on the committed HiGHS goldens of the ``cases/invest_*``.

* LP: every built project recovers revenue = opex + capex (profit 0 to
  1e-6 of its capex); every candidate has marginal value ≤ cost per unit;
  the market identity load payments = producers + rents holds.
* MIP, ``pricing_mode = fixed``: a profit gap and a make-whole uplift
  appear; ``relaxed`` prices shrink the uplift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_cost_recovery import compute_recovery

from .conftest import CASES

LP_GOLDENS = [
    "invest_screening_1b/golden/lp",
    "invest_screening_1b/golden/social",
    "invest_screening_1b/golden/private",
    "invest_screening_1b/golden/fom",
    "invest_garver_6b/golden/lp",
    "invest_bat_1b/golden/lp",
    "invest_wacc_1b/golden/uniform_lp",
    "invest_wacc_1b/golden/project_lp",
    "invest_multi_3b/golden/lp",
    "invest_multi_3b/golden/lp_nodesal",
]


def _golden(rel: str) -> Path:
    path = CASES / rel
    if not (path / "planning.json").is_file():
        pytest.skip(f"golden {rel} not present")
    return path


@pytest.mark.parametrize("rel", LP_GOLDENS)
def test_lp_zero_profit(rel: str) -> None:
    rep = compute_recovery(_golden(rel))
    built = [p for p in rep.projects if p.built]
    assert built, "an LP golden must build something"
    for p in built:
        if p.kind == "Demand":
            # the desalination plant sits at its water-demand bound (lmax):
            # a public project with a positive social surplus
            assert p.profit > 0.0
            continue
        assert abs(p.profit) <= 1e-6 * max(p.total_capex, 1.0) + 1e-3, p.name
        assert p.net_value_per_unit == pytest.approx(
            0.0, abs=1e-6 * p.cost_per_unit + 1e-6
        )
    for p in rep.projects:
        if not p.built:
            assert p.net_value_per_unit <= 1e-6 * max(p.cost_per_unit, 1.0)
    assert rep.market.residual == pytest.approx(
        0.0, abs=1e-6 * rep.market.load_payments
    )


ALL_GOLDENS = [
    *LP_GOLDENS,
    "invest_screening_1b/golden/mip",
    "invest_screening_1b/golden/mip_relaxed",
    "invest_garver_6b/golden/mip",
    "invest_garver_6b/golden/mip_relaxed",
    "invest_bat_1b/golden/mip",
    "invest_bat_1b/golden/mip_relaxed",
    "invest_wacc_1b/golden/uniform_mip",
    "invest_wacc_1b/golden/project_mip",
    "invest_multi_3b/golden/lp",
    "invest_multi_3b/golden/mip",
    "invest_multi_3b/golden/mip_relaxed",
    "invest_multi_3b/golden/lp_shortrun",
]


@pytest.mark.parametrize("rel", ALL_GOLDENS)
def test_no_project_is_paid_by_failure(rel: str) -> None:
    """Ample candidates + high VOLL: no unserved energy, no failure rent."""
    rep = compute_recovery(_golden(rel))
    assert rep.market.unserved_mwh == pytest.approx(0.0, abs=1e-6)
    assert rep.total_failure_rent == pytest.approx(0.0, abs=1e-6)


def test_screening_peak_paid_by_capacity_rent() -> None:
    rep = compute_recovery(_golden("invest_screening_1b/golden/lp"), price_blocks=True)
    built = {p.name: p for p in rep.projects}
    assert {k: p.new for k, p in built.items()} == pytest.approx(
        {"base": 550.0, "mid": 150.0, "peak": 320.0, "nuclear": 0.0}
    )
    peak = built["peak"]
    # The peaker never sets a price above its own cost by a variable-cost
    # spread: its whole margin is capacity rent, which pays its annuity.
    assert peak.variable_margin == pytest.approx(0.0, abs=1e-3 * peak.capex)
    assert peak.capacity_rent == pytest.approx(peak.capex, rel=1e-6)
    top = rep.blocks[0]
    assert top["set_cost"] == pytest.approx(110.0)
    assert top["capacity_rent"] * 20.0 == pytest.approx(peak.cost_per_unit, rel=1e-6)


def test_private_rate_shifts_the_mix() -> None:
    social = compute_recovery(_golden("invest_screening_1b/golden/social"))
    private = compute_recovery(_golden("invest_screening_1b/golden/private"))
    s = {p.name: p.new for p in social.projects}
    q = {p.name: p.new for p in private.projects}
    assert q["base"] < s["base"] and q["mid"] > s["mid"]


@pytest.mark.parametrize("case", ["invest_screening_1b", "invest_garver_6b"])
def test_mip_fixed_gap_relaxed_smaller(case: str) -> None:
    fixed = compute_recovery(_golden(f"{case}/golden/mip"))
    relaxed = compute_recovery(_golden(f"{case}/golden/mip_relaxed"))
    assert fixed.applied_modes == ["fixed"]
    assert relaxed.applied_modes == ["relaxed"]
    assert fixed.total_make_whole > 0.0
    assert relaxed.total_make_whole <= fixed.total_make_whole + 1e-6
    # Same MIP dispatch and builds: only the prices differ.
    assert fixed.obj_value == pytest.approx(relaxed.obj_value)
    assert [p.new for p in fixed.projects] == pytest.approx(
        [p.new for p in relaxed.projects]
    )


def test_wacc_per_project_flips_the_choice() -> None:
    uni = {
        p.name: p.new
        for p in compute_recovery(_golden("invest_wacc_1b/golden/uniform_lp")).projects
    }
    proj = {
        p.name: p.new
        for p in compute_recovery(_golden("invest_wacc_1b/golden/project_lp")).projects
    }
    assert uni["base_large"] > 0.0 and uni["base_small"] == pytest.approx(0.0)
    assert proj["base_small"] > 0.0 and proj["base_large"] == pytest.approx(0.0)


def test_multi_carrier_projects() -> None:
    rep = compute_recovery(_golden("invest_multi_3b/golden/lp"))
    by = {p.name: p for p in rep.projects}
    desal, ely = by["desal"], by["electrolyser"]
    assert desal.built and ely.built
    # desalination: water value covers electricity + the social-rate annuity
    assert desal.revenue > desal.opex + desal.capex
    assert abs(ely.profit) <= 1e-6 * ely.capex + 1e-3


def _mean_price(rel: str, bus: int) -> float:
    rep = compute_recovery(_golden(rel), price_blocks=True)
    vals = [b["price"] for b in rep.blocks if b["bus"] == bus]
    return sum(vals) / len(vals)


def test_desal_raises_the_short_run_prices() -> None:
    before = _mean_price("invest_multi_3b/golden/lp_nodesal", 3)
    short = _mean_price("invest_multi_3b/golden/lp_shortrun", 3)
    long_ = _mean_price("invest_multi_3b/golden/lp", 3)
    assert short > before * 1.05
    assert before <= long_ < short


def test_fixed_om_is_paid_with_the_annuity() -> None:
    """Fixed O&M (base 60k, mid 25k, peak 10k $/MW-yr): still zero profit."""
    rep = compute_recovery(_golden("invest_screening_1b/golden/fom"))
    fom = {"base": 60_000.0, "mid": 25_000.0, "peak": 10_000.0}
    for p in rep.projects:
        if not p.built:
            continue
        assert p.fixed_om == pytest.approx(p.new * fom[p.name], rel=1e-6)
        assert p.margin_new == pytest.approx(p.fixed_om + p.capex, rel=1e-6, abs=1.0)
