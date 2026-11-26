# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Rich terminal UI for interactive gtopt solver monitoring.

When gtopt runs in an interactive terminal, this module provides a
live dashboard (inspired by the Claude Code aesthetic) that shows:

  - Solver status, case name, method, elapsed time
  - Iteration progress bar with current pass indicator
  - Convergence statistics (LB, UB, gap) with sparkline trend
  - Iteration history table
  - System resource usage (CPU load, active workers)
  - Live solver log output
  - Interactive command bar (stop, stats, help)

When stdout is not a TTY (piped, redirected, or running in background),
the display is disabled and solver output flows to stdout/log files
unchanged.

Display modes (switch with Tab or 1/2/3):

  1  Dashboard — stats, progress, iteration history, log
  2  Grid — SDDP phase activity grid (forward/backward/elastic/aperture)
  3  Convergence — per-scene cost heatmap + bound sparklines

Interactive commands (single-key, no Enter required):

  s  Graceful stop — saves cuts, finishes current iteration
  i  Toggle system stats overlay (from planning JSON)
  h  Toggle help overlay
  q  Quit — terminate the solver immediately

Structure: this module is the *controller* (:class:`SolverDisplay`:
threads, keys, status polling, stop requests).  The pieces live in
:mod:`run_gtopt.tui`:

  - ``parse``    log-line trackers (plan steps, SDDP grid, levels)
  - ``model``    :class:`~run_gtopt.tui.model.RunState`, layouts, modes
  - ``panels`` / ``views`` / ``overlays`` / ``summary``  pure renderers
  - ``frame``    :func:`~run_gtopt.tui.frame.render_frame` (state -> frame)
  - ``keys`` / ``term``  terminal input and capabilities

``solver_status.json`` is found and read by :mod:`gtopt_shared.status`.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gtopt_shared.status import (
    PASS_NAMES as _PASS_NAMES,
    STOP_REQUEST_FILE as _STOP_REQUEST_FILE,
    find_planning_json,
    find_status_file,
    load_status,
)

from ._paths import write_stop_request
from .tui.fmt import (
    PROGRESS_BAR_WIDTH as _PROGRESS_BAR_WIDTH,
    SPARKLINE_WIDTH as _SPARKLINE_WIDTH,
    progress_bar as _bar,
    format_elapsed as _format_elapsed,
    format_mb as _format_mb,
    format_number as _format_number,
    spark_range as _spark_range,
    sparkline as _sparkline,
)
from .tui.frame import render_frame
from .tui.keys import (
    ESC_KEYS as _ESC_KEYS,
    NAV_KEY_ALIASES as _NAV_KEY_ALIASES,
    enter_cbreak as _enter_cbreak,
    exit_cbreak as _exit_cbreak,
    poll_key as _poll_key,
)
from .tui.model import (
    DEFAULT_CONVERGENCE_TOL as _DEFAULT_CONVERGENCE_TOL,
    MAX_HISTORY_ROWS as _MAX_HISTORY_ROWS,
    MAX_LOG_LINES as _MAX_LOG_LINES,
    MODE_CONVERGENCE as _MODE_CONVERGENCE,
    MODE_COUNT as _MODE_COUNT,
    MODE_DASHBOARD as _MODE_DASHBOARD,
    MODE_GRID as _MODE_GRID,
    MODE_NAMES as _MODE_NAMES,
    OVERLAY_HELP,
    OVERLAY_PATHS,
    OVERLAY_STATS,
    Layout as _Layout,
    RunState,
    layout_for,
    load_system_stats as _load_system_stats,
)
from .tui.overlays import (
    build_help_overlay as _build_help_overlay,
    build_paths_overlay as _build_paths_overlay,
    build_stats_overlay as _build_stats_overlay,
)
from .tui.panels import (
    FINAL_STATES as _FINAL_STATES,
    PASS_STYLES as _PASS_STYLES,
    SPINNER as _SPINNER,
    STATUS_STALE_S as _STATUS_STALE_S,
    build_async_panel as _build_async_panel,
    build_command_bar as _build_command_bar,
    build_header as _build_header,
    build_history as _build_history,
    build_log as _build_log,
    build_plan_panel as _build_plan_panel,
    build_progress as _build_progress,
    build_stats as _build_stats,
    build_system as _build_system,
    has_resource_telemetry as _has_resource_telemetry,
    log_line_style as _log_line_style,
    sddp_eta as _sddp_eta,
)
from .tui.parse import (
    COUNTED_LEVELS as _COUNTED_LEVELS,
    ECHO_PREFIX_RE as _ECHO_PREFIX_RE,
    GRID_APERTURE as _GRID_APERTURE,
    GRID_BACKWARD as _GRID_BACKWARD,
    GRID_ELASTIC as _GRID_ELASTIC,
    GRID_FORWARD as _GRID_FORWARD,
    GRID_IDLE as _GRID_IDLE,
    GRID_INFEASIBLE as _GRID_INFEASIBLE,
    GRID_MAX_ITERS as _GRID_MAX_ITERS,
    LOG_TS_RE as _LOG_TS_RE,
    Clock,
    SDDPGridTracker,
    SolverPhaseTracker,
    log_line_level,
)
from .tui.summary import build_final_summary, build_outcome_banner
from .tui.term import (
    UI_MODES,
    AsciiRender as _AsciiRender,
    ascii_wanted,
    is_interactive,
    make_console,
    set_ui_mode,
)
from .tui.views import (
    HEAT_BLOCKS as _HEAT_BLOCKS,
    HEAT_STYLES as _HEAT_STYLES,
    build_convergence as _build_convergence,
    build_sddp_grid as _build_sddp_grid,
    heat_style as _heat_style,
)

