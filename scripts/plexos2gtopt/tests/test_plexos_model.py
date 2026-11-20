"""Tests for :mod:`plexos2gtopt.plexos_model` (``--plexos-model``).

A small synthetic MasterDataSet exercises the materialiser: scenario
filtering and priority, dated / timeslice rows, Variable x Data File
expressions, the emission-price fold into fuel prices, generator
ratings with units, region Fixed Load, line schedules, the
pumped-storage -> Battery and DSP-bid -> Generator rewrites and
Battery Units scaling.  The banded ``Load Point`` / ``Heat Rate Incr``
curve, ``PlexosDb.band_values``, battery ``Max Load`` and the per-block
heat rate in the writer are covered too.
"""

from __future__ import annotations

import csv
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt.entities import FuelSpec, GeneratorSpec
from plexos2gtopt.gtopt_writer import build_generator_array
from plexos2gtopt.parsers import (
    _convexify_segments,
    _load_point_heat_rate_segments,
)
from plexos2gtopt.plexos_model import (
    MaterialiseOptions,
    find_data_file,
    materialise_model,
    parse_start_date,
    pattern_is_month_only,
    timeslice_matcher,
)
from plexos2gtopt.plexos_xml import NS, load_xml

_URI = NS.strip("{}")

_CLASSES = (
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

# (parent, child, collection name)
_COLLECTIONS = (
    *(("System", c, f"{c}s") for c in _CLASSES[1:]),
    ("Generator", "Fuel", "Fuels"),
    ("Generator", "Fuel", "Start Fuels"),
    ("Generator", "Node", "Nodes"),
    ("Generator", "Storage", "Head Storage"),
    ("Generator", "Storage", "Tail Storage"),
    ("Battery", "Node", "Nodes"),
    ("Emission", "Fuel", "Fuels"),
    ("Node", "Region", "Region"),
    ("Line", "Node", "Node From"),
    ("Line", "Node", "Node To"),
    ("Model", "Scenario", "Scenarios"),
)

_PROPS: dict[tuple[str, str], tuple[str, ...]] = {
    ("System", "Generator"): (
        "Max Capacity",
        "Units",
        "Units Out",
        "Rating",
        "Rating Factor",
        "Heat Rate",
        "Load Point",
        "Heat Rate Incr",
        "VO&M Charge",
        "Pump Load",
        "Pump Efficiency",
        "Pump Units",
        "Min Up Time",
        "Marginal Loss Factor",
    ),
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
    ("System", "Line"): ("Max Flow", "Min Flow", "Units"),
    ("System", "Variable"): ("Profile",),
    ("System", "Data File"): ("Filename",),
    ("Generator", "Fuel"): ("Ratio", "Offtake at Start"),
    ("Emission", "Fuel"): ("Production Rate",),
}


class _Builder:
    """Assemble a minimal PLEXOS MasterDataSet XML."""

    def __init__(self) -> None:
        self.root = ET.Element(f"{NS}MasterDataSet")
        self.cls = {c: i + 1 for i, c in enumerate(_CLASSES)}
        for name, cid in self.cls.items():
            self._row("t_class", class_id=cid, name=name)
        self.coll: dict[tuple[str, str, str], int] = {}
        self.prop: dict[tuple[int, str], int] = {}
        pid = 1
        for k, (p, c, n) in enumerate(_COLLECTIONS, start=1):
            self.coll[(p, c, n)] = k
            self._row(
                "t_collection",
                collection_id=k,
                parent_class_id=self.cls[p],
                child_class_id=self.cls[c],
                name=n,
            )
            for prop in _PROPS.get((p, c), ()):
                self.prop[(k, prop)] = pid
                self._row("t_property", property_id=pid, collection_id=k, name=prop)
                pid += 1
        self._oid = 0
        self._mid = 0
        self._did = 0
        self.obj: dict[str, int] = {}
        self.sys_mid: dict[str, int] = {}
        self.system = self.add_object("System", "System", system=False)

    def _row(self, table: str, **fields: object) -> None:
        elem = ET.SubElement(self.root, f"{NS}{table}")
        for k, v in fields.items():
            ET.SubElement(elem, f"{NS}{k}").text = str(v)

    def add_object(self, cls: str, name: str, system: bool = True) -> int:
        self._oid += 1
        oid = self._oid
        self._row("t_object", object_id=oid, class_id=self.cls[cls], name=name)
        self.obj[name] = oid
        if system:
            self.sys_mid[name] = self.member("System", "System", cls, name, f"{cls}s")
        return oid

    def member(
        self, pcls: str, parent: str, ccls: str, child: str, coll_name: str
    ) -> int:
        self._mid += 1
        coll = self.coll[(pcls, ccls, coll_name)]
        self._row(
            "t_membership",
            membership_id=self._mid,
            parent_class_id=self.cls[pcls],
            parent_object_id=self.obj[parent],
            collection_id=coll,
            child_class_id=self.cls[ccls],
            child_object_id=self.obj[child],
        )
        return self._mid

    def data(
        self,
        mid: int,
        coll: tuple[str, str, str],
        prop: str,
        value: float,
        *,
        band: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        pattern: str | None = None,
        text: str | None = None,
        tags: tuple[tuple[str, int | None], ...] = (),
    ) -> int:
        self._did += 1
        did = self._did
        pid = self.prop[(self.coll[coll], prop)]
        self._row(
            "t_data", data_id=did, membership_id=mid, property_id=pid, value=value
        )
        if band is not None:
            self._row("t_band", data_id=did, band_id=band)
        if date_from:
            self._row("t_date_from", data_id=did, date=date_from)
        if date_to:
            self._row("t_date_to", data_id=did, date=date_to)
        if pattern:
            self._row(
                "t_text", data_id=did, class_id=self.cls["Timeslice"], value=pattern
            )
        if text:
            self._row("t_text", data_id=did, class_id=self.cls["Data File"], value=text)
        for name, action in tags:
            fields: dict[str, object] = {"data_id": did, "object_id": self.obj[name]}
            if action is not None:
                fields["action_id"] = action
            self._row("t_tag", **fields)
        return did

    def sysdata(self, cls: str, name: str, prop: str, value: float, **kw) -> int:
        return self.data(
            self.sys_mid[name], ("System", cls, f"{cls}s"), prop, value, **kw
        )

    def write(self, path: Path) -> Path:
        ET.register_namespace("", _URI)
        ET.ElementTree(self.root).write(path, encoding="utf-8", xml_declaration=True)
        return path


def _read_long(path: Path) -> dict[tuple[str, int], list[float]]:
    out: dict[tuple[str, int], list[float]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["NAME"], int(row["BAND"]))
            out.setdefault(key, []).append(float(row["VALUE"]))
    return out


def _read_wide(path: Path) -> dict[str, list[float]]:
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    skip = {"YEAR", "MONTH", "DAY", "PERIOD"}
    names = [k for k in rows[0] if k.upper() not in skip]
    return {n: [float(r[n]) for r in rows] for n in names}


def _sem_like(tmp_path: Path) -> Path:
    """A two-node SEM-like toy model with the features the SEM uses."""
    b = _Builder()
    for name, cls in (
        ("Base", "Scenario"),
        ("Off", "Scenario"),
        ("M1", "Model"),
        ("R1", "Region"),
        ("N1", "Node"),
        ("N2", "Node"),
        ("Gas", "Fuel"),
        ("Oil", "Fuel"),
        ("CO2", "Emission"),
        ("CCGT", "Generator"),
        ("Wind", "Generator"),
        ("WindVar", "Variable"),
        ("WindFile", "Data File"),
        ("LossFile", "Data File"),
        ("TH", "Generator"),
        ("HEAD", "Storage"),
        ("TAIL", "Storage"),
        ("B1", "Battery"),
        ("B0", "Battery"),
        ("L1", "Line"),
    ):
        b.add_object(cls, name)
    b.member("Model", "M1", "Scenario", "Base", "Scenarios")
    b.member("Node", "N1", "Region", "R1", "Region")
    b.member("Node", "N2", "Region", "R1", "Region")  # 2 nodes: no Fixed Load
    for g, n in (("CCGT", "N1"), ("Wind", "N1"), ("TH", "N1")):
        b.member("Generator", g, "Node", n, "Nodes")
    for bat in ("B1", "B0"):
        b.member("Battery", bat, "Node", "N1", "Nodes")
    b.member("Generator", "TH", "Storage", "HEAD", "Head Storage")
    b.member("Generator", "TH", "Storage", "TAIL", "Tail Storage")
    b.member("Line", "L1", "Node", "N1", "Node From")
    b.member("Line", "L1", "Node", "N2", "Node To")

    # Loads: base 100, Base-scenario 120 (wins), Off-scenario 999 (dropped).
    b.sysdata("Node", "N1", "Load", 100.0)
    b.sysdata("Node", "N1", "Load", 120.0, tags=(("Base", None),))
    b.sysdata("Node", "N1", "Load", 999.0, tags=(("Off", None),))
    # Evening peak timeslice: +30 MW on H18-20 via a pattern row.
    b.sysdata("Node", "N1", "Load", 150.0, pattern="H18-20", tags=(("Base", None),))
    b.sysdata("Node", "N2", "Load", 40.0)

    # Fuel price dated step + CO2 fold (0.05 EUR/kg x 56 kg/GJ = 2.8).
    b.sysdata("Fuel", "Gas", "Price", 8.0)
    b.sysdata("Fuel", "Gas", "Price", 10.0, date_from="2025-01-02T00:00:00")
    b.sysdata("Fuel", "Oil", "Price", 20.0)
    b.sysdata("Emission", "CO2", "Price", 0.05)
    em = b.member("Emission", "CO2", "Fuel", "Gas", "Fuels")
    b.data(em, ("Emission", "Fuel", "Fuels"), "Production Rate", 56.0)

    # CCGT: two units, one out; Oil ratio 0 (removed); start offtake.
    b.sysdata("Generator", "CCGT", "Max Capacity", 200.0)
    b.sysdata("Generator", "CCGT", "Units", 2.0)
    b.sysdata("Generator", "CCGT", "Units Out", 1.0, date_from="2025-01-02T00:00:00")
    b.sysdata("Generator", "CCGT", "Min Up Time", 4.0)
    gas = b.member("Generator", "CCGT", "Fuel", "Gas", "Fuels")
    oil = b.member("Generator", "CCGT", "Fuel", "Oil", "Fuels")
    b.data(gas, ("Generator", "Fuel", "Fuels"), "Ratio", 1.0)
    b.data(oil, ("Generator", "Fuel", "Fuels"), "Ratio", 0.0)
    sf = b.member("Generator", "CCGT", "Fuel", "Gas", "Start Fuels")
    b.data(sf, ("Generator", "Fuel", "Start Fuels"), "Offtake at Start", 100.0)
    for band, (lp, inc) in enumerate(((100.0, 7.0), (200.0, 8.0)), start=1):
        b.sysdata("Generator", "CCGT", "Load Point", lp, band=band)
        b.sysdata("Generator", "CCGT", "Heat Rate Incr", inc, band=band)

    # Wind: Rating Factor = 100 x Variable(Profile <- Data File sample).
    b.sysdata("Generator", "Wind", "Max Capacity", 50.0)
    b.sysdata("Generator", "Wind", "Rating Factor", 100.0, tags=(("WindVar", 1),))
    b.sysdata("Variable", "WindVar", "Profile", 0.0, tags=(("WindFile", None),))
    b.sysdata("Data File", "WindFile", "Filename", 0.0, text="CSV Files\\wind.csv")
    # A loss-factor table that is not shipped: only a non-converted
    # property refers to it, so its absence is not an error.
    b.sysdata("Data File", "LossFile", "Filename", 0.0, text="CSV Files\\tlaf.csv")
    b.sysdata(
        "Generator", "CCGT", "Marginal Loss Factor", 1.0, tags=(("LossFile", None),)
    )
    data_dir = tmp_path / "data"
    (data_dir / "CSV Files").mkdir(parents=True)
    with (data_dir / "CSV Files" / "wind.csv").open("w", encoding="utf-8") as fh:
        fh.write("Year,Month,Day,Period,1,2\n")
        for day in (1, 2):
            for p in range(1, 25):
                fh.write(f"2025,1,{day},{p},{0.01 * p:.2f},0.5\n")

    # Pumped storage: 2 GWh head band, 290 MW turbine, 280 MW pump, 70 %.
    b.sysdata("Storage", "HEAD", "Max Volume", 2.3)
    b.sysdata("Storage", "HEAD", "Min Volume", 0.3)
    b.sysdata("Storage", "HEAD", "Initial Volume", 1.3)
    b.sysdata("Generator", "TH", "Max Capacity", 72.5)
    b.sysdata("Generator", "TH", "Units", 4.0)
    b.sysdata("Generator", "TH", "Pump Load", 70.0)
    b.sysdata("Generator", "TH", "Pump Efficiency", 70.0)

    # Batteries: B1 has 2 units of 10 MW / 20 MWh; B0 not built yet.
    for prop, v in (("Capacity", 20.0), ("Max Power", 10.0), ("Max Load", 8.0)):
        b.sysdata("Battery", "B1", prop, v)
    b.sysdata("Battery", "B1", "Units", 2.0)
    b.sysdata("Battery", "B0", "Capacity", 5.0)
    b.sysdata("Battery", "B0", "Units", 0.0)
    b.sysdata("Battery", "B0", "Units", 1.0, date_from="2026-01-01T00:00:00")

    # DSP: two bands on a single-node region would be required; R1 has
    # two nodes, so the bids are skipped and noted.
    b.sysdata("Region", "R1", "Fixed Load", -10.0)
    b.sysdata("Region", "R1", "DSP Bid Quantity", 5.0, band=1)
    b.sysdata("Region", "R1", "DSP Bid Price", 300.0, band=1)

    # Line: in service from day 2, asymmetric flows.
    b.sysdata("Line", "L1", "Max Flow", 80.0)
    b.sysdata("Line", "L1", "Min Flow", -60.0)
    b.sysdata("Line", "L1", "Units", 0.0)
    b.sysdata("Line", "L1", "Units", 1.0, date_from="2025-01-02T00:00:00")
    return b.write(tmp_path / "model.xml")


# ---------------------------------------------------------------------------
# Timeslice patterns
# ---------------------------------------------------------------------------


def test_timeslice_months_hours_and_continuation() -> None:
    f = timeslice_matcher("M1-3,10-12")
    assert f is not None
    assert f(datetime(2025, 2, 1), 5)
    assert f(datetime(2025, 11, 1), 5)
    assert not f(datetime(2025, 6, 1), 5)
    g = timeslice_matcher("M04-09,H18-20")
    assert g is not None
    assert g(datetime(2025, 5, 1), 19)
    assert not g(datetime(2025, 5, 1), 17)
    assert not g(datetime(2025, 1, 1), 19)


def test_timeslice_negation_weekday_and_day() -> None:
    f = timeslice_matcher("!H18-19")
    assert f is not None
    assert f(datetime(2025, 1, 1), 1)
    assert not f(datetime(2025, 1, 1), 18)
    # 2025-01-05 is a Sunday = PLEXOS W1; Monday is W2.
    w = timeslice_matcher("W1")
    assert w is not None
    assert w(datetime(2025, 1, 5), 1)
    assert not w(datetime(2025, 1, 6), 1)
    d = timeslice_matcher("D15")
    assert d is not None
    assert d(datetime(2025, 3, 15), 1)
    assert not d(datetime(2025, 3, 16), 1)


def test_timeslice_unknown_returns_none() -> None:
    assert timeslice_matcher("Peak") is None
    assert timeslice_matcher("Q1") is None
    assert timeslice_matcher("") is None


def test_pattern_is_month_only() -> None:
    assert pattern_is_month_only("M04-10")
    assert pattern_is_month_only("M1-3,11-12")
    assert not pattern_is_month_only("M1,H5")
    assert not pattern_is_month_only("Peak")


def test_parse_start_date_and_find_data_file(tmp_path: Path) -> None:
    assert parse_start_date("2025-01-13") == datetime(2025, 1, 13)
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "Wind.CSV.xz").write_bytes(b"")
    assert find_data_file("X\\wind.csv", [tmp_path]) == sub / "Wind.CSV.xz"
    assert find_data_file("missing.csv", [tmp_path]) is None


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


