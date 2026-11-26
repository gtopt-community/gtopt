# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Attack angle from a dominant-direction climatology.

For a weather source without wind direction (the Explorador Solar), the
attack angle of each span can come from the dominant wind direction of
the Explorador Eolico WRF, per month x local hour.  Only the direction
climatology is taken from the Eolico; it is a parameter of the rating,
not a second weather source.

Dominant direction of one (month, hour) cell: the 22.5 deg sector
(16-point rose, sector k centred on k * 22.5 deg) with the largest
sum of wind speed, and the speed-weighted circular mean of the
directions inside that sector.  ``share`` is that sector's fraction of
the speed sum; ``R`` the speed-weighted mean resultant length of all
the cell's directions (1 = steady, 0 = no preferred direction).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
SECTOR = 22.5
N_SECTORS = 16


@dataclass(frozen=True)
class Dominant:
    """Dominant direction [deg FROM], sector share and resultant length."""

    direction: float
    share: float
    r: float


def dominant_direction(speed: FloatArray, direction: FloatArray) -> Dominant:
    """Speed-weighted modal 22.5 deg sector and the circular mean in it."""
    ok = np.isfinite(speed) & np.isfinite(direction) & (speed > 0)
    w = speed[ok]
    d = np.mod(direction[ok], 360.0)
    if w.size == 0:
        return Dominant(float("nan"), float("nan"), float("nan"))
    k = np.floor(np.mod(d + SECTOR / 2, 360.0) / SECTOR).astype(np.int64)
    sums = np.bincount(k, weights=w, minlength=N_SECTORS)
    best = int(np.argmax(sums))  # ties: the lowest sector index
    rad = np.radians(d)
    m = k == best
    mean = np.degrees(
        np.arctan2((w[m] * np.sin(rad[m])).sum(), (w[m] * np.cos(rad[m])).sum())
    )
    r = np.hypot((w * np.sin(rad)).sum(), (w * np.cos(rad)).sum()) / w.sum()
    return Dominant(float(np.mod(mean, 360.0)), float(sums[best] / w.sum()), float(r))


def attack_angle(direction: FloatArray | float, azimuth: float) -> FloatArray:
    """Angle between the wind and the conductor axis, folded to 0-90 deg
    (a direction and its opposite, and the two span ends, are the same)."""
    a = np.mod(np.abs(np.asarray(direction, dtype=float) - azimuth), 180.0)
    return np.where(a > 90.0, 180.0 - a, a)


@dataclass
class AngleTable:
    """Per point, month (0-11) x local hour (0-23) tables."""

    direction: FloatArray  # (points, 12, 24)
    share: FloatArray
    r: FloatArray


def month_hour_dominant(
    local_times: Sequence[datetime], speed: FloatArray, direction: FloatArray
) -> AngleTable:
    """Dominant direction per point and local (month, hour).

    ``speed`` / ``direction``: (hours, points), rows on ``local_times``."""
    n = speed.shape[1]
    out = AngleTable(*(np.full((n, 12, 24), np.nan) for _ in range(3)))
    month = np.array([t.month - 1 for t in local_times])
    hour = np.array([t.hour for t in local_times])
    for mo in range(12):
        for h in range(24):
            rows = (month == mo) & (hour == h)
            if not rows.any():
                continue
            for j in range(n):
                dm = dominant_direction(speed[rows, j], direction[rows, j])
                out.direction[j, mo, h] = dm.direction
                out.share[j, mo, h] = dm.share
                out.r[j, mo, h] = dm.r
    return out


def angle_table(table: AngleTable, azimuths: Sequence[float]) -> FloatArray:
    """Attack angle per point, month and hour: (points, 12, 24)."""
    return np.stack(
        [attack_angle(table.direction[j], az) for j, az in enumerate(azimuths)]
    )


def wind_direction_series(
    local_times: Sequence[datetime],
    phi: FloatArray,
    azimuths: Sequence[float],
) -> FloatArray:
    """(hours, points) wind direction = azimuth + phi(month, local hour),
    so that the rating sees the attack angle phi."""
    month = np.array([t.month - 1 for t in local_times])
    hour = np.array([t.hour for t in local_times])
    out = np.empty((len(local_times), len(azimuths)))
    for j, az in enumerate(azimuths):
        p = phi[j][month, hour]
        if np.isnan(p).any():
            raise ValueError(
                "wind angle: the Eolico direction climatology has no "
                "(month, hour) cell for some target hours"
            )
        out[:, j] = np.mod(az + p, 360.0)
    return out
