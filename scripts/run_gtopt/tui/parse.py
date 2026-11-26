# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Log-line parsers of the dashboard: the state gtopt's log reveals.

* :class:`SDDPGridTracker` -- per (scene, iteration, phase) SDDP
  activity from the ``SDDP <Tag> [i<iter> s<scene> p<phase>]`` lines,
  merged with the ``phase_grid`` section of ``solver_status.json``.
* :class:`SolverPhaseTracker` -- the run's steps (parse, build, solve,
  write) for the plan checklist.
* :func:`log_line_level` -- the spdlog level of a line.

Trackers are fed under the display lock; renderers only ever see
immutable snapshots (:meth:`SDDPGridTracker.snapshot`,
:meth:`SolverPhaseTracker.snapshot`).  Time comes from an injectable
*clock* so replays are deterministic.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

#: A monotonic clock in seconds (``time.monotonic`` or a test fake).
Clock = Callable[[], float]

# ---------------------------------------------------------------------------
# SDDP phase grid — per-phase/iteration activity tracker
# ---------------------------------------------------------------------------

# Regex for the uniform "SDDP <Tag> [i<iter> s<scene> p<phase>]" format
_SDDP_LOG_RE = re.compile(
    # Per-(scene, phase) activity lines from sddp_method_iteration / forward
    # / backward.  ``p(\d+)`` is followed by an optional ``/<total>`` suffix
    # since the per-phase Forward INFO log (sddp_forward_pass.cpp) emits
    # ``p3/51`` — capturing the ``<total>`` lets the grid pre-allocate
    # all phase columns from the very first log line, instead of waiting
    # until the solver actually reaches the highest phase to discover the
    # true width.  The aperture group ``a(\d+)`` remains optional and
    # exclusive (apertures don't co-emit a phase total).
    r"SDDP (\w+) \[i(\d+) s(\d+) p(\d+)(?:/(\d+))?(?:\s+a(\d+))?\]"
)
# Optional kappa magnitude trailing the "SDDP Kappa [...]" prefix; used
# to surface the worst observed conditioning, not just the warning count.
_SDDP_KAPPA_VALUE_RE = re.compile(r"high kappa\s+([0-9eE+\-.]+)")

# Cell states (priority order: higher overwrites lower)
GRID_IDLE = 0
GRID_FORWARD = 1
GRID_BACKWARD = 2
GRID_ELASTIC = 3  # elastic solve during forward pass
GRID_APERTURE = 4  # aperture pass (backward)
GRID_INFEASIBLE = 5  # infeasible / error

# Cell rendering: (character, Rich style)
GRID_CELL_STYLES: dict[int, tuple[str, str]] = {
    GRID_IDLE: ("·", "dim"),
    GRID_FORWARD: ("▶", "bold cyan"),
    GRID_BACKWARD: ("◀", "bold magenta"),
    GRID_ELASTIC: ("◆", "bold yellow"),
    GRID_APERTURE: ("▣", "bold blue"),
    GRID_INFEASIBLE: ("✗", "bold red"),
}

# Maximum number of phases displayed in the grid
GRID_MAX_PHASES = 52
# Maximum number of iterations shown (most recent)
GRID_MAX_ITERS = 12

# Aggregate ("all scenes") cell shape — density of scene coverage at this
# (iter, phase) cell.  Index = floor(coverage * 5), clamped to [0, 4].
# Coverage 0 → "·"; coverage > 0 always renders something visible.
GRID_AGG_SHAPES: tuple[str, ...] = ("·", "░", "▒", "▓", "█")


