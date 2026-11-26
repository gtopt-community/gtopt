"""Tests for :mod:`plexos2gtopt.aemo_isp_expansion`.

A synthetic ISP-shaped XML (two sub-regions, an existing gas unit, an
existing wind farm retiring mid-year, a candidate solar farm with a
rating trace, a candidate REZ network augmentation, a candidate battery
and the PLEXOS capacity / build constraint families) is converted into a
one-year monthly-stage expansion case.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plexos2gtopt import aemo_isp_expansion as exp
from plexos2gtopt.tests.test_aemo_isp import _halfhourly_csv
from plexos2gtopt.tests.test_aemo_isp_constraints import _constraint, _CXml

FY_START = date(2027, 7, 1)
NDAYS = 366  # 2027-07-01 .. 2028-06-30 (2028 is a leap year)


@pytest.fixture(name="xcase")
def _xcase(tmp_path: Path) -> tuple[Path, Path]:
    x = _CXml()
    nth = x.obj("Node", "NTH")
    sth = x.obj("Node", "STH")
    for node, name in ((nth, "NTH"), (sth, "STH")):
        df = x.data_file(name, f"Traces\\demand\\{name}_dem.csv")
        x.data("Node", node, "Load", 0)
        x.data("Node", node, "Load", 0, tags=(df,))
    gas = x.obj("Fuel", "Gas")
    x.data("Fuel", gas, "Price", 5.0)
    # existing gas unit
    gt = x.cobj("Generator", "GT1", "Natural Gas NSW")
    x.member("Generator", "Node", "Nodes", gt, nth)
    x.member("Generator", "Fuel", "Fuels", gt, gas)
    x.data("Generator", gt, "Units", 1)
    x.data("Generator", gt, "Max Capacity", 100)
    x.data("Generator", gt, "Heat Rate", 10)
    x.data("Generator", gt, "VO&M Charge", 2)
    # existing wind farm, retired from 2028-01-01
    wf = x.cobj("Generator", "WF1", "Wind NSW")
    x.member("Generator", "Node", "Nodes", wf, sth)
    x.data("Generator", wf, "Units", 1)
    x.data("Generator", wf, "Units", 0, date_from="2028-01-01T00:00:00")
    x.data("Generator", wf, "Max Capacity", 80)
    x.data("Generator", wf, "Rating", 80)
    # candidate REZ solar: 1 MW units, rating trace, build from FY start
    sol = x.cobj("Generator", "S1_SAT", "New Entrant REZ NSW")
    x.member("Generator", "Node", "Nodes", sol, sth)
    sol_df = x.data_file("S1_SAT", "Traces\\solar\\S1_SAT_RefYear5000.csv")
    x.data("Generator", sol, "Units", 0)
    x.data("Generator", sol, "Max Capacity", 1)
    x.data("Generator", sol, "Rating", 0)
    x.data("Generator", sol, "Rating", 0, tags=(sol_df,))
    x.data("Generator", sol, "Max Units Built", 500, date_from="2027-07-01T00:00:00")
    x.data("Generator", sol, "Build Cost", 1500, date_from="2027-07-01T00:00:00")
    x.data("Generator", sol, "WACC", 7)
    x.data("Generator", sol, "Economic Life", 30)
    x.data("Generator", sol, "FO&M Charge", 15)
    x.data("Generator", sol, "Aux Incr", 2)
    # candidate REZ transmission augmentation (capacity token)
    aug = x.cobj(
        "Generator", "S1_Linear Augmentation 1", "Network Augmentation Single REZ"
    )
    x.member("Generator", "Node", "Nodes", aug, sth)
    x.data("Generator", aug, "Units", 0)
    x.data("Generator", aug, "Max Capacity", 1)
    x.data("Generator", aug, "Max Units Built", 400, date_from="2027-07-01T00:00:00")
    x.data("Generator", aug, "Build Cost", 800, date_from="2027-07-01T00:00:00")
    x.data("Generator", aug, "WACC", 3)
    x.data("Generator", aug, "Economic Life", 50)
    # candidate gas available only from 2028-07-01 (outside the horizon)
    ocgt = x.cobj("Generator", "NTH OCGT", "New Entrant Natural Gas NSW")
    x.member("Generator", "Node", "Nodes", ocgt, nth)
    x.data("Generator", ocgt, "Units", 0)
    x.data("Generator", ocgt, "Max Capacity", 1)
    x.data("Generator", ocgt, "Max Units Built", 1000, date_from="2028-07-01T00:00:00")
    # existing battery, retired from 2028-01-01 (emax 0 afterwards)
    eb = x.cobj("Battery", "EB1", "Large-scale Battery NSW")
    x.member("Battery", "Node", "Nodes", eb, nth)
    x.data("Battery", eb, "Units", 1)
    x.data("Battery", eb, "Units", 0, date_from="2028-01-01T00:00:00")
    x.data("Battery", eb, "Max Power", 10)
    x.data("Battery", eb, "Capacity", 20)
    x.data("Battery", eb, "Initial SoC", 50)
    # candidate battery (not expanded by the prototype)
    bat = x.cobj("Battery", "STH Battery - 2h", "New Entrant Large-scale Battery NSW")
    x.member("Battery", "Node", "Nodes", bat, sth)
    x.data("Battery", bat, "Units", 0)
    x.data("Battery", bat, "Max Power", 1)
    x.data("Battery", bat, "Capacity", 2)
    x.data("Battery", bat, "Charge Efficiency", 90)
    x.data("Battery", bat, "Discharge Efficiency", 95)
    x.data("Battery", bat, "Build Cost", 1000)
    x.data("Battery", bat, "WACC", 7)
    x.data("Battery", bat, "Economic Life", 20)
    x.data("Battery", bat, "Max Units Built", 300, date_from="2027-07-01T00:00:00")

    # resource limit: Σ capacity built ≤ 300
    rl = _constraint(
        x, "ResourceLimit_S1_Solar", "Resource Limits for REZ", sense=-1, RHS_Year=300
    )
    x.coef("Generator", sol, rl, "Capacity Built Coefficient", 1.0)
    # onboarding limit over installed capacity (existing wind + candidate)
    ob = _constraint(
        x, "OCLimit_S1", "Onboarding and Connections Build Limits REZs", sense=-1
    )
    x.data(
        "Constraint",
        ob,
        "RHS Year",
        250,
        date_from="2027-07-01T00:00:00",
        date_to="2028-06-30T00:00:00",
    )
    x.coef("Generator", sol, ob, "Installed Capacity Coefficient", 1.0)
    x.coef("Generator", wf, ob, "Installed Capacity Coefficient", 1.0)
    # policy ≥ with a candidate battery term → converter power capainst
    cis = _constraint(x, "CIS Dispatchable NSW", "Policies NEM", sense=1, RHS_Year=50)
    x.coef("Battery", bat, cis, "Generation Capacity Coefficient", 1.0)
    # shared upgrade with an in-year build term → expmod at the FY start
    sh = _constraint(x, "Shared_S1", "Shared REZ Upgrades", sense=-1, RHS_Year=1)
    x.coef("Generator", aug, sh, "Units Built in Year Coefficient", 1.0)
    # REZ export limit: solar + wind − 1.0 · augmentation ≤ 60
    rez = _constraint(
        x, "ExportLimit_S1", "REZ Network Export Constraints", sense=-1, RHS=60
    )
    x.coef("Generator", sol, rez, "Generation Coefficient", 1.0)
    x.coef("Generator", wf, rez, "Generation Coefficient", 1.0)
    x.coef("Generator", aug, rez, "Installed Capacity Coefficient", -1.0)
    # daily energy cap on the gas unit: 1 GWh/day
    day = _constraint(x, "GT1_Day", "Energy Constraints", sense=-1, RHS_Day=1)
    x.coef("Generator", gt, day, "Generation Coefficient", 1.0)

    # reservoirs: a Recycle pondage and a free (End Effects = Free) dam
    pond = x.obj("Storage", "Pond")
    x.data("Storage", pond, "Max Volume", 1000)
    x.data("Storage", pond, "Initial Volume", 500)
    x.data("Storage", pond, "End Effects Method", 2)
    dam = x.obj("Storage", "Dam")
    x.data("Storage", dam, "Max Volume", 5000)
    x.data("Storage", dam, "Initial Volume", 2000)
    x.data("Storage", dam, "End Effects Method", 1)
    # hydro unit on the dam commissioned mid-year: its turbine must exist
    hyd = x.cobj("Generator", "HYD_NEW", "Hydro NSW")
    x.member("Generator", "Node", "Nodes", hyd, nth)
    x.member("Generator", "Storage", "Head Storage", hyd, dam)
    x.data("Generator", hyd, "Units", 0)
    x.data("Generator", hyd, "Units", 1, date_from="2028-01-01T00:00:00")
    x.data("Generator", hyd, "Max Capacity", 40)
    x.data("Generator", hyd, "Efficiency Incr", 2.0)

    xml = tmp_path / "model" / "ISP X Model.xml"
    xml.parent.mkdir(parents=True)
    x.write(xml)
    for name, val in (("NTH", 60.0), ("STH", 30.0)):
        _halfhourly_csv(
            xml.parent / "Traces" / "demand" / f"{name}_dem.csv",
            FY_START,
            NDAYS,
            lambda k, h, v=val: v,
        )
    traces = tmp_path / "traces"
    _halfhourly_csv(
        traces / "solar" / "S1_SAT_RefYear5000.csv",
        FY_START,
        NDAYS,
        lambda k, h: 0.5 if 12 <= h < 36 else 0.0,
    )
    return xml, traces


def _build(xml: Path, traces: Path, **kw: object):
    opts = exp.ExpansionOptions(
        xml=xml,
        trace_roots=[xml.parent, traces],
        first_fy=2028,
        years=1,
        name="t",
        **kw,  # type: ignore[arg-type]
    )
    return exp.build_expansion_case(opts)


def _ucs(case) -> dict[str, dict]:
    return {
        uc["name"]: uc
        for uc in case.planning["system"].get("user_constraint_array", [])
    }


def _gens(case) -> dict[str, dict]:
    return {g["name"]: g for g in case.planning["system"]["generator_array"]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_month_starts_and_days() -> None:
    m = exp.month_starts(2028, 1)
    assert m[0] == date(2027, 7, 1) and m[-1] == date(2028, 6, 1) and len(m) == 12
    assert [exp.days_in_month(d) for d in m][7] == 29  # Feb 2028


def test_crf_and_annual_capcost() -> None:
    assert exp.crf(0.0, 20) == pytest.approx(0.05)
    assert exp.crf(0.07, 30) == pytest.approx(0.0805864, rel=1e-5)
    # 1500 $/kW, 7 %, 30 y, FO&M 15 $/kW-yr → $/MW-yr
    assert exp.annual_capcost(1500, 7, 30, 15) == pytest.approx(
        1000 * (1500 * 0.0805864 + 15), rel=1e-5
    )


def test_split_expression_and_hod_mean() -> None:
    assert exp.split_expression('generator("A").generation <= 5') == (
        'generator("A").generation',
        "<=",
        5.0,
    )
    with pytest.raises(ValueError):
        exp.split_expression("no operator")
    arr = np.concatenate([np.arange(24.0), np.arange(24.0) + 2])
    np.testing.assert_allclose(exp.hod_mean(arr, 2), np.arange(24.0) + 1)


# ---------------------------------------------------------------------------
# case
# ---------------------------------------------------------------------------


def test_time_structure(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase, discount_rate=0.07)
    sim = case.planning["simulation"]
    assert len(sim["stage_array"]) == 12
    assert len(sim["block_array"]) == 288
    assert case.stats["rep_days"] == 1
    assert sim["block_array"][0]["duration"] == 31.0
    assert sim["block_array"][24 * 7]["duration"] == 29.0
    assert "phase_array" not in sim
    assert case.planning["options"]["annual_discount_rate"] == 0.07
    ph = _build(*xcase, phases="month").planning["simulation"]["phase_array"]
    assert len(ph) == 12 and ph[3] == {"uid": 4, "first_stage": 3, "count_stage": 1}
    assert len(_build(*xcase, phases="year").planning["simulation"]["phase_array"]) == 1


def test_candidates(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase)
    gens = _gens(case)
    assert {"GT1", "WF1", "S1_SAT", "S1_Linear Augmentation 1"} <= set(gens)
    assert "NTH OCGT" not in gens  # not buildable inside the horizon
    sol = gens["S1_SAT"]
    assert sol["capacity"] == 0.0 and sol["expcap"] == 1.0
    assert sol["expmod"][0] == 500 and sol["expmod"][1:] == [0.0] * 11
    assert sol["capmax"] == [500.0] * 12
    assert sol["annual_capcost"][0] == pytest.approx(
        exp.annual_capcost(1500, 7, 30, 15), rel=1e-6
    )
    assert sol["type"].startswith("candidate_")
    assert case.stats["candidates_energy"] == 1
    assert case.stats["candidates_network"] == 1
    prof = case.schedules[("GeneratorProfile", "profile")]
    names = [p["generator"] for p in case.planning["system"]["generator_profile_array"]]
    sp = prof[names.index("S1_SAT") + 1]
    assert sp.shape == (12, 24)
    assert sp[0, 7] == pytest.approx(0.5 * 0.98)
    assert sp[0, 2] == pytest.approx(0.0)
    assert np.all(prof[names.index("S1_Linear Augmentation 1") + 1] == 0.0)


def test_existing_fleet_retirement(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase)
    gens = _gens(case)
    pmax = case.schedules[("Generator", "pmax")][gens["WF1"]["uid"]]
    assert np.all(pmax[:6] == pytest.approx(80.0))
    assert np.all(pmax[6:] == 0.0)
    gcost = case.schedules[("Generator", "gcost")][gens["GT1"]["uid"]]
    assert gcost[0, 0] == pytest.approx(52.0)


def test_capacity_constraints(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase)
    ucs = _ucs(case)
    rl = ucs["isp_ResourceLimit_S1_Solar"]
    assert rl["scope"] == "stage"
    assert rl["expression"] == 'generator("S1_SAT").capainst <= 300'
    ob = ucs["isp_OCLimit_S1"]
    # existing wind (80 MW until Dec) is folded into the RHS
    assert ob["expression"].startswith('generator("S1_SAT").capainst <= ')
    # one yearly row at the FY build stage; the folded fleet is the FY
    # mean (wind in service July..December only); other months inactive
    assert ob["expression"].endswith("<= 210")
    tight = _ucs(_build(*xcase, capacity_year_fleet="tightest"))["isp_OCLimit_S1"]
    assert tight["expression"].endswith("<= 170")
    end = _ucs(_build(*xcase, capacity_year_fleet="end"))["isp_OCLimit_S1"]
    assert end["expression"].endswith("<= 250")
    assert "rhs" not in ob
    assert ob["active_stages"] == [1] + [0] * 11
    assert "_capacity" not in ob
    cis = ucs["isp_CIS_Dispatchable_NSW"]
    assert cis["expression"] == ('converter("STH Battery - 2h_conv").capainst >= 50')
    assert cis["active_stages"][0] == 1
    sh = ucs["isp_Shared_S1"]
    assert sh["expression"] == 'generator("S1_Linear Augmentation 1").expmod <= 1'
    assert sh["active_stages"] == [1] + [0] * 11
    bats = {b["name"]: b for b in case.planning["system"]["battery_array"]}
    b = bats["STH Battery - 2h"]
    assert b["energy_power_ratio"] == 2.0
    assert b["power_expcap"] == 1.0 and b["expcap"] == 2.0
    assert b["power_expmod"][0] == 300 and b["power_expmod"][1:] == [0.0] * 11
    assert b["power_annual_capcost"][0] == pytest.approx(
        exp.annual_capcost(1000, 7, 20, 0), rel=1e-6
    )
    assert b["annual_capcost"] == 0.0
    assert b["input_efficiency"] == 0.9 and b["output_efficiency"] == 0.95
    assert b["pmax_discharge"] == 300.0 and b["emax"] == 600.0
    assert all(x["daily_cycle"] is True for x in bats.values())
    assert case.stats["candidates_storage"] == 1


def test_capacity_penalty(xcase: tuple[Path, Path]) -> None:
    hard = _ucs(_build(*xcase))["isp_OCLimit_S1"]
    assert "penalty" not in hard
    soft = _ucs(_build(*xcase, capacity_penalty=1e7))
    assert soft["isp_OCLimit_S1"]["penalty"] == 1e7
    assert "penalty" not in soft["isp_GT1_Day"]  # not a capacity constraint


def test_fy_capacity_rhs() -> None:
    months = exp.month_starts(2028, 2)
    rhs = np.full((24, 24), 100.0)
    rhs[3] = 80.0  # tighter in October of FY1
    rhs[15] = exp.INACTIVE_RHS  # absent in October of FY2
    fy = exp.fy_start_stages(months)
    assert [i for i, f in enumerate(fy) if f] == [0, 12]
    out = exp._fy_capacity_rhs(rhs, fy, "<=", exp.INACTIVE_RHS, "tightest")
    assert out[0, 0] == 80.0 and out[12, 0] == 100.0
    mean = exp._fy_capacity_rhs(rhs, fy, "<=", exp.INACTIVE_RHS)
    assert mean[0, 0] == pytest.approx((11 * 100.0 + 80.0) / 12)
    assert mean[12, 0] == 100.0  # the inactive month is not averaged
    end = exp._fy_capacity_rhs(rhs, fy, "<=", exp.INACTIVE_RHS, "end")
    assert end[0, 0] == 100.0
    # a >= target with a fleet growing during the year: the mean (and
    # the end) are looser than the 1-July snapshot
    grow = np.full((24, 24), 517.0)
    grow[4:12] = 300.0  # 217 MW commissioned in November
    assert exp._fy_capacity_rhs(grow, fy, ">=", -exp.INACTIVE_RHS, "tightest")[
        0, 0
    ] == pytest.approx(517.0)
    assert exp._fy_capacity_rhs(grow, fy, ">=", -exp.INACTIVE_RHS)[
        0, 0
    ] == pytest.approx((4 * 517.0 + 8 * 300.0) / 12)
    with pytest.raises(ValueError):
        exp._fy_capacity_rhs(rhs, fy, "<=", exp.INACTIVE_RHS, "bogus")
    assert np.all(np.delete(out[:, 0], [0, 12]) == exp.INACTIVE_RHS)
    ge = exp._fy_capacity_rhs(rhs * 0 + 5.0, fy, ">=", -exp.INACTIVE_RHS)
    assert ge[0, 0] == 5.0 and ge[1, 0] == -exp.INACTIVE_RHS


def test_life_factor() -> None:
    assert exp.life_factor(25, 3) == 1.0
    assert exp.life_factor(20, 25) == pytest.approx(0.8)
    assert exp.life_factor(0, 10) == 1.0


def test_day_masks_and_rep_mean() -> None:
    net = np.array([5.0, 1.0, 9.0, 3.0, 7.0])
    high, low = exp.day_masks(net, 2)
    assert high.tolist() == [True, False, True, False, True]
    assert (low == ~high).all()
    assert len(exp.day_masks(net, 1)) == 1 and exp.day_masks(net, 1)[0].all()
    with pytest.raises(ValueError):
        exp.day_masks(net, 3)
    arr = np.repeat(np.arange(5.0), 24)  # day d has value d all day
    np.testing.assert_allclose(exp.rep_mean(arr, high), np.full(24, 2.0))
    np.testing.assert_allclose(exp.rep_mean(arr, low), np.full(24, 2.0))
    half = np.repeat(np.arange(5.0), 48)  # half-hourly reads
    np.testing.assert_allclose(exp.rep_mean(half, high, 48), np.full(48, 2.0))
    assert exp.fy_start_stages(exp.month_starts(2028, 1), 2)[:3] == [
        True,
        False,
        False,
    ]


def test_two_representative_days(xcase: tuple[Path, Path]) -> None:
    one = _build(*xcase)
    two = _build(*xcase, rep_days=2, phases="month")
    sim = two.planning["simulation"]
    assert len(sim["stage_array"]) == 24 and len(sim["block_array"]) == 576
    # each month's two stages split its days (31 = 16 high + 15 low)
    assert two.stats["stage_days"][:2] == [16, 15]
    assert sum(two.stats["stage_days"]) == sum(one.stats["stage_days"])
    assert sim["block_array"][0]["duration"] == 16.0
    assert sim["phase_array"][1] == {"uid": 2, "first_stage": 2, "count_stage": 2}
    # builds only in the first stage of the FY
    sol = _gens(two)["S1_SAT"]
    assert sol["expmod"][0] == 500 and sol["expmod"][1:] == [0.0] * 23
    # energy is preserved: days-weighted stage means = month mean
    gens = _gens(one)
    p1 = one.schedules[("Generator", "pmax")][gens["WF1"]["uid"]]
    p2 = two.schedules[("Generator", "pmax")][_gens(two)["WF1"]["uid"]]
    d = np.asarray(two.stats["stage_days"], float)
    wmean = (p2[0::2] * d[0::2, None] + p2[1::2] * d[1::2, None]) / (
        d[0::2, None] + d[1::2, None]
    )
    np.testing.assert_allclose(wmean, p1, rtol=1e-9)
    # daily energy constraint: Σ stage RHS over the month's two stages
    r1 = [row[0] for row in _ucs(one)["isp_GT1_Day"]["rhs"]]
    r2 = [row[0] for row in _ucs(two)["isp_GT1_Day"]["rhs"]]
    assert r2[0] + r2[1] == pytest.approx(r1[0])


def test_operational_capacity_constraint(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase)
    ucs = _ucs(case)
    # WF1 retires in January: the LHS changes → one variant per LHS, each
    # inactive (RHS 1e9) in the stages of the other
    assert sorted(n for n in ucs if n.startswith("isp_ExportLimit_S1")) == [
        "isp_ExportLimit_S1_v1",
        "isp_ExportLimit_S1_v2",
    ]
    v1 = ucs["isp_ExportLimit_S1_v1"]
    assert "scope" not in v1
    assert 'generator("S1_Linear Augmentation 1").capainst' in v1["expression"]
    assert 'generator("S1_SAT").generation' in v1["expression"]
    assert 'generator("WF1").generation' in v1["expression"]
    assert 'generator("WF1")' not in ucs["isp_ExportLimit_S1_v2"]["expression"]
    assert v1["expression"].endswith("<= 60")
    assert v1["active_stages"][0] == 1 and v1["active_stages"][11] == 0


def test_daily_energy_becomes_stage_sum(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase)
    uc = _ucs(case)["isp_GT1_Day"]
    assert uc["scope"] == "stage"
    assert uc["expression"].startswith("sum{b in stage} dur[b] * (")
    rhs = [row[0] for row in uc["rhs"]]
    assert rhs[0] == pytest.approx(1000.0 * 31)
    assert rhs[7] == pytest.approx(1000.0 * 29)


def test_write_case(xcase: tuple[Path, Path], tmp_path: Path) -> None:
    case = _build(*xcase)
    out = tmp_path / "out"
    path = exp.write_expansion_case(case, out, "t")
    plan = json.loads(path.read_text())
    assert plan["system"]["generator_array"]
    df = pd.read_parquet(out / "Generator" / "pmax.parquet")
    assert list(df.columns) == ["stage", "block", "uid", "value"]
    assert df.stage.max() == 12 and df.block.max() == 288
    pf = pd.read_parquet(out / "GeneratorProfile" / "profile.parquet")
    assert list(pf.columns) == ["scenario", "stage", "block", "uid", "value"]
    # existing batteries: level bounds in Battery/*.parquet
    eb = next(b for b in plan["system"]["battery_array"] if b["name"] == "EB1")
    for f in ("emin", "emax"):
        assert eb[f] == f
        bf = pd.read_parquet(out / "Battery" / f"{f}.parquet")
        assert list(bf.columns) == ["stage", "block", "uid", "value"]
    # power limits stay inline (gtopt moves them to synthetic elements)
    assert isinstance(eb["pmax_charge"], list) and len(eb["pmax_charge"]) == 12
    em = pd.read_parquet(out / "Battery" / "emax.parquet")
    em = em[em.uid == eb["uid"]]
    assert em[em.stage == 1].value.max() == pytest.approx(20.0)
    assert em[em.stage == 12].value.max() == 0.0


def test_cli(xcase: tuple[Path, Path], tmp_path: Path) -> None:
    xml, traces = xcase
    out = tmp_path / "cli"
    rc = exp.main(
        [
            str(xml),
            "-t",
            str(traces),
            "--first-fy",
            "2028",
            "--years",
            "1",
            "-o",
            str(out),
            "-n",
            "c",
        ]
    )
    assert rc == 0
    assert (out / "c.json").is_file() and (out / "c.stats.json").is_file()
    st = json.loads((out / "c.stats.json").read_text())
    assert st["command"][-4:] == ["-o", str(out), "-n", "c"]
    assert st["fill_years"] == 1 and st["report_years"] == 1  # CLI default


def test_reservoir_terminal_rule_and_fill_year(xcase: tuple[Path, Path]) -> None:
    """Coupled volumes, free end volume, Recycle cycles within a stage."""
    xml, traces = xcase
    case = _build(xml, traces, fill_years=1)
    res = {r["name"]: r for r in case.planning["system"]["reservoir_array"]}
    for r in res.values():
        assert "efin" not in r and "efin_cost" not in r
    assert res["Pond"].get("daily_cycle") is True
    assert "daily_cycle" not in res["Dam"]
    assert res["Dam"]["eini"] == pytest.approx(2.0)
    st = case.stats
    assert st["report_years"] == 1 and st["fill_years"] == 1 and st["years"] == 2
    assert len(case.planning["simulation"]["stage_array"]) == 24
    assert st["reservoirs_cycled"] == 1 and st["reservoirs_free_terminal"] == 1
    tur = case.planning["system"]["turbine_array"]
    assert [t["generator"] for t in tur] == ["HYD_NEW"]
    assert st["hydro_added_later_turbine_array"] == 1


def test_weekday_rep_days_and_cycle_group(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase, rep_days=2, rep_kind="weekday", cycle_group=True)
    sim = case.planning["simulation"]
    st = sim["stage_array"]
    assert len(st) == 24
    # July 2027: 22 weekdays + 9 weekend days
    assert case.stats["stage_days"][:2] == [22, 9]
    assert st[0]["cycle_group"] == st[1]["cycle_group"] == 1
    assert st[2]["cycle_group"] == 2
    # weights: days per typical week, proportional to the days represented
    assert st[0]["cycle_weight"] == pytest.approx(7 * 22 / 31)
    assert st[0]["cycle_weight"] + st[1]["cycle_weight"] == pytest.approx(7.0)
    assert case.stats["period"] == "rep_days:2:weekday"
    with pytest.raises(ValueError):
        _build(*xcase, rep_kind="weekday")


@pytest.mark.parametrize(
    ("spec", "nb"),
    [("uniform:1", 168), ("uniform:2", 84), ("peak_ramp:4", 112), ("chebyshev:16", 16)],
)
def test_typical_week_samplers(xcase: tuple[Path, Path], spec: str, nb: int) -> None:
    one = _build(*xcase)
    wk = _build(*xcase, week_sampler=spec)
    sim = wk.planning["simulation"]
    st = sim["stage_array"]
    assert len(st) == 12 and st[0]["count_block"] == nb
    assert st[0]["period_hours"] == 168.0
    assert wk.stats["blocks_per_stage"] == nb
    # weighted durations: the stage stands for the whole month
    dur = np.array([b["duration"] for b in sim["block_array"][:nb]])
    assert dur.sum() == pytest.approx(31 * 24)
    assert ("time" in sim["block_array"][0]) == spec.startswith("chebyshev")
    # Chebyshev nodes are point samples; uniform / peak_ramp keep blocks
    assert (st[0].get("sampling") == "point") == spec.startswith("chebyshev")
    # point stages: cc (Clenshaw-Curtis) storage integration for batteries AND reservoirs
    mo = wk.planning["options"]["model_options"]
    point = spec.startswith("chebyshev")
    assert (mo.get("battery_storage_integration") == "cc") == point
    assert (mo.get("reservoir_storage_integration") == "cc") == point
    # daily-sum constraint keeps the month total (mean day × days)
    r1 = [row[0] for row in _ucs(one)["isp_GT1_Day"]["rhs"]]
    rw = [row[0] for row in _ucs(wk)["isp_GT1_Day"]["rhs"]]
    assert rw[0] == pytest.approx(r1[0])
    # block values: the energy of the chosen week (uniform: exact means)
    p = wk.schedules[("Generator", "pmax")][_gens(wk)["WF1"]["uid"]]
    assert p.shape == (12, nb)
    assert len(wk.stats["week_window_start_hour"]) == 12
    assert wk.stats["steps_per_day"] == 48
    hourly = _build(*xcase, week_sampler=spec, week_steps_per_day=24)
    assert hourly.stats["steps_per_day"] == 24
    if spec == "uniform:1":
        # hourly blocks: the same values from hourly or half-hourly reads
        ph = hourly.schedules[("Generator", "pmax")][_gens(hourly)["WF1"]["uid"]]
        np.testing.assert_allclose(ph, p, rtol=1e-9, atol=1e-9)


def test_rep_day_chebyshev_sampler(xcase: tuple[Path, Path]) -> None:
    hourly = _build(*xcase, rep_days=2, rep_kind="weekday")
    case = _build(*xcase, rep_days=2, rep_kind="weekday", day_sampler="chebyshev:8")
    sim = case.planning["simulation"]
    st = sim["stage_array"]
    assert len(st) == 24 and st[0]["count_block"] == 8
    assert st[0]["period_hours"] == 24.0 and st[0]["sampling"] == "point"
    mo = case.planning["options"]["model_options"]
    assert mo["battery_storage_integration"] == "cc"
    assert mo["reservoir_storage_integration"] == "cc"
    assert case.stats["period"] == "day:chebyshev:8"
    # previous behaviour (gtopt's per-type defaults) stays reachable
    prev = _build(
        *xcase,
        rep_days=2,
        rep_kind="weekday",
        day_sampler="chebyshev:8",
        point_storage_integration="none",
    )
    pmo = prev.planning["options"]["model_options"]
    assert "battery_storage_integration" not in pmo
    assert "reservoir_storage_integration" not in pmo
    cc = _build(
        *xcase,
        rep_days=2,
        rep_kind="weekday",
        day_sampler="chebyshev:8",
        point_storage_integration="cc",
    )
    cmo = cc.planning["options"]["model_options"]
    assert cmo["battery_storage_integration"] == "cc"
    assert cmo["reservoir_storage_integration"] == "cc"
    assert case.stats["blocks_per_stage"] == 8
    assert case.stats["steps_per_day"] == 48
    # July 2027: 22 weekdays; the stage weights stand for those days
    blk = sim["block_array"]
    dur = np.array([b["duration"] for b in blk[:8]])
    assert dur.sum() == pytest.approx(22 * 24)
    times = [b["time"] for b in blk[:8]]
    assert times == sorted(times) and 0.0 <= times[0] and times[-1] < 24.0
    # every (stage, block) schedule has the stage's block count
    for (cls, fld), tables in case.schedules.items():
        for arr in tables.values():
            assert np.asarray(arr).shape[-1] == 8, (cls, fld)
    # daily-sum constraint keeps the represented days' total
    r1 = [row[0] for row in _ucs(hourly)["isp_GT1_Day"]["rhs"]]
    rc = [row[0] for row in _ucs(case)["isp_GT1_Day"]["rhs"]]
    assert rc[0] == pytest.approx(r1[0])
    with pytest.raises(ValueError):
        _build(*xcase, day_sampler="chebyshev:8", week_sampler="uniform:1")
    with pytest.raises(ValueError):
        exp.make_day_sampler("uniform:1")
    d = exp.make_day_sampler("chebyshev:12")
    assert d.window_hours == 24.0 and d.periodic == "C0"


def test_rep_day_chebyshev_auto_order(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase, day_sampler="chebyshev:auto:tol=0.5")
    orders = case.stats["chebyshev_auto_orders"]
    assert len(orders) == 12 and set(orders) <= set(exp.DAY_ORDERS)
    assert case.stats["blocks_per_stage"] == max(orders)
    assert case.stats["period"] == f"day:chebyshev:{max(orders)}"


def test_chrono_reference_window(xcase: tuple[Path, Path]) -> None:
    case = _build(*xcase, week_sampler="chrono:28", week_steps_per_day=24)
    sim = case.planning["simulation"]
    st = sim["stage_array"]
    assert len(st) == 12 and st[0]["count_block"] == 28 * 24
    assert st[0]["period_hours"] == 28 * 24.0 and "sampling" not in st[0]
    dur = np.array([b["duration"] for b in sim["block_array"][: 28 * 24]])
    assert dur.sum() == pytest.approx(31 * 24)  # July: scaled to the month
    # the window is the first 28 days, hour by hour
    assert case.stats["week_window_start_hour"] == [0] * 12
    one = _build(*xcase)
    p1 = one.schedules[("Generator", "pmax")][_gens(one)["WF1"]["uid"]]
    pc = case.schedules[("Generator", "pmax")][_gens(case)["WF1"]["uid"]]
    assert pc.shape == (12, 28 * 24)
    # month-mean day of the reference ≈ the rep-day profile (28 of 31 days)
    assert np.mean(pc[0]) == pytest.approx(np.mean(p1[0]), rel=0.2)


def test_week_start_wednesday_noon(xcase: tuple[Path, Path]) -> None:
    """Every typical-week sampler starts its window on Wednesday 12:00 by
    default (``--week-start wed:12``); ``mon:0`` moves it."""
    months = exp.month_starts(2028, 1)
    for spec in ("uniform:2", "chebyshev:24"):
        wk = _build(*xcase, week_sampler=spec, week_steps_per_day=24)
        assert wk.stats["week_start"] == "wed:12"
        for day0, h0 in zip(months, wk.stats["week_window_start_hour"]):
            d, h = divmod(h0, 24)
            assert (day0 + timedelta(days=d)).weekday() == 2 and h == 12
    mon = _build(*xcase, week_sampler="uniform:2", week_start="mon:0")
    for day0, h0 in zip(months, mon.stats["week_window_start_hour"]):
        d, h = divmod(h0, 24)
        assert (day0 + timedelta(days=d)).weekday() == 0 and h == 0
    rep = _build(*xcase)
    assert rep.stats["week_start"] is None
    chrono = exp.make_sampler("chrono:28")
    assert exp.with_week_start(chrono, "wed:12") is chrono


def test_uniform_point_week(xcase: tuple[Path, Path]) -> None:
    """``uniform:1:point``: the hourly week as a point stage (power-based
    formulation): same grid and values as ``uniform:1``, node times at
    the hour centres, sampling = point, cc storage integration."""
    blk = _build(*xcase, week_sampler="uniform:1")
    pt = _build(*xcase, week_sampler="uniform:1:point")
    sb, sp = blk.planning["simulation"], pt.planning["simulation"]
    assert sp["stage_array"][0]["sampling"] == "point"
    assert "sampling" not in sb["stage_array"][0]
    assert [b["time"] for b in sp["block_array"][:3]] == [0.5, 1.5, 2.5]
    assert [b["duration"] for b in sp["block_array"]] == [
        b["duration"] for b in sb["block_array"]
    ]
    pb = blk.schedules[("Generator", "pmax")][_gens(blk)["WF1"]["uid"]]
    pp = pt.schedules[("Generator", "pmax")][_gens(pt)["WF1"]["uid"]]
    np.testing.assert_allclose(pp, pb)
    mo = pt.planning["options"]["model_options"]
    assert mo["battery_storage_integration"] == "cc"
    assert exp.make_sampler("uniform:2:point").point
    assert not exp.make_sampler("uniform:2").point
    with pytest.raises(ValueError):
        exp.make_sampler("uniform:1:bogus")


def test_ramp_refine_weeks(
    xcase: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--ramp-refine``: the 1 h block week gains 30 / 15 min blocks in
    the sun windows (same total duration), the Chebyshev week gains the
    nested 2N nodes (still a point stage with cc), "off" is a no-op.
    The fixture's flat net load has no ramps: the events are stubbed
    (detection is tested in test_week_sampler)."""
    import plexos2gtopt.week_sampler as ws  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        ws, "ramp_events", lambda ref, step, day_hours=24.0: np.array([30.0, 90.0])
    )

    def blocks(case):
        sim = case.planning["simulation"]
        return sim["block_array"][: sim["stage_array"][0]["count_block"]]

    def total(case) -> float:
        return sum(b["duration"] for b in blocks(case))

    base = _build(*xcase, week_sampler="uniform:1")
    for res, spec in ((0.5, "30m"), (0.25, "15m")):
        ref = _build(*xcase, week_sampler="uniform:1", ramp_refine=spec)
        assert len(blocks(ref)) > len(blocks(base))
        assert total(ref) == pytest.approx(total(base))
        assert ref.stats["ramp_refine"] == spec
        # binaries stay on the base hourly grid: 168 commitment cells
        cells = [b["commitment_cell"] for b in blocks(ref)]
        assert cells == sorted(cells) and len(set(cells)) == 168
        scale = total(base) / 168.0
        assert min(b["duration"] for b in blocks(ref)) == pytest.approx(res * scale)
    off = _build(*xcase, week_sampler="uniform:1", ramp_refine="off")
    assert len(blocks(off)) == len(blocks(base))

    ch = _build(*xcase, week_sampler="chebyshev:16")
    chr_ = _build(*xcase, week_sampler="chebyshev:16", ramp_refine="on")
    assert 16 < len(blocks(chr_)) < 32
    assert all("commitment_cell" not in b for b in blocks(chr_))
    assert total(chr_) == pytest.approx(total(ch))
    st = chr_.planning["simulation"]["stage_array"][0]
    assert st["sampling"] == "point"
    mo = chr_.planning["options"]["model_options"]
    assert mo["battery_storage_integration"] == "cc"
    with pytest.raises(ValueError):
        _build(*xcase, week_sampler="chebyshev:16:gauss", ramp_refine="30m")
    with pytest.raises(ValueError):
        exp.with_ramp_refine(object(), "30m")  # type: ignore[arg-type]


