# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Wind model: curves, shear, density, smoothing, wakes, cut-out, cap."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from re2gtopt import _wind as w


def test_generic_curve_known_values() -> None:
    c = w.generic_curve(330.0)
    v_r = (2 * 330 / (1.225 * 0.45)) ** (1 / 3)
    assert c.rated_speed == pytest.approx(v_r, abs=0.06)
    # cubic law between cut-in and rated
    v = 8.0
    assert float(c(np.array([v]))[0]) == pytest.approx(
        (v**3 - 27) / (v_r**3 - 27), abs=0.01
    )
    assert float(c(np.array([2.0]))[0]) == 0.0
    assert float(c(np.array([20.0]))[0]) == pytest.approx(1.0)
    assert float(c(np.array([25.0]))[0]) == 0.0  # cut-out
    assert float(c(np.array([40.0]))[0]) == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        {"specific_power": -1},
        {"specific_power": 300, "cp": 0.7},
        {"specific_power": 300, "cut_in": 12.0},
    ],
)
def test_generic_curve_errors(bad: dict[str, float]) -> None:
    sp = bad.pop("specific_power")
    with pytest.raises(ValueError):
        w.generic_curve(sp, **bad)


def test_power_curve_validation() -> None:
    with pytest.raises(ValueError):
        w.PowerCurve(np.array([1.0, 1.0]), np.array([0.0, 1.0]))
    with pytest.raises(ValueError):
        w.PowerCurve(np.array([1.0, 2.0]), np.array([0.0, 1.5]))
    with pytest.raises(ValueError):
        w.PowerCurve(np.array([[1.0, 2.0]]), np.array([[0.0, 1.0]]))


def test_known_turbine_csv(tmp_path: Path) -> None:
    """A tabulated turbine (kW) is normalised and interpolated."""
    f = tmp_path / "t2000.csv"
    rows = [(3, 0), (4, 100), (6, 500), (8, 1100), (10, 1700), (12, 2000), (25, 2000)]
    f.write_text("speed,power\n" + "\n".join(f"{a},{b}" for a, b in rows) + "\n")
    c = w.turbine_curve(str(f))
    assert float(c(np.array([7.0]))[0]) == pytest.approx(800 / 2000)
    assert c.rated_speed == 12.0
    assert c.cut_out == 25.0
    assert w.read_curve_csv(f).cut_out == 25.5
    c2 = w.turbine_curve("t2000.csv", base_dir=tmp_path)
    assert c2.name == "t2000"
    bad = tmp_path / "bad.csv"
    bad.write_text("v,p\n1,2\n")
    with pytest.raises(ValueError):
        w.read_curve_csv(bad)
    zero = tmp_path / "zero.csv"
    zero.write_text("speed,power\n1,0\n2,0\n")
    with pytest.raises(ValueError):
        w.read_curve_csv(zero)


@pytest.mark.parametrize("spec", ["iec1", "iec2", "iec3", "IEC3S", "sp250"])
def test_turbine_specs(spec: str) -> None:
    c = w.turbine_curve(spec)
    assert c.power.max() == pytest.approx(1.0)


@pytest.mark.parametrize("spec", ["spxx", "nonsense", "missing.csv"])
def test_turbine_spec_errors(spec: str) -> None:
    with pytest.raises(ValueError):
        w.turbine_curve(spec)


def test_specific_power() -> None:
    assert w.specific_power(3.45, 126.0) == pytest.approx(276.7, abs=0.2)
    with pytest.raises(ValueError):
        w.specific_power(0, 100)


def test_smoothing_rounds_knee_and_ramps_cutout() -> None:
    c = w.generic_curve(330.0)
    s = w.smooth_curve(c)
    v = np.array([c.rated_speed, 25.0, 27.0])
    single = c(v)
    smooth = s(v)
    assert smooth[0] < single[0]  # knee rounded
    assert 0 < smooth[1] < 1  # cut-out becomes a ramp
    assert smooth[2] < smooth[1]
    assert w.smooth_curve(c, 0.0, 0.0) is c
    with pytest.raises(ValueError):
        w.smooth_curve(c, -1.0, 0.2)


def test_atlite_smoothing_caps_at_eta() -> None:
    s = w.atlite_smooth(w.generic_curve(330.0))
    assert s.power.max() <= 0.95 + 1e-9