@pytest.fixture(name="materialised")
def fixture_materialised(tmp_path: Path):
    xml = _sem_like(tmp_path)
    out = tmp_path / "out"
    opts = MaterialiseOptions(
        model="M1",
        start=datetime(2025, 1, 1),
        n_days=2,
        sample=1,
        data_dirs=[tmp_path / "data"],
    )
    report = materialise_model(xml, opts, out)
    return out, report


def test_report_scenarios_and_counts(materialised) -> None:
    _out, report = materialised
    assert report.scenarios == ["Base"]
    assert report.rows_dropped_scenario == 1
    assert report.data_files == {"WindFile": "wind.csv"}
    assert "Generator.Min Up Time" not in report.not_converted
    assert report.not_converted["Generator.Marginal Loss Factor"] == 1
    notes = [n for n in report.notes if "tlaf.csv" in n]
    assert len(notes) == 1
    assert "not converted" in notes[0]
    assert str(Path.cwd()) not in notes[0]
    assert report.as_dict()["model"] == "M1"


def test_missing_data_file_of_converted_property_raises(tmp_path: Path) -> None:
    xml = _sem_like(tmp_path)
    shutil.rmtree(tmp_path / "data")
    opts = MaterialiseOptions(model="M1", start=datetime(2025, 1, 1), n_days=1)
    # The wind Rating Factor needs wind.csv, which is gone.
    with pytest.raises(FileNotFoundError, match="wind.csv"):
        materialise_model(xml, opts, tmp_path / "o")


