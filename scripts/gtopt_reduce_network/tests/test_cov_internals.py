# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge cases of the reducer internals (graph, distance, clustering, I/O)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from gtopt_reduce_network import _cluster, _partition_nx, _topology
from gtopt_reduce_network._aggregate import AggregatedLine
from gtopt_reduce_network._busmap import BusmapRow, _coerce
from gtopt_reduce_network._components import (
    _resolve_to_uid,
    expand_busmap_with_eliminated,
    rewrite_component_buses,
)
from gtopt_reduce_network._distance import (
    _build_ptdf_matrix,
    ptdf_distance_matrix,
    reactance_shortest_path_matrix,
    zbus_distance_matrix,
)
from gtopt_reduce_network._io import (
    Case,
    _index_buses,
    iter_bus_referencing_elements,
    resolve_bus_ref,
)
from gtopt_reduce_network._line_schedules import (
    _coerce_bool_or_int,
    _coerce_float,
    _scalars_from_line_array,
    _ScheduleTable,
    _stage_block_df_from_matrix,
    aggregate_line_schedules,
)
from gtopt_reduce_network._local_simplify import simplify_local
from gtopt_reduce_network._protected_lines import collect_protected_lines
from gtopt_reduce_network._reduce import (
    ReduceConfig,
    _build_bus_array,
    _build_line_array,
    _distance_matrix,
    _eliminated_to_parent,
    _injection_bus_set,
    reduce_case,
)
from gtopt_reduce_network._simplify import _write_lossfactor
from gtopt_reduce_network._topology import LineGraph, build_line_graph
from gtopt_reduce_network._user_constraints import filter_line_user_constraints

# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _case(system: dict[str, Any], options: dict[str, Any] | None = None) -> Case:
    case = Case(raw={"options": options or {}, "system": system})
    _index_buses(case)
    return case


def _buses(n: int) -> list[dict[str, Any]]:
    return [{"uid": i, "name": f"b{i}"} for i in range(1, n + 1)]


def _ln(uid: int, a: Any, b: Any, x: Any = 0.1, cap: Any = 100.0, **kw: Any):
    out = {
        "uid": uid,
        "name": f"l{uid}",
        "bus_a": a,
        "bus_b": b,
        "reactance": x,
        "tmax_ab": cap,
        "tmax_ba": cap,
    }
    out.update(kw)
    return out


def _graph(
    n_buses: int,
    edges: list[tuple[int, int, float]],
    caps: list[float] | None = None,
    dc: list[bool] | None = None,
) -> LineGraph:
    """LineGraph over buses 1..n with (a_idx, b_idx, x) local-index edges."""
    caps = caps or [100.0] * len(edges)
    return LineGraph(
        bus_uids=list(range(1, n_buses + 1)),
        bus_index={u: u - 1 for u in range(1, n_buses + 1)},
        line_uids=list(range(10, 10 + len(edges))),
        line_a=np.asarray([e[0] for e in edges], dtype=np.int64),
        line_b=np.asarray([e[1] for e in edges], dtype=np.int64),
        line_x=np.asarray([e[2] for e in edges], dtype=float),
        line_r=np.zeros(len(edges)),
        line_fab=np.asarray(caps, dtype=float),
        line_fba=np.asarray(caps, dtype=float),
        line_is_dc=None if dc is None else np.asarray(dc, dtype=bool),
    )


# ---------------------------------------------------------------------------
# _io / _busmap / _components
# ---------------------------------------------------------------------------


def test_io_validation_and_refs():
    case = _case({"bus_array": [{"uid": 1, "name": "a"}]})
    assert case.simulation == {}
    with pytest.raises(ValueError, match="missing uid"):
        _case({"bus_array": [{"name": "x"}]})
    with pytest.raises(ValueError, match="duplicate bus name"):
        _case({"bus_array": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "a"}]})
    with pytest.raises(TypeError, match="unsupported"):
        resolve_bus_ref(case, 1.5)  # type: ignore[arg-type]
    case.system["generator_array"] = [{"uid": 1}, {"uid": 2, "bus": "a"}]
    assert len(iter_bus_referencing_elements(case)) == 1


