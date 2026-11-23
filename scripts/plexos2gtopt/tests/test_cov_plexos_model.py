"""Branch coverage for :mod:`plexos2gtopt.plexos_model` edge cases.

Complements ``test_plexos_model.py`` (the SEM-like end-to-end fixture)
with focused tests of the helpers (rows, Data Files, the resolver
actions, the XML index) and small synthetic databases that hit the
less common materialisation branches: missing collections / classes,
outage ratings, multi-fuel ordering, pumped storage variants and DSP
bands without quantity.
"""

from __future__ import annotations

import csv
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import plexos_model as pm
from plexos2gtopt.plexos_model import (
    MaterialiseOptions,
    MaterialiseReport,
    find_data_file,
    materialise_bundle_dir,
    materialise_model,
    pattern_is_month_only,
    timeslice_matcher,
)
from plexos2gtopt.plexos_xml import NS

_URI = NS.strip("{}")

_GEN_PROPS = (
    "Max Capacity",
    "Units",
    "Units Out",
    "Rating",
    "Rating Factor",
    "Outage Rating",
    "Heat Rate",
    "VO&M Charge",
    "Pump Load",
    "Pump Efficiency",
    "Pump Units",
    "Marginal Loss Factor",
)
_ALL_PROPS: dict[tuple[str, str], tuple[str, ...]] = {
    ("System", "Generator"): _GEN_PROPS,
    ("System", "Fuel"): ("Price",),
    ("System", "Battery"): (
        "Units",
        "Capacity",
        "Max Power",
        "Max Load",
        "Charge Efficiency",
        "Discharge Efficiency",
    ),
    ("System", "Storage"): ("Max Volume", "Min Volume", "Initial Volume"),
    ("System", "Emission"): ("Price",),
    ("System", "Region"): ("Fixed Load", "DSP Bid Quantity", "DSP Bid Price"),
    ("System", "Node"): ("Load",),
    ("System", "Line"): (
        "Max Flow",
        "Min Flow",
        "Units",
        "Units Out",
        "Outage Max Rating",
        "Outage Min Rating",
    ),
    ("System", "Variable"): ("Profile",),
    ("System", "Data File"): ("Filename",),
    ("Generator", "Fuel"): ("Ratio", "Offtake at Start"),
    ("Emission", "Fuel"): ("Production Rate",),
}

_FULL_CLASSES = (
    "System",
    "Generator",
    "Fuel",
    "Battery",
    "Storage",
    "Emission",
    "Region",
    "Node",
    "Line",
    "Variable",
    "Data File",
    "Timeslice",
    "Scenario",
    "Model",
)
_FULL_COLLS = (
    *(("System", c, f"{c}s") for c in _FULL_CLASSES[1:]),
    ("Generator", "Fuel", "Fuels"),
    ("Generator", "Fuel", "Start Fuels"),
    ("Generator", "Node", "Nodes"),
    ("Generator", "Storage", "Head Storage"),
    ("Generator", "Storage", "Tail Storage"),
    ("Battery", "Node", "Nodes"),
    ("Emission", "Fuel", "Fuels"),
    ("Node", "Region", "Region"),
    ("Model", "Scenario", "Scenarios"),
)