def test_unknown_model_raises(tmp_path: Path) -> None:
    xml = _sem_like(tmp_path)
    opts = MaterialiseOptions(model="nope", start=datetime(2025, 1, 1), n_days=1)
    with pytest.raises(ValueError, match="not found"):
        materialise_model(xml, opts, tmp_path / "o")


def test_node_load_scenario_pattern_and_fixed_load(materialised) -> None:
    out, report = materialised
    load = _read_wide(out / "Nod_Load.csv")
    n1 = load["N1"]
    assert len(n1) == 48
    assert n1[0] == pytest.approx(120.0)  # scenario row beats base row
    assert n1[17] == pytest.approx(150.0)  # H18 pattern row
    assert n1[20] == pytest.approx(120.0)  # H21 outside the pattern
    assert load["N2"][0] == pytest.approx(40.0)
    # Two-node region: Fixed Load and DSP bids are not applied.
    assert any("Fixed Load not applied" in n for n in report.notes)
    assert any("DSP bids of region 'R1' skipped" in n for n in report.notes)


def test_fuel_price_dates_and_emission_fold(materialised) -> None:
    out, _report = materialised
    price = _read_long(out / "Fuel_Price.csv")
    gas = price[("Gas", 1)]
    assert gas[0] == pytest.approx(8.0 + 2.8)
    assert gas[24] == pytest.approx(10.0 + 2.8)
    assert price[("Oil", 1)][0] == pytest.approx(20.0)
    start = _read_long(out / "Gen_StartCost.csv")
    assert start[("CCGT", 1)][0] == pytest.approx(100.0 * 10.8)


