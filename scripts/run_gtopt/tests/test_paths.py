# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for run_gtopt._paths (gtopt output-directory resolution)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest

from run_gtopt._paths import (
    find_planning_json,
    remove_stale_run_files,
    resolve_output_dir,
)
from run_gtopt._runner import check_solution, report_solution
from run_gtopt._tui import _find_status_file


def _case(tmp_path: Path, opts: dict | None = None, name: str = "c") -> Path:
    case = tmp_path / name
    case.mkdir()
    (case / f"{name}.json").write_text(
        json.dumps({"options": opts or {}}), encoding="utf-8"
    )
    return case


def test_find_planning_json_variants(tmp_path: Path) -> None:
    case = _case(tmp_path)
    assert find_planning_json(case) == case / "c.json"
    assert find_planning_json(case / "c.json") == case / "c.json"
    txt = tmp_path / "x.txt"
    txt.write_text("", encoding="utf-8")
    assert find_planning_json(txt) is None
    assert find_planning_json(tmp_path / "missing") is None
    other = tmp_path / "other"
    other.mkdir()
    (other / "b_sanitized.json").write_text("{}", encoding="utf-8")
    assert find_planning_json(other) is None
    (other / "a.json").write_text("{}", encoding="utf-8")
    assert find_planning_json(other) == other / "a.json"


def test_default_is_output_next_to_json(tmp_path: Path) -> None:
    """Without a JSON value: ``results`` for a case directory (gtopt's
    directory-argument default), ``output`` next to a planning JSON."""
    case = _case(tmp_path)
    assert resolve_output_dir(case) == case / "results"
    assert resolve_output_dir(case / "c.json") == case / "output"


def test_json_output_directory_anchored_at_json(tmp_path: Path) -> None:
    case = _case(tmp_path, {"output_directory": "results"})
    assert resolve_output_dir(case) == case / "results"
    absolute = tmp_path / "abs_out"
    case2 = _case(tmp_path, {"output_directory": str(absolute)}, name="d")
    assert resolve_output_dir(case2) == absolute


@pytest.mark.parametrize(
    "extra",
    [
        ["-d", "cli_out"],
        ["--output-directory", "cli_out"],
        ["--output-directory=cli_out"],
    ],
)
def test_cli_override_is_cwd_relative(tmp_path: Path, extra: list[str]) -> None:
    case = _case(tmp_path, {"output_directory": "results"})
    cwd = tmp_path / "cwd"
    assert resolve_output_dir(case, extra, cwd=cwd) == cwd / "cli_out"
    abs_out = tmp_path / "abs"
    assert resolve_output_dir(case, ["-d", str(abs_out)], cwd=cwd) == abs_out


@pytest.mark.parametrize(
    "extra",
    [
        ["--set", "output_directory=set_out"],
        ["--set=output_directory=set_out"],
        ["--set", "other=1", "--set", "output_directory=set_out"],
    ],
)
def test_set_override_beats_json(tmp_path: Path, extra: list[str]) -> None:
    """``--set output_directory`` is relative to the CWD, like ``-d``."""
    case = _case(tmp_path, {"output_directory": "results"})
    cwd = tmp_path / "cwd"
    assert resolve_output_dir(case, extra, cwd=cwd) == cwd / "set_out"
    assert resolve_output_dir(case, ["-d", "cli", *extra], cwd=cwd) == cwd / "cli"


def test_dangling_flags_and_missing_json(tmp_path: Path) -> None:
    case = tmp_path / "nojson"
    case.mkdir()
    assert resolve_output_dir(case, ["-d"]) == case / "output"
    assert resolve_output_dir(case, ["--set"]) == case / "output"
    missing = tmp_path / "gone.json"
    assert resolve_output_dir(missing) == tmp_path / "output"


def test_bad_json_falls_back_to_default(tmp_path: Path) -> None:
    case = tmp_path / "bad"
    case.mkdir()
    (case / "bad.json").write_text("{not json", encoding="utf-8")
    assert resolve_output_dir(case) == case / "results"
    (case / "bad.json").write_text("[1, 2]", encoding="utf-8")
    assert resolve_output_dir(case / "bad.json") == case / "output"
    (case / "bad.json").write_text('{"options": {"output_directory": 3}}', "utf-8")
    assert resolve_output_dir(case) == case / "results"


