# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Synthetic towers, weather and server replies for the dlr2gtopt tests.

All data are made up (no downloaded data in the repository)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from dlr2gtopt._spans import Span, Tower, build_spans
from dlr2gtopt._weather import Weather


def make_towers(n: int = 21, line: str = "L1") -> list[Tower]:
    """Towers on a gentle S-curve heading roughly north, ~400 m apart."""
    out = []
    for k in range(n):
        lat = -31.9 + k * 0.0036
        lon = -71.5 + 0.004 * math.sin(k / 3.0)
        out.append(Tower(line=line, seq=k, lat=lat, lon=lon, elevation=100.0 + k))
    return out


def make_spans(n_towers: int = 21, line: str = "L1") -> list[Span]:
    """Spans of :func:`make_towers`."""
    return build_spans(make_towers(n_towers, line))


def make_weather(spans: list[Span], hours: int = 96, seed: int = 1) -> Weather:
    """Diurnal, spatially varying weather; span 0 is the windless (critical)
    one by construction."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2010, 1, 1)
    times = [t0 + timedelta(hours=h) for h in range(hours)]
    h = np.arange(hours)[:, None]
    n = len(spans)
    x = np.linspace(0.0, 1.0, n)[None, :]
    temp = 15 + 8 * np.sin(2 * np.pi * (h - 9) / 24) + 2 * x
    speed = np.clip(
        2.0 + 1.5 * np.sin(2 * np.pi * h / 24 + 3 * x) + rng.normal(0, 0.5, (hours, n)),
        0.1,
        None,
    )
    speed[:, 0] *= 0.3
    wdir = np.mod(200 + 40 * np.sin(2 * np.pi * h / 24) + 30 * x, 360.0)
    ghi = np.clip(900 * np.sin(np.pi * (np.mod(h, 24) - 6) / 12), 0, None) * np.ones(
        (1, n)
    )
    return Weather(
        times,
        spans,
        {
            "ambient_temperature": temp,
            "wind_speed": speed,
            "wind_direction": wdir,
            "solar_irradiance": ghi,
        },
    )


def explorador_csv(
    heights: list[float],
    values: list[list[float | None]],
    start: str = "2010-01-01 00:00:00",
) -> str:
    """An Explorador hourly CSV (same layout as the server's)."""
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    lines = ["," + ",".join(f"{h:g}" for h in heights)]
    for k, row in enumerate(values):
        cells = ["" if v is None else f"{v}" for v in row]
        lines.append(
            (t0 + timedelta(hours=k)).strftime("%Y-%m-%d %H:%M:%S")
            + ","
            + ",".join(cells)
        )
    return "\n".join(lines) + "\n"
