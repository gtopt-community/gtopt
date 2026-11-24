# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Uplift metrics on the synthetic 2-bus UC toy."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from gtopt_pricing_uplift import compute_uplift

from .conftest import TOY_PLANNING, UPLIFT, write_toy_results


@pytest.mark.parametrize("mode", ["fixed", "fast_start", "relaxed"])
def test_uplift_per_mode(toy_results, mode: str) -> None:
    rep = compute_uplift(toy_results(mode))
    mwp, loc, total = UPLIFT[mode]
    assert rep.pricing_mode == mode
    assert rep.make_whole == pytest.approx(mwp)
    assert rep.lost_opportunity == pytest.approx(loc)
    assert rep.total == pytest.approx(total)
    names = {u.commitment for u in rep.units}
    assert names == {"g_slow_uc", "g_fast_uc"}


def test_unit_breakdown_fixed(toy_results) -> None:
    rep = compute_uplift(toy_results("fixed"))
    by = {u.commitment: u for u in rep.units}
    fast = by["g_fast_uc"]
    # λ = 10 < c = 50 at pmin 40: margin -1600, start-up 1000.
    assert fast.energy_mwh == pytest.approx(40.0)
    assert fast.energy_margin == pytest.approx(-1600.0)
    assert fast.commitment_cost == pytest.approx(1000.0)
    assert fast.profit == pytest.approx(-2600.0)
    assert fast.make_whole == pytest.approx(2600.0)
    assert fast.lost_opportunity == pytest.approx(0.0)
    slow = by["g_slow_uc"]
    assert slow.bus == "b1"
    assert slow.profit == pytest.approx(-600.0)  # two hours of no-load
    assert slow.self_schedule_profit == pytest.approx(0.0)


def test_unit_breakdown_fast_start(toy_results) -> None:
    rep = compute_uplift(toy_results("fast_start"))
    slow = next(u for u in rep.units if u.commitment == "g_slow_uc")
    # Self-schedule: 120 MW at 60 in hour 1 (-300 no-load), off in hour 2.
    assert slow.self_schedule_profit == pytest.approx(5700.0)
    assert slow.profit == pytest.approx(4400.0)
    assert slow.lost_opportunity == pytest.approx(1300.0)
    assert slow.total == pytest.approx(1300.0)


def test_price_stats(toy_results) -> None:
    rep = compute_uplift(toy_results("relaxed"), ref_bus="b2")
    ps = rep.prices
    assert ps.ref_bus == "b2"
    assert ps.node_hours == pytest.approx(4.0)
    assert ps.negative_node_hours == pytest.approx(0.0)
    assert ps.ref_mean == pytest.approx(36.25)
    assert ps.ref_min == pytest.approx(12.5)
    assert ps.ref_max == pytest.approx(60.0)
    assert ps.ref_std == pytest.approx(23.75)
    assert ps.ref_mean_abs_change == pytest.approx(47.5)
    assert ps.congestion_rent == pytest.approx(0.0)  # uniform price


def test_negative_prices_and_congestion_rent(toy_results) -> None:
    # b1 negative (export-constrained), b2 positive: rent = Δλ·f·Δt.
    rd = toy_results("fixed", prices=(-5.0, -0.05))
    rep = compute_uplift(rd)
    ps = rep.prices
    assert ps.negative_node_hours == pytest.approx(4.0)
    assert ps.deep_negative_node_hours == pytest.approx(2.0)
    assert ps.ref_bus == "b1"  # default: first bus
    assert ps.ref_min == pytest.approx(-5.0)
    assert ps.congestion_rent == pytest.approx(0.0)


def test_congestion_rent_nonzero(tmp_path: Path) -> None:
    rd = write_toy_results(tmp_path / "c", "fixed")
    # Overwrite the prices: b1 = 10, b2 = 30 in both hours.
    (rd / "Bus" / "balance_dual_s0_p0.csv").write_text(
        '"scenario","stage","block","uid","value"\n'
        "1,1,1,1,10\n1,1,1,2,30\n1,1,2,1,10\n1,1,2,2,30\n",
        encoding="utf-8",
    )
    rep = compute_uplift(rd)
    # (30 - 10) * (100 + 80) MW·h
    assert rep.prices.congestion_rent == pytest.approx(3600.0)


