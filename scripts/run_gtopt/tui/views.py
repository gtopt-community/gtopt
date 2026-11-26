# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Full-height views: the SDDP phase grid (mode 2) and the convergence
heatmap (mode 3).  Pure functions of a grid snapshot / status mapping."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fmt import format_number, sparkline
from .model import MAX_HISTORY_ROWS
from .parse import (
    GRID_AGG_SHAPES,
    GRID_APERTURE,
    GRID_BACKWARD,
    GRID_CELL_STYLES,
    GRID_ELASTIC,
    GRID_FORWARD,
    GRID_IDLE,
    GRID_INFEASIBLE,
    GRID_MAX_ITERS,
    GRID_MAX_PHASES,
    SDDPGridTracker,
)

if TYPE_CHECKING:
    from rich.panel import Panel

# Convergence heatmap color ramp (green → yellow → red, 8 levels).
# Only the 16 ANSI colours (+ bold / dim), so the ramp reads the same on
# a 16-colour terminal or tmux without 256-colour support instead of
# collapsing "dark_green" / "dark_orange" / "orange_red1" to arbitrary
# neighbours; the block height carries the magnitude as well.
HEAT_BLOCKS = "▁▂▃▄▅▆▇█"
HEAT_STYLES = [
    "bold green",
    "green",
    "dim green",
    "bright_yellow",
    "yellow",
    "dim red",
    "red",
    "bold red",
]


