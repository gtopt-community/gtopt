# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Market models: generic core and one test block per model."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from gtopt_private_eval.case_data import ProjectData, UnitInfo, load_system
from gtopt_private_eval.defaults import DEFAULTS
from gtopt_private_eval.evaluate import (
    case_fixed_om,
    evaluate,
    fixed_om,
    risk_costs,
    system_warnings,
)
from gtopt_private_eval.markets import MARKETS, EvalContext, get_market
from gtopt_private_eval.markets.base import interp_factor
from gtopt_private_eval.markets.cl import peak_unit_price
from gtopt_private_eval.params import Params, deep_merge


def _params(**over: Any) -> Params:
    return Params(deep_merge(DEFAULTS, over))


def _ctx(gen_case: Callable[..., Path], **over: Any) -> tuple[EvalContext, ProjectData]:
    sysd = load_system(gen_case())
    proj = sysd.projects[0]
    ctx = EvalContext(sysd=sysd, params=_params(**over))
    ctx.tech[proj.name] = ctx.params.project(proj.name).get("technology", "ocgt")
    ctx.life[proj.name] = 10
    ctx.wacc[proj.name] = 0.08
    ctx.capex[proj.name] = 50e6
    return ctx, proj


def test_registry() -> None:
    assert set(MARKETS) == {
        "energy_only",
        "capacity_market",
        "reliability_option",
        "cfd",
        "cl",
    }
    with pytest.raises(ValueError, match="unknown market"):
        get_market("nowhere", {})
    for name, cls in MARKETS.items():
        m = get_market(name, DEFAULTS["markets"][name])
        assert isinstance(m, cls) and m.title and m.notes()


