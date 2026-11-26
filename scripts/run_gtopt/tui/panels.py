# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Dashboard panels: pure functions of status data -> Rich renderables.

Each builder takes the parsed ``solver_status.json`` mapping (or a
snapshot from :mod:`run_gtopt.tui.parse`) and returns a panel; none
reads the clock, the filesystem or mutable tracker state.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from gtopt_shared.status import PASS_NAMES, STATUS_AGE_KEY

from .fmt import PROGRESS_BAR_WIDTH, format_elapsed, format_mb, format_number
from .fmt import progress_bar, spark_range, sparkline
from .model import MAX_HISTORY_ROWS, MODE_COUNT, MODE_CONVERGENCE, MODE_DASHBOARD
from .model import MODE_GRID
from .parse import PlanSnapshot, PlanStep, SolverPhaseTracker, log_line_level

if TYPE_CHECKING:
    from rich.panel import Panel

# Resource telemetry older than this (status-file age) is flagged stale in
# the CPU/MEM panel.  A current gtopt rewrites the file on the monitor's
# ~0.5 s cadence, and the TUI polls at ~1 s; 6 s comfortably clears that
# while still catching a binary that only rewrites at iteration boundaries.
STATUS_STALE_S = 6.0

PASS_STYLES = {0: "dim", 1: "bold green", 2: "bold magenta"}
# Final ``solver_status.json`` states -> (label, style) for the progress bar.
FINAL_STATES = {
    "converged": ("converged", "bold green"),
    "max_iterations": ("iteration limit", "bold yellow"),
    "stopped": ("stopped", "bold yellow"),
    "done": ("done", "bold green"),
}

# Braille spinner frames (same cadence as plp2gtopt / Claude Code)
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

LEVEL_STYLES = {
    "critical": "bold red",
    "error": "bold red",
    "err": "bold red",
    "warning": "yellow",
    "warn": "yellow",
    "debug": "dim",
    "trace": "dim",
}


