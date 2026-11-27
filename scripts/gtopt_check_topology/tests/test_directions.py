# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the ``--directions`` radial-line classification."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from gtopt_check_topology._directions import (
    directions_for_planning,
    referenced_lines,
    render_directions,
    side_verdict,
)
from gtopt_check_topology.main import main


def _line(uid: int, a: str, b: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "uid": uid,
        "name": f"l{uid}",
        "bus_a": a,
        "bus_b": b,
        "reactance": 0.1,
        "tmax_ab": 100.0,
        "tmax_ba": 100.0,
    }
    out.update(extra)
    return out


def _planning(
    buses: list[str],
    lines: list[dict[str, Any]],
    *,
    gens: list[dict[str, Any]] | None = None,
    demands: list[dict[str, Any]] | None = None,
    **system: Any,
) -> dict[str, Any]:
    sys_: dict[str, Any] = {
        "bus_array": [{"uid": i + 1, "name": n} for i, n in enumerate(buses)],
        "line_array": lines,
        "generator_array": gens or [],
        "demand_array": demands or [],
    }
    sys_.update(system)
    return {"system": sys_}


def _gen(uid: int, bus: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"uid": uid, "name": f"g{uid}", "bus": bus, "pmax": 50.0}
    out.update(extra)
    return out


def _dem(uid: int, bus: str) -> dict[str, Any]:
    return {"uid": uid, "name": f"d{uid}", "bus": bus, "lmax": 10.0}


def _verdicts(planning: dict[str, Any]) -> dict[str, str]:
    rep = directions_for_planning(planning, ".")
    return {s.name: s.verdict for s in rep.lines if s.radial}


@pytest.mark.parametrize(
    ("a", "b", "want"),
    [
        ("gen", "load", "ab"),
        ("mixed", "load", "ab"),
        ("gen", "mixed", "ab"),
        ("load", "gen", "ba"),
        ("mixed", "gen", "ba"),
        ("load", "mixed", "ba"),
        ("mixed", "mixed", "both"),
        ("empty", "mixed", "dead_end"),
        ("load", "load", "dead_end"),
        ("gen", "gen", "dead_end"),
    ],
)
def test_side_verdict(a: str, b: str, want: str) -> None:
    assert side_verdict(a, b) == want


def test_chain_one_way_and_dead_end() -> None:
    # g -- hub -- load, hub -- empty stub; l2 written load -> hub.
    p = _planning(
        ["G", "H", "L", "E"],
        [_line(1, "G", "H"), _line(2, "L", "H"), _line(3, "H", "E")],
        gens=[_gen(1, "G")],
        demands=[_dem(1, "L")],
    )
    assert _verdicts(p) == {"l1": "ab", "l2": "ba", "l3": "dead_end"}
    rep = directions_for_planning(p, ".")
    assert rep.n_radial == 3
    assert rep.n_one_way == 2
    assert len(rep.suggestions) == 3
    js = rep.to_json()
    assert js["summary"]["dead_end"] == 1


def test_ring_is_not_radial_and_stub_through_ring() -> None:
    p = _planning(
        ["A", "B", "C", "D"],
        [
            _line(1, "A", "B"),
            _line(2, "B", "C"),
            _line(3, "C", "A"),
            _line(4, "C", "D"),
        ],
        gens=[_gen(1, "A")],
        demands=[_dem(1, "B"), _dem(2, "D")],
    )
    assert _verdicts(p) == {"l4": "ab"}


def test_negative_pmin_battery_and_zero_pmax() -> None:
    lines = [_line(1, "A", "B")]
    # pmin < 0 at B: mixed; against a gen-only A it is still ab ...
    gens = [_gen(1, "A"), _gen(2, "B", pmin=-5.0)]
    p = _planning(["A", "B"], lines, gens=gens)
    assert _verdicts(p) == {"l1": "ab"}
    # ... and both against a mixed A.
    p = _planning(["A", "B"], lines, gens=gens, demands=[_dem(1, "A")])
    assert _verdicts(p) == {"l1": "both"}
    # Standalone battery at B: mixed; a gen-only A still forces ab.
    bat = [{"uid": 1, "name": "bat", "bus": "B", "capacity": 10.0}]
    p = _planning(["A", "B"], lines, gens=[_gen(1, "A")], battery_array=bat)
    assert _verdicts(p) == {"l1": "ab"}
    # ... and a mixed A (gen + load) makes it both.
    p = _planning(
        ["A", "B"],
        lines,
        gens=[_gen(1, "A")],
        demands=[_dem(1, "A")],
        battery_array=bat,
    )
    assert _verdicts(p) == {"l1": "both"}
    # pmax = 0 generator at B is ignored -> B empty -> dead end.
    p = _planning(["A", "B"], lines, gens=[_gen(1, "A"), _gen(2, "B", pmax=0.0)])
    assert _verdicts(p) == {"l1": "dead_end"}
    # ... unless expandable.
    p = _planning(
        ["A", "B"],
        lines,
        gens=[_gen(1, "A"), _gen(2, "B", pmax=0.0, expcap=10.0)],
        demands=[_dem(1, "A")],
    )
    assert _verdicts(p) == {"l1": "ba"}


