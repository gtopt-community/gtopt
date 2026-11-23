"""Tests for the line-less bus warning of plp2gtopt (stale bus islands)."""

from typing import Any, Dict

from plp2gtopt.gtopt_writer import _find_lineless_bus_elements


def _planning(**system: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "bus_array": [
            {"uid": 1, "name": "A220"},
            {"uid": 2, "name": "B220"},
            {"uid": 3, "name": "Stale154"},
        ],
        "line_array": [{"uid": 1, "name": "A-B", "bus_a": 1, "bus_b": 2}],
        "generator_array": [
            {"uid": 1, "name": "G_OK", "bus": 1},
            {"uid": 2, "name": "G_STALE", "bus": 3},
        ],
        "demand_array": [{"uid": 1, "name": "D_OK", "bus": 2}],
    }
    base.update(system)
    return {"options": {}, "system": base}


def test_flags_generator_on_lineless_bus():
    warns = _find_lineless_bus_elements(_planning())
    assert len(warns) == 1
    assert "G_STALE" in warns[0]
    assert "Stale154" in warns[0]


def test_scalar_inactive_line_does_not_connect():
    plan = _planning(
        line_array=[
            {"uid": 1, "name": "A-B", "bus_a": 1, "bus_b": 2},
            {"uid": 2, "name": "A-S", "bus_a": 1, "bus_b": 3, "active": 0},
        ]
    )
    warns = _find_lineless_bus_elements(plan)
    assert [w for w in warns if "G_STALE" in w]


def test_scheduled_active_line_counts_as_connected():
    plan = _planning(
        line_array=[
            {"uid": 1, "name": "A-B", "bus_a": 1, "bus_b": 2},
            {"uid": 2, "name": "A-S", "bus_a": 1, "bus_b": 3, "active": "active"},
        ]
    )
    assert not _find_lineless_bus_elements(plan)


def test_demand_and_battery_checked():
    plan = _planning(
        generator_array=[],
        demand_array=[{"uid": 1, "name": "D_STALE", "bus": 3}],
        battery_array=[{"uid": 1, "name": "B_STALE", "bus": 3}],
    )
    warns = _find_lineless_bus_elements(plan)
    assert len(warns) == 2
    assert any(w.startswith("demand 'D_STALE'") for w in warns)
    assert any(w.startswith("battery 'B_STALE'") for w in warns)


def test_single_bus_cases_skipped():
    assert not _find_lineless_bus_elements(_planning(line_array=[]))
    plan = _planning()
    plan["options"] = {"model_options": {"use_single_bus": True}}
    assert not _find_lineless_bus_elements(plan)
