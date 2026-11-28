# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Features for real (multi-hydrology, monthly, converted) cases.

A two-scenario (hydrology), two-month case with element ``type`` fields,
file-backed schedules (Parquet), a block-hour map, an existing battery
and existing units evaluated with ``existing=`` patterns.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.defaults import DEFAULTS
from gtopt_private_eval.evaluate import evaluate
from gtopt_private_eval.main import main
from gtopt_private_eval.markets import EvalContext, get_market
from gtopt_private_eval.params import Params, deep_merge
from gtopt_private_eval.peak import CONTROL_PERIOD_WINDOW, peak_hours

from .conftest import Rows, write_case

# blocks: 1 = April day (360 h), 2 = April evening (360 h),
#         3 = May day (372 h),   4 = May evening (372 h)
DUR = {1: 360.0, 2: 360.0, 3: 372.0, 4: 372.0}
LOAD = {1: 100.0, 2: 300.0, 3: 100.0, 4: 250.0}
PRICE = {1: 20.0, 2: 80.0, 3: 30.0, 4: 60.0}
#: hydro output per (scenario, block): scenario 2 is the dry one
HYDRO = {(1, 1): 50.0, (1, 2): 80.0, (1, 3): 50.0, (1, 4): 80.0}
HYDRO.update({(2, 1): 20.0, (2, 2): 40.0, (2, 3): 20.0, (2, 4): 30.0})
PV_AVAIL = {1: 0.6, 2: 0.0, 3: 0.5, 4: 0.0}
STAGE = {1: 1, 2: 1, 3: 2, 4: 2}


def _hour_map() -> list[dict[str, int]]:
    """1-Apr-2026 (Wed), 4-Apr-2026 (Sat) and 1-May-2026 (Fri), 24 h each."""
    out = []
    for month, day, day_block, eve_block in ((4, 1, 1, 2), (4, 4, 1, 2), (5, 1, 3, 4)):
        for hour in range(1, 25):
            block = eve_block if 19 <= hour <= 22 else day_block
            out.append(
                {"year": 2026, "month": month, "day": day, "hour": hour, "block": block}
            )
    return out


def _write_inputs(rdir: Path) -> None:
    inp = rdir / "input"
    (inp / "GeneratorProfile").mkdir(parents=True)
    (inp / "Generator").mkdir(parents=True)
    rows = [
        {"scenario": s, "stage": STAGE[b], "block": b, "uid": 7, "value": PV_AVAIL[b]}
        for s in (1, 2)
        for b in DUR
    ]
    pd.DataFrame(rows).to_parquet(inp / "GeneratorProfile" / "profile.parquet")
    pmax = [
        {"stage": STAGE[b], "block": b, "uid": uid, "value": v}
        for b in DUR
        for uid, v in ((3, 200.0 if STAGE[b] == 1 else 150.0), (5, 0.0))
    ]
    pd.DataFrame(pmax).to_parquet(inp / "Generator" / "pmax.parquet")


