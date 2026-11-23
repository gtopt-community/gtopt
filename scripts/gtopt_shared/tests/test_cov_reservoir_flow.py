# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`gtopt_shared.reservoir_flow` helpers."""

from __future__ import annotations

import builtins
import logging
import math

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gtopt_shared import reservoir_flow as rf


# ---------------------------------------------------------------------------
# Scalar / schedule helpers
# ---------------------------------------------------------------------------


def test_is_absent_bound_variants():
    assert rf._is_absent_bound(None)
    assert rf._is_absent_bound(True)
    assert rf._is_absent_bound("fmax")
    assert rf._is_absent_bound(math.inf)
    assert rf._is_absent_bound(1e30)
    assert not rf._is_absent_bound(5)


def test_numeric_and_min_positive_caps():
    assert rf._numeric_cap([[1.0, 4.0], 2.0]) == 4.0
    assert rf._numeric_cap(-3.0) is None
    assert rf._numeric_cap(1e31) is None
    assert rf._min_positive_cap(True) is None
    assert rf._min_positive_cap(0.0) is None
    assert rf._min_positive_cap(2) == 2.0
    assert rf._min_positive_cap([[3.0, 0.0], [2.0, "x", True], math.nan]) == 2.0
    assert rf._min_positive_cap([0.0]) is None
    assert rf._min_positive_cap("pf") is None


def test_peak_from_schedule_variants():
    assert rf._peak_from_schedule(True) is None
    assert rf._peak_from_schedule(math.inf) is None
    assert rf._peak_from_schedule("ref") is None
    assert rf._peak_from_schedule((1.0, [5.0, math.nan, False])) == 5.0
    assert rf._peak_from_schedule({"a": 1}) is None


def test_junction_key_and_index_maps():
    assert rf._junction_key(None, {}, {}) is None
    assert rf._junction_key("j", {"j": "J"}, {}) == "J"
    assert rf._junction_key("raw", {}, {}) == "raw"
    assert rf._junction_key(3, {}, {3: "J3"}) == "J3"
    assert rf._junction_key(1.5, {}, {}) is None
    by_name, by_uid = rf._build_index_maps([{"name": 1, "uid": True}, {"uid": 2}])
    assert not by_name
    assert set(by_uid) == {2}


def test_turbine_key_and_junctions():
    assert rf._turbine_maxflow_key({"uid": 7}) == "uid:7"
    ww = {"name": "w", "uid": 4, "junction_a": "A", "junction_b": "B"}
    by_name, by_uid = {"w": ww}, {4: ww}
    assert rf._turbine_junctions({"junction_a": "X"}, by_name, by_uid) == ("X", None)
    assert rf._turbine_junctions({"waterway": "w"}, by_name, by_uid) == ("A", "B")
    assert rf._turbine_junctions({"waterway": 4}, by_name, by_uid) == ("A", "B")
    assert rf._turbine_junctions({"waterway": 9}, by_name, by_uid) == (None, None)
    assert rf._turbine_junctions({}, by_name, by_uid) == (None, None)


def test_pf_at_volume_skips_bad_segments():
    segs = [
        {"slope": True, "constant": 1.0},
        {"slope": 0.0, "constant": math.inf},
        {"slope": 0.1, "constant": 2.0, "volume": 10.0},
    ]
    assert rf._pf_at_volume(segs, 20.0) == pytest.approx(3.0)
    assert rf._pf_at_volume(segs[:1], 1.0) is None


def test_ref_helpers():
    assert rf._lookup_ref(1.5, {}, {}) is None
    assert rf._lookup_ref(2, {}, {2: "x"}) == "x"
    assert rf._ref_keys(5) == ["5"]
    assert not rf._ref_keys(None)


# ---------------------------------------------------------------------------
# Production factors / turbine max-flow
# ---------------------------------------------------------------------------


