# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt.line_lift_report`."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pandas as pd
import pytest

from plexos2gtopt import line_lift_report as llr


def test_per_block_shapes() -> None:
    assert llr._per_block(None, [1]) is None
    assert llr._per_block(5, [1, 2]) == 5.0
    # Non-numeric leaves are ignored; length mismatch -> max.
    assert llr._per_block([[1, "x", 3]], [1]) == 3.0
    assert llr._per_block([["x"]], [1]) is None
    assert llr._per_block([[1, 2]], [7, 8]) == {7: 1.0, 8: 2.0}


def test_at() -> None:
    assert llr._at({1: 2.0}, 1) == 2.0
    assert llr._at({1: 2.0}, 3) is None
    assert llr._at(4.0, 9) == 4.0


def test_analyze_csv_and_flown(tmp_path: Path) -> None:
    line_dir = tmp_path / "Line"
    line_dir.mkdir()
    pd.DataFrame([(1, 1, 50.0), (2, 1, 1.0)], columns=["uid", "block", "value"]).to_csv(
        line_dir / "flow_sol.csv", index=False
    )
    pd.DataFrame([(1, 1, 30.0), (2, 1, 1.0)], columns=["uid", "block", "value"]).to_csv(
        line_dir / "flown_sol.csv", index=False
    )
    pd.DataFrame([(1, 1, 2.0)], columns=["uid", "block", "value"]).to_csv(
        line_dir / "overloadn_sol.csv", index=False
    )
    case = {
        "simulation": {"block_array": [{"uid": 1}]},
        "system": {
            "line_array": [
                {"uid": 1, "name": "L1", "tmax_ab": 100, "tmax_ba": 60},
                # Zero rating -> utilisation is skipped.
                {"uid": 2, "tmax_ab": 0, "tmax_ba": 0},
            ]
        },
    }
    rows = llr.analyze(case, tmp_path, set())
    by = {r["line"]: r for r in rows}
    assert by["L1"]["max_util"] == pytest.approx(0.5)
    assert by["L1"]["overload_mwh"] == pytest.approx(2.0)
    assert by["2"]["max_util"] is None


def test_load_lifted_key_must_select_list(tmp_path: Path) -> None:
    js = tmp_path / "l.json"
    js.write_text(json.dumps({"common": {"x": 1}}))
    with pytest.raises(ValueError, match="must select a list"):
        llr.load_lifted(str(js), "common")


def test_main_plain_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case_json = tmp_path / "case.json"
    case_json.write_text(
        json.dumps(
            {
                "simulation": {"block_array": [{"uid": 1}]},
                "system": {"line_array": [{"uid": 1, "name": "L1"}]},
            }
        )
    )
    assert llr.main([str(case_json), str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "L1" in out and "candidate" not in out


def test_module_entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_json = tmp_path / "case.json"
    case_json.write_text(
        json.dumps({"simulation": {"block_array": []}, "system": {"line_array": []}})
    )
    monkeypatch.setattr(sys, "argv", ["x", str(case_json), str(tmp_path)])
    sys.modules.pop("plexos2gtopt.line_lift_report", None)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("plexos2gtopt.line_lift_report", run_name="__main__")
    assert exc.value.code == 0
