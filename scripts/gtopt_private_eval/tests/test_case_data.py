# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Reading solved cases into per-project data."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from gtopt_private_eval.case_data import load_system

from .conftest import write_stream


def test_generator_rows(gen_case: Callable[..., Path]) -> None:
    sysd = load_system(gen_case())
    assert sysd.scale == pytest.approx(1.0)
    assert sysd.peak_demand_mw == pytest.approx(100.0)
    (g,) = sysd.projects
    assert g.kind == "Generator" and g.new_mw == pytest.approx(50.0)
    assert g.energy_revenue() == pytest.approx((30 + 10) * 50 * 4380)
    assert g.variable_cost() == pytest.approx(10 * 50 * 8760)
    assert g.energy_mwh() == pytest.approx(50 * 8760)
    assert g.captured_price() == pytest.approx(20.0)
    assert g.duration_h() is None
    assert g.planning is not None
    assert g.planning.margin_new == pytest.approx(4.38e6)
    # annuity back to overnight (8 %, 25 y) and the 0-rate fallback
    assert g.planning_overnight(25) == pytest.approx(87600 / 0.0936788, rel=1e-5)
    assert {u.name for u in sysd.units} == {"g", "old"}
    assert sysd.dispatch[(1, 1, 1)][1][3] == pytest.approx(50.0)


def test_overnight_and_wacc_fields(gen_case: Callable[..., Path]) -> None:
    sysd = load_system(gen_case(overnight_capcost=1.0e6, wacc=0.1, annual_capcost=None))
    g = sysd.projects[0]
    assert g.planning_rate() == pytest.approx(0.1)
    assert g.planning_overnight(30) == pytest.approx(1.0e6)


def test_no_cost_fields(gen_case: Callable[..., Path]) -> None:
    g = load_system(gen_case(annual_capcost=None)).projects[0]
    assert g.planning_overnight(20) is None
    g2 = load_system(gen_case(annual_capcost=[1.0, 87600.0])).projects[0]
    assert g2.planning_overnight(25) == pytest.approx(87600 / 0.0936788, rel=1e-5)


def test_profile_and_srmc(gen_case: Callable[..., Path]) -> None:
    extra = {"generator_profile_array": [{"generator": "g", "profile": [[0.5, 0.25]]}]}
    rdir = gen_case(extra)
    write_stream(rdir, "Generator/srmc_sol", [(1, 1, 1, 1, 12.0), (1, 1, 2, 1, 12.0)])
    g = load_system(rdir).projects[0]
    assert [r.avail for r in g.rows] == [0.5, 0.25]
    assert g.variable_cost() == pytest.approx(12 * 50 * 8760)


def test_heat_rate_curve_cost_at_dispatch(gen_case: Callable[..., Path]) -> None:
    """Thermal cash cost follows the declared curve at the dispatched point:
    srmc (first slope) + fuel price × upper segments + no-load fuel."""
    extra = {
        "fuel_array": [{"uid": 1, "name": "gas", "price": 10.0}],
        "commitment_array": [{"uid": 7, "generator": "g", "noload_fuel": 5.0}],
    }
    rdir = gen_case(
        extra, fuel="gas", pmax_segments=[30.0, 60.0], heat_rate_segments=[1.0, 2.0]
    )
    write_stream(rdir, "Generator/srmc_sol", [(1, 1, 1, 1, 12.0), (1, 1, 2, 1, 12.0)])
    write_stream(rdir, "Commitment/status_sol", [(1, 1, 1, 7, 1.0), (1, 1, 2, 7, 0.0)])
    g = load_system(rdir).projects[0]
    # 10 × (2 − 1) × (50 − 30) / 50 = 4 $/MWh; no-load 10 × 5 / 50 = 1 (u = 1)
    assert [r.curve_cost for r in g.rows] == pytest.approx([5.0, 4.0])
    assert g.variable_cost() == pytest.approx((17.0 + 16.0) * 50 * 4380)
    # no fuel → no curve cost (the case gcost / srmc stands)
    g0 = load_system(gen_case(pmax_segments=[30.0, 60.0], heat_rate_segments=[1, 2]))
    assert all(r.curve_cost == 0.0 for r in g0.projects[0].rows)


