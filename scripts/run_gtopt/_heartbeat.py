# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Progress lines for plain (non-TUI) runs.

The batch runner streams gtopt's log, which can stay silent for minutes
inside one large solve.  :class:`BatchProgress` polls
``solver_status.json`` and prints to stderr

* one columnar line per new SDDP iteration::

      run_gtopt: iter   3/100  LB  1.23457e+06  UB  1.25000e+06  gap   1.25%  12.3s

* a heartbeat every :data:`HEARTBEAT_S` seconds with the elapsed time
  and the last known progress, so a CI log shows the run is alive.

Lines are cut to ``$COLUMNS`` when it is set.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from gtopt_shared.status import read_status

#: Seconds between heartbeat lines.
HEARTBEAT_S = 30.0

_PASS_NAMES = {0: "idle", 1: "forward pass", 2: "backward pass"}


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def iteration_line(rec: dict[str, Any], max_iterations: int | None) -> str:
    """Fixed-width summary of one ``history`` record."""
    it = rec.get("iteration", "?")
    width = len(str(max_iterations)) if max_iterations else 3
    head = f"iter {it:>{width}}/{max_iterations}" if max_iterations else f"iter {it:>3}"
    gap = rec.get("gap")
    gap_txt = f"{100.0 * gap:6.2f}%" if isinstance(gap, (int, float)) else "     ?"
    return (
        f"{head}  LB {rec.get('lower_bound', 0.0):12.6g}"
        f"  UB {rec.get('upper_bound', 0.0):12.6g}"
        f"  gap {gap_txt}  {rec.get('iteration_s', 0.0):6.1f}s"
    )


def progress_text(status: dict[str, Any]) -> str:
    """Short "where is it" text for the heartbeat."""
    if not status:
        return "no solver status yet"
    parts: list[str] = []
    max_it = status.get("max_iterations")
    if max_it:
        parts.append(f"iter {status.get('iteration', 0)}/{max_it}")
        gap = status.get("gap")
        if isinstance(gap, (int, float)):
            parts.append(f"gap {100.0 * gap:.2f}%")
        parts.append(_PASS_NAMES.get(status.get("current_pass", 0), "?"))
    elif status.get("total_scenes"):
        parts.append(f"scenes {status.get('scenes_done', 0)}/{status['total_scenes']}")
    if status.get("status"):
        parts.append(str(status["status"]))
    return ", ".join(parts) or "running"


class BatchProgress:
    """Poll ``solver_status.json`` and print iteration / heartbeat lines."""

    def __init__(
        self,
        output_dir: Path | None,
        out: TextIO,
        *,
        since: float = 0.0,
        interval: float = HEARTBEAT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._status_path = (
            output_dir / "solver_status.json" if output_dir is not None else None
        )
        self._out = out
        self._since = since
        self._interval = interval
        self._clock = clock
        self._start = clock()
        self._next_beat = self._start + interval
        self._printed_iters = 0
        self._next_read = self._start
        self.status: dict[str, Any] = {}

    def _read(self) -> None:
        path = self._status_path
        if path is None:
            return
        # None: missing, unreadable, mid-rewrite or left over from a
        # previous run (older than ``since``).
        data = read_status(path, since=self._since)
        if data is not None:
            self.status = data

    def _print(self, text: str) -> None:
        line = f"run_gtopt: {text}"
        cols = os.environ.get("COLUMNS", "")
        if cols.isdigit() and int(cols) > 10 and len(line) > int(cols):
            line = line[: int(cols) - 1] + "…"
        print(line, file=self._out, flush=True)

    def tick(self, force: bool = False) -> None:
        """Call periodically from the wait loop (the status file is read
        at most once a second unless *force*)."""
        now = self._clock()
        if force or now >= self._next_read:
            self._next_read = now + 1.0
            self._read()
        history = self.status.get("history") or []
        for rec in history[self._printed_iters :]:
            self._print(iteration_line(rec, self.status.get("max_iterations")))
        self._printed_iters = max(self._printed_iters, len(history))
        if now >= self._next_beat:
            self._next_beat = now + self._interval
            self._print(
                f"[{_fmt_elapsed(now - self._start)}] running — "
                f"{progress_text(self.status)}"
            )