def test_resolve_min_production_factors_paths():
    system = {
        "reservoir_array": [
            {"name": "R1", "uid": 1, "emin": "x", "capacity": 100.0},
            {"name": "R2", "uid": 2, "emin": 0.0, "emax": "ref"},
        ],
        "reservoir_production_factor_array": [
            # emin non-numeric → 0; emax falls back to capacity.
            {
                "reservoir": "R1",
                "turbine": "T1",
                "segments": [{"volume": 0.0, "slope": 0.01, "constant": 1.0}],
            },
            # Second element for the same turbine: smaller PF wins.
            {
                "reservoir": 1,
                "turbine": "T1",
                "segments": [{"volume": 0.0, "slope": 0.0, "constant": 0.5}],
            },
            # Neither emax nor capacity numeric → emax = emin.
            {
                "reservoir": "R2",
                "turbine": 9,
                "segments": [{"volume": 0.0, "slope": 1.0, "constant": 2.0}],
            },
            # Unusable segments + no mean → skipped.
            {"reservoir": "R1", "turbine": "T3", "segments": [{"slope": "x"}]},
            # Negative PF falls back to mean.
            {
                "reservoir": "R1",
                "turbine": "T4",
                "segments": [{"volume": 0.0, "slope": 0.0, "constant": -1.0}],
                "mean_production_factor": 1.7,
            },
        ],
    }
    out = rf.resolve_min_production_factors(system)
    assert out["T1"] == pytest.approx(0.5)
    assert out["9"] == pytest.approx(2.0)
    assert "T3" not in out
    assert out["T4"] == pytest.approx(1.7)


def test_compute_turbine_maxflow_paths():
    system = {
        "generator_array": [
            {"name": "G1", "uid": 1, "pmax": 100.0},
            {"name": "G2", "uid": 2, "pmax": "pmax"},
            {"name": "G3", "uid": 3, "pmax": "pmax"},
        ],
        "turbine_array": [
            {"name": "T0", "generator": "G1"},  # no PF → skipped
            {"name": "T1", "generator": 1, "production_factor": 2.0},
            {"name": "T2", "generator": "G2", "production_factor": 1.0},
            {"name": "T3", "generator": "G3", "production_factor": 1.0},
            {
                "name": "T4",
                "generator": "G3",
                "production_factor": 1.0,
                "capacity": 42.0,
            },
            {"uid": 5, "generator": "missing", "production_factor": 1.0},
            {"uid": 6, "generator": None, "production_factor": 1.0},
        ],
    }
    out = rf.compute_turbine_maxflow(system, generator_peaks={"G2": 30.0})
    assert out["T1"] == pytest.approx(50.0)
    assert out["T2"] == pytest.approx(30.0)
    assert "T3" not in out
    assert out["T4"] == pytest.approx(42.0)
    assert "uid:5" not in out


def test_generator_capacity_override_and_turbine_pf():
    gen = {"uid": 4, "name": "G"}
    assert rf._generator_capacity_override("G", gen, {}) is None
    assert rf._generator_capacity_override(True, gen, {4: 10.0}) == 10.0
    assert rf._generator_capacity_override("x", gen, {"G": 7}) == 7.0
    assert rf._generator_capacity_override("x", {"uid": True}, {"G": 7}) is None
    assert rf._generator_capacity_override("x", gen, {"G": -1}) is None
    assert rf._generator_capacity_override("x", None, {"G": 1}) is None
    t = {"name": "T", "uid": 3, "production_factor": 1.5}
    assert rf._resolve_turbine_pf(t, {"3": 0.8}) == 0.8
    assert rf._resolve_turbine_pf({"uid": True, "production_factor": 2}, {"x": 1}) == 2
    assert rf._resolve_turbine_pf(t, {"T": 0.0}) == 1.5


# ---------------------------------------------------------------------------
# Table readers
# ---------------------------------------------------------------------------