class _Flex:
    """MasterDataSet builder with a configurable class / collection set."""

    def __init__(
        self,
        classes: tuple[str, ...] = _FULL_CLASSES,
        colls: tuple[tuple[str, str, str], ...] = _FULL_COLLS,
    ) -> None:
        self.root = ET.Element(f"{NS}MasterDataSet")
        self.cls = {c: i + 1 for i, c in enumerate(classes)}
        for name, cid in self.cls.items():
            self.row("t_class", class_id=cid, name=name)
        self.coll: dict[tuple[str, str, str], int] = {}
        self.prop: dict[tuple[int, str], int] = {}
        pid = 1
        for k, (p, c, n) in enumerate(colls, start=1):
            self.coll[(p, c, n)] = k
            self.row(
                "t_collection",
                collection_id=k,
                parent_class_id=self.cls[p],
                child_class_id=self.cls[c],
                name=n,
            )
            for prop in _ALL_PROPS.get((p, c), ()):
                self.prop[(k, prop)] = pid
                self.row("t_property", property_id=pid, collection_id=k, name=prop)
                pid += 1
        self.oid = 0
        self.mid = 0
        self.did = 0
        self.obj: dict[str, int] = {}
        self.sys_mid: dict[str, int] = {}
        self.add("System", "System", system=False)

    def row(self, table: str, **fields: object) -> None:
        elem = ET.SubElement(self.root, f"{NS}{table}")
        for k, v in fields.items():
            ET.SubElement(elem, f"{NS}{k}").text = str(v)

    def add(self, cls: str, name: str, system: bool = True) -> int:
        self.oid += 1
        self.row("t_object", object_id=self.oid, class_id=self.cls[cls], name=name)
        self.obj[name] = self.oid
        if system:
            self.sys_mid[name] = self.member("System", "System", cls, name, f"{cls}s")
        return self.oid

    def member(self, pcls: str, parent: str, ccls: str, child: str, cname: str) -> int:
        self.mid += 1
        self.row(
            "t_membership",
            membership_id=self.mid,
            parent_class_id=self.cls[pcls],
            parent_object_id=self.obj[parent],
            collection_id=self.coll[(pcls, ccls, cname)],
            child_class_id=self.cls[ccls],
            child_object_id=self.obj[child],
        )
        return self.mid

    def data(
        self,
        mid: int,
        coll: tuple[str, str, str],
        prop: str,
        value: float,
        *,
        band: int | None = None,
        date_from: str | None = None,
        pattern: str | None = None,
        text: str | None = None,
        tags: tuple[tuple[str, int | None], ...] = (),
    ) -> int:
        self.did += 1
        did = self.did
        pid = self.prop[(self.coll[coll], prop)]
        self.row("t_data", data_id=did, membership_id=mid, property_id=pid, value=value)
        if band is not None:
            self.row("t_band", data_id=did, band_id=band)
        if date_from:
            self.row("t_date_from", data_id=did, date=date_from)
        if pattern:
            self.row(
                "t_text", data_id=did, class_id=self.cls["Timeslice"], value=pattern
            )
        if text:
            self.row("t_text", data_id=did, class_id=self.cls["Data File"], value=text)
        for name, action in tags:
            fields: dict[str, object] = {"data_id": did, "object_id": self.obj[name]}
            if action is not None:
                fields["action_id"] = action
            self.row("t_tag", **fields)
        return did

    def sd(self, cls: str, name: str, prop: str, value: float, **kw: Any) -> int:
        return self.data(
            self.sys_mid[name], ("System", cls, f"{cls}s"), prop, value, **kw
        )

    def write(self, path: Path) -> Path:
        ET.register_namespace("", _URI)
        ET.ElementTree(self.root).write(path, encoding="utf-8", xml_declaration=True)
        return path


def _opts(model: str = "M", **kw: Any) -> MaterialiseOptions:
    return MaterialiseOptions(model=model, start=datetime(2025, 1, 1), **kw)


def _long(path: Path) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(row["NAME"], []).append(float(row["VALUE"]))
    return out


def _elem(**fields: str) -> ET.Element:
    e = ET.Element("x")
    for k, v in fields.items():
        ET.SubElement(e, f"{NS}{k}").text = v
    return e


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_timeslice_and_month_only_edge_terms() -> None:
    assert pattern_is_month_only("M1,,3")
    assert pattern_is_month_only(",")
    assert timeslice_matcher("M1,,2") is not None
    assert timeslice_matcher("X1") is None


def test_row_active_date_windows() -> None:
    r = pm._Row(1, 1, 1, 0.0, date_to=datetime(2025, 1, 2))
    assert r.active(datetime(2025, 1, 2, 23), 24)
    assert not r.active(datetime(2025, 1, 3), 1)
    r2 = pm._Row(1, 1, 1, 0.0, date_to=datetime(2025, 1, 2, 12))
    assert r2.active(datetime(2025, 1, 2, 11), 12)
    assert not r2.active(datetime(2025, 1, 2, 13), 14)
    r3 = pm._Row(1, 1, 1, 0.0, date_from=datetime(2025, 1, 2))
    assert not r3.active(datetime(2025, 1, 1), 1)
    r4 = pm._Row(1, 1, 1, 0.0, match=lambda _ts, p: p == 3)
    assert not r4.active(datetime(2025, 1, 1), 1)
    assert r4.active(datetime(2025, 1, 1, 2), 3)


