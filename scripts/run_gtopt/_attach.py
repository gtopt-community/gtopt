# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Discovery and attach helpers for run_gtopt.

The C++ solver writes a small registry entry under
``$XDG_CACHE_HOME/gtopt/runs/<pid>`` (or ``~/.cache/gtopt/runs/<pid>``)
on the first ``write_solver_status()`` call.  Each entry is a one-line
file containing the absolute path of the solver's output directory.

This module provides:

* :func:`registry_dir` — resolve the registry path (from
                         :mod:`gtopt_shared.status`, with :class:`RunInfo`).
* :func:`list_runs`    — enumerate live runs (PID + output_dir + status
                         summary), skipping stale entries via
                         ``os.kill(pid, 0)``.
* :func:`resolve_target` — turn a CLI argument (PID or path) into the
                           directory we should attach to.
* :func:`run_attach_loop` — drive the existing :class:`SolverDisplay`
                            in observer mode (no subprocess).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from gtopt_shared.status import (
    STATUS_FILE,
    RunInfo,
    enrich_run_info,
    load_status,
    registry_dir,
)

if TYPE_CHECKING:
    from ._process import RunResult

__all__ = [
    "RunInfo",
    "find_run_by_pid",
    "list_runs",
    "print_run_list",
    "registry_dir",
    "resolve_target",
    "run_attach_loop",
]

log = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """``True`` when ``pid`` is alive *and* belongs to this user."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user — treat as alive
        # so we don't pretend it's stale; just unstoppable from here.
        return True
    except OSError:
        return False
    return True


def _status_file_for(output_dir: Path) -> Path:
    return output_dir / STATUS_FILE


def list_runs(prune_stale: bool = True) -> list[RunInfo]:
    """Enumerate live gtopt runs from the registry.

    Stale entries (process gone) are silently removed when
    ``prune_stale`` is ``True``.  Each surviving entry is enriched with
    the latest snapshot from its ``solver_status.json``.
    """
    rdir = registry_dir()
    if not rdir.is_dir():
        return []

    runs: list[RunInfo] = []
    for entry in sorted(rdir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        try:
            line = entry.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not line:
            continue
        out_dir = Path(line)
        alive = _pid_alive(pid)
        if not alive and prune_stale:
            try:
                entry.unlink()
            except OSError:
                pass
            continue
        runs.append(enrich_run_info(RunInfo(pid=pid, output_dir=out_dir, alive=alive)))
    return runs


def find_run_by_pid(pid: int) -> RunInfo | None:
    entry = registry_dir() / str(pid)
    if not entry.is_file():
        return None
    try:
        out_dir = Path(entry.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return enrich_run_info(RunInfo(pid=pid, output_dir=out_dir, alive=_pid_alive(pid)))


def resolve_target(target: str) -> RunInfo | None:
    """Resolve a CLI ``target`` to a :class:`RunInfo`.

    ``target`` may be:

    * a numeric PID present in the registry
    * an absolute or relative path to a case directory or directly to a
      ``solver_status.json`` (we infer the output_dir from its parent)
    """
    # Numeric PID lookup — first try registry, fall back to /proc cwd
    if target.isdigit():
        return find_run_by_pid(int(target))

    p = Path(target).expanduser().resolve()
    if not p.exists():
        return None
    if p.is_file() and p.name == STATUS_FILE:
        out_dir = p.parent
    elif p.is_dir():
        # Try ``solver_status.json`` directly; if absent, look in
        # conventional sub-dirs that gtopt may use.
        candidates = [p, p / "results", p / "output"]
        out_dir = next(
            (c for c in candidates if _status_file_for(c).is_file()),
            p,
        )
    else:
        return None

    # Try to read the PID from the status file (newer builds embed it).
    data = load_status(_status_file_for(out_dir))
    pid = int(data.get("pid", -1) or -1)
    return enrich_run_info(RunInfo(pid=pid, output_dir=out_dir, alive=_pid_alive(pid)))


# ---------------------------------------------------------------------------
# Attach: drive the existing TUI in observer mode (no subprocess)
# ---------------------------------------------------------------------------


def run_attach_loop(info: RunInfo) -> int:
    """Attach the Rich TUI to an already-running gtopt.

    Returns 0 when the user quits (q) — we never SIGTERM the solver
    here.  ``s`` still works because the TUI writes the stop file
    next to the discovered ``solver_status.json``.
    """
    # Local import keeps the CLI fast for ``--list`` / ``--help``.
    from ._tui import SolverDisplay  # noqa: PLC0415

    case_dir = (
        info.output_dir.parent if info.output_dir.parent.exists() else (info.output_dir)
    )
    display = SolverDisplay(
        case_name=info.case_name or case_dir.name,
        case_dir=case_dir,
        solver_hint=info.method or "",
        output_dir=info.output_dir,
        confirm_quit_after=None,  # q only detaches here
    )
    print(
        f"Attaching to gtopt pid={info.pid} "
        f"output={info.output_dir} (case={info.case_name})"
    )
    display.start()
    finished = False
    try:
        # Loop until the user presses 'q', or the solver disappears.
        # The TUI thread does its own polling; we just monitor liveness
        # at a relaxed cadence so an exited solver doesn't leave the
        # TUI running forever.
        while not display.quit_requested.is_set():
            if info.pid > 0 and not _pid_alive(info.pid):
                finished = True
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        # Ctrl-C in attach mode = detach without killing the solver.
        print("\nDetaching (Ctrl-C); gtopt continues running.")
    finally:
        display.stop()
        try:
            display.print_final(_attach_result(info, finished))
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            log.debug("print_final failed", exc_info=True)
    return 0


def _attach_result(info: RunInfo, finished: bool) -> RunResult:
    """Outcome inputs for an attached run: the exit code is unknown.

    A run that ended while observed is classified from its final
    ``solver_status.json`` and ``solution.csv`` (not "Success" by
    default); leaving while it still runs is reported as detached.
    """
    from ._process import RunResult  # noqa: PLC0415
    from ._runner import check_solution  # noqa: PLC0415

    status = load_status(_status_file_for(info.output_dir))
    alive = info.pid > 0 and _pid_alive(info.pid)
    return RunResult(
        returncode=None,
        output_dir=info.output_dir,
        status=status,
        solution={} if alive else check_solution(info.output_dir),
        detached=alive and not finished,
    )


def print_run_list(runs: list[RunInfo]) -> None:
    """Render a compact one-row-per-run summary to stdout."""
    if not runs:
        print(f"No live gtopt runs found.\n  (Registry: {registry_dir()})")
        return

    print(
        f"{'PID':>7}  {'STATUS':<12} {'METHOD':<12} {'ITER':>10}  "
        f"{'GAP%':>7}  {'ELAPSED':>8}  CASE  /  OUTPUT_DIR"
    )
    for r in runs:
        iter_str = (
            f"{r.iteration}/{r.max_iterations}"
            if r.iteration is not None and r.max_iterations
            else (str(r.iteration) if r.iteration is not None else "-")
        )
        gap_str = f"{100.0 * r.gap:.2f}" if r.gap is not None else "-"
        elapsed_str = f"{int(r.elapsed_s)}s" if r.elapsed_s is not None else "-"
        flag = " " if r.alive else "*"
        print(
            f"{r.pid:>7}{flag} {r.status or '-':<12} {r.method or '-':<12} "
            f"{iter_str:>10}  {gap_str:>7}  {elapsed_str:>8}  "
            f"{r.case_name or '-'}  /  {r.output_dir}"
        )
    if any(not r.alive for r in runs):
        print("  (* = stale entry; process no longer alive)")
