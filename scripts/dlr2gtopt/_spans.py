# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Towers -> spans: midpoint, axis azimuth, length and chainage per span.

A span joins two consecutive towers of one circuit.  The azimuth is the
initial great-circle bearing folded to [0, 180) deg: a span has an axis, not
a direction, and gtopt's attack angle is symmetric under a 180 deg turn.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Tower:
    """One tower of a line, in route order (``seq``)."""

    line: str
    seq: int
    lat: float
    lon: float
    elevation: float | None = None
    # Structure type as written in the structure list (e.g. "22AD+3"), and
    # the conductor attachment height above ground [m] when known; both
    # optional, used by :mod:`dlr2gtopt._heights`.
    structure_type: str | None = None
    attachment: float | None = None


@dataclass(frozen=True)
class Span:
    """One span between towers ``seq`` and ``seq + 1`` of ``line``."""

    line: str
    span: int
    lat: float
    lon: float
    azimuth: float
    length_m: float
    chainage_km: float
    elevation: float | None = None
    # Effective conductor height above ground [m] for this span (optional;
    # used by ``weather --height-source spans``).
    height: float | None = None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance [m]."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2 [deg from North, 0-360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def route_towers(
    towers: list[Tower], min_length_m: float = 1.0
) -> dict[str, list[Tower]]:
    """Towers of each line in route order, with consecutive towers closer
    than ``min_length_m`` merged (span ``k`` joins towers ``k-1`` and ``k``
    of the returned list)."""
    by_line: dict[str, list[Tower]] = {}
    for t in towers:
        by_line.setdefault(t.line, []).append(t)
    out: dict[str, list[Tower]] = {}
    for line, ts in by_line.items():
        ordered = sorted(ts, key=lambda t: t.seq)
        kept = [ordered[0]]
        for t in ordered[1:]:
            if haversine_m(kept[-1].lat, kept[-1].lon, t.lat, t.lon) >= min_length_m:
                kept.append(t)
        out[line] = kept
    return out


def build_spans(towers: list[Tower], min_length_m: float = 1.0) -> list[Span]:
    """Spans of each line from its towers (sorted by ``seq``).

    Consecutive towers closer than ``min_length_m`` (duplicated structures,
    e.g. two circuits listed on one tower) are merged.
    """
    out: list[Span] = []
    for line, kept in route_towers(towers, min_length_m).items():
        chain = 0.0
        for k, (a, b) in enumerate(zip(kept, kept[1:]), start=1):
            length = haversine_m(a.lat, a.lon, b.lat, b.lon)
            elev = (
                None
                if a.elevation is None or b.elevation is None
                else (a.elevation + b.elevation) / 2
            )
            out.append(
                Span(
                    line=line,
                    span=k,
                    lat=(a.lat + b.lat) / 2,
                    lon=(a.lon + b.lon) / 2,
                    azimuth=bearing_deg(a.lat, a.lon, b.lat, b.lon) % 180.0,
                    length_m=length,
                    chainage_km=(chain + length / 2) / 1000.0,
                    elevation=elev,
                )
            )
            chain += length
    return out


TOWER_FIELDS = (
    "line",
    "tower_seq",
    "lat",
    "lon",
    "elevation",
    "type",
    "attachment",
)
SPAN_FIELDS = (
    "line",
    "span",
    "lat",
    "lon",
    "azimuth",
    "length_m",
    "chainage_km",
    "elevation",
    "height",
)


def _opt_float(s: str | None) -> float | None:
    return None if s is None or s.strip() == "" else float(s)


def read_towers(path: Path, line: str | None = None) -> list[Tower]:
    """Towers CSV ``line,tower_seq,lat,lon[,elevation,type,attachment]``
    (``line`` optional when given as argument)."""
    out: list[Tower] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("line") or line
            if not name:
                raise ValueError(f"{path}: no 'line' column and no --line given")
            out.append(
                Tower(
                    line=name,
                    seq=int(row["tower_seq"]),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    elevation=_opt_float(row.get("elevation")),
                    structure_type=(row.get("type") or "").strip() or None,
                    attachment=_opt_float(row.get("attachment")),
                )
            )
    return out


def write_towers(path: Path, towers: list[Tower]) -> None:
    """Write the towers CSV (the format :func:`read_towers` reads)."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TOWER_FIELDS)
        for t in towers:
            w.writerow(
                [
                    t.line,
                    t.seq,
                    f"{t.lat:.7f}",
                    f"{t.lon:.7f}",
                    "" if t.elevation is None else t.elevation,
                    t.structure_type or "",
                    "" if t.attachment is None else t.attachment,
                ]
            )


def write_spans(path: Path, spans: list[Span]) -> None:
    """Write the spans CSV."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SPAN_FIELDS)
        for s in spans:
            w.writerow(
                [
                    s.line,
                    s.span,
                    f"{s.lat:.7f}",
                    f"{s.lon:.7f}",
                    f"{s.azimuth:.2f}",
                    f"{s.length_m:.1f}",
                    f"{s.chainage_km:.4f}",
                    "" if s.elevation is None else f"{s.elevation:.1f}",
                    "" if s.height is None else f"{s.height:.2f}",
                ]
            )


def read_spans(path: Path) -> list[Span]:
    """Read a spans CSV written by :func:`write_spans`."""
    with path.open(newline="", encoding="utf-8") as f:
        return [
            Span(
                line=row["line"],
                span=int(row["span"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                azimuth=float(row["azimuth"]),
                length_m=float(row["length_m"]),
                chainage_km=float(row["chainage_km"]),
                elevation=_opt_float(row.get("elevation")),
                height=_opt_float(row.get("height")),
            )
            for row in csv.DictReader(f)
        ]
