# -*- coding: utf-8 -*-

"""Split PLP demands into per-tier "falla" (load-shedding) segments.

PLP models unserved energy at each bus with several *falla* centrals
(``FALLA_<bus>_1 .. FALLA_<bus>_4`` in the CEN cases), each a fictitious
generator whose ``plpcnfce.dat`` ``CosVar`` is the tier cost and whose
``plpmance.dat`` ``PotMax`` schedule is a fixed fraction of the bus
demand (CEN: 5 % / 5 % / 10 % / 80 %).  gtopt has a single ``fcost``
per demand, so by default (``--falla-segments first``) only the
cheapest tier survives as the demand ``fcost``.

With ``--falla-segments all`` each demand on a bus with two or more
falla tiers is replaced by one *sibling demand per tier*:

* name ``<demand>_falla<k>`` (``k`` = 1 for the cheapest tier),
* ``lmax`` = ``f_k`` x original ``lmax`` (same for ``emin``),
* ``fcost`` = tier ``k`` cost (``CosVar`` or its ``plpcosce`` schedule),

where ``f_k = sum(PotMax_k) / sum_j sum(PotMax_j)`` over the tier
``plpmance`` schedules.  The fractions are normalised so the siblings
add up exactly to the original demand, and the curtailment of each
tier stays in the standard ``Demand`` fail output.

The pure naming / uid / fraction helpers live in
:mod:`gtopt_shared.demand_tiers` (shared with ``sddp2gtopt
--deficit-segments``) and are re-exported here for compatibility.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from gtopt_shared.demand_tiers import (
    DEFAULT_TIER_SEGMENTS,
    TIER_SEGMENT_CHOICES,
    split_fractions,
    tier_name,
    tier_uid,
    tier_uid_offset,
)

__all__ = [
    "DEFAULT_FALLA_SEGMENTS",
    "FALLA_SEGMENT_CHOICES",
    "FallaTier",
    "build_falla_tiers",
    "split_fractions",
    "tier_name",
    "tier_uid",
    "tier_uid_offset",
]

_logger = logging.getLogger(__name__)

#: CLI choices for ``--falla-segments`` (shared with sddp2gtopt).
FALLA_SEGMENT_CHOICES: Tuple[str, ...] = TIER_SEGMENT_CHOICES
#: Default mode — keeps the historical single-``fcost`` output.
DEFAULT_FALLA_SEGMENTS = DEFAULT_TIER_SEGMENTS


@dataclass(frozen=True)
class FallaTier:
    """One failure tier of a bus: its PLP central and demand fraction."""

    k: int
    """1-based tier index, ordered by increasing cost."""
    central: Dict[str, Any]
    """The PLP ``falla`` central (``name``, ``gcost``, ``bus``, ...)."""
    fraction: float
    """Share of the bus demand covered by this tier (sums to 1)."""


def build_falla_tiers(
    centrals: Iterable[Dict[str, Any]],
    mance_parser: Any,
) -> Dict[int, List[FallaTier]]:
    """Group PLP falla centrals into per-bus tiers with demand fractions.

    Tier capacity is the sum of the central's ``plpmance.dat`` ``pmax``
    schedule (PLP's ``plpcnfce.dat`` ``PotMax`` is the 9999 placeholder).
    Buses with a single falla central are left out (nothing to split);
    buses whose tiers lack a usable ``plpmance`` schedule are left out
    with a warning and keep the ``first`` behaviour.
    """
    per_bus: Dict[int, List[Dict[str, Any]]] = {}
    for central in centrals:
        if central.get("type") != "falla":
            continue
        bus = int(central.get("bus", 0) or 0)
        if bus <= 0:
            continue
        per_bus.setdefault(bus, []).append(central)

    tiers: Dict[int, List[FallaTier]] = {}
    for bus, fallas in sorted(per_bus.items()):
        if len(fallas) < 2:
            continue
        fallas = sorted(
            fallas, key=lambda c: (float(c.get("gcost", 0.0)), str(c.get("name")))
        )
        caps: List[float] = []
        for central in fallas:
            mance = (
                mance_parser.get_mance_by_name(central.get("name", ""))
                if mance_parser is not None
                else None
            )
            cap = float(sum(mance["pmax"])) if mance is not None else 0.0
            if cap <= 0.0:
                break
            caps.append(cap)
        if len(caps) != len(fallas):
            _logger.warning(
                "falla-segments: bus %d has %d falla tiers but no plpmance "
                "capacity for all of them; keeping the first-tier fcost",
                bus,
                len(fallas),
            )
            continue
        fracs = split_fractions(caps)
        tiers[bus] = [
            FallaTier(k=i + 1, central=c, fraction=f)
            for i, (c, f) in enumerate(zip(fallas, fracs))
        ]
        _logger.debug(
            "falla-segments: bus %d -> %s",
            bus,
            ", ".join(
                f"{t.central.get('name')}={t.fraction:.3f}@{t.central.get('gcost')}"
                for t in tiers[bus]
            ),
        )
    return tiers
