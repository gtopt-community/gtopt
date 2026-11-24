# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Price-taker self-schedule of one committable unit (dynamic programming).

The unit maximises ``Σ_t [(λ_t - c_t)·p_t - no-load·u_t]·Δt_t
- Σ_t (SU_t·v_t + SD_t·w_t)`` over its own constraints only:
``pmin_t·u_t ≤ p_t ≤ pmax_t·u_t``, minimum up / down times, the initial
state and ``must_run``.  Given ``u_t`` the best output is ``pmax_t`` when
``λ_t > c_t`` and ``pmin_t`` otherwise, so the DP runs over the on/off
state and the time spent in it (capped at ``max(min_up, min_down)``).
Ramp limits are ignored — the self-schedule profit is therefore an upper
bound and the lost-opportunity cost a conservative (high) estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_EPS = 1e-9


@dataclass
class SelfScheduleUnit:
    """Per-period data of one unit, in chronological order."""

    price: list[float]
    cost: list[float]
    duration: list[float]
    pmin: list[float]
    pmax: list[float]
    startup_cost: list[float] = field(default_factory=list)
    shutdown_cost: list[float] = field(default_factory=list)
    noload_cost: float = 0.0
    min_up: float = 0.0
    min_down: float = 0.0
    initial_on: bool = False
    initial_hours: float | None = None
    must_run: bool = False


def _period_on_value(unit: SelfScheduleUnit, t: int) -> float:
    """Best profit of period ``t`` given the unit is on."""
    margin = unit.price[t] - unit.cost[t]
    dur = unit.duration[t]
    best_p = unit.pmax[t] if margin > 0.0 else unit.pmin[t]
    return (margin * best_p - unit.noload_cost) * dur


def _at(values: list[float], t: int) -> float:
    return values[t] if t < len(values) else 0.0


def self_schedule_profit(unit: SelfScheduleUnit) -> float:
    """Maximum profit of the price-taking self-schedule of ``unit``."""
    cap = max(unit.min_up, unit.min_down, 0.0)
    start_hours = cap if unit.initial_hours is None else unit.initial_hours
    states: dict[tuple[bool, float], float] = {
        (unit.initial_on, min(max(start_hours, 0.0), cap)): 0.0
    }
    for t, dur in enumerate(unit.duration):
        on_value = _period_on_value(unit, t)
        nxt: dict[tuple[bool, float], float] = {}
        for (on, hours), value in states.items():
            for on2 in (True, False):
                if unit.must_run and not on2:
                    continue
                gain = on_value if on2 else 0.0
                if on and not on2:
                    if hours + _EPS < unit.min_up:
                        continue
                    gain -= _at(unit.shutdown_cost, t)
                elif on2 and not on:
                    if hours + _EPS < unit.min_down:
                        continue
                    gain -= _at(unit.startup_cost, t)
                hours2 = min(hours + dur if on2 == on else dur, cap)
                key = (on2, round(hours2, 9))
                cand = value + gain
                if cand > nxt.get(key, float("-inf")):
                    nxt[key] = cand
        if not nxt:
            # Infeasible horizon (e.g. must_run against an unmet min-down):
            # nothing the unit can do — report no self-schedule profit.
            return 0.0
        states = nxt
    return max(states.values())
