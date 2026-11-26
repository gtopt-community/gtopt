# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Attack angle from a dominant-direction climatology (synthetic roses)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from dlr2gtopt import _wind_angle as wa
from dlr2gtopt._tz import TimeBasis
from gtopt_shared import weather_source as ws


def _hours(year: int) -> list[datetime]:
    t0 = datetime(year, 1, 1)
    return [t0 + timedelta(hours=k) for k in range(8760)]


def test_constant_wind_gives_its_angle() -> None:
    d = wa.dominant_direction(np.full(50, 4.0), np.full(50, 30.0))
    assert d.direction == pytest.approx(30.0)
    assert d.share == pytest.approx(1.0) and d.r == pytest.approx(1.0)
    assert wa.attack_angle(d.direction, 0.0) == pytest.approx(30.0)
    assert wa.attack_angle(d.direction, 100.0) == pytest.approx(70.0)


def test_bimodal_rose_picks_the_heavier_sector() -> None:
    # more hours from 270 deg, but more wind (speed sum) from 90 deg
    speed = np.array([1.0] * 10 + [5.0] * 4)
    direction = np.array([270.0] * 10 + [88.0, 90.0, 92.0, 90.0])
    d = wa.dominant_direction(speed, direction)
    assert d.direction == pytest.approx(90.0, abs=0.5)
    assert d.share == pytest.approx(20.0 / 30.0)
    assert 0.0 < d.r < 1.0


def test_sector_across_north_and_empty_cell() -> None:
    d = wa.dominant_direction(np.ones(2), np.array([355.0, 5.0]))
    assert min(d.direction, 360.0 - d.direction) == pytest.approx(0.0, abs=1e-6)
    e = wa.dominant_direction(np.zeros(3), np.full(3, 10.0))
    assert np.isnan(e.direction) and np.isnan(e.share) and np.isnan(e.r)


@pytest.mark.parametrize(
    ("direction", "azimuth", "phi"),
    [
        (350.0, 10.0, 20.0),
        (100.0, 0.0, 80.0),
        (180.0, 0.0, 0.0),
        (270.0, 0.0, 90.0),
        (200.0, 0.0, 20.0),
        (10.0, 350.0, 20.0),
        (45.0, 225.0, 0.0),
    ],
)
def test_attack_angle_folds_to_0_90(
    direction: float, azimuth: float, phi: float
) -> None:
    assert float(wa.attack_angle(direction, azimuth)) == pytest.approx(phi)


def test_month_hour_lookup_follows_the_local_clock_across_dst() -> None:
    clock = TimeBasis(0.0, "America/Santiago")
    src = _hours(2010)
    loc = ws.local_times(src, clock)
    # direction encodes the LOCAL hour: 10 deg per hour (0..230)
    direction = np.array([[10.0 * t.hour] for t in loc])
    table = wa.month_hour_dominant(loc, np.ones((len(src), 1)), direction)
    assert table.direction.shape == (1, 12, 24)
    phi = wa.angle_table(table, [0.0])
    tgt = _hours(2021)
    tloc = ws.local_times(tgt, clock)
    series = wa.wind_direction_series(tloc, phi, [0.0])
    expect = np.mod(wa.attack_angle(np.array([10.0 * t.hour for t in tloc]), 0.0), 360)
    assert np.allclose(series[:, 0], expect)
    # the same UTC hour maps to different local hours in winter and summer
    jan = tgt.index(datetime(2021, 1, 15, 16))  # summer, UTC-3 -> 13 h
    jul = tgt.index(datetime(2021, 7, 15, 16))  # winter, UTC-4 -> 12 h
    assert series[jan, 0] == pytest.approx(float(wa.attack_angle(130.0, 0.0)))
    assert series[jul, 0] == pytest.approx(float(wa.attack_angle(120.0, 0.0)))


def test_missing_cell_is_refused() -> None:
    loc = [datetime(2010, 1, 1, h) for h in range(24)]
    table = wa.month_hour_dominant(loc, np.ones((24, 1)), np.full((24, 1), 10.0))
    phi = wa.angle_table(table, [0.0])
    with pytest.raises(ValueError, match="no \\(month, hour\\) cell"):
        wa.wind_direction_series([datetime(2021, 2, 1, 0)], phi, [0.0])