class SDDPGridTracker:
    """Tracks SDDP forward/backward/elastic/aperture activity per (scene, iter, phase).

    Parses log lines with the ``sddp_log()`` format and maintains a 3D grid:
    ``grid[scene][(iter, phase)] -> cell_state``.

    The grid is rendered as one small matrix per scene, with phases on X
    and iterations on Y, using colored block characters.
    """

    def __init__(self) -> None:
        # grid[scene][(iter, phase)] = cell_state
        self._grid: dict[int, dict[tuple[int, int], int]] = {}
        self._max_phase: int = 0
        self._max_iter: int = 0
        self._scenes: set[int] = set()
        self._active_cell: tuple[int, int, int] | None = None  # (scene, iter, phase)
        # Diagnostic counters for indicator lights
        self._kappa_warnings: int = 0
        self._max_kappa: float = 0.0
        self._elastic_count: int = 0
        self._infeasible_count: int = 0
        self._aperture_count: int = 0

    def process_line(self, line: str) -> None:
        """Parse an SDDP log line and update the grid."""
        m = _SDDP_LOG_RE.search(line)
        if m is None:
            return

        tag = m.group(1)
        it = int(m.group(2))
        sc = int(m.group(3))
        ph = int(m.group(4))
        # Group 5 = total phase count (e.g. "/51"); group 6 = aperture uid.
        total_phases_str = m.group(5)
        if total_phases_str is not None:
            # PhaseUid is 1-based; total=51 → highest valid ph index is 51.
            self._max_phase = max(self._max_phase, int(total_phases_str))
        has_aperture = m.group(6) is not None

        # Determine cell state from tag + context
        if tag == "Forward":
            if "elastic" in line.lower():
                state = GRID_ELASTIC
                self._elastic_count += 1
            else:
                state = GRID_FORWARD
        elif tag == "Backward":
            if has_aperture:
                state = GRID_APERTURE
                self._aperture_count += 1
            else:
                state = GRID_BACKWARD
        elif tag == "Aperture":
            state = GRID_APERTURE
            self._aperture_count += 1
        elif tag == "Elastic":
            state = GRID_ELASTIC
            self._elastic_count += 1
        elif tag == "Kappa":
            self._kappa_warnings += 1
            # Capture the actual kappa magnitude so the indicator strip
            # can show worst-conditioning instead of just a count of
            # warnings — the count alone tells you nothing about how
            # bad the LP is.
            kv = _SDDP_KAPPA_VALUE_RE.search(line)
            if kv is not None:
                try:
                    self._max_kappa = max(self._max_kappa, float(kv.group(1)))
                except ValueError:
                    pass
            return
        else:
            # Iter, Sim, Update, Init — no phase-level grid info
            return

        # Check for infeasible markers
        if "infeasib" in line.lower():
            self._infeasible_count += 1
            state = max(state, GRID_INFEASIBLE)

        # Update grid dimensions and state
        self._scenes.add(sc)
        self._max_phase = max(self._max_phase, ph)
        self._max_iter = max(self._max_iter, it)

        if sc not in self._grid:
            self._grid[sc] = {}

        # Only overwrite if new state has higher priority
        cell_key = (it, ph)
        current = self._grid[sc].get(cell_key, GRID_IDLE)
        if state > current:
            self._grid[sc][cell_key] = state

        self._active_cell = (sc, it, ph)

    @property
    def has_data(self) -> bool:
        return bool(self._grid)

    @property
    def scenes(self) -> list[int]:
        return sorted(self._scenes)

    @property
    def max_phase(self) -> int:
        return self._max_phase

    @property
    def max_iter(self) -> int:
        return self._max_iter

    def get_cell(self, scene: int, iteration: int, phase: int) -> int:
        sg = self._grid.get(scene)
        if sg is None:
            return GRID_IDLE
        return sg.get((iteration, phase), GRID_IDLE)

    def aggregate_cell(self, iteration: int, phase: int) -> tuple[int, float]:
        """Return ``(dominant_state, coverage_fraction)`` across all scenes.

        ``dominant_state`` is the highest-priority state seen at this
        ``(iteration, phase)`` in any scene — using the same priority
        order as :meth:`get_cell` (forward < backward < elastic <
        aperture < infeasible).  ``coverage_fraction`` is the share of
        registered scenes that have visited this cell at all (state >
        idle), in [0, 1].

        Used by :func:`_build_sddp_grid` to render the pinned
        "all scenes" summary row at the top of Grid mode.
        """
        if not self._scenes:
            return GRID_IDLE, 0.0
        worst = GRID_IDLE
        visited = 0
        for sc in self._scenes:
            # Every registered scene owns a grid row (process_line and
            # load_from_status add both together).
            cell = self._grid[sc].get((iteration, phase), GRID_IDLE)
            if cell != GRID_IDLE:
                visited += 1
                worst = max(worst, cell)
        return worst, visited / len(self._scenes)

    @property
    def active_cell(self) -> tuple[int, int, int] | None:
        return self._active_cell

    @property
    def kappa_warnings(self) -> int:
        return self._kappa_warnings

    @property
    def max_kappa(self) -> float:
        return self._max_kappa

    @property
    def elastic_count(self) -> int:
        return self._elastic_count

    @property
    def infeasible_count(self) -> int:
        return self._infeasible_count

    @property
    def aperture_count(self) -> int:
        return self._aperture_count

    def snapshot(self, min_iter: int = 0) -> SDDPGridTracker:
        """An independent copy for rendering, keeping only the cells of
        iterations >= *min_iter* (the grid view shows the last
        :data:`GRID_MAX_ITERS`); counters and dimensions are kept whole.
        Taken under the display lock, so the renderer never iterates a
        dict the log thread is growing."""
        dup = copy.copy(self)
        dup._grid = {
            sc: {k: v for k, v in cells.items() if k[0] >= min_iter}
            for sc, cells in self._grid.items()
        }
        dup._scenes = set(self._scenes)
        return dup

    # Map C++ GridCell chars to Python grid states
    _CELL_CHAR_MAP: dict[str, int] = {
        "F": GRID_FORWARD,
        "B": GRID_BACKWARD,
        "E": GRID_ELASTIC,
        "A": GRID_APERTURE,
        "X": GRID_INFEASIBLE,
    }

    def load_from_status(self, status: dict) -> None:
        """Merge phase_grid data from the status JSON into the grid.

        The C++ solver writes a ``phase_grid`` section keyed by PhaseUid:
        ``{"i": 0, "s": 0, "cells": {"1": "F", "2": ".", "3": "B"}}``.
        Iterating in sorted-UID order gives the natural column ordering
        without any UID-as-index arithmetic on either side.  This
        format also tolerates legacy positional strings (``"FF.FEB"``)
        for backward compatibility with status files captured before
        the format change — the legacy branch treats each character
        position as a 0-based phase column.
        """
        grid_data = status.get("phase_grid")
        if not grid_data:
            return
        for row in grid_data.get("rows", []):
            it = row.get("i", 0)
            sc = row.get("s", 0)
            cells = row.get("cells")
            if not cells:
                continue
            if isinstance(cells, dict):
                # New format: UID-keyed.  Sort by integer UID so the
                # column ordering is stable; the TUI's `ph` axis is
                # 0-based ordinal-within-sorted-UIDs (the natural
                # phase column index for the dense 1-based PhaseUid
                # layout the simulation framework allocates).
                cells_sorted = sorted(cells.items(), key=lambda kv: int(kv[0]))
                cells_iter = enumerate(ch for _uid, ch in cells_sorted)
                width = len(cells_sorted)
            else:
                # Legacy format: positional packed string.
                cells_iter = enumerate(cells)
                width = len(cells)
            # ``cells`` is truthy here, so width >= 1.
            self._scenes.add(sc)
            self._max_iter = max(self._max_iter, it)
            self._max_phase = max(self._max_phase, width - 1)
            if sc not in self._grid:
                self._grid[sc] = {}
            for ph, ch in cells_iter:
                state = self._CELL_CHAR_MAP.get(ch, GRID_IDLE)
                if state == GRID_IDLE:
                    continue
                cell_key = (it, ph)
                current = self._grid[sc].get(cell_key, GRID_IDLE)
                if state > current:
                    self._grid[sc][cell_key] = state


