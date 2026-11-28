# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Peak hours of a solved case: the Chilean *demanda de punta*.

The CEN's peak demand is the mean of the **52 highest hourly loads** of
the annual load curve — the same hours that measure the sufficiency of
PV and wind units (NT de Transferencias de Potencia; CEN 2025
definitive calculation, cap. VII).  The *período de control de punta*
(18:00–22:00 on working days, April–September; DS 4T/2026 §3.3) bills
the clients' peak demand, not the generators' sufficiency; it is
available as :data:`CONTROL_PERIOD_WINDOW`.  A gtopt case knows its loads per block; with a
``simulation.block_hour_map`` each chronological hour takes the load of
its block, so the 52 hours can be picked inside the window; without it
the highest-load blocks are taken by duration until 52 hours are filled
(no window).  Computed per scenario (hydrology).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .case_data import PKey, SystemData

#: the generators' sufficiency uses the 52 highest hours of the WHOLE
#: annual load curve (NT de Transferencias de Potencia; CEN, Informe
#: Potencia de Suficiencia 2025 definitivo, cap. VII) — no window
DEFAULT_WINDOW: dict[str, Any] = {}

#: the *período de control de punta* (April–September, hours ending
#: 19–22, working days; DS 4T/2026 §3.3) bills the CLIENTS' peak
#: demand; pass it as ``peak_window`` to study that period instead
CONTROL_PERIOD_WINDOW: dict[str, Any] = {
    "months": [4, 5, 6, 7, 8, 9],
    "hours": [19, 20, 21, 22],
    "working_days": True,
}


@dataclass
class PeakHours:
    """The ``n`` peak hours of one scenario."""

    scenario: int
    demand_mw: float  #: mean load of the peak hours [MW]
    #: period key -> number of peak hours that fall in it
    weight: dict[PKey, float] = field(default_factory=dict)
    windowed: bool = False  #: picked inside the control window

    @property
    def hours(self) -> float:
        return sum(self.weight.values())

    def mean(self, values: Mapping[PKey, float], default: float = 0.0) -> float:
        """Hour-weighted mean of a per-period quantity over the peak hours."""
        h = self.hours
        if h <= 0:
            return default
        return sum(n * values.get(k, default) for k, n in self.weight.items()) / h


def _in_window(y: int, m: int, d: int, h: int, window: Mapping[str, Any]) -> bool:
    months = window.get("months")
    if months and m not in months:
        return False
    hours = window.get("hours")
    if hours and h not in hours:
        return False
    if window.get("working_days"):
        try:
            if dt.date(y, m, d).weekday() >= 5:
                return False
        except ValueError:
            return False
    return True


def peak_hours(
    sysd: SystemData, n: int = 52, window: Mapping[str, Any] | None = None
) -> dict[int, PeakHours]:
    """Per scenario: the ``n`` highest-load hours (in ``window`` if any).

    ``window=None`` uses :data:`DEFAULT_WINDOW` (the whole year).  A
    window that leaves fewer than ``n`` hours falls back to the year.
    """
    win = DEFAULT_WINDOW if window is None else window
    out: dict[int, PeakHours] = {}
    for scen in sysd.scenarios:
        ph = _from_hour_map(sysd, scen, n, win) if sysd.hour_map else None
        if ph is None and sysd.hour_map and win:
            ph = _from_hour_map(sysd, scen, n, {})
        if ph is None:
            ph = _from_blocks(sysd, scen, n)
        out[scen] = ph
    return out


def _from_hour_map(
    sysd: SystemData, scen: int, n: int, window: Mapping[str, Any]
) -> PeakHours | None:
    hours: list[tuple[float, PKey]] = []
    for y, m, d, h, b in sysd.hour_map:
        if window and not _in_window(y, m, d, h, window):
            continue
        key = (scen, sysd.block_stage[b], b)
        if key in sysd.load:
            hours.append((sysd.load[key], key))
    if len(hours) < n:
        return None
    hours.sort(key=lambda t: t[0], reverse=True)
    ph = PeakHours(scenario=scen, demand_mw=0.0, windowed=bool(window))
    for load, key in hours[:n]:
        ph.weight[key] = ph.weight.get(key, 0.0) + 1.0
        ph.demand_mw += load / n
    return ph


def _from_blocks(sysd: SystemData, scen: int, n: int) -> PeakHours:
    blocks = sorted(
        (
            (sysd.load.get((p.scenario, p.stage, p.block), 0.0), p)
            for p, _w in sysd.periods
            if p.scenario == scen
        ),
        key=lambda t: t[0],
        reverse=True,
    )
    ph = PeakHours(scenario=scen, demand_mw=0.0)
    left = float(n)
    total = 0.0
    for load, p in blocks:
        if left <= 1e-9:
            break
        take = min(left, p.duration * sysd.scale)
        ph.weight[(p.scenario, p.stage, p.block)] = take
        total += load * take
        left -= take
    ph.demand_mw = total / ph.hours if ph.hours else 0.0
    return ph