def build_sddp_grid(
    tracker: SDDPGridTracker,
    scroll_offset: int = 0,
    max_visible_scenes: int | None = None,
    active_scene_hint: int | None = None,
) -> Panel:
    """Render the SDDP phase/iteration activity grid.

    Layout (top → bottom):
      1. Legend + diagnostic-indicator lights (always visible).
      2. Pinned **aggregate ("all scenes") row** — one (iter, phase)
         matrix where each cell encodes coverage as a density block
         (``·░▒▓█``) and color as the highest-priority state seen
         across all scenes.  This is what makes the panel *useful*
         when there are many scenes; without it you see at most the
         first N that fit in the terminal.
      3. Visible per-scene matrices, starting at ``scroll_offset``,
         clipped to ``max_visible_scenes``.  Above/below ribbons
         indicate how many scenes are off-screen.

    @p scroll_offset is the index into ``tracker.scenes`` of the first
    scene to render.
    @p max_visible_scenes caps the number of per-scene blocks emitted
    (a budget computed from the live console height in
    :meth:`SolverDisplay._build_display` so the panel never overflows
    the terminal — fixing the "can't scroll" hang).
    @p active_scene_hint is the scene id of the current active cell;
    used purely for the visible-range header so users with many
    scenes see *which* scene the SDDP solver is currently inside.
    """
    from rich.panel import Panel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    text = Text()

    max_ph = min(tracker.max_phase, GRID_MAX_PHASES)
    scenes = tracker.scenes
    n_scenes = len(scenes)

    # Legend
    text.append("  ")
    for state, (char, style) in GRID_CELL_STYLES.items():
        if state == GRID_IDLE:
            continue
        labels = {
            GRID_FORWARD: "fwd",
            GRID_BACKWARD: "bwd",
            GRID_ELASTIC: "elastic",
            GRID_APERTURE: "aperture",
            GRID_INFEASIBLE: "infeas",
        }
        text.append(char, style=style)
        text.append(f" {labels.get(state, '?')}  ", style="dim")

    # Indicator lights for diagnostics.
    # ``kappa`` shows the worst observed conditioning value alongside the
    # warning count — the count alone tells the user nothing about how
    # ill-conditioned the LP got.  Aperture is intentionally omitted
    # here: it is already visible via the ▣ cells in the grid and per-
    # aperture counts are noise during a normal run.
    indicators: list[tuple[str, str, str]] = []
    if tracker.kappa_warnings > 0:
        if tracker.max_kappa > 0:
            kappa_val = f"{tracker.kappa_warnings} (max {tracker.max_kappa:.1e})"
        else:
            kappa_val = str(tracker.kappa_warnings)
        indicators.append(("kappa", kappa_val, "bold red"))
    if tracker.elastic_count > 0:
        indicators.append(("elastic", str(tracker.elastic_count), "bold yellow"))
    if tracker.infeasible_count > 0:
        indicators.append(("infeas", str(tracker.infeasible_count), "bold red"))
    if indicators:
        text.append("   ")
        for label, count, ind_style in indicators:
            text.append(f" {label}:", style="dim")
            text.append(count, style=ind_style)
    text.append("\n")

    # Phase header row reused for the aggregate block AND each per-scene
    # block.  Inlined as a closure to keep the layout tweaks local.
    def _emit_phase_header() -> None:
        text.append("     ")  # indent matches the "  ##  " iter label width
        for ph in range(max_ph + 1):
            if ph % 5 == 0:
                label = str(ph)
                text.append(label, style="dim")
                text.append(" " * max(0, 1 - len(label)))
            else:
                text.append(" ", style="dim")
        text.append("\n")

    # ------------------------------------------------------------------
    # Pinned aggregate row — "all scenes" coverage + dominant state.
    # ------------------------------------------------------------------
    if n_scenes > 0:
        text.append(
            f"  all  ({n_scenes} scenes)",
            style="bold cyan",
        )
        text.append("\n")
        _emit_phase_header()
        first_iter = max(0, tracker.max_iter - GRID_MAX_ITERS + 1)
        for it in range(first_iter, tracker.max_iter + 1):
            text.append(f"  {it:>2} ", style="dim")
            for ph in range(max_ph + 1):
                state, coverage = tracker.aggregate_cell(it, ph)
                if state == GRID_IDLE:
                    text.append(" ", style="dim")
                    continue
                # Density char from coverage; color from dominant state.
                # Coverage > 0 always renders at least a "░" so the user
                # sees that *some* scene visited even sparse cells.
                shape_idx = min(
                    len(GRID_AGG_SHAPES) - 1,
                    max(1, int(coverage * len(GRID_AGG_SHAPES))),
                )
                _, base_style = GRID_CELL_STYLES[state]
                text.append(GRID_AGG_SHAPES[shape_idx], style=base_style)
            text.append("\n")
        # Visual divider before the per-scene blocks.
        text.append("\n")

    # ------------------------------------------------------------------
    # Per-scene blocks — windowed by scroll_offset + max_visible_scenes.
    # ------------------------------------------------------------------
    if n_scenes == 0:
        return Panel(
            text,
            title="[bold]SDDP Phase Grid[/bold]",
            border_style="dim cyan",
            padding=(0, 0),
        )

    if max_visible_scenes is None or max_visible_scenes >= n_scenes:
        visible_count = n_scenes
        offset = 0
    else:
        visible_count = max(1, max_visible_scenes)
        offset = max(0, min(scroll_offset, n_scenes - visible_count))

    visible_scenes = scenes[offset : offset + visible_count]
    above_hidden = offset
    below_hidden = n_scenes - (offset + visible_count)

    if above_hidden > 0 or below_hidden > 0:
        # Range header so operators know which scenes are on-screen.
        active_tag = ""
        if active_scene_hint is not None:
            active_tag = f"   active: s{active_scene_hint}"
        text.append(
            f"  scenes {visible_scenes[0]}–{visible_scenes[-1]} of {n_scenes}"
            f"   (j/k scroll, J/K page, g/G top/bottom){active_tag}",
            style="dim",
        )
        text.append("\n")
    if above_hidden > 0:
        text.append(f"  ▲ {above_hidden} scene(s) above\n", style="dim")

    for sc in visible_scenes:
        text.append(f"  s{sc}", style="bold")
        text.append("\n")
        _emit_phase_header()
        first_iter = max(0, tracker.max_iter - GRID_MAX_ITERS + 1)
        for it in range(first_iter, tracker.max_iter + 1):
            text.append(f"  {it:>2} ", style="dim")
            for ph in range(max_ph + 1):
                state = tracker.get_cell(sc, it, ph)
                char, style = GRID_CELL_STYLES[state]
                if (
                    tracker.active_cell is not None
                    and tracker.active_cell == (sc, it, ph)
                    and state != GRID_IDLE
                ):
                    text.append(char, style=f"{style} reverse")
                else:
                    text.append(char, style=style)
            text.append("\n")

    if below_hidden > 0:
        text.append(f"  ▼ {below_hidden} scene(s) below\n", style="dim")

    return Panel(
        text,
        title="[bold]SDDP Phase Grid[/bold]",
        border_style="dim cyan",
        padding=(0, 0),
    )


# ---------------------------------------------------------------------------
# Convergence heatmap / sparkline panel
# ---------------------------------------------------------------------------


def heat_style(value: float, lo: float, hi: float) -> tuple[str, str]:
    """Map *value* in [lo, hi] to a (block_char, Rich style) pair."""
    rng = hi - lo if hi > lo else 1.0
    idx = min(7, int((value - lo) / rng * 8))
    return HEAT_BLOCKS[idx], HEAT_STYLES[idx]


