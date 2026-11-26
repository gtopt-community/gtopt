# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Typical meteorological years (8760 h) of critical-span weather for DLR.

Long-term studies (SDDP, 20 years, monthly stages with a few blocks) have
no weather forecast.  gtopt computes each critical span's rating itself
(IEEE 738 in ``Line.dlr``) from per-span weather, so the product here is
**weather**: an hourly typical year (8760 h) of the four fields of every
critical span, built from a multi-year history, in two cases:

``mean``
    The expected case.  Default method ``tmy``: the Sandia / NREL TMY
    month selection (Finkelstein-Schafer statistic, then a closeness
    re-rank) applied to the **line rating** (min over critical spans, the
    same IEEE 738 as gtopt) plus optional weather indices.  Twelve real
    historical months are concatenated, keeping diurnal cycles and
    multi-day persistence; month boundaries are blended over
    ``blend_hours`` on each side (as TMY3).
``p90`` (criticality)
    The rating **exceeded 90 % of the time**, i.e. the **10th percentile
    of the line rating** (conservative).  Default method ``analogue``:
    for each month × hour-of-day the 10th percentile of the historical
    line rating is the target, and the output hour carries the weather
    of a historical hour (same month and hour of day) whose line rating
    is closest to it.  Method ``month``: the historical month whose
    monthly-mean line rating sits at the 10th percentile of the monthly
    means (a real, temporally coherent month, but only P90 on average).
``quantile:<q>`` / ``pNN``
    Generic: the q-quantile of the rating (``pNN`` = exceeded NN % of the
    time = quantile ``1 − NN/100``).

Every output hour takes the weather of **all spans from the same
historical hour** (or, at a blended month boundary, the same two hours
with the same weights), so spatial and cross-variable consistency is
kept.  Statistics are always taken on the per-hour line rating, i.e.
*after* the min over spans, never per span or per weather variable.

The typical year can then be mapped to a case's blocks (``to_blocks``)
and tiled over a long horizon (``repeat``).

References: Finkelstein & Schafer, "Improved goodness-of-fit tests",
Biometrika 58(3):641-645, 1971; Hall et al., "Generation of a typical
meteorological year", SAND78-1096C, 1978; Wilcox & Marion, "Users manual
for TMY3 data sets", NREL/TP-581-43156, 2008.
"""

from __future__ import annotations

import calendar
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt._tz import TimeBasis
from dlr2gtopt._emit import RatingParams, span_rating_mw, span_uids
from dlr2gtopt._spans import Span
from dlr2gtopt._weather import FIELDS, Weather, dir_from_uv, uv_from_dir

logger = logging.getLogger("dlr2gtopt")

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

HOURS_PER_YEAR = 8760
MEAN_METHODS = ("tmy", "analogue")
QUANTILE_METHODS = ("analogue", "month")
SELECTIONS = ("nearest", "rotate", "knn-mean")
ALIGN_MODES = ("exact", "conditional")
FS_INDICES = (
    "rating_mean",
    "rating_min",
    "rating_max",
    "wind_speed",
    "temperature",
    "ghi",
)
DEFAULT_FS_WEIGHTS = {
    "rating_mean": 0.4,
    "rating_min": 0.4,
    "wind_speed": 0.1,
    "temperature": 0.1,
}
DEFAULT_CASES = ("p90", "mean")
DURATION_PCTS = (1, 5, 10, 25, 50, 75, 90, 95, 99)
WRITE_MODES = ("weather", "rating", "both")
_REF_YEAR = 2001  # any non-leap year: the 8760 h calendar


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """A rating statistic: the mean, or the ``q``-quantile of the rating."""

    label: str
    q: float | None = None

    @property
    def is_mean(self) -> bool:
        """True for the mean case."""
        return self.q is None

    def stat(self, values: FloatArray) -> float:
        """The statistic of ``values`` (NaN for an empty array)."""
        v = np.asarray(values, dtype=float)
        if v.size == 0:
            return math.nan
        if self.q is None:
            return float(v.mean())
        return float(np.quantile(v, self.q))

    def describe(self) -> str:
        """Human text, with P-exceedance and quantile."""
        if self.q is None:
            return "mean rating"
        return (
            f"rating exceeded {100 * (1 - self.q):g} % of the time "
            f"(quantile {self.q:g})"
        )


def parse_case(text: str) -> Case:
    """``mean`` | ``pNN`` (exceeded NN % of the time: ``p90`` = quantile 0.10)
    | ``quantile:<q>`` | ``min``."""
    t = text.strip().lower()
    if t == "mean":
        return Case("mean")
    if t == "min":
        return Case("min", 0.0)
    if t.startswith(("quantile:", "q")) and t != "q":
        q = float(t.split(":", 1)[1] if ":" in t else t[1:])
        if not 0.0 <= q <= 1.0:
            raise ValueError(f"case {text!r}: quantile must be in [0, 1]")
        return Case(f"q{q:g}", q)
    m = re.fullmatch(r"p(\d+(?:\.\d+)?)", t)
    if m:
        e = float(m.group(1))
        if not 0.0 < e < 100.0:
            raise ValueError(f"case {text!r}: exceedance must be in (0, 100)")
        return Case(t, round(1.0 - e / 100.0, 10))
    raise ValueError(f"case {text!r}: use mean, pNN (e.g. p90), quantile:<q> or min")


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calendar:
    """Local (standard-time) calendar of a sequence of hours."""

    year: IntArray
    month: IntArray
    day: IntArray
    hour: IntArray  # 0..23

    @property
    def size(self) -> int:
        """Number of hours."""
        return int(self.year.size)


def local_calendar(
    times: Sequence[datetime], utc_offset: float | str = 0.0
) -> Calendar:
    """Calendar of UTC ``times`` shifted by ``utc_offset`` hours (e.g. −4 for
    Chilean standard time: PLP block maps are in local standard time), or
    in the civil time of an IANA zone when ``utc_offset`` is its name
    (daylight saving followed; see :mod:`dlr2gtopt._tz`)."""
    if isinstance(utc_offset, str):
        loc = TimeBasis(0.0, utc_offset).local(times)
    else:
        shift = timedelta(hours=utc_offset)
        loc = [t + shift for t in times]
    return Calendar(
        np.array([t.year for t in loc], dtype=np.int64),
        np.array([t.month for t in loc], dtype=np.int64),
        np.array([t.day for t in loc], dtype=np.int64),
        np.array([t.hour for t in loc], dtype=np.int64),
    )


def typical_calendar() -> Calendar:
    """The 8760 h calendar of a non-leap year (no 29 February)."""
    t0 = datetime(_REF_YEAR, 1, 1)
    times = [t0 + timedelta(hours=h) for h in range(HOURS_PER_YEAR)]
    return local_calendar(times)


_CUM_DAYS = np.cumsum(
    [0] + [calendar.monthrange(_REF_YEAR, m)[1] for m in range(1, 13)]
)


def hour_of_year(month: Any, day: Any, hour: Any) -> IntArray:
    """0-based hour of the 8760 h year; 29 February maps to 28 February."""
    m = np.asarray(month, dtype=np.int64)
    d = np.asarray(day, dtype=np.int64)
    h = np.asarray(hour, dtype=np.int64)
    d = np.where((m == 2) & (d == 29), 28, d)
    return (_CUM_DAYS[m - 1] + d - 1) * 24 + h


# ---------------------------------------------------------------------------
# history ratings
# ---------------------------------------------------------------------------


@dataclass
class History:
    """A weather history with its IEEE 738 ratings and local calendar."""

    weather: Weather
    params: RatingParams
    cal: Calendar
    span_mw: FloatArray  # (hours, spans)
    line_mw: FloatArray  # (hours,) min over spans
    valid: npt.NDArray[np.bool_]

    @classmethod
    def build(
        cls,
        weather: Weather,
        params: RatingParams,
        utc_offset: float | str = 0.0,
        years: Sequence[int] | None = None,
    ) -> History:
        """Ratings of every hour (optionally only some local years)."""
        cal = local_calendar(weather.times, utc_offset)
        span = span_rating_mw(weather, params)
        line = np.min(span, axis=1) if span.shape[1] else np.full(len(span), np.nan)
        valid = np.isfinite(line)
        if years is not None:
            valid &= np.isin(cal.year, np.asarray(list(years), dtype=np.int64))
        return cls(weather, params, cal, span, line, valid)

    def month_hours(self, year: int, month: int) -> IntArray | None:
        """History indices of one complete local month (29 Feb dropped), or
        None when the month is incomplete or has invalid hours."""
        c = self.cal
        m = (c.year == year) & (c.month == month) & ~((c.month == 2) & (c.day == 29))
        idx: IntArray | None = np.flatnonzero(m).astype(np.int64)
        need = int((_CUM_DAYS[month] - _CUM_DAYS[month - 1]) * 24)
        if (
            idx is not None
            and idx.size != need
            and 0 < idx.size
            and abs(idx.size - need) <= 2
        ):
            idx = self._civil_month(idx, need)
        if idx is None or idx.size != need or not self.valid[idx].all():
            return None
        return idx

    def _civil_month(self, idx: IntArray, need: int) -> IntArray | None:
        """A civil-time month (``--dst on``) has a repeated hour when
        daylight saving ends and a missing one when it starts: keep the
        first of a repeated hour and fill a missing hour with the previous
        hour, so that the month has exactly ``need`` hours."""
        c = self.cal
        slot = (c.day[idx] - 1) * 24 + c.hour[idx]
        out = np.full(need, -1, dtype=np.int64)
        for i, k in zip(idx, slot):
            if k >= need:
                continue
            if out[k] < 0:
                out[k] = i
        for k in range(need):
            if out[k] < 0:
                if k == 0:
                    return None
                out[k] = out[k - 1]
        return out

    def complete_years(self, month: int) -> list[int]:
        """Years whose ``month`` is complete and valid."""
        ys = np.unique(self.cal.year[self.valid & (self.cal.month == month)])
        return [int(y) for y in ys if self.month_hours(int(y), month) is not None]


def month_hour_stats(
    h: History, quantiles: Sequence[float] = (0.05, 0.10, 0.50)
) -> list[dict[str, float | int]]:
    """Month × hour-of-day statistics of the LINE rating (min over spans
    first, then the statistic): n, min, quantiles, mean, max."""
    out: list[dict[str, float | int]] = []
    for m in range(1, 13):
        for hr in range(24):
            sel = h.valid & (h.cal.month == m) & (h.cal.hour == hr)
            v = h.line_mw[sel]
            row: dict[str, float | int] = {"month": m, "hour": hr, "n": int(v.size)}
            if v.size:
                row["min"] = float(v.min())
                for q in quantiles:
                    row[f"p{round(100 * q):02d}"] = float(np.quantile(v, q))
                row["mean"] = float(v.mean())
                row["max"] = float(v.max())
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# TMY month selection (Finkelstein-Schafer)
# ---------------------------------------------------------------------------


def fs_statistic(candidate: FloatArray, long_term: FloatArray) -> float:
    """Finkelstein-Schafer statistic: mean |CDF_candidate − CDF_long-term|
    evaluated at the candidate's values."""
    c = np.sort(np.asarray(candidate, dtype=float))
    lt = np.sort(np.asarray(long_term, dtype=float))
    if c.size == 0 or lt.size == 0:
        return math.nan
    f_c = np.searchsorted(c, c, side="right") / c.size
    f_lt = np.searchsorted(lt, c, side="right") / lt.size
    return float(np.mean(np.abs(f_c - f_lt)))


