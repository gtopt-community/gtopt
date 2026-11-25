# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the plain-mode progress / heartbeat lines."""

import io
import json
import os
from pathlib import Path

import pytest

from run_gtopt._heartbeat import BatchProgress, iteration_line, progress_text


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_iteration_line_is_columnar() -> None:
    a = iteration_line(
        {
            "iteration": 3,
            "lower_bound": 1.0,
            "upper_bound": 2.0,
            "gap": 0.5,
            "iteration_s": 1.5,
        },
        100,
    )
    b = iteration_line(
        {
            "iteration": 12,
            "lower_bound": 123456.0,
            "upper_bound": 2e6,
            "gap": 0.01,
            "iteration_s": 10.0,
        },
        100,
    )
    assert a.startswith("iter   3/100") and b.startswith("iter  12/100")
    assert a.index("UB") == b.index("UB") and a.index("gap") == b.index("gap")
    assert "50.00%" in a
    c = iteration_line({"iteration": 1}, None)
    assert c.startswith("iter   1  ") and "?" in c


def test_progress_text_variants() -> None:
    assert progress_text({}) == "no solver status yet"
    sddp = {"iteration": 2, "max_iterations": 9, "gap": 0.1, "current_pass": 2}
    assert progress_text(sddp) == "iter 2/9, gap 10.00%, backward pass"
    assert progress_text({"iteration": 1, "max_iterations": 3}) == "iter 1/3, idle"
    mono = {"total_scenes": 4, "scenes_done": 1, "status": "running"}
    assert progress_text(mono) == "scenes 1/4, running"
    assert progress_text({"method": "x"}) == "running"


def test_batch_progress_iterations_and_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    clock = _Clock()
    out = io.StringIO()
    prog = BatchProgress(tmp_path, out, interval=30.0, clock=clock)
    prog.tick()
    assert out.getvalue() == ""
    status = tmp_path / "solver_status.json"
    hist = [{"iteration": 1, "gap": 0.5}]
    _write(status, {"max_iterations": 5, "iteration": 1, "history": hist})
    prog.tick()  # read throttled: same clock instant
    assert out.getvalue() == ""
    clock.t += 1.0
    prog.tick()
    assert out.getvalue().count("iter 1/5") == 1
    hist.append({"iteration": 2, "gap": 0.2})
    _write(status, {"max_iterations": 5, "iteration": 2, "gap": 0.2, "history": hist})
    prog.tick(force=True)
    prog.tick(force=True)  # no duplicates
    assert out.getvalue().count("iter 2/5") == 1
    clock.t += 30.0
    prog.tick()
    assert "[00:00:31] running — iter 2/5, gap 20.00%, idle" in out.getvalue()


def test_batch_progress_ignores_stale_bad_and_missing(tmp_path: Path) -> None:
    out = io.StringIO()
    status = tmp_path / "solver_status.json"
    _write(status, {"history": [{"iteration": 1}]})
    os.utime(status, (1.0, 1.0))
    BatchProgress(tmp_path, out, since=10.0).tick()
    assert out.getvalue() == ""
    status.write_text("{bad", encoding="utf-8")
    BatchProgress(tmp_path, out).tick()
    _write(status, [1, 2])
    prog = BatchProgress(tmp_path, out)
    prog.tick()
    assert not prog.status
    BatchProgress(None, out).tick()
    assert out.getvalue() == ""


def test_batch_progress_honours_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    clock = _Clock()
    out = io.StringIO()
    prog = BatchProgress(tmp_path, out, interval=1.0, clock=clock)
    clock.t += 5.0
    prog.tick()
    line = out.getvalue().rstrip("\n")
    assert len(line) == 40 and line.endswith("…")
