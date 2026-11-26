# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PV model (pvlib): clear-sky sanity, clipping invariants, every option."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from re2gtopt import _pv

pvlib = pytest.importorskip("pvlib")

LAT, LON = -23.5, -69.5  # Atacama


def _clear_day(days: int = 3, month: int = 1) -> tuple[list[datetime], np.ndarray]:
    t0 = datetime(2019, month, 15)
    times = [t0 + timedelta(hours=h) for h in range(24 * days)]
    import pandas as pd

    idx = pd.DatetimeIndex(times, tz="UTC") + pd.Timedelta(minutes=30)
    loc = pvlib.location.Location(LAT, LON, altitude=1500)
    cs = loc.get_clearsky(idx, model="ineichen")
    return times, cs["ghi"].to_numpy()


def test_clear_sky_noon_output() -> None:
    times, ghi = _clear_day()
    r = _pv.pv_plant_cf(times, ghi, LAT, LON, _pv.PVOptions(), elevation_m=1500)
    # local solar noon ~ 16:30 UTC at -69.5
    noon = [i for i, t in enumerate(times) if t.hour == 16]
    night = [i for i, t in enumerate(times) if t.hour in (4, 5, 6)]
    assert r.cf[noon].min() > 0.85
    assert r.cf[night].max() == 0.0
    assert 0.3 < r.cf.mean() < 0.55
    assert r.diagnostics["clipped_hours"] > 0  # DC/AC 1.3 clips at noon
    assert r.diagnostics["peak_pu"] == pytest.approx(1.0)


def test_clipping_invariants() -> None:
    times, ghi = _clear_day()
    for ratio in (1.0, 1.3, 1.5):
        o = _pv.PVOptions(dc_ac_ratio=ratio)
        r = _pv.pv_plant_cf(times, ghi * 1.2, LAT, LON, o)
        assert r.cf.max() <= 1.0 + 1e-12
        dc = _pv.pv_plant_cf(
            times,
            ghi * 1.2,
            LAT,
            LON,
            _pv.PVOptions(dc_ac_ratio=ratio, capacity_basis="dc"),
        )
        assert dc.cf.max() <= 1.0 / ratio + 1e-12
    lim = _pv.pv_plant_cf(times, ghi, LAT, LON, _pv.PVOptions(ac_limit=0.8))
    assert lim.diagnostics["peak_pu"] == pytest.approx(0.8)
    assert lim.cf.max() <= 0.8
    cap = _pv.pv_plant_cf(times, ghi, LAT, LON, _pv.PVOptions(cap=0.7))
    assert cap.cf.max() <= 0.7 + 1e-12


@pytest.mark.parametrize(
    "over",
    [
        {"tracking": "fixed"},
        {"tracking": "fixed", "tilt": 10.0, "azimuth": 0.0},
        {"tracking": "single-axis", "backtrack": "off"},
        {"decomposition": "erbs"},
        {"decomposition": "erbs-driesse"},
        {"decomposition": "disc"},
        {"decomposition": "dirint"},
        {"transposition": "haydavies"},
        {"transposition": "isotropic"},
        {"iam": "none"},
        {"temperature_model": "sapm"},
        {"temperature_model": "pvsyst"},
        {"temperature_model": "none"},
        {"time_label": "end"},
        {"time_label": "instant"},
    ],
)
def test_each_option(over: dict[str, object]) -> None:
    times, ghi = _clear_day(2)
    o = _pv.PVOptions().with_overrides(over)
    r = _pv.pv_plant_cf(times, ghi, LAT, LON, o, temperature_c=np.full(48, 25.0))
    assert 0.2 < r.cf.mean() < 0.6
    assert r.cf.max() <= 1.0


def test_source_decomposition_and_tracking_gain() -> None:
    times, ghi = _clear_day(2)
    with pytest.raises(ValueError):
        _pv.pv_plant_cf(times, ghi, LAT, LON, _pv.PVOptions(decomposition="source"))
    dni = ghi * 0.9
    dhi = ghi * 0.1
    r = _pv.pv_plant_cf(
        times, ghi, LAT, LON, _pv.PVOptions(decomposition="source"), dni=dni, dhi=dhi
    )
    assert r.cf.mean() > 0.2
    fixed = _pv.pv_plant_cf(times, ghi, LAT, LON, _pv.PVOptions(tracking="fixed"))
    track = _pv.pv_plant_cf(times, ghi, LAT, LON, _pv.PVOptions())
    assert track.cf.mean() > fixed.cf.mean()  # trackers gain in summer


def test_northern_hemisphere_default_azimuth() -> None:
    times, ghi = _clear_day(1, 6)
    r = _pv.pv_plant_cf(times, ghi, 35.0, -110.0, _pv.PVOptions(tracking="fixed"))
    assert r.cf.max() >= 0.0


def test_soiling_and_temperature_lower_output() -> None:
    times, ghi = _clear_day(2)
    o = _pv.PVOptions(dc_ac_ratio=1.0)
    base = _pv.pv_plant_cf(times, ghi, LAT, LON, o).cf.mean()
    dirty = _pv.pv_plant_cf(
        times, ghi, LAT, LON, _pv.PVOptions(dc_ac_ratio=1.0, soiling=0.1)
    ).cf.mean()
    hot = _pv.pv_plant_cf(
        times, ghi, LAT, LON, o, temperature_c=np.full(48, 40.0)
    ).cf.mean()
    assert dirty < base and hot < base


@pytest.mark.parametrize(
    "bad",
    [
        {"tracking": "x"},
        {"soiling": 1.0},
        {"dc_ac_ratio": 0.0},
        {"availability": 0.0},
        {"inverter_efficiency": 1.5},
        {"gcr": 1.0},
        {"cap": 2.0},
        {"ac_limit": 0.0},
    ],
)
def test_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _pv.PVOptions().with_overrides(bad).validate()
