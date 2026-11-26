# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Time basis of the local calendar (month / day / hour of every UTC hour).

The local calendar decides which historical hours form a month or an
hour-of-day bin and how typical hours map to blocks. PLP block maps
(``indhor``) are in **fixed local standard time** (Chile: UTC−4 all year,
no daylight saving). That is the default (``--dst off``). ``--dst on``
follows the civil clock of the zone (America/Santiago: UTC−3 in summer).

``--timezone auto`` derives the zone from the coordinates, with the
optional ``timezonefinder`` package when installed, else a small
built-in bounding-box table. ``--utc-offset`` overrides everything with
a fixed offset in hours.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DST_MODES = ("off", "on")

# (zone, lat_min, lat_max, lon_min, lon_max): first match wins
TZ_BOXES: tuple[tuple[str, float, float, float, float], ...] = (
    # Magallanes region: fixed UTC−3 since 2017
    ("America/Punta_Arenas", -56.5, -48.6, -76.0, -66.0),
    # mainland Chile (Arica to Aysén)
    ("America/Santiago", -48.6, -17.4, -76.0, -66.0),
    # Easter Island
    ("Pacific/Easter", -27.8, -26.4, -110.0, -108.5),
)


def zone_from_coords(lats: Sequence[float], lons: Sequence[float]) -> str:
    """IANA zone of the points (all must agree); ``timezonefinder`` when
    installed, else :data:`TZ_BOXES`.  Raises with a hint to pass
    ``--timezone`` when unknown or ambiguous."""
    if not lats:
        raise ValueError("--timezone auto: no coordinates; pass --timezone")
    found = [_zone_of(la, lo) for la, lo in zip(lats, lons)]
    if any(z is None for z in found):
        raise ValueError(
            "--timezone auto: coordinates outside the known zones; pass "
            "--timezone (e.g. America/Santiago) or --utc-offset"
        )
    zones = {z for z in found if z is not None}
    if len(zones) > 1:
        raise ValueError(
            f"--timezone auto: points span several zones {sorted(zones)}; "
            "pass --timezone or --utc-offset"
        )
    return str(zones.pop())


def _zone_of(lat: float, lon: float) -> str | None:
    try:
        from timezonefinder import (  # pylint: disable=import-outside-toplevel
            TimezoneFinder,
        )
    except ImportError:
        TimezoneFinder = None  # noqa: N806
    if TimezoneFinder is not None:
        z = TimezoneFinder().timezone_at(lat=lat, lng=lon)
        if z:
            return str(z)
    for name, la0, la1, lo0, lo1 in TZ_BOXES:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return None


def standard_offset(zone: str, year: int = 2020) -> float:
    """Standard-time (non-DST) UTC offset of ``zone`` [h]: the offset of
    the January / July instant that is not daylight saving."""
    z = ZoneInfo(zone)
    for month in (7, 1):
        t = datetime(year, month, 1, 12, tzinfo=z)
        dst = t.dst() or timedelta(0)
        if dst == timedelta(0):
            off = t.utcoffset() or timedelta(0)
            return off.total_seconds() / 3600.0
    t = datetime(year, 1, 1, 12, tzinfo=z)
    off = (t.utcoffset() or timedelta(0)) - (t.dst() or timedelta(0))
    return off.total_seconds() / 3600.0


@dataclass(frozen=True)
class TimeBasis:
    """Fixed offset (``zone`` None) or a civil zone following DST."""

    utc_offset: float = 0.0
    zone: str | None = None

    def local(self, times: Sequence[datetime]) -> list[datetime]:
        """Naive local times of naive-UTC ``times``."""
        if self.zone is None:
            shift = timedelta(hours=self.utc_offset)
            return [t + shift for t in times]
        z = ZoneInfo(self.zone)
        return [
            t.replace(tzinfo=timezone.utc).astimezone(z).replace(tzinfo=None)
            for t in times
        ]

    @property
    def spec(self) -> float | str:
        """What ``local_calendar`` takes: the offset or the zone name."""
        return self.zone if self.zone is not None else self.utc_offset

    def describe(self) -> str:
        """Human text for logs and reports."""
        if self.zone is not None:
            return f"{self.zone} (civil time, DST)"
        return f"UTC{self.utc_offset:+g} (fixed)"


def resolve(
    timezone_name: str | None,
    dst: str,
    utc_offset: float | None,
    lats: Sequence[float] = (),
    lons: Sequence[float] = (),
) -> TimeBasis:
    """The time basis from the CLI options: ``utc_offset`` (explicit,
    fixed) wins; else the zone (``auto`` = from the coordinates) at its
    standard offset (``dst`` off) or its civil clock (``dst`` on)."""
    if dst not in DST_MODES:
        raise ValueError(f"--dst must be one of {DST_MODES}")
    if utc_offset is not None:
        return TimeBasis(float(utc_offset))
    name = timezone_name or "auto"
    if name == "auto":
        name = zone_from_coords(list(lats), list(lons))
    if name.upper() == "UTC":
        return TimeBasis(0.0)
    ZoneInfo(name)  # validates the name
    if dst == "on":
        return TimeBasis(standard_offset(name), name)
    return TimeBasis(standard_offset(name))


__all__ = [
    "DST_MODES",
    "TZ_BOXES",
    "TimeBasis",
    "resolve",
    "standard_offset",
    "zone_from_coords",
]
