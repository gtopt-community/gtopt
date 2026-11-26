# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Typical-year RE profiles aligned hour by hour with the DLR typical year.

**Alignment (the consistency guarantee).**  ``dlr2gtopt typical-year``
writes, for each of the 8760 typical hours, the historical UTC hour whose
weather it uses (``source_time``) and, at a blended TMY month boundary,
the weight of the secondary hour (``blend``).  This module reads those
source times and takes each plant's per-unit output **from the same
historical hours**: ``cf(h) = (1 − blend)·CF(primary) + blend·CF(secondary)``.
Line ratings and RE generation therefore come from the same weather
hours, and the wind-cooling / wind-power correlation is kept.

The secondary hour of a blended boundary is the column ``source_time_2``
when the file has it (the proposed shared interface); otherwise it is
reconstructed exactly from ``dlr2gtopt``'s blending rule
(``assemble_months``): around the seam at typical hour ``p`` (first hour
of a month), the hours before ``p`` blend with
``source_time[p] + (pos − p)`` and the hours from ``p`` on with
``source_time[p − 1] + (pos − p + 1)``.

**Energy (``case`` + ``energy_preserve``).**  For generation the default
case is the expected energy (``mean``).  A month selection that is
typical for *line ratings* need not have each plant's long-term energy,
so the aligned year is rescaled per calendar month (default; or per year)
to the target energy with a capped multiplicative factor (bisection on
``mean(min(k·cf, ceiling)) = target``; the ceiling is the plant's maximum
historical CF, so the rescaling never creates output above what the plant
ever produced).  Targets:

=================  =========================================================
``mean``           long-term monthly mean CF (all history years)
``p50``            the mean monthly shape scaled to the median annual CF
``pNN`` / ``quantile:<q>``
                   the annual energy exceeded NN % of years (quantile
                   ``1 − NN/100``): ``empirical`` (quantile of the
                   historical annual CFs) or ``normal``
                   (``P50·(1 − z·σ)``, σ = √(σ_IAV² + σ_unc²), the lender
                   convention; ``uncertainty`` = σ_unc), with the mean
                   monthly shape
=================  =========================================================

``pNN`` here is an **energy-yield** statistic (exceeded in NN % of
*years*), not the per-hour criticality P90 of a line rating.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt import _typical as dty

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
HOURS = dty.HOURS_PER_YEAR
ENERGY_SCOPES = ("monthly", "annual")
P_METHODS = ("empirical", "normal")
_SEAMS = [int(dty._CUM_DAYS[m]) * 24 for m in range(1, 12)]  # noqa: SLF001


@dataclass(frozen=True)
class EnergyCase:
    """Energy statistic of the typical year: mean, p50 or a quantile."""

    label: str
    q: float | None = None  # quantile of the annual energy (None = mean)

    @property
    def is_mean(self) -> bool:
        """True for the expected-energy case."""
        return self.q is None


def parse_energy_case(text: str) -> EnergyCase:
    """``mean`` | ``p50`` | ``pNN`` (energy exceeded NN % of years =
    quantile ``1 − NN/100``) | ``quantile:<q>``."""
    t = text.strip().lower()
    if t == "mean":
        return EnergyCase("mean")
    c = dty.parse_case(t)
    if c.q is None or c.label == "min":
        raise ValueError(f"energy case {text!r}: use mean, pNN or quantile:<q>")
    return EnergyCase(c.label, c.q)


# ---------------------------------------------------------------------------
# source times
# ---------------------------------------------------------------------------


@dataclass
class SourceHours:
    """The historical UTC hours behind each typical hour."""

    primary: list[datetime]
    secondary: list[datetime | None]
    blend: FloatArray  # weight of the secondary hour
    month: IntArray
    day: IntArray
    hour: IntArray  # 1..24
    meta: dict[str, Any] = field(default_factory=dict)
    # knn-mean: every historical hour averaged into the typical hour
    members: list[list[datetime]] | None = None


def _to_dt(x: Any) -> datetime:
    import pandas as pd  # pylint: disable=import-outside-toplevel

    return pd.Timestamp(x).to_pydatetime().replace(tzinfo=None)


def _nearest_seam(pos: int) -> int:
    return min(_SEAMS, key=lambda s: abs(pos - s))


def reconstruct_secondary(
    primary: list[datetime], blend: FloatArray
) -> list[datetime | None]:
    """Secondary hours of blended month seams (``dlr2gtopt`` rule)."""
    out: list[datetime | None] = [None] * len(primary)
    for pos in np.flatnonzero(np.asarray(blend) > 0):
        p = _nearest_seam(int(pos))
        if pos < p:
            out[pos] = primary[p] + timedelta(hours=int(pos - p))
        else:
            out[pos] = primary[p - 1] + timedelta(hours=int(pos - p + 1))
    return out


