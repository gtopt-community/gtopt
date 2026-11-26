# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Inverse exercise: fit each plant's weather -> output conversion on its
observed hourly production, then apply it to any weather (the typical year).

Pairing
    Observed hourly CF (CEN generación real / Pmax) and the modelled
    *driver* at the plant for the same UTC hours: the density-normalised
    hub-height wind speed (wind), or the modelled DC output before the
    inverter (PV, p.u. of Pmax: plane-of-array irradiance, temperature,
    soiling and DC losses already in).

Exclusions (:func:`valid_hours`, each flag counted in the table)
    * missing observations;
    * commissioning ramp: hours before ``ramp_months`` complete months
      after the first month with output;
    * outage / zero at high resource: observed < ``zero_cf`` while the
      uncalibrated model gives > ``zero_model_cf``;
    * flat-top curtailment: ≥ ``flat_hours`` consecutive hours whose
      output changes by < ``flat_tol`` while the model is above the
      observation by > ``flat_margin`` (dispatch instructions hold the
      output flat below its potential);
    * an explicit curtailment mask (CEN vertimiento), when given.

Methods (``--calibration``)
    ``scalar``       one factor ``k = Σ obs / Σ model`` (Renewables.ninja-
                     like, on the CF): keeps the model's shape;
    ``curve``        wind: an empirical plant power curve, the mean
                     observed CF per 0.5 m/s bin of density-normalised hub
                     speed, made monotone up to its peak (pool-adjacent-
                     violators) and clipped at the fitted ceiling.  It
                     contains wakes, availability, losses and the real
                     ceiling.  PV: ``CF = min(k_dc · DC, ceiling)``, an
                     effective performance ratio fitted on unclipped hours
                     and the observed clipping ceiling;
    ``quantile-map`` the modelled CF distribution mapped onto the observed
                     one per calendar month (pooled when a month is short);
    ``none``         the forward model as is.

Censoring (``--censoring``): observed output is a *censored* version of
the potential (Chilean PV is curtailed systematically at midday in the
north; wind too under congestion).  The profile gtopt needs is the
**potential (available) generation**; curtailment is gtopt's dispatch
decision.  So curtailment must never pull ``k`` or the curve down:

    ``filter``        (default) drop the hours flagged above, plus hours
                      with a zonal marginal cost ≤ ``price_eps`` while the
                      output sits below the model (when prices are given);
    ``mask``          potential = observed + curtailed (CEN reducciones /
                      vertimiento per plant and hour); nothing dropped for
                      curtailment;
    ``unconstrained`` fit only on hours that are clearly unconstrained:
                      model potential below ``unconstrained_frac`` of the
                      ceiling (early/late/cloudy hours) and, with prices, a
                      marginal cost above ``price_eps``; best for PV ``k``;
    ``envelope``      upper-envelope estimators on the filtered hours: the
                      ``envelope_q`` quantile of obs/model (scalar, PV) or
                      of each wind-speed bin (wind curve) -- robust to
                      unflagged curtailment, biased high on noisy data;
    ``naive``         every observed hour (for comparison only).

Ceiling
    The p99.5 of the observed CF on valid hours, compared with the
    ceiling implied by the reported Pmax (1.0, or the injection limit).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from re2gtopt._bias import quantile_map, quantile_map_by_month

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]
CALIBRATIONS = ("none", "scalar", "curve", "quantile-map")
CENSORINGS = ("filter", "mask", "unconstrained", "envelope", "naive")


@dataclass(frozen=True)
class FilterOptions:
    """Curtailment / outage heuristics (documented defaults)."""

    ramp_months: int = 3
    zero_cf: float = 0.01
    zero_model_cf: float = 0.3
    flat_hours: int = 2
    flat_tol: float = 0.005
    flat_margin: float = 0.10
    ceiling_quantile: float = 0.995
    min_hours: int = 2000