def test_remove_stale_run_files_keeps_results_and_cuts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    out = tmp_path / "output"
    out.mkdir()
    keep = [out / "solution.csv", out / "sddp_cuts.csv", out / "cuts" / "x.csv"]
    keep[2].parent.mkdir()
    for p in keep:
        p.write_text("x", encoding="utf-8")
    (out / "solver_status.json").write_text("{}", encoding="utf-8")
    (out / "sddp_stop_request.json").write_text("{}", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        removed = remove_stale_run_files(out)
    assert sorted(p.name for p in removed) == [
        "sddp_stop_request.json",
        "solver_status.json",
    ]
    assert all(p.exists() for p in keep)
    assert "stop request left by a previous run" in caplog.text
    assert "removed stale" in caplog.text
    assert not remove_stale_run_files(out)
    assert not remove_stale_run_files(tmp_path / "missing")


def test_remove_stale_run_files_unlink_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "solver_status.json").write_text("{}", encoding="utf-8")

    def _boom(_self: Path, missing_ok: bool = False) -> None:
        raise OSError("ro")

    monkeypatch.setattr(Path, "unlink", _boom)
    assert not remove_stale_run_files(tmp_path)
    assert "could not remove" in caplog.text


_SOL = "scene,phase,status,obj_value,kappa\n0,0,0,5.0,1\n"


def test_check_solution_flags_stale_file(tmp_path: Path) -> None:
    sol = tmp_path / "solution.csv"
    sol.write_text(_SOL, encoding="utf-8")
    old = time.time() - 3600
    os.utime(sol, (old, old))
    res = check_solution(tmp_path, since=time.time())
    assert res["stale"] is True
    assert check_solution(tmp_path, since=old - 10)["status"] == 0


def test_check_solution_stat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "solution.csv").write_text(_SOL, encoding="utf-8")
    real_stat = Path.stat

    def _stat(self: Path, *a, **kw):
        if self.name == "solution.csv" and not kw and not a:
            raise OSError("gone")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", _stat)
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "solution.csv")
    assert not check_solution(tmp_path, since=0.0)


def test_report_solution_marks_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sol = tmp_path / "solution.csv"
    sol.write_text(_SOL, encoding="utf-8")
    old = time.time() - 3600
    os.utime(sol, (old, old))
    report_solution(tmp_path, since=time.time())
    cap = capsys.readouterr()
    assert "stale file from a previous run" in cap.out + cap.err


def test_tui_status_file_prefers_resolved_output_dir(tmp_path: Path) -> None:
    case = _case(tmp_path)
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "solver_status.json").write_text("{}", encoding="utf-8")
    assert _find_status_file(case, custom) == custom / "solver_status.json"
    # Before the file exists, the resolved dir is the polled target.
    assert _find_status_file(case, tmp_path / "later") == (
        tmp_path / "later" / "solver_status.json"
    )


def test_log_candidates_include_resolved_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from run_gtopt._runner import (  # noqa: PLC0415
        _resolve_log_dir_candidates,
    )

    monkeypatch.chdir(tmp_path)
    case = _case(tmp_path)
    out = tmp_path / "elsewhere"
    cands = _resolve_log_dir_candidates(case, out)
    assert cands[0] == (out / "logs").resolve()


def test_write_stop_request(tmp_path: Path) -> None:
    from run_gtopt._paths import write_stop_request  # noqa: PLC0415

    path = write_stop_request(tmp_path / "out", source="t")
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["source"] == "t"
    blocker = tmp_path / "f"
    blocker.write_text("", encoding="utf-8")
    assert write_stop_request(blocker / "out") is None


def test_relative_case_gives_absolute_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _case(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = resolve_output_dir(Path("c"))
    assert out.is_absolute()
    assert out == tmp_path / "c" / "results"


def test_latest_gtopt_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from run_gtopt._runner import latest_gtopt_log  # noqa: PLC0415

    assert latest_gtopt_log(tmp_path) is None
    logs = tmp_path / "logs"
    logs.mkdir()
    assert latest_gtopt_log(tmp_path) is None
    old, new = logs / "gtopt_1.log", logs / "gtopt_2.log"
    old.write_text("a", encoding="utf-8")
    new.write_text("b", encoding="utf-8")
    os.utime(old, (100.0, 100.0))
    os.utime(new, (200.0, 200.0))
    (logs / "run_gtopt_stderr_x.log").write_text("c", encoding="utf-8")
    assert latest_gtopt_log(tmp_path) == new
    assert latest_gtopt_log(tmp_path, since=150.0) == new
    assert latest_gtopt_log(tmp_path, since=300.0) is None
    other = tmp_path / "cwd" / "output" / "logs"
    other.mkdir(parents=True)
    newest = other / "gtopt_7.log"
    newest.write_text("d", encoding="utf-8")
    os.utime(newest, (250.0, 250.0))
    assert latest_gtopt_log(tmp_path, extra_dirs=(other,)) == newest
    assert latest_gtopt_log(tmp_path, extra_dirs=(tmp_path / "nope",)) == new

    def _boom(_self: Path, _pattern: str) -> Any:
        raise OSError("gone")

    monkeypatch.setattr(Path, "glob", _boom)
    assert latest_gtopt_log(tmp_path) is None