def test_energy_only_and_co2(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    m = get_market("energy_only", {})
    rev = m.revenues(ctx, proj)
    assert rev == {"energy": pytest.approx(40 * 50 * 4380)}
    assert m.costs(ctx, proj) == {}
    ctx2, proj2 = _ctx(gen_case, co2={"price": 10.0})
    ctx2.mef = {((1, 1, 1), 1): 1.0}
    rev2 = m.revenues(ctx2, proj2)
    # g emits 0.56 t/MWh (ocgt default) all year, avoids 1 t/MWh in block 1
    assert rev2["co2"] == pytest.approx(10 * (50 * 4380 - 0.56 * 50 * 8760))
    ctx3, proj3 = _ctx(gen_case, co2={"price": 10.0, "in_cash_flow": False})
    assert "co2" not in m.revenues(ctx3, proj3)


def test_transmission_modes(gen_case: Callable[..., Path]) -> None:
    ctx, _proj = _ctx(gen_case)
    line = ProjectData(
        kind="Line",
        name="l",
        uid=1,
        bus=1,
        new_mw=10,
        installed_mw=10,
        line_margin=123.0,
    )
    ctx.life["l"], ctx.capex["l"] = 40, 1e6
    assert get_market("energy_only", {}).revenues(ctx, line) == {
        "congestion_rent": 123.0
    }
    reg = get_market(
        "energy_only",
        {
            "transmission": "regulated",
            "regulated_rate": 0.07,
            "regulated_tax_gross_up": 0.0,
        },
    ).revenues(ctx, line)
    assert reg["regulated_avi"] == pytest.approx(1e6 * 0.0750089, rel=1e-5)
    assert reg["regulated_coma"] == pytest.approx(0.021e6)  # FOM + insurance
    for name in ("capacity_market", "reliability_option", "cfd", "cl"):
        m = get_market(name, DEFAULTS["markets"][name])
        assert "energy" not in m.revenues(ctx, line)
        assert m.costs(ctx, line) == {}


def test_capacity_market(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    m = get_market(
        "capacity_market",
        {"price_per_mw_year": 1000.0, "derating": {"ocgt": 0.5}},
    )
    assert m.revenues(ctx, proj)["capacity"] == pytest.approx(1000 * 50 * 0.5)
    m2 = get_market(
        "capacity_market",
        {
            "price_per_mw_year": 1000.0,
            "derating": {"default": 1.0},
            "contract_years": 3,
            "price_after": 10.0,
        },
    )
    assert m2.revenues(ctx, proj)["capacity"] == [50000.0] * 3 + [500.0]
    assert interp_factor({"4": 0.5, "8": 0.7}, 6.0) == pytest.approx(0.6)
    assert interp_factor({"0": 0.0, "4": 0.5}, 2.0) == pytest.approx(0.25)
    bat = ProjectData(
        kind="Battery",
        name="b",
        uid=1,
        bus=1,
        new_mw=10,
        installed_mw=10,
        new_mwh=20,
    )
    ctx.tech["b"], ctx.life["b"] = "bess", 15
    m3 = get_market("capacity_market", DEFAULTS["markets"]["capacity_market"])
    assert m3.derating(ctx, bat) == pytest.approx(0.29)


def test_reliability_option(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    m = get_market(
        "reliability_option",
        {
            "price_per_mw_year": 1000.0,
            "derating": {"default": 1.0},
            "strike_price": 20.0,
        },
    )
    rev, cost = m.revenues(ctx, proj), m.costs(ctx, proj)
    assert rev["capacity"] == pytest.approx(50000.0)
    # price 30 > strike 20 in block 1 (4380 h) on 50 derated MW
    assert cost["ro_refund"] == pytest.approx(10 * 4380 * 50)
    assert any("strike" in n for n in m.notes())


def test_cfd(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    m = get_market("cfd", {"strike": 25.0, "contract_years": 2})
    diff = m.revenues(ctx, proj)["cfd_difference"]
    one = (25 - 30) * 50 * 4380 + (25 - 10) * 50 * 4380
    assert diff == [one, one, 0.0]
    m2 = get_market("cfd", {"strike": {"g": 20.0}})
    assert m2.revenues(ctx, proj)["cfd_difference"] == pytest.approx(0.0)
    m3 = get_market("cfd", {"strike": "lcoe"})
    assert "cfd_difference" not in m3.revenues(ctx, proj)  # no LCOE yet
    ctx.lcoe["g"] = 20.0
    assert m3.revenues(ctx, proj)["cfd_difference"] == pytest.approx(0.0)
    bat = ProjectData(kind="Battery", name="b", uid=1, bus=1, new_mw=1, installed_mw=1)
    assert "cfd_difference" not in m.revenues(ctx, bat)


def test_cl_sufficiency_and_prorate(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    prm = {
        "capacity_price_usd_kw_month": 10.0,
        "sufficiency_factors": {"ocgt": 0.9, "default": 0.5},
        "prorate": "none",
        "sscc": {"ocgt": {"share": 0.1, "price_usd_mw_h": 2.0}},
    }
    m = get_market("cl", prm)
    rev = m.revenues(ctx, proj)
    assert rev["sufficiency"] == pytest.approx(50 * 0.9 * 120000.0)
    assert rev["sscc"] == pytest.approx(50 * 0.1 * 2.0 * 8760)
    # case pro-rating: peak 100 MW / Σ prelim (g 50x0.9 + old 80x0.5 = 85)
    ctx.tech["old"] = "thermal"
    k = 100.0 / (50 * 0.9 + 80 * 0.5)
    m_case = get_market("cl", {**prm, "prorate": "case", "prorate_cap": None})
    assert m_case.prorate_factor(ctx) == pytest.approx(k)
    m_cap = get_market("cl", {**prm, "prorate": "case", "prorate_cap": 1.0})
    assert m_cap.prorate_factor(ctx) == pytest.approx(1.0)
    assert get_market("cl", {**prm, "prorate": 0.8}).prorate_factor(ctx) == 0.8
    ctx.sysd.units = []
    assert m_case.prorate_factor(ctx) == 1.0
    ctx.sysd.units = [UnitInfo("b", "Battery", 10.0, 10.0, 4.0)]
    assert m_case.preliminary(ctx, ctx.sysd) == pytest.approx(0.0)  # no table


def test_cl_reserves_from_case_replace_sscc(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    ctx.sysd.has_reserves = True
    proj.reserve_revenue = 7.0
    rev = get_market("cl", DEFAULTS["markets"]["cl"]).revenues(ctx, proj)
    assert rev["reserves"] == 7.0 and "sscc" not in rev


def test_cl_storage_and_peak_unit() -> None:
    table = DEFAULTS["markets"]["cl"]["storage_sufficiency"]
    assert interp_factor(table, 0.5) == 0.0
    assert interp_factor(table, 1.0) == pytest.approx(0.36)
    assert interp_factor(table, 3.5) == pytest.approx(0.915)
    assert interp_factor(table, 8.0) == 1.0
    assert interp_factor(table, None) == 0.0
    assert interp_factor({}, 2.0) == 0.0
    # CNE RE 803/2025 inputs reproduce ~8.77 $/kW-month at the base date
    pu = DEFAULTS["markets"]["cl"]["peak_unit"]
    assert peak_unit_price(pu) == pytest.approx(8.77, abs=0.01)
    m = get_market("cl", {"capacity_price_mode": "peak_unit", "peak_unit": pu})
    assert m.capacity_price_kw_month() == pytest.approx(8.77, abs=0.01)
    assert m.sufficiency_factor("bess", "Battery", 4.0) == pytest.approx(0.0)
    m2 = get_market("cl", DEFAULTS["markets"]["cl"])
    assert m2.sufficiency_factor("bess", "Battery", 4.0) == pytest.approx(0.98)


def test_cl_costs(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case)
    energy = 50 * 8760
    prm = {
        "green_tax": {"price_usd_t": 5.0, "threshold_t_year": 1000.0},
        "injection_toll_usd_mwh": 2.0,
        "cod_year": 2032,
        "toll_factors": {"2032": 0.5, "2033": 0.25, "2034": 0.0},
        "withdrawal_charges_usd_mwh": 3.7,
        "ppa_withdrawal_share": 0.5,
    }
    cost = get_market("cl", prm).costs(ctx, proj)
    assert cost["green_tax"] == pytest.approx(0.56 * energy * 5.0)
    assert cost["transmission_injection"] == [
        pytest.approx(2 * energy * 0.5),
        pytest.approx(2 * energy * 0.25),
        0.0,
        0.0,
    ]
    assert cost["withdrawal_charges"] == pytest.approx(3.7 * 0.5 * energy)
    # below the threshold, no toll, merchant: nothing
    none = get_market("cl", {"green_tax": {"threshold_t_year": 1e12}}).costs(ctx, proj)
    assert none == {}
    # years before the table start pay the full toll
    early = get_market(
        "cl", {**prm, "cod_year": 2030, "toll_factors": {"2032": 0.5}}
    ).costs(ctx, proj)["transmission_injection"]
    assert early[0] == pytest.approx(2 * energy)


def test_evaluate_all_markets(gen_case: Callable[..., Path]) -> None:
    sysd = load_system(gen_case())
    prm = _params(projects={"g": {"technology": "ocgt", "economic_life": 25}})
    for name in MARKETS:
        rep = evaluate(sysd, prm, name)
        (p,) = rep.projects
        assert p.capex > 0 and p.life == 25
        d = rep.to_dict()
        assert d["market"] == name and d["projects"][0]["name"] == "g"
        assert "pre-tax" in d["analysis"]


def test_cl_excludes_scarcity_rent(gen_case: Callable[..., Path]) -> None:
    """Prices above the highest generator cost are not paid in Chile."""
    ctx, proj = _ctx(gen_case)
    assert ctx.sysd.max_srmc == pytest.approx(30.0)
    assert ctx.sysd.unserved_mwh == 0.0
    m = get_market("cl", {"capacity_price_usd_kw_month": 0.0})
    assert "scarcity_excluded" not in m.revenues(ctx, proj)  # 30 = cap
    # block 1 priced at VOLL with 5 MW unserved
    ctx.sysd.price[(1, 1, 1, 1)] = 1000.0
    for r in proj.rows:
        if r.key == (1, 1, 1):
            r.price = 1000.0
    rev = m.revenues(ctx, proj)
    assert rev["scarcity_excluded"] == pytest.approx(-(1000.0 - 30.0) * 50 * 4380)
    assert rev["energy"] + rev["scarcity_excluded"] == pytest.approx(
        (30.0 + 10.0) * 50 * 4380
    )
    assert get_market("cl", {"scarcity_cap": 500.0}).scarcity_rent(
        ctx, proj
    ) == pytest.approx(500.0 * 50 * 4380)
    assert get_market("cl", {"scarcity_cap": None}).scarcity_rent(ctx, proj) == 0.0
    assert get_market("cl", {"scarcity_cap": "off"}).scarcity_rent(ctx, proj) == 0.0
    ctx.sysd.max_srmc = 0.0
    assert m.scarcity_rent(ctx, proj) == 0.0
    assert "scarcity cap: case" in m.notes()[0]


def test_unserved_energy_is_flagged(gen_case: Callable[..., Path]) -> None:
    rdir = gen_case()
    (rdir / "Demand").mkdir(exist_ok=True)
    (rdir / "Demand" / "fail_sol_s0_p0.csv").write_text(
        '"scenario","stage","block","uid","value"\n1,1,1,1,5\n'
    )
    sysd = load_system(rdir)
    assert sysd.unserved_mwh == pytest.approx(5 * 4380)
    assert sysd.peak_demand_mw == pytest.approx(105.0)
    warn = system_warnings(sysd)
    assert warn and "unserved energy" in warn[0]
    rep = evaluate(sysd, _params(), "cl")
    assert any("WARNING" in n for n in rep.notes)


def test_fixed_om_not_double_counted(gen_case: Callable[..., Path]) -> None:
    """The tool's 2 % is replaced by the case's own fixed O&M field."""
    ctx, proj = _ctx(gen_case)
    assert case_fixed_om(ctx.params, proj) is None
    assert fixed_om(ctx, proj, 50e6) == pytest.approx(0.02 * 50e6)
    proj.elem["fixed_om_cost"] = [[12000.0]]
    assert case_fixed_om(ctx.params, proj) == pytest.approx(12000.0 * 50)
    assert fixed_om(ctx, proj, 50e6) == pytest.approx(12000.0 * 50)
    ctx2, _ = _ctx(gen_case, finance={"fom_in_case": False})
    assert fixed_om(ctx2, proj, 50e6) == pytest.approx(0.02 * 50e6)
    ctx3, _ = _ctx(gen_case, finance={"fom_in_case": True})
    assert fixed_om(ctx3, proj, 50e6) == 0.0
    bat = ProjectData(
        kind="Battery",
        name="g",
        uid=1,
        bus=1,
        new_mw=10,
        installed_mw=10,
        new_mwh=40,
        elem={"annual_fom": 100.0},
        power_elem={"annual_fom": 1000.0},
    )
    assert case_fixed_om(ctx.params, bat) == pytest.approx(100 * 40 + 1000 * 10)
    bat.elem = {}
    assert case_fixed_om(ctx.params, bat) == pytest.approx(1000 * 10)
    # end to end: no fixed_om line beyond the case's value
    rdir = gen_case(fixed_om_cost=8000.0)
    rep = evaluate(load_system(rdir), _params(), "energy_only")
    assert rep.projects[0].year1.costs["fixed_om"] == pytest.approx(8000.0 * 50)


def test_insurance_warranty_guarantee_lines(gen_case: Callable[..., Path]) -> None:
    ctx, proj = _ctx(gen_case, finance={"insurance_pct_capex": 0.0})
    assert not risk_costs(ctx, proj, 50e6)
    ctx, proj = _ctx(
        gen_case,
        finance={"insurance_pct_capex": 0.005},
        technologies={
            "ocgt": {
                "warranty_pct_capex": 0.001,
                "warranty_usd_kw_year": 2.0,
                "guarantee_pct_capex": 0.1,
                "guarantee_fee_pct": 0.02,
                "guarantee_years": 2,
            }
        },
    )
    out = risk_costs(ctx, proj, 50e6)
    assert out["insurance"] == pytest.approx(0.005 * 50e6)
    assert out["warranty"] == pytest.approx(0.001 * 50e6 + 2.0 * 50_000)
    assert out["guarantee_fee"] == pytest.approx([0.1 * 50e6 * 0.02] * 2 + [0.0])
    prm = _params(
        finance={
            "insurance_pct_capex": 0.005,
            "guarantee_pct_capex": 0.1,
            "guarantee_fee_pct": 0.02,
            "guarantee_years": 2,
        }
    )
    rep = evaluate(ctx.sysd, prm, "energy_only")
    y = rep.projects[0].cash.years
    assert "insurance" in y[0].costs and y[2].costs["guarantee_fee"] == 0.0


def test_cl_cen_payment_guarantee(gen_case: Callable[..., Path]) -> None:
    """DS 125 art. 157: 3 months of energy + capacity balance deficit."""
    ctx, proj = _ctx(gen_case)
    m0 = get_market("cl", {"capacity_price_usd_kw_month": 10.0})
    assert m0.cen_guarantee_amount(ctx, proj) == 0.0  # merchant: none
    assert "cen_payment_guarantee" not in m0.costs(ctx, proj)
    prm = {
        "capacity_price_usd_kw_month": 10.0,
        "sufficiency_factors": {"ocgt": 0.0},
        "prorate": "none",
        "ppa_withdrawal_share": 1.0,
        "cen_guarantee": {"months": 3, "peak_month_factor": 1.0, "fee_pct": 0.01},
    }
    m = get_market("cl", prm)
    # g runs flat 50 MW: contract q = 50 MW, net position 0 -> energy 0;
    # capacity deficit (50 - 0) MW x 10 $/kW-month x 1000 per month
    amount = m.cen_guarantee_amount(ctx, proj)
    assert amount == pytest.approx(3 * 50 * 10.0 * 1000)
    assert m.costs(ctx, proj)["cen_payment_guarantee"] == pytest.approx(0.01 * amount)
    # half contracted: energy surplus, capacity deficit 25 MW
    half = get_market("cl", {**prm, "ppa_withdrawal_share": 0.5})
    assert half.cen_guarantee_amount(ctx, proj) == pytest.approx(3 * 25 * 10_000)
    fixed = get_market(
        "cl", {**prm, "cen_guarantee": {"amount_usd": 1e6, "fee_pct": 0.02}}
    )
    assert fixed.costs(ctx, proj)["cen_payment_guarantee"] == pytest.approx(2e4)
    # an explicit 80 MW baseload contract: buys 30 MW in block 1 at 30 $/MWh
    # and 30 MW in block 2 at 10 $/MWh; capacity deficit 80 MW
    c80 = get_market("cl", {**prm, "cen_guarantee": {"contract_mw": 80.0}})
    energy = (30 * 30.0 + 30 * 10.0) * 4380 / 12
    assert c80.cen_guarantee_amount(ctx, proj) == pytest.approx(
        3 * (energy + 80 * 10_000)
    )
    assert (
        get_market(
            "cl", {**prm, "cen_guarantee": {"contract_mw": 0.0}}
        ).cen_guarantee_amount(ctx, proj)
        == 0.0
    )
    proj.rows = []
    assert m.cen_guarantee_amount(ctx, proj) == 0.0