def read_source_hours(path: Path) -> SourceHours:
    """Source hours from a ``dlr2gtopt`` typical-year file (one row per
    hour × span; only the first span is read) or from a plain table
    ``hour_of_year, source_time[, source_time_2, blend, knn_sources]``.

    ``source_time_2`` (the blend partner) and ``knn_sources`` (knn-mean
    members) are read as written by ``dlr2gtopt``; files without
    ``source_time_2`` (older ``dlr2gtopt``) get it rebuilt by
    :func:`reconstruct_secondary`."""
    df = dty.read_frame(path)
    if "source_time" not in df.columns or "hour_of_year" not in df.columns:
        raise ValueError(f"{path}: needs hour_of_year and source_time columns")
    first = df.sort_values("hour_of_year").drop_duplicates("hour_of_year")
    if len(first) != HOURS:
        raise ValueError(f"{path}: {len(first)} typical hours, not {HOURS}")
    prim = [_to_dt(x) for x in first["source_time"]]
    blend = (
        first["blend"].to_numpy(dtype=float)
        if "blend" in first.columns
        else np.zeros(HOURS)
    )
    if "source_time_2" in first.columns:
        import pandas as pd  # pylint: disable=import-outside-toplevel

        sec: list[datetime | None] = [
            None if pd.isna(x) else _to_dt(x) for x in first["source_time_2"]
        ]
    else:
        sec = reconstruct_secondary(prim, blend)
    members = None
    if "knn_sources" in first.columns:
        members = [
            [_to_dt(t) for t in str(x).split(";") if t] if isinstance(x, str) else []
            for x in first["knn_sources"]
        ]
    tc = dty.typical_calendar()
    meta: dict[str, Any] = {"file": str(path)}
    for col in ("line", "span"):
        if col in df.columns:
            meta[f"n_{col}s"] = int(df[col].nunique())
    meta["provenance"] = (
        "source_time_2" if "source_time_2" in first.columns else "reconstructed"
    )
    if members is not None:
        meta["knn_hours"] = int(sum(len(m) > 1 for m in members))
    return SourceHours(prim, sec, blend, tc.month, tc.day, tc.hour + 1, meta, members)


# ---------------------------------------------------------------------------
# CF history
# ---------------------------------------------------------------------------


@dataclass
class CFHistory:
    """Hourly per-unit output of plants: arrays (hours, plants), UTC."""

    times: list[datetime]
    plants: list[str]
    uids: list[int]
    cf: FloatArray

    def index(self) -> dict[datetime, int]:
        """time -> row."""
        return {t: i for i, t in enumerate(self.times)}


def read_cf_history(path: Path, column: str = "cf") -> CFHistory:
    """Long table ``time, plant, uid, <column>``."""
    df = dty.read_frame(path)
    need = {"time", "plant", column}
    if not need <= set(df.columns):
        raise ValueError(f"{path}: needs columns {sorted(need)}")
    import pandas as pd  # pylint: disable=import-outside-toplevel

    df["time"] = pd.to_datetime(df["time"])
    plants = list(dict.fromkeys(df["plant"].astype(str)))
    uid_of = (
        dict(zip(df["plant"].astype(str), df["uid"].astype(int)))
        if "uid" in df.columns
        else {p: k + 1 for k, p in enumerate(plants)}
    )
    df["plant"] = df["plant"].astype(str)
    t_index = pd.DatetimeIndex(sorted(df["time"].unique()))
    wide = df.pivot(index="time", columns="plant", values=column).reindex(
        index=t_index, columns=plants
    )
    times = [t.to_pydatetime() for t in wide.index]
    return CFHistory(
        times, plants, [int(uid_of[p]) for p in plants], wide.to_numpy(dtype=float)
    )