def _runs(mask: BoolArray) -> list[tuple[int, int]]:
    """[start, end) of the runs of True."""
    m = np.concatenate(([False], mask, [False])).astype(int)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def valid_hours(
    obs: FloatArray,
    model: FloatArray,
    year: IntArray,
    month: IntArray,
    opt: FilterOptions | None = None,
    curtailed: BoolArray | None = None,
    price: FloatArray | None = None,
    price_eps: float = 1.0,
) -> tuple[BoolArray, dict[str, int]]:
    """Mask of usable hours and the count of each exclusion."""
    opt = opt or FilterOptions()
    obs = np.asarray(obs, dtype=float)
    model = np.asarray(model, dtype=float)
    n = obs.size
    # missing: no observation, or no model (e.g. no wind data that year)
    missing = ~np.isfinite(obs) | ~np.isfinite(model)
    o = np.where(missing, 0.0, obs)
    model = np.where(np.isfinite(model), model, 0.0)
    # commissioning ramp
    ramp = np.zeros(n, dtype=bool)
    ym = np.asarray(year) * 12 + np.asarray(month) - 1
    producing = np.flatnonzero(o > opt.zero_cf)
    if producing.size:
        first = int(ym[producing[0]])
        ramp = ym < first + opt.ramp_months
    zero = (o < opt.zero_cf) & (model > opt.zero_model_cf) & ~missing
    # flat tops below the potential
    flat = np.zeros(n, dtype=bool)
    step = np.abs(np.diff(o, prepend=np.nan))
    still = (step < opt.flat_tol) & (o > opt.zero_cf) & (model > o + opt.flat_margin)
    for a, b in _runs(still):
        if b - a + 1 >= opt.flat_hours:
            flat[max(a - 1, 0) : b] = True
    cur = np.zeros(n, dtype=bool) if curtailed is None else np.asarray(curtailed)
    cheap = np.zeros(n, dtype=bool)
    if price is not None:
        pr = np.asarray(price, dtype=float)
        cheap = np.isfinite(pr) & (pr <= price_eps) & (model > o + opt.flat_margin)
    bad = missing | ramp | zero | flat | cur | cheap
    counts = {
        "hours": int(n),
        "missing": int(missing.sum()),
        "ramp": int((ramp & ~missing).sum()),
        "zero_high_resource": int(zero.sum()),
        "flat_curtailment": int((flat & ~zero).sum()),
        "curtailment_mask": int(cur.sum()),
        "low_price": int((cheap & ~flat & ~zero).sum()),
        "valid": int((~bad).sum()),
    }
    return ~bad, counts


def fit_hours(
    censoring: str,
    obs: FloatArray,
    model: FloatArray,
    valid: BoolArray,
    *,
    ceiling: float = 1.0,
    unconstrained_frac: float = 0.5,
    price: FloatArray | None = None,
    price_eps: float = 1.0,
) -> BoolArray:
    """The hours a fit uses under a censoring mode (``valid`` from
    :func:`valid_hours`; ``mask`` expects the potential already in ``obs``)."""
    if censoring not in CENSORINGS:
        raise ValueError(f"censoring must be one of {CENSORINGS}")
    finite = np.isfinite(np.asarray(obs, dtype=float))
    if censoring == "naive":
        return finite
    if censoring != "unconstrained":
        return np.asarray(valid) & finite
    sel = (
        np.asarray(valid) & finite & (np.asarray(model) < unconstrained_frac * ceiling)
    )
    if price is not None:
        sel &= np.nan_to_num(np.asarray(price, dtype=float), nan=0.0) > price_eps
    return sel


def curtailed_share(
    potential: FloatArray, obs: FloatArray, flagged: BoolArray
) -> float:
    """Estimated curtailed energy share: Σ max(potential − obs, 0) over the
    flagged (excluded, non-missing) hours / Σ potential."""
    p = np.asarray(potential, float)
    o = np.nan_to_num(np.asarray(obs, float))
    tot = float(p.sum())
    if tot <= 0:
        return 0.0
    f = np.asarray(flagged) & np.isfinite(np.asarray(obs, float))
    return float(np.maximum(p[f] - o[f], 0.0).sum() / tot)


def pava(y: FloatArray, w: FloatArray) -> FloatArray:
    """Weighted isotonic (non-decreasing) regression, pool-adjacent-
    violators."""
    vals: list[float] = []
    wts: list[float] = []
    lens: list[int] = []
    for yi, wi in zip(np.asarray(y, float), np.asarray(w, float)):
        vals.append(yi)
        wts.append(wi)
        lens.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            wsum = wts[-2] + wts[-1]
            v = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / wsum if wsum > 0 else 0.0
            ln = lens[-2] + lens[-1]
            vals[-2:] = [v]
            wts[-2:] = [wsum]
            lens[-2:] = [ln]
    return np.repeat(np.array(vals), lens)


