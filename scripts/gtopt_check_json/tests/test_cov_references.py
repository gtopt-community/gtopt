# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``check_element_references`` on every element kind."""

from __future__ import annotations

from gtopt_check_json._reference_checks import check_element_references


def test_dangling_references_for_every_kind() -> None:
    planning = {
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [{"uid": 1, "name": "g1", "bus": "b1"}],
            "demand_array": [{"uid": 1, "name": "d1", "bus": "b1"}],
            "battery_array": [
                {"uid": 1, "name": "bat1", "bus": "b1", "source_generator": "g1"},
                {"uid": 2, "name": "bat2", "bus": "nobus", "source_generator": "nog"},
                {"uid": 3},
            ],
            "converter_array": [
                {"uid": 1, "battery": "nob", "generator": "nog", "demand": "nod"}
            ],
            "reserve_zone_array": [{"uid": 1, "name": "rz"}],
            "reserve_provision_array": [
                {"uid": 1, "generator": "bat1_gen", "reserve_zone": "rz"},
                {"uid": 2, "generator": "nog", "reserve_zone": "norz"},
            ],
            "generator_profile_array": [{"uid": 1, "generator": "nog"}],
            "demand_profile_array": [{"uid": 1, "demand": "nod"}],
            "junction_array": [{"uid": 1, "name": "j1"}],
            "waterway_array": [
                {"uid": 1, "name": "w1", "junction_a": "j1", "junction_b": "noj"}
            ],
            "turbine_array": [{"uid": 1, "waterway": "now", "generator": "nog"}],
            "flow_array": [{"uid": 1, "junction": "noj"}],
            "reservoir_array": [{"uid": 1, "name": "r1", "junction": "noj"}],
            "reservoir_seepage_array": [
                {"uid": 1, "waterway": "now", "reservoir": "nor"}
            ],
            "reservoir_discharge_limit_array": [
                {"uid": 1, "waterway": "w1", "reservoir": "nor"}
            ],
        }
    }
    msgs = [f.message for f in check_element_references(planning)]
    joined = "\n".join(msgs)
    for expected in (
        "'nobus'",
        "source_generator",
        "Converter",
        "'nob'",
        "'nod'",
        "'norz'",
        "GeneratorProfile",
        "DemandProfile",
        "Turbine",
        "Waterway",
        "Flow",
        "Reservoir",
        "ReservoirSeepage",
        "ReservoirDischargeLimit",
    ):
        assert expected in joined, expected
    assert "bat1_gen" not in joined  # synthetic battery generator is valid
