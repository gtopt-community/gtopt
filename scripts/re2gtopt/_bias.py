# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Bias correction of simulated plant output against observed output.

Reanalysis and mesoscale wind is biased per site; Renewables.ninja
(Staffell & Pfenninger 2016) corrects the *wind speed* (``v' = α v + β``)
until the simulated capacity factor matches the observed one, which keeps
the power curve's nonlinearity (a CF scaling would not).  Methods:

``none``          no correction (default without observed data);
``speed-scale``   α with β = 0, solved by bisection so that the mean CF
                  equals the target (wind) / irradiance scale (PV);
``speed-offset``  β with α = 1 (wind only);
``cf-scale``      a constant factor on the CF (then clipped at the cap):
                  the simplest, distorts the duration curve;
``quantile``      empirical quantile mapping of the simulated CF onto the
                  observed CF distribution (per calendar month with
                  ``by_month``): matches the duration curve and the
                  maximum, but needs a long overlap and is not
                  extrapolable to new plants.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
BIAS_METHODS = ("none", "speed-scale", "speed-offset", "cf-scale", "quantile")


def solve_monotone(
    f: Callable[[float], float],
    target: float,
    lo: float,
    hi: float,
    tol: float = 1e-7,
    iters: int = 200,
) -> float:
    """x in [lo, hi] with f(x) = target for an increasing ``f`` (bisection;
    the nearest end when the target is out of range)."""
    f_lo, f_hi = f(lo), f(hi)
    if target <= f_lo:
        return lo
    if target >= f_hi:
        return hi
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def fit_speed_scale(
    model: Callable[[float, float], FloatArray],
    target_cf: float,
    lo: float = 0.3,
    hi: float = 3.0,
) -> float:
    """α so that mean(model(α, 0)) = target_cf."""
    return solve_monotone(lambda a: float(np.mean(model(a, 0.0))), target_cf, lo, hi)


def fit_speed_offset(
    model: Callable[[float, float], FloatArray],
    target_cf: float,
    lo: float = -8.0,
    hi: float = 8.0,
) -> float:
    """β so that mean(model(1, β)) = target_cf."""
    return solve_monotone(lambda b: float(np.mean(model(1.0, b))), target_cf, lo, hi)


def cf_scale(
    cf: FloatArray, target_cf: float, cap: float = 1.0
) -> tuple[float, FloatArray]:
    """Factor k with mean(min(k·cf, cap)) = target (capped scaling); the
    factor and the scaled series."""
    cf = np.asarray(cf, dtype=float)
    if target_cf <= 0 or float(cf.mean()) <= 0:
        return 0.0, np.zeros_like(cf)
    k = solve_monotone(
        lambda x: float(np.mean(np.minimum(x * cf, cap))), target_cf, 0.0, 50.0
    )
    return k, np.minimum(k * cf, cap)


def quantile_map(
    sim: FloatArray,
    obs_sim_overlap: FloatArray,
    obs: FloatArray,
    n_q: int = 101,
) -> FloatArray:
    """Map ``sim`` through the transfer function fitted between the
    simulated (``obs_sim_overlap``) and observed (``obs``) CF on the same
    hours: F_obs^-1(F_sim(x)), linear between ``n_q`` quantiles."""
    q = np.linspace(0.0, 1.0, n_q)
    qs = np.quantile(np.asarray(obs_sim_overlap, dtype=float), q)
    qo = np.quantile(np.asarray(obs, dtype=float), q)
    # strictly increasing abscissa for np.interp (ties at zero output)
    qs_u, idx = np.unique(qs, return_index=True)
    qo_u = np.maximum.accumulate(qo[idx])
    return np.clip(np.interp(sim, qs_u, qo_u), 0.0, 1.0)


def quantile_map_by_month(
    sim: FloatArray,
    sim_month: npt.NDArray[np.int64],
    overlap_sim: FloatArray,
    overlap_obs: FloatArray,
    overlap_month: npt.NDArray[np.int64],
    min_hours: int = 240,
) -> FloatArray:
    """:func:`quantile_map` per calendar month (all months pooled where a
    month has fewer than ``min_hours`` overlap hours)."""
    out = quantile_map(sim, overlap_sim, overlap_obs)
    for m in range(1, 13):
        sel_o = overlap_month == m
        if int(sel_o.sum()) < min_hours:
            continue
        sel = sim_month == m
        out[sel] = quantile_map(sim[sel], overlap_sim[sel_o], overlap_obs[sel_o])
    return out