def test_generator_ratings_units_and_rating_factor(materialised) -> None:
    out, _report = materialised
    rating = _read_long(out / "Gen_Rating.csv")
    ccgt = rating[("CCGT", 1)]
    assert ccgt[0] == pytest.approx(400.0)
    assert ccgt[24] == pytest.approx(200.0)  # one unit out from day 2
    wind = rating[("Wind", 1)]
    # 50 MW x (100 x 0.01 p) / 100.
    assert wind[0] == pytest.approx(0.5)
    assert wind[23] == pytest.approx(12.0)
    assert ("TH", 1) not in rating  # pumped storage is rewritten


def test_dead_fuel_membership_removed(materialised) -> None:
    out, _report = materialised
    db = load_xml(out / "model.xml")
    by_id = db.object_by_id()
    gen = next(o for o in db.objects_of_class("Generator") if o.name == "CCGT")
    fuels = sorted(
        by_id[m.child_object_id].name
        for m in db.memberships
        if m.parent_object_id == gen.object_id
    )
    assert "Oil" not in fuels
    assert "Gas" in fuels


def test_line_schedules(materialised) -> None:
    out, _report = materialised
    units = _read_wide(out / "Lin_Units.csv")["L1"]
    assert units[0] == 0.0
    assert units[24] == 1.0
    assert _read_long(out / "Lin_MaxRating.csv")[("L1", 1)][0] == 80.0
    assert _read_long(out / "Lin_MinRating.csv")[("L1", 1)][0] == -60.0