@pytest.fixture
def real_case(tmp_path: Path) -> Callable[..., Path]:
    def make(hour_map: bool = True, months: bool = True) -> Path:
        stages = [
            {"uid": 1, "first_block": 0, "count_block": 2},
            {"uid": 2, "first_block": 2, "count_block": 2},
        ]
        if months:
            stages[0]["month"] = "april"
            stages[1]["month"] = "may"
        sim: dict[str, Any] = {
            "block_array": [{"uid": b, "duration": d} for b, d in DUR.items()],
            "stage_array": stages,
            "scenario_array": [
                {"uid": 1, "probability_factor": 0.5},
                {"uid": 2, "probability_factor": 0.5},
            ],
        }
        if hour_map:
            sim["block_hour_map"] = _hour_map()
        system = {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [
                {
                    "uid": 1,
                    "name": "HYD",
                    "bus": 1,
                    "capacity": 100,
                    "type": "hydro_ror",
                },
                {
                    "uid": 2,
                    "name": "PV",
                    "bus": 1,
                    "capacity": 50,
                    "pmax": 50,
                    "type": "solar",
                },
                {
                    "uid": 3,
                    "name": "TH",
                    "bus": 1,
                    "capacity": 250,
                    "pmax": "pmax",
                    "gcost": 70,
                    "type": "thermal",
                },
                {
                    "uid": 4,
                    "name": "TH_DIE",
                    "bus": 1,
                    "capacity": 250,
                    "gcost": 200,
                    "type": "thermal",
                },
                {
                    "uid": 5,
                    "name": "FUTURE",
                    "bus": 1,
                    "capacity": 300,
                    "pmax": "pmax",
                    "gcost": 50,
                    "type": "thermal",
                },
                {"uid": 6, "name": "BAT_gen", "bus": 1, "capacity": 100, "gcost": 0},
            ],
            "demand_array": [
                {"uid": 1, "name": "d", "bus": 1, "lmax": 300},
                {"uid": 2, "name": "BAT_dem", "bus": 1, "lmax": 100},
            ],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "BAT",
                    "emax": 400.0,
                    "capacity": 400.0,
                    "pmax_charge": 100.0,
                    "pmax_discharge": 100.0,
                }
            ],
            "capacity_profile_array": [
                {"uid": 7, "name": "PV", "owner": "PV", "profile": "profile"}
            ],
        }
        planning = {
            "options": {"input_directory": "input"},
            "simulation": sim,
            "system": system,
        }
        streams: dict[str, Rows] = {
            "Bus/balance_dual": [
                (s, STAGE[b], b, 1, PRICE[b]) for s in (1, 2) for b in DUR
            ],
            "Generator/generation_sol": [
                *((s, STAGE[b], b, 1, HYDRO[(s, b)]) for s in (1, 2) for b in DUR),
                *((s, STAGE[b], b, 2, 50 * PV_AVAIL[b]) for s in (1, 2) for b in DUR),
                *(
                    (s, STAGE[b], b, 3, LOAD[b] - HYDRO[(s, b)] - 50 * PV_AVAIL[b])
                    for s in (1, 2)
                    for b in DUR
                ),
                (1, 1, 2, 6, 100.0),
                (2, 1, 2, 6, 100.0),
            ],
            "Demand/load_sol": [
                *((s, STAGE[b], b, 1, LOAD[b]) for s in (1, 2) for b in DUR),
                (1, 1, 1, 2, 100.0),
                (2, 1, 1, 2, 100.0),
            ],
        }
        rdir = write_case(
            tmp_path / f"real{int(hour_map)}{int(months)}", planning, streams
        )
        _write_inputs(rdir)
        return rdir

    return make


def _cl(**prm: Any) -> dict[str, Any]:
    base = {
        "capacity_price_usd_kw_month": 10.0,
        "sufficiency_method": "case",
        "peak_hours": 4,
        "dry_hydrologies": 1,
        "prorate_cap": None,
        "sufficiency_factors": {"thermal": 0.9, "default": 0.9},
    }
    base.update(prm)
    return deep_merge(DEFAULTS["markets"]["cl"], base)


def _ctx(sysd: Any) -> EvalContext:
    return EvalContext(sysd=sysd, params=Params(deep_merge(DEFAULTS, {})))


def test_calendar_and_units(real_case: Callable[..., Path]) -> None:
    sysd = load_system(real_case())
    assert sysd.stage_month == {1: 4, 2: 5}
    assert sysd.monthly_stages()
    assert len(sysd.hour_map) == 72
    units = {u.name: u for u in sysd.units}
    # file-backed pmax: 200 in April, 150 in May (capacity 250): size
    # 200 MW, in service 174.6 MW on average (time-weighted)
    th_mean = (200 * 720 + 150 * 744) / 1464
    assert units["TH"].max_mw == pytest.approx(200.0)
    assert units["TH"].available_mw == pytest.approx(th_mean)
    assert units["FUTURE"].available_mw == pytest.approx(0.0)  # not in service
    assert units["PV"].avail[(1, 1, 1)] == pytest.approx(0.6)
    assert units["PV"].elem_type == "solar"
    bat = units["BAT"]
    assert bat.kind == "Battery" and bat.duration_h == pytest.approx(4.0)
    assert bat.gen[(1, 1, 2)] == pytest.approx(100.0)
    assert "BAT_gen" not in units
    # dispatched-unit ceiling per period: TH (70) runs everywhere
    assert sysd.dispatched_srmc[(1, 1, 2)] == pytest.approx(70.0)
    # stage months from the hour map when the stages carry no month field
    assert load_system(real_case(months=False)).stage_month == {1: 4, 2: 5}
    assert not load_system(real_case(hour_map=False, months=False)).monthly_stages()