def test_busmap_coerce_string_annotations():
    class Row:  # pylint: disable=too-few-public-methods
        __annotations__ = {"a": "int | None", "b": "float | None", "c": "str"}

    got = _coerce({"a": "3", "b": "2.5", "c": "x", "zz": "ignored"}, Row)
    assert got == {"a": 3, "b": 2.5, "c": "x"}


def test_components_rewrite_and_expand():
    case = _case(
        {
            "bus_array": _buses(3),
            "generator_array": [
                {"uid": 1, "bus": None},
                {"uid": 2, "bus": 3},  # not in busmap
                {"uid": 3, "bus": 2},  # int ref moved to cluster 1
                {"uid": 4, "bus": "b1"},  # unchanged
            ],
        }
    )
    rows = rewrite_component_buses(case, {1: 1, 2: 1}, bus_name_by_uid={1: "b1"})
    assert case.system["generator_array"][2]["bus"] == 1
    assert [(r.component_uid, r.original_bus_uid) for r in rows] == [(3, 2), (4, 1)]
    out = expand_busmap_with_eliminated(
        [BusmapRow(1, 1)],
        [5, 6, 7],
        parent_bus_of_eliminated={6: 9, 7: 1},
    )
    assert [(r.original_bus_uid, r.cluster_bus_uid) for r in out] == [(1, 1), (7, 1)]
    assert _resolve_to_uid(case, True) is None
    assert _resolve_to_uid(case, 99) is None
    assert _resolve_to_uid(case, 2.0) is None


# ---------------------------------------------------------------------------
# _topology
# ---------------------------------------------------------------------------


def test_build_line_graph_validation_and_skips():
    case = _case(
        {
            "bus_array": [
                {"uid": 1, "name": "Salar110"},
                {"uid": 2, "name": "Norte220"},
                {"uid": 3, "name": "x"},
            ],
            "line_array": [
                _ln(1, 1, 2, active=False),
                _ln(2, 1, "missing"),
                _ln(3, 1, None),
                _ln(4, 1, 1),
                _ln(5, 1, 2, active=1, x=True, cap=[[5, 50], ["s"]]),
                _ln(6, 2, 3, x="file", cap=True),
                _ln(7, 1, 3, x=0.2, cap="tmax.parquet"),
            ],
        }
    )
    for kw in ("dc_reactance_threshold", "dc_voltage_threshold", "dc_power_threshold"):
        with pytest.raises(ValueError, match=kw):
            build_line_graph(case, **{kw: -1.0})
    g = build_line_graph(case)
    assert g.skipped_line_uids == [2, 3, 4]
    assert g.line_uids == [5, 6, 7]
    assert g.line_fab.tolist() == [50.0, 0.0, 0.0]
    assert g.dc_line_uids == [5, 6]
    assert _topology._max_scalar([["x"]]) is None
    # undirected adjacency: no lines, and a pure-DC pair (1/inf == 0)
    assert _topology.build_undirected_adjacency(_graph(2, [])).nnz == 0
    dc_only = _graph(2, [(0, 1, np.inf)])
    assert _topology.build_undirected_adjacency(dc_only).nnz == 0


# ---------------------------------------------------------------------------
# _distance
# ---------------------------------------------------------------------------


def test_distance_empty_graphs():
    empty = _graph(0, [])
    for fn in (reactance_shortest_path_matrix, zbus_distance_matrix):
        assert fn(empty).shape == (0, 0)
    assert ptdf_distance_matrix(empty).shape == (0, 0)
    assert _build_ptdf_matrix(_graph(3, [])).shape == (0, 3)