def test_week_sampler_gcost_schedule_width(xcase: tuple[Path, Path]) -> None:
    wk = _build(*xcase, week_sampler="uniform:2")
    for (cls, fld), tables in wk.schedules.items():
        for arr in tables.values():
            assert np.asarray(arr).shape[-1] == 84, (cls, fld)


def test_make_sampler() -> None:
    s = exp.make_sampler(
        "chebyshev:64:gauss:periodic=C0:odd_terms=3:bound_mode=bounded"
    )
    assert s.n == 64 and s.kind == "gauss" and s.periodic == "C0"
    assert s.odd_terms == 3 and s.bound_mode == "bounded"
    assert exp.make_sampler("uniform:2").hours == 2.0
    with pytest.raises(ValueError):
        exp.make_sampler("chebyshev:8:bogus=1")
    with pytest.raises(ValueError):
        exp.make_sampler("daily")


def test_chebyshev_auto_order(xcase: tuple[Path, Path]) -> None:
    assert exp.split_auto_order("chebyshev:64") is None
    spec, tol, metric = exp.split_auto_order(  # type: ignore[misc]
        "chebyshev:auto:gauss:tol=0.05:metric=ramp"
    )
    assert spec == "chebyshev:{n}:gauss" and tol == 0.05 and metric == "ramp"
    with pytest.raises(ValueError):
        exp.split_auto_order("chebyshev:auto:metric=bogus")
    wk = _build(*xcase, week_sampler="chebyshev:auto:tol=0.5")
    orders = wk.stats["chebyshev_auto_orders"]
    assert len(orders) == 12 and all(n >= 32 for n in orders)
    nb = max(orders)  # periodic Lobatto: N blocks (node N ≡ node 0 dropped)
    assert wk.stats["blocks_per_stage"] == nb
    assert wk.stats["period"] == f"chebyshev:{max(orders)}"