@dataclass
class Calibration:
    """A fitted plant conversion."""

    plant: str
    type: str
    method: str
    k: float = 1.0
    ceiling: float = 1.0
    curve_speed: list[float] = field(default_factory=list)
    curve_cf: list[float] = field(default_factory=list)
    qmap: dict[str, Any] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)

    def apply(
        self,
        model_cf: FloatArray,
        driver: FloatArray,
        month: IntArray | None = None,
        inverter_efficiency: float = 0.98,
    ) -> FloatArray:
        """Calibrated CF from the forward model's CF and driver."""
        model_cf = np.asarray(model_cf, dtype=float)
        if self.method == "none":
            return model_cf
        if self.method == "scalar":
            return np.clip(self.k * model_cf, 0.0, self.ceiling)
        if self.method == "curve":
            if self.type == "wind":
                v = np.asarray(driver, dtype=float)
                out = np.interp(
                    v,
                    self.curve_speed,
                    self.curve_cf,
                    left=0.0,
                    right=self.curve_cf[-1],
                )
                return np.clip(out, 0.0, self.ceiling)
            dc = np.asarray(driver, dtype=float)
            return np.clip(self.k * dc * inverter_efficiency, 0.0, self.ceiling)
        # quantile-map
        qs = self.qmap
        if month is None or not qs.get("by_month"):
            return _qm_apply(model_cf, qs["all"])
        out = _qm_apply(model_cf, qs["all"])
        for m_str, tab in qs["by_month"].items():
            sel = np.asarray(month) == int(m_str)
            out[sel] = _qm_apply(model_cf[sel], tab)
        return out

    def table_row(self) -> dict[str, Any]:
        """Flat summary for the calibration table."""
        row: dict[str, Any] = {
            "plant": self.plant,
            "type": self.type,
            "method": self.method,
            "k": round(self.k, 4),
            "ceiling": round(self.ceiling, 4),
        }
        if self.curve_speed:
            row["curve_points"] = len(self.curve_speed)
            row["curve_v_half"] = _speed_at(self.curve_speed, self.curve_cf, 0.5)
        row.update(self.info)
        return row


def _speed_at(v: list[float], p: list[float], level: float) -> float | None:
    pa = np.asarray(p, dtype=np.float64)
    if np.max(pa) < level:
        return None
    i = int(np.argmax(pa >= level))
    return round(float(v[i]), 2)


def _qm_table(
    sim: FloatArray, obs: FloatArray, n_q: int = 101
) -> dict[str, list[float]]:
    q = np.linspace(0, 1, n_q)
    return {
        "sim": np.quantile(sim, q).round(6).tolist(),
        "obs": np.quantile(obs, q).round(6).tolist(),
    }


def _qm_apply(x: FloatArray, tab: dict[str, list[float]]) -> FloatArray:
    qs, idx = np.unique(np.asarray(tab["sim"]), return_index=True)
    qo = np.maximum.accumulate(np.asarray(tab["obs"])[idx])
    return np.clip(np.interp(x, qs, qo), 0.0, 1.0)


def fit_wind_curve(
    driver: FloatArray,
    obs: FloatArray,
    ceiling: float,
    bin_width: float = 0.5,
    min_count: int = 10,
    v_max: float = 30.0,
    envelope_q: float | None = None,
) -> tuple[list[float], list[float]]:
    """Binned mean observed CF vs speed, isotonic up to the peak bin (the
    tail beyond the peak may decline: high-wind cut-out), clipped."""
    v = np.asarray(driver, float)
    o = np.asarray(obs, float)
    edges = np.arange(0.0, v_max + bin_width, bin_width)
    centres, means, counts = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (v >= a) & (v < b)
        c = int(sel.sum())
        if c >= min_count:
            centres.append(0.5 * (a + b))
            means.append(
                float(o[sel].mean())
                if envelope_q is None
                else float(np.quantile(o[sel], envelope_q))
            )
            counts.append(c)
    if len(centres) < 3:
        raise ValueError("curve fit: too few populated wind-speed bins")
    m = np.array(means)
    w = np.array(counts, dtype=float)
    k_peak = int(np.argmax(m))
    mono = m.copy()
    mono[: k_peak + 1] = pava(m[: k_peak + 1], w[: k_peak + 1])
    # the tail after the peak: non-increasing (cut-out), never above peak
    if k_peak + 1 < m.size:
        tail = -pava(-m[k_peak + 1 :], w[k_peak + 1 :])
        mono[k_peak + 1 :] = np.minimum(tail, mono[k_peak])
    speeds = [0.0] + centres
    cfs = [0.0] + np.clip(mono, 0.0, ceiling).tolist()
    return speeds, cfs


