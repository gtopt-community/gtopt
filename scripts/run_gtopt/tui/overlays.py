# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Overlays toggled over any view: help (``h``), system stats (``i``)
and run paths (``o``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.panel import Panel


def build_help_overlay() -> Panel:
    """Render the help overlay panel."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    table = Table(show_header=False, border_style="dim", padding=(0, 2), expand=True)
    table.add_column("Key", style="bold cyan", width=18)
    table.add_column("Description")

    commands = [
        ("1", "Dashboard mode — stats, history table, log output"),
        ("2", "Grid mode — SDDP phase activity grid"),
        ("3", "Convergence mode — per-scene cost heatmap + sparklines"),
        ("Tab", "Cycle through display modes"),
        ("s", "Graceful stop — finish current iteration, save cuts, exit"),
        ("i", "Toggle system stats overlay (buses, generators, etc.)"),
        ("o", "Toggle paths overlay (output dir, gtopt log, status, error LPs)"),
        ("h / ?", "Toggle this help overlay (Esc closes overlays)"),
        ("q", "Quit — terminate the solver (asks y/N after the first minute)"),
        (
            "Ctrl+C",
            "1st: graceful stop (like s) · 2nd: terminate · 3rd: kill",
        ),
        ("j / k  ↓ / ↑", "Grid mode: scroll scenes down / up by 1"),
        ("J / K  PgDn / PgUp", "Grid mode: scroll scenes down / up by 5 (page)"),
        ("g / G  Home / End", "Grid mode: jump to first / last scene"),
    ]
    for key, desc in commands:
        table.add_row(Text(key, style="bold cyan"), desc)

    return Panel(
        table,
        title="[bold]Help — Interactive Commands[/bold]",
        border_style="cyan",
        padding=(0, 1),
    )


def build_paths_overlay(
    entries: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> Panel:
    """The ``o`` overlay: where this run's files are."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("What", style="bold")
    table.add_column("Path", overflow="fold")
    for label, value in entries:
        table.add_row(label, value)
    return Panel(table, title="[bold]Paths[/bold]", border_style="cyan", padding=(0, 1))


def build_stats_overlay(stats: dict[str, Any]) -> Panel:
    """Render the system stats overlay from the planning JSON."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    if not stats:
        return Panel(
            Text("  No planning JSON found", style="dim"),
            title="[bold]System Stats[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", min_width=18)
    grid.add_column(min_width=10)
    grid.add_column(style="bold", min_width=18)
    grid.add_column(min_width=10)

    # System elements (2-column layout)
    elements = stats.get("elements", {})
    items = list(elements.items())
    for idx in range(0, len(items), 2):
        name1, count1 = items[idx]
        if idx + 1 < len(items):
            name2, count2 = items[idx + 1]
            grid.add_row(
                name1,
                Text(str(count1), style="cyan"),
                name2,
                Text(str(count2), style="cyan"),
            )
        else:
            grid.add_row(name1, Text(str(count1), style="cyan"), "", "")

    # Simulation structure
    grid.add_row("", "", "", "")
    grid.add_row(
        "Scenarios",
        Text(str(stats.get("scenarios", "?")), style="cyan"),
        "Stages",
        Text(str(stats.get("stages", "?")), style="cyan"),
    )
    grid.add_row(
        "Blocks",
        Text(str(stats.get("blocks", "?")), style="cyan"),
        "Method",
        Text(str(stats.get("method", "?")), style="cyan"),
    )
    solver = stats.get("solver", "")
    if solver:
        grid.add_row(
            "Solver",
            Text(solver, style="cyan"),
            "",
            Text("", style="cyan"),
        )

    # Key options
    rows: list[tuple[str, str]] = []
    if stats.get("scale_objective") is not None:
        rows.append(("Scale objective", str(stats["scale_objective"])))
    if stats.get("use_kirchhoff") is not None:
        rows.append(("Kirchhoff", str(stats["use_kirchhoff"])))
    if stats.get("use_single_bus") is not None:
        rows.append(("Single bus", str(stats["use_single_bus"])))
    for idx in range(0, len(rows), 2):
        name1, val1 = rows[idx]
        if idx + 1 < len(rows):
            name2, val2 = rows[idx + 1]
            grid.add_row(name1, Text(val1, style="dim"), name2, Text(val2, style="dim"))
        else:
            grid.add_row(name1, Text(val1, style="dim"), "", "")

    return Panel(
        grid,
        title="[bold]System Stats[/bold]",
        border_style="cyan",
        padding=(0, 1),
    )
