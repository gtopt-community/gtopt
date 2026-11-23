# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Subprocess / log-tailing tests for run_gtopt._runner.

The ``subprocess`` module used by ``_runner`` is replaced by a fake so no
real process (and never a real solver) is launched.
"""

from __future__ import annotations

import io
import json
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Callable
from typing import Any

import pytest

from run_gtopt import _runner, _tui
from run_gtopt._runner import (
    _build_gtopt_cmd,
    _build_ld_library_path,
    _extract_runpath,
    _find_case_json,
    _resolve_log_dir,
    _resolve_log_dir_candidates,
    _run_batch,
    _run_interactive,
    _snapshot_gtopt_logs,
    _tail_log_to,
    _wait_for_new_log,
    _wait_for_new_log_multi,
    _watch_and_tail,
    _watch_and_tail_multi,
    check_solution,
    run_check_lp,
    run_gtopt,
    run_plexos2gtopt,
    run_plp2gtopt,
)


def _no_popen(cmd: list[str]) -> Any:
    raise AssertionError(f"unexpected Popen({cmd})")


class _FakeSubprocess:
    """Drop-in for the ``subprocess`` module as used by ``_runner``."""

    DEVNULL = -3
    STDOUT = -2

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.on_run: Callable[[list[str]], None] | None = None
        self.popen_factory: Callable[[list[str]], Any] = _no_popen

    def run(self, cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        self.calls.append((cmd, kwargs))
        if self.on_run is not None:
            self.on_run(cmd)
        return SimpleNamespace(
            returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )

    def Popen(self, cmd: list[str], **kwargs: Any) -> Any:  # noqa: N802  # pylint: disable=invalid-name
        self.calls.append((cmd, kwargs))
        return self.popen_factory(cmd)


@pytest.fixture(name="fake_sp")
def _fake_sp(monkeypatch: pytest.MonkeyPatch) -> _FakeSubprocess:
    fake = _FakeSubprocess()
    monkeypatch.setattr(_runner, "subprocess", fake)
    return fake


# ---------------------------------------------------------------------------
# Converters / command building
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rc", [0, 3])
def test_run_converters(fake_sp: _FakeSubprocess, tmp_path: Path, rc: int) -> None:
    fake_sp.returncode = rc
    assert run_plp2gtopt("plp2gtopt", tmp_path, tmp_path / "o", ["-y", "1"]) == rc
    assert fake_sp.calls[-1][0][-2:] == ["-y", "1"]
    assert run_plexos2gtopt("plexos2gtopt", tmp_path, tmp_path / "o") == rc
    assert run_plexos2gtopt("plexos2gtopt", tmp_path, tmp_path / "o", ["-q"]) == rc
    assert fake_sp.calls[-1][0] == [
        "plexos2gtopt",
        str(tmp_path),
        "-o",
        str(tmp_path / "o"),
        "-q",
    ]


def test_build_gtopt_cmd(tmp_path: Path) -> None:
    assert _build_gtopt_cmd("g", tmp_path) == ["g", str(tmp_path)]
    assert _build_gtopt_cmd("g", tmp_path, 4, "zstd", ["--x"]) == [
        "g",
        str(tmp_path),
        "--x",
    ]


# ---------------------------------------------------------------------------
# LD_LIBRARY_PATH / RUNPATH
# ---------------------------------------------------------------------------


def test_extract_runpath_parses_readelf(
    fake_sp: _FakeSubprocess, tmp_path: Path
) -> None:
    fake_sp.stdout = (
        " 0x1 (NEEDED) Shared library: [libc.so]\n"
        " 0x1d (RUNPATH) Library runpath: [/opt/a:$ORIGIN::/opt/b]\n"
    )
    assert _extract_runpath(tmp_path / "x.so") == ["/opt/a", "/opt/b"]


def test_extract_runpath_malformed_and_missing(
    fake_sp: _FakeSubprocess, tmp_path: Path
) -> None:
    fake_sp.stdout = "RPATH without brackets\n"
    assert not _extract_runpath(tmp_path / "x.so")

    def _raise(_cmd: list[str]) -> None:
        raise FileNotFoundError("readelf")

    fake_sp.on_run = _raise
    assert not _extract_runpath(tmp_path / "x.so")


def test_build_ld_library_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    plugins = bin_dir / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "libgtopt_solver_a.so").write_text("", encoding="utf-8")
    (plugins / "libgtopt_solver_b.so").write_text("", encoding="utf-8")
    solver_lib = tmp_path / "solver_lib"
    solver_lib.mkdir()
    monkeypatch.setattr(
        _runner,
        "_extract_runpath",
        lambda _so: [str(solver_lib), str(plugins)],
    )
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing")
    parts = _build_ld_library_path(str(bin_dir / "gtopt")).split(":")
    assert str(plugins.resolve()) in parts
    assert str(bin_dir.resolve()) in parts
    assert parts.count(str(solver_lib.resolve())) == 1
    assert parts[-1] == "/existing"

    monkeypatch.delenv("LD_LIBRARY_PATH")
    assert "/existing" not in _build_ld_library_path(str(bin_dir / "gtopt"))


# ---------------------------------------------------------------------------
# Case JSON / log-dir resolution
# ---------------------------------------------------------------------------


def test_find_case_json_variants(tmp_path: Path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("", encoding="utf-8")
    assert _find_case_json(txt) is None
    assert _find_case_json(tmp_path / "missing") is None
    js = tmp_path / "plan.json"
    js.write_text("{}", encoding="utf-8")
    assert _find_case_json(js) == js
    assert _find_case_json(tmp_path) == js
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _find_case_json(empty) is None


def test_resolve_log_dir_candidates_bad_json_and_file_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    case = tmp_path / "c"
    case.mkdir()
    bad = case / "c.json"
    bad.write_text("{oops", encoding="utf-8")
    cands = _resolve_log_dir_candidates(bad)
    assert cands[0] == (case / "output" / "logs").resolve()
    assert (tmp_path / "results" / "logs").resolve() in cands
    assert _resolve_log_dir(bad) == cands[0]


def test_resolve_log_dir_candidates_skips_uncreatable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    case = tmp_path / "c"
    case.mkdir()
    (case / "c.json").write_text(
        json.dumps({"options": {"log_directory": "blocker/logs"}}), encoding="utf-8"
    )
    (case / "blocker").write_text("file, not dir", encoding="utf-8")
    cands = _resolve_log_dir_candidates(case)
    assert (case / "blocker" / "logs").resolve() not in cands
    assert cands  # the remaining fallbacks survive


def test_resolve_log_dir_candidates_resolve_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(self: Path, strict: bool = False) -> Path:
        raise RuntimeError(f"loop {self} {strict}")

    monkeypatch.setattr(Path, "resolve", _boom)
    assert not _resolve_log_dir_candidates(tmp_path)
    monkeypatch.undo()


def test_resolve_log_dir_none_when_no_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_runner, "_resolve_log_dir_candidates", lambda _c: [])
    assert _resolve_log_dir(tmp_path) is None


# ---------------------------------------------------------------------------
# Snapshot / wait / tail
# ---------------------------------------------------------------------------


class _BadDir:
    """Path-like whose glob raises OSError."""

    def glob(self, _pat: str) -> Any:
        raise OSError("gone")


def test_snapshot_gtopt_logs(tmp_path: Path) -> None:
    (tmp_path / "gtopt_1.log").write_text("", encoding="utf-8")
    (tmp_path / "other.log").write_text("", encoding="utf-8")
    assert _snapshot_gtopt_logs(tmp_path) == {tmp_path / "gtopt_1.log"}
    assert _snapshot_gtopt_logs(_BadDir()) == set()  # type: ignore[arg-type]


def test_wait_for_new_log_returns_newest(tmp_path: Path) -> None:
    old = tmp_path / "gtopt_1.log"
    old.write_text("", encoding="utf-8")
    new = tmp_path / "gtopt_2.log"
    new.write_text("", encoding="utf-8")
    stop = threading.Event()
    assert _wait_for_new_log(tmp_path, {old}, stop, poll_interval=0.0) == new


def test_wait_for_new_log_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    monkeypatch.setattr(_runner.time, "sleep", lambda _s: stop.set())
    assert _wait_for_new_log(_BadDir(), set(), stop) is None  # type: ignore[arg-type]
    stop.clear()
    assert (
        _wait_for_new_log_multi([_BadDir()], {}, stop) is None  # type: ignore[list-item]
    )


def test_tail_log_to_reads_and_drains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "gtopt_1.log"
    log.write_text("a\nb\n", encoding="utf-8")
    stop = threading.Event()
    lines: list[str] = []

    def _sleep(_s: float) -> None:
        # Writer appends a late line, then signals stop -> drained.
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("late\n")
        stop.set()

    monkeypatch.setattr(_runner.time, "sleep", _sleep)
    _tail_log_to(log, lines.append, stop)
    assert lines == ["a\n", "b\n", "late\n"]


def test_tail_log_to_missing_file(tmp_path: Path) -> None:
    lines: list[str] = []
    _tail_log_to(tmp_path / "missing.log", lines.append, threading.Event())
    assert not lines


def test_watch_and_tail_variants(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    lines: list[str] = []
    # Stop already set -> nothing waited for, nothing tailed.
    _watch_and_tail(tmp_path, set(), lines.append, stop)
    _watch_and_tail_multi([tmp_path], {}, lines.append, stop)
    assert not lines

    log = tmp_path / "gtopt_5.log"
    log.write_text("x\n", encoding="utf-8")
    go = threading.Event()
    calls = {"n": 0}

    def _consume(line: str) -> None:
        lines.append(line)
        calls["n"] += 1
        go.set()

    # Tail a fresh file, then exit once stop is set after the first line.
    t = threading.Thread(
        target=_watch_and_tail, args=(tmp_path, set(), _consume, go), daemon=True
    )
    go.clear()
    t.start()
    t.join(timeout=10)
    assert lines == ["x\n"]

    lines.clear()
    go.clear()
    t = threading.Thread(
        target=_watch_and_tail_multi,
        args=([tmp_path], {tmp_path: set()}, _consume, go),
        daemon=True,
    )
    t.start()
    t.join(timeout=10)
    assert lines == ["x\n"]


# ---------------------------------------------------------------------------
# _run_batch
# ---------------------------------------------------------------------------


class _Recorder(io.StringIO):
    """stdout replacement that records writes (thread-safe enough here)."""


def test_run_batch_tails_new_log(
    fake_sp: _FakeSubprocess, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    case = tmp_path / "case"
    case.mkdir()
    out = _Recorder()
    monkeypatch.setattr(_runner.sys, "stdout", out)

    def _on_run(_cmd: list[str]) -> None:
        logs = case / "output" / "logs"
        (logs / "gtopt_1.log").write_text("solving...\n", encoding="utf-8")
        deadline = time.monotonic() + 10.0
        while "solving" not in out.getvalue() and time.monotonic() < deadline:
            time.sleep(0.01)

    fake_sp.on_run = _on_run
    fake_sp.returncode = 4
    assert _run_batch(["gtopt", str(case)], {}, case) == 4
    assert "solving...\n" in out.getvalue()
    assert fake_sp.calls[0][1]["stdout"] == _FakeSubprocess.DEVNULL


def test_run_batch_without_log_dirs(
    fake_sp: _FakeSubprocess, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_runner, "_resolve_log_dir_candidates", lambda _c: [])
    fake_sp.returncode = 2
    assert _run_batch(["gtopt"], {"A": "1"}, tmp_path) == 2
    assert "stdout" not in fake_sp.calls[0][1]


# ---------------------------------------------------------------------------
# _run_interactive
# ---------------------------------------------------------------------------


class _FakeDisplay:
    instances: list[_FakeDisplay] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.quit_requested = threading.Event()
        self.lines: list[str] = []
        self.events: list[Any] = []
        _FakeDisplay.instances.append(self)

    def add_log_line(self, line: str) -> None:
        self.lines.append(line)

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def print_final(self, rc: int) -> None:
        self.events.append(("final", rc))


class _FakeProc:
    def __init__(self, polls: list[int | None], display_quit: bool = False):
        self._polls = polls
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.display_quit = display_quit

    def __enter__(self) -> _FakeProc:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def poll(self) -> int | None:
        val = self._polls.pop(0) if self._polls else 0
        if val is not None:
            self.returncode = val
        elif self.display_quit:
            _FakeDisplay.instances[-1].quit_requested.set()
        return val

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.returncode = -sig

    def wait(self) -> int | None:
        return self.returncode


@pytest.fixture(name="fake_display")
def _fake_display(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDisplay]:
    _FakeDisplay.instances.clear()
    monkeypatch.setattr(_tui, "SolverDisplay", _FakeDisplay)
    monkeypatch.setattr(_runner.time, "sleep", lambda _s: None)
    return _FakeDisplay


def test_run_interactive_normal_exit(
    fake_sp: _FakeSubprocess,
    fake_display: type[_FakeDisplay],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    case = tmp_path / "mycase"
    case.mkdir()
    proc = _FakeProc([None, 0])
    fake_sp.popen_factory = lambda _cmd: proc
    rc = _run_interactive(["gtopt", str(case), "--solver", "highs"], {}, case)
    assert rc == 0
    disp = fake_display.instances[-1]
    assert disp.kwargs["case_name"] == "mycase"
    assert disp.kwargs["solver_hint"] == "highs"
    assert disp.events == ["start", "stop", ("final", 0)]
    assert not proc.signals


def test_run_interactive_quit_terminates(
    fake_sp: _FakeSubprocess,
    fake_display: type[_FakeDisplay],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_runner, "_resolve_log_dir_candidates", lambda _c: [])
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    proc = _FakeProc([None, None], display_quit=True)
    fake_sp.popen_factory = lambda _cmd: proc
    rc = _run_interactive(["gtopt", str(plan), "--solver"], {}, plan)
    assert proc.signals == [signal.SIGTERM]
    assert rc == -signal.SIGTERM
    disp = fake_display.instances[-1]
    assert disp.kwargs["case_name"] == "plan"
    assert disp.kwargs["solver_hint"] == ""


# ---------------------------------------------------------------------------
# run_gtopt dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interactive", [True, False])
def test_run_gtopt_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, interactive: bool
) -> None:
    seen: dict[str, Any] = {}

    def _runner_fn(name: str):
        def _fn(cmd: list[str], env: dict[str, str], case: Path) -> int:
            seen.update(name=name, cmd=cmd, env=env, case=case)
            return 9

        return _fn

    monkeypatch.setattr(_tui, "is_interactive", lambda: interactive)
    monkeypatch.setattr(_runner, "_run_interactive", _runner_fn("interactive"))
    monkeypatch.setattr(_runner, "_run_batch", _runner_fn("batch"))
    monkeypatch.setattr(_runner, "_build_ld_library_path", lambda _b: "/lib")
    monkeypatch.delenv("MALLOC_CONF", raising=False)
    assert run_gtopt("gtopt", tmp_path, extra_args=["-v"]) == 9
    assert seen["name"] == ("interactive" if interactive else "batch")
    assert seen["cmd"] == ["gtopt", str(tmp_path), "-v"]
    assert seen["env"]["LD_LIBRARY_PATH"] == "/lib"
    assert seen["env"]["MALLOC_CONF"] == "background_thread:true"


# ---------------------------------------------------------------------------
# run_check_lp / check_solution edge cases
# ---------------------------------------------------------------------------


def test_run_check_lp_invokes_checker(
    fake_sp: _FakeSubprocess,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import shutil  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(shutil, "which", lambda _n: "/bin/gtopt_check_lp")
    fake_sp.stdout = "row 1 infeasible\n"
    fake_sp.stderr = "warn\n"
    lps = [tmp_path / "error_1.lp", tmp_path / "error_2.lp"]
    run_check_lp(lps)
    out = capsys.readouterr().out
    assert "Analysing first error LP: error_1.lp" in out
    assert "  row 1 infeasible" in out
    assert "  warn" in out
    assert "(and 1 more)" in out
    assert fake_sp.calls[0][0][-2:] == ["--quiet", "--no-ai"]


def test_check_solution_parquet_and_errors(tmp_path: Path) -> None:
    import pyarrow as pa  # pylint: disable=import-outside-toplevel
    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    pq.write_table(
        pa.table({"status": [0], "obj_value": [5.0], "scale_objective": [1.0]}),
        tmp_path / "solution.parquet",
    )
    sol = check_solution(tmp_path)
    assert sol["status"] == 0
    assert sol["objective"] == pytest.approx(5.0)

    empty = tmp_path / "e"
    empty.mkdir()
    pq.write_table(
        pa.table({"status": pa.array([], pa.int64())}), empty / "solution.parquet"
    )
    assert not check_solution(empty)

    noobj = tmp_path / "n"
    noobj.mkdir()
    (noobj / "solution.csv").write_text("status\n0\n", encoding="utf-8")
    assert check_solution(noobj) == {"status": 0}


def test_check_solution_corrupt_parquet(tmp_path: Path) -> None:
    (tmp_path / "solution.parquet").write_bytes(b"not a parquet file")
    assert not check_solution(tmp_path)


def test_read_result_table_corrupt_parquet(tmp_path: Path) -> None:
    from run_gtopt._runner import _read_result_table  # pylint: disable=import-outside-toplevel

    (tmp_path / "x.parquet").write_bytes(b"garbage")
    assert _read_result_table(tmp_path, "x") is None


def test_resolve_log_dir_candidates_dedupes_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    cands = _resolve_log_dir_candidates(tmp_path)
    assert cands == [
        (tmp_path / "output" / "logs").resolve(),
        (tmp_path / "results" / "logs").resolve(),
    ]


def test_tail_log_to_drains_after_stop(tmp_path: Path) -> None:
    log = tmp_path / "gtopt_1.log"
    log.write_text("first\n", encoding="utf-8")

    class _StopAfterWrite:
        """is_set() appends a final line then reports stop (race window)."""

        def is_set(self) -> bool:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("final\n")
            return True

    lines: list[str] = []
    _tail_log_to(log, lines.append, _StopAfterWrite())  # type: ignore[arg-type]
    assert lines == ["first\n", "final\n"]
