# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Resolve where gtopt writes its output, the same way gtopt does.

gtopt picks the output directory with this precedence (see
``resolve_log_directory`` / ``gtopt_main`` in ``source/gtopt_main.cpp``):

1. CLI ``-d/--output-directory DIR``;
2. ``--set output_directory=DIR``;
3. ``options.output_directory`` in the planning JSON;
4. the default: ``results`` inside the case directory when gtopt is given
   a directory (``gtopt case/``), else the compiled-in ``output``.

A relative value from 1-2 (the command line) is taken relative to the
CWD; one from 3-4 is anchored at the planning JSON's parent directory.
run_gtopt uses this single resolver for the stale-file cleanup, the TUI
status / stop file, the post-run report and the post-flight checks so
all of them look where gtopt actually wrote.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from gtopt_shared.status import STATUS_FILE, STOP_REQUEST_FILE

log = logging.getLogger(__name__)

#: gtopt's compiled-in ``PlanningOptionsLP::default_output_directory``.
DEFAULT_OUTPUT_DIRECTORY = "output"

#: Default when gtopt gets a case *directory* (``resolve_planning_args``).
DIRECTORY_ARG_OUTPUT_DIRECTORY = "results"

#: Files a previous run leaves in the output directory that would mislead
#: the next one: a stale status file makes the dashboard / report show the
#: old run, and a stale stop request makes a new SDDP run stop right after
#: its first iteration (gtopt never deletes it).  Nothing else — results,
#: cuts, logs — is ever removed by run_gtopt.
STALE_RUN_FILES = (STATUS_FILE, STOP_REQUEST_FILE)


def find_planning_json(case: Path) -> Path | None:
    """Return the planning JSON for *case* (a JSON file or a case dir)."""
    if case.is_file():
        return case if case.suffix == ".json" else None
    if not case.is_dir():
        return None
    canonical = case / f"{case.name}.json"
    if canonical.is_file():
        return canonical
    candidates = sorted(
        p for p in case.glob("*.json") if not p.stem.endswith("_sanitized")
    )
    return candidates[0] if candidates else None


def _option_value(extra_args: list[str], flags: tuple[str, ...]) -> str | None:
    """Return the last value given to any of *flags* in *extra_args*."""
    value: str | None = None
    for i, arg in enumerate(extra_args):
        for flag in flags:
            if arg == flag and i + 1 < len(extra_args):
                value = extra_args[i + 1]
            elif flag.startswith("--") and arg.startswith(flag + "="):
                value = arg[len(flag) + 1 :]
    return value


def _set_value(extra_args: list[str], key: str) -> str | None:
    """Return the last ``--set key=VALUE`` value in *extra_args*."""
    value: str | None = None
    for i, arg in enumerate(extra_args):
        assignment: str | None = None
        if arg == "--set" and i + 1 < len(extra_args):
            assignment = extra_args[i + 1]
        elif arg.startswith("--set="):
            assignment = arg[len("--set=") :]
        if assignment is not None and assignment.startswith(key + "="):
            value = assignment[len(key) + 1 :]
    return value


def _json_output_directory(json_path: Path | None) -> str | None:
    if json_path is None:
        return None
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    opts = data.get("options") if isinstance(data, dict) else None
    value = opts.get("output_directory") if isinstance(opts, dict) else None
    return value if isinstance(value, str) and value else None


def resolve_output_dir(
    case: Path,
    extra_args: list[str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Return the directory gtopt will write its output to for *case*.

    *case* is what run_gtopt hands to gtopt (a case directory or planning
    JSON); *extra_args* are the ``--`` passthrough arguments.  The result
    is absolute so the paths printed to the user can be copy-pasted.
    """
    return _resolve_output_dir(case, extra_args, cwd).absolute()


def _resolve_output_dir(
    case: Path, extra_args: list[str] | None, cwd: Path | None
) -> Path:
    args = extra_args or []
    base_cwd = cwd or Path.cwd()
    cli = _option_value(args, ("-d", "--output-directory")) or _set_value(
        args, "output_directory"
    )
    if cli:
        path = Path(cli)
        return path if path.is_absolute() else base_cwd / path

    json_path = find_planning_json(case)
    # gtopt expands a directory argument only to ``dir/<dir name>.json``.
    dir_arg = case.is_dir() and json_path == case / f"{case.name}.json"
    value = _json_output_directory(json_path) or (
        DIRECTORY_ARG_OUTPUT_DIRECTORY if dir_arg else DEFAULT_OUTPUT_DIRECTORY
    )
    path = Path(value)
    if path.is_absolute():
        return path
    if json_path is not None:
        anchor = json_path.parent
    else:
        anchor = case if case.is_dir() else case.parent
    return anchor / path


def remove_stale_run_files(output_dir: Path) -> list[Path]:
    """Delete the stale per-run files listed in :data:`STALE_RUN_FILES`.

    Every removal is logged; results, cuts and logs are never touched.
    Returns the removed paths.
    """
    removed: list[Path] = []
    for name in STALE_RUN_FILES:
        path = output_dir / name
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError as exc:
            log.warning("could not remove stale %s: %s", path, exc)
            continue
        removed.append(path)
        if name == STOP_REQUEST_FILE:
            log.warning(
                "removed a stop request left by a previous run: %s "
                "(it would have stopped this run after one iteration)",
                path,
            )
        else:
            log.info("removed stale %s from a previous run", path)
    return removed


def write_stop_request(output_dir: Path, source: str = "run_gtopt") -> Path | None:
    """Ask a running SDDP/cascade gtopt to stop after this iteration.

    Returns the written file, or ``None`` when it could not be written.
    """
    path = output_dir / STOP_REQUEST_FILE
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = {"requested_at": time.time(), "source": source}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("failed to create stop request file: %s", exc)
        return None
    log.info("created stop request: %s", path)
    return path
