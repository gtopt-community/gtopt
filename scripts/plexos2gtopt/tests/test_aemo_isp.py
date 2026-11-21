"""Tests for the AEMO ISP dispatch-window builder (:mod:`plexos2gtopt.aemo_isp`).

A synthetic ISP-shaped MasterDataSet XML is generated in ``tmp_path``
(no AEMO data is vendored): two sub-regions, a dated thermal unit, a
trace-driven solar farm (``Units`` × per-unit ``Rating`` trace), a
candidate with ``Units = 0``, a timesliced line, a battery, a hydro
storage with a head-storage turbine and a daily inflow trace.
"""

from __future__ import annotations

import json
from datetime import date
from xml.sax.saxutils import escape
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plexos2gtopt.aemo_isp import IspCaseOptions, build_dispatch_case, main, write_case
from plexos2gtopt.isp_traces import (
    TimesliceCalendar,
    TraceLocator,
    parse_month_pattern,
    read_interval_trace,
    read_yearly_column,
)
from plexos2gtopt.plexos_xml import NS


class _Xml:
    """Tiny MasterDataSet writer for tests."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.classes: dict[str, int] = {}
        self.objects: dict[str, int] = {}
        self.colls: dict[tuple[str, str], int] = {}
        self.props: dict[tuple[int, str], int] = {}
        self.sysmem: dict[int, int] = {}
        self.next = {"obj": 1000, "coll": 1, "prop": 1, "mem": 1, "data": 1}
        self.cls("System")
        self.system = self.obj("System", "NEM")

    def _row(self, table: str, **kv: object) -> None:
        body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in kv.items())
        self.parts.append(f"<{table}>{body}</{table}>")

    def cls(self, name: str) -> int:
        if name not in self.classes:
            cid = len(self.classes) + 1
            self.classes[name] = cid
            self._row("t_class", class_id=cid, name=name)
        return self.classes[name]

    def obj(self, cls: str, name: str) -> int:
        oid = self.next["obj"]
        self.next["obj"] += 1
        self._row("t_object", object_id=oid, class_id=self.cls(cls), name=name)
        self.objects[f"{cls}:{name}"] = oid
        if cls != "System":
            self.sysmem[oid] = self.member("System", cls, cls + "s", self.system, oid)
        return oid

    def coll(self, parent: str, child: str, name: str) -> int:
        key = (parent, name)
        if key not in self.colls:
            cid = self.next["coll"]
            self.next["coll"] += 1
            self.colls[key] = cid
            self._row(
                "t_collection",
                collection_id=cid,
                parent_class_id=self.cls(parent),
                child_class_id=self.cls(child),
                name=name,
            )
        return self.colls[key]

    def member(self, parent: str, child: str, name: str, p: int, c: int) -> int:
        cid = self.coll(parent, child, name)
        mid = self.next["mem"]
        self.next["mem"] += 1
        self._row(
            "t_membership",
            membership_id=mid,
            collection_id=cid,
            parent_object_id=p,
            child_object_id=c,
        )
        return mid

    def prop(self, cls: str, name: str) -> int:
        cid = self.coll("System", cls, cls + "s")
        key = (cid, name)
        if key not in self.props:
            pid = self.next["prop"]
            self.next["prop"] += 1
            self.props[key] = pid
            self._row("t_property", property_id=pid, collection_id=cid, name=name)
        return self.props[key]

    def data(
        self,
        cls: str,
        oid: int,
        prop: str,
        value: float,
        *,
        date_from: str | None = None,
        tags: tuple[int, ...] = (),
        text: str | None = None,
    ) -> int:
        did = self.next["data"]
        self.next["data"] += 1
        self._row(
            "t_data",
            data_id=did,
            membership_id=self.sysmem[oid],
            property_id=self.prop(cls, prop),
            value=value,
        )
        if date_from:
            self._row("t_date_from", data_id=did, date=date_from)
        for t in tags:
            self._row("t_tag", data_id=did, object_id=t)
        if text is not None:
            self._row("t_text", data_id=did, class_id=self.cls("Data File"), value=text)
        return did

    def data_file(self, name: str, filename: str) -> int:
        oid = self.obj("Data File", name)
        self.data("Data File", oid, "Filename", 0, text=filename)
        return oid

    def write(self, path: Path) -> None:
        body = "\n".join(self.parts)
        path.write_text(
            f'<MasterDataSet xmlns="{NS[1:-1]}">\n{body}\n</MasterDataSet>\n'
        )


def _halfhourly_csv(path: Path, start: date, days: int, fn) -> None:
    lines = ["Year,Month,Day," + ",".join(f"{i:02d}" for i in range(1, 49))]
    for k in range(days):
        d = pd.Timestamp(start) + pd.Timedelta(days=k)
        vals = [fn(k, h) for h in range(48)]
        lines.append(f"{d.year},{d.month},{d.day}," + ",".join(f"{v:g}" for v in vals))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture(name="isp_case")
def _isp_case(tmp_path: Path) -> tuple[Path, Path]:
    x = _Xml()
    north = x.obj("Node", "NTH")
    south = x.obj("Node", "STH")
    region = x.obj("Region", "R1")
    x.data("Region", region, "VoLL", 20000)
    # timeslices: Summer by month pattern, "Hot" by state file
    summer = x.obj("Timeslice", "Summer")
    x.data("Timeslice", summer, "Include", -1, text="M1-3,11,12")
    hot = x.obj("Timeslice", "R1 Hot Day")
    ts_df = x.data_file("Timeslice", "Traces\\timeslice\\ts.csv")
    x.data("Timeslice", hot, "Include", 0, tags=(ts_df,))
    # demand traces
    for node, name in ((north, "NTH"), (south, "STH")):
        df = x.data_file(name, f"Traces\\demand\\{name}_dem.csv")
        x.data("Node", node, "Load", 0)
        x.data("Node", node, "Load", 0, tags=(df,))
    # thermal: Units 1 now, retired 2030; Rating 90 on hot days
    fuel = x.obj("Fuel", "Coal")
    x.data("Fuel", fuel, "Price", 2.0, date_from="2025-07-01T00:00:00")
    x.data("Fuel", fuel, "Price", 3.0, date_from="2027-01-01T00:00:00")
    coal = x.obj("Generator", "COAL1")
    x.member("Generator", "Node", "Nodes", coal, north)
    x.member("Generator", "Fuel", "Fuels", coal, fuel)
    x.data("Generator", coal, "Units", 1)
    x.data("Generator", coal, "Units", 0, date_from="2030-07-01T00:00:00")
    x.data("Generator", coal, "Max Capacity", 100)
    x.data("Generator", coal, "Rating", 90, tags=(hot,))
    x.data("Generator", coal, "Heat Rate", 10)
    x.data("Generator", coal, "VO&M Charge", 5)
    x.data("Generator", coal, "Aux Incr", 10)
    # solar: 50 units x per-unit trace
    sol = x.obj("Generator", "SOLAR1")
    x.member("Generator", "Node", "Nodes", sol, south)
    sol_df = x.data_file("SOLAR1", "Traces\\solar\\SOLAR1_RefYear5000.csv")
    x.data("Generator", sol, "Units", 50)
    x.data("Generator", sol, "Max Capacity", 1)
    x.data("Generator", sol, "Rating", 0)
    x.data("Generator", sol, "Rating", 0, tags=(sol_df,))
    # candidate (Units 0) must be skipped
    cand = x.obj("Generator", "CAND1")
    x.member("Generator", "Node", "Nodes", cand, south)
    x.data("Generator", cand, "Max Capacity", 1)
    x.data("Generator", cand, "Max Units Built", 500, date_from="2027-07-01T00:00:00")
    # line with summer / not-summer limits
    line = x.obj("Line", "NTH-STH")
    x.member("Line", "Node", "Node From", line, north)
    x.member("Line", "Node", "Node To", line, south)
    x.data("Line", line, "Units", 1)
    x.data("Line", line, "Max Flow", 300)
    x.data("Line", line, "Min Flow", -200)
    x.data("Line", line, "Max Flow", 250, tags=(summer,))
    # battery 2 units x 10 MW / 20 MWh
    bat = x.obj("Battery", "BESS1")
    x.member("Battery", "Node", "Nodes", bat, south)
    x.data("Battery", bat, "Units", 2)
    x.data("Battery", bat, "Capacity", 20)
    x.data("Battery", bat, "Max Power", 10)
    x.data("Battery", bat, "Charge Efficiency", 90)
    x.data("Battery", bat, "Discharge Efficiency", 90)
    x.data("Battery", bat, "Max SoC", 100)
    # hydro: storage in ML, daily inflow trace in m3/s, turbine 2 MW/(m3/s)
    sto = x.obj("Storage", "Lake")
    x.data("Storage", sto, "Max Volume", 50000)
    x.data("Storage", sto, "Initial Volume", 20000)
    inflow_df = x.data_file("Lake", "Traces\\hydro\\Lake_inflow.csv")
    x.data("Storage", sto, "Natural Inflow", 0, tags=(inflow_df,))
    hyd = x.obj("Generator", "HYD1")
    x.member("Generator", "Node", "Nodes", hyd, north)
    x.member("Generator", "Storage", "Head Storage", hyd, sto)
    x.data("Generator", hyd, "Units", 1)
    x.data("Generator", hyd, "Max Capacity", 40)
    x.data("Generator", hyd, "Efficiency Incr", 2.0)
    # Generator properties read by `generator_extras`
    x.data("Generator", hyd, "Min Load", 6)
    x.data("Generator", hyd, "Min Load", 15, tags=(summer,))
    x.data("Generator", sol, "Mark-up", -1)
    x.data(
        "Generator",
        coal,
        "Min Capacity Factor Year",
        30,
        date_from="2026-07-01T00:00:00",
    )
    x.data("Generator", coal, "Outage Factor", 18.5)
    x.data("Generator", cand, "Maintenance Rate", 3.5)
    xml = tmp_path / "model" / "ISP Test Model.xml"
    xml.parent.mkdir(parents=True)
    x.write(xml)

    traces = tmp_path / "traces"
    start = date(2027, 1, 1)
    _halfhourly_csv(
        xml.parent / "Traces" / "demand" / "NTH_dem.csv", start, 31, lambda k, h: 100.0
    )
    _halfhourly_csv(
        xml.parent / "Traces" / "demand" / "STH_dem.csv",
        start,
        31,
        lambda k, h: 40.0 + h,
    )
    _halfhourly_csv(
        traces / "solar" / "SOLAR1_RefYear5000.csv",
        start,
        31,
        lambda k, h: 0.5 if 14 <= h < 36 else 0.0,
    )
    (traces / "hydro").mkdir(parents=True)
    (traces / "hydro" / "Lake_inflow.csv").write_text(
        "Year,Month,Day,1\n" + "".join(f"2027,1,{d},{10 + d}\n" for d in range(1, 32))
    )
    (traces / "timeslice").mkdir(parents=True)
    (traces / "timeslice" / "ts.csv").write_text(
        "DATETIME,NAME,TIMESLICE\n01/07/2026,R1 Hot Day,0\n"
        "19/01/2027,R1 Hot Day,-1\n20/01/2027,R1 Hot Day,0\n"
    )
    return xml, traces


def _opts(xml: Path, traces: Path, start: date, days: int = 3) -> IspCaseOptions:
    return IspCaseOptions(
        xml=xml, trace_roots=[xml.parent, traces], start=start, days=days, name="t"
    )


def test_dispatch_case_elements(isp_case: tuple[Path, Path]) -> None:
    xml, traces = isp_case
    case = build_dispatch_case(_opts(xml, traces, date(2027, 1, 18)))
    sys_ = case.planning["system"]
    assert [b["name"] for b in sys_["bus_array"]] == ["NTH", "STH"]
    gens = {g["name"]: g for g in sys_["generator_array"]}
    assert set(gens) == {"COAL1", "SOLAR1", "HYD1"}  # CAND1 has no units
    # fuel mode (default): gcost = VO&M, fuel cost via Fuel.price x heat rate
    assert gens["COAL1"]["gcost"] == pytest.approx(5.0)
    assert gens["COAL1"]["fuel"] == "Coal"
    # sent-out heat rate: Heat Rate / (1 − 10 % Aux Incr)
    assert gens["COAL1"]["heat_rate"] == pytest.approx(10.0 / 0.9)
    fuels = {f["name"]: f for f in sys_["fuel_array"]}
    assert fuels["Coal"]["price"] == pytest.approx(3.0)  # dated 2027 row
    pmax = case.schedules[("Generator", "pmax")]
    coal = pmax[gens["COAL1"]["uid"]]
    # 100 MW x (1 - 10 % aux); 90 MW rating on the hot day (2nd day)
    assert coal[:24] == pytest.approx(np.full(24, 90.0))
    assert coal[24:48] == pytest.approx(np.full(24, 81.0))
    solar = pmax[gens["SOLAR1"]["uid"]]
    assert solar.max() == pytest.approx(25.0)  # 50 units x 0.5
    assert solar[0] == pytest.approx(0.0)
    assert solar[7] == pytest.approx(25.0)
    # demand trace: hourly mean of the half-hours
    lmax = case.schedules[("Demand", "lmax")]
    assert lmax[1][0] == pytest.approx(100.0)
    assert lmax[2][0] == pytest.approx(40.5)
    assert case.planning["options"]["model_options"]["demand_fail_cost"] == 20000


def test_generator_property_extras(isp_case: tuple[Path, Path]) -> None:
    """Min Load → pmin, Mark-up → gcost, Min CF Year → prorated floor."""
    xml, traces = isp_case
    opts = _opts(xml, traces, date(2027, 1, 18), 2)
    case = build_dispatch_case(opts)
    gens = {g["name"]: g for g in case.planning["system"]["generator_array"]}
    assert gens["SOLAR1"]["gcost"] == pytest.approx(-1.0)
    assert gens["COAL1"]["gcost"] == pytest.approx(5.0)
    # Min Load: 15 MW in Summer (January), capped at pmax (40 MW)
    pmin = case.schedules[("Generator", "pmin")][gens["HYD1"]["uid"]]
    assert gens["HYD1"]["pmin"] == "pmin"
    assert pmin == pytest.approx(np.full(48, 15.0))
    # Min Capacity Factor Year is a long-period target: skipped by default
    ucs = case.planning["system"].get("user_constraint_array", [])
    assert not [u for u in ucs if u["name"].startswith("min_cf_year")]
    gp = case.stats["generator_properties"]
    assert gp["Min Capacity Factor Year: not enforced (long period)"] == 1
    assert gp["not modelled: Outage Factor"] == 1
    assert gp["Mark-up → gcost"] == 1
    assert gp["Min Load → pmin"] == 1

    opts.isp.long_periods = "prorate"
    pro = build_dispatch_case(opts)
    uc = {u["name"]: u for u in pro.planning["system"]["user_constraint_array"]}[
        "min_cf_year_COAL1"
    ]
    # 30 % × 1 unit × 100 MW × (1 − 10 % aux) × 48 h = 1296 MWh
    assert uc["expression"].endswith(">= 1296")
    assert uc["scope"] == "stage"

    opts.isp.generator_extras = False
    legacy = build_dispatch_case(opts)
    lg = {g["name"]: g for g in legacy.planning["system"]["generator_array"]}
    assert lg["SOLAR1"]["gcost"] == pytest.approx(0.0)
    assert "pmin" not in lg["HYD1"]
    assert "generator_properties" not in legacy.stats


def test_line_timeslice_and_battery(isp_case: tuple[Path, Path]) -> None:
    xml, traces = isp_case
    summer = build_dispatch_case(_opts(xml, traces, date(2027, 1, 18), 1))
    winter_opts = _opts(xml, traces, date(2027, 6, 1), 1)
    with pytest.raises(KeyError):
        # no demand trace rows in June in the synthetic files
        build_dispatch_case(winter_opts)
    tab = summer.schedules[("Line", "tmax_ab")][1]
    tba = summer.schedules[("Line", "tmax_ba")][1]
    assert tab == pytest.approx(np.full(24, 250.0))
    assert tba == pytest.approx(np.full(24, 200.0))
    bat = summer.planning["system"]["battery_array"][0]
    assert bat["emax"] == pytest.approx(40.0)
    assert bat["eini"] == pytest.approx(20.0)
    assert bat["input_efficiency"] == pytest.approx(0.9)
    assert bat["pmax_discharge"] == pytest.approx(20.0)


def test_hydro_mapping(isp_case: tuple[Path, Path]) -> None:
    xml, traces = isp_case
    case = build_dispatch_case(_opts(xml, traces, date(2027, 1, 18), 2))
    sys_ = case.planning["system"]
    res = sys_["reservoir_array"][0]
    assert res["emax"] == pytest.approx(50.0)  # 50 000 ML = 50 hm3
    assert res["eini"] == pytest.approx(20.0)
    assert res["efin"] == pytest.approx(20.0)
    tur = sys_["turbine_array"][0]
    assert tur["generator"] == "HYD1"
    assert tur["production_factor"] == pytest.approx(2.0)
    assert tur["drain"] is True
    inflow = case.schedules[("Flow", "discharge")][1]
    assert inflow[:24] == pytest.approx(np.full(24, 28.0))
    assert inflow[24:] == pytest.approx(np.full(24, 29.0))
    no_hydro = build_dispatch_case(
        IspCaseOptions(
            xml=xml,
            trace_roots=[traces],
            start=date(2027, 1, 18),
            days=1,
            include_hydro=False,
        )
    )
    assert "reservoir_array" not in no_hydro.planning["system"]


def test_write_case_and_cli(isp_case: tuple[Path, Path], tmp_path: Path) -> None:
    xml, traces = isp_case
    out = tmp_path / "out"
    rc = main(
        [
            str(xml),
            "-t",
            str(traces),
            "-s",
            "2027-01-18",
            "-d",
            "2",
            "-o",
            str(out),
            "-n",
            "wk",
        ]
    )
    assert rc == 0
    planning = json.loads((out / "wk.json").read_text())
    assert len(planning["simulation"]["block_array"]) == 48
    assert planning["options"]["model_options"]["use_kirchhoff"] is False
    df = pd.read_parquet(out / "Generator" / "pmax.parquet")
    assert list(df.columns) == ["stage", "block", "uid", "value"]
    assert len(df) == 48 * 3
    flow = pd.read_parquet(out / "Flow" / "discharge.parquet")
    assert "scenario" in flow.columns
    stats = json.loads((out / "wk.stats.json").read_text())
    assert stats["generators"] == 3
    assert stats["missing_traces"] == []
    case = build_dispatch_case(_opts(xml, traces, date(2027, 1, 18), 1))
    path = write_case(case, tmp_path / "out2", "x")
    assert path.is_file()


# ---------------------------------------------------------------------------
# isp_traces
# ---------------------------------------------------------------------------


def test_month_pattern() -> None:
    assert parse_month_pattern("M4-10") == set(range(4, 11))
    assert parse_month_pattern("M1-3,11,12") == {1, 2, 3, 11, 12}
    assert parse_month_pattern("D1,H1; D15,H1") is None


def test_calendar_state_file(tmp_path: Path) -> None:
    p = tmp_path / "ts.csv"
    p.write_text(
        "DATETIME,NAME,TIMESLICE\n18/12/2021,A,-1\n20/12/2021,A,0\n01/01/2022,B,-1\n"
    )
    cal = TimesliceCalendar()
    cal.add_state_csv(p)
    assert cal.add_month_name("M7")
    assert not cal.add_month_name("Summer")
    assert not cal.is_active("A", date(2021, 12, 17))
    assert cal.is_active("A", date(2021, 12, 19))
    assert not cal.is_active("A", date(2021, 12, 20))
    assert cal.is_active("M7", date(2030, 7, 3))
    assert not cal.is_active("unknown", date(2030, 7, 3))
    assert cal.known("B") and not cal.known("C")


def test_interval_trace_and_locator(tmp_path: Path) -> None:
    _halfhourly_csv(
        tmp_path / "Traces" / "wind" / "W.csv", date(2026, 7, 1), 2, lambda k, h: h
    )
    loc = TraceLocator([tmp_path / "nowhere", tmp_path])
    path = loc.resolve("Traces\\wind\\W.csv")
    assert path is not None
    arr = read_interval_trace(path, date(2026, 7, 2), 1)
    assert arr[0] == pytest.approx(0.5)
    assert arr[23] == pytest.approx(46.5)
    assert loc.resolve("Traces\\wind\\missing.csv") is None
    # a day outside the file is mapped by whole years into its range
    arr2 = read_interval_trace(path, date(2027, 7, 1), 1)
    assert arr2[1] == pytest.approx(2.5)
    with pytest.raises(KeyError):
        read_interval_trace(path, date(2027, 7, 3), 1, fill="none")


def test_yearly_column(tmp_path: Path) -> None:
    p = tmp_path / "MaxEnergyYear.csv"
    p.write_text("Year,G1,G2\n2020,10,20\n2021,11,21\n")
    assert read_yearly_column(p, 2021, "G2") == pytest.approx(21.0)
    assert read_yearly_column(p, 2030, "G1") == pytest.approx(11.0)
    assert read_yearly_column(p, 2010, "G1") == pytest.approx(10.0)
    assert read_yearly_column(p, 2021, "G9") is None