def test_peak_hours(real_case: Callable[..., Path]) -> None:
    sysd = load_system(real_case())
    # window Apr-Sep 19-22 h working days: 1-Apr evening (300 MW) and
    # 1-May evening (250 MW); Saturday 4-Apr is out
    assert not peak_hours(sysd, 4)[1].windowed  # default: the whole year
    ph = peak_hours(sysd, 4, CONTROL_PERIOD_WINDOW)
    assert ph[1].windowed and ph[1].demand_mw == pytest.approx(300.0)
    assert ph[1].weight == {(1, 1, 2): 4.0}
    six = peak_hours(sysd, 6, CONTROL_PERIOD_WINDOW)[2]
    assert six.demand_mw == pytest.approx((4 * 300 + 2 * 250) / 6)
    assert six.mean({(2, 1, 2): 1.0}) == pytest.approx(4 / 6)
    # a window with too few hours falls back to the whole year
    assert peak_hours(sysd, 20, CONTROL_PERIOD_WINDOW)[1].hours == pytest.approx(20.0)
    assert not peak_hours(sysd, 20, CONTROL_PERIOD_WINDOW)[1].windowed
    # no hour map: highest-load blocks by duration (x scale)
    nomap = load_system(real_case(hour_map=False))
    ph2 = peak_hours(nomap, 52)[1]
    assert ph2.demand_mw == pytest.approx(300.0)
    assert ph2.hours == pytest.approx(52.0)
    assert peak_hours(nomap, 52)[1].mean({}) == 0.0


def test_case_sufficiency(real_case: Callable[..., Path]) -> None:
    sysd = load_system(real_case(), existing=["HYD", "PV", "BAT", "TH"])
    ctx = _ctx(sysd)
    for p in sysd.projects:
        ctx.tech[p.name] = ctx.params.technology(p.name, p.kind, p.elem_type)
    assert ctx.tech == {
        "HYD": "hydro",
        "PV": "solar_pv",
        "TH": "thermal",
        "BAT": "bess",
    }
    m = get_market("cl", _cl(sufficiency_exclude=["*_DIE"], renewable_peak_rule="min"))
    assert m.dry_scenarios(ctx) == [2]
    proj = {p.name: p for p in sysd.projects}
    # hydro: dry scenario 2, peak hours = 1-Apr evening -> 40 MW / 100 MW
    assert m.project_factor(ctx, proj["HYD"]) == pytest.approx(0.4)
    # PV: no output in the evening peak
    assert m.project_factor(ctx, proj["PV"]) == pytest.approx(0.0)
    # storage: DS 70 table, 4 h -> 0.98
    assert m.project_factor(ctx, proj["BAT"]) == pytest.approx(0.98)
    assert m.project_factor(ctx, proj["TH"]) == pytest.approx(0.9)
    prelim = m.unit_preliminary(ctx)
    assert "TH_DIE" not in prelim
    assert prelim["FUTURE"] == 0.0
    th_mean = (200 * 720 + 150 * 744) / 1464
    total = 0.4 * 100 + 0.0 + 0.9 * th_mean + 0.98 * 100
    assert m.preliminary(ctx, sysd) == pytest.approx(total)
    assert m.peak_demand(ctx) == pytest.approx(300.0)
    k = 300.0 / total
    assert m.prorate_factor(ctx) == pytest.approx(k)
    d = m.details(ctx, proj["HYD"])
    assert d["sufficiency_definitive_mw"] == pytest.approx(40.0 * k)
    assert d["dry_scenarios"] == 1.0
    assert d["mean_bus_price"] == pytest.approx(
        sum(PRICE[b] * d for b, d in DUR.items()) / sum(DUR.values())
    )
    # PV annual CF bound and the dispatch fallback when there is no profile
    pv = proj["PV"]
    gen = {r.key: r.gen for r in pv.rows if r.gen}
    f = m.case_factor(ctx, "solar_pv", "Generator", None, gen, {}, 50.0)
    assert f == pytest.approx(0.0)
    every = {r.key: 0.3 for r in pv.rows}
    flat = m.case_factor(ctx, "wind", "Generator", None, {}, every, 50.0)
    assert flat == pytest.approx(0.3)
    # a missing period counts as fully available (profile default 1)
    part = m.case_factor(
        ctx, "wind", "Generator", None, {}, {k: 0.3 for k in gen}, 50.0
    )
    assert part == pytest.approx((0.3 * 360 + 360 + 0.3 * 372 + 372) / 1464)
    # no MW: the table factor (the unit adds 0 MW anyway)
    assert m.case_factor(ctx, "hydro", "Generator", None, {}, {}, 0.0) == 0.6
    # the old table method is unchanged
    old = get_market("cl", _cl(sufficiency_method="factors"))
    assert old.peak_demand(ctx) == pytest.approx(sysd.peak_demand_mw)
    assert old.project_factor(ctx, proj["HYD"]) == pytest.approx(
        DEFAULTS["markets"]["cl"]["sufficiency_factors"]["hydro"]
    )