if TYPE_CHECKING:
    from rich.console import Group

    from ._process import RunResult

log = logging.getLogger(__name__)

# Names kept importable from ``run_gtopt._tui`` (tests, older callers);
# the definitions live in :mod:`run_gtopt.tui`.
__all__ = [
    "UI_MODES",
    "RunState",
    "SDDPGridTracker",
    "SolverDisplay",
    "SolverPhaseTracker",
    "_AsciiRender",
    "_COUNTED_LEVELS",
    "_DEFAULT_CONVERGENCE_TOL",
    "_ECHO_PREFIX_RE",
    "_ESC_KEYS",
    "_FINAL_STATES",
    "_GRID_APERTURE",
    "_GRID_BACKWARD",
    "_GRID_ELASTIC",
    "_GRID_FORWARD",
    "_GRID_IDLE",
    "_GRID_INFEASIBLE",
    "_HEAT_BLOCKS",
    "_HEAT_STYLES",
    "_MAX_HISTORY_ROWS",
    "_MODE_CONVERGENCE",
    "_MODE_COUNT",
    "_MODE_DASHBOARD",
    "_MODE_GRID",
    "_MODE_NAMES",
    "_NAV_KEY_ALIASES",
    "_PASS_NAMES",
    "_PASS_STYLES",
    "_PROGRESS_BAR_WIDTH",
    "_SPARKLINE_WIDTH",
    "_SPINNER",
    "_STATUS_STALE_S",
    "_STOP_REQUEST_FILE",
    "_bar",
    "_build_async_panel",
    "_build_command_bar",
    "_build_convergence",
    "_build_header",
    "_build_help_overlay",
    "_build_history",
    "_build_log",
    "_build_paths_overlay",
    "_build_plan_panel",
    "_build_progress",
    "_build_sddp_grid",
    "_build_stats",
    "_build_stats_overlay",
    "_build_system",
    "_find_planning_json",
    "_find_status_file",
    "_format_elapsed",
    "_format_mb",
    "_format_number",
    "_has_resource_telemetry",
    "_heat_style",
    "_load_status",
    "_load_system_stats",
    "_log_line_style",
    "_sddp_eta",
    "_spark_range",
    "_sparkline",
    "ascii_wanted",
    "is_interactive",
    "layout_for",
    "log_line_level",
    "make_console",
    "set_ui_mode",
]

