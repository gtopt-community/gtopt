# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for run_gtopt._process (gtopt child, capture, failure details)."""

from __future__ import annotations

import io
import signal
import sys
from pathlib import Path

import pytest

from run_gtopt import _process, _runner
from run_gtopt._process import (
    ErrorCollector,
    RunResult,
    SolverProcess,
    describe_exit,
    failure_details,
    is_error_line,
    shell_exit_code,
)


def _sh(body: str) -> list[str]:
    return ["/bin/sh", "-c", body]


def test_is_error_line() -> None:
    assert is_error_line("[2026] [critical] boom")
    assert is_error_line("gtopt: [error] bad")
    assert not is_error_line("[info] error_lp=false failed=0")


@pytest.mark.parametrize(
    ("rc", "expected"),
    [
        (None, "still running"),
        (0, "exit code 0 (success)"),
        (3, "exit code 3 (internal error)"),
        (42, "exit code 42"),
        (-signal.SIGKILL, "killed by SIGKILL (out of memory"),
        (128 + signal.SIGABRT, "killed by SIGABRT (abort"),
        (-signal.SIGSEGV, "killed by SIGSEGV (crash"),
        (-signal.SIGTERM, "killed by SIGTERM"),
        (-200, "killed by signal 200"),
    ],
)
def test_describe_exit(rc: int | None, expected: str) -> None:
    assert describe_exit(rc).startswith(expected)


def test_shell_exit_code() -> None:
    assert shell_exit_code(None) == 1
    assert shell_exit_code(0) == 0
    assert shell_exit_code(3) == 3
    assert shell_exit_code(-signal.SIGTERM) == 128 + signal.SIGTERM


def test_error_collector_dedupes_stderr_and_log_copies() -> None:
    col = ErrorCollector()
    col.feed("gtopt: [critical] Directory 'x' given")
    col.feed("[2026-11-25 02:36:16.304] [critical] Directory 'x' given")
    assert col.lines == ["gtopt: [critical] Directory 'x' given"]


def test_error_collector_dedupes_and_limits() -> None:
    col = ErrorCollector(limit=2)
    for line in ["[error] a\n", "[info] x", "[error] a", "[critical] b", "[error] c"]:
        col.feed(line)
    assert col.lines == ["[error] a", "[critical] b"]


def test_solver_process_captures_lazily(tmp_path: Path) -> None:
    echo = io.StringIO()
    seen: list[str] = []
    proc = SolverProcess(
        _sh("echo out; echo '[critical] bad' >&2; exit 2"),
        {},
        capture_dir=tmp_path / "logs",
        echo=echo,
        on_line=seen.append,
    )
    proc.start()
    assert proc.wait() == 2
    assert proc.poll() == 2
    assert proc.returncode == 2
    assert seen == ["out", "[critical] bad"]
    assert echo.getvalue() == "out\n[critical] bad\n"
    assert proc.capture_path is not None
    assert proc.capture_path.read_text(encoding="utf-8") == "out\n[critical] bad\n"
    res = proc.result(log_path=tmp_path / "gtopt_1.log", stop_requested=True)
    assert res.errors == ["[critical] bad"]
    assert res.tail == ["out", "[critical] bad"]
    assert res.stop_requested
    assert proc.elapsed >= 0.0
    proc.send_signal(signal.SIGTERM)  # exited: no-op


def test_solver_process_silent_run_creates_no_file(tmp_path: Path) -> None:
    proc = SolverProcess(_sh("exit 0"), {}, capture_dir=tmp_path / "logs")
    assert proc.returncode is None
    proc.start()
    assert proc.wait() == 0
    assert proc.capture_path is None
    assert not (tmp_path / "logs").exists()


def test_solver_process_unwritable_capture_dir(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    proc = SolverProcess(_sh("echo x"), {}, capture_dir=blocker / "logs")
    proc.start()
    assert proc.wait() == 0
    assert proc.capture_path is None
    assert list(proc.tail) == ["x"]


def test_solver_process_signal_and_new_session(tmp_path: Path) -> None:
    proc = SolverProcess(_sh("exec sleep 30"), {}, capture_dir=None)
    proc.start()
    assert proc.proc is not None
    import os  # noqa: PLC0415

    assert os.getsid(proc.proc.pid) != os.getsid(0)  # own session
    proc.send_signal(signal.SIGTERM)
    assert proc.wait() == -signal.SIGTERM


def test_solver_process_without_preexec_off_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_process.sys, "platform", "darwin")
    proc = SolverProcess(_sh("exit 0"), {})
    proc.start()
    assert proc.wait() == 0