def daily_indices(h: History, idx: IntArray) -> dict[str, FloatArray]:
    """Daily indices of the hours ``idx`` (whole days, in order)."""
    n_d = idx.size // 24
    line = h.line_mw[idx].reshape(n_d, 24)
    d = h.weather.data
    out = {
        "rating_mean": line.mean(axis=1),
        "rating_min": line.min(axis=1),
        "rating_max": line.max(axis=1),
        "wind_speed": d["wind_speed"][idx].mean(axis=1).reshape(n_d, 24).mean(axis=1),
        "temperature": d["ambient_temperature"][idx]
        .mean(axis=1)
        .reshape(n_d, 24)
        .mean(axis=1),
    }
    ghi = d.get("solar_irradiance")
    out["ghi"] = (
        np.zeros(n_d)
        if ghi is None
        else ghi[idx].mean(axis=1).reshape(n_d, 24).mean(axis=1)
    )
    return out


def parse_weights(text: str | None) -> dict[str, float]:
    """``rating_mean=0.5,wind_speed=0.5`` -> normalised weights."""
    if not text:
        return dict(DEFAULT_FS_WEIGHTS)
    out: dict[str, float] = {}
    for part in text.split(","):
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in FS_INDICES:
            raise ValueError(f"FS weight {k!r}: use one of {FS_INDICES}")
        out[k] = float(v)
    tot = sum(out.values())
    if tot <= 0:
        raise ValueError("FS weights must sum to > 0")
    return {k: v / tot for k, v in out.items()}


@dataclass
class MonthPick:
    """The historical year chosen for one calendar month, with the scores."""

    month: int
    year: int
    scores: dict[int, float] = field(default_factory=dict)
    note: str = ""


def select_tmy_months(
    h: History,
    weights: Mapping[str, float] | None = None,
    candidates: int = 5,
) -> list[MonthPick]:
    """Per calendar month, the TMY month: lowest weighted FS statistic over
    the daily indices; among the ``candidates`` best, the one whose mean
    daily line rating is closest to the long-term mean (``candidates = 1``
    = pure FS)."""
    w = dict(DEFAULT_FS_WEIGHTS if weights is None else weights)
    picks: list[MonthPick] = []
    for m in range(1, 13):
        years = h.complete_years(m)
        if not years:
            raise ValueError(f"month {m}: no complete historical month")
        per_year = {}
        for y in years:
            idx = h.month_hours(y, m)
            assert idx is not None
            per_year[y] = daily_indices(h, idx)
        lt = {k: np.concatenate([v[k] for v in per_year.values()]) for k in w}
        ws = {
            y: sum(wk * fs_statistic(per_year[y][k], lt[k]) for k, wk in w.items())
            for y in years
        }
        ranked = sorted((ws[y], y) for y in years)
        top = [y for _, y in ranked[: max(1, candidates)]]
        lt_mean = float(
            np.concatenate([v["rating_mean"] for v in per_year.values()]).mean()
        )
        best = min(
            (abs(float(per_year[y]["rating_mean"].mean()) - lt_mean), y) for y in top
        )[1]
        picks.append(MonthPick(m, best, {y: round(ws[y], 5) for y in years}, "fs"))
    return picks


def select_quantile_months(h: History, case: Case) -> list[MonthPick]:
    """Per calendar month, the historical month whose monthly-mean line
    rating is closest to the case statistic of the monthly means."""
    picks: list[MonthPick] = []
    for m in range(1, 13):
        years = h.complete_years(m)
        if not years:
            raise ValueError(f"month {m}: no complete historical month")
        means = {}
        for y in years:
            idx = h.month_hours(y, m)
            assert idx is not None
            means[y] = float(h.line_mw[idx].mean())
        target = case.stat(np.array(list(means.values())))
        best = min((abs(means[y] - target), y) for y in years)[1]
        picks.append(
            MonthPick(m, best, {y: round(v, 3) for y, v in means.items()}, "month")
        )
    return picks