# ---------------------------------------------------------------------------
# Solver phase tracking (plan table)
# ---------------------------------------------------------------------------

# Ordered solver phases: (key, human label)
_SOLVER_PHASES: list[tuple[str, str]] = [
    ("parse", "Parsing input files"),
    ("validate", "Validating planning"),
    ("build_lp", "Building LP model"),
    ("optimize", "Solving"),
    ("sim_pass", "Simulation pass"),
    ("solution", "Solution statistics"),
    ("write", "Writing output files"),
]

# Log line patterns that trigger phase transitions.
# Each entry: (compiled regex, phase key, action).
# Actions: "start" = begin phase, "done" = complete phase, "detail" = update detail.
_PHASE_TRIGGERS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"Parsing input file"), "parse", "start"),
    (re.compile(r"Parse all input files time"), "parse", "done"),
    (re.compile(r"Planning validation passed"), "validate", "done"),
    (re.compile(r"Planning validation failed"), "validate", "done"),
    (re.compile(r"=== Building LP model ==="), "build_lp", "start"),
    (re.compile(r"Build lp time"), "build_lp", "done"),
    (re.compile(r"=== System optimization ==="), "optimize", "start"),
    (
        re.compile(r"SDDP Iter \[i\d+\]: === starting \((\d+) of (\d+)\) ==="),
        "optimize",
        "detail",
    ),
    (
        re.compile(r"Monolithic(?:Method|Solver): scene (\d+) done.*\((\d+)/(\d+)\)"),
        "optimize",
        "detail",
    ),
    (re.compile(r"SDDP Sim \[i\d+\]: === simulation pass"), "sim_pass", "start"),
    (re.compile(r"SDDP Sim \[i\d+\]: done in"), "sim_pass", "done"),
    (re.compile(r"=== Solution statistics ==="), "solution", "done"),
    (re.compile(r"=== Output writing ==="), "write", "start"),
    (re.compile(r"Write output time"), "write", "done"),
]