def test_read_parquet_peaks_long_and_wide(tmp_path, caplog):
    long_p = tmp_path / "long.parquet"
    pq.write_table(
        pa.table(
            {
                "uid": pa.array([1, 1, 2, None, 3], type=pa.float64()),
                "value": pa.array([1.0, 5.0, 2.0, 9.0, None]),
            }
        ),
        long_p,
    )
    assert rf._read_parquet_peaks_by_uid(long_p) == {1: 5.0, 2: 2.0}

    long_s = tmp_path / "long_str.parquet"
    pq.write_table(
        pa.table({"uid": ["1", "bad"], "value": ["3.0", "4.0"]}),
        long_s,
    )
    assert rf._read_parquet_peaks_by_uid(long_s) == {1: 3.0}

    wide = tmp_path / "wide.parquet"
    pq.write_table(
        pa.table(
            {
                "block": [1, 2],
                "uid:7": [1.0, 0.5],
                "gen:8": pa.array([None, None], type=pa.float64()),
                "name": ["a", "b"],
                "x:9": ["bad", "4.5"],
            }
        ),
        wide,
    )
    assert rf._read_parquet_peaks_by_uid(wide) == {7: 1.0, 9: 4.5}

    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not parquet")
    with caplog.at_level(logging.WARNING):
        assert rf._read_parquet_peaks_by_uid(bad) is None
        assert rf._read_parquet_peaks_by_uid(tmp_path / "missing.parquet") is None
    assert "failed to read" in caplog.text


def test_read_parquet_peaks_without_pyarrow(tmp_path, monkeypatch):
    path = tmp_path / "x.parquet"
    path.write_bytes(b"x")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyarrow.parquet":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert rf._read_parquet_peaks_by_uid(path) is None


def test_read_csv_peaks(tmp_path):
    ok = tmp_path / "ok.csv"
    ok.write_text("uid,value\n1,2\n1,5\nx,3\n2,nan\n", encoding="utf-8")
    assert rf._read_csv_peaks_by_uid(ok) == {1: 5.0}
    nouid = tmp_path / "nouid.csv"
    nouid.write_text("a,b\n1,2\n", encoding="utf-8")
    assert rf._read_csv_peaks_by_uid(nouid) is None
    assert rf._read_csv_peaks_by_uid(tmp_path / "missing.csv") is None


def test_locate_and_read_table(tmp_path, caplog):
    assert rf._locate_and_read_table("t", None) is None
    (tmp_path / "t.csv").write_text("uid,value\n4,8\n", encoding="utf-8")
    assert rf._locate_and_read_table("t", tmp_path, subdir="Flow") == {4: 8.0}
    with caplog.at_level(logging.WARNING):
        assert rf._locate_and_read_table("none", tmp_path) is None
    assert "could not locate" in caplog.text


# ---------------------------------------------------------------------------
# Peak resolvers
# ---------------------------------------------------------------------------


def test_resolve_inflow_peaks_variants(tmp_path, caplog):
    (tmp_path / "Flow").mkdir()
    (tmp_path / "Flow" / "dis.csv").write_text("uid,value\n2,6\n", encoding="utf-8")
    system = {
        "junction_array": [{"name": "J1", "uid": 1}],
        "flow_array": [
            {"junction": "J1", "discharge": 3.0, "direction": "out"},
            {"junction": 99, "discharge": 4.0},
            {"uid": 2, "junction": "J1", "discharge": "dis"},
            {"uid": True, "junction": "J1", "discharge": "dis"},
            {"uid": 5, "junction": "J1", "discharge": "dis"},
        ],
    }
    peaks = rf.resolve_inflow_peaks(system, input_dir=tmp_path)
    assert peaks == {"99": 4.0, "J1": 6.0}
    with caplog.at_level(logging.WARNING):
        assert rf.resolve_inflow_peaks(system, input_dir=None) == {"99": 4.0}
    assert "no input_dir" in caplog.text