def test_existing_units_and_capex(real_case: Callable[..., Path]) -> None:
    sysd = load_system(real_case(), existing=["PV", "BAT", "NOPE*"])
    names = {p.name: p for p in sysd.projects}
    assert set(names) == {"PV", "BAT"}
    pv, bat = names["PV"], names["BAT"]
    assert pv.existing and pv.planning is None and pv.new_mw == pytest.approx(50.0)
    assert bat.new_mw == pytest.approx(100.0) and bat.new_mwh == pytest.approx(400.0)
    params = Params(
        deep_merge(DEFAULTS, {"projects": {"PV": {"overnight_capcost": 1e6}}})
    )
    rep = evaluate(sysd, params, "cl")
    ev = {p.name: p for p in rep.projects}
    assert ev["PV"].capex == pytest.approx(50e6)  # project override
    assert ev["BAT"].capex == pytest.approx(180_000 * 400)  # CNE $/kWh
    assert ev["BAT"].existing and ev["BAT"].planning_annuity == 0.0
    assert ev["BAT"].details["charge_mwh"] > 0
    # per-scenario spread: the PV has the same profile in both scenarios
    det = ev["PV"].details
    assert det["energy_mwh_min_scenario"] == pytest.approx(
        det["energy_mwh_max_scenario"]
    )
    assert det["captured_price_min_scenario"] == pytest.approx(det["captured_price"])
    assert ev["BAT"].to_dict()["existing"] is True
    # no capex anywhere: 0 (revenue view only)
    params0 = Params(
        deep_merge(DEFAULTS, {"technologies": {"solar_pv": {"capex_usd_kw": None}}})
    )
    assert {p.name: p for p in evaluate(sysd, params0, "energy_only").projects}[
        "PV"
    ].capex == 0.0


def test_monthly_guarantee(real_case: Callable[..., Path]) -> None:
    sysd = load_system(real_case(), existing=["HYD"])
    ctx = _ctx(sysd)
    proj = sysd.projects[0]
    ctx.tech[proj.name] = "hydro"
    prm = _cl(
        sufficiency_method="factors",
        sufficiency_factors={"hydro": 0.0},
        prorate="none",
        cen_guarantee={"contract_mw": 100.0, "months": 1},
    )
    m = get_market("cl", prm)
    scale = sysd.scale
    # per scenario and month: Σ CMg (100 − gen) h; scenario 2 (dry) is worst
    worst = max(
        sum(PRICE[b] * (100 - HYDRO[(s, b)]) * DUR[b] for b in (1, 2)) * scale
        for s in (1, 2)
    )
    worst = max(
        worst,
        max(
            sum(PRICE[b] * (100 - HYDRO[(s, b)]) * DUR[b] for b in (3, 4)) * scale
            for s in (1, 2)
        ),
    )
    cap = 100 * 10.0 * 1000
    assert m.cen_guarantee_amount(ctx, proj) == pytest.approx(worst + cap)
    off = get_market(
        "cl",
        {**prm, "cen_guarantee": {"contract_mw": 100.0, "months": 1, "monthly": "off"}},
    )
    mean = sum(r.price * (100 - r.net) * r.hours for r in proj.rows) / 12
    assert off.cen_guarantee_amount(ctx, proj) == pytest.approx(mean + cap)