def test_zbus_and_ptdf_islands_and_singular():
    # buses 1-2 connected, bus 3 isolated, buses 4-5 connected
    g = _graph(5, [(0, 1, 0.1), (3, 4, 0.2)])
    z = zbus_distance_matrix(g)
    assert z[2, 2] == 0.0
    assert np.isinf(z[0, 3])
    assert z[0, 1] == pytest.approx(0.1)
    ptdf = _build_ptdf_matrix(g)
    assert ptdf[0, 3] == 0.0 and ptdf[1, 0] == 0.0
    # singular reduced susceptance: weights 1, 1 and -0.5 (x=-2, which the
    # undirected adjacency ignores) cancel exactly.
    sing = _graph(3, [(0, 1, 1.0), (1, 2, 1.0), (0, 2, -2.0)])
    zs = zbus_distance_matrix(sing)
    assert np.isinf(zs[0, 1])
    assert not _build_ptdf_matrix(sing).any()


# ---------------------------------------------------------------------------
# _local_simplify
# ---------------------------------------------------------------------------


def test_degree2_elimination_leaves_self_loop_pair():
    """Ring a-b-c with only ``a`` pinned: eliminating b yields two parallel
    a-c edges, so c sees both outer endpoints equal to a and is kept until
    the next parallel-merge pass."""
    g = _graph(3, [(0, 1, 0.1), (1, 2, 0.1), (0, 2, 0.3)])
    res = simplify_local(g, injection_bus_uids=[1])
    assert res.eliminated_buses[0] == 2
    assert 1 in res.surviving_buses


# ---------------------------------------------------------------------------
# _cluster
# ---------------------------------------------------------------------------


def test_select_anchors_rules_and_errors():
    case = _case(
        {
            "bus_array": _buses(5),
            "generator_array": [
                {"uid": 1, "name": "g1", "bus": 1, "capacity": [[0, 80]]},
                {"uid": 2, "bus": "b2", "pmax": 5},
                {"uid": 3, "bus": "zz", "pmax": 5},
                {"uid": 4, "bus": None, "pmax": 5},
                {"name": "g5", "bus": 3, "capacity": True, "pmax": [["x"]]},
            ],
            "demand_array": [
                {"uid": 1, "bus": 4, "lmax": 30},
                {"uid": 2, "bus": "nope", "lmax": 30},
                {"uid": 3, "bus": None},
            ],
            "turbine_array": [
                {"uid": 1, "bus": "bad"},
                {"uid": 2, "generator": "g5"},
                {"uid": 3, "generator": 1},
                {"uid": 4, "generator": True},
                {"uid": 5, "generator": 2.5},
                {"uid": 6, "bus": 5},
                {"uid": 7},
            ],
        }
    )
    sel = _cluster.select_anchors(
        case,
        target_buses=5,
        surviving_bus_uids=[1, 2, 3, 4, 5],
        user_anchor_uids=[2, 99],
        min_load_mw=10.0,
        min_gen_capacity_mw=50.0,
        top_n_by_injection=5,
    )
    assert sel.bus_uids == [1, 2, 3, 4, 5]
    assert sel.by_rule[3] == ["reservoir-host"]
    assert "min-load" in sel.by_rule[4]
    assert "min-gen" in sel.by_rule[1]
    # thresholds above every bus, no reservoir hosts
    sel2 = _cluster.select_anchors(
        case,
        target_buses=5,
        surviving_bus_uids=[1, 2, 3, 4, 5],
        min_load_mw=1e6,
        min_gen_capacity_mw=1e6,
        include_reservoir_hosts=False,
        top_n_by_injection=5,
    )
    assert sel2.bus_uids == [1, 2, 4]
    with pytest.raises(ValueError, match="user anchors exceed"):
        _cluster.select_anchors(
            case,
            target_buses=1,
            surviving_bus_uids=[1, 2, 3],
            user_anchor_uids=[1, 2],
        )
    assert _cluster._scalar_peak("file", fallback=None) == 0.0


def test_build_busmap_empty_and_shared_anchor_split():
    assert not _cluster.build_busmap([], np.zeros((0, 0)), target_buses=1, anchors=[])
    labels = _cluster._split_shared_anchor_clusters(np.asarray([0, 0, 1]), [0, 1])
    assert labels.tolist() == [0, 2, 1]


# ---------------------------------------------------------------------------
# _partition_nx
# ---------------------------------------------------------------------------