def test_compressor_and_converter() -> None:
    lines = [_line(1, "A", "B")]
    p = _planning(
        ["A", "B"],
        lines,
        gens=[_gen(1, "A")],
        gas_compressor_array=[{"uid": 1, "name": "c", "bus": 2}],
    )
    assert _verdicts(p) == {"l1": "ab"}
    p = _planning(
        ["A", "B"],
        lines,
        gens=[_gen(1, "A")],
        demands=[_dem(1, "A")],
        carrier_converter_array=[
            {
                "uid": 1,
                "name": "p2g",
                "from_carrier": "electric",
                "from_node": "B",
                "to_carrier": "gas",
                "to_node": "n1",
            }
        ],
    )
    assert _verdicts(p) == {"l1": "both"}


def test_parallel_circuits_need_kvl() -> None:
    lines = [_line(1, "A", "B"), _line(2, "B", "A")]
    p = _planning(["A", "B"], lines, gens=[_gen(1, "A")], demands=[_dem(1, "B")])
    assert _verdicts(p) == {"l1": "ab", "l2": "ba"}
    # A parallel DC link (no reactance) allows circulation.
    lines[1].pop("reactance")
    assert _verdicts(p) == {"l1": "both", "l2": "both"}
    # So does a disabled Kirchhoff formulation.
    lines[1]["reactance"] = 0.1
    p["options"] = {"model_options": {"use_kirchhoff": False}}
    assert _verdicts(p) == {"l1": "both", "l2": "both"}


def test_inactive_line_and_candidate_loop() -> None:
    lines = [_line(1, "A", "B"), _line(2, "B", "C"), _line(3, "C", "A", active=0)]
    gens = [_gen(1, "A")]
    dems = [_dem(1, "B"), _dem(2, "C")]
    p = _planning(["A", "B", "C"], lines, gens=gens, demands=dems)
    assert _verdicts(p) == {"l1": "ab", "l2": "ab"}
    lines[2]["active"] = 1  # the candidate closes the loop
    assert not _verdicts(p)


def test_user_constraint_protection() -> None:
    p = _planning(
        ["A", "B"],
        [_line(1, "A", "B")],
        gens=[_gen(1, "A")],
        demands=[_dem(1, "B")],
        user_constraint_array=[
            {"uid": 1, "name": "u", "expression": 'line("l1").flow <= 5'}
        ],
    )
    rep = directions_for_planning(p, ".")
    assert rep.lines[0].protected
    assert not rep.suggestions
    ids, all_ = referenced_lines(p)
    assert ids == {"l1"}
    assert not all_


def test_referenced_lines_all_and_user_models() -> None:
    p = _planning(
        ["A"],
        [],
        user_model_array=[
            {"constraint_array": [{"expression": "sum(line(all).flow) <= 1"}]}
        ],
        line_commitment_array=[{"uid": 1, "line": "lx"}],
        user_constraint_array=[
            {"expression": 'gas_line("g").flow + LINE(\'a\', "b").flow <= 1'}
        ],
    )
    ids, all_ = referenced_lines(p)
    assert all_
    assert ids == {"a", "b", "lx"}


def test_render_and_cli(tmp_path: Path) -> None:
    p = _planning(
        ["G", "L"],
        [_line(1, "G", "L", flow_direction="ab")],
        gens=[_gen(1, "G")],
        demands=[_dem(1, "L")],
    )
    rep = directions_for_planning(p, ".")
    buf = io.StringIO()
    render_directions(rep, Console(file=buf, no_color=True, width=200), False)
    text = buf.getvalue()
    assert "1 one-way" in text
    assert "flow_direction = ab" in text

    case = tmp_path / "case.json"
    case.write_text(json.dumps(p), encoding="utf-8")
    out = tmp_path / "dirs.json"
    rc = main([str(case), "--no-color", "--quiet", "--directions-out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["lines"][0]["verdict"] == "ab"