# ---------------------------------------------------------------------------
# typical year
# ---------------------------------------------------------------------------


@dataclass
class TypicalYear:
    """An 8760 h typical year of per-span weather."""

    case: Case
    method: str
    spans: list[Span]
    data: dict[str, FloatArray]  # field -> (8760, spans)
    source: IntArray  # primary historical index per hour
    source_times: list[datetime]  # primary historical UTC time per hour
    blend: FloatArray  # weight of the secondary source (0 = none)
    target: FloatArray  # per-hour target rating (NaN if not analogue)
    meta: dict[str, Any] = field(default_factory=dict)
    span_mw: FloatArray | None = None
    line_mw: FloatArray | None = None
    # secondary historical UTC time of a blended hour (None = not blended)
    source_times_2: list[datetime | None] | None = None
    # knn-mean: every historical UTC hour averaged into the hour
    members: list[list[datetime]] | None = None

    def weather(self) -> Weather:
        """As a :class:`Weather` on the reference (non-leap) year."""
        t0 = datetime(_REF_YEAR, 1, 1)
        times = [t0 + timedelta(hours=k) for k in range(HOURS_PER_YEAR)]
        return Weather(times, list(self.spans), dict(self.data))

    def rate(self, params: RatingParams) -> None:
        """Recompute the IEEE 738 ratings of the emitted weather."""
        self.span_mw = span_rating_mw(self.weather(), params)
        self.line_mw = np.min(self.span_mw, axis=1)


def _mix(
    data: Mapping[str, FloatArray],
    a: IntArray,
    b: IntArray,
    w: FloatArray,
) -> dict[str, FloatArray]:
    """(1 − w)·row a + w·row b per field; direction through u/v."""
    ww = w[:, None]
    out = {}
    for k, v in data.items():
        if k != "wind_direction":
            out[k] = (1 - ww) * v[a] + ww * v[b]
    spd = data["wind_speed"]
    d = data["wind_direction"]
    ua, va = uv_from_dir(spd[a], d[a])
    ub, vb = uv_from_dir(spd[b], d[b])
    out["wind_direction"] = dir_from_uv(
        (1 - ww) * ua + ww * ub, (1 - ww) * va + ww * vb
    )
    return out


def assemble_months(
    h: History, picks: Sequence[MonthPick], blend_hours: int = 6
) -> tuple[dict[str, FloatArray], IntArray, FloatArray, IntArray]:
    """Concatenate the picked months (8760 h) and blend each interior month
    boundary over ``blend_hours`` on each side (0 = off).  Returns the
    weather, the primary source index, the secondary weight and the
    secondary source index (−1 where not blended)."""
    parts = []
    for pick in sorted(picks, key=lambda q: q.month):
        idx = h.month_hours(pick.year, pick.month)
        if idx is None:
            raise ValueError(f"month {pick.month} of {pick.year} is incomplete")
        parts.append(idx)
    src = np.concatenate(parts)
    if src.size != HOURS_PER_YEAR:
        raise ValueError(f"typical year has {src.size} hours, not 8760")
    data = {k: v[src].copy() for k, v in h.weather.data.items()}
    blend = np.zeros(HOURS_PER_YEAR)
    primary = src.copy()
    secondary = np.full(HOURS_PER_YEAR, -1, dtype=np.int64)
    n_hist = len(h.weather.times)
    times = h.weather.times
    for m in range(1, 12):
        p = int(_CUM_DAYS[m] * 24)  # first hour of month m+1 (0-based m)
        # the same (local) year continues: real consecutive months, no seam
        same_year = int(h.cal.year[src[p - 1]]) == int(h.cal.year[src[p]])
        if blend_hours <= 0 or same_year:
            continue  # off, or the same year continues: nothing to blend
        ks = np.arange(-blend_hours, blend_hours)
        a = src[p - 1] + ks + 1
        b = src[p] + ks
        ok = (a >= 0) & (a < n_hist) & (b >= 0) & (b < n_hist)
        if not ok.all():
            continue
        exp_a = [times[src[p - 1]] + timedelta(hours=int(k + 1)) for k in ks]
        exp_b = [times[src[p]] + timedelta(hours=int(k)) for k in ks]
        if [times[i] for i in a] != exp_a or [times[i] for i in b] != exp_b:
            continue  # history not contiguous there
        if not (h.valid[a].all() and h.valid[b].all()):
            continue
        w = (ks + blend_hours + 0.5) / (2 * blend_hours)
        mixed = _mix(h.weather.data, a, b, w)
        pos = p + ks
        for k in data:
            data[k][pos] = mixed[k]
        primary[pos] = np.where(w < 0.5, a, b)
        secondary[pos] = np.where(w < 0.5, b, a)
        blend[pos] = np.minimum(w, 1 - w)
    return data, primary, blend, secondary


def _analogue_weather(
    h: History,
    pool: IntArray,
    target: float,
    selection: str,
    k: int,
    n_days: int,
    tolerance: float,
) -> tuple[list[dict[str, FloatArray]], list[int], int, list[int]]:
    """Weather rows for the ``n_days`` days of one month × hour bin, their
    sources, the knn fallback count and the hours averaged (knn-mean)."""
    dist = np.abs(h.line_mw[pool] - target)
    order = pool[np.lexsort((pool, dist))]
    if selection == "nearest":
        row = {f: v[order[0]] for f, v in h.weather.data.items()}
        return [row] * n_days, [int(order[0])] * n_days, 0, [int(order[0])]
    if selection == "rotate":
        kk = max(1, min(k, order.size))
        rows = []
        srcs = []
        for d in range(n_days):
            i = int(order[d % kk])
            rows.append({f: v[i] for f, v in h.weather.data.items()})
            srcs.append(i)
        return rows, srcs, 0, []
    # knn-mean: average of the K nearest, accepted only within tolerance
    kk = max(1, min(k, order.size))
    near = order[:kk]
    w = np.full(kk, 1.0 / kk)
    avg: dict[str, FloatArray] = {}
    for f, v in h.weather.data.items():
        if f != "wind_direction":
            avg[f] = (w[:, None] * v[near]).sum(axis=0)
    u, vv = uv_from_dir(
        h.weather.data["wind_speed"][near], h.weather.data["wind_direction"][near]
    )
    avg["wind_direction"] = dir_from_uv(
        (w[:, None] * u).sum(axis=0), (w[:, None] * vv).sum(axis=0)
    )
    one = Weather(
        [datetime(_REF_YEAR, 1, 1)],
        h.weather.spans,
        {f: a[None, :] for f, a in avg.items()},
    )
    r = float(np.min(span_rating_mw(one, h.params)))
    if abs(r - target) <= tolerance * max(abs(target), 1e-9):
        return [avg] * n_days, [int(order[0])] * n_days, 0, [int(i) for i in near]
    row = {f: v[order[0]] for f, v in h.weather.data.items()}
    return [row] * n_days, [int(order[0])] * n_days, 1, [int(order[0])]