def test_build_busmap_nx_trivial_and_errors():
    g = _graph(3, [(0, 1, 0.1), (1, 2, 0.1)])
    with pytest.raises(ValueError, match="weight_mode"):
        _partition_nx.build_busmap_nx(g, target_buses=1, anchors=[], weight_mode="x")
    assert not _partition_nx.build_busmap_nx(_graph(0, []), target_buses=1, anchors=[])
    rows = _partition_nx.build_busmap_nx(g, target_buses=3, anchors=[])
    assert [(r.original_bus_uid, r.cluster_bus_uid) for r in rows] == [
        (1, 1),
        (2, 2),
        (3, 3),
    ]


def test_build_weighted_graph_susceptance_parallel_and_floor():
    g = _graph(
        3,
        [(0, 1, 0.5), (0, 1, 0.5), (1, 2, 0.1), (0, 2, 0.1)],
        caps=[1.0, 1.0, 0.0, 0.0],
        dc=[False, False, False, True],
    )
    wg = _partition_nx._build_weighted_graph(g, "susceptance")
    assert wg[1][2]["weight"] == pytest.approx(4.0)
    assert not wg.has_edge(1, 3)
    cg = _partition_nx._build_weighted_graph(g, "capacity")
    assert cg[2][3]["weight"] == pytest.approx(2e-6)


def test_partition_component_merge_and_split_paths():
    # two dense triangles joined by a weak tie: louvain finds 2 communities
    sub = nx.Graph()
    for a, b in [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6)]:
        sub.add_edge(a, b, weight=10.0)
    sub.add_edge(3, 4, weight=0.1)
    merged = _partition_nx._partition_component(sub, 1, 0)
    assert merged == [{1, 2, 3, 4, 5, 6}]
    # louvain(2) -> merge loop is exercised by asking the merger directly
    comms = [{1, 2, 3}, {4, 5, 6}, {7}]
    sub.add_edge(6, 7, weight=5.0)
    out = _partition_nx._merge_strongest_pair(sub, comms)
    assert {4, 5, 6, 7} in out
    # no coupling at all: safety net merges the two smallest
    lone = nx.Graph()
    lone.add_nodes_from([1, 2, 3])
    out = _partition_nx._merge_strongest_pair(lone, [{1}, {2}, {3}])
    assert sorted(map(sorted, out)) == [[1, 2], [3]]
    # split: more communities requested than louvain returns
    split = _partition_nx._partition_component(sub, 5, 0)
    assert len(split) == 5
    # split None when every community is a singleton
    assert _partition_nx._split_worst_bottleneck(lone, [{1}, {2}, {3}]) is None
    # disconnected inner community splits off its smallest component
    out = _partition_nx._split_worst_bottleneck(lone, [{1, 2, 3}])
    assert out is not None and len(out) == 2


def test_partition_merge_loop_when_louvain_overshoots(monkeypatch):
    sub = nx.path_graph([1, 2, 3, 4])
    nx.set_edge_attributes(sub, 1.0, "weight")
    monkeypatch.setattr(
        nx.community,
        "louvain_communities",
        lambda *a, **k: [{1}, {2}, {3}, {4}],
    )
    out = _partition_nx._partition_component(sub, 2, 0)
    assert len(out) == 2


def test_split_shared_anchor_communities():
    out = _partition_nx._split_shared_anchor_communities([{1, 2, 3}, {4}], [1, 3])
    assert out == [{1, 2}, {4}, {3}]


# ---------------------------------------------------------------------------
# _protected_lines / _user_constraints
# ---------------------------------------------------------------------------


def test_collect_protected_lines_sources(tmp_path: Path):
    (tmp_path / "uc_a.pampl").write_text('line("l1").flow <= 1;\n', encoding="utf-8")
    (tmp_path / "other.pampl").write_text("line('l2') <= 1;\n", encoding="utf-8")
    system = {
        "bus_array": _buses(3),
        "line_array": [
            _ln(1, 1, 2),
            _ln(2, 2, 3),
            _ln(3, 1, "ghost"),
            _ln(4, 1, 3),
        ],
        "line_commitment_array": [{"line": "3"}, {"line": None}, {"line": "nope"}],
    }
    # glob discovery of uc_*.pampl when user_constraint_files is absent
    uids, buses = collect_protected_lines(_case(dict(system)), input_dir=tmp_path)
    assert uids == {1, 3}
    assert buses == {1, 2}
    # explicit file list (missing entries ignored)
    system["user_constraint_files"] = ["other.pampl", "missing.pampl"]
    uids, _ = collect_protected_lines(_case(dict(system)), input_dir=tmp_path)
    assert uids == {2, 3}
    # nothing referenced
    bare = {"bus_array": _buses(2), "line_array": [_ln(1, 1, 2)]}
    assert collect_protected_lines(_case(bare)) == (set(), set())


