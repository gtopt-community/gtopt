# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for plexos2gtopt.line_lift_report (synthetic gtopt output)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from plexos2gtopt import line_lift_report as llr

CASE = {
    "simulation": {
        "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 2}]
    },
    "system": {
        "line_array": [
            {"uid": 1, "name": "A->B", "tmax_ab": 100, "tmax_ba": 100},
            {"uid": 2, "name": "C->D", "tmax_ab": [[50, 80]], "tmax_ba": 50},
            {"uid": 3, "name": "E->F", "tmax_ab": 10, "tmax_normal_ab": 5},
        ]
    },
}


def _write(out: Path, name: str, rows: list[tuple[int, int, float]]) -> None:
    (out / "Line").mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=["uid", "block", "value"])
    df.to_parquet(out / "Line" / f"{name}.parquet")


def test_analyze_ranks_by_dual(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "flow_sol",
        [(1, 1, 100.0), (1, 2, -40.0), (2, 1, 25.0), (2, 2, 80.0), (3, 1, 6.0)],
    )
    _write(tmp_path, "flow_cost", [(1, 1, -12.0), (2, 2, 3.0)])
    _write(tmp_path, "overloadp_sol", [(3, 2, 1.0)])
    rows = llr.analyze(CASE, tmp_path, {"C->D"})
    assert [r["line"] for r in rows] == ["A->B", "C->D", "E->F"]
    a, c, e = rows[0], rows[1], rows[2]
    assert a["max_util"] == 1.0 and a["blocks_at_cap"] == 1 and a["dual_max"] == 12.0
    assert c["rating_ab"] == 80.0 and c["blocks_at_cap"] == 1 and c["lifted"]
    assert e["max_util"] == 1.2 and e["overload_mwh"] == 2.0
    assert llr.suggest(rows, {"C->D"}, min_dual=1.0) == ["A->B"]


def test_analyze_missing_outputs(tmp_path: Path) -> None:
    rows = llr.analyze(CASE, tmp_path, set())
    assert all(r["max_util"] is None and r["dual_max"] == 0.0 for r in rows)


def test_load_lifted_forms(tmp_path: Path) -> None:
    assert llr.load_lifted(None) == set()
    assert llr.load_lifted("A->B, C->D") == {"A->B", "C->D"}
    txt = tmp_path / "l.txt"
    txt.write_text("A->B\n\nC->D\n")
    assert llr.load_lifted(str(txt)) == {"A->B", "C->D"}
    js = tmp_path / "l.json"
    js.write_text(json.dumps({"common": ["X"], "per_case": {"2026-04-07": ["Y", "Z"]}}))
    assert llr.load_lifted(str(js), "per_case.2026-04-07") == {"Y", "Z"}
    assert llr.load_lifted(str(js), "common") == {"X"}
    (tmp_path / "list.json").write_text('["Q"]')
    assert llr.load_lifted(str(tmp_path / "list.json")) == {"Q"}


def test_main_suggest(tmp_path: Path, capsys) -> None:
    case_json = tmp_path / "case.json"
    case_json.write_text(json.dumps(CASE))
    out = tmp_path / "out"
    _write(out, "flow_sol", [(1, 1, 100.0)])
    _write(out, "flow_cost", [(1, 1, 5.0)])
    rc = llr.main(
        [
            str(case_json),
            str(out),
            "--lifted",
            "C->D",
            "--suggest",
            "--json",
            str(tmp_path / "t.json"),
        ]
    )
    text = capsys.readouterr().out
    assert rc == 0
    assert "--lift-line-caps A->B,C->D" in text
    assert len(json.loads((tmp_path / "t.json").read_text())) == 3
