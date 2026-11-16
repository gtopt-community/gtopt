"""Tests for tools/gtopt_compare_csv.py (golden CSV comparator).

Pins the robustness contract used by the ``e2e_*_compare_*`` integration
tests: columns are matched by name, only golden columns are compared,
run-dependent telemetry columns are never compared, and new columns
appended to ``solution.csv`` do not break existing goldens.

Run:  cd tools && python -m pytest test_gtopt_compare_csv.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# pylint: disable=wrong-import-position
import gtopt_compare_csv  # noqa: E402

_GOLDEN = (
    "scene,phase,status,status_name,obj_value,kappa,max_kappa,gap,gap_change\n"
    "0,0,0,optimal,23163.424133184083,284,284,0,1\n"
)
_ACTUAL_NEW_COLS = (
    "scene,phase,status,status_name,obj_value,kappa,max_kappa,gap,gap_change,"
    "solve_ticks,solve_time_s,solve_calls,infeasible_count\n"
    "0,0,0,optimal,23163.424133184083,99,99,0,1,0.0123,0.004567,3,1\n"
)


def _compare(tmp_path, actual: str, expected: str, **kw):
    a = tmp_path / "actual.csv"
    e = tmp_path / "expected.csv"
    a.write_text(actual, encoding="utf-8")
    e.write_text(expected, encoding="utf-8")
    return gtopt_compare_csv.compare_csv(str(a), str(e), **kw)


def test_extra_appended_columns_are_ignored(tmp_path):
    errors, warnings = _compare(tmp_path, _ACTUAL_NEW_COLS, _GOLDEN, strict=True)
    assert not errors
    assert not warnings


def test_telemetry_columns_never_compared(tmp_path):
    golden = _ACTUAL_NEW_COLS
    actual = _ACTUAL_NEW_COLS.replace("0.0123,0.004567,3,1", "9.9,1.5,42,0")
    actual = actual.replace(",99,99,", ",1e9,1e9,")
    errors, warnings = _compare(tmp_path, actual, golden, strict=True)
    assert not errors
    assert not warnings


def test_skip_columns_cover_solution_telemetry():
    telemetry = "kappa max_kappa solve_ticks solve_time_s solve_calls infeasible_count"
    assert set(telemetry.split()) <= gtopt_compare_csv.SKIP_COLUMNS
    assert "obj_value" not in gtopt_compare_csv.SKIP_COLUMNS
    assert "status" not in gtopt_compare_csv.SKIP_COLUMNS


def _solution(obj: str, scale: str | None) -> str:
    header = "scene,phase,status,status_name,obj_value,kappa,gap"
    row = f"0,0,0,optimal,{obj},5,0"
    if scale is not None:
        header += ",solve_time_s,scale_objective"
        row += f",0.01,{scale}"
    return f"{header}\n{row}\n"


def test_obj_value_compared_in_physical_units(tmp_path):
    # Same physical objective, both sides declare scale_objective.
    golden = _solution("23163.424", "1000")
    actual = _solution("23163.424", "1")
    errors, warnings = _compare(tmp_path, actual, golden, strict=True)
    assert not errors
    assert not warnings


def test_legacy_raw_golden_is_rescaled(tmp_path):
    # Legacy golden without scale_objective holding the LP-raw objective
    # (obj / 1000) compares equal to the physical output.
    golden = _solution("23.163424", None)
    actual = _solution("23163.424", "1000")
    errors, warnings = _compare(tmp_path, actual, golden, strict=True)
    assert not errors
    assert not warnings
    # ... and a legacy golden already in physical units also matches.
    golden = _solution("23163.424", None)
    errors, _ = _compare(tmp_path, actual, golden, strict=True)
    assert not errors


def test_rescaled_comparison_catches_real_objective_change(tmp_path):
    # A 1 % objective change is caught after rescaling a legacy raw golden...
    golden = _solution("23.163424", None)
    actual = _solution("23395.058", "1000")
    errors, _ = _compare(tmp_path, actual, golden, strict=True)
    assert any("Objective mismatch" in e for e in errors)
    # ... and between two scale-aware files, even in non-strict mode
    # (the optimal objective is unique, unlike degenerate primals).
    golden = _solution("23163.424", "1000")
    errors, _ = _compare(tmp_path, actual, golden, strict=False)
    assert any("Objective mismatch" in e for e in errors)
    # A scale-aware golden is NOT re-interpreted: a raw-unit actual fails.
    actual = _solution("23.163424", "1000")
    errors, _ = _compare(tmp_path, actual, golden, strict=False)
    assert any("Objective mismatch" in e for e in errors)


def test_obj_value_mismatch_is_detected(tmp_path):
    actual = _ACTUAL_NEW_COLS.replace("23163.424133184083", "23164.5")
    errors, _ = _compare(tmp_path, actual, _GOLDEN, strict=False)
    assert any("obj_value" in e for e in errors)


def test_status_mismatch_is_error(tmp_path):
    actual = _ACTUAL_NEW_COLS.replace(",optimal,", ",infeasible,")
    errors, _ = _compare(tmp_path, actual, _GOLDEN)
    assert any("status_name" in e for e in errors)


def test_missing_golden_column_is_error(tmp_path):
    actual = "scene,phase,status,status_name,kappa\n0,0,0,optimal,1\n"
    errors, _ = _compare(tmp_path, actual, _GOLDEN)
    assert any("missing" in e and "obj_value" in e for e in errors)


def test_reordered_columns_match_by_name(tmp_path):
    golden = "a,b,c\n1,2,3\n"
    actual = "c,a,b\n3,1,2\n"
    errors, warnings = _compare(tmp_path, actual, golden, strict=True)
    assert not errors
    assert not warnings


def test_extra_uid_column_is_error(tmp_path):
    golden = '"scenario","stage","block","uid:1"\n1,1,1,5\n'
    actual = '"scenario","stage","block","uid:1","uid:2"\n1,1,1,5,7\n'
    errors, _ = _compare(tmp_path, actual, golden)
    assert any("uid:2" in e for e in errors)


def test_wide_values_within_tolerance(tmp_path):
    golden = '"scenario","stage","block","uid:1","uid:2"\n1,1,1,5,-0\n'
    actual = '"scenario","stage","block","uid:1","uid:2"\n1,1,1,5.0000000001,0\n'
    errors, warnings = _compare(tmp_path, actual, golden, strict=True)
    assert not errors
    assert not warnings


def test_line_count_mismatch_is_error(tmp_path):
    errors, _ = _compare(tmp_path, _GOLDEN + "0,1,0,optimal,1,1,1,0,1\n", _GOLDEN)
    assert any("Line count" in e for e in errors)


def test_dropped_skip_column_is_not_an_error(tmp_path):
    golden = "scene,obj_value,kappa\n0,5,99\n"
    actual = "scene,obj_value\n0,5\n"
    errors, warnings = _compare(tmp_path, actual, golden, strict=True)
    assert not errors
    assert not warnings