def analogue_year(
    h: History,
    case: Case,
    *,
    selection: str = "nearest",
    analogues: int = 5,
    tolerance: float = 0.02,
) -> TypicalYear:
    """Per month × hour of day: target = case statistic of the historical
    line ratings of that bin; each day's hour takes the weather (all spans)
    of an analogue hour of the same bin (``selection``)."""
    if selection not in SELECTIONS:
        raise ValueError(f"selection must be one of {SELECTIONS}")
    n_s = len(h.weather.spans)
    data = {f: np.zeros((HOURS_PER_YEAR, n_s)) for f in h.weather.data}
    source = np.zeros(HOURS_PER_YEAR, dtype=np.int64)
    target = np.zeros(HOURS_PER_YEAR)
    members: list[list[datetime]] = [[] for _ in range(HOURS_PER_YEAR)]
    fallbacks = 0
    for m in range(1, 13):
        n_days = int(_CUM_DAYS[m] - _CUM_DAYS[m - 1])
        for hr in range(24):
            pool = np.flatnonzero(h.valid & (h.cal.month == m) & (h.cal.hour == hr))
            if pool.size == 0:
                raise ValueError(f"month {m} hour {hr}: no historical hours")
            tgt = case.stat(h.line_mw[pool])
            rows, srcs, fb, near = _analogue_weather(
                h, pool, tgt, selection, analogues, n_days, tolerance
            )
            fallbacks += fb
            pos = hour_of_year(np.full(n_days, m), np.arange(1, n_days + 1), hr)
            for d, p in enumerate(pos):
                for f in data:
                    data[f][p] = rows[d][f]
            source[pos] = srcs
            target[pos] = tgt
            for p in pos:
                members[p] = [h.weather.times[i] for i in near]
    return TypicalYear(
        case,
        "analogue",
        list(h.weather.spans),
        data,
        source,
        [h.weather.times[i] for i in source],
        np.zeros(HOURS_PER_YEAR),
        target,
        {
            "selection": selection,
            "analogues": analogues,
            "tolerance": tolerance,
            "knn_fallbacks": fallbacks,
        },
        members=members if selection == "knn-mean" else None,
    )


def month_year(
    h: History,
    case: Case,
    picks: Sequence[MonthPick],
    method: str,
    blend_hours: int = 6,
) -> TypicalYear:
    """Typical year from one picked historical month per calendar month."""
    data, primary, blend, secondary = assemble_months(h, picks, blend_hours)
    times = h.weather.times
    return TypicalYear(
        case,
        method,
        list(h.weather.spans),
        data,
        primary,
        [h.weather.times[i] for i in primary],
        blend,
        np.full(HOURS_PER_YEAR, np.nan),
        {
            "months": {p.month: p.year for p in picks},
            "scores": {p.month: p.scores for p in picks},
            "blend_hours": blend_hours,
        },
        source_times_2=[times[i] if i >= 0 else None for i in secondary],
    )


@dataclass(frozen=True)
class TypicalOptions:
    """Options of :func:`build_typical` (one field per CLI option)."""

    mean_method: str = "tmy"
    quantile_method: str = "analogue"
    selection: str = "nearest"
    analogues: int = 5
    tolerance: float = 0.02
    fs_weights: Mapping[str, float] | None = None
    tmy_candidates: int = 5
    blend_hours: int = 6
    source_times: Sequence[datetime] | None = None
    align: str = "conditional"
    condition_k: int = 60


def aligned_year(
    h: History,
    case: Case,
    source_times: Sequence[datetime],
    *,
    align: str = "conditional",
    condition_k: int = 60,
) -> TypicalYear:
    """Typical year aligned with ANOTHER typical year's source hours (e.g.
    the renewable-generation P50 year), so both come from the same
    historical weather situation.

    ``exact``: every hour takes the weather of the given source hour (the
    DLR year is then the same weather as the other year; its rating
    statistic is whatever that weather gives).  ``conditional``: among the
    historical hours of the same month and hour of day, the
    ``condition_k`` hours whose span-mean wind speed is closest to the
    source hour's form the pool; the target is the case statistic of their
    line rating (e.g. the P90 GIVEN that wind situation) and the hour
    takes the weather of the pool hour closest to the target.  The P90
    conservatism is kept while the wind-cooling correlation with the
    other year (windy hours: high wind output AND high rating) survives."""
    if align not in ALIGN_MODES:
        raise ValueError(f"align must be one of {ALIGN_MODES}")
    if len(source_times) != HOURS_PER_YEAR:
        raise ValueError(f"source hours: {len(source_times)} given, 8760 needed")
    idx = {t: i for i, t in enumerate(h.weather.times)}
    missing = [t for t in source_times if t not in idx]
    if missing:
        raise ValueError(
            f"{len(missing)} source hours are not in the history (first {missing[0]})"
        )
    src = np.array([idx[t] for t in source_times], dtype=np.int64)
    target = np.full(HOURS_PER_YEAR, np.nan)
    if align == "conditional":
        wind = h.weather.data["wind_speed"].mean(axis=1)
        tc = typical_calendar()
        out = src.copy()
        for m in range(1, 13):
            for hr in range(24):
                pool = np.flatnonzero(h.valid & (h.cal.month == m) & (h.cal.hour == hr))
                if pool.size == 0:
                    raise ValueError(f"month {m} hour {hr}: no historical hours")
                for pos in np.flatnonzero((tc.month == m) & (tc.hour == hr)):
                    dw = np.abs(wind[pool] - wind[src[pos]])
                    near = pool[np.lexsort((pool, dw))][: max(1, condition_k)]
                    tgt = case.stat(h.line_mw[near])
                    r = h.line_mw[near]
                    out[pos] = int(near[np.lexsort((near, np.abs(r - tgt)))][0])
                    target[pos] = tgt
        src = out
    return TypicalYear(
        case,
        f"aligned-{align}",
        list(h.weather.spans),
        {f: v[src] for f, v in h.weather.data.items()},
        src,
        [h.weather.times[i] for i in src],
        np.zeros(HOURS_PER_YEAR),
        target,
        {"align": align, "condition_k": condition_k},
    )


def build_typical(h: History, case: Case, opt: TypicalOptions) -> TypicalYear:
    """One typical year for ``case``, rated with the history's parameters."""
    if opt.mean_method not in MEAN_METHODS:
        raise ValueError(f"mean method must be one of {MEAN_METHODS}")
    if opt.quantile_method not in QUANTILE_METHODS:
        raise ValueError(f"quantile method must be one of {QUANTILE_METHODS}")
    if opt.source_times is not None:
        ty = aligned_year(
            h,
            case,
            opt.source_times,
            align=opt.align,
            condition_k=opt.condition_k,
        )
    elif case.is_mean and opt.mean_method == "tmy":
        picks = select_tmy_months(h, opt.fs_weights, opt.tmy_candidates)
        ty = month_year(h, case, picks, "tmy", opt.blend_hours)
    elif not case.is_mean and opt.quantile_method == "month":
        ty = month_year(
            h, case, select_quantile_months(h, case), "month", opt.blend_hours
        )
    else:
        ty = analogue_year(
            h,
            case,
            selection=opt.selection,
            analogues=opt.analogues,
            tolerance=opt.tolerance,
        )
    ty.rate(h.params)
    return ty


# ---------------------------------------------------------------------------
# verification and validation
# ---------------------------------------------------------------------------


def _bin_values(
    h: History, years: Sequence[int] | None = None
) -> dict[tuple[int, int], FloatArray]:
    sel = h.valid.copy()
    if years is not None:
        sel &= np.isin(h.cal.year, np.asarray(list(years), dtype=np.int64))
    out: dict[tuple[int, int], FloatArray] = {}
    for m in range(1, 13):
        for hr in range(24):
            out[(m, hr)] = np.sort(
                h.line_mw[sel & (h.cal.month == m) & (h.cal.hour == hr)]
            )
    return out