def test_pumped_storage_becomes_battery(materialised) -> None:
    out, report = materialised
    db = load_xml(out / "model.xml")
    names = {o.name for o in db.objects_of_class("Battery")}
    assert "HEAD pumped storage" in names
    assert not any(o.name == "TH" for o in db.objects_of_class("Generator"))
    assert not db.objects_of_class("Storage")
    bat = next(o for o in db.objects_of_class("Battery") if o.name.startswith("HEAD"))
    assert db.static_property("Battery", bat.object_id, "Capacity") == pytest.approx(
        2000.0
    )
    assert db.static_property("Battery", bat.object_id, "Max Power") == pytest.approx(
        290.0
    )
    assert db.static_property("Battery", bat.object_id, "Max Load") == pytest.approx(
        280.0
    )
    ini = _read_long(out / "BESS_IniValue.csv")[("HEAD pumped storage", 1)]
    assert ini[0] == pytest.approx(50.0)
    assert any("pumped storage 'HEAD'" in r for r in report.rewrites)


def test_battery_units_scale_and_unbuilt_removed(materialised) -> None:
    out, report = materialised
    db = load_xml(out / "model.xml")
    batts = {o.name: o.object_id for o in db.objects_of_class("Battery")}
    assert "B0" not in batts
    assert db.static_property("Battery", batts["B1"], "Capacity") == pytest.approx(40.0)
    assert db.static_property("Battery", batts["B1"], "Max Load") == pytest.approx(16.0)
    assert any("Units = 0" in r for r in report.rewrites)


