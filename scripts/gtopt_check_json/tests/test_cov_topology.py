# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for bus-connectivity / unreferenced-element checks."""

from __future__ import annotations

from typing import Any

from gtopt_check_json import _topology_checks as tc


def _planning() -> dict[str, Any]:
    buses: list[dict[str, Any]] = [
        {"uid": 1, "name": "main1"},
        {"uid": 2, "name": "main2"},
        {"uid": 3, "name": "main3"},
        {"uid": 4},  # uid-only bus, joined to the main grid by uid
        {"uid": 5, "name": "isl"},  # island with elements
        {"uid": 6, "name": "isl2"},
    ]
    # 11 empty single-bus islands
    buses += [{"uid": 100 + i, "name": f"e{i}"} for i in range(11)]
    return {
        "system": {
            "bus_array": buses,
            "line_array": [
                {"uid": 1, "name": "l12", "bus_a": "main1", "bus_b": 2},
                {"uid": 2, "name": "l23", "bus_a": "main2", "bus_b": "main3"},
                {"uid": 3, "bus_a": 3, "bus_b": 4},
                {"uid": 4, "name": "dangling", "bus_a": "main1", "bus_b": "ghost"},
                {"uid": 5, "name": "isl_l", "bus_a": "isl", "bus_b": "isl2"},
                {"uid": 6, "name": "nobus"},
            ],
            "generator_array": [
                {"uid": 1, "name": "g_main", "bus": "main1"},
                {"uid": 2, "bus": 5},
                {"uid": 3, "name": "g_ghost", "bus": "ghost"},
                {"uid": 4, "name": "g_nobus"},
                {"uid": 5, "name": "g_prof"},
                {"uid": 6},
            ],
            "demand_array": [
                {"uid": 1, "name": "d_isl", "bus": "isl2"},
                {"uid": 2, "bus": "ghost"},
                {"uid": 3},
            ],
            "battery_array": [
                {"uid": 1, "bus": "isl"},
                {"uid": 2, "name": "bat_ghost", "bus": "ghost"},
                {"uid": 3, "name": "bat_nobus"},
            ],
            "generator_profile_array": [{"generator": "g_prof"}, {}],
            "converter_array": [{"generator": 99}, {}],
            "turbine_array": [
                {"generator": 98, "junction_a": "ja"},
                {"junction_b": 7},
            ],
            "reserve_provision_array": [{"generator": 97}, {}],
            "waterway_array": [{"junction_a": "w1"}],
            "flow_array": [{"junction": "f1"}, {}],
            "reservoir_array": [{"junction": "r1", "spill_junction": "s1"}, {}],
            "flow_right_array": [{"junction_b": "fr1"}],
            "junction_array": [
                {"uid": 1, "name": "w1"},
                {"uid": 2, "name": "f1"},
                {"uid": 3, "name": "r1"},
                {"uid": 4, "name": "s1"},
                {"uid": 5, "name": "fr1"},
                {"uid": 6, "name": "ja"},
                {"uid": 7},
                {"uid": 8, "name": "orphan"},
                {"uid": 9},
            ],
        }
    }


def test_islands() -> None:
    islands = tc.analyse_bus_islands(_planning())
    assert len(islands) == 12
    first = islands[0]
    assert sorted(first.buses) == ["isl", "isl2"]
    assert first.line_names == ["isl_l"]
    assert first.generator_names == ["2"]
    assert first.demand_names == ["d_isl"]
    assert first.battery_names == ["1"]
    assert first.has_elements
    assert not islands[1].has_elements


def test_bus_connectivity_findings() -> None:
    findings = tc.check_bus_connectivity(_planning())
    assert len(findings) == 2
    assert "1 island(s) with elements (2 bus(es))" in findings[0].message
    assert "11 empty island(s)" in findings[1].message
    assert "… and 1 more" in findings[1].message


def test_bus_connectivity_trivial_cases() -> None:
    assert not tc.check_bus_connectivity({"system": {"bus_array": [{"uid": 1}]}})
    connected = {
        "system": {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}],
            "line_array": [{"bus_a": "a", "bus_b": "b"}],
        }
    }
    assert not tc.check_bus_connectivity(connected)


def test_many_buses_in_islands_with_elements() -> None:
    buses = [
        {"uid": 1, "name": "m1"},
        {"uid": 2, "name": "m2"},
        {"uid": 3, "name": "m3"},
    ]
    gens = []
    for i in range(11):
        buses.append({"uid": 10 + i, "name": f"x{i}"})
        gens.append({"uid": i, "bus": f"x{i}"})
    planning = {
        "system": {
            "bus_array": buses,
            "line_array": [
                {"bus_a": "m1", "bus_b": "m2"},
                {"bus_a": "m2", "bus_b": "m3"},
            ],
            "generator_array": gens,
        }
    }
    findings = tc.check_bus_connectivity(planning)
    assert len(findings) == 1
    assert "… and 1 more" in findings[0].message


def test_unreferenced_elements() -> None:
    messages = [f.message for f in tc.check_unreferenced_elements(_planning())]
    assert any("Generator 'g_nobus'" in m for m in messages)
    assert any("Generator '6'" in m for m in messages)
    assert not any("g_prof" in m for m in messages)
    assert any("Junction 'orphan'" in m for m in messages)
    assert any("Junction '9'" in m for m in messages)
    assert not any("Junction '7'" in m for m in messages)
    assert len(messages) == 4