_POLL_INTERVAL = 1.0  # seconds between status file polls
# Rich Live refresh rate.  At 4 Hz the dashboard noticeably flickered and
# pegged a CPU core — 2 Hz is plenty for human feedback (Rich coalesces
# multiple updates per frame anyway) and roughly halves the TUI thread's
# CPU footprint.
_REFRESH_PER_SECOND = 2
# Keyboard poll period of the render loop (status polls stay at
# ``poll_interval``).
_KEY_INTERVAL = 0.05

# ``q`` terminates the solver; past this run age it asks y/N first.
_CONFIRM_QUIT_AFTER_S = 60.0


def _load_status(path: Path | None) -> dict[str, Any]:
    """The status JSON with its age stamped (``{}`` on failure)."""
    return load_status(path, stamp_age=True)


def _find_status_file(case_dir: Path, output_dir: Path | None = None) -> Path:
    """See :func:`gtopt_shared.status.find_status_file`."""
    return find_status_file(case_dir, output_dir)


def _find_planning_json(case_dir: Path) -> Path | None:
    """See :func:`gtopt_shared.status.find_planning_json`."""
    return find_planning_json(case_dir)


class SolverDisplay:  # pylint: disable=too-many-instance-attributes
    """Rich-based live dashboard for gtopt solver monitoring.

    Start the display in a background thread with :meth:`start`.  Feed
    solver output lines with :meth:`add_log_line`.  Stop with
    :meth:`stop` (blocks until the render thread exits).

    The display thread polls the solver status JSON file and redraws
    the frame each cycle: :meth:`snapshot` captures a
    :class:`~run_gtopt.tui.model.RunState` under the lock and
    :func:`~run_gtopt.tui.frame.render_frame` renders it.  It also reads
    single-key commands from stdin (in cbreak mode).

    *clock* (default ``time.monotonic``) and *console* (default
    :func:`make_console`) are injectable so a recorded run replays to
    the same frames.  If rendering fails, the dashboard reports it once
    and falls back to plain output (:attr:`plain_fallback`).
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        case_name: str,
        case_dir: Path,
        poll_interval: float = _POLL_INTERVAL,
        solver_hint: str = "",
        output_dir: Path | None = None,
        confirm_quit_after: float | None = _CONFIRM_QUIT_AFTER_S,
        *,
        clock: Clock = time.monotonic,
        console: Any = None,
    ) -> None:
        self.case_name = case_name
        self._output_dir = output_dir
        # ``q`` asks y/N once the run is older than this (None = never;
        # observer mode, where q only detaches).
        self._confirm_quit_after = confirm_quit_after
        self._quit_pending = False
        self._shutdown_msg: str | None = None
        # False when stdin is not a TTY (cbreak unavailable): no keys.
        self._keys_enabled = True
        self.poll_interval = poll_interval
        self._status_file: Path | None = None
        self._case_dir = case_dir
        self._solver_hint = solver_hint
        self._clock = clock

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._log_lines: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._last_log_key: str | None = None
        self._last_log_repeat = 0
        #: Counts and first distinct messages for the final summary.
        self.warning_count = 0
        self.error_count = 0
        self.warnings: list[str] = []
        # Counted warning/error messages per source whose copy from the
        # other source (log file vs stderr echo) has not arrived yet.
        self._uncounted_copies: dict[str, set[str]] = {"log": set(), "echo": set()}
        #: The gtopt_N.log being tailed, once discovered.
        self.log_path: Path | None = None
        self._console: Any = console
        self._status: dict[str, Any] = {}
        self._start_time = clock()
        self._lock = threading.Lock()
        self._phase_tracker = SolverPhaseTracker(clock=clock)
        self._grid_tracker = SDDPGridTracker()
        #: Set when the render thread failed: log lines go to stdout.
        self.plain_fallback = False

        # Interactive command state
        self._show_help = False
        self._show_stats = False
        self._show_paths = False
        self._display_mode = _MODE_DASHBOARD
        self._stop_sent = False
        self._system_stats: dict[str, Any] = {}
        self.quit_requested = threading.Event()

        # Grid-mode scroll state (vim-style j/k/J/K/g/G).  Tracks the
        # offset into the scene list rendered below the pinned
        # aggregate row.  Reset to 0 whenever the scene set changes
        # so a freshly-discovered scene scrolls back into view.
        self._grid_scroll_offset: int = 0
        self._grid_user_scrolled: bool = False
        self._grid_last_scene_count: int = 0

    @property
    def console(self) -> Any:
        """One shared Rich Console (see :func:`make_console`)."""
        if self._console is None:
            self._console = make_console()
        return self._console

    def _render(self, renderable: Any) -> Any:
        """*renderable*, ASCII-only when :func:`ascii_wanted`."""
        if ascii_wanted(self.console.encoding):
            return _AsciiRender(renderable)
        return renderable

    # -- public API --------------------------------------------------------

    # Patterns to detect solver and method from gtopt log output
    _SOLVER_RE = re.compile(r"Solver:\s+(\S+)")
    _SDDP_RE = re.compile(r"SDDP[: ]")
    _MONO_RE = re.compile(r"Monolithic(?:Method|Solver):")

    def set_log_path(self, path: Path) -> None:
        """Record the gtopt log being tailed (shown in the Output title)."""
        self.log_path = path

    def _first_sighting(self, line: str) -> bool:
        """False when *line* is the copy of a message already counted from
        the other source: gtopt writes warnings / errors both to its log
        file and to stderr (echoed here with a ``gtopt:`` prefix)."""
        source = "echo" if line.startswith("gtopt:") else "log"
        other = "log" if source == "echo" else "echo"
        norm = _ECHO_PREFIX_RE.sub("", line).strip()
        pending = self._uncounted_copies[other]
        if norm in pending:
            pending.discard(norm)
            return False
        self._uncounted_copies[source].add(norm)
        return True

    def add_log_line(self, line: str) -> None:
        """Append a solver output line (thread-safe).

        A message identical to the previous one (timestamps ignored) is
        collapsed into that line with a ``(×N)`` count; warnings are also
        counted for the final summary.  After a dashboard failure
        (:attr:`plain_fallback`) the line is also printed to stdout.
        """
        stripped = line.rstrip()
        key = _LOG_TS_RE.sub("", stripped)
        with self._lock:
            level = log_line_level(stripped)
            if level in _COUNTED_LEVELS and self._first_sighting(stripped):
                if level in ("warning", "warn"):
                    self.warning_count += 1
                    if key not in self.warnings and len(self.warnings) < 5:
                        self.warnings.append(key)
                else:
                    self.error_count += 1
            if key == self._last_log_key and self._log_lines:
                self._last_log_repeat += 1
                self._log_lines[-1] = f"{stripped}  (×{self._last_log_repeat})"
            else:
                self._last_log_key = key
                self._last_log_repeat = 1
                self._log_lines.append(stripped)
            self._phase_tracker.process_line(stripped)
            self._grid_tracker.process_line(stripped)
            # Detect solver from log output (e.g. "  Solver: cplex/22.1.1")
            if not self._system_stats.get("solver"):
                m = self._SOLVER_RE.search(stripped)
                if m:
                    self._system_stats["solver"] = m.group(1)
            # Detect method from log output
            if not self._system_stats.get("method"):
                if self._SDDP_RE.search(stripped):
                    self._system_stats["method"] = "sddp"
                elif self._MONO_RE.search(stripped):
                    self._system_stats["method"] = "monolithic"
        if self.plain_fallback:
            print(stripped, flush=True)

    def load_planning_stats(self) -> None:
        """Seed the method / solver / tolerance from the planning JSON."""
        stats = _load_system_stats(_find_planning_json(self._case_dir))
        if self._solver_hint:
            stats["solver"] = self._solver_hint
        self._system_stats = stats

    def start(self) -> None:
        """Launch the dashboard render thread."""
        self._start_time = self._clock()
        # Initialize system stats from planning JSON so the method is
        # displayed correctly from the very first frame.
        self.load_planning_stats()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="gtopt-tui"
        )
        self._thread.start()

    def update_status(self, status: dict[str, Any]) -> None:
        """Adopt a freshly read ``solver_status.json`` (no I/O)."""
        self._status = status
        self._sync_stats_from_status()

    def poll_status(self) -> None:
        """Find (once it exists) and read the status file."""
        if self._status_file is None or not self._status_file.is_file():
            self._status_file = _find_status_file(self._case_dir, self._output_dir)
        self.update_status(_load_status(self._status_file))

    def _sync_stats_from_status(self) -> None:
        """Propagate solver/method and phase grid from the status JSON."""
        status = self._status
        if not status:
            return
        with self._lock:
            if not self._system_stats.get("solver"):
                solver = status.get("solver", "")
                if solver:
                    self._system_stats["solver"] = solver
            if not self._system_stats.get("method"):
                method = status.get("method", "")
                if method:
                    self._system_stats["method"] = method
            # Merge phase grid from status JSON (enables remote monitoring)
            if "phase_grid" in status:
                self._grid_tracker.load_from_status(status)

    def stop(self) -> None:
        """Signal the render thread to finish and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def print_final(self, result: RunResult | int) -> None:
        """Print a static completion banner after the live display ends.

        *result* is the :class:`~run_gtopt._process.RunResult` of the run
        (a bare return code is accepted for callers without one).  On
        failure the banner lists gtopt's critical/error lines, the last
        lines it printed on stdout/stderr and where the full capture and
        the gtopt log live.
        """
        from ._process import (  # noqa: PLC0415
            RunResult as _RunResult,
            error_lp_hint,
            failure_details,
            run_paths,
        )

        if not isinstance(result, _RunResult):
            result = _RunResult(returncode=result)
        if not result.status:
            result.status = dict(self._status)
        outcome = result.outcome(stop_requested=self._stop_sent)
        failing = outcome.kind in ("failed", "killed", "non_optimal")
        panel = build_outcome_banner(
            outcome,
            self._clock() - self._start_time,
            failure_lines=failure_details(result) if failing else (),
            hint_lines=error_lp_hint(result.output_dir),
            paths=run_paths(result),
        )
        console = self.console
        for renderable in self._final_summary(result):
            console.print(self._render(renderable))
        console.print(self._render(panel))

    def _final_summary(self, result: RunResult) -> list[Any]:
        """What stays on screen after the dashboard: the last iterations,
        step timings, warning/error counts and the first distinct
        warnings.  Printed above the outcome banner."""
        history = (result.status or {}).get("history") or []
        with self._lock:
            plan = self._phase_tracker.snapshot()
            warnings = list(self.warnings)
            n_warn, n_err = self.warning_count, self.error_count
        return build_final_summary(history, plan, warnings, n_warn, n_err)

    # -- command handlers --------------------------------------------------

    def _handle_key(self, key: str) -> None:
        """Process a single-key command."""
        if self._quit_pending:
            # Answer to "Terminate gtopt? [y/N]": anything but y cancels.
            self._quit_pending = False
            if key in ("y", "Y"):
                self.quit_requested.set()
            return
        if key == "ESC":  # close overlays
            self._show_help = False
            self._show_stats = False
            self._show_paths = False
            return
        key = _NAV_KEY_ALIASES.get(key, key)
        if key in ("s", "S"):
            self._cmd_stop()
        elif key in ("i", "I"):
            self._show_stats = not self._show_stats
            self._show_help = False
            # Lazy-load stats from planning JSON on first toggle
            if self._show_stats and not self._system_stats.get("elements"):
                json_path = _find_planning_json(self._case_dir)
                file_stats = _load_system_stats(json_path)
                # Merge file stats under log-detected values (log wins)
                for k, v in file_stats.items():
                    if k not in self._system_stats or not self._system_stats[k]:
                        self._system_stats[k] = v
        elif key in ("\t",):  # Tab — cycle display mode
            self._display_mode = (self._display_mode + 1) % _MODE_COUNT
        elif key == "1":
            self._display_mode = _MODE_DASHBOARD
        elif key == "2":
            self._display_mode = _MODE_GRID
        elif key == "3":
            self._display_mode = _MODE_CONVERGENCE
        elif key in ("h", "H", "?"):
            self._show_help = not self._show_help
            self._show_stats = False
            self._show_paths = False
        elif key in ("o", "O"):
            self._show_paths = not self._show_paths
            self._show_help = False
            self._show_stats = False
        elif key in ("q", "Q"):
            elapsed = self._clock() - self._start_time
            if (
                self._confirm_quit_after is not None
                and elapsed >= self._confirm_quit_after
            ):
                self._quit_pending = True
            else:
                self.quit_requested.set()
        elif self._display_mode == _MODE_GRID and key in (
            "j",
            "k",
            "J",
            "K",
            "g",
            "G",
        ):
            self._scroll_grid(key)

    def _scroll_grid(self, key: str) -> None:
        """Adjust the per-scene scroll offset in Grid mode (vim keys).

        Page sizes are intentionally fixed (1 line / 5 lines) rather
        than read from the live console height — the render path
        clamps the offset to the visible window anyway, and using a
        fixed step keeps the user's mental model stable across
        terminal resizes.  ``g``/``G`` jump to top/bottom; both also
        latch ``_grid_user_scrolled`` so the auto-follow logic in
        :meth:`_grid_visible_window` stops yanking the viewport away
        from what the user explicitly asked for.
        """
        n_scenes = len(self._grid_tracker.scenes)
        if n_scenes == 0:
            return
        self._grid_user_scrolled = True
        if key == "j":
            self._grid_scroll_offset += 1
        elif key == "k":
            self._grid_scroll_offset -= 1
        elif key == "J":
            self._grid_scroll_offset += 5
        elif key == "K":
            self._grid_scroll_offset -= 5
        elif key == "g":
            self._grid_scroll_offset = 0
            self._grid_user_scrolled = False  # re-enable auto-follow
        else:  # "G" (caller only forwards j/k/J/K/g/G)
            self._grid_scroll_offset = max(0, n_scenes - 1)
        # Clamp; final clamping against the dynamic visible-window
        # size happens in the renderer.
        self._grid_scroll_offset = max(
            0, min(self._grid_scroll_offset, max(0, n_scenes - 1))
        )

    def _grid_visible_window(self) -> tuple[int | None, int | None]:
        """Return ``(max_visible_scenes, active_scene_hint)`` for the
        Grid panel renderer.

        The visible scene count is derived from the live console height
        minus a fixed budget for the surrounding panels (header, plan,
        progress, command bar, log) plus the legend, the pinned
        aggregate row, and the per-scene phase header.  Scenes that
        wouldn't fit are scrolled off-screen and surfaced as
        "▲ N above / ▼ N below" ribbons.

        Auto-follow: if the user hasn't manually scrolled (i.e. used
        j/k/J/K/G — but ``g`` resets the latch) and the active scene
        falls outside the current window, the offset is yanked so it
        stays visible.  This matches what an SDDP operator wants —
        watch the scene currently being solved without having to keep
        pressing j.
        """

        scenes = self._grid_tracker.scenes
        n = len(scenes)
        if n == 0:
            return None, None

        # Console height — fall back to a sane default if Rich can't
        # detect the terminal (e.g. piped stdout).
        try:
            height = self.console.size.height
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            height = 40
        # Per-scene block height = scene header (1) + phase axis (1)
        # + iter rows (_GRID_MAX_ITERS).  Aggregate row + legend +
        # other panels eat ~20 lines of fixed overhead.
        per_block = _GRID_MAX_ITERS + 2
        budget = max(1, (height - 22) // per_block)

        # Reset scroll if scene list shrank below the offset.
        if self._grid_scroll_offset >= n:
            self._grid_scroll_offset = 0

        # Auto-follow active scene when the user hasn't scrolled.
        active = self._grid_tracker.active_cell
        active_scene = active[0] if active is not None else None
        if (
            not self._grid_user_scrolled
            and active_scene is not None
            and active_scene in scenes
        ):
            idx = scenes.index(active_scene)
            if idx < self._grid_scroll_offset:
                self._grid_scroll_offset = idx
            elif idx >= self._grid_scroll_offset + budget:
                self._grid_scroll_offset = max(0, idx - budget + 1)

        # Reset latch if scene set changed (new scenes appeared).
        if n != self._grid_last_scene_count:
            self._grid_last_scene_count = n
        return budget, active_scene

    def _cmd_stop(self) -> None:
        """Create the stop-request file for a graceful solver stop.

        The stop file must be written into whichever directory the
        solver is reading — that is, ``options.output_directory`` from
        the planning JSON (canonical "results/" for cascade cases,
        "output/" for the older convention).  Falling back to
        "<case>/output/" silently broke graceful stop on every cascade
        run.  We mirror :func:`_find_status_file`'s discovery logic so
        stop and status always agree.
        """
        if self._stop_sent:
            return
        # Already-discovered status file is the highest-confidence
        # signal of where the solver writes — drop the stop request
        # next to it.  Falling back to the same candidate-directory
        # search keeps the behavior consistent for early stops issued
        # before the first status JSON appears.
        if self._status_file is not None:
            stop_dir = self._status_file.parent
        else:
            stop_dir = _find_status_file(self._case_dir, self._output_dir).parent
        if write_stop_request(stop_dir, source="run_gtopt_tui") is None:
            return
        self._stop_sent = True
        with self._lock:
            self._log_lines.append(
                ">>> Graceful stop requested — solver will finish "
                "current iteration and save cuts"
            )

    def path_entries(self) -> list[tuple[str, str]]:
        """Rows of the ``o`` overlay (output dir, log, status, error LPs)."""
        status_file = self._status_file or _find_status_file(
            self._case_dir, self._output_dir
        )
        out_dir = self._output_dir or status_file.parent
        entries = [("Output", str(out_dir))]
        entries.append(
            ("gtopt log", str(self.log_path) if self.log_path else "(not found yet)")
        )
        entries.append(
            (
                "Status",
                str(status_file) if status_file.is_file() else "(not written yet)",
            )
        )
        try:
            error_lps = sorted((out_dir / "logs").glob("error*.lp*"))
        except OSError:
            error_lps = []
        if error_lps:
            entries.append((f"Error LPs ({len(error_lps)})", str(error_lps[0].parent)))
        # Same status file, plotted (matplotlib) or as text (--no-gui).
        entries.append(("Charts", f"gtopt_monitor --status-file {status_file}"))
        return entries

    def request_stop(self) -> bool:
        """Graceful stop (same as ``s``); True once the request is written."""
        self._cmd_stop()
        return self._stop_sent

    def set_shutdown_state(self, message: str) -> None:
        """Show a Ctrl-C / quit progress message ("stopping…") in the TUI."""
        with self._lock:
            self._shutdown_msg = message
            self._log_lines.append(f">>> {message}")

    # -- frame -------------------------------------------------------------

    def _layout(self) -> _Layout:
        """Panel budget for the current terminal size."""
        try:
            size = self.console.size
            width, height = size.width, size.height
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            width, height = 120, 50
        return layout_for(width, height)

    def _overlay(self) -> str | None:
        if self._show_help:
            return OVERLAY_HELP
        if self._show_stats:
            return OVERLAY_STATS
        if self._show_paths:
            return OVERLAY_PATHS
        return None

    def snapshot(self) -> RunState:
        """Capture the state of one frame.

        Also advances the spinner and refreshes the "Solving" step detail
        from the status JSON (more current than the log).  The SDDP grid
        cells are copied only when the grid view is shown.
        """
        data = self._status
        elapsed = self._clock() - self._start_time
        mode = self._display_mode
        overlay = self._overlay()
        with self._lock:
            log_lines = tuple(self._log_lines)
            opt_st = self._phase_tracker.states["optimize"]
            if opt_st.status == "active" and data:
                if data.get("max_iterations"):
                    opt_st.detail = (
                        f"iter {data.get('iteration', 0)}/{data['max_iterations']}"
                    )
                elif data.get("total_scenes") is not None:
                    opt_st.detail = (
                        f"scene {data.get('scenes_done', 0)}/{data['total_scenes']}"
                    )
            self._phase_tracker.tick()
            plan = self._phase_tracker.snapshot()
            tracker = self._grid_tracker
            grid = None
            visible: int | None = None
            active_scene: int | None = None
            if mode == _MODE_GRID and tracker.has_data:
                visible, active_scene = self._grid_visible_window()
                grid = tracker.snapshot(
                    min_iter=max(0, tracker.max_iter - _GRID_MAX_ITERS + 1)
                )
            system_stats = dict(self._system_stats)
            shutdown_msg = self._shutdown_msg
        return RunState(
            case_name=self.case_name,
            elapsed=elapsed,
            status=data,
            system_stats=system_stats,
            log_lines=log_lines,
            log_path=self.log_path,
            plan=plan,
            grid=grid,
            grid_active_cell=tracker.active_cell,
            grid_max_phase=tracker.max_phase,
            infeasible_count=tracker.infeasible_count,
            grid_scroll_offset=self._grid_scroll_offset,
            grid_visible_scenes=visible,
            grid_active_scene=active_scene,
            mode=mode,
            overlay=overlay,
            path_entries=(
                tuple(self.path_entries()) if overlay == OVERLAY_PATHS else ()
            ),
            stop_sent=self._stop_sent,
            quit_pending=self._quit_pending,
            shutdown_msg=shutdown_msg,
            keys_enabled=self._keys_enabled,
        )

    def _build_display(self) -> Group:
        return render_frame(self.snapshot(), self._layout())

    def _fall_back_to_plain(self, exc: BaseException) -> None:
        """The dashboard died: say so once and keep the run visible."""
        self.plain_fallback = True
        log.warning(
            "live dashboard stopped (%s: %s); continuing with plain output",
            type(exc).__name__,
            exc,
        )
        log.debug("TUI render error", exc_info=exc)
        if self._stop.is_set():
            return  # the run is over; print_final follows
        with self._lock:
            recent = list(self._log_lines)
        for line in recent:
            print(line, flush=True)

    def _run_loop(self) -> None:
        from rich.live import Live  # noqa: PLC0415

        console = self.console
        old_settings = _enter_cbreak()
        cbreak_active = old_settings is not None
        self._keys_enabled = cbreak_active
        try:
            with Live(
                self._render(self._build_display()),
                console=console,
                refresh_per_second=_REFRESH_PER_SECOND,
                # Keep the last frame on screen (no erase at exit); the
                # final summary and banner are printed below it.
                transient=False,
            ) as live:
                # Decouple keyboard polling from status-file polling.
                # Status reads are expensive (full JSON parse + JSON
                # file may be tens of KB) so we keep their cadence at
                # ``poll_interval`` (default 1 s).  But waking only
                # once per poll_interval makes 'q', 's' and the mode
                # keys feel laggy — at 1 s the user releases the key
                # before we even see it.  Run an inner loop at ~50 ms
                # for keys, and only refresh the status when the
                # accumulated time crosses ``poll_interval``.
                last_status_refresh = 0.0
                last_render = 0.0
                # Render at most ``_REFRESH_PER_SECOND`` Hz to avoid
                # flicker.  A key press latches ``dirty`` so the redraw
                # happens on the first tick the throttle allows (≤ 0.5 s)
                # instead of waiting for the next status poll.
                render_interval = 1.0 / max(1, _REFRESH_PER_SECOND)
                dirty = False
                while not self._stop.wait(_KEY_INTERVAL):
                    now = self._clock()
                    # Poll keyboard for interactive commands
                    key = _poll_key(cbreak_active)
                    if key:
                        self._handle_key(key)
                        dirty = True
                    # Refresh status no faster than poll_interval
                    if now - last_status_refresh >= self.poll_interval:
                        self.poll_status()
                        last_status_refresh = now
                        dirty = True
                    if dirty and (now - last_render) >= render_interval:
                        live.update(self._render(self._build_display()))
                        last_render = now
                        dirty = False

                # One final render
                self.update_status(_load_status(self._status_file))
                with self._lock:
                    self._phase_tracker.finish_all()
                live.update(self._render(self._build_display()))
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # Never let the TUI thread crash the solver; never die silently.
            self._fall_back_to_plain(exc)
        finally:
            _exit_cbreak(old_settings)
