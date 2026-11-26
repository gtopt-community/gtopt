# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Synthetic multi-year span weather for the typical-year tests (made up)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from dlr2gtopt._emit import RatingParams
from dlr2gtopt._ieee738 import CONDUCTORS
from dlr2gtopt._spans import Span
from dlr2gtopt._weather import Weather

PARAMS = RatingParams(conductor=CONDUCTORS["flint"], tmax_conductor=55.0)


def spans3() -> list[Span]:
    """Three spans with different axes."""
    return [
        Span("L", 1, -31.90, -71.47, 10.0, 300.0, 0.15, 50.0),
        Span("L", 2, -31.80, -71.49, 157.0, 300.0, 10.0, 80.0),
        Span("L", 3, -31.70, -71.51, 80.0, 300.0, 20.0, 120.0),
    ]


def history(
    years: tuple[int, ...] = (2001, 2002, 2003, 2004),
    year_shift: dict[int, float] | None = None,
    seed: int = 7,
    spans: list[Span] | None = None,
) -> Weather:
    """Hourly UTC weather of whole calendar years with a diurnal cycle,
    seasonal temperature and noise.  ``year_shift`` adds a per-year wind
    speed offset [m/s] (the TMY test uses it to make one year central)."""
    sp = spans or spans3()
    rng = np.random.default_rng(seed)
    times: list[datetime] = []
    for y in years:
        t = datetime(y, 1, 1)
        while t.year == y:
            times.append(t)
            t += timedelta(hours=1)
    n, n_s = len(times), len(sp)
    hod = np.array([t.hour for t in times], dtype=float)[:, None]
    doy = np.array([t.timetuple().tm_yday for t in times], dtype=float)[:, None]
    yr = np.array([t.year for t in times])
    shift = np.array([(year_shift or {}).get(int(y), 0.0) for y in yr])[:, None]
    x = np.linspace(0.0, 1.0, n_s)[None, :]
    temp = (
        18
        + 6 * np.cos(2 * np.pi * (doy - 15) / 365)
        + 6 * np.sin(2 * np.pi * (hod - 9) / 24)
        + 1.5 * x
        + rng.normal(0, 1.0, (n, n_s))
    )
    speed = np.clip(
        3.0
        + shift
        + 2.0 * np.sin(2 * np.pi * (hod - 10) / 24)
        + rng.normal(0, 0.8, (n, 1))
        + rng.normal(0, 0.4, (n, n_s)),
        0.2,
        None,
    )
    wdir = np.mod(
        220 + 50 * np.sin(2 * np.pi * hod / 24) + 40 * x + rng.normal(0, 20, (n, n_s)),
        360,
    )
    ghi = np.clip(1000 * np.sin(np.pi * (hod - 6) / 12), 0, None) * np.ones((1, n_s))
    return Weather(
        times,
        list(sp),
        {
            "ambient_temperature": temp,
            "wind_speed": speed,
            "wind_direction": wdir,
            "solar_irradiance": ghi,
        },
    )