def build_header(
    case_name: str,
    data: dict,
    elapsed: float,
    system_stats: dict | None = None,
    compact: bool = False,
) -> Panel:
    """Title panel; *compact* folds the status into the first row and
    drops the subtitle (one row less for small terminals)."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    status_str = data.get("status", "starting")

    # Method: from status JSON or log detection
    stats = system_stats or {}
    method = stats.get("method", "")
    if not method or method == "?":
        method = (
            "sddp"
            if data.get("max_iterations")
            else "monolithic"
            if data.get("total_scenes") is not None
            else "..."
        )

    # Solver: from log detection or CLI flag
    solver = stats.get("solver", "")

    style_map = {
        "running": "bold yellow",
        "converged": "bold green",
        "initializing": "bold cyan",
        "starting": "bold cyan",
        "completed": "bold green",
        "optimal": "bold green",
    }
    status_style = style_map.get(status_str, "bold white")

    # Build info line: "Method: sddp  Solver: cplex"
    info_parts = [f"Method: {method}"]
    if solver:
        info_parts.append(f"Solver: {solver}")
    info_text = "  ".join(info_parts)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="right", ratio=1)
    case_cell = Text(f"Case: {case_name}", style="bold")
    if compact:
        case_cell.append(f"  {status_str}", style=status_style)
    grid.add_row(
        case_cell,
        Text(info_text, style="dim"),
        Text(format_elapsed(elapsed), style="bold cyan"),
    )
    if not compact:
        grid.add_row(Text(status_str, style=status_style), "", "")

    # Always-visible LB / UB / gap line so operators see convergence
    # progress regardless of which display mode is active (Dashboard,
    # Grid, or Convergence).  Skipped before any iteration data exists.
    lb = data.get("lower_bound")
    ub = data.get("upper_bound")
    gap = data.get("gap")
    if lb is not None or ub is not None:
        # ``Text`` already imported at the top of the function — no
        # need to re-import as ``_T``; just alias it for the same
        # locality the prior alias provided.
        _T = Text
        bounds = _T()
        bounds.append("LB ", style="bold cyan")
        bounds.append(format_number(lb or 0.0), style="cyan")
        bounds.append("   UB ", style="bold magenta")
        bounds.append(format_number(ub or 0.0), style="magenta")
        if gap is not None:
            gap_style = (
                "bold green"
                if gap < 0.01
                else "bold yellow"
                if gap < 0.1
                else "bold red"
            )
            bounds.append("   Gap ", style="bold")
            bounds.append(f"{100.0 * gap:.2f}%", style=gap_style)
        # Δ Gap — the change in gap since the previous iteration (in
        # percentage points).  This is the quantity the SDDP/cascade
        # convergence test actually watches (ΔUB-stationarity): once it
        # falls below the per-level tolerance the solver stops.  Small
        # |Δ| → converging (green); larger → still moving (yellow).
        # ALWAYS render the field (with "—" when it can't be computed yet,
        # i.e. fewer than two history rows — the first iteration of a run
        # or of each cascade level) so it does not flicker in and out of
        # the header and shift the layout.
        hist = data.get("history") or []
        bounds.append("   ΔGap ", style="bold")
        if len(hist) >= 2:
            dgap = hist[-1].get("gap", 0.0) - hist[-2].get("gap", 0.0)
            adg = abs(dgap)
            dg_style = (
                "bold green"
                if adg < 0.01
                else "bold yellow"
                if adg < 0.05
                else "bold red"
            )
            bounds.append(f"{100.0 * dgap:+.2f}%", style=dg_style)
        else:
            bounds.append("—", style="dim")
        # Iteration counter, when SDDP-style data is present
        it = data.get("iteration")
        max_it = data.get("max_iterations")
        if it is not None and max_it:
            bounds.append(f"   iter {it}/{max_it}", style="dim")
        grid.add_row(bounds, "", "")

    subtitle = (
        ""
        if compact
        else (" [dim]— Generation & Transmission Expansion Planning[/dim]")
    )
    return Panel(
        grid,
        title=f"[bold cyan]⚡ gtopt[/bold cyan]{subtitle}",
        border_style="cyan",
        padding=(0, 1),
    )


def sddp_eta(data: dict) -> str | None:
    """Rough time left to the iteration limit from recent iteration times.

    Median of the last three ``iteration_s`` values times the remaining
    iterations.  SDDP usually converges earlier, so this is an upper
    bound and is labelled as such.
    """
    history = data.get("history") or []
    times = sorted(r.get("iteration_s", 0.0) for r in history[-3:])
    times = [t for t in times if t > 0]
    remaining = data.get("max_iterations", 0) - data.get("iteration", 0)
    if not times or remaining <= 0:
        return None
    median = times[len(times) // 2]
    return f"≤ {format_elapsed(median * remaining)} to iter limit"


def build_progress(
    data: dict,
    convergence_tol: float | None = None,
    phase: tuple[int, int] | None = None,
) -> Panel:
    """Progress panel.

    SDDP: the bar tracks the gap against the convergence tolerance
    (full when gap <= tol; the iteration budget is shown as text with an
    ETA to the limit), plus the phase being solved within the current
    pass.  Monolithic: the scene count.  *phase* is ``(current, total)``.
    """
    from rich.panel import Panel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    iteration = data.get("iteration", 0)
    max_iter = data.get("max_iterations", 0)
    current_pass = data.get("current_pass", 0)
    scenes_done = data.get("scenes_done")
    total_scenes = data.get("total_scenes")
    pass_name = PASS_NAMES.get(current_pass, "?")
    pass_style = PASS_STYLES.get(current_pass, "dim")
    # A finished run is not "[idle]": show how it ended instead.
    final = FINAL_STATES.get(str(data.get("status", "")))
    if final is None and data.get("converged"):
        final = FINAL_STATES["converged"]
    if final is not None:
        pass_name, pass_style = final
    state = f"  [{pass_style}]\\[{pass_name}][/{pass_style}]"

    if max_iter > 0:
        gap = data.get("gap")
        tol = convergence_tol or 0.0
        parts = [f"  Iteration [bold]{iteration}[/bold]/{max_iter}"]
        if isinstance(gap, (int, float)) and tol > 0:
            frac = 1.0 if gap <= tol else tol / gap
            if final is not None and data.get("converged"):
                frac = 1.0
            parts.append(f"  gap {100.0 * gap:.2f}% → tol {100.0 * tol:.2f}%  ")
            parts.append(progress_bar(frac, PROGRESS_BAR_WIDTH // 2))
        else:
            pct = 100 if final is not None else min(100, iteration * 100 // max_iter)
            parts.append("  ")
            parts.append(progress_bar(pct / 100.0, PROGRESS_BAR_WIDTH // 2))
            parts.append(f"  {pct}%")
        parts.append(state)
        if final is None:
            if phase is not None and phase[1] > 0:
                parts.append(f"  phase {phase[0]}/{phase[1]}")
            elif scenes_done is not None:
                parts.append(f"  scenes: {scenes_done}")
            eta = sddp_eta(data)
            if eta:
                parts.append(f"  [dim]{eta}[/dim]")
        text = Text.from_markup("".join(parts))
    elif total_scenes:
        done = scenes_done or 0
        frac = 1.0 if final is not None else done / total_scenes
        text = Text.from_markup(
            f"  [bold]Solving[/bold]  scenes {done}/{total_scenes}  "
            + progress_bar(frac, PROGRESS_BAR_WIDTH // 2)
            + state
        )
    else:
        text = Text.from_markup(f"  [bold]Solving...[/bold]{state}")

    return Panel(text, border_style="dim cyan", padding=(0, 1))


def build_async_panel(data: dict) -> Panel | None:
    """Render async scene execution state when max_async_spread > 0."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    async_data = data.get("async")
    if not async_data:
        return None

    spread = async_data.get("spread", 0)
    max_spread = async_data.get("max_async_spread", 0)
    converged = async_data.get("converged_scenes", 0)
    pending = async_data.get("pool_tasks_pending", 0)
    active = async_data.get("pool_tasks_active", 0)
    cpu = async_data.get("pool_cpu_load", 0.0)
    iters = async_data.get("scene_iterations", [])
    states = async_data.get("scene_states", [])

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", min_width=16)
    grid.add_column(min_width=12)
    grid.add_column(style="bold", min_width=16)
    grid.add_column(min_width=12)

    spread_style = "bold red" if spread >= max_spread else "cyan"
    grid.add_row(
        "Spread",
        Text(f"{spread}/{max_spread}", style=spread_style),
        "Converged",
        Text(str(converged), style="bold green" if converged else "dim"),
    )
    grid.add_row(
        "Pool pending",
        Text(str(pending), style="cyan"),
        "Pool active",
        Text(str(active), style="cyan"),
    )
    if cpu > 0:
        grid.add_row("CPU load", Text(f"{cpu:.0f}%", style="cyan"), "", "")

    # Compact per-scene iteration display (up to 20 scenes)
    if iters:
        scene_parts: list[str] = []
        for si, it_val in enumerate(iters[:20]):
            st = states[si] if si < len(states) else "?"
            style = "green" if st == "done" else "cyan" if st == "training" else "dim"
            scene_parts.append(f"[{style}]s{si}:{it_val}[/{style}]")
        scene_text = Text.from_markup("  " + "  ".join(scene_parts))
        grid.add_row("", "", "", "")
        grid.add_row(scene_text, "", "", "")

    return Panel(
        grid,
        title="[bold]Async Scenes[/bold]",
        border_style="dim cyan",
        padding=(0, 1),
    )