def test_fint_ffloat_bad_values() -> None:
    e = _elem(a="x", b="", c="2.5")
    assert pm._fint(e, "a") is None
    assert pm._fint(e, "b") is None
    assert pm._fint(e, "missing") is None
    assert pm._ffloat(e, "a") is None
    assert pm._ffloat(e, "b") is None
    assert pm._ffloat(e, "c") == 2.5


def test_data_file_reader(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("Name,Value\nA,1\n")
    with pytest.raises(ValueError, match="unsupported Data File layout"):
        pm._DataFile(bad)

    one = tmp_path / "one.csv"
    one.write_text("Year,Month,Day,Period,Value\n\n2025,1,1,1,3.5\n")
    df = pm._DataFile(one)
    assert df.value(datetime(2025, 1, 1), 1, "7") == 3.5
    with pytest.raises(KeyError, match="no row"):
        df.value(datetime(2025, 1, 2), 1, "1")

    two = tmp_path / "two.csv"
    two.write_text("Year,Month,Day,Period,1,2\n2025,1,1,1,3,4\n")
    df2 = pm._DataFile(two)
    assert df2.value(datetime(2025, 1, 1), 1, "2") == 4.0
    with pytest.raises(KeyError, match="no column"):
        df2.value(datetime(2025, 1, 1), 1, "9")


def test_find_data_file_skips_non_dirs(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("")
    assert find_data_file("x.csv", [f, tmp_path / "missing"]) is None


def test_db_skips_incomplete_rows_and_lookups() -> None:
    root = ET.Element(f"{NS}MasterDataSet")
    for table, fields in (
        ("t_class", {"class_id": "1", "name": "System"}),
        ("t_class", {"class_id": "x", "name": "Bad"}),
        ("t_object", {"object_id": "1", "class_id": "1", "name": "S"}),
        ("t_object", {"object_id": "2"}),
        (
            "t_collection",
            {"collection_id": "1", "parent_class_id": "1", "child_class_id": "1"},
        ),
        ("t_collection", {"collection_id": "2"}),
        (
            "t_membership",
            {
                "membership_id": "1",
                "collection_id": "1",
                "parent_object_id": "1",
                "child_object_id": "1",
            },
        ),
        ("t_membership", {"membership_id": "2"}),
        ("t_property", {"property_id": "1", "collection_id": "1", "name": "P"}),
        ("t_property", {"property_id": "2"}),
    ):
        e = ET.SubElement(root, f"{NS}{table}")
        for k, v in fields.items():
            ET.SubElement(e, f"{NS}{k}").text = v
    db = pm._Db(root)
    assert db.class_name == {1: "System"}
    assert set(db.obj_name) == {1}
    assert set(db.coll) == {1}
    assert set(db.mem) == {1}
    assert db.prop_name == {1: "P"}
    assert db.cls_of(99) == ""
    assert db.collection_name_of_membership(1) == ""
    with pytest.raises(KeyError, match="no collection"):
        db.find_collection("System", "Generator")
    with pytest.raises(KeyError, match="no property"):
        db.find_property(1, "Q")


def test_parse_rows_skips_malformed_companions() -> None:
    b = _Flex()
    b.add("Model", "M")
    b.add("Node", "N")
    b.sd("Node", "N", "Load", 5.0)
    b.row("t_band", data_id="x")
    b.row("t_date_from", data_id=1, date="not-a-date")
    b.row("t_date_to", data_id="y", date="2025-01-01")
    b.row("t_text", data_id=1)
    b.row("t_tag", data_id=1)
    b.row("t_data", data_id=99, membership_id=1, property_id=1)
    db = pm._Db(b.root)
    report = MaterialiseReport()
    groups, dropped = pm._parse_rows(db, b.root, set(), report)
    rows = [r for rs in groups.values() for r in rs]
    assert len(rows) == 1
    assert rows[0].date_from is None
    assert not dropped


def test_editor_filter_noop() -> None:
    b = _Flex()
    db = pm._Db(b.root)
    ed = pm._XmlEditor(b.root, db)
    n = len(b.root)
    ed._filter("t_object", "object_id", set())
    assert len(b.root) == n


# ---------------------------------------------------------------------------
# Resolver expression actions
# ---------------------------------------------------------------------------


def test_resolver_actions_and_missing_operands(tmp_path: Path) -> None:
    b = _Flex()
    b.add("Model", "M")
    b.add("Variable", "V")
    b.add("Variable", "VEmpty")
    b.add("Variable", "VLoose", system=False)
    b.add("Data File", "DF")
    b.add("Data File", "DFBad")
    b.add("Data File", "DFNoText")
    b.add("Data File", "DFLoose", system=False)
    b.add("Node", "N")
    b.add("Fuel", "F")
    b.sd("Variable", "V", "Profile", 4.0)
    b.sd("Data File", "DF", "Filename", 0.0, text="dir\\d.csv")
    b.sd("Data File", "DFBad", "Filename", 0.0, text="loss.csv")
    b.sd("Data File", "DFNoText", "Filename", 0.0)
    (tmp_path / "d.csv").write_text(
        "Year,Month,Day,Period,1\n" + "".join(f"2025,1,1,{p},2\n" for p in range(1, 25))
    )
    (tmp_path / "loss.csv").write_text("Name,Pattern,Value\nA,M1,1\n")
    xml = b.write(tmp_path / "m.xml")
    root = ET.parse(xml).getroot()
    db = pm._Db(root)
    report = MaterialiseReport()
    groups, _ = pm._parse_rows(db, root, set(), report)
    res = pm._Resolver(db, groups, _opts(), [tmp_path], report)
    ts = datetime(2025, 1, 1)

    def val(base: float, exprs: list[tuple[int, int | None]]) -> float:
        return res.row_value(pm._Row(0, 0, 0, base, exprs=exprs), ts, 1)

    o = b.obj
    assert val(10.0, [(o["V"], pm._ACTION_DIV)]) == 2.5
    assert val(10.0, [(o["DF"], pm._ACTION_ADD)]) == 12.0
    assert val(10.0, [(o["DF"], pm._ACTION_SUB)]) == 8.0
    assert val(10.0, [(o["DF"], pm._ACTION_MUL)]) == 20.0
    assert val(10.0, [(o["DF"], None)]) == 2.0
    assert val(10.0, [(o["V"], 99)]) == 10.0  # unknown action ignored
    # Non-expression class, missing / empty operands keep the value.
    assert val(10.0, [(o["N"], 1)]) == 10.0
    assert val(10.0, [(o["VEmpty"], 1)]) == 10.0
    assert val(10.0, [(o["VLoose"], 1)]) == 10.0
    assert val(10.0, [(o["DFBad"], 1)]) == 10.0
    assert val(10.0, [(o["DFNoText"], 1)]) == 10.0
    assert val(10.0, [(o["DFLoose"], 1)]) == 10.0
    assert any("Data File not read" in n for n in report.notes)
    assert report.data_files == {"DF": "d.csv"}
    # Zero divisor keeps the value.
    zero = pm._Row(0, 0, 0, 0.0)
    res.index[(res.sys_mem[o["V"]], "Profile", 1)] = [zero]
    assert val(10.0, [(o["V"], pm._ACTION_DIV)]) == 10.0


# ---------------------------------------------------------------------------
# Materialisation branches
# ---------------------------------------------------------------------------


def test_minimal_db_without_optional_collections(tmp_path: Path) -> None:
    """No Emission/Generator-Fuel/Node-Region/Head Storage collections."""
    classes = ("System", "Generator", "Node", "Line", "Region", "Scenario", "Model")
    colls = tuple(("System", c, f"{c}s") for c in classes[1:]) + (
        ("Generator", "Node", "Nodes"),
    )
    b = _Flex(classes, colls)
    b.add("Model", "M")
    b.add("Node", "N")
    b.add("Node", "NoLoad")
    b.add("Node", "Loose", system=False)
    b.add("Generator", "G")
    b.add("Generator", "GLoose", system=False)
    b.add("Line", "L")
    b.add("Line", "LLoose", system=False)
    b.add("Region", "R")
    b.sd("Node", "N", "Load", 10.0)
    b.sd("Generator", "G", "Max Capacity", 100.0)
    b.sd("Generator", "G", "Rating", 80.0)
    b.sd("Generator", "G", "Outage Rating", 60.0)
    b.sd("Generator", "G", "Heat Rate", 9.0)
    b.sd("Generator", "G", "Heat Rate", 11.0, date_from="2025-01-01T12:00:00")
    b.sd("Line", "L", "Units", 1.0)
    xml = b.write(tmp_path / "x.xml")
    report = materialise_model(xml, _opts(), tmp_path / "o")
    rating = _long(tmp_path / "o" / "Gen_Rating.csv")
    assert rating["G"][0] == 60.0  # Rating (no factor) then outage cap
    hr = _long(tmp_path / "o" / "Gen_HeatRate.csv")
    assert hr["G"][0] == 9.0 and hr["G"][13] == 11.0
    assert not (tmp_path / "o" / "Lin_MaxRating.csv").exists()
    assert report.csv_written["Lin_Units.csv"] == 1


def test_no_battery_class_keeps_pumped_storage(tmp_path: Path) -> None:
    classes = ("System", "Generator", "Storage", "Node", "Scenario", "Model")
    colls = tuple(("System", c, f"{c}s") for c in classes[1:]) + (
        ("Generator", "Node", "Nodes"),
        ("Generator", "Storage", "Head Storage"),
    )
    b = _Flex(classes, colls)
    b.add("Model", "M")
    b.add("Storage", "H")
    b.add("Generator", "P")
    b.member("Generator", "P", "Storage", "H", "Head Storage")
    b.sd("Generator", "P", "Pump Load", 10.0)
    xml = b.write(tmp_path / "x.xml")
    report = materialise_model(xml, _opts(), tmp_path / "o")
    assert "pumped storage kept as hydro: no Battery class" in report.notes


def _pumped(b: _Flex, *, two_nodes: bool, empty_band: bool) -> None:
    b.add("Model", "M")
    for n in ("N1", "N2"):
        b.add("Node", n)
    b.add("Storage", "H")
    b.add("Storage", "T")
    b.add("Generator", "P1")
    b.add("Generator", "P2")
    for g, n in (("P1", "N1"), ("P2", "N2" if two_nodes else "N1")):
        b.member("Generator", g, "Node", n, "Nodes")
        b.member("Generator", g, "Storage", "H", "Head Storage")
        b.member("Generator", g, "Storage", "T", "Tail Storage")
        b.sd("Generator", g, "Max Capacity", 50.0)
        b.sd("Generator", g, "Pump Load", 40.0)
    b.sd("Generator", "P1", "Units", 2.0)
    b.sd("Generator", "P1", "Pump Units", 1.0)
    b.sd("Generator", "P2", "Pump Efficiency", 80.0)
    if not empty_band:
        b.sd("Storage", "H", "Max Volume", 2.0)
        b.sd("Storage", "H", "Initial Volume", 1.0)


def test_pumped_storage_pump_units_and_default_efficiency(tmp_path: Path) -> None:
    b = _Flex()
    _pumped(b, two_nodes=False, empty_band=False)
    report = materialise_model(b.write(tmp_path / "x.xml"), _opts(), tmp_path / "o")
    rw = next(r for r in report.rewrites if "pumped storage" in r)
    # P1: 2 units x 50 MW + P2: 1 x 50; pump P1: 1 pump unit x 40 + P2 40.
    assert "150 MW generating" in rw
    assert "80 MW pumping" in rw
    assert "pump efficiency 75 %" in rw  # mean(70 default, 80)
    assert (tmp_path / "o" / "BESS_IniValue.csv").exists()


def test_pumped_storage_zero_capacity_and_two_nodes(tmp_path: Path) -> None:
    b = _Flex()
    _pumped(b, two_nodes=False, empty_band=True)
    materialise_model(b.write(tmp_path / "x.xml"), _opts(), tmp_path / "o")
    assert not (tmp_path / "o" / "BESS_IniValue.csv").exists()

    b2 = _Flex()
    _pumped(b2, two_nodes=True, empty_band=False)
    report = materialise_model(b2.write(tmp_path / "y.xml"), _opts(), tmp_path / "o2")
    assert any("not rewritten (2 nodes)" in n for n in report.notes)


def test_fuels_ordering_start_costs_and_batteries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    b = _Flex()
    b.add("Model", "M")
    b.add("Scenario", "S")
    b.member("Model", "M", "Scenario", "S", "Scenarios")
    b.add("Node", "N")
    b.add("Region", "R")
    b.add("Region", "R2")
    b.member("Node", "N", "Region", "R", "Region")
    b.add("Fuel", "A")
    b.add("Fuel", "B")
    b.add("Emission", "CO2")
    b.add("Generator", "G")
    b.add("Battery", "BT")
    b.add("Battery", "BNoUnits")
    gb = b.member("Generator", "G", "Fuel", "B", "Fuels")
    ga = b.member("Generator", "G", "Fuel", "A", "Fuels")
    b.data(gb, ("Generator", "Fuel", "Fuels"), "Ratio", 0.3)
    b.data(ga, ("Generator", "Fuel", "Fuels"), "Ratio", 0.7)
    sf = b.member("Generator", "G", "Fuel", "A", "Start Fuels")
    b.data(sf, ("Generator", "Fuel", "Start Fuels"), "Offtake at Start", 0.0)
    em = b.member("Emission", "CO2", "Fuel", "A", "Fuels")
    b.data(em, ("Emission", "Fuel", "Fuels"), "Production Rate", 0.0)
    b.sd("Fuel", "A", "Price", 3.0)
    b.sd("Generator", "G", "Max Capacity", 10.0)
    # Battery Units = 2 scaled; a second Units row tagged with the scenario
    # (Units "None" impossible), and a battery without Units.
    b.sd("Battery", "BT", "Units", 2.0)
    b.sd("Battery", "BT", "Capacity", 4.0)
    b.sd("Battery", "BNoUnits", "Capacity", 3.0)
    # DSP band with zero quantity and no price -> no generator.
    b.sd("Region", "R", "DSP Bid Quantity", 0.0, band=1)
    # Region without DSP / Fixed Load.
    b.sd("Region", "R2", "Fixed Load", 0.0, tags=(("S", None),))
    # A pattern row varying a property that is frozen at start.
    b.sd("Battery", "BT", "Max Power", 1.0)
    b.sd("Battery", "BT", "Max Power", 2.0, pattern="H5")
    # Data File referenced only by a non-converted property, twice.
    b.add("Data File", "Lost")
    b.sd("Data File", "Lost", "Filename", 0.0, text="lost.csv")
    b.sd("Generator", "G", "Marginal Loss Factor", 1.0, tags=(("Lost", None),))
    b.sd(
        "Generator",
        "G",
        "Marginal Loss Factor",
        1.0,
        band=2,
        tags=(("Lost", None),),
    )
    xml = b.write(tmp_path / "x.xml")
    with caplog.at_level(logging.INFO, logger=pm.__name__):
        report = materialise_model(xml, _opts(), tmp_path / "o")
    assert any("largest-ratio fuel" in n for n in report.notes)
    out = ET.parse(tmp_path / "o" / "x.xml").getroot()
    gf = [
        int(e.findtext(f"{NS}child_object_id") or 0)
        for e in out.findall(f"{NS}t_membership")
        if e.findtext(f"{NS}collection_id")
        == str(b.coll[("Generator", "Fuel", "Fuels")])
    ]
    assert gf == [b.obj["A"], b.obj["B"]]
    assert "Gen_StartCost.csv" not in report.csv_written
    assert not any("DSP bid bands" in r for r in report.rewrites)
    assert report.frozen_at_start.get("Battery.Max Power") == 1
    assert sum("Data File 'Lost'" in n for n in report.notes) == 1  # note deduplicated
    assert any("frozen at its start value" in r.message for r in caplog.records)


def test_dsp_band_without_price_uses_default(tmp_path: Path) -> None:
    b = _Flex()
    b.add("Model", "M")
    b.add("Node", "N")
    b.add("Region", "R")
    b.add("Region", "RLoose", system=False)
    b.member("Node", "N", "Region", "R", "Region")
    b.sd("Region", "R", "DSP Bid Quantity", 5.0, band=1)
    report = materialise_model(b.write(tmp_path / "x.xml"), _opts(), tmp_path / "o")
    assert _long(tmp_path / "o" / "Gen_Rating.csv")["DSP R 1"][0] == 5.0
    # Regression: a band with a quantity but no price used to crash the
    # rewrite log line (``f"{None:g}"``); it now reports the default bid.
    assert any("DSP R 1 (5 MW @ 10000)" in r for r in report.rewrites)


def test_dsp_without_node_region_collection(tmp_path: Path) -> None:
    classes = ("System", "Generator", "Region", "Node", "Scenario", "Model")
    colls = tuple(("System", c, f"{c}s") for c in classes[1:])
    b = _Flex(classes, colls)
    b.add("Model", "M")
    b.add("Region", "R")
    b.sd("Region", "R", "DSP Bid Quantity", 5.0, band=1)
    report = materialise_model(b.write(tmp_path / "x.xml"), _opts(), tmp_path / "o")
    assert not report.rewrites


def test_materialise_bundle_dir_cleans_up_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[Path] = []

    def _boom(_xml: Path, _opts: MaterialiseOptions, out: Path) -> MaterialiseReport:
        made.append(out)
        raise RuntimeError("boom")

    monkeypatch.setattr(pm, "materialise_model", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        materialise_bundle_dir(tmp_path / "x.xml", _opts())
    assert made and not made[0].exists()


def test_materialise_bundle_dir_ok(tmp_path: Path) -> None:
    b = _Flex()
    b.add("Model", "M")
    xml = b.write(tmp_path / "x.xml")
    tmp, report = materialise_bundle_dir(xml, _opts())
    try:
        assert (Path(tmp.name) / "x.xml").is_file()
        assert report.model == "M"
    finally:
        tmp.cleanup()


def test_editor_add_data_non_default_band() -> None:
    b = _Flex()
    db = pm._Db(b.root)
    ed = pm._XmlEditor(b.root, db)
    did = ed.add_data(1, 1, 3.0, band=2)
    bands = [
        e
        for e in b.root.findall(f"{NS}t_band")
        if e.findtext(f"{NS}data_id") == str(did)
    ]
    assert len(bands) == 1 and bands[0].findtext(f"{NS}band_id") == "2"


def test_partial_hourly_rates_offtakes_and_misc_rows(tmp_path: Path) -> None:
    b = _Flex()
    b.add("Model", "M")
    b.add("Node", "N")
    b.add("Fuel", "A")
    b.add("Fuel", "B")
    b.add("Fuel", "Loose", system=False)
    b.add("Emission", "CO2")
    b.add("Storage", "H")
    b.add("Generator", "G")
    b.add("Generator", "Turbine")
    b.member("Generator", "Turbine", "Storage", "H", "Head Storage")
    b.sd("Fuel", "A", "Price", 2.0)
    b.sd("Fuel", "B", "Price", 4.0)
    b.sd("Emission", "CO2", "Price", 1.0)
    for fuel in ("A", "B"):
        em = b.member("Emission", "CO2", "Fuel", fuel, "Fuels")
        coll = ("Emission", "Fuel", "Fuels")
        b.data(em, coll, "Production Rate", 0.0)
        b.data(em, coll, "Production Rate", 3.0, pattern="H2")
    sf = b.member("Generator", "G", "Fuel", "A", "Start Fuels")
    coll = ("Generator", "Fuel", "Start Fuels")
    b.data(sf, coll, "Offtake at Start", 0.0)
    b.data(sf, coll, "Offtake at Start", 10.0, pattern="H3")
    b.sd("Generator", "G", "Max Capacity", 5.0)
    b.sd("Generator", "G", "Heat Rate", 8.0)  # constant: no schedule
    b.sd("Generator", "G", "Units", 1.0, pattern="Peak")  # unparsed pattern
    kept = b.sd("Node", "N", "Load", 7.0)
    # Malformed duplicate data_id without a <value>: left untouched.
    b.row("t_data", data_id=kept, membership_id=b.sys_mid["N"], property_id=1)
    xml = b.write(tmp_path / "x.xml")
    report = materialise_model(xml, _opts(), tmp_path / "o")
    price = _long(tmp_path / "o" / "Fuel_Price.csv")
    assert price["A"][0] == 2.0 and price["A"][1] == 5.0
    assert price["B"][1] == 7.0
    start = _long(tmp_path / "o" / "Gen_StartCost.csv")
    assert start["G"][2] == 20.0 and start["G"][0] == 0.0
    assert "Gen_HeatRate.csv" not in report.csv_written
    assert any("unparsed timeslice pattern 'Peak'" in n for n in report.notes)
    assert not any("pumped storage" in r for r in report.rewrites)


def test_missing_data_file_on_converted_property_raises(tmp_path: Path) -> None:
    b = _Flex()
    b.add("Model", "M")
    b.add("Battery", "BT")
    b.add("Data File", "Cap")
    b.sd("Data File", "Cap", "Filename", 0.0, text="cap.csv")
    b.sd("Battery", "BT", "Capacity", 1.0, tags=(("Cap", None),))
    with pytest.raises(pm.MissingDataFileError, match="cap.csv"):
        materialise_model(b.write(tmp_path / "x.xml"), _opts(), tmp_path / "o")
