# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Classify how a gtopt run ended, for the final banner.

The exit code alone is not the outcome: gtopt exits 0 when an SDDP run
hits ``max_iterations`` without converging, when it stops on a graceful
stop request, or when a monolithic solve wrote no solution.  The banner
therefore combines the exit code with ``solver_status.json`` (its final
``status``: ``converged`` / ``max_iterations`` / ``stopped`` / ``done``)
and the ``status`` column of ``solution.csv`` (0 = optimal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._process import describe_exit

# Outcome kinds, from best to worst.
OPTIMAL = "optimal"
CONVERGED = "converged"
NOT_CONVERGED = "not_converged"
STOPPED = "stopped"
UNKNOWN = "unknown"
DETACHED = "detached"
NON_OPTIMAL = "non_optimal"
FAILED = "failed"
KILLED = "killed"

#: Kinds that mean "the run did what was asked".
GOOD_KINDS = frozenset({OPTIMAL, CONVERGED})

_STYLE = {
    OPTIMAL: "green",
    CONVERGED: "green",
    NOT_CONVERGED: "yellow",
    STOPPED: "yellow",
    UNKNOWN: "cyan",
    DETACHED: "cyan",
    NON_OPTIMAL: "red",
    FAILED: "red",
    KILLED: "red",
}
_ICON = {
    OPTIMAL: "✓",
    CONVERGED: "✓",
    NOT_CONVERGED: "⚠",
    STOPPED: "⏸",
    UNKNOWN: "?",
    DETACHED: "↪",
    NON_OPTIMAL: "✗",
    FAILED: "✗",
    KILLED: "✗",
}
_ASCII_ICON = {
    OPTIMAL: "OK",
    CONVERGED: "OK",
    NOT_CONVERGED: "!!",
    STOPPED: "||",
    UNKNOWN: "??",
    DETACHED: "->",
    NON_OPTIMAL: "XX",
    FAILED: "XX",
    KILLED: "XX",
}


@dataclass
class Outcome:
    """What the banner says about a finished (or detached) run."""

    kind: str
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def style(self) -> str:
        return _STYLE[self.kind]

    @property
    def icon(self) -> str:
        return _ICON[self.kind]

    @property
    def ascii_icon(self) -> str:
        return _ASCII_ICON[self.kind]

    @property
    def ok(self) -> bool:
        return self.kind in GOOD_KINDS


def _gap_text(status: dict[str, Any]) -> str:
    gap = status.get("gap")
    return f"gap {100.0 * gap:.3g}%" if isinstance(gap, (int, float)) else ""


def _iter_text(status: dict[str, Any]) -> str:
    it = status.get("iteration")
    max_it = status.get("max_iterations")
    if it is None:
        return ""
    return f"iteration {it}/{max_it}" if max_it else f"iteration {it}"


def _join(*parts: str) -> str:
    return ", ".join(p for p in parts if p)


def _solution_line(solution: dict[str, Any]) -> str | None:
    if solution.get("stale"):
        return "solution.csv is from a previous run (no new solution written)"
    sol_status = solution.get("status")
    if sol_status is None:
        return None
    if sol_status == 0:
        return "solution status: optimal"
    return f"solution status {sol_status}: NON-OPTIMAL / INFEASIBLE"


def classify(
    returncode: int | None,
    status: dict[str, Any] | None = None,
    solution: dict[str, Any] | None = None,
    *,
    stop_requested: bool = False,
    forced: str | None = None,
    detached: bool = False,
) -> Outcome:
    """Combine exit code, status JSON and solution file into an Outcome.

    *returncode* is ``None`` when unknown (``--attach``).  *forced* is
    ``"terminated"`` / ``"killed"`` when run_gtopt had to signal gtopt;
    *detached* means the observer left while gtopt kept running.
    """
    status = status or {}
    solution = solution or {}
    final = str(status.get("status") or "")
    progress = _join(_iter_text(status), _gap_text(status))
    sol_line = _solution_line(solution)
    sol_status = None if solution.get("stale") else solution.get("status")

    if detached:
        return Outcome(
            DETACHED,
            "Detached — gtopt is still running",
            [line for line in (progress,) if line],
        )

    if forced is not None:
        lines = [f"gtopt {describe_exit(returncode)}"]
        if progress:
            lines.append(f"last progress: {progress}")
        lines.append("results of this run are incomplete")
        return Outcome(KILLED, f"Interrupted — gtopt was {forced}", lines)

    if returncode is not None and returncode != 0:
        lines = [f"gtopt {describe_exit(returncode)}"]
        if sol_line:
            lines.append(sol_line)
        if returncode < 0 or returncode > 128:
            return Outcome(KILLED, "Failed — gtopt was killed", lines)
        if returncode == 1:
            return Outcome(
                NON_OPTIMAL, "Finished — solution NOT optimal / infeasible", lines
            )
        return Outcome(FAILED, "Failed", lines)

    # returncode is 0 (or unknown, when attached to a finished run).
    lines = [line for line in (progress, sol_line) if line]
    if sol_status not in (None, 0):
        return Outcome(NON_OPTIMAL, "Finished — solution NOT optimal", lines)
    if final == "converged" or (status.get("converged") and final != "stopped"):
        return Outcome(CONVERGED, "Converged", lines)
    if final == "max_iterations":
        return Outcome(
            NOT_CONVERGED, "Finished — NOT converged (iteration limit)", lines
        )
    if final == "stopped" or stop_requested:
        return Outcome(
            STOPPED, "Stopped on request — NOT converged (cuts saved)", lines
        )
    if sol_status == 0:
        return Outcome(OPTIMAL, "Optimal", lines)
    if returncode == 0:
        lines.append("no solver status or solution file was found")
        return Outcome(UNKNOWN, "Finished — solution status unknown", lines)
    lines.append("gtopt exit status unknown")
    return Outcome(UNKNOWN, "Ended — outcome unknown", lines)