def write_cf_history(h: CFHistory, path: Path, column: str = "cf") -> None:
    """Long table ``time, plant, uid, <column>``."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    n_t, n_p = h.cf.shape
    df = pd.DataFrame(
        {
            "time": np.repeat(np.array(h.times, dtype="datetime64[s]"), n_p),
            "plant": np.tile(h.plants, n_t),
            "uid": np.tile(h.uids, n_t),
            column: h.cf.reshape(-1),
        }
    )
    dty.write_frame(df, path)


def align(hist: CFHistory, src: SourceHours) -> FloatArray:
    """(8760, plants) output on the typical year's source hours: the
    primary hour, blended with the secondary at month seams, or the mean
    over the knn-mean members (same weights as the DLR weather)."""
    idx = hist.index()
    rows_a = []
    missing = []
    for t in src.primary:
        if t not in idx:
            missing.append(t)
        rows_a.append(idx.get(t, 0))
    out = hist.cf[np.array(rows_a)].copy()
    for pos in np.flatnonzero(src.blend > 0):
        t2 = src.secondary[pos]
        if t2 is None or t2 not in idx:
            if t2 is not None:
                missing.append(t2)
            continue
        w = float(src.blend[pos])
        out[pos] = (1 - w) * out[pos] + w * hist.cf[idx[t2]]
    for row, mem in enumerate(src.members or []):
        if len(mem) < 2:
            continue
        miss = [t for t in mem if t not in idx]
        if miss:
            missing.extend(miss)
            continue
        out[row] = hist.cf[[idx[t] for t in mem]].mean(axis=0)
    if missing:
        raise ValueError(
            f"the CF history misses {len(missing)} source hour(s) of the typical "
            f"year (first {min(missing)}): build it over the same years"
        )
    if np.isnan(out).any():
        raise ValueError("the CF history has NaN on source hours of the typical year")
    return out


# ---------------------------------------------------------------------------
# energy targets and rescaling
# ---------------------------------------------------------------------------


def local_month_year(
    times: list[datetime], utc_offset: float | str
) -> tuple[IntArray, IntArray]:
    """Local (year, month) of UTC times."""
    cal = dty.local_calendar(times, utc_offset)
    return cal.year, cal.month


def energy_targets(
    hist: CFHistory,
    case: EnergyCase,
    *,
    utc_offset: float | str = -4.0,
    years: list[int] | None = None,
    p_method: str = "empirical",
    uncertainty: float = 0.0,
) -> tuple[FloatArray, dict[str, Any]]:
    """(12, plants) target monthly mean CF, and per-plant statistics."""
    if p_method not in P_METHODS:
        raise ValueError(f"p-method must be one of {P_METHODS}")
    yr, mo = local_month_year(hist.times, utc_offset)
    n_p = len(hist.plants)
    cf = hist.cf
    ok = np.isfinite(cf)
    use = np.ones(len(hist.times), dtype=bool)
    if years is not None:
        use &= np.isin(yr, years)
    monthly = np.zeros((12, n_p))
    for m in range(1, 13):
        sel = use & (mo == m)
        for j in range(n_p):
            s = sel & ok[:, j]
            monthly[m - 1, j] = float(cf[s, j].mean()) if s.any() else math.nan
    # complete years (all 12 months present, ≥ 90 % of hours)
    ys = sorted({int(y) for y in yr[use]})
    annual = np.full((len(ys), n_p), math.nan)
    for i, y in enumerate(ys):
        sel = use & (yr == y)
        for j in range(n_p):
            s = sel & ok[:, j]
            months = set(mo[s].tolist())
            if len(months) == 12 and s.sum() >= 0.9 * 8760:
                annual[i, j] = float(cf[s, j].mean())
    stats: dict[str, Any] = {"years": ys, "plants": {}}
    targets = monthly.copy()
    for j, name in enumerate(hist.plants):
        a = annual[:, j][np.isfinite(annual[:, j])]
        mean_m = monthly[:, j]
        days = np.diff(dty._CUM_DAYS)  # noqa: SLF001
        mean_annual = float(np.nansum(mean_m * days) / days.sum())
        st: dict[str, Any] = {
            "mean_cf": round(mean_annual, 4),
            "n_years": int(a.size),
            "iav": round(float(a.std(ddof=1) / a.mean()), 4) if a.size > 1 else None,
        }
        if not case.is_mean:
            q = float(0.5 if case.q is None else case.q)
            if p_method == "empirical":
                if a.size < 2:
                    raise ValueError(
                        f"plant {name}: {case.label} needs ≥ 2 complete years"
                    )
                tgt = float(np.quantile(a, q))
            else:
                from statistics import NormalDist  # pylint: disable=C0415

                p50 = float(np.median(a)) if a.size else mean_annual
                iav = float(a.std(ddof=1) / a.mean()) if a.size > 1 else 0.0
                sig = math.sqrt(iav**2 + uncertainty**2)
                tgt = p50 * (1.0 + NormalDist().inv_cdf(q) * sig)
            targets[:, j] = mean_m * (tgt / mean_annual if mean_annual > 0 else 0.0)
            st["target_cf"] = round(tgt, 4)
        else:
            st["target_cf"] = st["mean_cf"]
        stats["plants"][name] = st
    return targets, stats


def capped_scale(
    cf: FloatArray, target: float, ceiling: float
) -> tuple[float, FloatArray]:
    """k with mean(min(k·cf, ceiling)) = target (bisection)."""
    from re2gtopt._bias import cf_scale  # pylint: disable=import-outside-toplevel

    return cf_scale(cf, target, ceiling)


def preserve_energy(
    typ: FloatArray,
    targets: FloatArray,
    ceilings: FloatArray,
    scope: str = "monthly",
) -> tuple[FloatArray, FloatArray]:
    """Rescale the aligned year to the targets; returns the profile and
    the factors ((12, plants) for ``monthly``, (1, plants) for ``annual``)."""
    if scope not in ENERGY_SCOPES:
        raise ValueError(f"energy scope must be one of {ENERGY_SCOPES}")
    tc = dty.typical_calendar()
    out = typ.copy()
    n_p = typ.shape[1]
    days = np.diff(dty._CUM_DAYS)  # noqa: SLF001
    if scope == "annual":
        fac = np.zeros((1, n_p))
        for j in range(n_p):
            tgt = float(np.nansum(targets[:, j] * days) / days.sum())
            fac[0, j], out[:, j] = capped_scale(typ[:, j], tgt, ceilings[j])
        return out, fac
    fac = np.zeros((12, n_p))
    for m in range(1, 13):
        sel = tc.month == m
        for j in range(n_p):
            fac[m - 1, j], out[sel, j] = capped_scale(
                typ[sel, j], float(targets[m - 1, j]), ceilings[j]
            )
    return out, fac


def typical_report(
    hist: CFHistory,
    aligned: FloatArray,
    final: FloatArray,
    targets: FloatArray,
    factors: FloatArray,
    stats: dict[str, Any],
    utc_offset: float | str = -4.0,
) -> dict[str, Any]:
    """Per plant: aligned vs target vs final annual/monthly CF, the factors
    and where the aligned year's energy sits among the historical years."""
    tc = dty.typical_calendar()
    out: dict[str, Any] = {"years": stats["years"], "plants": {}}
    yr, mo = local_month_year(hist.times, utc_offset)
    for j, name in enumerate(hist.plants):
        ann = []
        for y in stats["years"]:
            s = (yr == y) & np.isfinite(hist.cf[:, j])
            if s.sum() >= 0.9 * 8760 and len(set(mo[s].tolist())) == 12:
                ann.append(float(hist.cf[s, j].mean()))
        a_cf = float(aligned[:, j].mean())
        pct = float(np.mean(np.array(ann) <= a_cf)) if ann else math.nan
        out["plants"][name] = {
            **stats["plants"][name],
            "aligned_cf": round(a_cf, 4),
            "final_cf": round(float(final[:, j].mean()), 4),
            "aligned_percentile_among_years": round(pct, 3),
            "monthly_aligned": [
                round(float(aligned[tc.month == m, j].mean()), 4) for m in range(1, 13)
            ],
            "monthly_target": [round(float(x), 4) for x in targets[:, j]],
            "monthly_final": [
                round(float(final[tc.month == m, j].mean()), 4) for m in range(1, 13)
            ],
            "factors": [round(float(x), 4) for x in factors[:, j]],
            "max_final": round(float(np.max(final[:, j])), 4),
        }
    return out