def test_resolve_generator_and_waterway_peaks(tmp_path):
    gen_dir = tmp_path / "Generator"
    gen_dir.mkdir()
    (gen_dir / "pmax.csv").write_text("uid,value\n1,10\n2,0\n3,7\n", encoding="utf-8")
    system = {
        "generator_array": [
            {"name": "G1", "uid": 1, "pmax": "pmax"},
            {"name": "G2", "uid": 2, "pmax": "pmax"},
            {"uid": 3, "pmax": "pmax"},
            {"name": "G4", "uid": "x", "pmax": "pmax"},
            {"name": "G5", "uid": 5, "pmax": "other"},
            {"name": "G6", "uid": 6, "pmax": 3.0},
        ]
    }
    assert not rf.resolve_generator_pmax_peaks(system, input_dir=None)
    assert rf.resolve_generator_pmax_peaks(system, input_dir=tmp_path) == {
        "G1": 10.0,
        "1": 10.0,
        "3": 7.0,
    }

    ww_dir = tmp_path / "Waterway"
    ww_dir.mkdir()
    (ww_dir / "fmax.csv").write_text("uid,value\n1,10\n2,-1\n", encoding="utf-8")
    wsys = {
        "waterway_array": [
            {"uid": 1, "fmax": "fmax"},
            {"uid": 2, "fmax": "fmax"},
            {"uid": "x", "fmax": "fmax"},
            {"uid": 4, "fmax": "nope"},
            {"uid": 5, "fmax": 3.0},
        ]
    }
    assert not rf.resolve_waterway_fmax_peaks(wsys, input_dir=None)
    assert rf.resolve_waterway_fmax_peaks(wsys, input_dir=tmp_path) == {1: 10.0}


def test_direction_and_lookup_helpers():
    assert rf._is_outflow_direction(" Extraction ")
    assert rf._is_outflow_direction(-1)
    assert not rf._is_outflow_direction(None)
    assert rf._ww_peak({"uid": "x"}, {1: 1.0}) is None


# ---------------------------------------------------------------------------
# Seepage
# ---------------------------------------------------------------------------


def test_piecewise_seepage_and_eval_volume():
    segs = [
        {"volume": "x", "slope": 1.0, "constant": 0.0},
        {"volume": 0.0, "slope": 0.1, "constant": 1.0},
        {"volume": 50.0, "slope": 0.2, "constant": -4.0},
    ]
    assert rf._piecewise_seepage(segs, 60.0) == pytest.approx(8.0)
    assert rf._piecewise_seepage([{"volume": "x"}], 1.0) is None
    assert rf._piecewise_seepage([{"volume": 0.0, "slope": "s"}], 1.0) is None
    assert rf._seepage_eval_volume({"emax": "ref"}, segs) == 50.0
    assert rf._seepage_eval_volume(None, []) == 0.0


def test_resolve_reservoir_seepage_caps():
    assert not rf.resolve_reservoir_seepage_caps({})
    system = {
        "reservoir_array": [{"name": "R", "emax": 10.0}],
        "waterway_array": [{"name": "filt", "uid": 11}],
        "reservoir_seepage_array": [
            {"reservoir": "R", "waterway": "filt", "segments": []},
            {
                "reservoir": "R",
                "waterway": "filt",
                "segments": [{"volume": 0.0, "slope": 0.0, "constant": 3.0}],
            },
            {
                "reservoir": "R",
                "waterway": 12,
                "segments": [{"volume": 0.0, "slope": 0.0, "constant": 2.0}],
            },
            {
                "reservoir": "R",
                "waterway": "ghost",
                "segments": [{"volume": 0.0, "slope": 0.0, "constant": 1.0}],
            },
            {
                "reservoir": "R",
                "waterway": "neg",
                "segments": [{"volume": 0.0, "slope": 0.0, "constant": -1.0}],
            },
        ],
    }
    assert rf.resolve_reservoir_seepage_caps(system) == {
        11: 3.0,
        "filt": 3.0,
        12: 2.0,
        "ghost": 1.0,
    }
    assert rf._seepage_cap({"uid": 99, "name": "filt"}, {"filt": 3.0}) == 3.0
    assert rf._seepage_cap({"uid": 99}, {"filt": 3.0}) is None


def test_penstock_helpers():
    ww = {"name": "pen", "uid": 3, "fmax": "f"}
    by_name, by_uid = {"pen": ww}, {3: ww}
    turbines = [{"waterway": "missing"}, {"waterway": 3}, {"waterway": "pen"}]
    assert rf._turbine_penstock_uids(turbines, by_name, by_uid) == {3}
    assert (
        rf._turbine_penstock_uids(
            [{"waterway": "nouid"}], {"nouid": {"name": "nouid"}}, {}
        )
        == set()
    )
    assert rf._turbine_penstock_fmax({"waterway": 9}, by_name, by_uid, {}) is None
    assert (
        rf._turbine_penstock_fmax({"waterway": "pen"}, by_name, by_uid, {3: 7.0}) == 7.0
    )


