# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`gtopt_shared.hydro_epf`."""

from __future__ import annotations

import logging

import pytest

from gtopt_shared import hydro_epf as he


def _cascade() -> dict:
    """R1 (J1) --T1(pf 2)--> J2 --T2(pf 3)--> sea, plus a spill J1 -> J2."""
    return {
        "junction_array": [{"name": "J1"}, {"name": "J2"}, {"uid": 9}],
        "reservoir_array": [
            {"name": "R1", "junction": "J1"},
            {"name": "Rx", "junction": "missing"},
        ],
        "waterway_array": [
            {"name": "spill", "junction_a": "J1", "junction_b": "J2"},
            {"name": "orphan", "junction_a": None, "junction_b": "J2"},
        ],
        "turbine_array": [
            {
                "name": "T1",
                "junction_a": "J1",
                "junction_b": "J2",
                "production_factor": 2.0,
            },
            {"name": "T2", "junction_a": "J2", "production_factor": 3.0},
        ],
    }


def test_epf_with_prebuilt_graph_and_missing_anchor():
    system = _cascade()
    g = he.build_hydro_graph(system)
    # T2 has no junction_b → skipped in the EPF graph; spill + T1 both
    # reach J2 but J2 is visited once.
    out = he.epf_per_reservoir(system, graph=g)
    assert out["R1"] == pytest.approx(2.0)
    assert out["Rx"] == 0.0


def test_chain_modes_and_errors():
    system = _cascade()
    assert he.chain_epf_per_reservoir(system, mode="segment") == he.epf_per_reservoir(
        system
    )
    with pytest.raises(ValueError, match="unknown mode"):
        he.chain_epf_per_reservoir(system, mode="bogus")
    out = he.chain_epf_per_reservoir(system, with_path=True)
    assert out["R1"] == (pytest.approx(5.0), ["T1", "T2"])
    assert out["Rx"] == (0.0, [])
    mp = he.chain_epf_per_reservoir(system, mode="max_path", stop_at_reservoirs=True)
    assert mp["R1"] == pytest.approx(5.0)


def test_default_resolver_and_mean_curve():
    assert he._default_resolver("Turbine", {"pf": "ref"}, "pf", 1.5) == 1.5
    assert he._default_resolver("Turbine", {"pf": 2.0}, "pf", 1.5) == 2.0
    assert he._mean_pf_curve([], 0.0, 1.0) == 0.0
    seg = [{"volume": 0.0, "slope": 0.0, "constant": 4.0}]
    # vmax < vmin is clamped to vmin.
    assert he._mean_pf_curve(seg, 5.0, 1.0) == pytest.approx(4.0)


def test_chain_graph_mean_pf_and_inactive_waterway():
    system = {
        "junction_array": [{"name": "A"}, {"name": "B"}],
        "reservoir_array": [{"name": "R", "junction": "A", "uid": 7}],
        "reservoir_production_factor_array": [
            {"turbine": "T", "reservoir": 99, "mean_production_factor": 1.25},
            {"turbine": "U", "reservoir": 99},
        ],
        "waterway_array": [
            {"name": "pen", "junction_a": "A", "junction_b": "B", "active": 0},
            {"name": "skip"},
        ],
        "turbine_array": [
            {"name": "T", "waterway": "pen"},
            {"name": "U", "junction_a": "B", "production_factor": 2.0, "active": 0},
        ],
    }
    g = he.build_chain_graph(system)
    pen = next(d for _u, _v, d in g.edges(data=True) if d["name"] == "pen")
    assert pen["production_factor"] == 0.0
    assert pen["active"] is False
    u_arc = next(d for _u, _v, d in g.edges(data=True) if d["name"] == "U")
    assert u_arc["production_factor"] == 0.0
    # Active waterway: mean PF is used for the turbine.
    system["waterway_array"][0]["active"] = 1
    g2 = he.build_chain_graph(system)
    pen2 = next(d for _u, _v, d in g2.edges(data=True) if d["name"] == "pen")
    assert pen2["production_factor"] == pytest.approx(1.25)


def _cyclic() -> dict:
    """A --T1(1)--> B --T2(2)--> A (pump-back style loop) and B --T3(4)--> sea."""
    return {
        "junction_array": [{"name": "A"}, {"name": "B"}],
        "reservoir_array": [{"name": "R", "junction": "A"}],
        "turbine_array": [
            {
                "name": "T1",
                "junction_a": "A",
                "junction_b": "B",
                "production_factor": 1.0,
            },
            {
                "name": "T2",
                "junction_a": "B",
                "junction_b": "A",
                "production_factor": 2.0,
            },
            {"name": "T3", "junction_a": "B", "production_factor": 4.0},
            # A second, weaker arc A -> B: does not beat T1.
            {
                "name": "T4",
                "junction_a": "A",
                "junction_b": "B",
                "production_factor": 0.5,
            },
        ],
    }


def test_turbined_path_cycle_is_ignored(caplog):
    with caplog.at_level(logging.WARNING, logger=he.__name__):
        out = he.chain_epf_per_reservoir(_cyclic(), with_path=True)
    assert out["R"] == (pytest.approx(5.0), ["T1", "T3"])
    assert "cycle" in caplog.text


def test_max_path_cycle_fallback(caplog):
    with caplog.at_level(logging.WARNING, logger=he.__name__):
        out = he.chain_epf_per_reservoir(_cyclic(), mode="max_path", with_path=True)
    assert out["R"][0] == pytest.approx(5.0)
    assert out["R"][1] == ["T1", "T3"]
    assert "has a cycle" in caplog.text


def test_max_path_dag_parallel_arcs_keep_best():
    system = _cyclic()
    system["turbine_array"] = [t for t in system["turbine_array"] if t["name"] != "T2"]
    system["waterway_array"] = [{"name": "w0", "junction_a": "B", "junction_b": "C"}]
    out = he.chain_epf_per_reservoir(system, mode="max_path", with_path=True)
    assert out["R"] == (pytest.approx(5.0), ["T1", "T3"])


def test_epf_diamond_counts_shared_node_once():
    system = {
        "junction_array": [{"name": n} for n in ("A", "B", "C", "D")],
        "reservoir_array": [{"name": "R", "junction": "A"}],
        "waterway_array": [
            {"name": "ab", "junction_a": "A", "junction_b": "B"},
            {"name": "ac", "junction_a": "A", "junction_b": "C"},
            {"name": "cd", "junction_a": "C", "junction_b": "D"},
        ],
        "turbine_array": [
            {
                "name": "T",
                "junction_a": "B",
                "junction_b": "D",
                "production_factor": 1.0,
            },
        ],
    }
    # D is reached from B (via T) and from C; it is visited only once.
    assert he.epf_per_reservoir(system)["R"] == pytest.approx(1.0)