def verify(ty: TypicalYear, h: History) -> dict[str, Any]:
    """Typical-year ratings (recomputed from the emitted weather) against
    the historical distribution: per month mean, p10 and the exceedance
    (share of historical hours of the same month × hour whose rating is ≥
    the typical one; 0.90 is the P90 target), the duration curve, and the
    analogue target error."""
    assert ty.line_mw is not None
    tc = typical_calendar()
    bins = _bin_values(h)
    exceed = np.empty(HOURS_PER_YEAR)
    for (m, hr), v in bins.items():
        pos = np.flatnonzero((tc.month == m) & (tc.hour == hr))
        r = ty.line_mw[pos]
        exceed[pos] = 1.0 - np.searchsorted(v, r, side="left") / max(v.size, 1)
    months = []
    for m in range(1, 13):
        sel = tc.month == m
        hv = h.line_mw[h.valid & (h.cal.month == m)]
        months.append(
            {
                "month": m,
                "typical_mean": float(ty.line_mw[sel].mean()),
                "history_mean": float(hv.mean()),
                "typical_p10": float(np.quantile(ty.line_mw[sel], 0.10)),
                "history_p10": float(np.quantile(hv, 0.10)),
                "exceedance": float(exceed[sel].mean()),
            }
        )
    hist = h.line_mw[h.valid]
    out: dict[str, Any] = {
        "case": ty.case.label,
        "definition": ty.case.describe(),
        "method": ty.method,
        "meta": _jsonable(ty.meta),
        "typical_mean_mw": float(ty.line_mw.mean()),
        "history_mean_mw": float(hist.mean()),
        "exceedance": float(exceed.mean()),
        "expected_exceedance": None if ty.case.q is None else 1.0 - ty.case.q,
        "months": months,
        "duration_curve": {
            f"p{p:02d}": {
                "typical": float(np.percentile(ty.line_mw, p)),
                "history": float(np.percentile(hist, p)),
            }
            for p in DURATION_PCTS
        },
    }
    fin = np.isfinite(ty.target)
    if fin.any():
        err = ty.line_mw[fin] - ty.target[fin]
        out["target_error_mw"] = {
            "mean_abs": float(np.abs(err).mean()),
            "max_abs": float(np.abs(err).max()),
            "mean_rel": float((np.abs(err) / np.abs(ty.target[fin])).mean()),
        }
    return out


def holdout_errors(
    typical_line: FloatArray,
    h: History,
    test_years: Sequence[int],
    static_mw: float | None = None,
) -> dict[str, Any]:
    """Hour-by-hour error of a typical year's line rating against the actual
    hours of ``test_years`` (same month, day, hour; 29 Feb -> 28 Feb).
    Over-estimation (typical > actual) is the risk: the model would allow
    flow the conductor cannot carry."""
    sel = h.valid & np.isin(h.cal.year, np.asarray(list(test_years), dtype=np.int64))
    act = h.line_mw[sel]
    pos = hour_of_year(h.cal.month[sel], h.cal.day[sel], h.cal.hour[sel])
    typ = typical_line[pos]
    out: dict[str, Any] = {"test_hours": int(act.size), **_errors(typ, act)}
    if static_mw is not None:
        out["static"] = _errors(np.full_like(act, static_mw), act)
    return out


def _errors(typ: FloatArray, act: FloatArray) -> dict[str, float]:
    d = typ - act
    return {
        "p_over": float(np.mean(d > 0)),
        "p_over_5pct": float(np.mean(d > 0.05 * act)),
        "mean_over_mw": float(np.mean(np.maximum(d, 0))),
        "mean_under_mw": float(np.mean(np.maximum(-d, 0))),
        "bias_mw": float(np.mean(d)),
        "mae_mw": float(np.mean(np.abs(d))),
        "worst_over_mw": float(np.max(d)) if d.size else math.nan,
    }


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    return x


# ---------------------------------------------------------------------------
# typical-year file
# ---------------------------------------------------------------------------

TYPICAL_COLUMNS = (
    "hour_of_year",
    "month",
    "day",
    "hour",
    "line",
    "span",
    *FIELDS,
    "span_rating_mw",
    "line_rating_mw",
    "source_time",
    "blend",
    "target_mw",
)
# provenance columns written with ``sources`` (knn_sources: knn-mean only)
SOURCE_COLUMNS = ("source_time_2", "knn_sources")


def typical_frame(ty: TypicalYear, sources: bool = True) -> Any:
    """Long table, one row per (hour, span).  ``hour_of_year`` 1-8760,
    ``hour`` 1-24 (PLP convention: hour 1 = 00:00-01:00 local).

    ``sources`` adds the full provenance of every hour: ``source_time_2``
    (secondary UTC hour of a blended month seam, empty otherwise; its
    weight is ``blend``) and, for ``knn-mean``, ``knn_sources`` (every
    averaged UTC hour, ``;``-separated ISO times)."""
    import pandas as pd

    tc = typical_calendar()
    n_s = len(ty.spans)

    def rep(a: Any) -> Any:
        return np.repeat(np.asarray(a), n_s)

    cols: dict[str, Any] = {
        "hour_of_year": rep(np.arange(1, HOURS_PER_YEAR + 1)),
        "month": rep(tc.month),
        "day": rep(tc.day),
        "hour": rep(tc.hour + 1),
        "line": np.tile([s.line for s in ty.spans], HOURS_PER_YEAR),
        "span": np.tile([s.span for s in ty.spans], HOURS_PER_YEAR),
    }
    for f in FIELDS:
        if f in ty.data:
            cols[f] = ty.data[f].reshape(-1)
    assert ty.span_mw is not None and ty.line_mw is not None
    cols["span_rating_mw"] = ty.span_mw.reshape(-1)
    cols["line_rating_mw"] = rep(ty.line_mw)
    cols["source_time"] = rep(np.array(ty.source_times, dtype="datetime64[s]"))
    cols["blend"] = rep(ty.blend)
    cols["target_mw"] = rep(ty.target)
    if sources:
        t2 = ty.source_times_2 or [None] * HOURS_PER_YEAR
        cols["source_time_2"] = rep(pd.to_datetime(pd.Series(t2)).to_numpy())
        if ty.members is not None:
            cols["knn_sources"] = rep(
                np.array(
                    [";".join(t.isoformat() for t in m) for m in ty.members],
                    dtype=object,
                )
            )
    return pd.DataFrame(cols)


