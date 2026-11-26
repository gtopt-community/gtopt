# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Observed hourly generation and the validation / cross-correlation report.

Observed input: a long table ``time, plant, <value>`` where the value is
``mwh`` (hourly energy, divided by the plant's Pmax) or ``cf``
(per-unit).  ``time`` is the hour *start* in UTC, or in local standard
time with ``utc_offset`` (Chile: −4; CEN hour 1 = 00:00-01:00 local).
Optional ``curtailed_mwh`` / ``curtailed_cf`` (CEN reducciones /
vertimiento) and a separate price table ``time, plant, price``.

The data themselves are downloaded by script (``re2gtopt fetch-cen``)
into a cache outside the repository and never committed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt import _typical as dty
from re2gtopt import _calib as cal
from re2gtopt._plants import Plant
from re2gtopt._typical import CFHistory

FloatArray = npt.NDArray[np.float64]


@dataclass
class Observed:
    """Observed CF (and optional curtailed CF, price) on the model hours."""

    cf: FloatArray  # (hours, plants), NaN = missing
    curtailed: FloatArray | None
    price: FloatArray | None


def read_observed(
    path: Path,
    plants: list[Plant],
    times: list[datetime],
    *,
    utc_offset: float = 0.0,
    price_path: Path | None = None,
    column: str | None = None,
) -> Observed:
    """Observed per-unit output of ``plants`` on the UTC hours ``times``
    (``column``: an MWh column to use instead of ``cf`` / ``mwh``, e.g.
    ``forecast_mwh``)."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    df = dty.read_frame(path)
    if "time" not in df.columns or "plant" not in df.columns:
        raise ValueError(f"{path}: needs time and plant columns")
    df["time"] = pd.to_datetime(df["time"]) - pd.Timedelta(hours=utc_offset)
    pmax = {p.name: p.pmax_mw for p in plants}
    df = df[df["plant"].astype(str).isin(pmax)].copy()
    if column is not None:
        if column not in df.columns:
            raise ValueError(f"{path}: no {column} column")
        df["cf"] = df[column] / df["plant"].astype(str).map(pmax)
    elif "cf" not in df.columns:
        if "mwh" not in df.columns:
            raise ValueError(f"{path}: needs a cf or mwh column")
        df["cf"] = df["mwh"] / df["plant"].astype(str).map(pmax)
    if "curtailed_mwh" in df.columns and "curtailed_cf" not in df.columns:
        df["curtailed_cf"] = df["curtailed_mwh"] / df["plant"].astype(str).map(pmax)
    t_idx = {t: i for i, t in enumerate(times)}
    names = [p.name for p in plants]

    def grid(frame: Any, col: str) -> FloatArray:
        out = np.full((len(times), len(names)), np.nan)
        for j, nm in enumerate(names):
            sub = frame[frame["plant"].astype(str) == nm]
            for t, v in zip(sub["time"], sub[col]):
                i = t_idx.get(t.to_pydatetime())
                if i is not None:
                    out[i, j] = float(v)
        return out

    obs = grid(df, "cf")
    cur = grid(df, "curtailed_cf") if "curtailed_cf" in df.columns else None
    price = None
    if price_path is not None:
        pdf = dty.read_frame(price_path)
        pdf["time"] = pd.to_datetime(pdf["time"]) - pd.Timedelta(hours=utc_offset)
        price = grid(pdf, "price")
    return Observed(obs, cur, price)


def utc_calendar(
    times: list[datetime], utc_offset: float | str
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Local (year, month, hour 0-23) of UTC times."""
    c = dty.local_calendar(times, utc_offset)
    return c.year, c.month, c.hour


def duration_curve(x: FloatArray, n: int = 21) -> list[float]:
    """Values exceeded 0, 5, ..., 100 % of the hours."""
    q = np.linspace(1.0, 0.0, n)
    return [round(float(v), 4) for v in np.quantile(x, q)]


def validate(
    model: CFHistory,
    obs: Observed,
    plants: list[Plant],
    *,
    utc_offset: float | str = -4.0,
    valid: npt.NDArray[np.bool_] | None = None,
    extra: dict[str, CFHistory] | None = None,
) -> dict[str, Any]:
    """Per plant: metrics, diurnal / monthly shapes, duration curves, for
    the model and any ``extra`` variants (e.g. uncalibrated, generic, the
    cap off), on the overlapping valid hours."""
    yr, mo, hr = utc_calendar(model.times, utc_offset)
    out: dict[str, Any] = {"plants": {}}
    for j, p in enumerate(plants):
        o = obs.cf[:, j]
        sel = np.isfinite(o) & np.isfinite(model.cf[:, j])
        if valid is not None:
            sel &= valid[:, j]
        if sel.sum() == 0:
            out["plants"][p.name] = {"note": "no overlapping observed hours"}
            continue
        res: dict[str, Any] = {
            "years": sorted({int(y) for y in yr[sel]}),
            "observed_duration": duration_curve(o[sel]),
        }
        variants = {"model": model, **(extra or {})}
        for name, h in variants.items():
            m = h.cf[:, j]
            s2 = sel & np.isfinite(m)
            res[name] = {
                **cal.metrics(m[s2], o[s2]),
                "shapes": cal.shape_profiles(m[s2], o[s2], mo[s2], hr[s2]),
                "duration": duration_curve(m[s2]),
            }
        out["plants"][p.name] = res
    return out


def read_rating(path: Path) -> tuple[list[datetime], FloatArray]:
    """A line-rating series ``time, line_rating_mw`` (UTC)."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    df = dty.read_frame(path)
    col = "line_rating_mw" if "line_rating_mw" in df.columns else "value"
    if "time" not in df.columns or col not in df.columns:
        raise ValueError(f"{path}: needs time and line_rating_mw columns")
    df["time"] = pd.to_datetime(df["time"])
    return [t.to_pydatetime() for t in df["time"]], df[col].to_numpy(dtype=float)


def cross_correlation(
    model: CFHistory,
    rating_times: list[datetime],
    rating: FloatArray,
    obs: Observed | None = None,
    lags: tuple[int, ...] = (-3, -2, -1, 0, 1, 2, 3),
) -> dict[str, Any]:
    """Pearson correlation of each plant's CF with the DLR line rating on
    the shared hours, at several lags (hours; positive = rating later),
    for the model and (where observed) the observation on the same hours."""
    r_idx = {t: i for i, t in enumerate(rating_times)}
    # model-hour -> rating-hour index per lag, built once for all plants
    lag_idx = {
        lag: np.array(
            [r_idx.get(t + timedelta(hours=lag), -1) for t in model.times],
            dtype=np.int64,
        )
        for lag in lags
    }
    out: dict[str, Any] = {}
    for j, name in enumerate(model.plants):
        res: dict[str, Any] = {}
        for lag in lags:
            k_all = lag_idx[lag]
            sel = (k_all >= 0) & np.isfinite(model.cf[:, j])
            if int(sel.sum()) < 3:
                continue
            ri = np.flatnonzero(sel)
            rrows = k_all[ri]
            rr = rating[rrows]
            ent: dict[str, Any] = {
                "n": int(ri.size),
                "model": _pearson(model.cf[ri, j], rr),
            }
            if obs is not None:
                o = obs.cf[ri, j]
                ok = np.isfinite(o)
                ent["n_obs"] = int(ok.sum())
                ent["observed"] = _pearson(o[ok], rr[ok]) if ok.sum() > 2 else math.nan
                ent["model_on_obs_hours"] = (
                    _pearson(model.cf[ri, j][ok], rr[ok]) if ok.sum() > 2 else math.nan
                )
            res[str(lag)] = ent
        out[name] = res
    return out


def _pearson(a: FloatArray, b: FloatArray) -> float:
    if a.size < 3 or float(np.std(a)) == 0 or float(np.std(b)) == 0:
        return math.nan
    return round(float(np.corrcoef(a, b)[0, 1]), 4)
