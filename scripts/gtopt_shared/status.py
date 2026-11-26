# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Find and read gtopt's ``solver_status.json`` (one shared reader).

gtopt rewrites ``<output_directory>/solver_status.json`` while it runs
(``source/solver_status.cpp``) and registers itself under
``$XDG_CACHE_HOME/gtopt/runs/<pid>``.  ``run_gtopt`` (dashboard, batch
heartbeat, ``--list`` / ``--attach``, exit-code check) and
``gtopt_monitor`` all read that file; this module is the single copy of
"where is it and how do I read it":

* :func:`read_status` / :func:`load_status` -- tolerant JSON readers (a
  half-written file, a stale file from an earlier run, a non-object
  payload never raise).
* :func:`find_status_file` / :func:`find_planning_json` -- locate the
  files of a case directory.
* :func:`registry_dir`, :class:`RunInfo`, :func:`enrich_run_info` -- the
  live-run registry and its one-row summary.
* Schema constants shared with the C++ writer (:data:`PASS_NAMES`, file
  names).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The status file gtopt writes under ``options.output_directory``.
STATUS_FILE = "solver_status.json"
#: Names written by older gtopt builds (probed after :data:`STATUS_FILE`).
LEGACY_STATUS_FILES = ("sddp_status.json", "monolithic_status.json")
#: The graceful-stop file gtopt's SDDP loop polls.
STOP_REQUEST_FILE = "sddp_stop_request.json"
#: Conventional output dirs of a case (newer cases: ``output``; older and
#: cascade cases: ``results``), probed when the planning JSON is silent.
CONVENTIONAL_OUTPUT_DIRS = ("output", "results")
#: Key :func:`load_status` adds with the file age in seconds.
STATUS_AGE_KEY = "_status_age_s"

#: ``SolverStatusSnapshot::current_pass`` (C++) -> name.
PASS_NAMES = {0: "idle", 1: "forward", 2: "backward"}


def read_status(path: Path | None, *, since: float | None = None) -> dict | None:
    """Parse the status JSON at *path*.

    Returns ``None`` when *path* is ``None``, missing, unreadable, not
    valid JSON (gtopt may be rewriting it), not a JSON object, or -- with
    *since* (an epoch time) -- older than *since* (left over from a
    previous run).
    """
    if path is None:
        return None
    try:
        if since is not None and path.stat().st_mtime < since:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_status(
    path: Path | None,
    *,
    since: float | None = None,
    stamp_age: bool = False,
) -> dict[str, Any]:
    """:func:`read_status`, but ``{}`` instead of ``None``.

    With *stamp_age* the file age in seconds is stored under
    :data:`STATUS_AGE_KEY` so a monitor can flag stale telemetry.
    """
    data = read_status(path, since=since)
    if data is None or path is None:
        return {}
    if stamp_age:
        try:
            data[STATUS_AGE_KEY] = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            pass
    return data


def find_planning_json(case_dir: Path, *, prefer_sanitized: bool = True) -> Path | None:
    """Locate the planning JSON of a case.

    *case_dir* may be the JSON file itself.  For a directory:
    ``<dir>/<dir_name>.json``, then (with *prefer_sanitized*) a
    ``*_sanitized.json`` copy, then any ``*.json`` (sorted by name).
    """
    if case_dir.is_file() and case_dir.suffix == ".json":
        return case_dir
    candidate = case_dir / f"{case_dir.name}.json"
    if candidate.is_file():
        return candidate
    patterns = ("*_sanitized.json", "*.json") if prefer_sanitized else ("*.json",)
    for pattern in patterns:
        for p in sorted(case_dir.glob(pattern)):
            if p.is_file():
                return p
    return None


def planning_output_dir(json_path: Path | None) -> Path | None:
    """``options.output_directory`` of a planning JSON, resolved against
    the JSON's directory; ``None`` when unset or unreadable."""
    if json_path is None:
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            planning = json.load(f)
    except (OSError, ValueError):
        return None
    options = planning.get("options") if isinstance(planning, dict) else None
    out_opt = options.get("output_directory", "") if isinstance(options, dict) else ""
    if not isinstance(out_opt, str) or not out_opt:
        return None
    p = Path(out_opt)
    return p if p.is_absolute() else json_path.parent / p


def find_status_file(case_dir: Path, output_dir: Path | None = None) -> Path:
    """The status file of the case at *case_dir*.

    Candidate output dirs, in order: *output_dir* (the resolved run
    output dir, when known), the planning JSON's
    ``options.output_directory``, then :data:`CONVENTIONAL_OUTPUT_DIRS`.
    The first one holding :data:`STATUS_FILE` (or a legacy name) wins;
    when none exists yet the canonical path in the first candidate is
    returned so the caller can keep polling it.
    """
    base = case_dir.parent if case_dir.is_file() else case_dir
    candidate_dirs: list[Path] = []
    if output_dir is not None:
        candidate_dirs.append(output_dir)
    declared = planning_output_dir(find_planning_json(case_dir))
    if declared is not None:
        candidate_dirs.append(declared)
    candidate_dirs.extend(base / sub for sub in CONVENTIONAL_OUTPUT_DIRS)

    for cand in candidate_dirs:
        for name in (STATUS_FILE, *LEGACY_STATUS_FILES):
            status = cand / name
            if status.is_file():
                return status
    return candidate_dirs[0] / STATUS_FILE


# ---------------------------------------------------------------------------
# Live-run registry
# ---------------------------------------------------------------------------


def registry_dir() -> Path:
    """``$XDG_CACHE_HOME/gtopt/runs`` (or the HOME-derived path).

    Mirrors ``solver_status.cpp::runs_registry_dir`` on the C++ side.
    """
    xdg = os.environ.get("XDG_CACHE_HOME") or ""
    if xdg:
        base = Path(xdg)
    else:
        home = os.environ.get("HOME") or ""
        base = Path(home) / ".cache" if home else Path(".")
    return base / "gtopt" / "runs"


@dataclass(slots=True)
class RunInfo:
    """Snapshot of one live (or recently-live) gtopt run."""

    pid: int
    output_dir: Path
    status: str | None = None  # "running" / "converged" / ...
    iteration: int | None = None
    max_iterations: int | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    gap: float | None = None
    elapsed_s: float | None = None
    method: str | None = None
    case_name: str | None = None
    alive: bool = True


def enrich_run_info(info: RunInfo) -> RunInfo:
    """Fill *info* from its ``solver_status.json`` (unchanged when the
    file is missing or unreadable)."""
    data = load_status(info.output_dir / STATUS_FILE)
    if not data:
        return info
    info.status = data.get("status")
    info.iteration = data.get("iteration")
    info.max_iterations = data.get("max_iterations")
    info.lower_bound = data.get("lower_bound")
    info.upper_bound = data.get("upper_bound")
    info.gap = data.get("gap")
    info.elapsed_s = data.get("elapsed_s")
    info.method = data.get("method")
    # Best-effort case name = parent of output_dir: gtopt cases write to
    # ``<case>/output/`` or ``<case>/results/``.
    info.case_name = info.output_dir.parent.name or info.output_dir.name
    return info