def write_frame(df: Any, path: Path) -> None:
    """Parquet or CSV by suffix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False, float_format="%.4f")


def read_source_times(path: Path) -> list[datetime]:
    """The 8760 source hours (UTC) of another typical year: a table with
    ``hour_of_year`` (1-8760) and ``source_time`` (one row per hour, or
    per hour and span as in a ``typical_<case>`` file)."""
    import pandas as pd

    df = read_frame(path)
    if not {"hour_of_year", "source_time"} <= set(df.columns):
        raise ValueError(f"{path}: needs hour_of_year and source_time columns")
    first = df.drop_duplicates("hour_of_year").sort_values("hour_of_year")
    return [t.to_pydatetime() for t in pd.to_datetime(first["source_time"])]


def read_frame(path: Path) -> Any:
    """Parquet or CSV by suffix."""
    import pandas as pd

    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


@dataclass
class TypicalTable:
    """A typical year read back: (8760, spans) arrays."""

    keys: list[tuple[str, int]]
    data: dict[str, FloatArray]
    span_mw: FloatArray
    line_mw: FloatArray
    month: IntArray
    day: IntArray
    hour: IntArray  # 1..24
    source_time: list[datetime] | None = None  # primary UTC source hour


def read_typical(path: Path) -> TypicalTable:
    """Read a typical-year file written by :func:`typical_frame`."""
    df = read_frame(path).sort_values(["hour_of_year", "line", "span"])
    keys = list(dict.fromkeys(zip(df["line"].astype(str), df["span"].astype(int))))
    n_s = len(keys)
    if len(df) != HOURS_PER_YEAR * n_s:
        raise ValueError(f"{path}: expected {HOURS_PER_YEAR} hours x {n_s} spans")

    def mat(col: str) -> FloatArray:
        return df[col].to_numpy(dtype=float).reshape(HOURS_PER_YEAR, n_s)

    first = df.iloc[::n_s]
    src_t = None
    if "source_time" in df.columns:
        import pandas as pd

        src_t = [t.to_pydatetime() for t in pd.to_datetime(first["source_time"])]
    return TypicalTable(
        keys,
        {f: mat(f) for f in FIELDS if f in df.columns},
        mat("span_rating_mw"),
        first["line_rating_mw"].to_numpy(dtype=float),
        first["month"].to_numpy(dtype=np.int64),
        first["day"].to_numpy(dtype=np.int64),
        first["hour"].to_numpy(dtype=np.int64),
        src_t,
    )


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockSpec:
    """A block and the typical-year hours (0-based, with repeats) it holds."""

    stage: int
    block: int
    hours: IntArray
    month: int = 0  # template month (hour-band / chronological layouts)


def blocks_from_map(
    df: Any,
    stage_of_block: Mapping[int, int] | None = None,
    hour_base: int = 1,
) -> list[BlockSpec]:
    """Blocks from an hour→block membership table: columns ``month, day,
    hour, block`` [``stage``] (plp2gtopt's ``BlockHourMap/block_hour_map``
    from PLP ``indhor.csv``; ``hour`` 1-24 there, ``hour_base`` 1).  The
    stage comes from a ``stage`` column or ``stage_of_block`` (the case's
    ``stage_array``)."""
    need = {"month", "day", "hour", "block"}
    if not need <= set(df.columns):
        raise ValueError(f"block map needs columns {sorted(need)}")
    hr = df["hour"].to_numpy(dtype=np.int64) - hour_base
    if hr.min() < 0 or hr.max() > 23:
        raise ValueError("block map: hour out of range (check --map-hour-base)")
    hoy = hour_of_year(df["month"].to_numpy(), df["day"].to_numpy(), hr)
    blocks = df["block"].to_numpy(dtype=np.int64)
    if "stage" in df.columns:
        stages = df["stage"].to_numpy(dtype=np.int64)
    elif stage_of_block is not None:
        try:
            stages = np.array([stage_of_block[int(b)] for b in blocks], dtype=np.int64)
        except KeyError as exc:
            raise ValueError(f"block {exc} is not in the planning stage_array") from exc
    else:
        raise ValueError("block map has no stage column: pass the planning JSON")
    out = []
    order = np.lexsort((blocks, stages))
    s_sorted, b_sorted = stages[order], blocks[order]
    cut = np.flatnonzero((np.diff(s_sorted) != 0) | (np.diff(b_sorted) != 0)) + 1
    for grp in np.split(order, cut):
        out.append(BlockSpec(int(stages[grp[0]]), int(blocks[grp[0]]), hoy[grp]))
    return out


def stage_of_block_from_planning(planning: Mapping[str, Any]) -> dict[int, int]:
    """block uid -> stage uid from a gtopt planning JSON (``simulation``
    ``block_array`` + ``stage_array`` with ``first_block``/``count_block``)."""
    sim = planning.get("simulation", planning)
    blocks = sim.get("block_array", [])
    out: dict[int, int] = {}
    for k, st in enumerate(sim.get("stage_array", [])):
        first = int(st.get("first_block", 0))
        count = int(st.get("count_block", 0))
        uid = int(st.get("uid", k + 1))
        for b in blocks[first : first + count]:
            out[int(b["uid"])] = uid
    if not out:
        raise ValueError("planning JSON has no stage_array/block_array")
    return out


def parse_bands(text: str) -> list[tuple[int, int]]:
    """``1-7,8-18,19-24`` -> 1-based inclusive hour bands (must cover 1..24
    exactly once)."""
    bands = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        lo, hi = int(a), int(b or a)
        if not 1 <= lo <= hi <= 24:
            raise ValueError(f"hour band {part!r} outside 1-24")
        bands.append((lo, hi))
    cover = sorted(h for lo, hi in bands for h in range(lo, hi + 1))
    if cover != list(range(1, 25)):
        raise ValueError("hour bands must cover hours 1-24 exactly once")
    return bands


def stage_months(
    first_month: int = 1, stages: int = 12, first_stage: int = 1
) -> list[tuple[int, int]]:
    """Monthly stages: (stage uid, calendar month) from ``first_month``."""
    return [(first_stage + k, (first_month - 1 + k) % 12 + 1) for k in range(stages)]


def read_stage_months(path: Path) -> list[tuple[int, int]]:
    """CSV ``stage,month`` (one row per stage)."""
    df = read_frame(path)
    return [(int(s), int(m)) for s, m in zip(df["stage"], df["month"])]


def blocks_from_bands(
    stages: Sequence[tuple[int, int]],
    bands: Sequence[tuple[int, int]],
    first_block: int = 1,
) -> list[BlockSpec]:
    """Month × hour-band blocks (the fallback for load blocks whose hour
    membership is unknown): block k of a stage holds every typical hour of
    the stage's month whose hour (1-24) is in band k."""
    tc = typical_calendar()
    out = []
    uid = first_block
    for st, m in stages:
        for lo, hi in bands:
            sel = np.flatnonzero(
                (tc.month == m) & (tc.hour + 1 >= lo) & (tc.hour + 1 <= hi)
            )
            out.append(BlockSpec(st, uid, sel, m))
            uid += 1
    return out


def blocks_chronological(
    stages: Sequence[tuple[int, int]],
    block_hours: int = 1,
    first_block: int = 1,
) -> list[BlockSpec]:
    """Chronological blocks: each stage's month cut into consecutive blocks
    of ``block_hours`` typical hours (1 = hourly, direct)."""
    if block_hours < 1:
        raise ValueError("block_hours must be >= 1")
    tc = typical_calendar()
    out = []
    uid = first_block
    for st, m in stages:
        idx = np.flatnonzero(tc.month == m)
        for k in range(0, idx.size, block_hours):
            out.append(BlockSpec(st, uid, idx[k : k + block_hours], m))
            uid += 1
    return out


@dataclass
class BlockWeather:
    """Per-block weather of every span (rows = blocks)."""

    specs: list[BlockSpec]
    data: dict[str, FloatArray]  # field -> (blocks, spans)
    span_mw: FloatArray
    line_mw: FloatArray
    source_hour: IntArray  # 1-based typical hour (0 = knn mean)
    stat_mw: FloatArray
    # UTC historical hour of each block's weather (the nearest hour for a
    # knn-mean block); None when unknown (typical file without it)
    source_time: list[datetime | None] | None = None


