"""Tests for the module-level helpers of :mod:`plp2gtopt.gtopt_writer`."""

from __future__ import annotations

from typing import Any

from plp2gtopt.gtopt_writer import (
    _collapse_orphan_drain_outflows,
    _find_by_uid_or_name,
)


def test_collapse_without_candidates() -> None:
    system: dict[str, Any] = {
        "junction_array": [{"uid": 1, "name": "j1", "drain": True}],
    }
    assert _collapse_orphan_drain_outflows(system) == 0
    assert _collapse_orphan_drain_outflows({}) == 0


def test_collapse_only_pure_junction_b_sinks() -> None:
    system: dict[str, Any] = {
        "junction_array": [
            {"uid": 1, "name": "res"},
            {"uid": 2, "name": "A_sink", "drain": True},  # ww_b by uid + turb_b
            {"uid": 3, "name": "B_ocean", "drain": True},  # also a flow target
            {"uid": 4, "name": "C_sink", "drain": True},  # used as junction_a
            {"uid": 5, "name": "D_sink", "drain": True},  # reservoir junction
            {"uid": 6, "name": "E_ocean", "drain": True},  # flow right
            {"uid": 7, "name": "F_sink", "drain": True},  # unreferenced
            {"uid": 8, "name": "G_sink", "drain": True},  # turbine junction_a
        ],
        "waterway_array": [
            {"name": "w1", "junction_a": "res", "junction_b": 2},
            {"name": "w2", "junction_a": "res", "junction_b": "B_ocean"},
            {"name": "w3", "junction_a": "C_sink", "junction_b": "res"},
            {"name": "w4", "junction_a": 1.5, "junction_b": None},
        ],
        "turbine_array": [
            {"name": "t1", "junction_a": "res", "junction_b": "A_sink"},
            {"name": "t2", "junction_a": "G_sink", "junction_b": "res"},
        ],
        "flow_array": [{"name": "f", "junction": "B_ocean"}],
        "reservoir_array": [{"name": "r", "junction": "D_sink"}],
        "flow_right_array": [{"name": "fr", "junction_a": "E_ocean"}],
    }
    assert _collapse_orphan_drain_outflows(system) == 1
    names = [j["name"] for j in system["junction_array"]]
    assert "A_sink" not in names and "B_ocean" in names and "F_sink" in names
    w1 = system["waterway_array"][0]
    assert "junction_b" not in w1
    assert "junction_b" not in system["turbine_array"][0]
    assert system["waterway_array"][1]["junction_b"] == "B_ocean"


def test_find_by_uid_or_name() -> None:
    arr = [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}]
    assert _find_by_uid_or_name(arr, 2) == arr[1]
    assert _find_by_uid_or_name(arr, 9) is None
    assert _find_by_uid_or_name(arr, "a") == arr[0]
    assert _find_by_uid_or_name(arr, "z") is None
    assert _find_by_uid_or_name(arr, 1.5) is None