def test_filter_line_user_constraints(tmp_path: Path):
    src_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    src_dir.mkdir()
    out_dir.mkdir()
    (src_dir / "uc_tx.pampl").write_text(
        "var slack_c1;\n"
        "# comment for c1\n"
        'constraint c1: line("l1").flow\n'
        "   <= 10 + slack_c1;\n"
        'constraint c2: line("keep").flow <= 5;\n'
        "param p = 3;\n",
        encoding="utf-8",
    )
    (src_dir / "uc_plain.pampl").write_text(
        'constraint c3: generator("g").generation <= 5;\n', encoding="utf-8"
    )
    case = _case(
        {
            "bus_array": _buses(1),
            "user_constraint_array": [
                {"name": "i1", "expression": 'line("l1").flow <= 1'},
                {"name": "i2", "expression": "generator(1) <= 1"},
            ],
            "user_constraint_files": ["uc_tx.pampl", "uc_plain.pampl", "gone.pampl"],
        }
    )
    dropped = filter_line_user_constraints(
        case,
        input_dir=src_dir,
        out_dir=out_dir,
        tag="red",
        surviving_lines=frozenset({"keep"}),
    )
    assert dropped == 2
    assert [uc["name"] for uc in case.system["user_constraint_array"]] == ["i2"]
    assert case.system["user_constraint_files"] == [
        "uc_tx_red.pampl",
        "uc_plain.pampl",
        "gone.pampl",
    ]
    text = (out_dir / "uc_tx_red.pampl").read_text(encoding="utf-8")
    assert "c1" not in text and "c2" in text and "param p" in text
    assert (out_dir / "uc_plain.pampl").exists()
    # same dir: no copy, files untouched; non-list files -> early return
    case2 = _case({"bus_array": _buses(1), "user_constraint_files": ["uc_plain.pampl"]})
    assert not filter_line_user_constraints(
        case2, input_dir=src_dir, out_dir=src_dir, tag="t"
    )
    case3 = _case({"bus_array": _buses(1), "user_constraint_files": "x"})
    assert not filter_line_user_constraints(
        case3, input_dir=src_dir, out_dir=src_dir, tag="t"
    )


# ---------------------------------------------------------------------------
# _simplify
# ---------------------------------------------------------------------------


def test_write_lossfactor_rejects_unknown_collision():
    case = _case({"bus_array": [], "demand_array": [{"uid": 1, "lossfactor": 0.1}]})
    with pytest.raises(ValueError, match="unknown collision"):
        _write_lossfactor(case, "demand_array", 0.03, collision="bogus")


# ---------------------------------------------------------------------------
# _reduce
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"partition": "x"},
        {"nx_weight": "x"},
        {"dc_reactance_threshold": -1.0},
        {"dc_voltage_threshold": -1.0},
        {"dc_power_threshold": -1.0},
    ],
)
def test_reduce_config_validation(kwargs: dict[str, Any]):
    with pytest.raises(ValueError):
        ReduceConfig(target_buses=2, **kwargs)


