# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch tests for run_gtopt._sanitize (cascade levels and runtime fixes)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from run_gtopt._sanitize import (
    _validate_cascade_options,
    _validate_options,
    sanitize_json,
)


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "case.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_lp_names_valid_integer_kept() -> None:
    opts: dict[str, Any] = {"use_lp_names": 2}
    msgs = _validate_options(opts)
    assert not any("use_lp_names" in m for m in msgs)
    assert opts["use_lp_names"] == 2


def test_eps_within_range_no_message() -> None:
    opts: dict[str, Any] = {"solver_options": {"optimal_eps": 1e-6}}
    assert not any("optimal_eps" in m for m in _validate_options(opts))


def test_cascade_levels_not_a_list() -> None:
    msgs: list[str] = []
    _validate_cascade_options({"levels": {"a": 1}}, msgs)
    assert msgs == ["WARN: cascade_options.levels must be an array"]


def test_cascade_levels_absent() -> None:
    msgs: list[str] = []
    _validate_cascade_options({"other": 1}, msgs)
    assert not msgs


def test_cascade_level_fixes() -> None:
    levels: list[Any] = [
        "not-an-object",
        {
            "model_options": {"use_single_bus": True, "use_kirchhoff": True},
            "sddp_options": {"max_iterations": 5, "convergence_tol": 0.1},
            "solver": {"max_iterations": 3},
            "transition": {"optimality_dual_threshold": 0.5},
        },
        {
            "model_options": "x",
            "sddp_options": "x",
            "solver": "x",
            "transition": "x",
        },
    ]
    msgs: list[str] = []
    _validate_cascade_options({"level_array": levels}, msgs)
    assert msgs[0] == "WARN: cascade_options.levels[0] must be an object"
    assert "use_kirchhoff incompatible" in msgs[1]
    assert len(msgs) == 2
    assert levels[1]["model_options"]["use_kirchhoff"] is False
    assert levels[1]["sddp_options"]["max_iterations"] == 5


def test_sanitize_relative_dirs_become_absolute(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "options": {
                "input_directory": "in",
                "output_directory": "out",
                "output_compression": "zstd",
                "solver_options": {"threads": 4},
            }
        },
    )
    out = sanitize_json(
        path,
        compression="zstd",
        threads=4,
        output_directory="out",
        input_directory="in",
    )
    assert out == tmp_path / "case_sanitized.json"
    opts = json.loads(out.read_text(encoding="utf-8"))["options"]
    assert opts["input_directory"] == str((tmp_path / "in").resolve())
    assert opts["output_directory"] == str((tmp_path / "out").resolve())
    assert opts["output_compression"] == "zstd"
    assert opts["solver_options"]["threads"] == 4


def test_sanitize_overrides_and_export(tmp_path: Path) -> None:
    path = _write(tmp_path, {"options": {"input_directory": "/abs/in"}})
    export = tmp_path / "exported.json"
    out = sanitize_json(
        path,
        input_directory="/other/in",
        output_directory="/other/out",
        export_path=export,
    )
    assert out == export
    opts = json.loads(export.read_text(encoding="utf-8"))["options"]
    assert opts["input_directory"] == "/other/in"
    assert opts["output_directory"] == "/other/out"


def test_invalid_state_variable_lookup_mode_message() -> None:
    """Regression: the message used f-string brace escaping in a plain
    string literal and printed ``{{...}}``."""
    opts: dict[str, Any] = {"sddp_options": {"state_variable_lookup_mode": "bogus"}}
    msgs = _validate_options(opts)
    assert msgs == [
        "WARN: sddp state_variable_lookup_mode='bogus' not in "
        "{'warm_start', 'cross_phase'}"
    ]