def build_convergence(data: dict) -> Panel:
    """Render the per-scene convergence heatmap and bound sparklines.

    Layout:
      1. Aggregate LB / UB sparkline convergence curves
      2. Per-scene cost heatmap (rows=iterations, cols=scenes)
      3. Per-scene UB sparklines
    """
    from rich.panel import Panel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    text = Text()
    history = data.get("history", [])

    if not history:
        text.append("  Waiting for iteration data...", style="dim")
        return Panel(
            text,
            title="[bold]Convergence[/bold]",
            border_style="dim cyan",
            padding=(0, 1),
        )

    # -- 1. Aggregate bound sparklines --
    lbs = [r.get("lower_bound", 0.0) for r in history]
    ubs = [r.get("upper_bound", 0.0) for r in history]
    gaps = [r.get("gap", 0.0) for r in history]

    text.append("  LB ", style="bold cyan")
    text.append(sparkline(lbs, width=40), style="cyan")
    text.append(f"  {format_number(lbs[-1])}", style="cyan")
    text.append("\n")
    text.append("  UB ", style="bold magenta")
    text.append(sparkline(ubs, width=40), style="magenta")
    text.append(f"  {format_number(ubs[-1])}", style="magenta")
    text.append("\n")
    text.append("  Gap", style="bold")
    text.append(" ")
    text.append(sparkline(gaps, width=40), style="yellow")
    gap_val = gaps[-1]
    gap_style = (
        "bold green"
        if gap_val < 0.01
        else "bold yellow"
        if gap_val < 0.1
        else "bold red"
    )
    text.append(f"  {100.0 * gap_val:.2f}%", style=gap_style)
    text.append("\n\n")

    # -- 2. Per-scene cost heatmap --
    # Collect scene_upper_bounds from history
    n_scenes = 0
    scene_ub_matrix: list[list[float]] = []  # [iter_idx][scene_idx]
    for rec in history:
        sub = rec.get("scene_upper_bounds", [])
        if sub:
            n_scenes = max(n_scenes, len(sub))
            scene_ub_matrix.append(sub)

    if scene_ub_matrix and n_scenes > 0:
        # Find global min/max for color mapping
        all_vals = [v for row in scene_ub_matrix for v in row]
        lo, hi = min(all_vals), max(all_vals)

        text.append("  Scene Costs", style="bold")
        text.append("  (rows=iterations, cols=scenes)\n", style="dim")

        # Scene header
        text.append("       ")
        for sc in range(n_scenes):
            if n_scenes <= 20 or sc % 5 == 0:
                label = f"s{sc}"
                text.append(f"{label:<3}", style="dim")
            else:
                text.append("   ", style="dim")
        text.append("\n")

        # Show last MAX_HISTORY_ROWS iterations
        display_rows = scene_ub_matrix[-MAX_HISTORY_ROWS:]
        first_iter = len(scene_ub_matrix) - len(display_rows)
        for row_idx, row in enumerate(display_rows):
            it = first_iter + row_idx
            text.append(f"  {it:>3} ", style="dim")
            for val in row:
                char, style = heat_style(val, lo, hi)
                text.append(f" {char} ", style=style)
            text.append("\n")

        # Color bar legend
        text.append("\n       ")
        text.append("low", style="bold green")
        text.append(" ")
        for i in range(8):
            text.append(HEAT_BLOCKS[i], style=HEAT_STYLES[i])
        text.append(" ")
        text.append("high", style="bold red")
        text.append(
            f"   range: [{format_number(lo)} .. {format_number(hi)}]", style="dim"
        )
        text.append("\n")

        # -- 3. Per-scene UB sparklines --
        if n_scenes <= 20 and len(scene_ub_matrix) >= 2:
            text.append("\n")
            text.append("  Per-Scene Trends\n", style="bold")
            for sc in range(n_scenes):
                scene_vals = [row[sc] for row in scene_ub_matrix if sc < len(row)]
                text.append(f"  s{sc:<3}", style="dim")
                text.append(sparkline(scene_vals, width=30))
                text.append(f"  {format_number(scene_vals[-1])}", style="dim")
                text.append("\n")
    else:
        text.append("  Per-scene data not yet available\n", style="dim")
        text.append(
            "  (scene_upper_bounds will appear after first SDDP iteration)\n",
            style="dim",
        )

    return Panel(
        text,
        title="[bold]Convergence[/bold]",
        border_style="dim cyan",
        padding=(0, 1),
    )