def build_stats(data: dict) -> Panel:
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    lb = data.get("lower_bound", 0.0)
    ub = data.get("upper_bound", 0.0)
    gap = data.get("gap", 0.0)
    converged = data.get("converged", False)
    history = data.get("history", [])
    total_cuts = sum(r.get("cuts_added", 0) for r in history)
    last_cuts = history[-1].get("cuts_added", 0) if history else 0

    gap_style = (
        "bold green" if gap < 0.01 else "bold yellow" if gap < 0.1 else "bold red"
    )
    conv_text = (
        Text("Yes ✓", style="bold green") if converged else Text("No", style="dim")
    )
    gaps = [r.get("gap", 0.0) for r in history]
    spark = sparkline(gaps) if gaps else ""

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", min_width=14)
    grid.add_column(min_width=18)
    grid.add_column(style="bold", min_width=14)
    grid.add_column(min_width=18)

    grid.add_row(
        "Lower Bound",
        Text(format_number(lb), style="cyan"),
        "Upper Bound",
        Text(format_number(ub), style="cyan"),
    )
    # Render gap as a percentage to match the per-iteration log format
    # (sddp_iteration.cpp emits ``gap=25.00%``).  Six decimals on a
    # fraction like ``0.250043`` is unintuitive for operators glancing
    # at the dashboard.
    grid.add_row(
        "Gap",
        Text(f"{100.0 * gap:.2f}%", style=gap_style),
        "Converged",
        conv_text,
    )
    grid.add_row(
        "Cuts (iter)",
        Text(str(last_cuts)),
        "Cuts (total)",
        Text(str(total_cuts)),
    )
    if spark:
        grid.add_row("Gap trend", Text(spark, style="cyan"), "", "")

    return Panel(
        grid,
        title="[bold]Solver Statistics[/bold]",
        border_style="dim cyan",
        padding=(0, 1),
    )


