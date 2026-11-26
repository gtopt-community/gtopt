# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Compose one dashboard frame from a :class:`~run_gtopt.tui.model.RunState`.

:func:`render_frame` is pure: same state + layout -> same renderable.
The live dashboard calls it twice a second; the snapshot and replay
tests call it with recorded states.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .model import (
    MODE_DASHBOARD,
    MODE_GRID,
    OVERLAY_HELP,
    OVERLAY_PATHS,
    OVERLAY_STATS,
    Layout,
    RunState,
)
from .overlays import build_help_overlay, build_paths_overlay, build_stats_overlay
from .panels import (
    build_async_panel,
    build_command_bar,
    build_header,
    build_history,
    build_log,
    build_plan_panel,
    build_progress,
    build_stats,
    build_system,
    has_resource_telemetry,
)
from .views import build_convergence, build_sddp_grid

if TYPE_CHECKING:
    from rich.console import Group
    from rich.panel import Panel


def progress_panel(state: RunState) -> Panel:
    """The progress bar panel (iteration / gap / phase or scene count)."""
    return build_progress(
        dict(state.status),
        convergence_tol=state.convergence_tol,
        phase=state.phase,
    )


def _log_panel(state: RunState, layout: Layout) -> Panel:
    return build_log(state.log_lines, layout.log_lines, log_path=state.log_path)


def _mode_panels(state: RunState, layout: Layout) -> list[Any]:
    """The panels of the active display mode (below header + plan)."""
    data = dict(state.status)
    compact = layout.compact
    panels: list[Any] = []
    if state.mode == MODE_DASHBOARD:
        # Full dashboard: stats, progress, history, system, log.  The
        # compact layout drops the panels the header already summarises
        # (LB/UB/gap) and the resource panel.
        if state.has_sddp:
            panels.append(progress_panel(state))
            if not compact:
                panels.append(build_stats(data))
            async_panel = build_async_panel(data)
            if async_panel is not None:
                panels.append(async_panel)
            if data.get("history"):
                panels.append(build_history(data, layout.history_rows))
            if not compact and has_resource_telemetry(data):
                panels.append(build_system(data))
        elif state.has_monolithic:
            panels.append(progress_panel(state))
            if not compact and has_resource_telemetry(data):
                panels.append(build_system(data))
        panels.append(_log_panel(state, layout))
    elif state.mode == MODE_GRID:
        if state.has_sddp:
            panels.append(progress_panel(state))
        if state.grid is not None and state.grid.has_data:
            panels.append(
                build_sddp_grid(
                    state.grid,
                    scroll_offset=state.grid_scroll_offset,
                    max_visible_scenes=state.grid_visible_scenes,
                    active_scene_hint=state.grid_active_scene,
                )
            )
        panels.append(_log_panel(state, layout))
    else:  # MODE_CONVERGENCE
        if state.has_sddp:
            panels.append(progress_panel(state))
        panels.append(build_convergence(data))
    return panels


def _overlay_panel(state: RunState) -> Panel | None:
    if state.overlay == OVERLAY_HELP:
        return build_help_overlay()
    if state.overlay == OVERLAY_STATS:
        return build_stats_overlay(dict(state.system_stats))
    if state.overlay == OVERLAY_PATHS:
        return build_paths_overlay(state.path_entries)
    return None


def render_frame(state: RunState, layout: Layout) -> Group:
    """The whole dashboard for *state* on a terminal of *layout*."""
    from rich.console import Group  # noqa: PLC0415

    panels: list[Any] = [
        build_header(
            state.case_name,
            dict(state.status),
            state.elapsed,
            dict(state.system_stats),
            compact=layout.compact,
        ),
        build_plan_panel(state.plan, compact=layout.compact),
    ]
    panels.extend(_mode_panels(state, layout))
    overlay = _overlay_panel(state)
    if overlay is not None:
        panels.append(overlay)
    notice, notice_style = state.notice
    panels.append(build_command_bar(state.stop_sent, state.mode, notice, notice_style))
    return Group(*panels)
