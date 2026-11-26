"""Readers for AEMO ISP PLEXOS trace files and timeslice calendars.

The AEMO Integrated System Plan (ISP) PLEXOS model ships its time series
as ``Data File`` objects whose ``Filename`` property points at a CSV
under ``Traces\\<kind>\\``.  Three layouts occur:

* **Interval-wide** — ``Year,Month,Day,01..48`` (half-hourly; demand,
  solar, wind, rooftop PV, half-hourly hydro inflows) or ``01..24``.
* **Daily** — ``Year,Month,Day,<value>`` (monthly/daily hydro inflows).
* **Yearly-by-column** — ``Year,<object>,<object>…`` (``Max Energy
  Year`` hydro energy limits, one column per generator).

Timeslices (``Summer``, ``NSW Hot Day``, ``M7`` …) scope individual
property rows.  Their activity comes from the object name (``M1``..``M12``),
a month pattern on ``Include`` (``M4-10``, ``M1-3,11,12``) or a
state-change CSV (``DATETIME,NAME,TIMESLICE`` with ``-1`` = on).

Everything is evaluated per **calendar day**: the ISP timeslices change
at day granularity, and the interval traces are averaged to hourly
blocks by :func:`read_interval_trace`.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path, PureWindowsPath

import numpy as np

_MONTH_PATTERN_RE = re.compile(r"^M[\d\-,\s]+$")


class TraceLocator:
    """Resolve PLEXOS ``Traces\\kind\\file.csv`` names against local roots.

    AEMO distributes the traces in several archives (model folder with
    ``Traces/demand`` …, plus separate solar / wind / hydro / timeslice
    archives).  Each root is tried in order with three layouts:
    ``root/Traces/kind/file``, ``root/kind/file`` and ``root/file``.
    """

    def __init__(self, roots: Sequence[Path | str]) -> None:
        self.roots = [Path(r) for r in roots]

    def resolve(self, filename: str) -> Path | None:
        """Return the local path of ``filename`` or ``None``."""
        parts = PureWindowsPath(filename.strip()).parts
        if not parts:
            return None
        tails = [Path(*parts)]
        if parts[0].lower() == "traces" and len(parts) > 1:
            tails.append(Path(*parts[1:]))
        tails.append(Path(parts[-1]))
        for root in self.roots:
            for tail in tails:
                cand = root / tail
                if cand.is_file():
                    return cand
        return None


@lru_cache(maxsize=4096)
def _read_rows(path: str) -> tuple[list[str], dict[date, list[float]]]:
    """Read a ``Year,Month,Day,…`` CSV into ``{date: values}``."""
    rows: dict[date, list[float]] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        for rec in reader:
            if len(rec) < 4:
                continue
            day = date(int(rec[0]), int(rec[1]), int(rec[2]))
            rows[day] = [float(v) if v.strip() else 0.0 for v in rec[3:]]
    return header, rows


#: samples per day of every series built from the traces (24 = hourly,
#: the default; 48 = half-hourly, used by the typical-week samplers)
_STEPS_PER_DAY = [24]


def steps_per_day() -> int:
    """Samples per day of the series read now (24 or 48)."""
    return _STEPS_PER_DAY[0]


@contextmanager
def trace_resolution(spd: int) -> Iterator[None]:
    """Read every trace at ``spd`` samples per day (24 or 48) inside the
    ``with`` block."""
    if spd not in (24, 48):
        raise ValueError(f"steps per day must be 24 or 48, got {spd}")
    old = _STEPS_PER_DAY[0]
    _STEPS_PER_DAY[0] = spd
    try:
        yield
    finally:
        _STEPS_PER_DAY[0] = old


def read_interval_trace(
    path: Path, start: date, days: int, *, fill: str = "nearest"
) -> np.ndarray:
    """Return a series (``steps_per_day() × days``, hourly by default) from
    an interval or daily CSV.

    Half-hourly columns are averaged in pairs for an hourly series (MW
    average over the hour) or kept as they are for a half-hourly one;
    hourly columns are held over both half hours; a single value column
    is held constant over the day.  A day
    missing from the file is filled from the nearest earlier day of the
    same calendar date one year back when ``fill == "nearest"`` (daily
    hydro files start at 2019/2020) or raises ``KeyError``.
    """
    _header, rows = _read_rows(str(path))
    spd = steps_per_day()
    out = np.zeros(spd * days, dtype=np.float64)
    for k in range(days):
        day = start + timedelta(days=k)
        vals = rows.get(day)
        if vals is None and fill == "nearest":
            vals = _nearest(rows, day)
        if vals is None:
            raise KeyError(f"{path.name}: no row for {day}")
        out[spd * k : spd * (k + 1)] = _to_steps(vals, spd)
    return out


def _nearest(rows: dict[date, list[float]], day: date) -> list[float] | None:
    if not rows:
        return None
    last = max(rows)
    first = min(rows)
    probe = day
    # Shift whole years towards the covered range, keeping the calendar day.
    while probe > last:
        probe = _shift_year(probe, -1)
    while probe < first:
        probe = _shift_year(probe, +1)
    return rows.get(probe)


def _shift_year(day: date, delta: int) -> date:
    try:
        return day.replace(year=day.year + delta)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year + delta, day=28)


def _to_steps(vals: list[float], spd: int) -> np.ndarray:
    if spd == 24:
        return _to_hourly(vals)
    n = len(vals)
    arr = np.asarray(vals, dtype=np.float64)
    if n == 48:
        return arr
    if n == 24:
        return np.repeat(arr, 2)
    if n == 1:
        return np.full(48, arr[0])
    raise ValueError(f"unsupported interval count {n} (expected 48, 24 or 1)")


def _to_hourly(vals: list[float]) -> np.ndarray:
    n = len(vals)
    arr = np.asarray(vals, dtype=np.float64)
    if n == 48:
        hourly: np.ndarray = arr.reshape(24, 2).mean(axis=1)
        return hourly
    if n == 24:
        return arr
    if n == 1:
        return np.full(24, arr[0])
    raise ValueError(f"unsupported interval count {n} (expected 48, 24 or 1)")


@lru_cache(maxsize=64)
def _read_yearly(path: str) -> tuple[list[str], dict[int, list[float]]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        rows = {
            int(rec[0]): [float(v) if v.strip() else 0.0 for v in rec[1:]]
            for rec in reader
            if rec and rec[0].strip()
        }
    return header, rows


def read_yearly_column(path: Path, year: int, column: str) -> float | None:
    """Value of ``column`` for ``year`` in a ``Year,<col>…`` CSV.

    Falls back to the last year present when ``year`` is past the end.
    """
    header, rows = _read_yearly(str(path))
    if column not in header[1:] or not rows:
        return None
    idx = header[1:].index(column)
    past = [y for y in rows if y <= year]
    key = year if year in rows else (max(past) if past else min(rows))
    return rows[key][idx]


# ---------------------------------------------------------------------------
# Timeslices
# ---------------------------------------------------------------------------


def parse_month_pattern(text: str) -> set[int] | None:
    """``"M4-10"`` → {4..10}; ``"M1-3,11,12"`` → {1,2,3,11,12}.

    Returns ``None`` when ``text`` is not a pure month pattern (PLEXOS
    also allows day/hour patterns such as ``"D1,H1; D15,H1"`` which do
    not apply at day granularity).
    """
    text = text.strip()
    if not _MONTH_PATTERN_RE.match(text):
        return None
    months: set[int] = set()
    for tok in text[1:].split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            lo, hi = (int(x) for x in tok.split("-", 1))
            months.update(range(lo, hi + 1))
        else:
            months.add(int(tok))
    return months


@dataclass
class TimesliceCalendar:
    """Day-level activity of named PLEXOS timeslices."""

    #: name → set of active months
    months: dict[str, set[int]] = field(default_factory=dict)
    #: name → sorted [(date, active)] state changes
    switches: dict[str, list[tuple[date, bool]]] = field(default_factory=dict)

    def add_month_name(self, name: str) -> bool:
        """Register ``M<n>`` style names; return True when recognised."""
        m = re.fullmatch(r"M(\d{1,2})", name.strip())
        if m and 1 <= int(m.group(1)) <= 12:
            self.months[name] = {int(m.group(1))}
            return True
        return False

    def add_state_csv(self, path: Path, names: Iterable[str] | None = None) -> None:
        """Load a ``DATETIME,NAME,TIMESLICE`` state-change file."""
        wanted = set(names) if names is not None else None
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for rec in csv.DictReader(fh):
                name = rec["NAME"].strip()
                if wanted is not None and name not in wanted:
                    continue
                day = datetime.strptime(rec["DATETIME"].strip()[:10], "%d/%m/%Y")
                on = float(rec["TIMESLICE"]) != 0.0
                self.switches.setdefault(name, []).append((day.date(), on))
        for lst in self.switches.values():
            lst.sort()

    def is_active(self, name: str, day: date) -> bool:
        """Whether timeslice ``name`` is active on ``day``."""
        if name in self.months:
            return day.month in self.months[name]
        lst = self.switches.get(name)
        if not lst:
            return False
        state = False
        for when, on in lst:
            if when > day:
                break
            state = on
        return state

    def known(self, name: str) -> bool:
        return name in self.months or name in self.switches


__all__ = [
    "TimesliceCalendar",
    "TraceLocator",
    "parse_month_pattern",
    "read_interval_trace",
    "read_yearly_column",
]
