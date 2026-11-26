"""Cyclic unit commitment on typical periods (``--uc-cycle`` /
``--long-transition``).

``apply_uc_cycle`` marks every ``Commitment`` cyclic (``cycle: "stage"``)
and finds the units whose ``min_up_time`` / ``min_down_time`` exceed the
typical period of some chronological stage (Σ block durations, or
``period_hours``).  gtopt refuses such units unless they declare
``long_transition`` (``on`` / ``off`` / ``no_uc``); the converter only
writes that declaration when ``--long-transition`` is given, and always
prints the affected list — it never decides silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

LONG_TRANSITION_MODES = ("on", "off", "no_uc")

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LongUnit:
    """A cyclic commitment with a time constraint longer than a cycle."""

    name: str
    constraint: str
    hours: float
    cycle_hours: float


def stage_cycle_hours(planning: dict[str, Any]) -> list[float]:
    """Typical-period length [h] of every chronological stage."""
    sim = planning.get("simulation", {})
    blocks = sim.get("block_array", [])
    out: list[float] = []
    for stage in sim.get("stage_array", []):
        if not stage.get("chronological", False):
            continue
        if stage.get("period_hours"):
            out.append(float(stage["period_hours"]))
            continue
        first = int(stage.get("first_block", 0))
        count = int(stage.get("count_block", 0))
        out.append(sum(float(b["duration"]) for b in blocks[first : first + count]))
    return out


def find_long_units(planning: dict[str, Any]) -> list[LongUnit]:
    """Cyclic commitments whose MUT / MDT exceed the shortest cycle."""
    cycles = [c for c in stage_cycle_hours(planning) if c > 0.0]
    if not cycles:
        return []
    shortest = min(cycles)
    out: list[LongUnit] = []
    for cm in planning.get("system", {}).get("commitment_array", []):
        if cm.get("cycle") != "stage":
            continue
        for field in ("min_up_time", "min_down_time"):
            hours = float(cm.get(field, 0.0) or 0.0)
            if hours > shortest + 1e-9:
                out.append(LongUnit(str(cm.get("name")), field, hours, shortest))
    return out


def apply_uc_cycle(
    planning: dict[str, Any],
    *,
    cycle: str = "none",
    long_transition: str | None = None,
) -> list[LongUnit]:
    """Mark commitments cyclic and declare long units when asked.

    Returns the long units found.  With ``long_transition`` unset they
    stay undeclared (gtopt then fails validation listing them).
    """
    if cycle != "stage":
        return []
    if long_transition is not None and long_transition not in LONG_TRANSITION_MODES:
        raise ValueError(
            f"--long-transition must be one of {LONG_TRANSITION_MODES}, "
            f"got {long_transition!r}"
        )
    commitments = planning.get("system", {}).get("commitment_array", [])
    for cm in commitments:
        cm["cycle"] = "stage"
        # cyclic UC has no initial state
        for key in ("initial_status", "initial_hours", "initial_power"):
            cm.pop(key, None)
    longs = find_long_units(planning)
    names = {u.name for u in longs}
    if long_transition is not None:
        for cm in commitments:
            if cm.get("name") in names:
                cm["long_transition"] = long_transition
    for unit in longs:
        msg = (
            f"cyclic UC: {unit.name} {unit.constraint} = {unit.hours} h > "
            f"typical period {unit.cycle_hours} h"
        )
        if long_transition is None:
            _log.warning("%s — undeclared (pass --long-transition)", msg)
        else:
            _log.warning("%s — long_transition = %s", msg, long_transition)
    return longs
