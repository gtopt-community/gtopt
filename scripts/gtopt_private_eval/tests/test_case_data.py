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
