# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/check_expected_values.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import check_expected_values as cev


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _case(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    _write(out / "solution.csv", "scene,phase,status,obj_value\n0,1,0,8815.56\n")
    # Sharded long layout; stage 3 row missing in drain (sparse = 0).
    _write(
        out / "Reservoir" / "water_value_dual_s0_p1.csv",
        '"scenario","stage","block","uid","value"\n1,1,1,1,-1000\n',
    )
    _write(
        out / "Reservoir" / "water_value_dual_s0_p2.csv",
        '"scenario","stage","block","uid","value"\n1,2,2,1,-1000\n',
    )
    _write(
        out / "Reservoir" / "drain_sol.csv",
        '"scenario","stage","block","uid","value"\n1,1,1,1,469.4\n1,2,2,1,330.6\n',
    )
    # Wide layout.
    _write(
        out / "Junction" / "drain_sol.csv",
        '"scenario","stage","block","uid:1","uid:2"\n1,1,1,5,100\n1,2,2,,100\n',
    )
    return out


def _run(out: Path, tmp_path: Path, spec: dict) -> int:
    exp = tmp_path / "expected.json"
    exp.write_text(json.dumps({"c": spec}), encoding="utf-8")
    return cev.main(["--output-dir", str(out), "--expected", str(exp), "--case", "c"])


def test_passing_checks(tmp_path: Path) -> None:
    out = _case(tmp_path)
    spec = {
        "objective": 8815.56,
        "checks": [
            {
                "file": "Reservoir/water_value_dual",
                "uid": 1,
                "stage": 2,
                "value": -1000,
            },
            {"file": "Reservoir/water_value_dual", "uid": 1, "stage": 1, "max": -1},
            {"file": "Reservoir/drain_sol", "uid": 1, "stages": [1, 2], "sum": 800},
            {"file": "Reservoir/drain_sol", "uid": 1, "stage": 3, "value": 0},
            {"file": "Junction/drain_sol", "uid": 2, "stages": [1, 2], "sum": 200},
            {"file": "Junction/drain_sol", "uid": 1, "stage": 2, "min": 0},
        ],
    }
    assert _run(out, tmp_path, spec) == 0


def test_failures(tmp_path: Path) -> None:
    out = _case(tmp_path)
    wv = "Reservoir/water_value_dual"
    for spec in (
        {"objective": 1.0},
        {"checks": [{"file": wv, "uid": 1, "stage": 1, "value": -500}]},
        {"checks": [{"file": wv, "uid": 1, "stage": 1, "min": 0}]},
        {"checks": [{"file": wv, "uid": 1, "stage": 1, "max": -2000}]},
        {"checks": [{"file": "Nope/x_sol", "uid": 1, "stage": 1, "value": 0}]},
    ):
        assert _run(out, tmp_path, spec) == 1, spec


def test_missing_case_and_solution(tmp_path: Path) -> None:
    exp = tmp_path / "expected.json"
    exp.write_text("{}", encoding="utf-8")
    args = ["--output-dir", str(tmp_path), "--expected", str(exp)]
    assert cev.main([*args, "--case", "x"]) == 1
    assert _run(tmp_path / "empty", tmp_path, {"objective": 0.0}) == 1