def test_shear_methods() -> None:
    v10 = np.array([4.0, 6.0])
    v20 = np.array([4.4, 6.9])
    lv = {10.0: v10, 20.0: v20}
    p = w.shear_extrapolate(lv, 100.0, "power")
    assert p == pytest.approx(v20 * 5 ** (1 / 7))
    lg = w.shear_extrapolate(lv, 100.0, "log", z0=0.03)
    assert lg == pytest.approx(v20 * math.log(100 / 0.03) / math.log(20 / 0.03))
    # fitted: recovers the exponent of a pure power-law pair
    a = np.log(v20 / v10) / math.log(2)
    f = w.shear_extrapolate(lv, 100.0, "fitted", alpha_clip=(0, 1))
    assert f == pytest.approx(v20 * 5**a)
    r = w.shear_extrapolate(lv, 100.0, "ratio", ratio=np.array([1.3, 1.2]))
    assert r == pytest.approx(v20 * np.array([1.3, 1.2]))
    # none: interpolation between bracketing levels, no extrapolation
    lv3 = {10.0: v10, 20.0: v20, 40.0: v20 + 1}
    assert w.shear_extrapolate(lv3, 30.0, "none") == pytest.approx(v20 + 0.5)
    assert w.shear_extrapolate(lv3, 80.0, "none") == pytest.approx(v20 + 1)
    assert w.shear_extrapolate(lv3, 5.0, "none") == pytest.approx(v10)
    assert w.shear_extrapolate(lv3, 20.0, "power") == pytest.approx(v20)


@pytest.mark.parametrize(
    ("levels", "method", "kw"),
    [
        ({}, "power", {}),
        ({10.0: np.ones(2)}, "bogus", {}),
        ({10.0: np.ones(2)}, "fitted", {}),
        ({10.0: np.ones(2)}, "ratio", {}),
        ({10.0: np.ones(2)}, "log", {"z0": 20.0}),
    ],
)
def test_shear_errors(levels: dict[float, np.ndarray], method: str, kw: dict) -> None:
    with pytest.raises(ValueError):
        w.shear_extrapolate(levels, 100.0, method, **kw)


def test_fit_shear_ratio() -> None:
    month = np.repeat(np.arange(1, 13), 24 * 2)
    hour = np.tile(np.arange(24), 24)
    v_ref = np.full(month.size, 5.0)
    v_hub = v_ref * (1.2 + 0.01 * hour)
    r = w.fit_shear_ratio(v_ref, v_hub, month, hour)
    assert r.shape == (12, 24)
    assert r[3, 10] == pytest.approx(1.3)
    empty = w.fit_shear_ratio(v_ref[:1], v_hub[:1], month[:1], hour[:1])
    assert empty[5, 5] == 1.0


def test_density_correction() -> None:
    assert float(w.air_density(15.0)) == pytest.approx(1.225, abs=0.001)
    rho_hi = float(w.air_density(15.0 - 0.0065 * 2500, None, 2500.0))
    assert rho_hi == pytest.approx(0.957, abs=0.01)
    vn = w.density_normalise(np.array([10.0]), rho_hi)
    assert float(vn[0]) == pytest.approx(10 * (rho_hi / 1.225) ** (1 / 3))
    assert w.pressure_at(0.0) == pytest.approx(101325.0)


def test_cutout_modes() -> None:
    v = np.array([10, 26, 22, 19, 26, 18.0])
    p = np.ones(6)
    assert w.apply_cutout(v, p, "hard") == pytest.approx(p)
    hy = w.apply_cutout(v, p, "hysteresis", cut_out=25, restart=20)
    assert hy.tolist() == [1, 0, 0, 1, 0, 1]
    soft = w.apply_cutout(v, p, "soft", cut_out=25, soft_width=5)
    assert soft[1] == pytest.approx(0.8)
    with pytest.raises(ValueError):
        w.apply_cutout(v, p, "bogus")
    with pytest.raises(ValueError):
        w.apply_cutout(v, p, "hysteresis", cut_out=20, restart=21)


def test_wake_deficit_hits_loss() -> None:
    rng = np.random.default_rng(1)
    v = rng.weibull(2.0, 5000) * 8.0
    c = w.smooth_curve(w.generic_curve(300.0))
    d = w.wake_deficit_for_loss(c, v, 0.07)
    loss = 1 - c(v * (1 - d)).mean() / c(v).mean()
    assert loss == pytest.approx(0.07, abs=1e-3)
    assert w.wake_deficit_for_loss(c, v, 0.0) == 0.0
    assert w.wake_deficit_for_loss(c, np.zeros(3), 0.1) == 0.0


def _speeds() -> np.ndarray:
    rng = np.random.default_rng(3)
    return rng.weibull(2.0, 8760) * 9.0


def test_farm_cf_cap_invariant() -> None:
    v = _speeds()
    for cap in (0.9, 0.95, None):
        r = w.wind_farm_cf(v, w.WindOptions(turbine="iec2", cap=cap))
        assert r.cf.max() <= (cap or 1.0) + 1e-12
        assert r.cf.min() >= 0
    r = w.wind_farm_cf(v, w.WindOptions(turbine="iec2", cap=0.9))
    assert r.diagnostics["capped_hours"] > 0