def test_dispatched_scarcity_cap(real_case: Callable[..., Path]) -> None:
    sysd = load_system(real_case(), existing=["PV"])
    ctx = _ctx(sysd)
    proj = sysd.projects[0]
    ctx.tech[proj.name] = "solar_pv"
    # case-wide cap = 200 (TH_DIE never runs): nothing removed
    assert get_market("cl", _cl()).scarcity_rent(ctx, proj) == 0.0
    # per period: TH (70 $/MWh) is the costliest unit running; prices
    # above 70 (block 2: 80) are scarcity, PV produces 0 there -> 0; the
    # battery-free unit ceiling with a margin
    m = get_market("cl", _cl(scarcity_cap="dispatched"))
    assert m.period_cap(sysd, (1, 1, 2)) == pytest.approx(70.0)
    assert m.scarcity_rent(ctx, proj) == pytest.approx(0.0)
    mm = get_market("cl", _cl(scarcity_cap="dispatched", scarcity_cap_margin=0.1))
    assert mm.period_cap(sysd, (1, 1, 2)) == pytest.approx(77.0)
    sysd.dispatched_srmc[(1, 1, 1)] = 0.0
    assert m.period_cap(sysd, (1, 1, 1)) == pytest.approx(sysd.max_srmc)
    # a period where PV runs above the ceiling
    sysd.dispatched_srmc[(1, 1, 1)] = 10.0
    rent = (20.0 - 10.0) * 30.0 * proj.rows[0].hours
    assert m.scarcity_rent(ctx, proj) == pytest.approx(rent)