def test_static_xml_has_no_scenario_tags(materialised) -> None:
    out, _report = materialised
    root = ET.parse(out / "model.xml").getroot()
    assert not root.findall(f"{NS}t_tag")
    assert not root.findall(f"{NS}t_date_from")


def test_dsp_bids_single_node_region(tmp_path: Path) -> None:
    b = _Builder()
    for name, cls in (
        ("M", "Model"),
        ("R", "Region"),
        ("N", "Node"),
    ):
        b.add_object(cls, name)
    b.member("Node", "N", "Region", "R", "Region")
    b.sysdata("Node", "N", "Load", 100.0)
    b.sysdata("Region", "R", "Fixed Load", -10.0)
    for band, (q, p) in enumerate(((5.0, 300.0), (7.0, 400.0)), start=1):
        b.sysdata("Region", "R", "DSP Bid Quantity", q, band=band)
        b.sysdata("Region", "R", "DSP Bid Price", p, band=band)
    xml = b.write(tmp_path / "dsp.xml")
    opts = MaterialiseOptions(model="M", start=datetime(2025, 1, 1), n_days=1)
    report = materialise_model(xml, opts, tmp_path / "o")
    assert _read_wide(tmp_path / "o" / "Nod_Load.csv")["N"][0] == pytest.approx(90.0)
    rating = _read_long(tmp_path / "o" / "Gen_Rating.csv")
    assert rating[("DSP R 1", 1)][0] == pytest.approx(5.0)
    assert rating[("DSP R 2", 1)][0] == pytest.approx(7.0)
    db = load_xml(tmp_path / "o" / "dsp.xml")
    g2 = next(o for o in db.objects_of_class("Generator") if o.name == "DSP R 2")
    assert db.static_property("Generator", g2.object_id, "VO&M Charge") == 400.0
    assert any("DSP bid bands" in r for r in report.rewrites)