def test_farm_never_exceeds_pmax_with_oversize() -> None:
    v = _speeds()
    o = w.WindOptions(turbine="iec2", cap=None, availability=1.0, electrical_loss=0.0)
    r = w.wind_farm_cf(v, o, installed_ratio=1.2)
    assert r.cf.max() <= 1.0 + 1e-12
    assert r.diagnostics["peak_pu"] == pytest.approx(1.0)
    r2 = w.wind_farm_cf(
        v, w.WindOptions(turbine="iec2", smoothing="none"), pmax_basis="net"
    )
    assert r2.diagnostics["peak_pu"] == pytest.approx(0.98, abs=1e-6)
    with pytest.raises(ValueError):
        w.wind_farm_cf(v, o, pmax_basis="bogus")


@pytest.mark.parametrize(
    "over",
    [
        {"smoothing": "none"},
        {"smoothing": "ninja"},
        {"smoothing": "atlite"},
        {"smoothing": "gaussian", "smoothing_a": 1.0, "smoothing_b": 0.1},
        {"density": "none"},
        {"density": "elevation"},
        {"density": "weather"},
        {"wake_mode": "power"},
        {"wake_mode": "speed"},
        {"cutout_mode": "hard"},
        {"cutout_mode": "hysteresis"},
        {"cutout_mode": "soft"},
        {"turbine": "auto"},
    ],
)
def test_each_option_value(over: dict[str, object]) -> None:
    v = _speeds()
    o = w.WindOptions().with_overrides(over)
    t = np.full(v.size, 10.0)
    r = w.wind_farm_cf(v, o, temperature_c=t, elevation_m=1500.0)
    assert 0.05 < r.cf.mean() < 0.7
    assert r.cf.max() <= 0.95 + 1e-12


def test_density_lowers_output_at_altitude() -> None:
    v = _speeds()
    base = w.wind_farm_cf(v, w.WindOptions(density="none", cap=None)).cf.mean()
    high = w.wind_farm_cf(
        v, w.WindOptions(density="elevation", cap=None), elevation_m=2500.0
    ).cf.mean()
    assert high < 0.95 * base


def test_wake_power_mode_caps_peak() -> None:
    v = np.concatenate([_speeds()[:2000] * 0.8, np.full(100, 18.0)])
    o = w.WindOptions(
        smoothing="none",
        wake_mode="power",
        availability=1,
        electrical_loss=0,
        cap=None,
    )
    assert w.wind_farm_cf(v, o).cf.max() == pytest.approx(0.93)
    o2 = w.WindOptions(smoothing="none", availability=1, electrical_loss=0, cap=None)
    assert w.wind_farm_cf(v, o2).cf.max() == pytest.approx(1.0)


@pytest.mark.parametrize(
    "bad",
    [
        {"smoothing": "x"},
        {"density": "x"},
        {"wake_mode": "x"},
        {"cutout_mode": "x"},
        {"wake_loss": 1.0},
        {"electrical_loss": -0.1},
        {"availability": 0.0},
        {"cap": 1.5},
    ],
)
def test_option_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        w.WindOptions().with_overrides(bad).validate()


def test_farm_curve_defaults() -> None:
    assert w.farm_curve(w.WindOptions(smoothing="none")).name == "iec2"
    assert w.farm_curve(w.WindOptions(smoothing="atlite")).power.max() <= 0.95
    g = w.farm_curve(w.WindOptions(smoothing="gaussian", smoothing_a=0.5))
    assert g.name.endswith("+sm")


def test_speed_bias_parameters_shift_output() -> None:
    v = _speeds()
    o = w.WindOptions()
    lo = w.wind_farm_cf(v, o).cf.mean()
    hi = w.wind_farm_cf(v, o, speed_scale=1.1).cf.mean()
    off = w.wind_farm_cf(v, o, speed_offset=1.0).cf.mean()
    assert hi > lo and off > lo


def test_missing_wind_hours_stay_missing() -> None:
    v = np.array([np.nan, 5.0, 12.0, 15.0, np.nan])
    res = w.wind_farm_cf(v, w.WindOptions())
    assert np.isnan(res.cf[0]) and np.isnan(res.cf[-1])
    assert np.isfinite(res.cf[1:4]).all()
    assert 0.9 < res.diagnostics["peak_pu"] <= 1.0
    assert res.diagnostics["mean_hub_speed"] == pytest.approx(32 / 3)
    allnan = w.wind_farm_cf(np.full(3, np.nan), w.WindOptions())
    assert allnan.diagnostics["peak_pu"] == 0.0


def test_single_plant_default_no_smoothing() -> None:
    assert w.WindOptions().smoothing == "none"