def test_set_parent_death_signal_is_harmless() -> None:
    _process._set_parent_death_signal()  # pylint: disable=protected-access


def test_failure_details_variants(tmp_path: Path) -> None:
    res = RunResult(
        returncode=3,
        errors=["[critical] bad"],
        tail=["", "[critical] bad", "loading plugin"],
        capture_path=tmp_path / "cap.log",
        log_path=tmp_path / "gtopt_1.log",
    )
    lines = failure_details(res)
    assert lines[0] == "Errors reported by gtopt:"
    assert "  [critical] bad" in lines
    assert "Last solver output:" in lines
    assert "  loading plugin" in lines
    assert lines.count("  [critical] bad") == 1
    assert lines[-1] == "  loading plugin"
    assert "Last solver output:" not in failure_details(res, include_tail=False)
    assert failure_details(RunResult(returncode=-9)) == [
        "gtopt printed nothing on stdout/stderr."
    ]


def test_runners_report_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    missing = str(tmp_path / "no_such_gtopt")
    monkeypatch.chdir(tmp_path)
    assert _runner._run_batch([missing], {}, tmp_path) == 127  # pylint: disable=protected-access
    assert "cannot start gtopt" in capsys.readouterr().err

    class _Disp:
        def __init__(self, **_kw: object) -> None:
            self.add_log_line = lambda _l: None
            self.set_log_path = lambda _p: None

    from run_gtopt import _tui  # noqa: PLC0415

    monkeypatch.setattr(_tui, "SolverDisplay", _Disp)
    assert _runner._run_interactive([missing], {}, tmp_path) == 127  # pylint: disable=protected-access
    assert "cannot start gtopt" in capsys.readouterr().err
    assert sys.platform  # keep import used


def test_solver_process_without_capture_dir() -> None:
    proc = SolverProcess(_sh("echo only-ring"), {}, capture_dir=None)
    proc.start()
    assert proc.wait() == 0
    assert proc.capture_path is None
    assert list(proc.tail) == ["only-ring"]


def test_found_log_list_notifies() -> None:
    seen: list[Path] = []
    found = _runner._FoundLog(seen.append)  # pylint: disable=protected-access
    found.append(Path("a.log"))
    plain = _runner._FoundLog()  # pylint: disable=protected-access
    plain.append(Path("b.log"))
    assert seen == [Path("a.log")] and list(found) == [Path("a.log")]
    assert list(plain) == [Path("b.log")]


def test_run_paths(tmp_path: Path) -> None:
    from run_gtopt._process import run_paths  # noqa: PLC0415

    assert not run_paths(RunResult(returncode=0))
    (tmp_path / "solver_status.json").write_text("{}", encoding="utf-8")
    res = RunResult(
        returncode=0,
        output_dir=tmp_path,
        log_path=tmp_path / "logs" / "gtopt_1.log",
        capture_path=tmp_path / "logs" / "cap.log",
    )
    assert [label for label, _ in run_paths(res)] == [
        "Output",
        "gtopt log",
        "Status",
        "Solver stdout/stderr",
    ]
    (tmp_path / "solver_status.json").unlink()
    assert "Status" not in [label for label, _ in run_paths(res)]


def test_error_lp_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from run_gtopt._process import error_lp_hint  # noqa: PLC0415

    assert not error_lp_hint(None)
    assert not error_lp_hint(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(5):
        (logs / f"error_{i}.lp.zst").write_text("", encoding="utf-8")
    lines = error_lp_hint(tmp_path)
    assert lines[0].startswith("5 error LP file(s)")
    assert "… and 2 more" in lines[-2]
    assert lines[-1].startswith("Diagnose: gtopt_check_lp ")
    assert lines[-1].endswith("--solver all")
    assert len(error_lp_hint(tmp_path, limit=10)) == 7

    def _boom(_self: Path, _pat: str) -> object:
        raise OSError("gone")

    monkeypatch.setattr(Path, "glob", _boom)
    assert not error_lp_hint(tmp_path)


def test_batch_final_lists_error_lps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from run_gtopt._runner import _print_batch_final  # noqa: PLC0415

    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "error_1.lp").write_text("", encoding="utf-8")
    _print_batch_final(RunResult(returncode=1, output_dir=tmp_path))
    err = capsys.readouterr().err
    assert "run_gtopt: 1 error LP file(s)" in err
    assert "run_gtopt: Output: " in err