@dataclass
class PhaseState:
    """Tracks a single solver phase's status and timing."""

    label: str
    status: str = "pending"  # pending | active | done | skipped
    t_start: float = 0.0
    t_end: float = 0.0
    detail: str = ""
    clock: Clock = field(default=time.monotonic, repr=False, compare=False)

    @property
    def elapsed(self) -> float:
        if self.status == "active":
            return self.clock() - self.t_start
        if self.status == "done":
            return self.t_end - self.t_start
        return 0.0


@dataclass(frozen=True, slots=True)
class PlanStep:
    """Immutable view of one :class:`PhaseState` (elapsed frozen)."""

    label: str
    status: str
    elapsed: float
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PlanSnapshot:
    """The plan checklist at one instant: steps in order + spinner frame."""

    steps: tuple[PlanStep, ...] = ()
    frame: int = 0


class SolverPhaseTracker:
    """Tracks solver progress through its execution phases.

    Parses solver log lines to detect phase transitions and maintains
    state for each phase (pending / active / done / skipped).
    """

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, PhaseState] = {}
        self._order: list[str] = []
        self._current: str | None = None
        self._frame: int = 0
        self._is_monolithic: bool = False

        for key, label in _SOLVER_PHASES:
            self._states[key] = PhaseState(label=label, clock=clock)
            self._order.append(key)

    def process_line(self, line: str) -> None:
        """Parse a solver log line and update phase states."""
        if not self._is_monolithic and (
            "MonolithicMethod:" in line or "MonolithicSolver:" in line
        ):
            self._is_monolithic = True
            self._states["sim_pass"].status = "skipped"

        for pattern, key, action in _PHASE_TRIGGERS:
            match = pattern.search(line)
            if match is None:
                continue
            st = self._states[key]

            if action == "start":
                self._finish_current()
                st.status = "active"
                st.t_start = self._clock()
                self._current = key
            elif action == "done":
                if st.status == "pending":
                    # Instant step (validate) or done without explicit start
                    st.status = "done"
                    st.t_start = st.t_end = self._clock()
                elif st.status == "active":
                    st.status = "done"
                    st.t_end = self._clock()
                if self._current == key:
                    self._current = None
            elif action == "detail" and st.status == "active":
                groups = match.groups()
                if len(groups) == 2:  # noqa: PLR2004
                    st.detail = f"iter {groups[0]}/{groups[1]}"
                else:  # 3 groups: monolithic "scene N done (k/total)"
                    st.detail = f"scene {groups[1]}/{groups[2]}"
            break  # first match wins

    def finish_all(self) -> None:
        """Mark any active phase as done (called when solver exits)."""
        self._finish_current()

    def _finish_current(self) -> None:
        if self._current is not None:
            st = self._states[self._current]
            if st.status == "active":
                st.status = "done"
                st.t_end = self._clock()
            self._current = None

    @property
    def order(self) -> list[str]:
        return self._order

    @property
    def states(self) -> dict[str, PhaseState]:
        return self._states

    def tick(self) -> None:
        """Advance the spinner frame counter."""
        self._frame += 1

    @property
    def frame(self) -> int:
        return self._frame

    def snapshot(self) -> PlanSnapshot:
        """The current steps and spinner frame, frozen for rendering."""
        return PlanSnapshot(
            steps=tuple(
                PlanStep(st.label, st.status, st.elapsed, st.detail)
                for st in (self._states[k] for k in self._order)
            ),
            frame=self._frame,
        )


# ---------------------------------------------------------------------------
# spdlog levels
# ---------------------------------------------------------------------------

# spdlog level token: "[warning]", "[error]", ... (first one on the line).
LOG_LEVEL_RE = re.compile(
    r"\[(trace|debug|info|warning|warn|error|err|critical)\]", re.IGNORECASE
)
# Leading "[2026-11-25 02:36:16.304] " timestamp, ignored when deduping.
LOG_TS_RE = re.compile(r"^\[\d{4}-\d\d-\d\d [\d:.]+\]\s*")
# Levels counted for the final summary's "N warning(s), M error(s)".
COUNTED_LEVELS = ("warning", "warn", "error", "err", "critical")
# Timestamp / "gtopt:" echo prefixes, stripped to match a stderr copy of
# a message with its log-file copy.
ECHO_PREFIX_RE = re.compile(r"^(?:\s*\[\d{4}-\d\d-\d\d [\d:.]+\]\s*|\s*gtopt:\s*)+")


def log_line_level(line: str) -> str | None:
    """spdlog level of *line* (``"warning"``, ``"error"``, ...) or None."""
    m = LOG_LEVEL_RE.search(line)
    return m.group(1).lower() if m else None
