# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""What stays on screen after the dashboard: the final summary and the
outcome banner.  Pure builders; the controller gathers the inputs."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .fmt import format_elapsed, format_number
from .parse import PlanSnapshot

if TYPE_CHECKING:
    from rich.panel import Panel

    from .._outcome import Outcome

# Iteration rows kept in the persistent final summary.
SUMMARY_ITERATIONS = 10


def build_final_summary(
    history: Sequence[dict[str, Any]],
    plan: PlanSnapshot,
    warnings: Sequence[str],
    n_warn: int,
    n_err: int,
) -> list[Any]:
    """The last iterations, step timings, warning/error counts and the
    first distinct warnings (printed above the outcome banner)."""
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    out: list[Any] = []
    if history:
        table = Table(
            title="Last iterations",
            title_justify="left",
            title_style="bold",
            header_style="bold",
            border_style="dim",
            box=None,
            padding=(0, 1),
        )
        for col in ("Iter", "Lower bound", "Upper bound", "Gap", "Fwd", "Bwd"):
            table.add_column(col, justify="right")
        for rec in history[-SUMMARY_ITERATIONS:]:
            table.add_row(
                str(rec.get("iteration", "")),
                format_number(rec.get("lower_bound", 0.0)),
                format_number(rec.get("upper_bound", 0.0)),
                f"{100.0 * rec.get('gap', 0.0):.2f}%",
                f"{rec.get('forward_pass_s', 0.0):.1f}s",
                f"{rec.get('backward_pass_s', 0.0):.1f}s",
            )
        out.append(table)
    steps = [
        f"{st.label} {st.elapsed:.1f}s" for st in plan.steps if st.status == "done"
    ]
    if steps:
        out.append(Text("Steps: " + " · ".join(steps), style="dim"))
    if n_warn or n_err:
        counts = Text("Log: ")
        counts.append(f"{n_warn} warning(s)", style="yellow" if n_warn else "")
        counts.append(", ")
        counts.append(f"{n_err} error(s)", style="red" if n_err else "")
        out.append(counts)
        for w in warnings:
            out.append(Text(f"  {w}", style="yellow"))
    return out


def build_outcome_banner(
    outcome: Outcome,
    elapsed: float,
    failure_lines: Sequence[str] = (),
    hint_lines: Sequence[str] = (),
    paths: Sequence[tuple[str, Path]] = (),
) -> Panel:
    """The bordered ``✓ gtopt — Success`` / ``✗ ...`` banner.

    *failure_lines* (gtopt's errors and output tail) are shown for
    failed / killed / non-optimal outcomes only; *hint_lines* point at
    error LPs; *paths* lists where the run's files are.
    """
    from rich.panel import Panel as RPanel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    style = outcome.style
    text = Text(f"  Elapsed {format_elapsed(elapsed)}", style=f"bold {style}")
    for line in outcome.lines:
        text.append(f"\n  {line}", style=style)
    if outcome.kind in ("failed", "killed", "non_optimal"):
        text.append("\n")
        for line in failure_lines:
            text.append(
                f"\n  {line}",
                style="red" if line.startswith("  ") else "bold",
            )
    if hint_lines:
        text.append("\n")
        for line in hint_lines:
            text.append(f"\n  {line}", style="red" if "error LP" in line else "")
    if paths:
        text.append("\n")
        width = max(len(label) for label, _ in paths)
        for label, path in paths:
            text.append(f"\n  {label + ':':<{width + 1}} ", style="bold")
            text.append(str(path), style="")
    return RPanel(
        text,
        title=f"[bold {style}]{outcome.icon} gtopt — {outcome.title}[/bold {style}]",
        border_style=style,
        padding=(0, 1),
    )
