# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Maintenance counters with a fixed calendar (pure engine, no I/O).

One unit has an ordered list of maintenance types, lowest first (e.g.
``CI`` < ``HGP`` < ``major``).  Each type ``k`` keeps a counter of the
usage since its last reset, in four measures, and is due when the first
of them reaches its interval (OEM "whichever comes first")::

    ratio_k = max(EOH / interval_eoh, starts / interval_starts,
                  cycles / interval_cycles, years / max_years)

An event of type ``k`` resets the counters of ``k`` and of every lower
type (hierarchical reset) to ``reset_fraction`` x their value (imperfect
maintenance; default 0 = as good as new).

Usage accrues uniformly inside a period (one gtopt stage).  The calendar
is a list of fixed events (unit, type, time, duration) that reset the
counters at their dates.  When a counter of type ``k`` reaches 1 at
``t_c``:

* if a calendar event ``s`` of level >= ``k`` follows, the cheaper of
  (a) **advancing** ``s`` by ``Δt = t_s − t_c``, priced
  ``C_s·[1 − (1+r)^−Δt]`` (discounted from the advanced date
  ``t_c`` to the start of the horizon), and (b) an **extra** event of
  type ``k`` at ``t_c`` (``C_k`` discounted from ``t_c``) is taken;
* otherwise an extra event of type ``k`` is inserted at ``t_c``.

The calendar stays fixed: an advanced event still resets the counters at
its scheduled date (the advancement is costed, not re-timed).  Every
calendar event inside the horizon is classified from the counter of its
own type just before the reset:

* ``late``: a counter of its level or lower reached 1 before the date
  (``advance_years`` = required advancement Δt);
* ``early``: ``ratio < 1 − tol`` (``life_wasted = 1 − ratio``);
* ``consistent`` otherwise.

A calendar event after the horizon that a counter made late is reported
(and its advancement priced) too, without a reset.

At the end of the horizon the worn share of the next interval of every
type is valued as ``min(ratio_k, 1)·C_k`` (terminal value of the
accumulated wear, discounted from the end).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

#: Default hours of one year.
HOURS_PER_YEAR = 8760.0
#: The four counter measures.
MEASURES = ("eoh", "starts", "cycles", "years")
#: Classification tolerance on the counter ratio.
RATIO_TOL = 1e-6


@dataclass(frozen=True)
class MaintType:
    """One maintenance type of a unit (intervals optional, at least one)."""

    name: str
    interval_eoh: float | None = None  #: HOM / HEOM interval [EOH]
    interval_starts: float | None = None  #: OEM starts interval
    interval_cycles: float | None = None  #: equivalent full cycles (BESS)
    max_years: float | None = None  #: calendar maximum [years]
    cost: float = 0.0  #: $ per event
    outage_days: float = 0.0  #: outage of one event [days]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MaintType:
        """Build from a JSON object; unknown keys raise ``ValueError``."""
        known = {f.name for f in fields(cls)}
        bad = sorted(set(data) - known)
        if bad:
            raise ValueError(f"unknown maintenance type key(s): {', '.join(bad)}")
        if "name" not in data:
            raise ValueError("maintenance type without 'name'")
        kwargs: dict[str, Any] = {
            k: (str(v) if k == "name" else v) for k, v in data.items()
        }
        out = cls(**kwargs)
        out.validate()
        return out

    def validate(self) -> None:
        """Positive intervals (at least one), non-negative cost / outage."""
        ivals = [self.interval(m) for m in MEASURES]
        if all(v is None for v in ivals):
            raise ValueError(f"type {self.name!r}: no interval set")
        for m, v in zip(MEASURES, ivals, strict=True):
            if v is not None and not v > 0:
                raise ValueError(f"type {self.name!r}: {m} interval must be > 0")
        if self.cost < 0 or self.outage_days < 0:
            raise ValueError(f"type {self.name!r}: cost / outage_days must be >= 0")

    def interval(self, measure: str) -> float | None:
        """Interval of one measure (``years`` = ``max_years``)."""
        return {
            "eoh": self.interval_eoh,
            "starts": self.interval_starts,
            "cycles": self.interval_cycles,
            "years": self.max_years,
        }[measure]


@dataclass(frozen=True)
class Period:
    """Usage of one unit over one period (a stage) starting at ``t0`` [h]."""

    t0: float
    hours: float
    eoh: float = 0.0
    starts: float = 0.0
    cycles: float = 0.0
    energy_mwh: float = 0.0  #: unit energy, for the replacement energy
    price: float = 0.0  #: replacement price [$/MWh] of the period


@dataclass(frozen=True)
class CalendarEvent:
    """A fixed maintenance event of the exogenous calendar."""

    unit: str
    type: str
    time: float  #: start [h from the start of the horizon]
    duration: float = 0.0  #: outage [h]


@dataclass
class EventRecord:
    """One maintenance event met in a counter simulation."""

    unit: str
    type: str
    source: str  #: ``calendar`` or ``extra``
    time: float  #: [h]
    ratio: float  #: counter ratio of its type just before the event
    status: str  #: ``early`` / ``late`` / ``consistent`` / ``extra``
    life_wasted: float = 0.0
    advance_years: float = 0.0
    cost_pv: float = 0.0  #: advancement (calendar) or event cost (extra)
    replacement_pv: float = 0.0  #: replacement energy of an extra outage


