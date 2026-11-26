# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Subprocess invocation for plp2gtopt and gtopt, and post-run reporting."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from gtopt_shared.status import STATUS_FILE, load_status
from gtopt_shared.solution_csv import (
    GTOPT_REVISION,
    GTOPT_VERSION,
    default_scale_objective,
    format_gtopt_build,
    resolve_objective,
)

from ._heartbeat import BatchProgress
from ._interrupt import InterruptController
from ._paths import write_stop_request
from ._process import (
    ErrorCollector,
    RunResult,
    SolverProcess,
    error_lp_hint,
    failure_details,
    run_paths,
    shell_exit_code,
)

log = logging.getLogger(__name__)


def _extract_runpath(so_path: Path) -> list[str]:
    """Extract RUNPATH/RPATH entries from a shared library via readelf."""
    try:
        result = subprocess.run(
            ["readelf", "-d", str(so_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if "RUNPATH" in line or "RPATH" in line:
                # Format: ... Library runpath: [/opt/coinor/lib:/usr/local/lib]
                start = line.find("[")
                end = line.find("]")
                if 0 <= start < end:
                    paths = line[start + 1 : end].split(":")
                    return [p for p in paths if p and p != "$ORIGIN"]
    except FileNotFoundError:
        pass
    return []


def _build_ld_library_path(gtopt_bin: str) -> str:
    """Build LD_LIBRARY_PATH from the gtopt binary and its plugins.

    Adds directories where solver plugins and their shared-library
    dependencies are expected to live, mirroring the search order used
    by ``SolverRegistry::discover_default_paths`` in the C++ code.

    Also extracts RUNPATH entries from discovered plugin .so files so
    that solver-specific library directories (e.g. /opt/coinor/lib,
    /opt/cplex/lib) are included without manual LD_LIBRARY_PATH setup.
    """
    exe_dir = Path(gtopt_bin).resolve().parent
    candidates = [
        exe_dir / ".." / "lib" / "gtopt" / "plugins",
        exe_dir / ".." / "lib",
        exe_dir / "plugins",
        exe_dir / ".." / "plugins",
        exe_dir,
        Path("/usr/local/lib/gtopt/plugins"),
        Path("/usr/local/lib"),
    ]
    lib_dirs: list[str] = [str(p.resolve()) for p in candidates if p.is_dir()]

    # Extract RUNPATH from plugin .so files to pick up solver lib dirs.
    for cand in candidates:
        if cand.is_dir():
            for so_file in cand.glob("libgtopt_solver_*.so"):
                for rp in _extract_runpath(so_file):
                    rp_resolved = str(Path(rp).resolve())
                    if rp_resolved not in lib_dirs:
                        lib_dirs.append(rp_resolved)

    existing = os.environ.get("LD_LIBRARY_PATH", "")
    if existing:
        lib_dirs.append(existing)
    return ":".join(lib_dirs)


def run_plp2gtopt(
    plp2gtopt_bin: str,
    input_dir: Path,
    output_dir: Path,
    extra_args: list[str] | None = None,
) -> int:
    """Run plp2gtopt to convert a PLP case to gtopt format.

    Returns the process exit code.
    """
    cmd = [plp2gtopt_bin, str(input_dir), "-o", str(output_dir)]
    if extra_args:
        cmd.extend(extra_args)

    log.info("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        log.error("plp2gtopt failed with exit code %d", result.returncode)
    return result.returncode


def run_plexos2gtopt(
    plexos2gtopt_bin: str,
    input_bundle: Path,
    output_dir: Path,
    extra_args: list[str] | None = None,
) -> int:
    """Run plexos2gtopt to convert a PLEXOS bundle to gtopt format.

    Mirrors :func:`run_plp2gtopt`.  The converter auto-runs its
    PLEXOS↔gtopt comparison + structural validation and returns the
    CRITICAL finding count as its exit code (nonzero ⇒ abort).
    """
    cmd = [plexos2gtopt_bin, str(input_bundle), "-o", str(output_dir)]
    if extra_args:
        cmd.extend(extra_args)

    log.info("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        log.error("plexos2gtopt failed with exit code %d", result.returncode)
    return result.returncode


def _build_gtopt_cmd(
    gtopt_bin: str,
    case_dir: Path,
    threads: int | None = None,
    compression: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the gtopt command line.

    Threads and compression are already baked into the sanitized JSON
    by :func:`sanitize_json`, so they are not passed as CLI flags here.
    """
    cmd = [gtopt_bin, str(case_dir)]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def _find_case_json(case_dir: Path) -> Path | None:
    """Return the planning JSON inside @p case_dir (or @p case_dir itself
    if it already points at a JSON file).  Returns ``None`` when no JSON
    file is found.

    Used to discover the case's ``output_directory`` / ``log_directory``
    so the tail thread polls the right place for new gtopt logs.
    """
    if case_dir.is_file():
        return case_dir if case_dir.suffix == ".json" else None
    if not case_dir.is_dir():
        return None
    # Prefer a JSON file matching the dir name (the plp2gtopt convention).
    canonical = case_dir / f"{case_dir.name}.json"
    if canonical.is_file():
        return canonical
    candidates = sorted(case_dir.glob("*.json"))
    return candidates[0] if candidates else None


def _resolve_log_dir(case_dir: Path) -> Path | None:
    """Return the primary directory where gtopt will write its log files.

    Mirrors the resolution done by ``setup_file_logging`` on the C++
    side: gtopt picks ``log_directory`` if set in the planning JSON,
    otherwise falls back to ``<output_directory>/logs``.  Both paths
    are resolved relative to the case directory (the JSON's location).
    The directory is created so the tail thread can poll it for new
    files.

    Use :func:`_resolve_log_dir_candidates` instead when the caller
    needs a *complete* set of paths to watch — the primary path can
    desync from the actual gtopt output dir if the JSON's
    ``output_directory`` is ambiguous about its base (CWD vs JSON
    location), and that desync silently broke the TUI tail thread on
    runs where the sanitized JSON's resolved output_directory and
    gtopt's actual write target diverged (observed 2026-05-07: TUI
    polled ``<case>/results/logs/`` while gtopt wrote to
    ``<cwd>/output/logs/``).
    """
    candidates = _resolve_log_dir_candidates(case_dir)
    return candidates[0] if candidates else None


def _resolve_log_dir_candidates(
    case_dir: Path, resolved_output_dir: Path | None = None
) -> list[Path]:
    """Return every directory the tail thread should monitor for new
    ``gtopt_*.log`` files.

    The C++ side resolves ``output_directory`` / ``log_directory``
    relative to gtopt's CWD, which is **not always** the JSON file's
    parent directory: when ``run_gtopt`` is invoked from a sibling
    shell (``cd work && run_gtopt my_case``), the
    JSON ends up parsed from ``my_case/`` while gtopt itself
    runs with CWD=``work/``, so a JSON-relative
    ``output_directory: "results"`` resolves to ``work/results``
    on the C++ side but ``work/my_case/results`` on
    the Python side.  The two diverge silently and the TUI sits at
    "Waiting for solver output…" forever.

    To bridge this, the watcher walks every plausible candidate:

      1. ``<json_parent>/<json.options.log_directory>`` if set
         (explicit log_directory wins).
      2. ``<json_parent>/<json.options.output_directory>/logs`` —
         the primary path (matches gtopt under most CWDs).
      3. ``<json_parent>/output/logs`` — fallback for the C++
         compiled-in default output_directory.
      4. ``<cwd>/output/logs`` — what gtopt writes to when launched
         from a working dir different from the JSON's parent.
      5. ``<cwd>/results/logs`` — same as #4 but for the common
         plp2gtopt-emitted ``output_directory: "results"`` value.

    Returns the primary path first (so callers that pick `[0]` retain
    the prior behaviour), followed by the additional fallbacks in
    discovery order.  Duplicates are removed; missing directories are
    *created* so the tail-snapshot baseline can populate empty.
    """
    base = case_dir.parent if case_dir.is_file() else case_dir
    output_dir: str | None = None
    log_subdir: str | None = None
    json_path = _find_case_json(case_dir)
    if json_path is not None:
        try:
            with open(json_path, encoding="utf-8") as fh:
                data = json.load(fh)
            opts = data.get("options") or {}
            if isinstance(opts.get("output_directory"), str):
                output_dir = opts["output_directory"]
            if isinstance(opts.get("log_directory"), str):
                log_subdir = opts["log_directory"]
        except (OSError, json.JSONDecodeError):
            pass

    raw_candidates: list[Path] = []
    if log_subdir is not None:
        raw_candidates.append(base / log_subdir)
    if resolved_output_dir is not None:
        # The resolved gtopt output dir (CLI -d / --set aware) is the
        # most reliable guess after an explicit log_directory.
        raw_candidates.append(resolved_output_dir / "logs")
    if output_dir is not None:
        raw_candidates.append(base / output_dir / "logs")
    raw_candidates.append(base / "output" / "logs")
    cwd = Path.cwd()
    raw_candidates.append(cwd / "output" / "logs")
    raw_candidates.append(cwd / "results" / "logs")

    seen: set[Path] = set()
    out: list[Path] = []
    for raw in raw_candidates:
        try:
            resolved = raw.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        out.append(resolved)
    return out


def _snapshot_gtopt_logs(log_dir: Path) -> set[Path]:
    """Return the set of existing ``gtopt_*.log`` files in @p log_dir.

    Used as a baseline so the tail thread knows which file is the *new*
    one created by the run we're about to launch.
    """
    try:
        return set(log_dir.glob("gtopt_*.log"))
    except OSError:
        return set()


def _wait_for_new_log(
    log_dir: Path,
    baseline: set[Path],
    stop_event: threading.Event,
    poll_interval: float = 0.1,
) -> Path | None:
    """Wait until a new ``gtopt_*.log`` appears in @p log_dir; return it.

    Single-directory variant — see :func:`_wait_for_new_log_multi` for
    the variant the TUI tail thread uses, which polls every plausible
    log-dir candidate so the watcher does not silently desync from
    the actual C++ write target on layouts where the JSON's
    ``output_directory`` resolves differently on each side.
    """
    while not stop_event.is_set():
        try:
            current = set(log_dir.glob("gtopt_*.log"))
        except OSError:
            current = set()
        new = current - baseline
        if new:
            # If multiple appeared (very unlikely — only one writer per
            # gtopt run), pick the most recently modified.
            return max(new, key=lambda p: p.stat().st_mtime)
        time.sleep(poll_interval)
    return None


def _wait_for_new_log_multi(
    candidate_dirs: list[Path],
    baselines: dict[Path, set[Path]],
    stop_event: threading.Event,
    poll_interval: float = 0.1,
) -> Path | None:
    """Wait until a new ``gtopt_*.log`` appears in ANY @p candidate_dirs.

    Polls each candidate concurrently in a single round-robin loop.
    Returns the first novel file seen across all candidates.  When
    multiple candidates resolve to the same directory the per-dir
    baseline is shared, so we never double-count the same file.

    @p baselines maps each candidate dir to the set of pre-existing
    ``gtopt_*.log`` files at watch start.  Files outside that
    baseline are considered new.

    Returns ``None`` if @p stop_event fires before any new file
    appears in any candidate.
    """
    while not stop_event.is_set():
        for cand in candidate_dirs:
            try:
                current = set(cand.glob("gtopt_*.log"))
            except OSError:
                continue
            baseline = baselines.get(cand, set())
            new = current - baseline
            if new:
                return max(new, key=lambda p: p.stat().st_mtime)
        time.sleep(poll_interval)
    return None


def _tail_log_to(
    log_path: Path,
    consume,
    stop_event: threading.Event,
    poll_interval: float = 0.1,
) -> None:
    """Tail @p log_path line-by-line, calling @p consume(line) for each.

    Returns when @p stop_event is set AND the file has been fully
    drained.  Silently no-ops if the file vanishes or can't be opened.
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if line:
                    consume(line)
                    continue
                if stop_event.is_set():
                    # Drain anything written between our last read and
                    # now, then exit.
                    for tail_line in f:
                        consume(tail_line)
                    return
                time.sleep(poll_interval)
    except OSError:
        return


def _watch_and_tail(
    log_dir: Path,
    baseline: set[Path],
    consume,
    stop_event: threading.Event,
) -> None:
    """Wait for a new gtopt_N.log to appear, then tail it.

    Single-directory variant — see :func:`_watch_and_tail_multi` for
    the multi-candidate version the TUI tail thread uses.
    """
    log_path = _wait_for_new_log(log_dir, baseline, stop_event)
    if log_path is None:
        return
    _tail_log_to(log_path, consume, stop_event)


def _watch_and_tail_multi(
    candidate_dirs: list[Path],
    baselines: dict[Path, set[Path]],
    consume,
    stop_event: threading.Event,
    found: list[Path] | None = None,
) -> None:
    """Wait for a new ``gtopt_*.log`` in any candidate dir, then tail it.

    Combines :func:`_wait_for_new_log_multi` and :func:`_tail_log_to`
    for use as the target of a background tail thread that doesn't
    know in advance which of several plausible directories the C++
    side will actually open its log in.
    """
    log_path = _wait_for_new_log_multi(candidate_dirs, baselines, stop_event)
    if log_path is None:
        return
    if found is not None:
        found.append(log_path)
    _tail_log_to(log_path, consume, stop_event)


def _capture_dir(output_dir: Path | None, candidate_dirs: list[Path]) -> Path | None:
    """Directory for the captured solver stdout/stderr file."""
    if output_dir is not None:
        return output_dir / "logs"
    return candidate_dirs[0] if candidate_dirs else None


class _FoundLog(list):
    """``found`` list for the tail thread that also notifies a callback."""

    def __init__(self, on_found=None) -> None:
        super().__init__()
        self._on_found = on_found

    def append(self, path: Path) -> None:
        super().append(path)
        if self._on_found is not None:
            self._on_found(path)


def _start_tail(
    baselines: dict[Path, set[Path]],
    consume,
    stop_event: threading.Event,
    found: list[Path],
) -> threading.Thread | None:
    """Start the thread that tails the new ``gtopt_N.log`` (if any dir).

    *baselines* (``{dir: existing logs}``) must be taken before gtopt is
    launched so a fast run's log is never mistaken for an old one.
    """
    if not baselines:
        return None
    candidate_dirs = list(baselines)
    thread = threading.Thread(
        target=_watch_and_tail_multi,
        args=(candidate_dirs, baselines, consume, stop_event, found),
        daemon=True,
    )
    thread.start()
    return thread


def _final_state(
    output_dir: Path | None, since: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the run's final ``solver_status.json`` and ``solution.csv``."""
    if output_dir is None:
        return {}, {}
    status = load_status(output_dir / STATUS_FILE, since=since)
    return status, check_solution(output_dir, since=since)


def _finish(
    proc: SolverProcess,
    found: list[Path],
    output_dir: Path | None,
    **kwargs: Any,
) -> RunResult:
    """Build the :class:`RunResult` of a finished gtopt process."""
    status, solution = _final_state(output_dir, proc.started_wall)
    return proc.result(
        log_path=found[0] if found else None,
        output_dir=output_dir,
        status=status,
        solution=solution,
        **kwargs,
    )


def _print_batch_final(result: RunResult) -> None:
    """Plain-text outcome block on stderr for non-TTY runs."""
    outcome = result.outcome()
    print(
        f"run_gtopt: [{outcome.ascii_icon}] {outcome.title}",
        file=sys.stderr,
    )
    for line in outcome.lines:
        print(f"run_gtopt:      {line}", file=sys.stderr)
    if outcome.kind in ("failed", "killed", "non_optimal"):
        # The raw solver output was already echoed live on stderr.
        for line in failure_details(result, include_tail=False):
            print(f"run_gtopt: {line}", file=sys.stderr)
    for line in error_lp_hint(result.output_dir):
        print(f"run_gtopt: {line}", file=sys.stderr)
    for label, path in run_paths(result):
        print(f"run_gtopt: {label}: {path}", file=sys.stderr)


def _run_batch(
    cmd: list[str],
    env: dict[str, str],
    case_dir: Path,
    output_dir: Path | None = None,
) -> int:
    """Run gtopt and stream its log file to stdout.

    The C++ binary detects non-TTY stdout and writes its spdlog output to
    ``<output>/logs/gtopt_N.log`` via ``setup_file_logging``.  We snapshot
    the log directories before launch, wait for the new ``gtopt_N.log``
    to appear, then tail it line-by-line to stdout so an operator running
    ``run_gtopt`` from a CI / shell pipe still sees progress in real time.
    Whatever gtopt prints on its own stdout/stderr (early failures that
    never reach the log file) is echoed to stderr and saved next to the
    log (see :class:`SolverProcess`).
    """
    log.info("running: %s", " ".join(cmd))
    candidate_dirs = _resolve_log_dir_candidates(case_dir, output_dir)

    def _emit(line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()

    errors = ErrorCollector()

    def _emit_log(line: str) -> None:
        errors.feed(line)
        _emit(line)

    proc = SolverProcess(
        cmd,
        env,
        capture_dir=_capture_dir(output_dir, candidate_dirs),
        echo=sys.stderr,
        errors=errors,
    )
    stop_event = threading.Event()
    found: list[Path] = []
    baselines = {d: _snapshot_gtopt_logs(d) for d in candidate_dirs}
    try:
        proc.start()
    except OSError as exc:
        print(f"run_gtopt: cannot start gtopt: {exc}", file=sys.stderr)
        return 127
    tail_thread = _start_tail(baselines, _emit_log, stop_event, found)

    def _request_stop() -> bool:
        return output_dir is not None and write_stop_request(output_dir) is not None

    def _notify(msg: str) -> None:
        print(f"\nrun_gtopt: {msg}", file=sys.stderr, flush=True)

    ctl = InterruptController(proc, _request_stop, _notify)
    progress = BatchProgress(output_dir, sys.stderr, since=proc.started_wall - 1.0)
    try:
        with ctl.installed():
            while proc.poll() is None:
                ctl.tick()
                progress.tick()
                time.sleep(0.1)
        returncode = proc.wait()
        progress.tick(force=True)  # iterations finished since the last poll
    finally:
        stop_event.set()
        if tail_thread is not None:
            tail_thread.join(timeout=2.0)

    _print_batch_final(
        _finish(
            proc,
            found,
            output_dir,
            forced=ctl.forced,
            stop_requested=ctl.stop_requested,
        )
    )
    return shell_exit_code(returncode)


def _run_interactive(
    cmd: list[str],
    env: dict[str, str],
    case_dir: Path,
    output_dir: Path | None = None,
    case_name: str | None = None,
) -> int:
    """Run gtopt with a live Rich terminal dashboard.

    The dashboard runs in a background thread while the solver
    subprocess runs in the foreground.  The solver writes its log
    directly to ``<output>/logs/gtopt_N.log`` (via the C++ file-only
    sink that activates when stdout is not a TTY), and a tail thread
    discovers the new ``gtopt_N.log`` and forwards every line into the
    dashboard's log panel.  gtopt's own stdout/stderr is captured too
    (:class:`SolverProcess`), shown in the log panel and, on failure,
    in the final banner.

    Interactive commands (single-key):
      s  Graceful stop — create stop-request file (saves cuts)
      i  Toggle system stats overlay
      h  Toggle help overlay
      q  Quit — terminate the solver process
    """
    from ._tui import SolverDisplay  # noqa: PLC0415

    case_path = Path(case_dir)
    if case_name is None:
        # The target may be the temporary "<name>_sanitized.json" copy.
        case_name = case_path.stem if case_path.is_file() else case_path.name
        case_name = case_name.removesuffix("_sanitized")
    candidate_dirs = _resolve_log_dir_candidates(case_path, output_dir)

    # Extract --solver from command line for display
    solver_hint = ""
    for i, arg in enumerate(cmd):
        if arg == "--solver" and i + 1 < len(cmd):
            solver_hint = cmd[i + 1]
            break

    display = SolverDisplay(
        case_name=case_name,
        case_dir=case_path,
        solver_hint=solver_hint,
        output_dir=output_dir,
    )
    errors = ErrorCollector()

    def _log_line(line: str) -> None:
        errors.feed(line)
        display.add_log_line(line)

    proc = SolverProcess(
        cmd,
        env,
        capture_dir=_capture_dir(output_dir, candidate_dirs),
        on_line=display.add_log_line,
        errors=errors,
    )
    log.info("running (interactive): %s", " ".join(cmd))

    stop_event = threading.Event()
    found: list[Path] = _FoundLog(display.set_log_path)
    # Launch gtopt before any of our threads exist (preexec_fn + fork).
    baselines = {d: _snapshot_gtopt_logs(d) for d in candidate_dirs}
    try:
        proc.start()
    except OSError as exc:
        print(f"run_gtopt: cannot start gtopt: {exc}", file=sys.stderr)
        return 127
    tail_thread = _start_tail(baselines, _log_line, stop_event, found)
    display.start()
    ctl = InterruptController(proc, display.request_stop, display.set_shutdown_state)
    try:
        # Poll the TUI quit flag while the subprocess runs; Ctrl-C is
        # handled by the controller (graceful stop -> SIGTERM -> SIGKILL).
        with ctl.installed():
            while proc.poll() is None:
                ctl.tick()
                if display.quit_requested.is_set() and ctl.forced is None:
                    log.info("quit requested via TUI — terminating solver")
                    ctl.terminate("q (quit)")
                time.sleep(0.1)
        proc.wait()
    finally:
        stop_event.set()
        if tail_thread is not None:
            tail_thread.join(timeout=2.0)
        display.stop()
        display.print_final(
            _finish(
                proc,
                found,
                output_dir,
                forced=ctl.forced,
                stop_requested=ctl.stop_requested,
            )
        )

    return shell_exit_code(proc.returncode)


def run_gtopt(
    gtopt_bin: str,
    case_dir: Path,
    threads: int | None = None,
    compression: str | None = None,
    extra_args: list[str] | None = None,
    *,
    output_dir: Path | None = None,
    case_name: str | None = None,
) -> int:
    """Run the gtopt binary on a case directory.

    *output_dir* is the directory gtopt writes to (see
    :func:`run_gtopt._paths.resolve_output_dir`); it anchors the log
    tail, the status / stop files and the captured solver stderr.
    *case_name* is shown in the dashboard header (default: derived from
    *case_dir*, without a ``_sanitized`` suffix).

    When stdout is an interactive terminal, a Rich live dashboard is
    shown in a background thread.  Otherwise the solver runs as a
    plain subprocess with output flowing to stdout.

    Returns the process exit code.
    """
    from ._tui import is_interactive  # noqa: PLC0415

    cmd = _build_gtopt_cmd(gtopt_bin, case_dir, threads, compression, extra_args)

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = _build_ld_library_path(gtopt_bin)
    log.debug("LD_LIBRARY_PATH=%s", env["LD_LIBRARY_PATH"])

    # jemalloc: run a background purge thread so freed pages return to the OS
    # off the allocating hot path.  Measured -28% peak RSS and -15% wall on the
    # 2-year Maule/Laja SDDP parity case.  gtopt binaries built with
    # -DGTOPT_USE_JEMALLOC=ON already bake this into the `malloc_conf` symbol;
    # setting it here also covers binaries built without that define (and is a
    # no-op under glibc).  `setdefault` respects an operator's own MALLOC_CONF.
    env.setdefault("MALLOC_CONF", "background_thread:true")

    if is_interactive():
        return _run_interactive(cmd, env, case_dir, output_dir, case_name)
    return _run_batch(cmd, env, case_dir, output_dir)


# ---------------------------------------------------------------------------
# Error LP diagnostics
# ---------------------------------------------------------------------------


def find_error_lp_files(log_dir: Path) -> list[Path]:
    """Find error LP files in the log directory.

    Returns all files matching ``error*.lp*`` (includes compressed
    variants like .lp.gz, .lp.zst), sorted by modification time
    (oldest first).
    """
    if not log_dir.is_dir():
        return []
    candidates = list(log_dir.glob("error*.lp*"))
    candidates.sort(key=lambda f: f.stat().st_mtime)
    return candidates


def run_check_lp(lp_files: list[Path]) -> None:
    """Run gtopt_check_lp on the first (oldest) error LP file.

    Analyses the first error LP to give the user immediate feedback,
    then prints instructions for checking the remaining files manually.
    """

    if not lp_files:
        return

    log_dir = lp_files[0].parent
    total = len(lp_files)

    print()
    print("=" * 60)
    print("  run_gtopt — Error LP Diagnostics")
    print("=" * 60)
    print(f"  Found {total} error LP file(s) in {log_dir}")
    print()

    check_bin = shutil.which("gtopt_check_lp")
    if not check_bin:
        print("  gtopt_check_lp not installed — cannot analyse automatically.")
        print("  Install: pip install -e ./scripts")
        print()
    else:
        # Analyse the first (oldest) error LP file
        first_lp = lp_files[0]
        print(f"  Analysing first error LP: {first_lp.name}")
        print()
        # Automatic diagnostic stays local: no AI provider, no NEOS upload.
        cmd = [check_bin, str(first_lp), "--quiet", "--no-ai", "--no-neos"]
        log.info("running: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = result.stdout + result.stderr
        for line in output.splitlines():
            print(f"  {line}")
        print()

    # User instructions
    print("  To analyse error LP files manually:")
    print(f"    gtopt_check_lp {lp_files[0]}")
    if total > 1:
        print(f"    gtopt_check_lp {lp_files[1]}   # (and {total - 1} more)")
    print()
    print("  For deeper analysis (IIS, multiple solvers):")
    print(f"    gtopt_check_lp {lp_files[0]} --solver all")
    print()
    print(f"  All error LP files are in: {log_dir}")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# Post-run reporting
# ---------------------------------------------------------------------------


def check_solution(
    results_dir: Path,
    fallback_scale: float | None = None,
    since: float | None = None,
) -> dict:
    """Parse solution.csv (or solution.parquet) from the results directory.

    Returns a dict with keys: ``status``, ``objective``,
    ``scale_objective`` and, for outputs that record it, ``gtopt_build``
    (the writing gtopt as ``gtopt --version`` prints it), or empty if the
    file is not found / unparseable.
    ``objective`` is the first cell's ``obj_value`` in physical cost units
    (see :mod:`gtopt_shared.solution_csv`); *fallback_scale* is only used
    for legacy outputs that do not record their ``scale_objective``.  Both the CSV and Parquet
    paths now route through PyArrow so quoted CSV fields and
    Arrow-written headers are handled uniformly — the previous
    manual ``str.split(",")`` parser would mishandle quoted values.
    """
    sol_csv = results_dir / "solution.csv"
    sol_parquet = results_dir / "solution.parquet"
    if sol_csv.is_file():
        src = sol_csv
    elif sol_parquet.is_file():
        src = sol_parquet
    else:
        return {}

    # A solution file older than this run was left by a previous run
    # (gtopt failed before writing a new one): never report it as ours.
    if since is not None:
        try:
            mtime = src.stat().st_mtime
        except OSError:
            return {}
        if mtime < since:
            return {"stale": True, "stale_file": str(src)}

    try:
        if src.suffix == ".csv":
            import pyarrow.csv as pa_csv  # noqa: PLC0415

            table = pa_csv.read_csv(src)
        else:
            import pyarrow.parquet as pq  # noqa: PLC0415

            table = pq.read_table(src)

        if table.num_rows == 0 or "status" not in table.column_names:
            return {}

        df = table.to_pandas()
        result: dict = {"status": int(df["status"].iloc[0])}
        first_row = {str(c): str(df[c].iloc[0]) for c in df.columns}
        objective = resolve_objective(first_row, fallback_scale=fallback_scale)
        if objective is not None:
            result["objective"] = objective.value
            result["scale_objective"] = objective.scale_objective
        build = format_gtopt_build(
            first_row.get(GTOPT_VERSION), first_row.get(GTOPT_REVISION)
        )
        if build is not None:
            result["gtopt_build"] = build
        return result
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        log.warning("failed to parse %s: %s", src, exc)
        return {}


def _count_output_files(results_dir: Path) -> dict[str, int]:
    """Count output files by type in the results directory."""
    counts: dict[str, int] = {}
    if not results_dir.is_dir():
        return counts
    for f in results_dir.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower()
            if ext in (".parquet", ".csv", ".gz", ".zst"):
                counts[ext] = counts.get(ext, 0) + 1
    return counts


def _count_output_classes(results_dir: Path) -> list[str]:
    """List subdirectory names (element classes) in results."""
    if not results_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in results_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _format_size(nbytes: int) -> str:
    """Human-readable file size."""
    size = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_mb(mb: float) -> str:
    """Render a megabyte count as MB or GB depending on magnitude."""
    if mb >= 1024.0:
        return f"{mb / 1024.0:.1f} GB"
    return f"{mb:.0f} MB"


def _resource_summary_rows(results_dir: Path) -> list[tuple[str, str]]:
    """Pull resource-usage rows from the solver's status JSON.

    Reads ``solver_status.json`` next to *results_dir* and returns a
    short list of (label, value) pairs to append to the post-run
    summary.  Returns ``[]`` when no status file is available so that
    older / dry-run / aborted runs don't add empty placeholders.

    Surfaced fields:
      * **gtopt RSS**          — final + peak process resident size
      * **System free memory** — last poll of host available memory
      * **CPU**                — last load + average over the run
      * **LP pool**            — task count, avg per-task CPU and ΔRSS
    """
    status = load_status(results_dir / STATUS_FILE)
    if not status:
        return []

    rows: list[tuple[str, str]] = []

    rt = status.get("realtime") or {}
    rss_series = rt.get("process_rss_mb") or []
    cpu_series = rt.get("cpu_loads") or []
    free_series = rt.get("available_memory_mb") or []
    pct_series = rt.get("memory_percent") or []

    # Prefer the always-fresh realtime ring; fall back to the top-level
    # `pool_*` snapshot only when the ring is empty AND the snapshot is a
    # positive (real) reading.  The per-iteration `pool_*` fields can be a
    # stale 0 at the final write, which previously rendered "0 MB / 0% used".
    def _final(series: list, pool_key: str):
        if series:
            return float(series[-1])
        pool_v = status.get(pool_key)
        return float(pool_v) if pool_v is not None and float(pool_v) > 0.0 else None

    rss_now = _final(rss_series, "pool_process_rss_mb")
    free_now = _final(free_series, "pool_available_memory_mb")
    pct_now = _final(pct_series, "pool_memory_percent")

    rss_peak = max(rss_series) if rss_series else None

    if rss_now is not None:
        if rss_peak is not None and abs(rss_peak - float(rss_now)) > 1.0:
            rows.append(
                (
                    "gtopt memory (RSS)",
                    f"{_format_mb(float(rss_now))}  (peak {_format_mb(float(rss_peak))})",
                )
            )
        else:
            rows.append(("gtopt memory (RSS)", _format_mb(float(rss_now))))

    if free_now is not None:
        free_str = _format_mb(float(free_now))
        if pct_now is not None:
            free_str = f"{free_str}  ({float(pct_now):.0f}% used)"
        rows.append(("System free memory", free_str))

    if cpu_series:
        cpu_last = float(cpu_series[-1])
        cpu_avg = sum(float(c) for c in cpu_series) / len(cpu_series)
        rows.append(("CPU utilization", f"{cpu_last:.0f}%  (avg {cpu_avg:.0f}%)"))

    lp_stats = status.get("lp_task_stats") or {}
    dispatched = lp_stats.get("dispatched") or 0
    if dispatched:
        avg_cpu = float(lp_stats.get("avg_cpu_pct", 0.0))
        avg_mem = float(lp_stats.get("avg_rss_delta_mb", 0.0))
        rows.append(
            (
                "LP pool",
                f"{dispatched} tasks  avg CPU {avg_cpu:.0f}%  "
                f"avg ΔRSS {_format_mb(avg_mem)}",
            )
        )

    return rows


def _total_dir_size(path: Path) -> int:
    """Sum of all file sizes under *path*."""
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total


def _read_result_table(results_dir: Path, stem: str):
    """Try to read a result file as a pandas DataFrame.

    Handles three layouts transparently:

    * Legacy single file: ``{stem}.{ext}``.
    * Hive-partitioned parquet directory: ``{stem}.parquet/`` containing
      ``scene=<N>/phase=<M>/part.parquet`` — read as one frame via
      ``pd.read_parquet``.
    * CSV shards: ``{stem}_s*_p*.{csv,csv.zst,csv.gz}`` concatenated in
      sorted order.
    """
    try:
        import pandas as pd  # noqa: PLC0415

        pq_path = results_dir / (stem + ".parquet")
        if pq_path.is_dir() or pq_path.is_file():
            return pd.read_parquet(pq_path)

        parent = results_dir / Path(stem).parent
        name = Path(stem).name
        for ext in (".csv", ".csv.zst", ".csv.gz"):
            shards = sorted(parent.glob(f"{name}_s*_p*{ext}"))
            if shards:
                frames = [pd.read_csv(f) for f in shards]
                return pd.concat(frames, ignore_index=True) if frames else None

            fpath = results_dir / (stem + ext)
            if fpath.is_file():
                return pd.read_csv(fpath)
    except Exception:  # noqa: BLE001, S110  # pylint: disable=broad-exception-caught
        pass
    return None


def _compute_energy_indicators(
    results_dir: Path, planning_json: Path | None
) -> dict[str, float]:
    """Compute quick energy indicators from solver output.

    Returns a dict with keys like ``generated_twh``, ``served_twh``,
    ``shed_twh``, ``scale_objective``, ``discounted_energy_twh``.
    Missing indicators are omitted.
    """
    indicators: dict[str, float] = {}
    try:
        import numpy as np  # noqa: PLC0415

        import math  # noqa: PLC0415

        _HOURS_PER_YEAR = 8766.0  # 365.25 × 24, matching C++ hours_per_year

        # Load simulation structure from planning JSON
        durations: dict[int, float] = {}
        block_to_stage: dict[int, int] = {}
        # Generator uid → technology ``type`` (hydro_reservoir / hydro_ror /
        # thermal / solar / wind / gas / …), for the per-technology
        # generation breakdown in the summary.
        gen_type: dict[int, str] = {}
        # Effective discount factor per stage: combines the JSON
        # discount_factor with the annual discount rate applied at the
        # stage's start time (matching the C++ StageLp constructor).
        stage_effective_discount: dict[int, float] = {}
        scale_obj = 1000.0
        # Planning JSON contents; stays empty when no JSON is available so
        # the demand/bus lookups below degrade gracefully instead of
        # raising NameError (which silently dropped every indicator after
        # ``generated_twh``).
        data: dict = {}
        if planning_json and planning_json.is_file():
            with open(planning_json, encoding="utf-8") as f:
                data = json.load(f)
            blocks = data.get("simulation", {}).get("block_array", [])
            for b in blocks:
                durations[b["uid"]] = b.get("duration", 1.0)

            for g in data.get("system", {}).get("generator_array", []):
                if isinstance(g, dict) and "uid" in g:
                    gen_type[g["uid"]] = g.get("type") or "other"

            stages = data.get("simulation", {}).get("stage_array", [])
            block_uids = [b["uid"] for b in blocks]

            # Annual discount rate from options (same lookup as C++)
            opts = data.get("options", {})
            annual_rate = opts.get("annual_discount_rate", 0.0) or 0.0
            model_rate = opts.get("model_options", {}).get("annual_discount_rate", 0.0)
            if annual_rate == 0.0 and model_rate:
                annual_rate = model_rate

            # Build block → stage mapping and effective discount factors.
            # timeinit = cumulative hours from the start of the horizon
            # to the beginning of each stage (sum of block durations
            # for all blocks before this stage's first block).
            cumulative_hours = [0.0] * (len(block_uids) + 1)
            for i, b in enumerate(blocks):
                cumulative_hours[i + 1] = cumulative_hours[i] + b.get("duration", 1.0)

            for st in stages:
                first = st.get("first_block", 0)
                count = st.get("count_block", -1)
                json_df = st.get("discount_factor", 1.0) or 1.0
                end = first + count if count > 0 else len(block_uids)

                # C++ formula: exp(-ln(1+r) × timeinit / hours_per_year)
                timeinit = (
                    cumulative_hours[first] if first < len(cumulative_hours) else 0.0
                )
                if annual_rate > 0:
                    annual_df = math.exp(
                        -math.log1p(annual_rate) * timeinit / _HOURS_PER_YEAR
                    )
                else:
                    annual_df = 1.0

                stage_effective_discount[st["uid"]] = annual_df * json_df

                for bi in range(first, min(end, len(block_uids))):
                    block_to_stage[block_uids[bi]] = st["uid"]

            scale_obj = default_scale_objective(opts) or 1000.0
        indicators["scale_objective"] = scale_obj

        def _block_weight(blk: int, discounted: bool) -> float:
            """Per-block weight: duration, optionally × effective discount.

            The effective discount factor per stage combines the JSON
            ``discount_factor`` with ``exp(-ln(1+r)×t/8766)`` from the
            ``annual_discount_rate``, matching the C++ LP coefficients.
            """
            dur = durations.get(blk, 1.0)
            if not discounted:
                return dur
            stage_uid = block_to_stage.get(blk)
            eff_df = (
                stage_effective_discount.get(stage_uid, 1.0)
                if stage_uid is not None
                else 1.0
            )
            return dur * eff_df

        def _is_long(df) -> bool:
            """True for the long output layout (bare ``uid`` + ``value``
            columns, no per-element ``uid:N`` columns).  Mirrors
            ``gtopt_check_output._reader.dataset_layout``: gtopt switched the
            solution output to long (``output_layout: "long"``) in 2026-05,
            so the energy summary must read both shapes."""
            cols = set(df.columns)
            return (
                "uid" in cols
                and "value" in cols
                and not any(str(c).startswith("uid:") for c in cols)
            )

        def _energy_twh(df, discounted: bool = False) -> float:
            """Total dispatched energy → TWh, weighted by block duration
            (and effective discount when ``discounted``).  Handles both the
            long (``uid``/``value`` rows) and the legacy wide (``uid:N``
            columns) layouts.  Scenario/scene replicas are averaged so the
            result is the expected energy, not an N-fold sum."""
            if "block" not in df.columns:
                return 0.0
            if _is_long(df):
                # Mean ``value`` over scenario/scene replicas per
                # (block, uid), then Σ value × weight(block).
                grp = df.groupby(["block", "uid"], as_index=False)["value"].mean()
                vals = grp["value"].to_numpy(dtype=np.float64)
                wts = np.array(
                    [_block_weight(int(b), discounted) for b in grp["block"]],
                    dtype=np.float64,
                )
                return float(np.sum(vals * wts)) / 1e6  # MWh → TWh
            uid_cols = [c for c in df.columns if str(c).startswith("uid:")]
            if not uid_cols:
                return 0.0
            if "scenario" in df.columns:
                df = df.groupby("block", as_index=False)[uid_cols].mean()
            total = 0.0
            for _, row in df.iterrows():
                blk = int(row["block"])
                mw = float(np.sum(row[uid_cols].to_numpy(dtype=np.float64)))
                total += mw * _block_weight(blk, discounted)
            return total / 1e6  # MWh → TWh

        def _discounted_energy_twh(df) -> float:
            return _energy_twh(df, discounted=True)

        def _operational_cost(gen_df, srmc_df) -> float:
            """Total operational cost ($) = Σ srmc · dispatch · duration.

            ``srmc_sol`` is the generator's active-segment marginal cost
            (``vom + fuel``, $/MWh); multiplied by its dispatch (MW) and the
            block duration (h) it IS the operational cost (see
            ``source/generator_lp.cpp``: "total operational cost is
            srmc · dispatch · duration").  Long layout only (the current
            output); joined on (scenario, stage, block, uid) and averaged
            over scenarios to give the expected cost."""
            if not (_is_long(gen_df) and _is_long(srmc_df)):
                return 0.0
            keys = ["scenario", "stage", "block", "uid"]
            if any(k not in gen_df.columns for k in keys) or any(
                k not in srmc_df.columns for k in keys
            ):
                return 0.0
            merged = gen_df[keys + ["value"]].merge(
                srmc_df[keys + ["value"]], on=keys, suffixes=("_gen", "_srmc")
            )
            if merged.empty:
                return 0.0
            durs = np.array(
                [durations.get(int(b), 1.0) for b in merged["block"]],
                dtype=np.float64,
            )
            merged["_cost"] = (
                merged["value_gen"].to_numpy(dtype=np.float64)
                * merged["value_srmc"].to_numpy(dtype=np.float64)
                * durs
            )
            # Sum per scenario, then average across scenarios → expected $.
            per_scen = merged.groupby("scenario")["_cost"].sum()
            return float(per_scen.mean()) if not per_scen.empty else 0.0

        def _dual_weighted_cost(value_df, uid_to_bus, dual_df) -> float:
            """Σ value(uid) · bus_dual(bus(uid)) · duration, summed per
            scenario then averaged → expected $.

            ``value_df`` is a per-element flow (load_sol or generation_sol);
            ``dual_df`` is ``Bus/balance_dual`` (the LMP, $/MWh, keyed by bus
            uid).  Each element is priced at the marginal price of the bus it
            sits on.  Used for the LMP-valued withdrawal cost (load × LMP) and
            injection value (generation × LMP).  Long layout only."""
            cols = ["scenario", "block", "uid", "value"]
            if not (_is_long(value_df) and _is_long(dual_df)):
                return 0.0
            if any(c not in value_df.columns for c in cols) or any(
                c not in dual_df.columns for c in cols
            ):
                return 0.0
            v = value_df[cols].copy()
            v["bus"] = [uid_to_bus.get(int(u)) for u in v["uid"]]
            v = v[v["bus"].notna()]
            if v.empty:
                return 0.0
            dd = dual_df[cols].rename(columns={"uid": "bus", "value": "lmp"})
            v["bus"] = v["bus"].astype(dd["bus"].dtype)
            merged = v.merge(dd, on=["scenario", "block", "bus"], how="inner")
            if merged.empty:
                return 0.0
            durs = np.array(
                [durations.get(int(b), 1.0) for b in merged["block"]],
                dtype=np.float64,
            )
            merged["_cost"] = (
                merged["value"].to_numpy(dtype=np.float64)
                * merged["lmp"].to_numpy(dtype=np.float64)
                * durs
            )
            per_scen = merged.groupby("scenario")["_cost"].sum()
            return float(per_scen.mean()) if not per_scen.empty else 0.0

        # Generated energy
        gen_df = _read_result_table(results_dir, "Generator/generation_sol")
        if gen_df is not None:
            indicators["generated_twh"] = _energy_twh(gen_df)
            indicators["discounted_energy_twh"] = _discounted_energy_twh(gen_df)

            # Generation by technology → TWh.  Same expected-energy method as
            # _energy_twh (mean over scenario/scene per (block, uid), ×
            # duration), aggregated by the generator's ``type``.  Stored
            # under ``gentech::<type>`` keys so report_solution can render a
            # per-technology breakdown without changing the dict value type.
            if gen_type and _is_long(gen_df) and "uid" in gen_df.columns:
                grp = gen_df.groupby(["block", "uid"], as_index=False)["value"].mean()
                grp["mwh"] = grp["value"].to_numpy(dtype=np.float64) * np.array(
                    [durations.get(int(b), 1.0) for b in grp["block"]],
                    dtype=np.float64,
                )
                grp["tech"] = [gen_type.get(int(u), "other") for u in grp["uid"]]
                for tech, mwh in grp.groupby("tech")["mwh"].sum().items():
                    indicators[f"gentech::{tech}"] = float(mwh) / 1e6

        # Production cost (Σ srmc · dispatch · duration) from srmc_sol — the
        # actual variable resource cost (fuel + vom) of producing energy.
        srmc_df = _read_result_table(results_dir, "Generator/srmc_sol")
        if gen_df is not None and srmc_df is not None:
            prod_cost = _operational_cost(gen_df, srmc_df)
            if prod_cost > 0.0:
                indicators["production_cost"] = prod_cost

        # Restrict demand metrics to REAL consumer demands.  gtopt models
        # other consumers (notably battery charging) as Demand-class LP
        # elements that also write Demand/load_sol & Demand/fail_sol but are
        # NOT in demand_array — their unmet charge capacity shows up as huge
        # spurious "fail" (e.g. 211 TWh of battery non-charging on the CEN
        # 2-year case).  Counting only demand_array uids keeps "served" and
        # "shedding" about actual load.
        demand_uids = {
            d["uid"]
            for d in data.get("system", {}).get("demand_array", [])
            if isinstance(d, dict) and "uid" in d
        }

        def _real_demand(df):
            if df is None or "uid" not in getattr(df, "columns", []):
                return df
            return df[df["uid"].isin(demand_uids)] if demand_uids else df

        # Served demand (consumer load only)
        load_df = _read_result_table(results_dir, "Demand/load_sol")
        load_real = _real_demand(load_df)
        if load_real is not None:
            indicators["served_twh"] = _energy_twh(load_real)

        # LCOE = production cost / total served energy ($/MWh).
        served_twh = indicators.get("served_twh", 0.0)
        prod_cost = indicators.get("production_cost", 0.0)
        if prod_cost > 0.0 and served_twh > 0.0:
            indicators["lcoe_op_per_served"] = prod_cost / (served_twh * 1e6)

        # Load shedding (unserved consumer demand) — energy and penalty cost.
        fail_df = _real_demand(_read_result_table(results_dir, "Demand/fail_sol"))
        if fail_df is not None:
            shed = _energy_twh(fail_df)
            if shed > 1e-6:
                indicators["shed_twh"] = shed
            # Unserved-demand cost = unserved energy × VoLL.  Demands carry a
            # uniform fcost ($/MWh), so a representative VoLL × shed energy is
            # exact when uniform and a faithful estimate otherwise.
            fcosts = [
                float(d["fcost"])
                for d in data.get("system", {}).get("demand_array", [])
                if isinstance(d, dict) and isinstance(d.get("fcost"), (int, float))
            ]
            if shed > 1e-9 and fcosts:
                voll = float(np.median(fcosts))
                indicators["unserved_cost"] = shed * 1e6 * voll

        # Total losses.  Prefer the explicit Line/loss_sol (transmission
        # losses, emitted under ``--write-out extras``); otherwise fall back
        # to the system energy gap generated − served (covers transmission
        # plus storage/pump/converter consumption).  Reported in GWh and as
        # a percentage of served energy.
        gen_twh = indicators.get("generated_twh", 0.0)
        loss_df = _read_result_table(results_dir, "Line/loss_sol")
        if loss_df is not None:
            indicators["losses_twh"] = _energy_twh(loss_df)
        elif gen_twh > 0.0 and served_twh > 0.0:
            indicators["losses_twh"] = max(0.0, gen_twh - served_twh)
        if indicators.get("losses_twh", 0.0) > 0.0 and served_twh > 0.0:
            indicators["losses_pct_served"] = (
                100.0 * indicators["losses_twh"] / served_twh
            )

        # Bus-dual (LMP) valued costs.  Read Bus/balance_dual once and use it
        # for three things: the withdrawal cost (load × LMP, what demand pays
        # at the marginal price), the injection value (generation × LMP, what
        # generation earns at the marginal price), and the average LMP.  The
        # withdrawal−injection gap is the network surplus (congestion + loss
        # rents).  All expected over scenario/scene replicas.
        lmp_df = _read_result_table(results_dir, "Bus/balance_dual")
        if lmp_df is not None and _is_long(lmp_df) and "block" in lmp_df.columns:
            demand_bus = {
                d["uid"]: d.get("bus")
                for d in data.get("system", {}).get("demand_array", [])
                if isinstance(d, dict) and "uid" in d
            }
            gen_bus = {
                g["uid"]: g.get("bus")
                for g in data.get("system", {}).get("generator_array", [])
                if isinstance(g, dict) and "uid" in g
            }
            if load_df is not None:
                wd_cost = _dual_weighted_cost(load_df, demand_bus, lmp_df)
                if wd_cost > 0.0:
                    indicators["withdrawal_cost"] = wd_cost
            if gen_df is not None:
                inj_val = _dual_weighted_cost(gen_df, gen_bus, lmp_df)
                if inj_val > 0.0:
                    indicators["injection_value"] = inj_val

            lmp = lmp_df.groupby(["block", "uid"], as_index=False)["value"].mean()
            lmp["dur"] = [durations.get(int(b), 1.0) for b in lmp["block"]]
            avg_lmp = None
            if demand_bus and load_df is not None and _is_long(load_df):
                ld = load_df.groupby(["block", "uid"], as_index=False)["value"].mean()
                ld["bus"] = [demand_bus.get(int(u)) for u in ld["uid"]]
                ld = ld[ld["bus"].notna()]
                if not ld.empty:
                    load_bus = ld.groupby(["block", "bus"], as_index=False)[
                        "value"
                    ].sum()
                    load_bus = load_bus.rename(columns={"value": "load", "bus": "uid"})
                    load_bus["uid"] = load_bus["uid"].astype(lmp["uid"].dtype)
                    merged = lmp.merge(load_bus, on=["block", "uid"], how="inner")
                    wts = merged["load"].to_numpy(dtype=np.float64) * merged[
                        "dur"
                    ].to_numpy(dtype=np.float64)
                    if wts.sum() > 0.0:
                        avg_lmp = float(
                            (merged["value"].to_numpy(dtype=np.float64) * wts).sum()
                            / wts.sum()
                        )
            if avg_lmp is None:
                dur = lmp["dur"].to_numpy(dtype=np.float64)
                if dur.sum() > 0.0:
                    avg_lmp = float(
                        (lmp["value"].to_numpy(dtype=np.float64) * dur).sum()
                        / dur.sum()
                    )
            if avg_lmp is not None:
                indicators["avg_lmp"] = avg_lmp
            # Min/max bus marginal price across all (block, bus) cells
            # (expected over scenario/scene replicas).
            lmp_vals = lmp["value"].to_numpy(dtype=np.float64)
            if lmp_vals.size:
                indicators["lmp_min"] = float(lmp_vals.min())
                indicators["lmp_max"] = float(lmp_vals.max())

    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        log.debug("energy indicator computation failed", exc_info=True)

    return indicators


def report_solution(
    results_dir: Path,
    gtopt_dir: Path | None = None,
    planning_json: Path | None = None,
    since: float | None = None,
) -> None:
    """Print a Rich-formatted post-run results summary.

    *since* (a ``time.time()`` stamp taken before gtopt started) marks a
    ``solution.csv`` older than the run as stale instead of reporting it.
    """
    try:
        from gtopt_check_json._terminal import (  # noqa: PLC0415
            print_table,
        )
    except ImportError:
        # Fallback: plain text
        _report_solution_plain(results_dir, gtopt_dir, since)
        return

    # ── Energy indicators (fast) ──
    indicators = _compute_energy_indicators(results_dir, planning_json)

    # ── Solution status ──
    # ``objective`` is already in physical units ($); the planning JSON's
    # scale_objective is only a fallback for legacy solution.csv files.
    sol = check_solution(
        results_dir,
        fallback_scale=indicators.get("scale_objective"),
        since=since,
    )
    true_obj: float | None = sol.get("objective")
    status_str = _solution_status_label(sol)

    # ── Build rows ──
    rows: list[tuple[str, str]] = []
    rows.append(("Status", status_str))
    if true_obj is not None:
        rows.append(("Objective value", f"${true_obj:,.2f}"))

    if "generated_twh" in indicators:
        rows.append(("Generated energy", f"{indicators['generated_twh']:.3f} TWh"))
        # Per-technology breakdown (TWh + % of generation), largest first.
        total_gen = indicators.get("generated_twh", 0.0)
        tech_rows = sorted(
            (
                (k.split("::", 1)[1], v)
                for k, v in indicators.items()
                if k.startswith("gentech::")
            ),
            key=lambda kv: -kv[1],
        )
        for tech, twh in tech_rows:
            pct = (100.0 * twh / total_gen) if total_gen > 0 else 0.0
            rows.append((f"  {tech}", f"{twh:.3f} TWh ({pct:.1f}%)"))
    if "served_twh" in indicators:
        rows.append(("Served demand", f"{indicators['served_twh']:.3f} TWh"))
    if "shed_twh" in indicators:
        rows.append(("Load shedding", f"{indicators['shed_twh']:.3f} TWh"))

    # Total losses in GWh and as a % of served energy.
    if "losses_twh" in indicators:
        loss_gwh = indicators["losses_twh"] * 1e3
        loss_pct: float | None = indicators.get("losses_pct_served")
        loss_str = f"{loss_gwh:,.1f} GWh"
        if loss_pct is not None:
            loss_str = f"{loss_str}  ({loss_pct:.1f}% of served)"
        rows.append(("Total losses", loss_str))

    # Total-cost views — each as a total $ and a per-energy unit cost:
    #   • Production cost (SRMC)   Σ generation·srmc·dur — real variable
    #     resource cost (fuel+vom); /MWh served = LCOE.
    #   • Withdrawal cost (LMP)    Σ load·bus_dual·dur — what demand pays at
    #     the marginal price; /MWh served = avg price paid.
    #   • Injection value (LMP)    Σ generation·bus_dual·dur — what generation
    #     earns at the marginal price; /MWh gen = avg price earned.
    #   • Unserved demand          Σ fail·fcost·dur — curtailment penalty;
    #     /MWh unserved = effective VoLL.
    # Withdrawal − Injection = network surplus (congestion + loss rents).
    served_mwh = indicators.get("served_twh", 0.0) * 1e6
    gen_mwh = indicators.get("generated_twh", 0.0) * 1e6
    shed_mwh = indicators.get("shed_twh", 0.0) * 1e6

    def _cost_row(label: str, total: float, denom_mwh: float, basis: str):
        if denom_mwh > 0.0:
            return (label, f"${total:,.2f}  (${total / denom_mwh:.2f}/MWh {basis})")
        return (label, f"${total:,.2f}")

    if "production_cost" in indicators:
        rows.append(
            _cost_row(
                "Production cost (SRMC)",
                indicators["production_cost"],
                served_mwh,
                "served",
            )
        )
    if "withdrawal_cost" in indicators:
        rows.append(
            _cost_row(
                "Withdrawal cost (LMP)",
                indicators["withdrawal_cost"],
                served_mwh,
                "served",
            )
        )
    if "injection_value" in indicators:
        rows.append(
            _cost_row(
                "Injection value (LMP)",
                indicators["injection_value"],
                gen_mwh,
                "gen",
            )
        )
    if "unserved_cost" in indicators:
        rows.append(
            _cost_row(
                "Unserved demand",
                indicators["unserved_cost"],
                shed_mwh,
                "unserved",
            )
        )
    if "avg_lmp" in indicators:
        avg = indicators["avg_lmp"]
        lo = indicators.get("lmp_min")
        hi = indicators.get("lmp_max")
        if lo is not None and hi is not None:
            lmp_str = f"min ${lo:.2f} / avg ${avg:.2f} / max ${hi:.2f} /MWh"
        else:
            lmp_str = f"${avg:.2f}/MWh"
        rows.append(("LMP (min/avg/max)", lmp_str))

    # Avg generation cost = total cost / generated energy
    gen_twh = indicators.get("generated_twh", 0.0)
    if true_obj is not None and gen_twh > 0:
        gen_mwh = gen_twh * 1e6
        avg_cost = true_obj / gen_mwh
        rows.append(("Avg generation cost", f"${avg_cost:.2f}/MWh"))

    # LCOE = discounted total cost / discounted generated energy
    # The solver objective IS the sum of discounted costs (each stage's
    # cost is already multiplied by discount_factor in the LP).
    disc_twh = indicators.get("discounted_energy_twh", 0.0)
    if true_obj is not None and disc_twh > 0:
        disc_mwh = disc_twh * 1e6
        lcoe = true_obj / disc_mwh
        rows.append(("LCOE", f"${lcoe:.2f}/MWh"))

    # ── File inventory ──
    file_counts = _count_output_files(results_dir)
    total_size = _total_dir_size(results_dir)
    if file_counts:
        total_files = sum(file_counts.values())
        ext_str = ", ".join(f"{ext}: {cnt}" for ext, cnt in sorted(file_counts.items()))
        rows.append(("Output files", f"{total_files} ({_format_size(total_size)})"))
        rows.append(("  by type", ext_str))

    # ── Resources (gtopt RSS / system free / CPU / LP-pool) ──
    for label, value in _resource_summary_rows(results_dir):
        rows.append((label, value))

    # ── Paths ──
    if "gtopt_build" in sol:
        rows.append(("gtopt", sol["gtopt_build"]))
    rows.append(("Results", str(results_dir)))
    cwd = Path.cwd()
    log_path = latest_gtopt_log(
        results_dir,
        since,
        extra_dirs=(cwd / "output" / "logs", cwd / "results" / "logs"),
    )
    if log_path is not None:
        rows.append(("Log file", str(log_path)))
    status_path = results_dir / "solver_status.json"
    if status_path.is_file():
        rows.append(("Status file", str(status_path)))

    # The shared gtopt_check_json console is fixed at 120 columns; fit
    # the table to the actual terminal so it does not wrap at 80.
    print_table(
        headers=["Property", "Value"],
        rows=rows,
        aligns=["left", "right"],
        title="Results Summary",
        width=min(shutil.get_terminal_size((120, 24)).columns, 120),
        # Keep the labels whole; long paths wrap in the value column.
        min_widths=[max(len(label) for label, _ in rows), None],
        wraps=[False, True],
    )


def latest_gtopt_log(
    results_dir: Path,
    since: float | None = None,
    extra_dirs: tuple[Path, ...] = (),
) -> Path | None:
    """Newest ``gtopt_N.log`` (the name gtopt writes) in
    ``<results>/logs`` or any of *extra_dirs* (the same fallbacks the
    tail thread watches: gtopt may resolve its log directory against its
    CWD).  With *since*, a log older than the run is ignored.
    """
    candidates: list[tuple[float, Path]] = []
    for logs in (results_dir / "logs", *extra_dirs):
        try:
            candidates.extend((p.stat().st_mtime, p) for p in logs.glob("gtopt_*.log"))
        except OSError:
            continue
    if not candidates:
        return None
    mtime, path = max(candidates)
    if since is not None and mtime < since:
        return None
    return path


def _solution_status_label(sol: dict) -> str:
    """Human label for the ``status`` read by :func:`check_solution`."""
    if sol.get("stale"):
        return "NO NEW SOLUTION (stale file from a previous run)"
    status: int | None = sol.get("status")
    if status is None:
        return "NO SOLUTION FILE"
    return {0: "OPTIMAL", 1: "NON-OPTIMAL / INFEASIBLE"}.get(
        status, f"UNKNOWN ({status})"
    )


def _report_solution_plain(
    results_dir: Path,
    gtopt_dir: Path | None = None,
    since: float | None = None,
) -> None:
    """Fallback plain-text results summary."""
    sol = check_solution(results_dir, since=since)
    print(f"  Status: {_solution_status_label(sol)}")
    obj = sol.get("objective")
    if obj is not None:
        print(f"  Objective: {obj:.6g}")
    print(f"  Results: {results_dir}")