def fit_pv_pr(
    dc: FloatArray,
    obs: FloatArray,
    ceiling: float,
    inverter_efficiency: float = 0.98,
    envelope_q: float | None = None,
) -> float:
    """Least-squares k in obs ≈ k·DC·η on hours well below the ceiling
    (or the ``envelope_q`` quantile of obs/(DC·η))."""
    x = np.asarray(dc, float) * inverter_efficiency
    o = np.asarray(obs, float)
    sel = (x > 0.05) & (o < 0.9 * ceiling) & (x < 0.9 * ceiling)
    if sel.sum() < 10:
        sel = x > 0.05
    if envelope_q is not None:
        return float(np.quantile(o[sel] / x[sel], envelope_q)) if sel.any() else 1.0
    den = float((x[sel] ** 2).sum())
    return float((x[sel] * o[sel]).sum() / den) if den > 0 else 1.0


def fit_calibration(
    plant: str,
    ptype: str,
    method: str,
    model_cf: FloatArray,
    driver: FloatArray,
    obs: FloatArray,
    month: IntArray,
    valid: BoolArray,
    *,
    pmax_ceiling: float = 1.0,
    ceiling_quantile: float = 0.995,
    inverter_efficiency: float = 0.98,
    envelope_q: float | None = None,
) -> Calibration:
    """Fit one plant on its valid hours (``envelope_q``: upper-envelope
    estimators, see ``censoring = envelope``)."""
    if method not in CALIBRATIONS:
        raise ValueError(f"calibration must be one of {CALIBRATIONS}")
    o: FloatArray = np.asarray(obs, dtype=np.float64)[valid]
    s: FloatArray = np.asarray(model_cf, dtype=np.float64)[valid]
    d = np.asarray(driver, float)[valid]
    mo = np.asarray(month)[valid]
    if o.size == 0:
        raise ValueError(f"plant {plant}: no valid observed hours")
    ceiling = float(np.quantile(o, ceiling_quantile))
    info = {
        "fitted_ceiling": round(ceiling, 4),
        "pmax_ceiling": round(pmax_ceiling, 4),
        "ceiling_vs_pmax": round(ceiling - pmax_ceiling, 4),
        "observed_cf": round(float(o.mean()), 4),
        "model_cf": round(float(s.mean()), 4),
    }
    cal = Calibration(plant, ptype, method, ceiling=ceiling, info=info)
    if method == "none":
        cal.ceiling = 1.0
        return cal
    if method == "scalar":
        if envelope_q is not None:
            sel = s > 0.1
            cal.k = float(np.quantile(o[sel] / s[sel], envelope_q)) if sel.any() else 1
        else:
            cal.k = float(o.sum() / s.sum()) if s.sum() > 0 else 1.0
    elif method == "curve":
        if ptype == "wind":
            cal.curve_speed, cal.curve_cf = fit_wind_curve(
                d, o, ceiling, envelope_q=envelope_q
            )
        else:
            cal.k = fit_pv_pr(d, o, ceiling, inverter_efficiency, envelope_q)
    else:
        cal.qmap = {"all": _qm_table(s, o), "by_month": {}}
        for m in range(1, 13):
            sel = mo == m
            if int(sel.sum()) >= 240:
                cal.qmap["by_month"][str(m)] = _qm_table(s[sel], o[sel])
    fitted = cal.apply(s, d, mo, inverter_efficiency)
    info["fit_bias"] = round(float(fitted.mean() - o.mean()), 4)
    info["fit_rmse"] = round(float(np.sqrt(np.mean((fitted - o) ** 2))), 4)
    info["fit_corr"] = _corr(fitted, o)
    return cal


def _corr(a: FloatArray, b: FloatArray) -> float:
    if a.size < 2 or float(np.std(a)) == 0 or float(np.std(b)) == 0:
        return math.nan
    return round(float(np.corrcoef(a, b)[0, 1]), 4)


