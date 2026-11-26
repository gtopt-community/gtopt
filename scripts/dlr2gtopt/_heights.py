# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-span effective conductor height for the wind interpolation.

    h_eff = (h_a + h_b) / 2 - (2/3) * sag

``h_a``, ``h_b`` are the attachment heights of the representative phase at
the two towers of the span and ``sag`` the span's sag at the design
temperature (the mean height of a parabola is its chord minus 2/3 of the
sag).  With ground elevations at the towers and at mid-span, the height is
taken above the mid-span ground instead of flat terrain, bounded to
``[flat - 10, flat + 25]`` m and never below 5 m (a 90 m DEM cannot resolve
the ground under the conductor better than that).

Attachment, by tower, in this order (the ``src`` of each span records it):

``list``        the tower's own ``attachment`` (structure list column)
``memo``        tower geometry of the line's design report (memoria de
                calculo): cross-arm height of the phase for the tower's body
                extension, minus the suspension string for suspension
                families (``memo_strain``: strain/dead-end families, no
                string drop; ``memo_family``: a suspension family other than
                the one the cross-arms were given for)
``memo_weighted`` the report's structure-weighted cross-arm height (tower
                type unknown)
``class``       typical attachment of the voltage class (line defaults)

Sag, per span: the report's sag at the ruling span ``F`` scaled as
``F (L/RS)^2`` with ``RS = sqrt(sum L^3 / sum L)`` over the line; or the
catenary parameter ``C`` as ``L^2 / (8 C)``; or, without either, the class
sag (attachment minus mean height of the class, times 3/2) at the ruling
span.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._spans import Span, Tower

PHASES = ("low", "mid", "high")
_FAMILY = re.compile(r"^\s*(?:\d{2})?([A-Z]+)")
_EXTENSION = re.compile(r"([+\-±])\s*(\d+(?:[.,]\d+)?)")


@dataclass(frozen=True)
class TowerGeometry:
    """Line design data (memoria de calculo); every field optional.

    ``crossarm`` maps a body extension [m] to the cross-arm heights of the
    phases bottom to top; other extensions are shifted linearly from the
    nearest given one (+1 m body, +1 m cross-arm)."""

    crossarm: Mapping[float, Sequence[float]] = field(default_factory=dict)
    reference_family: str | None = None
    families: Mapping[str, str] = field(default_factory=dict)
    string_m: float = 0.0
    weighted: Sequence[float] | None = None
    ruling_sag_m: float | None = None
    catenary_m: float | None = None
    source: str = ""

    @classmethod
    def from_json(cls, path: Path) -> TowerGeometry:
        """Read the JSON form (see the README)."""
        d: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            crossarm={float(k): tuple(v) for k, v in d.get("crossarm", {}).items()},
            reference_family=d.get("reference_family"),
            families=dict(d.get("families", {})),
            string_m=float(d.get("string_m", 0.0)),
            weighted=tuple(d["weighted"]) if d.get("weighted") else None,
            ruling_sag_m=d.get("ruling_sag_m"),
            catenary_m=d.get("catenary_m"),
            source=str(d.get("source", "")),
        )


def parse_structure_type(text: str | None) -> tuple[str | None, float | None]:
    """Tower family and body extension from a structure-list type code.

    The family is the leading letters after an optional two-digit voltage
    prefix; the extension is the first signed number after it, 0 if none
    (``"22AD+3"`` -> ``("AD", 3.0)``, ``"22BD.2-3"`` -> ``("BD", -3.0)``,
    ``"22FD B-3 P+0"`` -> ``("FD", -3.0)``, ``"22AD±0"`` -> ``("AD", 0.0)``).
    """
    if not text:
        return None, None
    up = str(text).upper()
    m = _FAMILY.match(up)
    if not m:
        return None, None
    e = _EXTENSION.search(up, m.end())
    if e is None or e.group(1) == "±":
        return m.group(1), 0.0
    ext = float(e.group(2).replace(",", "."))
    return m.group(1), ext if e.group(1) == "+" else -ext


def _phase_index(n: int, phase: str) -> int:
    return {"low": 0, "mid": n // 2, "high": n - 1}[phase]


def tower_attachment(
    t: Tower, geo: TowerGeometry | None, phase: str, class_attach: float
) -> tuple[float, str]:
    """Attachment height of ``phase`` at tower ``t`` and its source."""
    if t.attachment is not None:
        return t.attachment, "list"
    if geo is not None and geo.crossarm:
        fam, ext = parse_structure_type(t.structure_type)
        kind = geo.families.get(fam or "", "")
        if fam is not None and ext is not None and kind in ("suspension", "strain"):
            near = min(geo.crossarm, key=lambda e: abs(e - ext))
            ys = geo.crossarm[near]
            y = ys[_phase_index(len(ys), phase)] + (ext - near)
            if kind == "strain":
                return y, "memo_strain"
            src = "memo" if fam == geo.reference_family else "memo_family"
            return y - geo.string_m, src
    if geo is not None and geo.weighted:
        ys = geo.weighted
        return ys[_phase_index(len(ys), phase)] - geo.string_m, "memo_weighted"
    return class_attach, "class"


@dataclass(frozen=True)
class SpanHeight:
    """One span's height and how it was obtained."""

    span: int
    height: float
    src: str
    sag_m: float
    sag_src: str
    h_flat: float
    h_terrain: float | None
    clearance: float | None


def line_heights(
    towers: Sequence[Tower],
    spans: Sequence[Span],
    *,
    geometry: TowerGeometry | None = None,
    phase: str = "mid",
    class_attach: float = 24.0,
    class_height: float = 16.0,
    midspan_ground: Mapping[int, float] | None = None,
) -> list[SpanHeight]:
    """Heights of the spans of one line; ``towers`` in route order with
    ``len(towers) == len(spans) + 1`` (see :func:`route_towers`)."""
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    if len(towers) != len(spans) + 1:
        raise ValueError("heights: need one more tower than spans")
    lengths = [s.length_m for s in spans]
    rs = math.sqrt(sum(x**3 for x in lengths) / max(sum(lengths), 1e-9))
    if geometry is not None and geometry.ruling_sag_m:
        f_rs, sag_src = geometry.ruling_sag_m, "memo_ruling"
    elif geometry is not None and geometry.catenary_m:
        f_rs, sag_src = rs**2 / (8 * geometry.catenary_m), "catenary"
    else:
        f_rs, sag_src = 1.5 * (class_attach - class_height), "class"
    att = [tower_attachment(t, geometry, phase, class_attach) for t in towers]
    out: list[SpanHeight] = []
    for k, s in enumerate(spans):
        sag = f_rs * (s.length_m / rs) ** 2 if rs > 0 else 0.0
        (ya, sa), (yb, sb) = att[k], att[k + 1]
        src = sa if sa == sb else f"{sa}|{sb}"
        flat = (ya + yb) / 2 - 2.0 / 3.0 * sag
        za, zb = towers[k].elevation, towers[k + 1].elevation
        zm = None if midspan_ground is None else midspan_ground.get(s.span)
        if za is None or zb is None or zm is None:
            h_t, clear, h = None, None, flat
        else:
            chord = (za + ya + zb + yb) / 2
            h_t = chord - 2.0 / 3.0 * sag - zm
            clear = chord - sag - zm
            h = min(max(h_t, flat - 10.0), flat + 25.0)
        out.append(
            SpanHeight(
                span=s.span,
                height=max(h, 5.0),
                src=src,
                sag_m=sag,
                sag_src=sag_src,
                h_flat=flat,
                h_terrain=h_t,
                clearance=clear,
            )
        )
    return out