def test_feasible_cycle_eini_clips_retiring_battery(caplog):
    from plexos2gtopt.aemo_isp_expansion import (  # pylint: disable=import-outside-toplevel
        _feasible_cycle_eini,
    )

    item = {"emax": [[8.9] * 24, [8.9] * 24, [0.0] * 24], "emin": [[0.0] * 24] * 3}
    assert _feasible_cycle_eini("B", 4.46, item) == 0.0
    assert "outside the stage-boundary range" in caplog.text
    ok = {"emax": [[8.9] * 24] * 2, "emin": [[1.0] * 24] * 2}
    assert _feasible_cycle_eini("B", 4.46, ok) == 4.46
    assert _feasible_cycle_eini("B", 0.5, ok) == 1.0


def test_chebyshev_periodic_default_ends_agree():
    from plexos2gtopt.aemo_isp_expansion import (  # pylint: disable=import-outside-toplevel
        make_sampler,
    )

    s = make_sampler("chebyshev:32")
    assert s.periodic == "C0"
    assert make_sampler("chebyshev:32:periodic=none").periodic is None
    t = (np.arange(336) + 0.5) * 0.5
    y = 0.5 + 0.4 * np.sin(2 * np.pi * t / 24.0) + 0.002 * t
    g = s.grid()
    v = s.sample(y, 0.5, g)
    # node N ≡ node 0 dropped: 32 blocks, no duplicated end instant
    assert len(v) == 32 and g.times[-1] < 168.0
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