def test_reduce_helpers():
    g = _graph(2, [(0, 1, 0.1)])
    assert _distance_matrix(g, "zbus").shape == (2, 2)
    assert _distance_matrix(g, "ptdf").shape == (2, 2)
    with pytest.raises(ValueError, match="unknown distance"):
        _distance_matrix(g, "bogus")
    case = _case(
        {
            "bus_array": _buses(2),
            "generator_array": [{"uid": 1, "bus": "zz"}, {"uid": 2, "bus": 1}],
            "demand_array": [{"uid": 1}],
        }
    )
    assert _injection_bus_set(case) == {1}
    assert _build_bus_array(case, [1, 7], {7: "agg7"})[1] == {"uid": 7, "name": "agg7"}
    dc_line = AggregatedLine(
        uid=5,
        name="d",
        bus_a_uid=1,
        bus_b_uid=2,
        reactance=None,
        resistance=0.2,
        tmax_ab=1.0,
        tmax_ba=1.0,
        is_dc=True,
    )
    assert _build_line_array([dc_line], {})[0]["resistance"] == 0.2
    # chain 1-2-3 with both 2 and 3 eliminated: 3's only neighbours are
    # eliminated or 2 -> parent search skips eliminated neighbours.
    chain = _graph(3, [(0, 1, 0.1), (1, 2, 0.1)])
    assert _eliminated_to_parent(chain, [2, 3]) == {2: 1}


def _reduce_case_dict() -> dict[str, Any]:
    buses = _buses(6)
    lines = [
        _ln(1, "b1", "b2"),
        _ln(2, "b2", "b3"),
        _ln(3, "b3", "b4", x=0.0, type="dc", resistance=0.1),  # DC link
        _ln(4, "b4", "b5"),
        _ln(5, "b5", "b6", cap=5.0),
        _ln(6, "b1", "ghost"),  # unresolvable, protected by a UC
    ]
    return {
        "bus_array": buses,
        "line_array": lines,
        "generator_array": [{"uid": 1, "bus": "b1", "pmax": 100}],
        "demand_array": [{"uid": 1, "bus": "b6", "lmax": 50}],
        "user_constraint_array": [{"name": "u", "expression": 'line("l6") <= 1'}],
    }


def test_reduce_case_dc_lines_tags_and_drop(tmp_path: Path):
    line_dir = tmp_path / "Line"
    line_dir.mkdir()
    pd.DataFrame(
        {"stage": [1, 1], "block": [1, 2], "uid": [1, 1], "value": [50.0, 60.0]}
    ).to_parquet(line_dir / "tmax_ab.parquet")
    case = _case(_reduce_case_dict())
    cfg = ReduceConfig(
        target_buses=3,
        drop_lines_below_mw=10.0,
        protect_constraint_lines=True,
        reduced_tag="r",
        parquet_case_dir=str(tmp_path),
    )
    res = reduce_case(case, cfg)
    lines = res.case.array("line_array")
    assert any(ln.get("type") == "dc" for ln in lines)
    assert all(ln.get("tmax_ab") != "tmax_ab_r" for ln in lines if ln.get("type"))
    assert any(ln.get("tmax_ab") == "tmax_ab_r" for ln in lines)
    assert (line_dir / "tmax_ab_r.parquet").exists()
    protected = [ln for ln in lines if ln.get("name") == "l6"]
    assert protected and protected[0]["bus_b"] == "ghost"


def test_reduce_case_tag_without_line_dir(tmp_path: Path):
    system = _reduce_case_dict()
    system["line_array"] = system["line_array"][:2]
    res = reduce_case(
        _case(system),
        ReduceConfig(target_buses=2, reduced_tag="r", parquet_case_dir=str(tmp_path)),
    )
    assert all(isinstance(ln["tmax_ab"], float) for ln in res.case.array("line_array"))


def test_reduce_case_louvain_partition():
    case = _case(_reduce_case_dict())
    res = reduce_case(case, ReduceConfig(target_buses=2, partition="louvain-mincut"))
    assert len(res.case.array("bus_array")) >= 2


# ---------------------------------------------------------------------------
# _line_schedules
# ---------------------------------------------------------------------------


def _eq(uid: int, absorbed: list[int]) -> AggregatedLine:
    return AggregatedLine(
        uid=uid,
        name=f"agg_{uid}",
        bus_a_uid=1,
        bus_b_uid=2,
        reactance=0.05,
        resistance=0.0,
        tmax_ab=100.0,
        tmax_ba=100.0,
        absorbed=absorbed,
    )