def test_reservoir_drain_enabled():
    assert not rf._reservoir_drain_enabled({})
    assert rf._reservoir_drain_enabled({"spillway_cost": 1.0})
    assert not rf._reservoir_drain_enabled(
        {"spillway_cost": 1.0, "spillway_capacity": 0.0}
    )
    assert rf._reservoir_drain_enabled({"spillway_cost": 1.0, "spillway_capacity": 5})


# ---------------------------------------------------------------------------
# Topology core
# ---------------------------------------------------------------------------


def test_estimate_bounds_topology_edges():
    """Junction without name, missing reservoir junction, seepage + extraction
    outlets, turbine without junction, penstock clamp, existing description."""
    system = {
        "junction_array": [
            {"uid": 1},
            {"name": "J2", "uid": 2},
            {"name": "J3", "uid": 3},
            {"name": "J4", "uid": 4},
            {"name": "J5", "uid": 5},
        ],
        "reservoir_array": [
            {"name": "R1", "junction": 1, "description": "base"},
            {"name": "R2", "junction": "J4"},
            {"name": "Rnone"},
            {"name": "R5", "junction": "J5"},
        ],
        "waterway_array": [
            # seepage-capped outlet from R1
            {"name": "filt", "uid": 10, "junction_a": 1, "junction_b": None},
            # uncapped extraction R1 -> J2 (non-reservoir)
            {"name": "ext", "uid": 11, "junction_a": 1, "junction_b": "J2"},
            # J2 onward conveyance
            {
                "name": "onward",
                "uid": 12,
                "junction_a": "J2",
                "junction_b": "J3",
                "fmax": 20.0,
            },
            # uncapped spill to a sink: ignored
            {"name": "sink", "uid": 13, "junction_a": 1, "junction_b": None},
            # uncapped spill to a reservoir: ignored
            {"name": "toR", "uid": 14, "junction_a": 1, "junction_b": "J4"},
            # uncapped extraction R5 -> J6 with no onward conveyance
            {"name": "dead", "uid": 17, "junction_a": "J5", "junction_b": "J6"},
            # penstock
            {
                "name": "pen",
                "uid": 15,
                "junction_a": "J4",
                "junction_b": "J3",
                "fmax": 5.0,
            },
            # parquet-peak capacity
            {
                "name": "pk",
                "uid": 16,
                "junction_a": "J3",
                "junction_b": "J4",
                "fmax": "fmax",
            },
        ],
        "turbine_array": [
            {"name": "T1", "waterway": "pen"},
            {"name": "Tzero"},
            {"name": "Tnoj", "junction_a": None},
        ],
    }
    report = rf.estimate_reservoir_flow_bounds(
        system,
        inflow_peaks={"J3": 4.0, "J5": 3.0},
        turbine_maxflow={"T1": 50.0, "Tzero": 0.0, "Tnoj": 3.0},
        ww_peaks={16: 6.0},
        seepage_caps={10: 2.0},
    )
    r1, r2, _, _ = system["reservoir_array"]
    # R1: filt 2 + onward 20 = 22 → ×1.1
    assert r1["fmax"] == pytest.approx(round(22 * 1.1))
    assert r1["description"].startswith("base; reservoir-flow estimate")
    # R2: penstock clamp min(50, 5) = 5.
    assert r2["fmax"] == pytest.approx(round(5 * 1.1))
    assert report["Rnone"] == (None, None)
    # R5: no release (fmax untouched), accept cap = its natural inflow.
    assert report["R5"] == (-float(round(3.0 * 1.1)), None)