def to_blocks(
    tt: TypicalTable,
    specs: Sequence[BlockSpec],
    case: Case,
    *,
    selection: str = "nearest",
    analogues: int = 5,
    tolerance: float = 0.02,
    spans: Sequence[Span] | None = None,
    params: RatingParams | None = None,
) -> BlockWeather:
    """Each block takes the weather (all spans) of its hour whose line
    rating is closest to the block statistic (``case`` over the block's
    hours).  ``knn-mean`` averages the K closest hours when the rating of
    the average (recomputed: needs ``spans`` and ``params``) is within
    ``tolerance``; ``rotate`` behaves as ``nearest`` (one value per block)."""
    if selection not in SELECTIONS:
        raise ValueError(f"selection must be one of {SELECTIONS}")
    n_b, n_s = len(specs), len(tt.keys)
    data = {f: np.zeros((n_b, n_s)) for f in tt.data}
    span_mw = np.zeros((n_b, n_s))
    line_mw = np.zeros(n_b)
    src = np.zeros(n_b, dtype=np.int64)
    stat = np.zeros(n_b)
    src_t: list[datetime | None] = [None] * n_b
    for i, sp in enumerate(specs):
        if sp.hours.size == 0:
            raise ValueError(f"stage {sp.stage} block {sp.block} has no hours")
        r = tt.line_mw[sp.hours]
        tgt = case.stat(r)
        stat[i] = tgt
        order = sp.hours[np.lexsort((sp.hours, np.abs(r - tgt)))]
        best = int(order[0])
        for f in data:
            data[f][i] = tt.data[f][best]
        span_mw[i] = tt.span_mw[best]
        line_mw[i] = tt.line_mw[best]
        src[i] = best + 1
        if tt.source_time is not None:
            src_t[i] = tt.source_time[best]
        if selection != "knn-mean":
            continue
        if spans is None or params is None:
            raise ValueError("knn-mean needs the spans and the conductor")
        near = order[: max(1, min(analogues, order.size))]
        one = {f: tt.data[f][near] for f in tt.data}
        avg = {f: v.mean(axis=0) for f, v in one.items() if f != "wind_direction"}
        u, v = uv_from_dir(one["wind_speed"], one["wind_direction"])
        avg["wind_direction"] = dir_from_uv(u.mean(axis=0), v.mean(axis=0))
        w = Weather(
            [datetime(_REF_YEAR, 1, 1)],
            list(spans),
            {f: a[None, :] for f, a in avg.items()},
        )
        smw = span_rating_mw(w, params)[0]
        if abs(float(smw.min()) - tgt) <= tolerance * max(abs(tgt), 1e-9):
            for f in data:
                data[f][i] = avg[f]
            span_mw[i] = smw
            line_mw[i] = float(smw.min())
            src[i] = 0
    return BlockWeather(
        list(specs),
        data,
        span_mw,
        line_mw,
        src,
        stat,
        src_t if tt.source_time is not None else None,
    )


def history_to_blocks(
    h: History,
    specs: Sequence[BlockSpec],
    case: Case,
) -> BlockWeather:
    """Blocks straight from the history: a block's pool is every valid
    historical hour whose (month, day, hour) falls in the block (29 Feb ->
    28 Feb), its statistic is the case over that pool (the exact P90 of the
    block's hours), and it takes the weather (all spans) of the historical
    hour whose line rating is closest to it."""
    hoy = hour_of_year(h.cal.month, h.cal.day, h.cal.hour)
    n_b, n_s = len(specs), len(h.weather.spans)
    data = {f: np.zeros((n_b, n_s)) for f in h.weather.data}
    span_mw = np.zeros((n_b, n_s))
    line_mw = np.zeros(n_b)
    src = np.zeros(n_b, dtype=np.int64)
    stat = np.zeros(n_b)
    src_t: list[datetime | None] = [None] * n_b
    for i, sp in enumerate(specs):
        member = np.zeros(HOURS_PER_YEAR, dtype=bool)
        member[sp.hours] = True
        pool = np.flatnonzero(h.valid & member[hoy])
        if pool.size == 0:
            raise ValueError(f"stage {sp.stage} block {sp.block} has no history")
        r = h.line_mw[pool]
        stat[i] = case.stat(r)
        best = int(pool[np.lexsort((pool, np.abs(r - stat[i])))][0])
        for f in data:
            data[f][i] = h.weather.data[f][best]
        span_mw[i] = h.span_mw[best]
        line_mw[i] = h.line_mw[best]
        src[i] = int(hoy[best]) + 1
        src_t[i] = h.weather.times[best]  # keeps the year
    return BlockWeather(list(specs), data, span_mw, line_mw, src, stat, src_t)


def block_frame(
    bw: BlockWeather, keys: Sequence[tuple[str, int]], sources: bool = True
) -> Any:
    """Long table, one row per (block, span).  ``sources`` adds
    ``source_time`` (UTC historical hour of the block's weather) and
    ``source_year`` when known (history mode always; typical mode when
    the typical file has ``source_time``)."""
    import pandas as pd

    n_s = len(keys)

    def rep(a: Any) -> Any:
        return np.repeat(np.asarray(a), n_s)

    cols: dict[str, Any] = {
        "stage": rep([s.stage for s in bw.specs]),
        "block": rep([s.block for s in bw.specs]),
        "month": rep([s.month for s in bw.specs]),
        "hours": rep([s.hours.size for s in bw.specs]),
        "line": np.tile([k[0] for k in keys], len(bw.specs)),
        "span": np.tile([k[1] for k in keys], len(bw.specs)),
    }
    for f, v in bw.data.items():
        cols[f] = v.reshape(-1)
    cols["span_rating_mw"] = bw.span_mw.reshape(-1)
    cols["line_rating_mw"] = rep(bw.line_mw)
    cols["stat_mw"] = rep(bw.stat_mw)
    cols["source_hour"] = rep(bw.source_hour)
    if sources and bw.source_time is not None:
        st = pd.to_datetime(pd.Series(bw.source_time))
        cols["source_time"] = rep(st.to_numpy())
        cols["source_year"] = rep(st.dt.year.fillna(0).astype(int).to_numpy())
    return pd.DataFrame(cols)


def read_block_frame(path: Path) -> tuple[BlockWeather, list[tuple[str, int]]]:
    """Read a block table written by :func:`block_frame`."""
    import pandas as pd

    df = read_frame(path)
    keys = list(dict.fromkeys(zip(df["line"].astype(str), df["span"].astype(int))))
    n_s = len(keys)
    first = df.iloc[::n_s]
    specs = [
        BlockSpec(int(s), int(b), np.zeros(int(n), dtype=np.int64), int(m))
        for s, b, m, n in zip(
            first["stage"], first["block"], first["month"], first["hours"]
        )
    ]

    def mat(col: str) -> FloatArray:
        return df[col].to_numpy(dtype=float).reshape(len(specs), n_s)

    return (
        BlockWeather(
            specs,
            {f: mat(f) for f in FIELDS if f in df.columns},
            mat("span_rating_mw"),
            first["line_rating_mw"].to_numpy(dtype=float),
            first["source_hour"].to_numpy(dtype=np.int64),
            first["stat_mw"].to_numpy(dtype=float),
            (
                [
                    None if pd.isna(t) else t.to_pydatetime()
                    for t in pd.to_datetime(first["source_time"])
                ]
                if "source_time" in df.columns
                else None
            ),
        ),
        keys,
    )