def test_reserve_revenue(gen_case: Callable[..., Path]) -> None:
    extra = {
        "reserve_zone_array": [{"uid": 1, "name": "csf"}],
        "reserve_provision_array": [
            {"uid": 1, "name": "g_csf", "generator": "g", "reserve_zones": ["csf"]},
            {"uid": 2, "name": "o_csf", "generator": "old", "reserve_zones": ["csf"]},
            {"uid": 3, "name": "z", "generator": "g", "reserve_zones": ["nope"]},
        ],
    }
    rdir = gen_case(extra)
    write_stream(rdir, "ReserveZone/urequirement_dual", [(1, 1, 1, 1, 4.0)])
    write_stream(rdir, "ReserveZone/drequirement_dual", [(1, 1, 2, 1, -2.0)])
    write_stream(rdir, "ReserveProvision/uprovision_sol", [(1, 1, 1, 1, 10.0)])
    write_stream(rdir, "ReserveProvision/dprovision_sol", [(1, 1, 2, 1, 5.0)])
    sysd = load_system(rdir)
    assert sysd.has_reserves
    g = sysd.projects[0]
    assert g.reserve_revenue == pytest.approx(4 * 10 * 4380 + 2 * 5 * 4380)


def test_reserve_revenue_uses_dispatched_provision_by_uid(
    gen_case: Callable[..., Path],
) -> None:
    """SSCC revenue is dual × the provision the LP dispatched (not the
    nameplate / urmax), and a provision may name its generator by uid."""
    extra = {
        "reserve_zone_array": [{"uid": 1, "name": "csf"}],
        "reserve_provision_array": [
            {
                "uid": 1,
                "name": "g_csf",
                "generator": 1,
                "reserve_zones": [1],
                "urmax": 1000,
            },
        ],
    }
    rdir = gen_case(extra)
    write_stream(rdir, "ReserveZone/urequirement_dual", [(1, 1, 1, 1, 4.0)])
    write_stream(rdir, "ReserveZone/drequirement_dual", [(1, 1, 1, 1, 0.0)])
    # gas-limited: the LP dispatched 0 in block 1 although urmax = 1000
    write_stream(rdir, "ReserveProvision/uprovision_sol", [(1, 1, 1, 1, 0.0)])
    write_stream(rdir, "ReserveProvision/dprovision_sol", [(1, 1, 1, 1, 0.0)])
    sysd = load_system(rdir)
    assert sysd.projects[0].reserve_revenue == pytest.approx(0.0)
    write_stream(rdir, "ReserveProvision/uprovision_sol", [(1, 1, 1, 1, 3.0)])
    sysd = load_system(rdir)
    assert sysd.projects[0].reserve_revenue == pytest.approx(4 * 3 * 4380)


def test_battery_and_line_goldens() -> None:
    from .conftest import golden

    bat = load_system(golden("invest_bat_1b/golden/lp"))
    assert bat.scale == pytest.approx(365.0)
    (b,) = bat.projects
    assert b.kind == "Battery"
    assert b.new_mw == pytest.approx(84.0, rel=1e-3)
    assert b.new_mwh == pytest.approx(288.88, rel=1e-3)
    assert b.duration_h() == pytest.approx(288.88 / 84.0, rel=1e-3)
    assert b.planning is not None
    assert b.energy_revenue() - b.variable_cost() == pytest.approx(
        b.planning.margin_new, rel=1e-6
    )
    assert b.charge_mwh() > b.energy_mwh() > 0  # losses
    assert any(u.kind == "Battery" for u in bat.units)
    garver = load_system(golden("invest_garver_6b/golden/lp"))
    lines = [p for p in garver.projects if p.built]
    assert {p.name for p in lines} == {"l2_5", "l2_6", "l3_5", "l4_6"}
    assert all(p.kind == "Line" and p.line_margin > 0 for p in lines)
