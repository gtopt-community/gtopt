# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Bias correction and calibration (the inverse exercise), with censoring."""

from __future__ import annotations

import numpy as np
import pytest

from re2gtopt import _bias as bi
from re2gtopt import _calib as cb
from re2gtopt import _wind as w


def _year_month(n: int, years: int = 3) -> tuple[np.ndarray, np.ndarray]:
    per = n // years
    year = np.repeat(np.arange(2015, 2015 + years), per)
    month = np.tile(np.repeat(np.arange(1, 13), per // 12 + 1)[:per], years)
    return year[:n], month[:n]


def test_speed_scale_recovers_known_factor() -> None:
    rng = np.random.default_rng(0)
    v = rng.weibull(2, 8760) * 8
    o = w.WindOptions(cap=None)
    truth = w.wind_farm_cf(v * 1.1, o).cf.mean()

    def model(a: float, b: float) -> np.ndarray:
        return w.wind_farm_cf(v, o, speed_scale=a, speed_offset=b).cf

    a = bi.fit_speed_scale(model, truth)
    assert a == pytest.approx(1.1, abs=0.01)
    b = bi.fit_speed_offset(model, truth)
    assert b > 0
    assert bi.solve_monotone(lambda x: x, -5, 0, 1) == 0
    assert bi.solve_monotone(lambda x: x, 5, 0, 1) == 1


def test_cf_scale_and_quantile_map() -> None:
    cf = np.linspace(0, 0.8, 100)
    _, out = bi.cf_scale(cf, 0.5, 0.9)
    assert out.mean() == pytest.approx(0.5, abs=1e-4)
    assert out.max() <= 0.9
    assert bi.cf_scale(np.zeros(3), 0.5)[0] == 0.0
    obs = np.sqrt(cf)
    qm = bi.quantile_map(cf, cf, obs)
    assert qm == pytest.approx(np.clip(obs, 0, 1), abs=0.02)
    month = np.repeat(np.arange(1, 13), 30)
    sim = np.tile(np.linspace(0, 1, 30), 12)
    res = bi.quantile_map_by_month(sim, month, sim, sim * 0.5, month, min_hours=20)
    assert res == pytest.approx(sim * 0.5, abs=0.02)
    assert "quantile" in bi.BIAS_METHODS


def test_pava_is_isotonic() -> None:
    y = np.array([1.0, 3.0, 2.0, 4.0, 3.5])
    r = cb.pava(y, np.ones(5))
    assert np.all(np.diff(r) >= 0)
    assert r.sum() == pytest.approx(y.sum())


def test_valid_hours_flags() -> None:
    n = 24 * 400
    year, month = _year_month(n, 1)
    model = np.full(n, 0.6)
    obs = np.full(n, 0.5) + 0.05 * np.sin(np.arange(n))
    obs[:10] = np.nan
    obs[100:110] = 0.0  # outage at high resource
    obs[200:206] = 0.2  # flat below the potential
    cur = np.zeros(n, dtype=bool)
    cur[300:302] = True
    price = np.full(n, 50.0)
    price[400:403] = 0.0
    obs[400:403] = [0.3, 0.33, 0.27]
    ok, c = cb.valid_hours(obs, model, year, month, None, cur, price)
    assert c["missing"] == 10
    assert c["zero_high_resource"] == 10
    assert c["flat_curtailment"] >= 6
    assert c["curtailment_mask"] == 2
    assert c["low_price"] >= 1
    assert not ok[205] and not ok[105] and ok[5000]


def _wind_synth(
    curtail: bool, seed: int = 5
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, w.PowerCurve]:
    rng = np.random.default_rng(seed)
    n = 3 * 8760
    v = rng.weibull(2.0, n) * 8.5
    truth = w.smooth_curve(w.generic_curve(300.0))
    pot = np.clip(0.92 * truth(v) + rng.normal(0, 0.02, n), 0, 1)
    obs = pot.copy()
    if curtail:  # flat caps at high output (congestion), 6-hour episodes
        episode = np.repeat(rng.random(n // 6 + 1) < 0.3, 6)[:n]
        caps = (pot > 0.6) & episode
        obs[caps] = 0.5
    model = np.clip(w.generic_curve(330.0)(v), 0, 1)  # a wrong prior model
    year, month = _year_month(n, 3)
    return v, pot, obs, model, np.stack([year, month]), truth


@pytest.mark.parametrize("censoring", ["filter", "envelope", "mask"])
def test_wind_curve_recovered_under_curtailment(censoring: str) -> None:
    v, pot, obs, model, ym, truth = _wind_synth(True)
    year, month = ym
    o = pot if censoring == "mask" else obs
    ok, _ = cb.valid_hours(o, model, year, month)
    sel = cb.fit_hours(censoring, o, model, ok)
    env = 0.6 if censoring == "envelope" else None
    fit = cb.fit_calibration(
        "W", "wind", "curve", model, v, o, month, sel, envelope_q=env
    )
    grid = np.linspace(4, 14, 21)
    got = fit.apply(np.zeros(grid.size), grid)
    want = 0.92 * truth(grid)
    assert np.max(np.abs(got - want)) < 0.1
    # naive fit on the censored data is biased low at high wind
    naive = cb.fit_calibration(
        "W", "wind", "curve", model, v, obs, month, np.isfinite(obs)
    )
    assert naive.apply(np.zeros(1), np.array([13.0]))[0] < got[-3]


def test_scalar_k_recovered_with_midday_caps() -> None:
    """PV: potential = k·model; midday caps (systematic curtailment)."""
    rng = np.random.default_rng(2)
    n = 3 * 8760
    hod = np.arange(n) % 24
    shape = np.clip(np.sin(np.pi * (hod - 6) / 12), 0, None)
    model = shape * (0.8 + 0.2 * rng.random(n))
    k_true = 0.9
    pot = np.clip(k_true * model + rng.normal(0, 0.01, n), 0, 1) * (model > 0)
    obs = pot.copy()
    days = rng.random(n // 24) < 0.5
    curt = np.repeat(days, 24)[:n] & (hod >= 10) & (hod <= 15)
    obs[curt] = np.minimum(obs[curt], 0.45)
    year, month = _year_month(n, 3)
    ok, counts = cb.valid_hours(obs, model, year, month)
    assert counts["flat_curtailment"] > 0
    results = {}
    for cens in ("filter", "unconstrained", "envelope", "naive"):
        sel = cb.fit_hours(cens, obs, model, ok, ceiling=1.0, unconstrained_frac=0.5)
        env = 0.5 if cens == "envelope" else None
        fit = cb.fit_calibration(
            "P", "pv", "scalar", model, model, obs, month, sel, envelope_q=env
        )
        results[cens] = fit.k
    for cens in ("filter", "unconstrained", "envelope"):
        assert results[cens] == pytest.approx(k_true, abs=0.04), cens
    assert results["naive"] < k_true - 0.05
    # PV curve (effective PR against the DC driver)
    sel = cb.fit_hours("unconstrained", obs, model, ok, ceiling=1.0)
    pv = cb.fit_calibration("P", "pv", "curve", model, model / 0.98, obs, month, sel)
    assert pv.k == pytest.approx(k_true, abs=0.04)
    share = cb.curtailed_share(k_true * model, obs, ~ok)
    assert share > 0.02
    assert cb.curtailed_share(np.zeros(3), obs[:3], np.ones(3, bool)) == 0.0


def test_ceiling_estimate_and_methods() -> None:
    rng = np.random.default_rng(4)
    n = 5000
    model = rng.random(n)
    obs = np.minimum(model, 0.93)
    month = np.tile(np.arange(1, 13), n // 12 + 1)[:n]
    ok = np.ones(n, bool)
    fit = cb.fit_calibration(
        "X", "pv", "quantile-map", model, model, obs, month, ok, pmax_ceiling=1.0
    )
    assert fit.ceiling == pytest.approx(0.93, abs=0.005)
    assert fit.info["ceiling_vs_pmax"] == pytest.approx(-0.07, abs=0.005)
    q = fit.apply(model, model, month)
    assert q.max() <= 0.93 + 1e-9
    q2 = fit.apply(model, model, None)
    assert q2.max() <= 0.93 + 1e-9
    none = cb.fit_calibration("X", "pv", "none", model, model, obs, month, ok)
    assert none.apply(model, model) == pytest.approx(model)
    row = none.table_row()
    assert row["method"] == "none"
    with pytest.raises(ValueError):
        cb.fit_calibration("X", "pv", "bogus", model, model, obs, month, ok)
    with pytest.raises(ValueError):
        cb.fit_calibration("X", "pv", "scalar", model, model, obs, month, ~ok)
    with pytest.raises(ValueError):
        cb.fit_hours("bogus", obs, model, ok)
    with pytest.raises(ValueError):
        cb.fit_wind_curve(np.zeros(5), np.zeros(5), 1.0)


def test_loyo_and_metrics() -> None:
    v, _, obs, model, ym, _ = _wind_synth(False)
    year, month = ym
    ok = np.isfinite(obs)
    res = cb.leave_one_year_out(
        "W",
        "wind",
        ["none", "scalar", "curve", "quantile-map"],
        model,
        v,
        obs,
        year,
        month,
        ok,
    )
    assert res["years"] == [2015, 2016, 2017]
    curve = res["methods"]["curve"]["pooled"]
    none = res["methods"]["none"]["pooled"]
    assert curve["rmse"] < none["rmse"]
    assert abs(curve["bias"]) < 0.01
    one = cb.leave_one_year_out(
        "W", "wind", ["scalar"], model, v, obs, np.zeros(year.size, int), month, ok
    )
    assert "note" in one
    m = cb.metrics(np.array([]), np.array([]))
    assert m == {"n": 0}
    hr = np.arange(v.size) % 24
    sh = cb.shape_profiles(model, obs, month, hr)
    assert len(sh["diurnal"]["pred"]) == 24 and len(sh["monthly"]["obs"]) == 12
    row = cb.fit_calibration("W", "wind", "curve", model, v, obs, month, ok).table_row()
    assert row["curve_points"] > 10 and row["curve_v_half"] is not None


def test_valid_hours_without_model() -> None:
    n = 24 * 200
    year, month = _year_month(n, 1)
    model = np.full(n, 0.5)
    model[: n // 2] = np.nan  # e.g. no wind data in the first years
    obs = np.full(n, 0.45)
    ok, counts = cb.valid_hours(obs, model, year, month)
    assert not ok[: n // 2].any()
    assert counts["missing"] == n // 2