def typical_frame(
    plants: list[str], uids: list[int], prof: FloatArray, src: SourceHours
) -> Any:
    """Long table, one row per (hour, plant)."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    n_p = len(plants)

    def rep(a: Any) -> Any:
        return np.repeat(np.asarray(a), n_p)

    return pd.DataFrame(
        {
            "hour_of_year": rep(np.arange(1, HOURS + 1)),
            "month": rep(src.month),
            "day": rep(src.day),
            "hour": rep(src.hour),
            "plant": np.tile(plants, HOURS),
            "uid": np.tile(uids, HOURS),
            "cf": prof.reshape(-1),
            "source_time": rep(np.array(src.primary, dtype="datetime64[s]")),
            "blend": rep(src.blend),
        }
    )


@dataclass
class TypicalCF:
    """An RE typical year read back."""

    plants: list[str]
    uids: list[int]
    cf: FloatArray  # (8760, plants)
    source_time: list[datetime]


def read_typical_cf(path: Path) -> TypicalCF:
    """Read a table written by :func:`typical_frame`."""
    df = dty.read_frame(path).sort_values(["hour_of_year", "uid"])
    plants = list(dict.fromkeys(df["plant"].astype(str)))
    n_p = len(plants)
    if len(df) != HOURS * n_p:
        raise ValueError(f"{path}: expected {HOURS} hours x {n_p} plants")
    first = df.iloc[::n_p]
    uids = [int(u) for u in df["uid"].iloc[:n_p]]
    return TypicalCF(
        list(df["plant"].astype(str).iloc[:n_p]),
        uids,
        df["cf"].to_numpy(dtype=float).reshape(HOURS, n_p),
        [_to_dt(x) for x in first["source_time"]],
    )