@pytest.mark.parametrize(
    ("category", "loss"),
    [
        ("New Entrant PHES NSW", 0.0),
        ("New Entrant Large-scale Battery NSW", None),
    ],
)
def test_battery_candidate_phes_has_no_self_discharge(
    category: str, loss: float | None, tmp_path: Path
) -> None:
    """PHES candidates pin ``annual_standing_loss = 0``.

    Li-ion keeps gtopt's default.
    """
    c = exp.Candidate(
        name="cand",
        oid=1,
        bus="b1",
        category=category,
        unit_mw=100.0,
        wacc=7.0,
        life=40.0,
        fom=0.0,
        vom=0.0,
        aux=0.0,
        heat_rate=None,
        fuel_oid=None,
        energy=True,
        max_units=[1.0],
        build_cost=[2000.0],
        kind="battery",
        unit_mwh=800.0,
        eff_in=0.85,
        eff_out=0.9,
    )
    opts = exp.ExpansionOptions(xml=tmp_path / "x.xml", trace_roots=[], years=1)
    entry = exp._battery_candidate(  # pylint: disable=protected-access
        c, [True], 1, opts, [0]
    )
    assert entry.get("annual_standing_loss") == loss
    assert entry["type"] == ("candidate_phes" if loss == 0.0 else "candidate_battery")
