# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fixed zonal reduction (``bus_groups`` / ``--bus-group-regex``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_reduce_network._io import Case
from gtopt_reduce_network._reduce import (
    ReduceConfig,
    bus_groups_from_csv,
    bus_groups_from_regex,
    busmap_from_groups,
    reduce_case,
)
from gtopt_reduce_network.main import main

_GROUPS = {1: "A", 2: "A", 3: "A", 4: "B", 5: "B", 6: "B"}


def test_two_groups_give_two_buses_and_one_corridor(two_clusters_case: Case) -> None:
    result = reduce_case(
        two_clusters_case, ReduceConfig(target_buses=0, bus_groups=dict(_GROUPS))
    )
    buses = result.case.array("bus_array")
    assert sorted(int(b["uid"]) for b in buses) == [1, 4]
    lines = result.case.array("line_array")
    # Only the A–B tie (uid 3, 50 MW) crosses the groups.
    assert len(lines) == 1
    assert float(lines[0]["tmax_ab"]) == pytest.approx(50.0)
    assert float(lines[0]["tmax_ba"]) == pytest.approx(50.0)
    cluster_of = {r.original_bus_uid: r.cluster_bus_uid for r in result.busmap}
    assert cluster_of == {1: 1, 2: 1, 3: 1, 4: 4, 5: 4, 6: 4}
    # Components follow their bus group.
    gens = {int(g["uid"]): g["bus"] for g in result.case.array("generator_array")}
    assert gens[1] in (1, "b1")
    assert gens[2] in (4, "b4")


def test_group_representative_prefers_anchor() -> None:
    rows = busmap_from_groups([1, 2, 3], {1: "A", 2: "A", 3: "A"}, (2,))
    assert {r.cluster_bus_uid for r in rows} == {2}


def test_missing_group_raises() -> None:
    with pytest.raises(ValueError, match="no group"):
        busmap_from_groups([1, 2], {1: "A"})


def test_regex_groups(two_clusters_case: Case) -> None:
    groups = bus_groups_from_regex(two_clusters_case, r"^(b)\d")
    assert set(groups.values()) == {"b"}
    with pytest.raises(ValueError, match="does not match"):
        bus_groups_from_regex(two_clusters_case, r"^b[1-3]$")


def test_csv_groups_accept_uid_and_name(
    two_clusters_case: Case, tmp_path: Path
) -> None:
    csv_path = tmp_path / "groups.csv"
    csv_path.write_text("bus,group\n1,A\nb2,A\n3,A\nb4,B\n5,B\n6,B\n", encoding="utf-8")
    assert bus_groups_from_csv(two_clusters_case, csv_path) == _GROUPS


def test_cli_bus_groups(two_clusters_case: Case, tmp_path: Path) -> None:
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(two_clusters_case.raw), encoding="utf-8")
    csv_path = tmp_path / "groups.csv"
    csv_path.write_text(
        "bus,group\n" + "".join(f"{u},{g}\n" for u, g in _GROUPS.items()),
        encoding="utf-8",
    )
    out = tmp_path / "two_node.json"
    with pytest.raises(SystemExit) as excinfo:
        main(["reduce", str(case_path), "--bus-groups", str(csv_path), "-o", str(out)])
    assert excinfo.value.code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["system"]["bus_array"]) == 2
    cfg = json.loads(
        out.with_name("two_node.reducer_config.json").read_text(encoding="utf-8")
    )
    assert cfg["bus_groups"]["1"] == "A"


def test_cli_bus_groups_reject_target(two_clusters_case: Case, tmp_path: Path) -> None:
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(two_clusters_case.raw), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "reduce",
                str(case_path),
                "--bus-group-regex",
                r"^(b)",
                "-K",
                "2",
                "-o",
                str(tmp_path / "x.json"),
            ]
        )
    assert excinfo.value.code == 1
