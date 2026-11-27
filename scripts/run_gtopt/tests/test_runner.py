# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for solution checking, command building, and reporting."""

from pathlib import Path
from unittest.mock import patch

from run_gtopt._runner import (
    _read_result_table,
    check_solution,
    find_error_lp_files,
    report_solution,
    run_check_lp,
    run_gtopt,
    run_plp2gtopt,
)


_SOLUTION_HEADER = (
    "scene,phase,status,status_name,obj_value,kappa,max_kappa,gap,gap_change,"
    "solve_ticks,solve_time_s,solve_calls,infeasible_count,scale_objective\n"
)


def test_check_solution_csv(tmp_path: Path):
    """Parse the current solution.csv: obj_value is physical, not rescaled."""
    sol = tmp_path / "solution.csv"
    sol.write_text(
        _SOLUTION_HEADER + "0,0,0,optimal,12345.6,3,3,0,1,0.1,0.01,1,0,1000\n"
    )
    result = check_solution(tmp_path)
    assert result["status"] == 0
    assert abs(result["objective"] - 12345.6) < 1e-6
    assert result["scale_objective"] == 1000


def test_check_solution_reports_gtopt_build(tmp_path: Path):
    """The gtopt_version/gtopt_revision columns surface as ``gtopt_build``."""
    sol = tmp_path / "solution.csv"
    sol.write_text(
        _SOLUTION_HEADER.rstrip("\n")
        + ",gtopt_version,gtopt_revision\n"
        + "0,0,0,optimal,12345.6,3,3,0,1,0.1,0.01,1,0,1000,"
        + "1.1.0,v1.1.0-20-g5a4fd2ef8abc\n"
    )
    result = check_solution(tmp_path)
    assert result["gtopt_build"] == "1.1.0 (v1.1.0-20-g5a4fd2ef8abc)"
    assert abs(result["objective"] - 12345.6) < 1e-6


def test_check_solution_without_build_columns(tmp_path: Path):
    """Outputs older than the build columns have no ``gtopt_build``."""
    sol = tmp_path / "solution.csv"
    sol.write_text(
        _SOLUTION_HEADER + "0,0,0,optimal,12345.6,3,3,0,1,0.1,0.01,1,0,1000\n"
    )
    assert "gtopt_build" not in check_solution(tmp_path)


def test_check_solution_legacy_csv(tmp_path: Path):
    """Legacy solution.csv (no telemetry/scale columns): obj_value is LP-raw."""
    sol = tmp_path / "solution.csv"
    sol.write_text(
        "scene,phase,status,status_name,obj_value,kappa,gap,gap_change\n"
        "0,0,0,optimal,12.3456,1,0,1\n"
    )
    result = check_solution(tmp_path, fallback_scale=1000.0)
    assert abs(result["objective"] - 12345.6) < 1e-6


def test_check_solution_missing(tmp_path: Path):
    """Missing solution file returns empty dict."""
    result = check_solution(tmp_path)
    assert not result


def test_check_solution_malformed(tmp_path: Path):
    """Malformed CSV returns empty or partial dict."""
    sol = tmp_path / "solution.csv"
    sol.write_text("garbage\n")
    result = check_solution(tmp_path)
    assert isinstance(result, dict)


def test_check_solution_non_optimal(tmp_path: Path):
    """Parse solution with non-optimal status."""
    sol = tmp_path / "solution.csv"
    sol.write_text(
        _SOLUTION_HEADER + "0,0,1,infeasible,0.0,-1,-1,0,1,0.1,0.01,1,1,1000\n"
    )
    result = check_solution(tmp_path)
    assert result["status"] == 1


def test_report_solution_empty_dir(tmp_path: Path, capsys):
    """report_solution on empty directory does not crash."""
    report_solution(tmp_path)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "NO SOLUTION FILE" in output


def test_report_solution_optimal(tmp_path: Path, capsys):
    """report_solution shows OPTIMAL for status 0."""
    sol = tmp_path / "solution.csv"
    sol.write_text(_SOLUTION_HEADER + "0,0,0,optimal,500.0,3,3,0,1,0.1,0.01,1,0,1000\n")
    report_solution(tmp_path)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "OPTIMAL" in output
    assert "500" in output
    # The physical obj_value must not be multiplied by scale_objective again.
    assert "500,000" not in output


