# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case tests for run_gtopt._attach (registry, liveness, attach loop)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from run_gtopt import _attach, _tui
from run_gtopt._attach import (
    RunInfo,
    _pid_alive,
    find_run_by_pid,
    list_runs,
    print_run_list,
    registry_dir,
    resolve_target,
    run_attach_loop,
)


@pytest.fixture(name="registry")
def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    rdir = tmp_path / "cache" / "gtopt" / "runs"
    rdir.mkdir(parents=True)
    return rdir


def test_registry_dir_without_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    assert registry_dir() == Path(".") / "gtopt" / "runs"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (None, True),
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError(), False),
    ],
)
def test_pid_alive_branches(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException | None, expected: bool
) -> None:
    def _kill(_pid: int, _sig: int) -> None:
        if exc is not None:
            raise exc

    monkeypatch.setattr(_attach.os, "kill", _kill)
    assert _pid_alive(1234) is expected
    assert _pid_alive(0) is False


def test_list_runs_skips_bad_entries(
    registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (registry / "subdir").mkdir()
    (registry / "notapid").write_text("/x", encoding="utf-8")
    (registry / "111").write_text("   ", encoding="utf-8")
    unreadable = registry / "222"
    unreadable.write_text("/y", encoding="utf-8")
    out = tmp_path / "case" / "output"
    out.mkdir(parents=True)
    (registry / "333").write_text(str(out), encoding="utf-8")

    real_read_text = Path.read_text

    def _read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == unreadable:
            raise OSError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    monkeypatch.setattr(_attach, "_pid_alive", lambda pid: pid == 333)
    runs = list_runs()
    assert [r.pid for r in runs] == [333]
    # No status file -> _enrich leaves the RunInfo untouched.
    assert runs[0].status is None
    assert runs[0].case_name is None


def test_list_runs_stale_unlink_error(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (registry / "444").write_text("/gone", encoding="utf-8")
    monkeypatch.setattr(_attach, "_pid_alive", lambda _pid: False)

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError(f"busy {self} {missing_ok}")

    monkeypatch.setattr(Path, "unlink", _unlink)
    assert not list_runs()
    assert (registry / "444").exists()


def test_find_run_by_pid_read_error(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (registry / "555").write_text("/x", encoding="utf-8")

    def _raise(self: Path, *_a: Any, **_k: Any) -> str:
        raise OSError(str(self))

    monkeypatch.setattr(Path, "read_text", _raise)
    assert find_run_by_pid(555) is None


def test_resolve_target_other_file_and_plain_dir(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("", encoding="utf-8")
    assert resolve_target(str(other)) is None

    plain = tmp_path / "plain"
    plain.mkdir()
    info = resolve_target(str(plain))
    assert info is not None
    assert info.output_dir == plain
    assert info.pid == -1
    assert info.alive is False


def test_resolve_target_special_file(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert resolve_target(str(fifo)) is None


def test_resolve_target_embedded_pid(tmp_path: Path) -> None:
    out = tmp_path / "c" / "output"
    out.mkdir(parents=True)
    (out / "solver_status.json").write_text(
        json.dumps({"pid": os.getpid(), "status": "running"}), encoding="utf-8"
    )
    info = resolve_target(str(tmp_path / "c"))
    assert info is not None
    assert info.pid == os.getpid()
    assert info.alive is True
    assert info.case_name == "c"


# ---------------------------------------------------------------------------
# run_attach_loop
# ---------------------------------------------------------------------------


class _FakeDisplay:
    last: _FakeDisplay | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.quit_requested = threading.Event()
        self.events: list[Any] = []
        self.fail_final = False
        _FakeDisplay.last = self

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def print_final(self, rc: int) -> None:
        self.events.append(("final", rc))
        if self.fail_final:
            raise RuntimeError("terminal gone")


@pytest.fixture(name="display")
def _display(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDisplay]:
    monkeypatch.setattr(_tui, "SolverDisplay", _FakeDisplay)
    return _FakeDisplay


def test_attach_loop_quits_on_q(
    display: type[_FakeDisplay],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "case" / "output"
    out.mkdir(parents=True)
    monkeypatch.setattr(_attach, "_pid_alive", lambda _p: True)

    def _sleep(_s: float) -> None:
        assert display.last is not None
        display.last.quit_requested.set()

    monkeypatch.setattr(_attach.time, "sleep", _sleep)
    info = RunInfo(pid=42, output_dir=out, method="sddp")
    assert run_attach_loop(info) == 0
    disp = display.last
    assert disp is not None
    assert disp.kwargs["case_name"] == "case"
    assert disp.kwargs["solver_hint"] == "sddp"
    assert disp.events == ["start", "stop", ("final", 0)]
    assert "Attaching to gtopt pid=42" in capsys.readouterr().out


def test_attach_loop_solver_exits(
    display: type[_FakeDisplay],  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_attach, "_pid_alive", lambda _p: False)
    # output_dir parent missing -> case_dir falls back to output_dir.
    info = RunInfo(pid=7, output_dir=tmp_path / "nope" / "out", case_name="x")
    assert run_attach_loop(info) == 0
    assert "exited; detaching" in capsys.readouterr().out


def test_attach_loop_ctrl_c_and_print_final_error(
    display: type[_FakeDisplay],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _sleep(_s: float) -> None:
        assert display.last is not None
        display.last.fail_final = True
        raise KeyboardInterrupt

    monkeypatch.setattr(_attach.time, "sleep", _sleep)
    info = RunInfo(pid=-1, output_dir=tmp_path)
    assert run_attach_loop(info) == 0
    assert "Detaching (Ctrl-C)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# print_run_list
# ---------------------------------------------------------------------------


def test_print_run_list_formats(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    runs = [
        RunInfo(pid=1, output_dir=tmp_path, iteration=3, max_iterations=0),
        RunInfo(pid=2, output_dir=tmp_path, alive=False),
        RunInfo(
            pid=3,
            output_dir=tmp_path,
            iteration=2,
            max_iterations=9,
            gap=0.5,
            elapsed_s=3.7,
        ),
    ]
    print_run_list(runs)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "3" in lines[1]
    assert "2*" in lines[2]
    assert "2/9" in lines[3]
    assert "50.00" in lines[3]
    assert "3s" in lines[3]
    assert "stale entry" in out
