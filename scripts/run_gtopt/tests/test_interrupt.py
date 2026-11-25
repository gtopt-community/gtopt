# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for run_gtopt._interrupt (Ctrl-C escalation) and its wiring."""

from __future__ import annotations

import io
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from run_gtopt import _runner
from run_gtopt._interrupt import InterruptController


class _Proc:
    def __init__(self) -> None:
        self.signals: list[int] = []
        self.alive = True

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def poll(self) -> int | None:
        return None if self.alive else 0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _ctl(stop_ok: bool = True) -> tuple[InterruptController, _Proc, list[str], _Clock]:
    proc = _Proc()
    msgs: list[str] = []
    clock = _Clock()
    ctl = InterruptController(
        proc, lambda: stop_ok, msgs.append, grace_s=10.0, clock=clock
    )
    return ctl, proc, msgs, clock


def test_escalation_stop_term_kill() -> None:
    ctl, proc, msgs, _clock = _ctl()
    ctl.on_interrupt()
    assert ctl.stop_requested and not proc.signals
    assert "graceful stop requested" in msgs[-1]
    ctl.on_interrupt()
    assert proc.signals == [signal.SIGTERM] and ctl.forced == "terminated"
    assert "stopping…" in msgs[-1]
    ctl.on_interrupt()
    assert proc.signals[-1] == signal.SIGKILL and ctl.forced == "killed"
    ctl.on_interrupt()  # 4th+ keeps killing
    assert proc.signals[-1] == signal.SIGKILL


def test_stop_request_failure_is_reported() -> None:
    ctl, _proc, msgs, _clock = _ctl(stop_ok=False)
    ctl.on_interrupt()
    assert not ctl.stop_requested
    assert "could not write the stop request" in msgs[-1]


def test_sigterm_grace_then_sigkill() -> None:
    ctl, proc, msgs, clock = _ctl()
    ctl.tick()  # nothing armed
    ctl.terminate("q (quit)")
    ctl.terminate("again")  # idempotent
    assert proc.signals == [signal.SIGTERM]
    clock.now = 9.9
    ctl.tick()
    assert proc.signals == [signal.SIGTERM]
    clock.now = 10.0
    ctl.tick()
    assert proc.signals == [signal.SIGTERM, signal.SIGKILL]
    assert "still running 10 s after SIGTERM" in msgs[-1]


def test_no_sigkill_when_already_exited() -> None:
    ctl, proc, _msgs, clock = _ctl()
    ctl.terminate("x")
    proc.alive = False
    clock.now = 100.0
    ctl.tick()
    assert proc.signals == [signal.SIGTERM]


def test_installed_routes_signals_and_restores() -> None:
    ctl, proc, _msgs, _clock = _ctl()
    before_int = signal.getsignal(signal.SIGINT)
    before_term = signal.getsignal(signal.SIGTERM)
    with ctl.installed():
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
    assert ctl.stop_requested
    assert proc.signals == [signal.SIGTERM]
    assert signal.getsignal(signal.SIGINT) is before_int
    assert signal.getsignal(signal.SIGTERM) is before_term


def test_installed_off_main_thread_is_noop() -> None:
    ctl, _proc, _msgs, _clock = _ctl()
    seen: list[object] = []

    def _worker() -> None:
        with ctl.installed() as c:
            seen.append(c)

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert seen == [ctl]


def _fake_gtopt(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake_gtopt"
    script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def _interrupt_later(times: int, delay: float = 0.4) -> threading.Thread:
    def _run() -> None:
        for _ in range(times):
            time.sleep(delay)
            os.kill(os.getpid(), signal.SIGINT)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def test_batch_first_ctrl_c_is_graceful_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake SDDP loop: exits 0 once the stop-request file appears."""
    out = tmp_path / "output"
    out.mkdir()
    err = io.StringIO()
    monkeypatch.setattr(_runner.sys, "stderr", err)
    monkeypatch.setattr(_runner.sys, "stdout", io.StringIO())
    gtopt = _fake_gtopt(
        tmp_path,
        f"while [ ! -f {out}/sddp_stop_request.json ]; do sleep 0.05; done;"
        f' echo \'{{"status": "stopped", "iteration": 2}}\''
        f" > {out}/solver_status.json; exit 0",
    )
    t = _interrupt_later(1)
    rc = _runner._run_batch([gtopt], {}, tmp_path, out)  # pylint: disable=protected-access
    t.join()
    assert rc == 0
    text = err.getvalue()
    assert "graceful stop requested" in text
    assert "[||] Stopped on request" in text


def test_batch_ctrl_c_escalates_to_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gtopt that ignores the stop file and SIGTERM ends with SIGKILL."""
    err = io.StringIO()
    monkeypatch.setattr(_runner.sys, "stderr", err)
    monkeypatch.setattr(_runner.sys, "stdout", io.StringIO())
    gtopt = _fake_gtopt(tmp_path, "trap '' TERM; while true; do sleep 0.05; done")
    t = _interrupt_later(3, delay=0.3)
    rc = _runner._run_batch([gtopt], {}, tmp_path, tmp_path / "out")  # pylint: disable=protected-access
    t.join()
    assert rc == 128 + signal.SIGKILL
    text = err.getvalue()
    assert "sent SIGTERM" in text and "sent SIGKILL" in text
    assert "Interrupted — gtopt was killed" in text


def test_batch_stop_request_without_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_runner, "_resolve_log_dir_candidates", lambda _c, _o=None: [])
    err = io.StringIO()
    monkeypatch.setattr(_runner.sys, "stderr", err)
    gtopt = _fake_gtopt(tmp_path, "exec sleep 30")
    t = _interrupt_later(2, delay=0.3)
    rc = _runner._run_batch([gtopt], {}, tmp_path)  # pylint: disable=protected-access
    t.join()
    assert rc == 128 + signal.SIGTERM
    assert "could not write the stop request" in err.getvalue()