def test_run_gtopt_builds_command(tmp_path: Path, monkeypatch):
    """run_gtopt builds a minimal command (threads/compression are in JSON)."""
    import run_gtopt._tui as tui  # noqa: PLC0415

    monkeypatch.setattr(tui, "is_interactive", lambda: False)
    args_file = tmp_path / "args.txt"
    fake = tmp_path / "gtopt"
    fake.write_text(f'#!/bin/sh\necho "$@" > {args_file}\n', encoding="utf-8")
    fake.chmod(0o755)
    with patch("run_gtopt._runner._resolve_log_dir_candidates", return_value=[]):
        rc = run_gtopt(str(fake), tmp_path, threads=4, compression="zstd")
    assert rc == 0
    cmd = args_file.read_text(encoding="utf-8").split()
    assert cmd == [str(tmp_path)]
    # threads and compression are baked into the sanitized JSON,
    # not passed as CLI flags
    assert "--lp-threads" not in cmd
    assert "--output-compression" not in cmd


def test_run_plp2gtopt_builds_command(tmp_path: Path):
    """run_plp2gtopt passes input/output dirs to subprocess."""
    with patch("run_gtopt._runner.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        rc = run_plp2gtopt("/usr/bin/plp2gtopt", tmp_path / "in", tmp_path / "out")
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "-o" in cmd


def test_find_error_lp_files_empty(tmp_path: Path):
    """No error LP files in empty directory."""
    assert not find_error_lp_files(tmp_path)


def test_find_error_lp_files_found(tmp_path: Path):
    """Finds all error*.lp files sorted by mtime."""
    logs = tmp_path / "logs"
    logs.mkdir()
    import time

    for i in range(5):
        f = logs / f"error_{i}_0.lp"
        f.write_text(f"lp{i}")
        # Ensure distinct mtimes
        time.sleep(0.01)
    result = find_error_lp_files(logs)
    assert len(result) == 5
    assert all(f.name.startswith("error") for f in result)
    # Oldest first
    assert result[0].name == "error_0_0.lp"


def test_find_error_lp_files_nonexistent():
    """Non-existent directory returns empty list."""
    assert not find_error_lp_files(Path("/no/such/dir"))


def test_find_error_lp_files_compressed(tmp_path: Path):
    """Finds compressed error LP files (.lp.gz, .lp.zst)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "error_0_0.lp.gz").write_text("")
    (logs / "error_1_0.lp.zst").write_text("")
    result = find_error_lp_files(logs)
    assert len(result) == 2


def test_run_check_lp_no_files(capsys):
    """run_check_lp with empty list does nothing."""
    run_check_lp([])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_run_check_lp_prints_instructions(tmp_path: Path, capsys):
    """run_check_lp prints user instructions even without the tool."""
    logs = tmp_path / "logs"
    logs.mkdir()
    lp = logs / "error_0_0.lp"
    lp.write_text("min: x;\n")
    with patch("shutil.which", return_value=None):
        run_check_lp([lp])
    captured = capsys.readouterr()
    assert "Error LP Diagnostics" in captured.out
    assert "gtopt_check_lp" in captured.out
    assert str(logs) in captured.out


def test_read_result_table_single_parquet(tmp_path: Path):
    """Legacy single-file parquet layout is read as one frame."""
    import pandas as pd  # noqa: PLC0415

    subdir = tmp_path / "Generator"
    subdir.mkdir()
    pd.DataFrame({"uid:1": [1.0, 2.0]}).to_parquet(subdir / "generation_sol.parquet")
    df = _read_result_table(tmp_path, "Generator/generation_sol")
    assert df is not None
    assert len(df) == 2


def test_read_result_table_parquet_hive_dataset(tmp_path: Path):
    """A hive-partitioned directory is read as one logical frame."""
    import pandas as pd  # noqa: PLC0415

    stem_dir = tmp_path / "Generator" / "generation_sol.parquet"
    for scene, phase, val in [(0, 0, 1.0), (0, 1, 2.0), (1, 0, 3.0)]:
        part = stem_dir / f"scene={scene}" / f"phase={phase}"
        part.mkdir(parents=True)
        pd.DataFrame({"uid:1": [val]}).to_parquet(part / "part.parquet")
    df = _read_result_table(tmp_path, "Generator/generation_sol")
    assert df is not None
    assert sorted(df["uid:1"].tolist()) == [1.0, 2.0, 3.0]
    assert {"scene", "phase"}.issubset(df.columns)


def test_read_result_table_csv_shards(tmp_path: Path):
    """CSV shards are concatenated in sorted order."""
    import pandas as pd  # noqa: PLC0415

    subdir = tmp_path / "Flow"
    subdir.mkdir()
    pd.DataFrame({"uid:1": [10.0]}).to_csv(subdir / "flow_sol_s0_p0.csv", index=False)
    pd.DataFrame({"uid:1": [20.0]}).to_csv(subdir / "flow_sol_s1_p0.csv", index=False)
    df = _read_result_table(tmp_path, "Flow/flow_sol")
    assert df is not None
    assert len(df) == 2


def test_read_result_table_missing(tmp_path: Path):
    """Missing stem returns None."""
    assert _read_result_table(tmp_path, "Nope/missing") is None


# ---------------------------------------------------------------------------
# _resolve_log_dir — must follow output_directory / log_directory from JSON
# ---------------------------------------------------------------------------


def _write_case_json(tmp_path: Path, options: dict) -> Path:
    """Helper: write a minimal case JSON with the given options."""
    import json as _json

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    json_path = case_dir / "case.json"
    json_path.write_text(_json.dumps({"options": options}, indent=2))
    return case_dir


def test_resolve_log_dir_default_output_directory(tmp_path: Path):
    """Without output_directory in JSON, falls back to ``output/logs``."""
    from run_gtopt._runner import _resolve_log_dir  # noqa: PLC0415

    case_dir = _write_case_json(tmp_path, {})
    log_dir = _resolve_log_dir(case_dir)
    assert log_dir is not None
    assert log_dir == (case_dir / "output" / "logs").resolve()
    assert log_dir.is_dir()


def test_resolve_log_dir_honours_output_directory(tmp_path: Path):
    """When the JSON sets output_directory='results' (the plp2gtopt
    convention) the runner must poll <case>/results/logs — not the
    hard-coded <case>/output/logs.  Regression for the "Waiting for
    solver output…" hang seen on a production PLP case."""
    from run_gtopt._runner import _resolve_log_dir  # noqa: PLC0415

    case_dir = _write_case_json(tmp_path, {"output_directory": "results"})
    log_dir = _resolve_log_dir(case_dir)
    assert log_dir is not None
    assert log_dir == (case_dir / "results" / "logs").resolve()
    assert log_dir.is_dir()


def test_resolve_log_dir_honours_explicit_log_directory(tmp_path: Path):
    """An explicit log_directory wins over output_directory (matches the
    C++ setup_file_logging precedence)."""
    from run_gtopt._runner import _resolve_log_dir  # noqa: PLC0415

    case_dir = _write_case_json(
        tmp_path,
        {"output_directory": "results", "log_directory": "custom/logs"},
    )
    log_dir = _resolve_log_dir(case_dir)
    assert log_dir is not None
    assert log_dir == (case_dir / "custom" / "logs").resolve()


def test_resolve_log_dir_no_json_falls_back_to_default(tmp_path: Path):
    """No JSON in the case dir → keep the legacy default."""
    from run_gtopt._runner import _resolve_log_dir  # noqa: PLC0415

    case_dir = tmp_path / "no-json-here"
    case_dir.mkdir()
    log_dir = _resolve_log_dir(case_dir)
    assert log_dir is not None
    assert log_dir == (case_dir / "output" / "logs").resolve()


def test_resolve_log_dir_candidates_includes_jsonpath_and_fallbacks(
    tmp_path: Path,
):
    """The multi-candidate resolver returns the JSON-resolved path
    plus the legacy ``output/logs`` fallback (and CWD-relative ones),
    so the TUI watcher sees gtopt's actual log file regardless of
    which side resolved ``output_directory`` first."""
    from run_gtopt._runner import _resolve_log_dir_candidates  # noqa: PLC0415

    case_dir = _write_case_json(tmp_path, {"output_directory": "results"})
    candidates = _resolve_log_dir_candidates(case_dir)
    # First candidate: the JSON-honoured path (primary).
    assert candidates[0] == (case_dir / "results" / "logs").resolve()
    # Legacy default must also be in the list — that's the bug this
    # fix addresses.
    assert (case_dir / "output" / "logs").resolve() in candidates


def test_wait_for_new_log_multi_picks_first_dir_to_get_a_file(
    tmp_path: Path,
):
    """The TUI watcher must accept new files in any candidate dir.
    Regression for the silent hang seen when JSON-resolved dir and
    gtopt's actual write target diverged."""
    import threading as _threading  # noqa: PLC0415
    from run_gtopt._runner import _wait_for_new_log_multi  # noqa: PLC0415

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    baselines = {dir_a: set(), dir_b: set()}
    stop = _threading.Event()
    expected = dir_b / "gtopt_42.log"
    timer = _threading.Timer(0.05, lambda: expected.write_text(""))
    timer.start()
    try:
        result = _wait_for_new_log_multi(
            [dir_a, dir_b], baselines, stop, poll_interval=0.01
        )
    finally:
        timer.cancel()
        stop.set()
    assert result is not None
    assert result.resolve() == expected.resolve()
