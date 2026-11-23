# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of the small :mod:`plexos2gtopt.parsers` helpers.

Covers topology maps, node / fuel extraction edge cases, the CO2 membership
walk, the weekly offtake CSV reader, Min Offtake folding, primary-energy
classification and the Load Point heat-rate segmentation.
"""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def test_bess_provision_direction_by_name() -> None:
    """Generic kinds resolve the direction from the constraint name."""
    assert P._bess_provision_direction("X", "dn") == "dn"
    assert P._bess_provision_direction("A_LW_B", "rp") == "dn"
    assert P._bess_provision_direction("A_RS_B", "rp") == "up"
    assert P._bess_provision_direction("PLAIN", "rp") is None


def test_battery_reserve_memberships_skips_dangling() -> None:
    """Memberships pointing at unknown objects are ignored."""
    kit = DbKit()
    rsv = kit.obj("Reserve", "R1")
    bat = kit.obj("Battery", "B1")
    kit.sysprop("Reserve", rsv, "Type", 3)
    kit.mem("Reserve", "Battery", rsv, bat, coll_name="Batteries")
    kit.mem("Reserve", "Battery", rsv, 9999, coll_name="Batteries")
    assert P._battery_reserve_memberships(kit.db) == {"B1": {"R1": 3}}


def test_topology_maps_edge_cases(caplog: pytest.LogCaptureFixture) -> None:
    """Generator / battery bus maps tolerate splits and dangling children."""
    kit = DbKit()
    n1 = kit.obj("Node", "n1")
    n2 = kit.obj("Node", "n2")
    g1 = kit.obj("Generator", "G1")
    g2 = kit.obj("Generator", "G2")
    b1 = kit.obj("Battery", "B1")
    b2 = kit.obj("Battery", "B2")
    f1 = kit.obj("Fuel", "Gas_A")
    kit.mem("Generator", "Node", g1, n1, coll_name="Nodes")
    kit.mem("Generator", "Node", g1, n2, coll_name="Nodes")
    kit.mem("Generator", "Node", g2, 9999, coll_name="Nodes")
    kit.mem("Battery", "Node", b1, n1, coll_name="Nodes")
    kit.mem("Battery", "Node", b2, 9999, coll_name="Nodes")
    kit.mem("Generator", "Fuel", g1, f1, coll_name="Fuels")
    kit.mem("Generator", "Fuel", g2, 9999, coll_name="Fuels")
    with caplog.at_level(logging.DEBUG, logger=P.logger.name):
        assert P._gen_bus_map(kit.db) == {g1: "n1"}
    assert "split across multiple nodes" in caplog.text
    assert P._battery_bus_map(kit.db) == {b1: "n1"}
    assert P._gen_fuel_map(kit.db) == {g1: ("Gas_A",)}


def test_topology_maps_empty_children_lists() -> None:
    """Parents with an empty child list are skipped (defensive path)."""
    kit = DbKit()
    kit.coll("Generator", "Node", "Nodes")
    kit.coll("Battery", "Node", "Nodes")

    class _Db(type(kit.db)):  # type: ignore[misc]
        def parent_to_children(self, collection_id: int) -> dict[int, list[int]]:
            del collection_id
            return {1: []}

    db = _Db(path=kit.db.path)
    db.classes_by_name = kit.db.classes_by_name
    db.classes_by_id = kit.db.classes_by_id
    db.collections = kit.db.collections
    assert not P._gen_bus_map(db)
    assert not P._battery_bus_map(db)


def test_extract_nodes_region_zone_and_dangling() -> None:
    """Region / Zone tags and dangling memberships."""
    kit = DbKit()
    n1 = kit.obj("Node", "n1")
    n2 = kit.obj("Node", "n2")
    reg = kit.obj("Region", "R")
    zone = kit.obj("Zone", "Z")
    kit.mem("Node", "Region", n1, reg, coll_name="Region")
    kit.mem("Node", "Region", n2, 9999, coll_name="Region")
    kit.mem("Node", "Zone", n1, zone, coll_name="Zone")
    kit.mem("Node", "Zone", n2, 9999, coll_name="Zone")
    kit.sysprop("Node", n1, "Voltage", 220.0)
    nodes = {n.name: n for n in P.extract_nodes(kit.db)}
    assert nodes["n1"].region == "R"
    assert nodes["n1"].zone == "Z"
    assert nodes["n1"].voltage == pytest.approx(220.0)
    assert nodes["n2"].region is None and nodes["n2"].zone is None
    assert nodes["n2"].voltage is None


def test_is_co2_emission_empty() -> None:
    """An empty emission name is never CO2."""
    assert P._is_co2_emission("") is False
    assert P._is_co2_emission("CO2") is True


def test_co2_membership_rates_edges() -> None:
    """Missing prop / rows, sentinel and zero rates are skipped."""
    kit = DbKit()
    kit.coll("Emission", "Fuel", "Fuels")
    assert not P._extract_fuel_co2_membership_rates(kit.db)

    kit = DbKit()
    e1 = kit.obj("Emission", "CO2")
    e2 = kit.obj("Emission", "CO2 upstream")
    e3 = kit.obj("Emission", "NOx")
    fuels = [kit.obj("Fuel", f"F{i}") for i in range(5)]
    cid = kit.coll("Emission", "Fuel", "Fuels")
    pid = kit.prop(cid, "Production Rate")
    kit.mem("Emission", "Fuel", e1, fuels[0], coll_name="Fuels")  # no rows
    m = kit.mem("Emission", "Fuel", e1, fuels[1], coll_name="Fuels")
    kit.data(m, pid, 1e30)  # sentinel
    m = kit.mem("Emission", "Fuel", e1, fuels[2], coll_name="Fuels")
    kit.data(m, pid, 0.0)  # zero
    m = kit.mem("Emission", "Fuel", e1, fuels[3], coll_name="Fuels")
    kit.data(m, pid, 56000.0)
    m = kit.mem("Emission", "Fuel", e2, fuels[3], coll_name="Fuels")
    kit.data(m, pid, 5000.0)
    m = kit.mem("Emission", "Fuel", e3, fuels[4], coll_name="Fuels")
    kit.data(m, pid, 1.0)
    kit.mem("Emission", "Fuel", 9999, fuels[4], coll_name="Fuels")
    out = P._extract_fuel_co2_membership_rates(kit.db)
    assert list(out) == [fuels[3]]
    assert out[fuels[3]] == pytest.approx((56.0, 5.0))


def test_max_offtake_week_csv_edges(tmp_path: Path) -> None:
    """BOM header, blank names, bad dates and non-overlapping weeks."""
    body = (
        "﻿NAME,YEAR,MONTH,DAY,PERIOD,VALUE\n"
        ",2026,4,16,1,9\n"
        "Gas_A,2026,13,40,1,9\n"
        "Gas_A,2026,4,1,1,7\n"
        "Gas_A,2026,4,22,1,7\n"
    )
    b = bundle(tmp_path, {"Fuel_MaxOfftakeWeek.csv": body}, n_days=7)
    out = P._extract_fuel_max_offtake_week(b, datetime(2026, 4, 22))
    # Week of 04-01 does not overlap; 04-22 fully covers the 7-day horizon.
    assert out == {"Gas_A": pytest.approx(7000.0)}


def test_min_offtake_bare_and_multi_window() -> None:
    """Bare per-interval value plus a windowed one accumulate."""
    kit = DbKit()
    fuel = kit.obj("Fuel", "Gas_A")
    kit.sysprop("Fuel", fuel, "Min Offtake", 5.0)
    kit.sysprop("Fuel", fuel, "Min Offtake Day", 2.0)
    kit.sysprop("Fuel", fuel, "Min Offtake Penalty", 50.0)
    total, cost = P._extract_fuel_min_offtake_horizon(kit.db, fuel, "Gas_A", 48.0)
    assert total == pytest.approx(5.0 + 2.0 * 2.0)
    assert cost == pytest.approx(50.0)


def test_extract_fuels_direct_props_and_heat_rate_synonym(tmp_path: Path) -> None:
    """Direct CO2 props stop at the first non-zero; ``Heat Rate`` synonym."""
    kit = DbKit()
    fuel = kit.obj("Fuel", "Gas_A")
    kit.sysprop("Fuel", fuel, "CO2 Production Rate", 0.05)
    kit.sysprop("Fuel", fuel, "Upstream Production Rate", 0.01)
    kit.sysprop("Fuel", fuel, "Heat Rate", 3.5)
    (spec,) = P.extract_fuels(kit.db, bundle(tmp_path))
    assert spec.co2_rate == pytest.approx(0.05)
    assert spec.co2_upstream_rate == pytest.approx(0.01)
    assert spec.heat_content == pytest.approx(3.5)


def test_primary_energy_generic_category() -> None:
    """A generic thermal category wins only after fuel lookups fail."""
    assert (
        P.primary_energy_of_generator("XYZ", "Termicas Ficticias", ("Unknown",))
        == "thermal"
    )


def test_recover_csv_thermals_skips_nonpositive_heat_rate() -> None:
    """CSV-only names with a non-positive heat rate are not recovered."""
    assert not P._recover_csv_only_thermals([], {"G_GNL_X": [0.0], "H": []}, {})


def _load_point_db(points: list[float], incrs: list[float]) -> tuple[DbKit, int]:
    kit = DbKit()
    gen = kit.obj("Generator", "G")
    for band, val in enumerate(points, start=1):
        kit.sysprop("Generator", gen, "Load Point", val, band=band)
    for band, val in enumerate(incrs, start=1):
        kit.sysprop("Generator", gen, "Heat Rate Incr", val, band=band)
    return kit, gen


def test_load_point_segments_non_increasing_break() -> None:
    """A non-increasing load point ends the walk; slope extends to pmax."""
    kit, gen = _load_point_db([100.0, 150.0, 120.0], [8.0, 9.0, 10.0])
    bps, slopes = P._load_point_heat_rate_segments(kit.db, gen, 200.0)
    assert bps[0] == pytest.approx(100.0)
    assert bps[-1] == pytest.approx(200.0)
    assert slopes[0] == pytest.approx(8.0)
    assert slopes[-1] == pytest.approx(9.0)


def test_load_point_segments_single_segment_is_empty() -> None:
    """A first load point above pmax collapses to a single segment."""
    kit, gen = _load_point_db([300.0, 400.0], [8.0, 9.0])
    assert P._load_point_heat_rate_segments(kit.db, gen, 100.0) == ((), ())