def test_schedule_table_long_format_and_bad_uid_column(tmp_path: Path):
    long = tmp_path / "long.parquet"
    pd.DataFrame({"stage": [1, 1], "uid": [3, 4], "value": [1.0, 2.0]}).to_parquet(long)
    t = _ScheduleTable.from_parquet("active", "stage", long)
    assert t.uid_cols == [3, 4]
    assert t.value_for(3).tolist() == [1.0]
    wide = tmp_path / "wide.parquet"
    pd.DataFrame({"uid:abc": [1.0], "uid:5": [2.0]}).to_parquet(wide)
    t2 = _ScheduleTable.from_parquet("active", "stage", wide)
    assert t2.uid_cols == [5]
    assert t2.stage_col is None


def test_scalar_coercions():
    assert _coerce_bool_or_int(True, default=1) == 1
    assert _coerce_bool_or_int("x", default=7) == 7
    assert _coerce_float(True, default=2.0) == 2.0
    out = _scalars_from_line_array([{"bus_a": 1}, {"uid": 1, "voltage": -1}])
    assert list(out) == [1]
    assert out[1].voltage == 1.0
    df = _stage_block_df_from_matrix(pd.DataFrame({"x": [0]}), [9], np.ones((1, 1)))
    assert list(df.columns) == ["uid:9"]


def test_aggregate_schedules_all_fields_with_empty_corridor(tmp_path: Path):
    line_dir = tmp_path / "Line"
    line_dir.mkdir()

    def stage(name: str, cols: dict[int, list[float]]) -> None:
        df = pd.DataFrame({"stage": [1, 2]})
        for u, v in cols.items():
            df[f"uid:{u}"] = v
        df.to_parquet(line_dir / f"{name}.parquet", index=False)

    stage("active", {1: [1, 0], 2: [1, 1]})
    stage("reactance", {1: [10.0, 10.0]})
    stage("resistance", {1: [1.0, 1.0]})
    pd.DataFrame({"stage": [1, 2], "block": [1, 1], "uid:1": [5.0, 6.0]}).to_parquet(
        line_dir / "tmax_ab.parquet", index=False
    )
    stage("voltage", {1: [110.0, 110.0], 2: [220.0, 220.0]})
    # previously written reduced file is ignored (re-run safety)
    pd.DataFrame({"stage": [1]}).to_parquet(line_dir / "active_t.parquet")
    out = aggregate_line_schedules(
        case_dir=tmp_path,
        input_directory=".",
        reduced_tag="t",
        aggregated_lines=[_eq(90, [1, 2]), _eq(91, [])],
        original_line_array=[{"uid": 1, "tmax_ab": 7.0}, {"uid": 2, "tmax_ab": 8.0}],
    )
    # voltage is an input only: equivalents carry the scalar V = 10.
    assert set(out) == {"active", "reactance", "resistance", "tmax_ab"}
    assert not (line_dir / "voltage_t.parquet").exists()
    tmax = pd.read_parquet(line_dir / "tmax_ab_t.parquet")
    assert tmax["uid:90"].tolist() == [13.0, 8.0]  # line 1 off in stage 2
    assert tmax["uid:91"].tolist() == [0.0, 0.0]


def test_aggregate_schedules_resistance_without_voltage_or_reactance(
    tmp_path: Path, caplog
):
    line_dir = tmp_path / "Line"
    line_dir.mkdir()
    pd.DataFrame({"stage": [1], "uid:1": [1.0]}).to_parquet(
        line_dir / "resistance.parquet", index=False
    )
    with caplog.at_level(logging.INFO):
        out = aggregate_line_schedules(
            case_dir=tmp_path,
            input_directory=".",
            reduced_tag="t",
            aggregated_lines=[_eq(90, [1])],
            original_line_array=[{"uid": 1, "reactance": 10.0}],
        )
    assert out == {"resistance": "resistance_t"}
    # nothing to aggregate onto
    assert not (
        aggregate_line_schedules(
            case_dir=tmp_path,
            input_directory=".",
            reduced_tag="u",
            aggregated_lines=[],
            original_line_array=[],
        )
    )
