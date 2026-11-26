# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The dashboard's data model.

:class:`RunState` is an immutable snapshot of everything one frame
shows: the parsed ``solver_status.json``, what the log revealed (plan
steps, SDDP grid, recent lines) and the interactive view state (mode,
overlay, pending confirmation).  The controller
(:class:`run_gtopt._tui.SolverDisplay`) builds one per frame under its
lock; :func:`run_gtopt.tui.frame.render_frame` turns it into a Rich
renderable without touching the clock, the filesystem or a tracker.
Recorded states therefore render to the same frame every time, which is
what the snapshot / replay tests rely on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parse import PlanSnapshot, SDDPGridTracker

# Display modes -- cycle with Tab, jump with 1/2/3.
MODE_DASHBOARD = 0  # full dashboard (stats, progress, history, log)
MODE_GRID = 1  # SDDP phase activity grid
MODE_CONVERGENCE = 2  # per-scene convergence heatmap + sparklines
MODE_COUNT = 3
MODE_NAMES = {
    MODE_DASHBOARD: "dashboard",
    MODE_GRID: "grid",
    MODE_CONVERGENCE: "convergence",
}

#: Overlays (``h`` / ``i`` / ``o``); at most one is shown.
OVERLAY_HELP = "help"
OVERLAY_STATS = "stats"
OVERLAY_PATHS = "paths"

MAX_LOG_LINES = 16  # rolling log buffer size
MAX_HISTORY_ROWS = 8  # iteration rows shown in the table

# gtopt's PlanningOptionsLP::default_sddp_convergence_tol.
DEFAULT_CONVERGENCE_TOL = 0.01


@dataclass(frozen=True)
class Layout:
    """Panel budget for one terminal size (see :func:`layout_for`)."""

    compact: bool
    log_lines: int
    history_rows: int


# Below either size the dashboard switches to the compact layout: a
# one-line plan, no statistics / resource panels, 3 history rows and
# 4 log lines, so the frame (and the command bar) fits a 40-row pane.
COMPACT_HEIGHT = 40
COMPACT_WIDTH = 100
TALL_HEIGHT = 60


def layout_for(width: int, height: int) -> Layout:
    """Pick the panel budget for a *width* x *height* terminal."""
    if height < COMPACT_HEIGHT or width < COMPACT_WIDTH:
        return Layout(compact=True, log_lines=4, history_rows=3)
    if height >= TALL_HEIGHT:
        return Layout(compact=False, log_lines=MAX_LOG_LINES, history_rows=10)
    return Layout(compact=False, log_lines=10, history_rows=MAX_HISTORY_ROWS)


@dataclass(frozen=True)
class RunState:  # pylint: disable=too-many-instance-attributes
    """One frame's worth of run data and view state (immutable).

    *status* is the parsed ``solver_status.json`` (``{}`` before gtopt
    writes it); the typed properties below are the fields the frame
    layout branches on.  The panel builders still take the raw mapping
    (the schema is gtopt's, see ``source/solver_status.cpp``).
    """

    case_name: str
    elapsed: float = 0.0
    status: Mapping[str, Any] = field(default_factory=dict)
    system_stats: Mapping[str, Any] = field(default_factory=dict)
    log_lines: tuple[str, ...] = ()
    log_path: Path | None = None
    plan: PlanSnapshot = field(default_factory=PlanSnapshot)
    # SDDP grid: scalars always; the cell copy only in grid mode.
    grid: SDDPGridTracker | None = None
    grid_active_cell: tuple[int, int, int] | None = None
    grid_max_phase: int = 0
    infeasible_count: int = 0
    grid_scroll_offset: int = 0
    grid_visible_scenes: int | None = None
    grid_active_scene: int | None = None
    # Interactive view state.
    mode: int = MODE_DASHBOARD
    overlay: str | None = None
    path_entries: tuple[tuple[str, str], ...] = ()
    stop_sent: bool = False
    quit_pending: bool = False
    shutdown_msg: str | None = None
    keys_enabled: bool = True

    # -- status fields -----------------------------------------------------

    @property
    def has_sddp(self) -> bool:
        """SDDP-style status (an iteration budget is reported)."""
        return bool(self.status.get("max_iterations"))

    @property
    def has_monolithic(self) -> bool:
        """Monolithic status (a scene count is reported)."""
        return self.status.get("total_scenes") is not None

    @property
    def history(self) -> list[dict[str, Any]]:
        """Per-iteration records (``[]`` before the first iteration)."""
        return list(self.status.get("history") or [])

    @property
    def convergence_tol(self) -> float | None:
        """SDDP convergence tolerance from the planning JSON, if known."""
        tol = self.system_stats.get("convergence_tol")
        return float(tol) if isinstance(tol, (int, float)) else None

    @property
    def phase(self) -> tuple[int, int] | None:
        """``(phase being solved, highest phase)`` from the SDDP log."""
        if self.grid_active_cell is None:
            return None
        return self.grid_active_cell[2], self.grid_max_phase

    # -- view --------------------------------------------------------------

    @property
    def notice(self) -> tuple[str | None, str]:
        """``(text, style)`` of the line above the key hints."""
        if self.quit_pending:
            return (
                "Terminate gtopt now? Progress of the current iteration is "
                "lost (use s for a graceful stop).  [y/N]",
                "bold red",
            )
        if self.shutdown_msg:
            return self.shutdown_msg, "bold yellow"
        if self.infeasible_count:
            return (
                f"✗ {self.infeasible_count} infeasible solve(s) — error LPs in "
                "the output logs/ dir (press o); gtopt_check_lp diagnoses them",
                "bold red",
            )
        if not self.keys_enabled:
            return (
                "keys disabled (stdin is not a TTY) — Ctrl-C requests a graceful stop",
                "dim",
            )
        return None, "bold yellow"


def load_system_stats(json_path: Path | None) -> dict[str, Any]:
    """Extract system stats from the planning JSON for the ``i`` overlay."""
    if json_path is None or not json_path.is_file():
        return {}
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    system = data.get("system", {})
    simulation = data.get("simulation", {})
    options = data.get("options", {})

    # Count system elements
    element_counts: dict[str, int] = {}
    for key in (
        "bus_array",
        "generator_array",
        "demand_array",
        "line_array",
        "battery_array",
        "flow_array",
        "converter_array",
        "inflow_array",
        "reservoir_array",
        "fuel_array",
    ):
        arr = system.get(key, [])
        if arr:
            label = key.replace("_array", "").replace("_", " ").title()
            element_counts[label] = len(arr)

    # Simulation structure
    scenarios = simulation.get("scenario_array", [])
    stages = simulation.get("stage_array", [])
    blocks = simulation.get("block_array", [])

    # Options summary
    method = options.get("method", "?")
    sddp_opts = options.get("sddp_options") or {}
    tol = sddp_opts.get("convergence_tol")
    convergence_tol = (
        float(tol) if isinstance(tol, (int, float)) else DEFAULT_CONVERGENCE_TOL
    )
    solver = options.get("solver", "")
    model_opts = options.get("model_options") or {}

    return {
        "elements": element_counts,
        "scenarios": len(scenarios),
        "stages": len(stages),
        "blocks": len(blocks),
        "method": method,
        "convergence_tol": convergence_tol,
        "solver": solver,
        "scale_objective": model_opts.get("scale_objective"),
        "use_kirchhoff": model_opts.get("use_kirchhoff"),
        "use_single_bus": model_opts.get("use_single_bus"),
    }