def metrics(pred: FloatArray, obs: FloatArray) -> dict[str, float]:
    """bias, RMSE, MAE, correlation, and the RMS error of the duration
    curve (sorted series, percentile by percentile)."""
    p = np.asarray(pred, float)
    o = np.asarray(obs, float)
    if p.size == 0:
        return {"n": 0}
    q = np.linspace(0, 1, 101)
    return {
        "n": int(p.size),
        "mean_pred": round(float(p.mean()), 4),
        "mean_obs": round(float(o.mean()), 4),
        "bias": round(float(p.mean() - o.mean()), 4),
        "rmse": round(float(np.sqrt(np.mean((p - o) ** 2))), 4),
        "mae": round(float(np.mean(np.abs(p - o))), 4),
        "corr": _corr(p, o),
        "duration_rmse": round(
            float(np.sqrt(np.mean((np.quantile(p, q) - np.quantile(o, q)) ** 2))), 4
        ),
        "max_pred": round(float(p.max()), 4),
        "max_obs": round(float(o.max()), 4),
    }


def shape_profiles(
    pred: FloatArray, obs: FloatArray, month: IntArray, hour: IntArray
) -> dict[str, Any]:
    """Diurnal (24) and monthly (12) mean profiles, and their errors."""
    out: dict[str, Any] = {"diurnal": {}, "monthly": {}}
    for key, idx, n in (("diurnal", hour, 24), ("monthly", month, 12)):
        base = 0 if key == "diurnal" else 1
        pv, ov = [], []
        for k in range(base, base + n):
            sel = np.asarray(idx) == k
            pv.append(round(float(pred[sel].mean()), 4) if sel.any() else math.nan)
            ov.append(round(float(obs[sel].mean()), 4) if sel.any() else math.nan)
        diff = np.array(pv) - np.array(ov)
        out[key] = {
            "pred": pv,
            "obs": ov,
            "rmse": round(float(np.sqrt(np.nanmean(diff**2))), 4),
            "corr": _corr(np.nan_to_num(np.array(pv)), np.nan_to_num(np.array(ov))),
        }
    return out


def leave_one_year_out(
    plant: str,
    ptype: str,
    methods: list[str],
    model_cf: FloatArray,
    driver: FloatArray,
    obs: FloatArray,
    year: IntArray,
    month: IntArray,
    valid: BoolArray,
    *,
    pmax_ceiling: float = 1.0,
    ceiling_quantile: float = 0.995,
    inverter_efficiency: float = 0.98,
) -> dict[str, Any]:
    """Leave-one-year-out: per method, fit on the other years, predict the
    held-out year (valid hours); metrics pooled over all held-out hours."""
    years = sorted({int(y) for y in np.asarray(year)[valid]})
    res: dict[str, Any] = {"years": years, "methods": {}}
    if len(years) < 2:
        res["note"] = "fewer than 2 years: no cross-validation"
        return res
    for meth in methods:
        preds, obss = [], []
        per_year = {}
        for y in years:
            test = valid & (np.asarray(year) == y)
            train = valid & (np.asarray(year) != y)
            try:
                cal = fit_calibration(
                    plant,
                    ptype,
                    meth,
                    model_cf,
                    driver,
                    obs,
                    month,
                    train,
                    pmax_ceiling=pmax_ceiling,
                    ceiling_quantile=ceiling_quantile,
                    inverter_efficiency=inverter_efficiency,
                )
            except ValueError:
                continue
            p = cal.apply(
                np.asarray(model_cf)[test],
                np.asarray(driver)[test],
                np.asarray(month)[test],
                inverter_efficiency,
            )
            o = np.asarray(obs)[test]
            preds.append(p)
            obss.append(o)
            per_year[str(y)] = metrics(p, o)
        if preds:
            res["methods"][meth] = {
                "pooled": metrics(np.concatenate(preds), np.concatenate(obss)),
                "per_year": per_year,
            }
    return res


__all__ = [
    "CALIBRATIONS",
    "Calibration",
    "FilterOptions",
    "fit_calibration",
    "leave_one_year_out",
    "metrics",
    "quantile_map",
    "quantile_map_by_month",
    "shape_profiles",
    "valid_hours",
]
