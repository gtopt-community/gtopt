# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""TopologyBuilder.build(): auto mode, max_nodes escalation, focus, hiding."""

import pytest

from gtopt_diagram import gtopt_diagram as gd
from gtopt_diagram._graph_model import (
    AUTO_BUS_THRESHOLD,
    AUTO_NONE_THRESHOLD,
)


def _big_system(n_buses: int, gens_per_bus: int = 1) -> dict:
    buses = [
        {"uid": i, "name": f"b{i}", "voltage": 500 if i % 2 else 110}
        for i in range(1, n_buses + 1)
    ]
    gens = [
        {"uid": 1000 * i + k, "name": f"g{i}_{k}", "bus": i, "pmax": 10}
        for i in range(1, n_buses + 1)
        for k in range(gens_per_bus)
    ]
    lines = [
        {"uid": i, "name": f"l{i}", "bus_a": i, "bus_b": i + 1}
        for i in range(1, n_buses)
    ]
    return {
        "system": {
            "name": "big",
            "bus_array": buses,
            "generator_array": gens,
            "line_array": lines,
        }
    }


def _builder(planning, **opts):
    return gd.TopologyBuilder(planning, opts=gd.FilterOptions(**opts))


def test_auto_mode_bus_tier():
    n = AUTO_NONE_THRESHOLD // 2 + 1  # buses + gens >= none threshold
    b = _builder(_big_system(n))
    b.build()
    assert b.eff_agg == "bus"
    assert b.auto_info[1] == "bus"


def test_auto_mode_type_tier_sets_voltage_threshold():
    n = AUTO_BUS_THRESHOLD // 2 + 1
    b = _builder(_big_system(n))
    b.build()
    assert b.eff_agg == "type"
    assert b.eff_vthresh > 0


def test_auto_mode_with_focus_bus():
    small = _builder(_big_system(3), focus_buses=["b1"])
    small.build()
    assert small.eff_agg == "none"
    big = _builder(_big_system(AUTO_BUS_THRESHOLD // 2 + 1), focus_buses=["b1"])
    big.build()
    assert big.eff_agg == "bus"


@pytest.mark.parametrize(
    ("start", "max_nodes", "expected"),
    [
        ("none", 1000, "none"),
        ("none", 15, "bus"),
        ("none", 8, "global"),
        ("type", 1000, "type"),
    ],
)
def test_max_nodes_escalation(start, max_nodes, expected):
    b = _builder(_big_system(5, gens_per_bus=3), aggregate=start, max_nodes=max_nodes)
    b.build()
    assert b.eff_agg == expected


def test_hide_isolated_and_hydro_subsystem_edge_pruning():
    planning = _big_system(3)
    planning["system"]["bus_array"].append({"uid": 99, "name": "alone"})
    b = _builder(planning, aggregate="none", hide_isolated=True)
    model = b.build()
    assert "alone" not in " ".join(n.label for n in model.nodes)


def test_focus_set_unknown_bus_and_unresolvable_lines():
    planning = _big_system(4)
    planning["system"]["line_array"].append({"uid": 50, "bus_a": 1, "bus_b": 77})
    b = _builder(planning, aggregate="none", focus_buses=["b1", "nope"], focus_hops=1)
    model = b.build()
    buses = sorted(n.label.split("\n")[0] for n in model.nodes if n.kind == "bus")
    assert buses == ["b1:1", "b2:2"]