def test_reservoir_max_inflow_target_absent_and_restore():
    import networkx as nx

    g = nx.DiGraph()
    assert rf._reservoir_max_inflow(g, {}, target="X", this_reservoir_idx=0) == 0.0
    g.add_edge(rf._SRC, "J", capacity=10.0)
    g.add_edge("J", rf._SINK, capacity=10.0)
    # Own release 4 < edge capacity 10: edge is shrunk then restored.
    val = rf._reservoir_max_inflow(g, {0: ("J", 4.0)}, target="J", this_reservoir_idx=0)
    assert val == pytest.approx(6.0)
    assert g[rf._SRC]["J"]["capacity"] == pytest.approx(10.0)
    # Own release == capacity: edge dropped then re-added.
    g2 = nx.DiGraph()
    g2.add_edge(rf._SRC, "J", capacity=4.0)
    g2.add_edge("K", "J", capacity=1.0)
    val2 = rf._reservoir_max_inflow(
        g2, {0: ("J", 4.0)}, target="J", this_reservoir_idx=0
    )
    assert val2 == 0.0
    assert g2[rf._SRC]["J"]["capacity"] == pytest.approx(4.0)


def test_build_maxflow_graph_accumulates_and_skips():
    graph, src_caps = rf._build_maxflow_graph(
        waterways=[
            {"uid": 1, "junction_a": "A", "junction_b": "B", "fmax": 2.0},
            {"uid": 2, "junction_a": "A", "junction_b": "B", "fmax": 3.0},
            {"uid": 3, "junction_a": "A", "junction_b": None, "fmax": 3.0},
            {"uid": 4, "junction_a": "A", "junction_b": "C", "fmax": "x"},
            {"uid": 5, "junction_a": "A", "junction_b": "C"},
        ],
        turbine_edges=[("B", None, 1.0)],
        inflow_peaks={"A": 0.0, "B": 2.0},
        reservoir_junction={0: "A", 1: "B"},
        reservoir_release={0: 0.0, 1: 3.0},
        jkey=lambda r: r,
        ww_peaks={4: 1.5},
        seepage_caps={5: 0.5},
    )
    assert graph["A"]["B"]["capacity"] == pytest.approx(5.0)
    assert graph["A"]["C"]["capacity"] == pytest.approx(2.0)
    assert graph["B"][rf._SINK]["capacity"] == pytest.approx(1.0)
    assert not graph.has_edge(rf._SRC, "A")
    assert graph[rf._SRC]["B"]["capacity"] == pytest.approx(5.0)
    assert src_caps == {1: ("B", 3.0)}


# ---------------------------------------------------------------------------
# Top-level wrappers
# ---------------------------------------------------------------------------


def test_apply_reservoir_flow_estimates_guards():
    assert not rf.apply_reservoir_flow_estimates({"system": []}, input_dir=None)
    empty = {"system": {"reservoir_array": []}}
    assert not rf.apply_reservoir_flow_estimates(empty, input_dir=None)


def test_widen_extraction_bounds_symmetric():
    assert not rf.widen_extraction_bounds_symmetric({"system": None})
    planning = {
        "system": {
            "reservoir_array": [
                {"name": "A", "fmin": -3.0, "fmax": 5},
                {"name": "B", "fmin": "x", "fmax": True},
            ]
        }
    }
    out = rf.widen_extraction_bounds_symmetric(planning, factor=3.0)
    assert out == {"A": (-15.0, 15.0), "B": (None, None)}


def test_drop_large_reservoir_spillways():
    system = {
        "waterway_array": [
            {"name": "Vert_BIG", "junction_a": "BIG"},
            {"name": "BIG_ver", "junction_a": "BIG"},
            {"name": "Vert_SMALL", "junction_a": "SMALL"},
            {"name": "BIG_gen", "junction_a": "BIG"},
        ]
    }
    caps = {"BIG": 1000.0, "SMALL": 10.0, "BAD": True}
    assert not rf.drop_large_reservoir_spillways(system, caps, 0.0)
    assert not rf.drop_large_reservoir_spillways(system, {"SMALL": 1.0}, 500.0)
    dropped = rf.drop_large_reservoir_spillways(
        system, caps, 500.0, protected_waterways=frozenset({"BIG_ver"})
    )
    assert dropped == ["Vert_BIG"]
    assert [w["name"] for w in system["waterway_array"]] == [
        "BIG_ver",
        "Vert_SMALL",
        "BIG_gen",
    ]
    # Nothing to drop → array untouched.
    assert not rf.drop_large_reservoir_spillways({"waterway_array": []}, caps, 500.0)