def build_history(data: dict, max_rows: int = MAX_HISTORY_ROWS) -> Panel:
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    history = data.get("history", [])
    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Iter", justify="right", style="bold", width=5)
    table.add_column("Lower Bound", justify="right", width=16)
    table.add_column("Upper Bound", justify="right", width=16)
    table.add_column("Gap", justify="right", width=10)
    table.add_column("Δ Gap", justify="right", width=9)
    table.add_column("Fwd", justify="right", width=7)
    table.add_column("Bwd", justify="right", width=7)

    # Δ Gap = change in the gap since the previous iteration, in percentage
    # points (negative = gap shrinking, i.e. converging).  Computed over the
    # FULL history so the first visible row keeps its true predecessor even
    # when the table is sliced to the last MAX_HISTORY_ROWS.  Replaces the
    # former "Cuts" column — the per-iter gap movement is the convergence
    # signal operators actually track.
    for idx, rec in enumerate(history[-max_rows:]):
        full_idx = max(0, len(history) - max_rows) + idx
        g = rec.get("gap", 0.0)
        gap_style = "green" if g < 0.01 else "yellow" if g < 0.1 else "red"
        if full_idx > 0:
            dg = g - history[full_idx - 1].get("gap", 0.0)
            # Shrinking gap (dg < 0) is progress → green; growing → red.
            dg_style = "green" if dg < 0 else "red" if dg > 0 else "dim"
            dg_cell = f"[{dg_style}]{100.0 * dg:+.2f}%[/{dg_style}]"
        else:
            dg_cell = "[dim]—[/dim]"
        table.add_row(
            str(rec.get("iteration", 0)),
            format_number(rec.get("lower_bound", 0.0)),
            format_number(rec.get("upper_bound", 0.0)),
            f"[{gap_style}]{100.0 * g:.2f}%[/{gap_style}]",
            dg_cell,
            f"{rec.get('forward_pass_s', 0.0):.1f}s",
            f"{rec.get('backward_pass_s', 0.0):.1f}s",
        )

    if not history:
        table.add_row(
            "[dim]waiting for first iteration...[/dim]", "", "", "", "", "", ""
        )

    return Panel(
        table,
        title="[bold]Iteration History[/bold]",
        border_style="dim cyan",
        padding=(0, 0),
    )


def has_resource_telemetry(data: dict) -> bool:
    """True when *data* carries any CPU/memory observation worth showing."""
    rt = data.get("realtime") or {}
    if rt.get("cpu_loads") or rt.get("active_workers"):
        return True
    return any(
        data.get(k) is not None
        for k in (
            "pool_process_rss_mb",
            "pool_available_memory_mb",
            "pool_memory_percent",
        )
    )


