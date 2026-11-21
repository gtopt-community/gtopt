"""AEMO ISP hydro: hm³ flow conversion, pumping, week-start levels, end value."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from plexos2gtopt.aemo_isp import (
    MWH_PER_HM3_PER_PF,
    IspCase,
    IspCaseOptions,
    _downstream_energy,
    build_dispatch_case,
)
from plexos2gtopt.tests.test_aemo_isp import _halfhourly_csv, _Xml


@pytest.fixture(name="phes_case")
def _phes_case(tmp_path: Path) -> Path:
    """Dam → Lower ← Upper pumped pair; volumes in ML (1000 m³)."""
    x = _Xml()
    node = x.obj("Node", "N1")
    df = x.data_file("N1", "Traces\\demand\\N1_dem.csv")
    x.data("Node", node, "Load", 0)
    x.data("Node", node, "Load", 0, tags=(df,))
    stores = {}
    for name, vmax, vmin, vini in (
        ("Dam", 100_000, 10_000, 0),  # empty placeholder → mid level
        ("Upper", 4_000, 0, 1_000),
        ("Lower", 4_000, 0, 3_000),
    ):
        sto = x.obj("Storage", name)
        x.data("Storage", sto, "Max Volume", vmax)
        x.data("Storage", sto, "Min Volume", vmin)
        x.data("Storage", sto, "Initial Volume", vini)
        stores[name] = sto
    x.data("Storage", stores["Dam"], "Max Spill", 120)
    for gname, head, eff, pump in (
        ("HYD", "Dam", 2.0, 0.0),
        ("PHES", "Upper", 1.5, 30.0),
    ):
        g = x.obj("Generator", gname)
        x.member("Generator", "Node", "Nodes", g, node)
        x.member("Generator", "Storage", "Head Storage", g, stores[head])
        x.member("Generator", "Storage", "Tail Storage", g, stores["Lower"])
        x.data("Generator", g, "Units", 1)
        x.data("Generator", g, "Max Capacity", 40)
        x.data("Generator", g, "Efficiency Incr", eff)
        if pump:
            x.data("Generator", g, "Pump Load", pump)
            x.data("Generator", g, "Pump Efficiency", 80)
    xml = tmp_path / "model" / "ISP Hydro.xml"
    xml.parent.mkdir(parents=True)
    x.write(xml)
    _halfhourly_csv(
        xml.parent / "Traces" / "demand" / "N1_dem.csv",
        date(2027, 1, 1),
        31,
        lambda k, h: 50.0,
    )
    return xml


def _case(
    xml: Path, *, hydro_extras: bool = True, hydro_water_value: float = 300.0
) -> IspCase:
    opts = IspCaseOptions(
        xml=xml,
        trace_roots=[xml.parent],
        start=date(2027, 1, 18),
        days=1,
        hydro_extras=hydro_extras,
        hydro_water_value=hydro_water_value,
    )
    return build_dispatch_case(opts)


def test_flow_conversion_and_spill(phes_case: Path) -> None:
    sys_ = _case(phes_case).planning["system"]
    rsv = {r["name"]: r for r in sys_["reservoir_array"]}
    for r in rsv.values():
        # hm³ volumes, m³/s flows: 3600 m³ per (m³/s · h) = 0.0036 hm³
        assert r["flow_conversion_rate"] == pytest.approx(0.0036)
    assert rsv["Dam"]["spillway_capacity"] == pytest.approx(120.0)
    assert "spillway_capacity" not in rsv["Upper"]


def test_week_start_levels(phes_case: Path) -> None:
    case = _case(phes_case)
    rsv = {r["name"]: r for r in case.planning["system"]["reservoir_array"]}
    # Initial Volume 0 ≤ Min Volume: an empty placeholder → middle of band
    assert rsv["Dam"]["eini"] == pytest.approx(55.0)
    assert rsv["Dam"]["efin"] == pytest.approx(55.0)
    # a real Initial Volume is kept as the representative level
    assert rsv["Upper"]["eini"] == pytest.approx(1.0)
    assert rsv["Lower"]["eini"] == pytest.approx(3.0)
    assert case.stats["hydro_level_rule"] == {"mid_level": 1, "initial_volume": 2}


def test_end_volume_water_value(phes_case: Path) -> None:
    sys_ = _case(phes_case, hydro_water_value=100.0).planning["system"]
    rsv = {r["name"]: r for r in sys_["reservoir_array"]}
    # efin_cost [$/hm³] = water value × Σ production factors down the cascade
    assert rsv["Dam"]["efin_cost"] == pytest.approx(100.0 * 2.0 * MWH_PER_HM3_PER_PF)
    assert rsv["Upper"]["efin_cost"] == pytest.approx(100.0 * 1.5 * MWH_PER_HM3_PER_PF)
    # no turbine below the tail pond: priced as one 1 MW/(m³/s) unit
    assert rsv["Lower"]["efin_cost"] == pytest.approx(100.0 * MWH_PER_HM3_PER_PF)


def test_pumped_storage_pump(phes_case: Path) -> None:
    sys_ = _case(phes_case).planning["system"]
    assert len(sys_["pump_array"]) == 1
    pump = sys_["pump_array"][0]
    assert pump["pump_factor"] == pytest.approx(1.5)
    assert pump["efficiency"] == pytest.approx(0.8)
    # water pumped = 30 MW × 0.8 / 1.5 MW/(m³/s) = 16 m³/s
    assert pump["capacity"] == pytest.approx(16.0)
    ww = {w["name"]: w for w in sys_["waterway_array"]}[pump["waterway"]]
    assert (ww["junction_a"], ww["junction_b"]) == ("Lower", "Upper")
    assert ww["fmax"] == pytest.approx(16.0)
    dem = {d["name"]: d for d in sys_["demand_array"]}[pump["demand"]]
    assert dem["bus"] == "N1"
    assert dem["capacity"] == pytest.approx(30.0)
    assert dem["fcost"] == 0.0  # flexible load, not curtailable demand
    assert "lmax" not in dem
    uids = [d["uid"] for d in sys_["demand_array"]]
    assert uids == list(range(1, len(uids) + 1))


def test_legacy_hydro_unchanged(phes_case: Path) -> None:
    sys_ = _case(phes_case, hydro_extras=False).planning["system"]
    assert "pump_array" not in sys_
    rsv = {r["name"]: r for r in sys_["reservoir_array"]}
    assert "flow_conversion_rate" not in rsv["Dam"]
    assert rsv["Dam"]["eini"] == pytest.approx(10.0)  # clamped Initial Volume
    assert rsv["Dam"]["efin_cost"] == pytest.approx(1.0e4)


def test_downstream_energy_parallel_vs_series() -> None:
    # A feeds B through a 2.0 unit or a parallel 3.0 unit; B drains
    # through a 1.0 unit.  Parallel units are alternatives (max), the
    # series cascade adds up: E(A) = 3 + 1, E(B) = 1, E(C) = 0.
    arcs = [("A", "B", 2.0), ("A", "B", 3.0), ("B", None, 1.0), ("C", "A", 0.0)]
    e = _downstream_energy(["A", "B", "C", "D"], arcs)
    assert e["A"] == pytest.approx(4.0 * MWH_PER_HM3_PER_PF)
    assert e["B"] == pytest.approx(1.0 * MWH_PER_HM3_PER_PF)
    assert e["C"] == pytest.approx(4.0 * MWH_PER_HM3_PER_PF)  # waterway to A
    assert e["D"] == 0.0
