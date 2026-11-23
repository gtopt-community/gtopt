# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""TopologyBuilder electrical-side edge cases (aggregation, filters, lines)."""

import pytest

from gtopt_diagram._graph_model import FilterOptions
from gtopt_diagram.gtopt_diagram import TopologyBuilder


def _sys(**extra):
    system = {
        "name": "net",
        "bus_array": [
            {"uid": 1, "name": "b1", "voltage": 220},
            {"uid": 2, "name": "b2", "voltage": 220},
            {"uid": 3, "name": "b3", "voltage": 66},
            {"uid": 4, "name": "b4"},
        ],
        "generator_array": [
            {"uid": 1, "name": "COLBUN_1", "bus": 1, "pmax": 100, "type": "hydro"},
            {"uid": 2, "name": "COLBUN_2", "bus": 1, "pmax": 120, "type": "hydro"},
            {"uid": 3, "name": "PV_A", "bus": 2, "pmax": 30, "type": "solar"},
            {"uid": 4, "name": "PV_B", "bus": 2, "pmax": 40, "type": "solar"},
            {"uid": 5, "name": "TG", "bus": 3, "capacity": 10},
            {"uid": 6, "name": "lost", "bus": 99, "pmax": 1},
        ],
        "demand_array": [{"uid": 1, "name": "d1", "bus": 3, "lmax": 50}],
        "line_array": [
            {"uid": 1, "name": "l12", "bus_a": 1, "bus_b": 2, "tmax_ab": 300},
            {"uid": 2, "name": "l12b", "bus_a": 1, "bus_b": 2, "tmax_ba": 200},
            {"uid": 3, "name": "l23", "bus_a": 2, "bus_b": 3},
            {"uid": 4, "name": "lself", "bus_a": 4, "bus_b": 4},
            {"uid": 5, "name": "lbad", "bus_a": 1, "bus_b": 77},
        ],
    }
    system.update(extra)
    return {"system": system}


def _build(planning, **opts):
    opts.setdefault("draw_logical_elements", False)
    builder = TopologyBuilder(planning, opts=FilterOptions(**opts))
    return builder, builder.build()


def _ids(model):
    return {n.node_id for n in model.nodes}


def test_common_stem_variants():
    stem = TopologyBuilder._common_stem  # pylint: disable=protected-access
    assert stem([]) == ""
    assert stem(["solo"]) == "solo"
    assert stem(["abc", "xyz"]) == "abc"  # no common prefix
    assert stem(["COLBUN_1", "COLBUN_2"]) == "COLBUN"
    assert stem(["Plant1A", "Plant1B"]) == "Plant1"
    assert stem(["Plant12A", "Plant13A"]) == "Plant1"
    assert stem(["ab1", "ab2"]) == "ab1"  # stem too short → first name


@pytest.mark.parametrize("agg", ["bus", "type", "global"])
def test_aggregations_with_top_gens(agg):
    _, model = _build(_sys(), aggregate=agg, top_gens=5)
    labels = [n.label for n in model.nodes]
    assert any("2 units" in lbl for lbl in labels)
    # unresolvable bus generator never becomes an aggregate node
    assert not any("lost" in lbl and "units" in lbl for lbl in labels)


def test_top_gens_one_renders_individual_units():
    _, model = _build(_sys(), aggregate="bus", top_gens=1)
    labels = " ".join(n.label for n in model.nodes)
    assert "COLBUN_2" in labels  # biggest unit kept
    assert "COLBUN_1" not in labels


def test_filter_types_keeps_only_solar():
    _, model = _build(_sys(), aggregate="none", filter_types=["solar"])
    labels = " ".join(n.label for n in model.nodes)
    assert "PV_A" in labels
    assert "COLBUN" not in labels


def test_voltage_threshold_lumps_and_dedups_lines():
    _, model = _build(_sys(), aggregate="none", voltage_threshold=100)
    ids = _ids(model)
    assert not any(i.startswith("bus_3") for i in ids)  # 66 kV bus lumped
    line_edges = [e for e in model.edges if not e.directed]
    pairs = [tuple(sorted((e.src, e.dst))) for e in line_edges]
    assert len(pairs) == len(set(pairs))  # parallel l12/l12b deduplicated


def test_lines_labels_compact_and_tmax_fallback():
    _, model = _build(_sys(), aggregate="none")
    labels = [e.label for e in model.edges if not e.directed]
    assert any("l12b" in lbl and "200 MW" in lbl for lbl in labels)
    assert any(lbl.startswith("l23") for lbl in labels)
    _, compact = _build(_sys(), aggregate="none", compact=True)
    assert "l12:1" in [e.label.split("\n")[0] for e in compact.edges if not e.directed]


def test_focus_bus_restricts_buses():
    _, model = _build(_sys(), aggregate="none", focus_buses=["b1"], focus_hops=0)
    bus_nodes = [n for n in model.nodes if n.kind == "bus"]
    assert [n.label.split("\n")[0] for n in bus_nodes] == ["b1:1"]


def test_duplicate_bus_entries_render_once():
    planning = _sys()
    planning["system"]["bus_array"].append({"uid": 1, "name": "b1"})
    _, model = _build(planning, aggregate="none")
    assert sum(1 for n in model.nodes if n.kind == "bus") == 4


def test_battery_capacity_fallback_and_converters():
    planning = _sys(
        battery_array=[
            {
                "uid": 1,
                "name": "bat1",
                "capacity": 40,
                "bus": 1,
                "input_efficiency": 0.9,
                "output_efficiency": 0.95,
            },
        ],
        converter_array=[
            {
                "uid": 1,
                "name": "cv1",
                "battery": 1,
                "generator": 3,
                "demand": 1,
                "capacity": 20,
            },
        ],
    )
    _, model = _build(planning, aggregate="none")
    bat = next(n for n in model.nodes if n.kind == "battery")
    assert "40 MWh" in bat.label
    assert "η=0.9/" in bat.label
    labels = {e.label for e in model.edges}
    assert {"stored\nenergy", "discharge", "charge"} <= labels


@pytest.mark.parametrize("agg", ["none", "bus", "type", "global"])
def test_generator_profiles_attach_to_aggregates(agg):
    planning = _sys(
        generator_profile_array=[
            {"uid": 1, "name": "gp1", "generator": 1, "profile": "solar_file"},
            {"uid": 2, "name": "gp5", "generator": 5, "profile": [0.5]},
            {"uid": 3, "name": "gpx", "generator": 999, "profile": 1.0},
        ],
        demand_profile_array=[
            {"uid": 1, "name": "dp1", "demand": 1, "profile": [1.0]},
            {"uid": 2, "name": "dpx", "demand": 42, "profile": "f"},
        ],
    )
    _, model = _build(planning, aggregate=agg, draw_logical_elements=True)
    profile_edges = [e for e in model.edges if e.label == "profile"]
    # gp1, gp5 → generator nodes; dp1 → demand; unknown refs dropped
    assert len(profile_edges) == 3


def test_compact_profiles():
    planning = _sys(
        generator_profile_array=[{"uid": 1, "name": "gp1", "generator": 1}],
        demand_profile_array=[{"uid": 1, "name": "dp1", "demand": 1}],
    )
    _, model = _build(
        planning, aggregate="none", draw_logical_elements=True, compact=True
    )
    assert {"gp1:1", "dp1:1"} <= {n.label for n in model.nodes}