def build_system(data: dict) -> Panel:
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    rt = data.get("realtime", {})
    cpus = rt.get("cpu_loads", [])
    workers = rt.get("active_workers", [])
    mem_pcts = rt.get("memory_percent", [])
    rss_vals = rt.get("process_rss_mb", [])
    free_vals = rt.get("available_memory_mb", [])

    # The realtime ring is the AUTHORITATIVE live source: the background
    # SolverMonitor refreshes it every ~0.5 s, so it always reflects the
    # current poll.  The top-level `pool_*` snapshot is written only at
    # iteration boundaries and can lag (or be 0 when the snapshot pool's
    # monitor is stale) — so prefer the ring and use `pool_*` only as a
    # fallback when the ring is empty.  A non-positive `pool_*` is treated
    # as "no reading" so a stale 0 never shadows a fresh ring value.
    pool_rss = data.get("pool_process_rss_mb")
    pool_free = data.get("pool_available_memory_mb")
    pool_pct = data.get("pool_memory_percent")
    async_data = data.get("async") or {}
    pool_active = async_data.get("pool_tasks_active")
    pool_pending = async_data.get("pool_tasks_pending")

    def _live(ring: list, pool_v) -> float:
        if ring:
            return float(ring[-1])
        return float(pool_v) if pool_v is not None and float(pool_v) > 0.0 else 0.0

    cpu_val = cpus[-1] if cpus else 0.0
    worker_val = workers[-1] if workers else 0
    mem_val = _live(mem_pcts, pool_pct)
    rss_val = _live(rss_vals, pool_rss)
    free_val = _live(free_vals, pool_free)

    bar_width = 30
    cpu_filled = int(cpu_val * bar_width / 100)
    cpu_bar = (
        f"[green]{'█' * cpu_filled}[/green][dim]{'░' * (bar_width - cpu_filled)}[/dim]"
    )
    mem_filled = int(mem_val * bar_width / 100)
    mem_bar = (
        f"[blue]{'█' * mem_filled}[/blue][dim]{'░' * (bar_width - mem_filled)}[/dim]"
    )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(min_width=6)
    grid.add_column(min_width=40)
    grid.add_column(min_width=20)

    # Right-column annotations: CPU row reports active workers; MEM row
    # reports gtopt's RSS plus system free memory (the user-visible
    # "headroom" before the OS starts pressuring the process).
    # "Workers: 0" only says the ring has no sample: show what is known.
    right = [f"Workers: {worker_val}"] if worker_val else []
    if pool_active is not None or pool_pending is not None:
        a = pool_active if pool_active is not None else 0
        p = pool_pending if pool_pending is not None else 0
        right.append(f"Pool: {a} active  {p} pending")
    cpu_right = Text("  ".join(right), style="cyan")

    if free_val > 0.0:
        mem_right = Text(
            f"gtopt: {format_mb(rss_val)}   Free: {format_mb(free_val)}",
            style="blue",
        )
    else:
        mem_right = Text(f"gtopt: {format_mb(rss_val)}", style="blue")

    grid.add_row(
        Text("CPU", style="bold"),
        Text.from_markup(f"{cpu_bar} {cpu_val:.0f}%"),
        cpu_right,
    )
    # No memory reading at all (no ring, no pool snapshot): skip the row
    # rather than draw an empty 0 % bar and "gtopt: 0 MB".
    if mem_val > 0.0 or rss_val > 0.0 or free_val > 0.0:
        grid.add_row(
            Text("MEM", style="bold"),
            Text.from_markup(f"{mem_bar} {mem_val:.0f}%"),
            mem_right,
        )

    # Staleness note.  A current gtopt rewrites the status file every ~0.5 s
    # (monitor periodic-write fix), so CPU/MEM stays live between iterations.
    # If the file is much older than that, the running binary predates the
    # fix and only refreshes at iteration boundaries — tell the user so a
    # frozen panel isn't mistaken for a hung solve.
    age = data.get(STATUS_AGE_KEY)
    if isinstance(age, (int, float)) and age > STATUS_STALE_S:
        grid.add_row(
            Text("note", style="dim yellow"),
            Text(
                f"CPU/MEM sample {age:.0f}s old — live refresh needs a gtopt "
                "rebuilt with the monitor periodic-write fix",
                style="dim yellow",
            ),
            "",
        )

    cpu_spark = sparkline(cpus, lo=0.0, hi=100.0)
    mem_spark = sparkline(mem_pcts, lo=0.0, hi=100.0)
    if cpu_spark:
        grid.add_row(
            Text("Load", style="dim"),
            Text(cpu_spark, style="green"),
            Text(f"min–max{spark_range(cpus)}%", style="dim"),
        )
    if mem_spark:
        grid.add_row(
            Text("Mem", style="dim"),
            Text(mem_spark, style="blue"),
            Text(f"min–max{spark_range(mem_pcts)}%", style="dim"),
        )

    # LP task stats summary (from top-level JSON, not realtime)
    lp_stats = data.get("lp_task_stats")
    if lp_stats:
        dispatched = lp_stats.get("dispatched", 0)
        avg_cpu = lp_stats.get("avg_cpu_pct", 0.0)
        avg_mem = lp_stats.get("avg_rss_delta_mb", 0.0)
        grid.add_row(
            Text("LP", style="dim"),
            Text(
                f"{dispatched} tasks  avg CPU {avg_cpu:.0f}%  "
                f"avg mem Δ{format_mb(avg_mem)}",
                style="dim",
            ),
            "",
        )

    return Panel(grid, border_style="dim cyan", padding=(0, 1))


def log_line_style(line: str) -> str:
    """Style a log line by its spdlog level, not by substrings.

    Matching "error"/"fail" anywhere coloured lines such as
    ``error_lp=false`` or ``failed=0`` red; the level token decides now.
    """
    if line.startswith(">>>"):
        return "bold yellow"
    level = log_line_level(line)
    if level in LEVEL_STYLES:
        return LEVEL_STYLES[level]
    lower = line.lower()
    if "converge" in lower:
        return "bold green"
    if "iteration" in lower or "iter " in lower:
        return "cyan"
    return "dim" if level is None else ""