@dataclass
class CounterResult:
    """Events, terminal wear and the event-method cost of one unit."""

    events: list[EventRecord] = field(default_factory=list)
    terminal_ratio: dict[str, float] = field(default_factory=dict)
    terminal_pv: float = 0.0

    @property
    def extra_pv(self) -> float:
        """PV of the extra (unscheduled) events."""
        return sum(e.cost_pv for e in self.events if e.source == "extra")

    @property
    def advance_pv(self) -> float:
        """PV of the advancement of calendar events."""
        return sum(e.cost_pv for e in self.events if e.source == "calendar")

    @property
    def replacement_pv(self) -> float:
        """PV of the replacement energy of extra outages."""
        return sum(e.replacement_pv for e in self.events)

    @property
    def total_pv(self) -> float:
        """Event-method cost: extra + advancement + replacement + terminal."""
        return self.extra_pv + self.advance_pv + self.replacement_pv + self.terminal_pv


def advancement_cost(cost: float, advance_years: float, rate: float) -> float:
    """``C·[1 − (1+r)^−Δt]``: cost of doing an event Δt years earlier."""
    return float(cost * (1.0 - (1.0 + rate) ** (-advance_years)))


@dataclass
class _State:
    values: list[dict[str, float]]
    due: list[float | None]


class CounterSim:
    """Counter simulation of one unit (see the module doc)."""

    def __init__(
        self,
        unit: str,
        types: Sequence[MaintType],
        *,
        calendar: Sequence[CalendarEvent] = (),
        initial: Mapping[str, Mapping[str, float]] | None = None,
        reset_fraction: float = 0.0,
        discount_rate: float = 0.06,
        hours_per_year: float = HOURS_PER_YEAR,
    ) -> None:
        """Bind the types (lowest first), calendar and initial counters."""
        if not types:
            raise ValueError(f"{unit}: no maintenance types")
        if not 0.0 <= reset_fraction < 1.0:
            raise ValueError("reset_fraction must be in [0, 1)")
        if discount_rate <= -1.0:
            raise ValueError("discount_rate must be > -1")
        self.unit = unit
        self.types = list(types)
        self.level = {t.name: i for i, t in enumerate(self.types)}
        if len(self.level) != len(self.types):
            raise ValueError(f"{unit}: duplicate maintenance type names")
        for ev in calendar:
            if ev.type not in self.level:
                raise ValueError(f"{unit}: calendar type {ev.type!r} is not defined")
        self.calendar = sorted(calendar, key=lambda e: e.time)
        self.reset_fraction = reset_fraction
        self.rate = discount_rate
        self.hpy = hours_per_year
        init = initial or {}
        unknown = sorted(set(init) - set(self.level))
        if unknown:
            raise ValueError(f"{unit}: initial counters of unknown type(s) {unknown}")
        values = []
        for t in self.types:
            v = dict.fromkeys(MEASURES, 0.0)
            for m, x in (init.get(t.name) or {}).items():
                if m not in v:
                    raise ValueError(f"{unit}: unknown counter measure {m!r}")
                if x < 0:
                    raise ValueError(f"{unit}: initial counters must be >= 0")
                v[m] = float(x)
            v["years"] *= hours_per_year  # kept in hours internally
            values.append(v)
        self._st = _State(values, [None] * len(self.types))
        self.result = CounterResult()

    # ── helpers ─────────────────────────────────────────────────────────
    def _df(self, t: float) -> float:
        return float((1.0 + self.rate) ** (-t / self.hpy))

    def _limit(self, t: MaintType, m: str) -> float | None:
        iv = t.interval(m)
        if iv is None:
            return None
        return iv * self.hpy if m == "years" else iv

    def ratio(self, k: int) -> float:
        """Counter ratio of type ``k`` (max over its measures)."""
        t = self.types[k]
        v = self._st.values[k]
        out = 0.0
        for m in MEASURES:
            lim = self._limit(t, m)
            if lim is not None:
                out = max(out, v[m] / lim)
        return out

    def _advance(self, dt: float, rates: Mapping[str, float]) -> None:
        if dt <= 0:
            return
        for v in self._st.values:
            for m in MEASURES:
                v[m] += rates[m] * dt

    def _crossing(
        self, t: float, end: float, rates: Mapping[str, float]
    ) -> tuple[float, int] | None:
        """Earliest ``(t_c, k)`` in ``[t, end)`` where a free counter hits 1."""
        best: tuple[float, int] | None = None
        best_t = 0.0
        for k, typ in enumerate(self.types):
            if self._st.due[k] is not None:
                continue
            v = self._st.values[k]
            for m in MEASURES:
                lim = self._limit(typ, m)
                if lim is None:
                    continue
                if v[m] >= lim * (1.0 - RATIO_TOL):
                    tc = t
                elif rates[m] > 0:
                    tc = t + (lim - v[m]) / rates[m]
                else:
                    continue
                if tc < end and (best is None or tc < best_t):
                    best, best_t = (tc, k), tc
        return best

    def _reset(self, k: int) -> None:
        for j in range(k + 1):
            for m in MEASURES:
                self._st.values[j][m] *= self.reset_fraction
            self._st.due[j] = None

    def _next_calendar(self, tc: float, k: int) -> CalendarEvent | None:
        for ev in self.calendar:
            if ev.time >= tc and self.level[ev.type] >= k:
                return ev
        return None

    # ── events ──────────────────────────────────────────────────────────
    def _on_crossing(self, tc: float, k: int, period: Period) -> None:
        typ = self.types[k]
        extra_pv = typ.cost * self._df(tc)
        nxt = self._next_calendar(tc, k)
        if nxt is not None:
            dt_years = (nxt.time - tc) / self.hpy
            ctyp = self.types[self.level[nxt.type]]
            adv_pv = advancement_cost(ctyp.cost, dt_years, self.rate) * self._df(tc)
            if adv_pv <= extra_pv:
                self._st.due[k] = tc  # late: priced when the event is met
                return
        outage_h = typ.outage_days * 24.0
        mean_mw = period.energy_mwh / period.hours if period.hours > 0 else 0.0
        repl = outage_h * mean_mw * period.price * self._df(tc)
        self.result.events.append(
            EventRecord(
                unit=self.unit,
                type=typ.name,
                source="extra",
                time=tc,
                ratio=self.ratio(k),
                status="extra",
                cost_pv=extra_pv,
                replacement_pv=repl,
            )
        )
        self._reset(k)

    def _on_calendar(self, ev: CalendarEvent) -> None:
        j = self.level[ev.type]
        ratio = self.ratio(j)
        dues = [d for d in self._st.due[: j + 1] if d is not None]
        rec = EventRecord(
            unit=self.unit,
            type=ev.type,
            source="calendar",
            time=ev.time,
            ratio=ratio,
            status="consistent",
        )
        if dues:
            tc = min(dues)
            rec.status = "late"
            rec.advance_years = (ev.time - tc) / self.hpy
            rec.cost_pv = advancement_cost(
                self.types[j].cost, rec.advance_years, self.rate
            ) * self._df(tc)
        elif ratio < 1.0 - RATIO_TOL:
            rec.status = "early"
            rec.life_wasted = 1.0 - ratio
        self.result.events.append(rec)
        self._reset(j)

    def _pending_beyond(self, later: Sequence[CalendarEvent]) -> None:
        """Price the advancement of calendar events after the horizon."""
        for ev in later:
            j = self.level[ev.type]
            dues = [d for d in self._st.due[: j + 1] if d is not None]
            if not dues:
                continue
            tc = min(dues)
            adv = (ev.time - tc) / self.hpy
            self.result.events.append(
                EventRecord(
                    unit=self.unit,
                    type=ev.type,
                    source="calendar",
                    time=ev.time,
                    ratio=self.ratio(j),
                    status="late",
                    advance_years=adv,
                    cost_pv=advancement_cost(self.types[j].cost, adv, self.rate)
                    * self._df(tc),
                )
            )
            for k in range(j + 1):
                self._st.due[k] = None

    # ── driver ──────────────────────────────────────────────────────────
    def run(self, periods: Sequence[Period]) -> CounterResult:
        """Simulate ``periods`` (chronological); returns the result."""
        cal = list(self.calendar)
        ci = 0
        start = periods[0].t0 if periods else 0.0
        while ci < len(cal) and cal[ci].time < start:
            ci += 1  # before the horizon: already reflected in `initial`
        end_h = start
        for per in periods:
            t = per.t0
            end = per.t0 + per.hours
            end_h = end
            h = per.hours
            rates = {
                "eoh": per.eoh / h if h > 0 else 0.0,
                "starts": per.starts / h if h > 0 else 0.0,
                "cycles": per.cycles / h if h > 0 else 0.0,
                "years": 1.0,
            }
            while True:
                seg_end = end
                has_cal = ci < len(cal) and cal[ci].time < end
                if has_cal:
                    seg_end = max(t, cal[ci].time)
                hit = self._crossing(t, seg_end, rates)
                if hit is not None:
                    tc, k = hit
                    self._advance(tc - t, rates)
                    t = tc
                    self._on_crossing(tc, k, per)
                    continue
                self._advance(seg_end - t, rates)
                t = seg_end
                if has_cal:
                    self._on_calendar(cal[ci])
                    ci += 1
                    continue
                break
        self._pending_beyond(cal[ci:])
        df_end = self._df(end_h)
        for k, typ in enumerate(self.types):
            r = self.ratio(k)
            self.result.terminal_ratio[typ.name] = r
            self.result.terminal_pv += min(r, 1.0) * typ.cost * df_end
        return self.result


__all__ = [
    "HOURS_PER_YEAR",
    "MEASURES",
    "CalendarEvent",
    "CounterResult",
    "CounterSim",
    "EventRecord",
    "MaintType",
    "Period",
    "advancement_cost",
]