# ---------------------------------------------------------------------------
# Parser / writer helpers
# ---------------------------------------------------------------------------


def test_band_values_and_load_point_segments(tmp_path: Path) -> None:
    xml = _sem_like(tmp_path)
    db = load_xml(xml)
    ccgt = next(o for o in db.objects_of_class("Generator") if o.name == "CCGT")
    assert db.band_values("Generator", ccgt.object_id, "Load Point") == [
        (1, 100.0),
        (2, 200.0),
    ]
    assert db.band_values("Generator", ccgt.object_id, "Pump Load") == []
    bps, slopes = _load_point_heat_rate_segments(db, ccgt.object_id, 200.0)
    assert bps == (100.0, 200.0)
    assert slopes == (7.0, 8.0)
    # Load points beyond pmax are clipped; below pmax the last slope
    # extends (and merges with the equal last band).
    assert _load_point_heat_rate_segments(db, ccgt.object_id, 150.0) == (
        (100.0, 150.0),
        (7.0, 8.0),
    )
    assert _load_point_heat_rate_segments(db, ccgt.object_id, 300.0) == (
        (100.0, 300.0),
        (7.0, 8.0),
    )
    wind = next(o for o in db.objects_of_class("Generator") if o.name == "Wind")
    assert _load_point_heat_rate_segments(db, wind.object_id, 50.0) == ((), ())


def test_writer_emits_per_block_heat_rate() -> None:
    fuel = (FuelSpec(object_id=9, name="Gas", price=10.0),)
    gen = GeneratorSpec(
        object_id=1,
        name="IC",
        bus_name="N1",
        type_tag="thermal",
        pmax=100.0,
        heat_rate=10.0,
        heat_rate_profile=(10.0, 12.0, 14.0, 16.0),
        fuel_names=("Gas",),
    )
    layout = ((1, 2), (3, 4))
    blocks = build_generator_array((gen,), fuel, block_layout=layout)
    assert blocks[0]["heat_rate"] == [[11.0, 15.0]]
    hourly = build_generator_array((gen,), fuel)
    assert hourly[0]["heat_rate"] == [[10.0, 12.0, 14.0, 16.0]]
    flat = GeneratorSpec(
        object_id=2,
        name="G",
        bus_name="N1",
        type_tag="thermal",
        pmax=50.0,
        heat_rate=9.0,
        heat_rate_profile=(9.0, 9.0, 9.0, 9.0),
        fuel_names=("Gas",),
    )
    assert build_generator_array((flat,), fuel)[0]["heat_rate"] == 9.0


def test_convexify_segments_merges_equal_and_decreasing_slopes() -> None:
    # Equal slopes merge exactly.
    assert _convexify_segments([10.0, 20.0, 30.0], [5.0, 5.0, 6.0]) == (
        [20.0, 30.0],
        [5.0, 6.0],
    )
    # Rounding noise (9.2356 vs 9.235583) merges too.
    bps, sl = _convexify_segments([12.82, 41.61, 42.0], [9.2356, 9.235583, 9.235583])
    assert bps == [42.0]
    assert sl[0] == pytest.approx(9.2356, rel=1e-5)
    # A genuine decrease is replaced by the length-weighted average.
    bps, sl = _convexify_segments([10.0, 30.0, 40.0], [8.0, 6.0, 9.0])
    assert bps == [30.0, 40.0]
    assert sl == pytest.approx([(8.0 * 10 + 6.0 * 20) / 30, 9.0])
    # Already convex: unchanged.
    assert _convexify_segments([1.0, 2.0], [1.0, 2.0]) == ([1.0, 2.0], [1.0, 2.0])