def build_log(
    lines: list[str] | tuple[str, ...],
    max_lines: int = 10,
    log_path: Path | None = None,
) -> Panel:
    from rich.panel import Panel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    log_text = Text()
    if not lines:
        log_text.append("  Waiting for solver output...", style="dim")
    else:
        for line in lines[-max_lines:]:
            log_text.append(f"  {line}\n", style=log_line_style(line))

    title = "[bold]Output[/bold]"
    if log_path is not None:
        title += f" [dim]· {log_path.name}[/dim]"
    return Panel(
        log_text,
        title=title,
        border_style="dim cyan",
        padding=(0, 0),
    )


MODE_SHORT = {
    MODE_DASHBOARD: "dash",
    MODE_GRID: "grid",
    MODE_CONVERGENCE: "conv",
}


def build_command_bar(
    stop_sent: bool,
    mode: int = MODE_DASHBOARD,
    notice: str | None = None,
    notice_style: str = "bold yellow",
) -> Any:
    """Render the key hints as a one-line footer (no border).

    The old bordered panel used a literal "│" separator and wrapped at
    80 columns, breaking the frame; the footer never wraps (it is cut
    with "…" on very narrow terminals) and saves two rows.  *notice* (a
    pending ``q`` confirmation, or the Ctrl-C / quit "stopping…" state)
    gets its own line above it.
    """
    from rich.console import Group  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    keys = Text(no_wrap=True, overflow="ellipsis")
    keys.append(" ")
    for m in range(MODE_COUNT):
        current = m == mode
        keys.append(str(m + 1), style="bold green" if current else "bold cyan")
        keys.append(f" {MODE_SHORT[m]}  ", style="bold green" if current else "dim")
    keys.append("Tab", style="bold cyan")
    keys.append(" cycle  ·  ", style="dim")
    for key, label in (
        ("s", "stop sent" if stop_sent else "stop"),
        ("i", "stats"),
        ("h", "help"),
        ("q", "quit"),
    ):
        keys.append(key, style="bold cyan")
        keys.append(f" {label}  ", style="dim" if label == "stop sent" else "")
    if not notice:
        return keys
    # The notice may wrap: a cut "[y/N]" would hide the question.
    return Group(Text(f" {notice}", style=notice_style), keys)


def _plan_step_text(text: Any, st: PlanStep, frame: int) -> None:
    text.append("  ")
    if st.status == "done":
        text.append("✓", style="bold green")
        text.append(f" {st.label}")
        text.append(f"  ({st.elapsed:.1f}s)", style="dim")
    elif st.status == "active":
        spinner = SPINNER[frame % len(SPINNER)]
        text.append(spinner, style="bold cyan")
        text.append(f" {st.label}...")
        if st.detail:
            text.append(f"  ({st.detail})", style="cyan")
        text.append(f"  ({st.elapsed:.1f}s)", style="dim")
    else:
        text.append("○", style="dim")
        text.append(f" {st.label}", style="dim")


def build_plan_panel(
    plan: PlanSnapshot | SolverPhaseTracker, compact: bool = False
) -> Panel:
    """Render the solver phase checklist (plan table).

    *plan* is a :class:`PlanSnapshot` (a live tracker is snapshotted
    first).  *compact* (small terminals) shows only the active step — or
    the last finished one — on one line, prefixed by a ``[k/N]`` step
    counter.
    """
    from rich.panel import Panel as RPanel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    if isinstance(plan, SolverPhaseTracker):
        plan = plan.snapshot()
    text = Text()
    steps = [st for st in plan.steps if st.status != "skipped"]
    if compact:
        active = [st for st in steps if st.status == "active"]
        done = [st for st in steps if st.status == "done"]
        shown = active or done[-1:] or steps[:1]
        position = len(done) + (1 if active else 0)
        text.append(f"  [{position}/{len(steps)}]", style="dim")
        for current in shown[:1]:
            _plan_step_text(text, current, plan.frame)
        return RPanel(text, border_style="dim cyan", padding=(0, 0))

    for st in steps:
        _plan_step_text(text, st, plan.frame)
        text.append("\n")

    return RPanel(text, border_style="dim cyan", padding=(0, 0))