def test_startup_derived_from_status_and_no_flow(toy_results) -> None:
    rep = compute_uplift(toy_results("fixed", with_startup=False, with_flow=False))
    assert rep.total == pytest.approx(3200.0)
    assert rep.prices.congestion_rent is None


def test_pricing_csv_provenance(toy_results) -> None:
    csv = (
        "scene,phase,pricing_mode,applied_mode,fallback,attempted,"
        "relaxed_columns,pinned_columns,status,reason\n"
        '1,1,relaxed,fixed,1,0,0,0,-1,"pricing LP not optimal"\n'
        '1,2,relaxed,relaxed,0,1,4,0,0,""\n'
    )
    rep = compute_uplift(toy_results("relaxed", pricing_csv=csv))
    assert rep.applied_modes == ["fixed", "relaxed"]
    assert rep.pricing_fallbacks == 1
    d = rep.to_dict()
    assert d["pricing_fallbacks"] == 1
    assert d["total_uplift"] == pytest.approx(1700.0)
    assert d["units"][0]["total"] == pytest.approx(
        d["units"][0]["make_whole"] + d["units"][0]["lost_opportunity"]
    )


def test_schedules_relax_and_missing_generator(tmp_path: Path) -> None:
    plan = copy.deepcopy(TOY_PLANNING)
    sysd = plan["system"]
    # Per-stage startup cost array and a [stage][block] pmax schedule.
    sysd["commitment_array"][1]["startup_cost"] = [1000.0]
    sysd["generator_array"][1]["pmax"] = [[100.0, 100.0]]
    sysd["commitment_array"].append(
        {"uid": 3, "name": "lp_uc", "generator": "g_slow", "relax": True}
    )
    sysd["commitment_array"].append({"uid": 4, "name": "orphan", "generator": "nope"})
    rd = write_toy_results(tmp_path / "s", "fixed", planning=plan)
    rep = compute_uplift(rd)
    assert {u.commitment for u in rep.units} == {"g_slow_uc", "g_fast_uc"}
    assert rep.total == pytest.approx(3200.0)


def test_srmc_overrides_gcost(toy_results) -> None:
    rd = toy_results("fixed")
    # srmc 30 for g_fast in hour 1: margin (10-30)*40 = -800 → MWP 1800.
    (rd / "Generator" / "srmc_sol_s0_p0.csv").write_text(
        '"scenario","stage","block","uid","value"\n1,1,1,2,30\n',
        encoding="utf-8",
    )
    rep = compute_uplift(rd)
    fast = next(u for u in rep.units if u.commitment == "g_fast_uc")
    assert fast.make_whole == pytest.approx(1800.0)


def test_missing_outputs_raise(tmp_path: Path) -> None:
    rd = write_toy_results(tmp_path / "m", "fixed")
    (rd / "Bus" / "balance_dual_s0_p0.csv").unlink()
    with pytest.raises(FileNotFoundError, match="balance_dual"):
        compute_uplift(rd)
    with pytest.raises(FileNotFoundError, match="planning"):
        compute_uplift(tmp_path / "nowhere")


def test_no_buses_leaves_ref_stats_unset(toy_results) -> None:
    plan = copy.deepcopy(TOY_PLANNING)
    plan["system"]["bus_array"] = []
    rep = compute_uplift(toy_results("fixed", planning=plan))
    d = rep.to_dict()
    assert d["prices"]["ref_mean"] is None
    assert rep.prices.ref_bus == ""


def test_missing_status_and_unknown_bus(tmp_path: Path) -> None:
    plan = copy.deepcopy(TOY_PLANNING)
    plan["system"]["generator_array"][1]["bus"] = "nowhere"
    rd = write_toy_results(tmp_path / "ns", "fixed", planning=plan)
    (rd / "Commitment" / "status_sol_s0_p0.csv").unlink()
    rep = compute_uplift(rd)
    fast = next(u for u in rep.units if u.commitment == "g_fast_uc")
    assert fast.bus == "-1"
    # No status: no no-load; λ = 0 at an unknown bus → margin -50·40.
    assert fast.energy_margin == pytest.approx(-2000.0)