def test_cli_existing(
    real_case: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            str(real_case()),
            "--market",
            "cl",
            "--existing",
            "PV",
            "--set",
            'markets.cl.sufficiency_method="case"',
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Generator:PV (existing) [solar_pv]" in out
    assert "details: mean_bus_price" in out


def test_calendar_from_files_and_slow_paths(
    real_case: Callable[..., Path], tmp_path: Path
) -> None:
    """Block-hour map as an input table (no year / day columns), integer
    stage months, a missing schedule table and a wide CSV table."""
    rdir = real_case(hour_map=False, months=False)
    planning = json.loads((rdir / "planning.json").read_text())
    planning["simulation"]["block_hour_map"] = "BlockHourMap/bhm"
    planning["simulation"]["stage_array"][0]["month"] = 4
    gens = planning["system"]["generator_array"]
    gens[3]["pmax"] = "missing"  # TH_DIE: no such table
    gens[1]["gcost"] = "gc"  # PV: wide CSV schedule
    (rdir / "planning.json").write_text(json.dumps(planning))
    bhm = pd.DataFrame(_hour_map()).drop(columns=["year", "day"])
    (rdir / "input" / "BlockHourMap").mkdir()
    bhm.to_parquet(rdir / "input" / "BlockHourMap" / "bhm.parquet")
    (rdir / "input" / "Generator" / "gc.csv").write_text(
        "stage,block,uid:2\n1,1,3.5\n1,2,3.5\n2,3,4.5\n2,4,4.5\n"
    )
    sysd = load_system(rdir, existing=["PV"])
    assert sysd.stage_month == {1: 4, 2: 5}  # 1 from the field, 2 from the map
    assert {y for y, *_ in sysd.hour_map} == {2001}
    units = {u.name: u for u in sysd.units}
    assert units["TH_DIE"].available_mw == 250.0  # no pmax table: capacity
    assert sysd.projects[0].rows[0].gcost == pytest.approx(3.5)
    assert sysd.projects[0].rows[-1].gcost == pytest.approx(4.5)


def test_unbuilt_candidate_and_battery_schedule(
    real_case: Callable[..., Path],
) -> None:
    """An unbuilt expandable unit is not in the pie (its pmax is only a
    bound), and a battery entering in May counts half its MW."""
    rdir = real_case()
    planning = json.loads((rdir / "planning.json").read_text())
    planning["system"]["generator_array"].append(
        {
            "uid": 9,
            "name": "CAND",
            "bus": 1,
            "capacity": 0,
            "pmax": 1.0e4,
            "expcap": 10,
            "expmod": 10,
            "annual_capcost": 1.0,
        }
    )
    planning["system"]["battery_array"][0]["pmax_discharge"] = [
        [0.0, 0.0],
        [100.0, 100.0],
    ]
    (rdir / "planning.json").write_text(json.dumps(planning))
    sysd = load_system(rdir, existing=["BAT"])
    units = {u.name: u for u in sysd.units}
    assert units["CAND"].installed_mw == 0.0 and units["CAND"].available_mw == 0.0
    bat = units["BAT"]
    assert bat.max_mw == pytest.approx(100.0)
    assert bat.available_mw == pytest.approx(100.0 * 744 / 1464)
    assert bat.duration_h == pytest.approx(4.0)
    proj = next(pr for pr in sysd.projects if pr.name == "BAT")
    assert proj.existing and proj.new_mw == pytest.approx(100.0)


def test_renewable_peak_rule(real_case: Callable[..., Path]) -> None:
    """PV falls back to its annual CF when the peak hours sit in too few
    blocks to be resolved (``auto``); ``min`` / ``annual`` force a rule."""
    sysd = load_system(real_case(), existing=["PV"])
    ctx = _ctx(sysd)
    (pv,) = [p for p in sysd.projects if p.name == "PV"]
    ctx.tech[pv.name] = "solar_pv"
    strict = get_market("cl", _cl(renewable_peak_rule="min"))
    annual = get_market("cl", _cl(renewable_peak_rule="annual"))
    auto = get_market("cl", _cl(min_peak_blocks=99))
    resolved = get_market("cl", _cl(min_peak_blocks=1))
    assert strict.project_factor(ctx, pv) == pytest.approx(0.0)
    low = annual.project_factor(ctx, pv)
    assert low > 0.0
    assert auto.project_factor(ctx, pv) == pytest.approx(low)
    assert resolved.project_factor(ctx, pv) == pytest.approx(0.0)
    assert auto.peak_blocks(ctx) >= 1
    d = auto.details(ctx, pv)
    assert d["peak_resolved"] == 0.0 and d["peak_blocks"] >= 1.0
    with pytest.raises(ValueError, match="renewable_peak_rule"):
        get_market("cl", _cl(renewable_peak_rule="bogus")).project_factor(ctx, pv)


def test_unit_sufficiency_factors(real_case: Callable[..., Path]) -> None:
    """A unit's own factor overrides the table / case factor, in the
    project and in the pie."""
    sysd = load_system(real_case(), existing=["TH"])
    ctx = _ctx(sysd)
    th = next(p for p in sysd.projects if p.name == "TH")
    ctx.tech[th.name] = "thermal"
    for method in ("factors", "case"):
        m = get_market(
            "cl",
            _cl(sufficiency_method=method, unit_sufficiency_factors={"T?": 0.5}),
        )
        assert m.unit_factor("TH") == pytest.approx(0.5)
        assert m.unit_factor("PV") is None
        assert m.project_factor(ctx, th) == pytest.approx(0.5)
        unit = next(u for u in sysd.units if u.name == "TH")
        mw = unit.available_mw if method == "case" else unit.installed_mw
        assert m.unit_preliminary(ctx)["TH"] == pytest.approx(0.5 * mw)


def test_existing_with_risk_streams_and_fuel(real_case: Callable[..., Path]) -> None:
    """Case sufficiency and the CEN guarantee are set ex ante over every
    hydrology: the per-scenario views of the private CVaR get the same
    values, so E[NPV] equals the NPV of the whole project (here linear:
    no capex, so no tax shield to floor)."""
    sysd = load_system(real_case(), existing=["HYD"])
    cl = _cl(prorate="none", cen_guarantee={"contract_mw": 60.0, "months": 1})
    cfg = {
        "markets": {"cl": cl},
        "risk": {"mode": "cvar"},
        "finance": {
            "stream_rates": {"method": "rate", "rates": {"energy": 0.1}},
        },
    }
    rep = evaluate(sysd, Params(deep_merge(DEFAULTS, cfg)), "cl")
    hyd = rep.projects[0]
    assert hyd.existing and hyd.risk is not None and hyd.streams is not None
    assert "npv_vs_fuel_scenario" in hyd.sensitivity
    assert hyd.details["sufficiency_factor"] == pytest.approx(0.4)
    assert hyd.risk.expectation == pytest.approx(hyd.cash.npv)
    # the dry hydrology (2) is the worst, by energy only
    assert hyd.risk.order[0] == 2
    # the sufficiency of a one-scenario view is the whole project's
    ctx = _ctx(sysd)
    ctx.tech["HYD"] = "hydro"
    m = get_market("cl", cl)
    proj = sysd.projects[0]
    full = m.revenues(ctx, proj)["sufficiency"]
    amount = m.cen_guarantee_amount(ctx, proj)
    for sc in (1, 2):
        view = replace(
            proj,
            rows=[replace(r, hours=2 * r.hours) for r in proj.rows if r.key[0] == sc],
        )
        assert m.revenues(ctx, view)["sufficiency"] == pytest.approx(full)
        assert m.cen_guarantee_amount(ctx, view) == pytest.approx(amount)