def repeat(
    template: BlockWeather,
    stages: Sequence[tuple[int, int]],
    first_block: int = 1,
) -> BlockWeather:
    """Tile a month template (blocks whose ``month`` is set) over a horizon
    of (stage uid, month) pairs; block uids are renumbered consecutively
    from ``first_block`` in horizon order."""
    by_month: dict[int, list[int]] = {}
    stage_of_month: dict[int, int] = {}
    for i, s in enumerate(template.specs):
        if stage_of_month.setdefault(s.month, s.stage) != s.stage:
            raise ValueError(
                f"template has several stages for month {s.month}: "
                "build it with one stage per month"
            )
        if s.month == 0:
            raise ValueError(
                "repeat needs a month template (hour bands or chronological)"
            )
        by_month.setdefault(s.month, []).append(i)
    rows: list[int] = []
    specs: list[BlockSpec] = []
    uid = first_block
    for st, m in stages:
        if m not in by_month:
            raise ValueError(f"template has no blocks for month {m}")
        for i in by_month[m]:
            t = template.specs[i]
            specs.append(BlockSpec(st, uid, t.hours, m))
            rows.append(i)
            uid += 1
    r = np.array(rows, dtype=np.int64)
    return BlockWeather(
        specs,
        {f: v[r] for f, v in template.data.items()},
        template.span_mw[r],
        template.line_mw[r],
        template.source_hour[r],
        template.stat_mw[r],
        (
            [template.source_time[i] for i in r]
            if template.source_time is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Line.dlr schedules
# ---------------------------------------------------------------------------


def write_schedules(
    out_dir: Path,
    bw: BlockWeather,
    spans: Sequence[Span],
    params: RatingParams,
    *,
    mode: str = "weather",
    line_uid: int = 1,
    uid_base: int | None = None,
    prefix: str = "dlr",
    fmt: str = "parquet",
    scenarios: Sequence[int] = (1,),
    tmax_profile: bool = False,
) -> dict[str, Any]:
    """Write ``out_dir/Line/<prefix>_<field>.<fmt>`` (long layout
    ``scenario,stage,block,uid,value``, uid = span uid) and
    ``out_dir/<prefix>_line<uid>.json`` (the ``dlr`` object).  ``rating``
    mode writes the per-span MW ``rating``; ``both`` writes both (the JSON
    then uses the weather).  ``tmax_profile`` also writes the per-line MW
    profile ``Line/<prefix>_tmax`` (``stage,block,uid,value``, uid = line
    uid) for ``Line.tmax_ab`` / ``tmax_ba`` in cases without DLR support."""
    import pandas as pd

    if mode not in WRITE_MODES:
        raise ValueError(f"mode must be one of {WRITE_MODES}")
    n_b, n_s = len(bw.specs), len(spans)
    uids = span_uids(line_uid, n_s, uid_base)
    st = np.array([s.stage for s in bw.specs])
    bl = np.array([s.block for s in bw.specs])

    def long(values: FloatArray, name: str) -> None:
        frames = [
            pd.DataFrame(
                {
                    "scenario": np.full(n_b * n_s, sc),
                    "stage": np.repeat(st, n_s),
                    "block": np.repeat(bl, n_s),
                    "uid": np.tile(uids, n_b),
                    "value": values.reshape(-1),
                }
            )
            for sc in scenarios
        ]
        write_frame(
            pd.concat(frames, ignore_index=True), out_dir / "Line" / f"{name}.{fmt}"
        )

    spans_json: list[dict[str, Any]] = []
    for k, s in enumerate(spans):
        js: dict[str, Any] = {"uid": uids[k], "name": f"{s.line}_span{s.span}"}
        if mode != "rating":
            js["azimuth"] = round(s.azimuth, 2)
            js["elevation"] = round(s.elevation or 0.0, 1)
        spans_json.append(js)
    if mode in ("weather", "both"):
        for f in FIELDS:
            if f not in bw.data:
                continue
            name = f"{prefix}_{f}"
            long(bw.data[f], name)
            for js in spans_json:
                js[f] = name
    if mode in ("rating", "both"):
        name = f"{prefix}_rating"
        long(bw.span_mw, name)
        if mode == "rating":
            for js in spans_json:
                js["rating"] = name
    if tmax_profile:
        write_frame(
            pd.DataFrame(
                {"stage": st, "block": bl, "uid": line_uid, "value": bw.line_mw}
            ),
            out_dir / "Line" / f"{prefix}_tmax.{fmt}",
        )
    dlr: dict[str, Any] = {}
    if mode != "rating":
        dlr["conductor"] = params.conductor.to_json()
        dlr["tmax_conductor"] = params.tmax_conductor
        dlr["voltage"] = params.voltage_kv
        for key, val, default in (
            ("v_pu", params.v_pu, 1.0),
            ("power_factor", params.power_factor, 1.0),
            ("safety_factor", params.safety_factor, 1.0),
            ("low_wind_speed", params.low_wind_speed, 0.5),
            ("low_wind_angle", params.low_wind_angle, 45.0),
        ):
            if val != default:
                dlr[key] = val
        if params.solar_mode != "ghi" or "solar_irradiance" not in bw.data:
            dlr["solar_mode"] = (
                "none" if "solar_irradiance" not in bw.data else params.solar_mode
            )
    dlr["critical_spans"] = spans_json
    out_dir.mkdir(parents=True, exist_ok=True)
    import json

    (out_dir / f"{prefix}_line{line_uid}.json").write_text(
        json.dumps({"dlr": dlr}, indent=2) + "\n", encoding="utf-8"
    )
    return dlr


# ---------------------------------------------------------------------------
# plots
# ---------------------------------------------------------------------------


def plot_typical(
    out_dir: Path,
    h: History,
    years: Sequence[TypicalYear],
    stats: Sequence[Mapping[str, float | int]],
    static_mw: float | None = None,
) -> list[Path]:
    """PNG plots: month × hour heatmap of the p10 line rating; duration
    curves (history vs each typical year); monthly mean and p10."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("--plot needs matplotlib") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    grid = np.full((12, 24), np.nan)
    for r in stats:
        if "p10" in r:
            grid[int(r["month"]) - 1, int(r["hour"])] = float(r["p10"])
    fig, ax = plt.subplots(figsize=(9, 4.5))
    im = ax.imshow(grid, aspect="auto", origin="lower", cmap="viridis")
    ax.set_xlabel("hour of day (local)")
    ax.set_ylabel("month")
    ax.set_yticks(range(12), [calendar.month_abbr[m] for m in range(1, 13)])
    ax.set_title("P90 line rating (10th percentile, min over critical spans) [MW]")
    fig.colorbar(im, ax=ax, label="MW")
    p = out_dir / "heatmap_p90_month_hour.png"
    fig.tight_layout()
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    hist = np.sort(h.line_mw[h.valid])[::-1]
    ax.plot(
        np.linspace(0, 100, hist.size),
        hist,
        color="0.3",
        lw=2,
        label="history (all hours)",
    )
    for ty in years:
        assert ty.line_mw is not None
        v = np.sort(ty.line_mw)[::-1]
        ax.plot(
            np.linspace(0, 100, v.size),
            v,
            lw=1.4,
            label=f"typical {ty.case.label} ({ty.method})",
        )
    if static_mw is not None:
        ax.axhline(static_mw, ls="--", color="k", lw=1, label="static rating")
    ax.set_xlabel("% of hours exceeded")
    ax.set_ylabel("line rating [MW]")
    ax.set_title("Duration curves: typical years vs history")
    ax.legend()
    ax.grid(alpha=0.3)
    p = out_dir / "duration_curves.png"
    fig.tight_layout()
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    months = np.arange(1, 13)
    hm = [h.line_mw[h.valid & (h.cal.month == m)] for m in months]
    ax.plot(months, [v.mean() for v in hm], "k-o", label="history mean")
    ax.plot(
        months, [np.quantile(v, 0.1) for v in hm], "k--s", label="history P90 (p10)"
    )
    tc = typical_calendar()
    for ty in years:
        assert ty.line_mw is not None
        ax.plot(
            months,
            [ty.line_mw[tc.month == m].mean() for m in months],
            "-^",
            label=f"typical {ty.case.label} mean",
        )
    ax.set_xticks(months, [calendar.month_abbr[int(m)] for m in months])
    ax.set_ylabel("line rating [MW]")
    ax.set_title("Monthly line rating: typical years vs history")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    p = out_dir / "monthly_ratings.png"
    fig.tight_layout()
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)
    return paths
