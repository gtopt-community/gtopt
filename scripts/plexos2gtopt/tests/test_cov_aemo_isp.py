"""Branch tests for :mod:`plexos2gtopt.aemo_isp`.

Complements ``test_aemo_isp.py``: resolver edge rows (bands, unknown
tags, missing trace files, yearly traces), the skip rules of the
dispatch-window builder, the hydro helpers and the JSON writer.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from plexos2gtopt import aemo_isp
from plexos2gtopt.aemo_isp import (
    IspCase,
    IspCaseOptions,
    IspResolver,
    PumpGen,
    build_dispatch_case,
    write_case,
)
from plexos2gtopt.isp_traces import TraceLocator
from plexos2gtopt.plexos_xml import PlexosDataRow, load_xml
from plexos2gtopt.tests.test_aemo_isp import _halfhourly_csv, _Xml
from plexos2gtopt.tests.test_aemo_isp_constraints import _constraint, _CXml
from plexos2gtopt.tests.test_cov_aemo_isp_constraints import D0, Fake

# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------


def test_resolver_skips_odd_rows_and_tags(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    x = _Xml()
    n = x.obj("Node", "N")
    base = x.data("Node", n, "Load", 5)
    banded = x.data("Node", n, "Load", 9)
    other = x.data("Node", n, "Load", 7)
    # a Data File without a filename text and one whose file is missing
    x.data_file("Empty", "")
    gone = x.data_file("Gone", "Traces\\state\\gone.csv")
    ts = x.obj("Timeslice", "T")
    x.data("Timeslice", ts, "Include", -1, text="D1,H1")  # not a month pattern
    x.data("Timeslice", ts, "Include", -1, tags=(gone,))
    x.data("Timeslice", ts, "Include", -1, tags=(n,))
    x.data("Node", n, "Include", 1, text="M1")  # Include on a non-timeslice
    path = tmp_path / "m.xml"
    x.write(path)
    db = load_xml(path)
    db.band_for_data[banded] = 2
    db.tag_for_data[base] = [987654]  # unknown tag object: ignored
    db.tag_for_data[other] = [n]  # a non-timeslice / non-file tag
    db.data_rows.append(
        PlexosDataRow(data_id=999999, membership_id=999999, property_id=1, value=1.0)
    )
    res = IspResolver(db, TraceLocator([tmp_path]))
    assert res.scalar(n, "Load", D0) == 5.0
    assert "Empty" not in res.filename
    assert "T" not in res.calendar.months
    assert "timeslice file for Gone not found" in caplog.text
    (tagged,) = [r for r in res.rows[(n, "Load")] if r.other_tag]
    assert tagged.others == (n,)


def test_resolver_hourly_default_and_yearly_trace(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    x = _Xml()
    g = x.obj("Generator", "G1")
    x.data("Generator", g, "Rating", 4, date_from="2030-01-01T00:00:00")
    yearly = x.data_file("MEY", "Traces\\yearly\\mey.csv")
    x.data("Generator", g, "Max Energy Year", 0, tags=(yearly,))
    gone = x.data_file("Gone", "Traces\\gone.csv")
    x.data("Generator", g, "Max Capacity", 0, tags=(gone,))
    path = tmp_path / "m.xml"
    x.write(path)
    (tmp_path / "yearly").mkdir()
    (tmp_path / "yearly" / "mey.csv").write_text("Year,G1\n2027,3.5\n")
    (tmp_path / "yearly" / "nocol.csv").write_text("Year,Other\n2027,1\n")
    res = IspResolver(load_xml(path), TraceLocator([tmp_path]))
    assert np.allclose(res.hourly(g, "Rating", D0, 1, default=2.0), 2.0)
    assert np.allclose(res.hourly(g, "Max Energy Year", D0, 1), 3.5)
    res.filename["MEY"] = "Traces\\yearly\\nocol.csv"
    assert np.allclose(res.hourly(g, "Max Energy Year", D0, 1), 0.0)
    assert np.allclose(res.hourly(g, "Max Capacity", D0, 2), 0.0)
    assert res.missing_hits == 2
    assert caplog.text.count("trace for Data File 'Gone' not found") == 1


# ---------------------------------------------------------------------------
# dispatch window: skip rules and optional properties
# ---------------------------------------------------------------------------


def _skip_case(tmp_path: Path) -> Path:
    x = _CXml()
    n = x.obj("Node", "N")
    x.obj("Node", "EMPTY")  # no Load: no demand
    df = x.data_file("N", "Traces\\demand\\N_dem.csv")
    x.data("Node", n, "Load", 0, tags=(df,))
    gas = x.obj("Fuel", "Gas")
    x.data("Fuel", gas, "Price", 3.0)
    dam = x.obj("Storage", "Dam")
    x.data("Storage", dam, "Max Volume", 1000)

    def gen(name: str, *, node: bool = True, cap: bool = True) -> int:
        o = x.obj("Generator", name)
        if node:
            x.member("Generator", "Node", "Nodes", o, n)
        x.data("Generator", o, "Units", 1)
        if cap:
            x.data("Generator", o, "Max Capacity", 100)
        return o

    gen("NONODE", node=False)
    gen("NOCAP", cap=False)
    g1 = gen("G1")
    x.member("Generator", "Fuel", "Fuels", g1, gas)
    x.data("Generator", g1, "Heat Rate", 10)
    x.data("Generator", g1, "Rating Factor", 50)
    x.data("Generator", g1, "Max Energy Day", 1)
    x.data("Generator", g1, "Max Energy Year", 365)
    x.data("Generator", g1, "Min Load", 0)
    g2 = gen("G2")
    x.member("Generator", "Fuel", "Fuels", g2, gas)
    x.data("Generator", g2, "Heat Rate", 12)
    x.data("Generator", g2, "Max Energy Year", 0)
    # min capacity factor with a Rating but no Max Capacity
    g3 = gen("G3", cap=False)
    x.data("Generator", g3, "Rating", 50)
    x.data("Generator", g3, "Min Capacity Factor Year", 10)
    # hydro unit on a head storage without Efficiency Incr: no turbine
    hy = gen("HY")
    x.member("Generator", "Storage", "Head Storage", hy, dam)

    def bat(name: str, cap: float, power: float) -> None:
        o = x.obj("Battery", name)
        x.member("Battery", "Node", "Nodes", o, n)
        x.data("Battery", o, "Units", 1)
        x.data("Battery", o, "Capacity", cap)
        x.data("Battery", o, "Max Power", power)

    bat("B0CAP", 0, 10)
    bat("B0POW", 10, 0)

    def line(name: str, *, nodes: bool = True, flows: bool = True) -> None:
        o = x.obj("Line", name)
        if nodes:
            x.member("Line", "Node", "Node From", o, n)
            x.member("Line", "Node", "Node To", o, n)
        if flows:
            x.data("Line", o, "Max Flow", 10)
            x.data("Line", o, "Min Flow", -10)

    line("LNONODE", nodes=False)
    line("LNOFLOW", flows=False)
    # per-block RHS changing inside the window → RHS schedule
    c = _constraint(x, "Var1", "Group Constraints", sense=-1, RHS=50)
    x.data("Constraint", c, "RHS", 60, date_from="2027-01-02T00:00:00")
    x.coef("Generator", g1, c, "Generation Coefficient", 1.0)
    path = tmp_path / "m.xml"
    x.write(path)
    _halfhourly_csv(
        tmp_path / "demand" / "N_dem.csv", date(2027, 1, 1), 3, lambda k, h: 40.0
    )
    return path


def test_dispatch_skip_rules(tmp_path: Path) -> None:
    xml = _skip_case(tmp_path)
    opts = IspCaseOptions(
        xml=xml,
        trace_roots=[tmp_path],
        start=date(2027, 1, 1),
        days=2,
        demand_fail_cost=1234.0,
    )
    opts.isp.long_periods = "prorate"
    case = build_dispatch_case(opts)
    sysm = case.planning["system"]
    st = case.stats
    assert [d["name"] for d in sysm["demand_array"]] == ["load_N"]
    assert st["gen_skipped_no_node"] == 1
    assert st["gen_skipped_no_capacity"] == 1
    assert st["line_skipped"] == 2
    assert "battery_array" not in sysm
    assert "turbine_array" not in sysm
    assert case.planning["options"]["model_options"]["demand_fail_cost"] == 1234.0
    gens = {g["name"]: g for g in sysm["generator_array"]}
    assert gens["G1"]["capacity"] == pytest.approx(50.0)
    assert "pmin" not in gens["G1"]
    assert sysm["fuel_array"] == [{"uid": 1, "name": "Gas", "price": 3.0}]
    ucs = {uc["name"]: uc for uc in sysm["user_constraint_array"]}
    # Max Energy Year 365 GWh → 1 000 MWh/day (a later row overrides Day)
    assert ucs["energy_day_G1"]["expression"].endswith("<= 1000")
    assert "min_cf_year_G3" in ucs
    var = ucs["isp_Var1"]
    assert var["rhs"] == "rhs"
    rhs = case.schedules[("UserConstraint", "rhs")][var["uid"]]
    assert rhs[0] == pytest.approx(50.0) and rhs[-1] == pytest.approx(60.0)
    opts.include_batteries = False
    assert "battery_array" not in build_dispatch_case(opts).planning["system"]


# ---------------------------------------------------------------------------
# generator / hydro helpers
# ---------------------------------------------------------------------------


def _gen_obj(f: Fake, name: str = "G") -> Any:
    return f.res.obj[f.obj("Generator", name)]


def test_min_load_all_zero_and_cf_fallback() -> None:
    f = Fake()
    o = _gen_obj(f)
    f.prop(o.object_id, "Min Load", 0.0)
    gx: dict[str, Any] = {}
    assert aemo_isp._min_load(f.res, o, D0, 1, np.ones(24), gx) is None
    f.prop(o.object_id, "Min Capacity Factor Year", 50.0)
    opts = IspCaseOptions(xml=Path("x"), trace_roots=[], start=D0, days=1)
    opts.isp.long_periods = "prorate"
    gx = {"Min Capacity Factor Year → window energy floor": 0}
    uc = aemo_isp._min_cf_year_uc(
        f.res, o, D0, 24, 1.0, 0.0, np.full(24, 8.0), opts, gx
    )
    assert uc is not None
    # 50 % of the window peak (8 MW) over 24 h
    assert uc["expression"].endswith(">= 96")


def test_pump_gen_requires_units_and_efficiency() -> None:
    f = Fake()
    o = _gen_obj(f)
    f.prop(o.object_id, "Pump Load", 10.0)
    f.prop(o.object_id, "Pump Units", 0.0)
    tail = {o.object_id: 7}
    assert aemo_isp._pump_gen(f.res, o, D0, 1.0, "b", 1, tail, 2.0) is None
    f.res.rows[(o.object_id, "Pump Units")] = []
    f.prop(o.object_id, "Pump Efficiency", 0.0)
    assert aemo_isp._pump_gen(f.res, o, D0, 1.0, "b", 1, tail, 2.0) is None


def test_week_start_volume_rules() -> None:
    f = Fake()
    st = f.obj("Storage", "S")
    f.prop(st, "Initial Volume", 4000.0)
    f.db.horizon_start = datetime(2027, 1, 18)  # type: ignore[attr-defined]
    counts: dict[str, int] = {
        "horizon_start_initial_volume": 0,
        "target": 0,
        "initial_volume": 0,
    }
    vol = aemo_isp._week_start_volume(f.res, st, D0, 0.0, 3.0, counts)
    assert vol == 3.0
    f.db.horizon_start = None  # type: ignore[attr-defined]
    f.prop(st, "Target", 2000.0)
    assert aemo_isp._week_start_volume(f.res, st, D0, 0.0, 3.0, counts) == 2.0
    assert counts == {
        "horizon_start_initial_volume": 1,
        "target": 1,
        "initial_volume": 0,
    }


def test_build_hydro_waterways_and_skipped_pump() -> None:
    f = Fake()
    f.db.horizon_start = None  # type: ignore[attr-defined]
    a = f.obj("Storage", "A")
    b = f.obj("Storage", "B")
    w1 = f.obj("Waterway", "W1")
    w2 = f.obj("Waterway", "W2")
    f.obj("Waterway", "W3")  # no Storage From: skipped
    f.member("Waterway", "Storage From", w1, a)
    f.member("Waterway", "Storage To", w1, b)
    f.member("Waterway", "Storage From", w2, b)  # drains to the sea
    f.prop(w2, "Max Flow", 1e30)
    f.prop(w1, "Max Flow", 25.0)
    opts = IspCaseOptions(xml=Path("x"), trace_roots=[], start=D0, days=1)
    pumps = [PumpGen("P", "bus", head=999, tail=a, eff=1.0, pump_mw=1.0, pump_eff=1.0)]
    out = aemo_isp._build_hydro(f.res, opts, aemo_isp.HydroInputs([], pumps), {}, {})
    ww = {w["name"]: w for w in out["waterway_array"]}
    assert ww["W1"] == {
        "uid": 1,
        "name": "W1",
        "junction_a": "A",
        "junction_b": "B",
        "fmax": 25.0,
    }
    assert "junction_b" not in ww["W2"] and "fmax" not in ww["W2"]
    assert set(ww) == {"W1", "W2"}
    assert "pump_array" not in out or not out["pump_array"]


def test_fill_nan_from_day_neighbours() -> None:
    arr = np.array([np.nan, 1.0, np.nan, 3.0, np.nan])
    assert np.allclose(aemo_isp._fill_nan_from_day_neighbours(arr), [1, 1, 1, 3, 3])
    assert np.allclose(
        aemo_isp._fill_nan_from_day_neighbours(np.full(3, np.nan)), [0, 0, 0]
    )


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------


def test_write_case_skips_empty_schedules(tmp_path: Path) -> None:
    case = IspCase(
        planning={"x": 1.5},
        schedules={("Line", "tmax_ab"): {}, ("Demand", "lmax"): {1: np.ones(24)}},
    )
    path = write_case(case, tmp_path, "c")
    assert not (tmp_path / "Line").exists()
    assert (tmp_path / "Demand" / "lmax.parquet").exists()
    assert json.loads(path.read_text()) == {"x": 1.5}


def test_json_default() -> None:
    text = json.dumps(
        {"x": np.float64(1.5), "n": np.int32(2)}, default=aemo_isp._json_default
    )
    assert json.loads(text) == {"x": 1.5, "n": 2}
    assert aemo_isp._json_default(math.nan) is None
    with pytest.raises(TypeError):
        aemo_isp._json_default(object())
