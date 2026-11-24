# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Parquet-stem Line fields and explicitly protected lines."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from gtopt_reduce_network._io import Case, load_case
from gtopt_reduce_network._reduce import ReduceConfig, reduce_case
from gtopt_reduce_network._topology import (
    MVA_BASE,
    PU_LINE_VOLTAGE,
    LineScheduleResolver,
    build_line_graph,
)


def _write_case(tmp_path: Path, two_clusters_case: Case) -> Path:
    raw = json.loads(json.dumps(two_clusters_case.raw))
    raw["options"] = {"input_directory": "."}
    lines = raw["system"]["line_array"]
    lines[2]["tmax_ab"] = "my_tmax"  # the b2–b5 tie
    lines[2]["active"] = "my_active"
    lines[0]["reactance"] = "my_x"
    line_dir = tmp_path / "Line"
    line_dir.mkdir()
    pd.DataFrame(
        {
            "stage": [1, 1, 2, 2],
            "block": [1, 2, 1, 2],
            "uid": [3] * 4,
            "value": [10.0, 70.0, 20.0, 30.0],
        }
    ).to_parquet(line_dir / "my_tmax.parquet")
    pd.DataFrame({"stage": [1, 2], "uid": [3, 3], "value": [0, 1]}).to_parquet(
        line_dir / "my_active.parquet"
    )
    pd.DataFrame({"stage": [1, 2], "uid:1": [0.02, 0.04]}).to_parquet(
        line_dir / "my_x.parquet"
    )
    path = tmp_path / "case.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_stems_resolve_to_relaxed_static_values(
    tmp_path: Path, two_clusters_case: Case
) -> None:
    case = load_case(_write_case(tmp_path, two_clusters_case))
    graph = build_line_graph(case, schedules=LineScheduleResolver(tmp_path / "Line"))
    pos = {u: i for i, u in enumerate(graph.line_uids)}
    assert graph.line_fab[pos[3]] == pytest.approx(70.0)  # max over stages
    # Mean of the positive scheduled values, in p.u. on MVA_BASE (V = 1).
    assert graph.line_x[pos[1]] == pytest.approx(0.03 * MVA_BASE)
    assert graph.line_is_dc is not None and not graph.line_is_dc[pos[1]]
    # Without a resolver the stem capacity collapses to 0 (old behaviour).
    bare = build_line_graph(case)
    assert bare.line_fab[{u: i for i, u in enumerate(bare.line_uids)}[3]] == 0.0


def test_inactive_everywhere_line_is_dropped(
    tmp_path: Path, two_clusters_case: Case
) -> None:
    path = _write_case(tmp_path, two_clusters_case)
    pd.DataFrame({"stage": [1, 2], "uid": [3, 3], "value": [0, 0]}).to_parquet(
        tmp_path / "Line" / "my_active.parquet"
    )
    graph = build_line_graph(
        load_case(path), schedules=LineScheduleResolver(tmp_path / "Line")
    )
    assert 3 not in graph.line_uids


def test_protected_line_kept_between_groups(
    tmp_path: Path, two_clusters_case: Case
) -> None:
    case = load_case(_write_case(tmp_path, two_clusters_case))
    groups = {1: "A", 2: "A", 3: "A", 4: "B", 5: "B", 6: "B"}
    result = reduce_case(
        case,
        ReduceConfig(
            target_buses=0,
            bus_groups=groups,
            protected_line_uids=(3,),
            parquet_case_dir=str(tmp_path),
        ),
    )
    lines = result.case.array("line_array")
    assert [int(ln["uid"]) for ln in lines] == [3]
    # Kept with its original identity, stems included.
    assert lines[0]["tmax_ab"] == "my_tmax"
    assert lines[0]["active"] == "my_active"


def test_unknown_protected_line_raises(two_clusters_case: Case) -> None:
    with pytest.raises(ValueError, match="not in line_array"):
        reduce_case(
            two_clusters_case, ReduceConfig(target_buses=2, protected_line_uids=(99,))
        )


def test_mixed_voltages_merge_in_per_unit(two_clusters_case: Case) -> None:
    raw = json.loads(json.dumps(two_clusters_case.raw))
    for ln in raw["system"]["line_array"]:
        ln["voltage"] = 220.0
        ln["resistance"] = 1.0
    raw["system"]["line_array"][2]["voltage"] = 500.0  # the b2–b5 tie
    case = Case(raw=raw)
    from gtopt_reduce_network._io import (  # pylint: disable=import-outside-toplevel
        _index_buses,
    )

    _index_buses(case)
    groups = {1: "A", 2: "A", 3: "A", 4: "B", 5: "B", 6: "B"}
    result = reduce_case(case, ReduceConfig(target_buses=0, bus_groups=groups))
    (eq,) = result.case.array("line_array")
    # The single inter-group line is the 500 kV tie: its equivalent keeps
    # gtopt's X/V² and R/V² (p.u. on MVA_BASE, emitted at V = √MVA_BASE).
    v2 = PU_LINE_VOLTAGE**2
    assert eq["voltage"] == pytest.approx(PU_LINE_VOLTAGE)
    assert eq["reactance"] / v2 == pytest.approx(5.0 / 500.0**2)
    assert eq["resistance"] / v2 == pytest.approx(1.0 / 500.0**2)


def test_cluster_active_is_or_of_members(
    tmp_path: Path, two_clusters_case: Case
) -> None:
    """An old-vintage representative must not switch its cluster off.

    b1 (the group-A representative) is active only in stage 1 (an old
    network vintage); b2 / b3 are always active.  The reduced bus must
    stay in service in every stage — its ``active`` schedule is dropped.
    b4..b6 are all "never active" → group B keeps its schedule.
    """
    raw = json.loads(json.dumps(two_clusters_case.raw))
    raw["options"] = {"input_directory": "."}
    buses = raw["system"]["bus_array"]
    buses[0]["active"] = "old_active"
    for b in buses[3:]:
        b["active"] = "dead_active"
    bus_dir = tmp_path / "Bus"
    bus_dir.mkdir()
    pd.DataFrame({"stage": [1, 2], "uid": [1, 1], "value": [1, 0]}).to_parquet(
        bus_dir / "old_active.parquet"
    )
    pd.DataFrame(
        {"stage": [1, 2] * 3, "uid": [4, 4, 5, 5, 6, 6], "value": [0] * 6}
    ).to_parquet(bus_dir / "dead_active.parquet")
    path = tmp_path / "case.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    groups = {1: "A", 2: "A", 3: "A", 4: "B", 5: "B", 6: "B"}
    result = reduce_case(
        load_case(path),
        ReduceConfig(target_buses=0, bus_groups=groups, parquet_case_dir=str(tmp_path)),
    )
    by_uid = {int(b["uid"]): b for b in result.case.array("bus_array")}
    assert "active" not in by_uid[1]
    assert by_uid[4]["active"] == "dead_active"
