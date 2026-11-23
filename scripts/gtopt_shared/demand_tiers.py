# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Pure helpers to split a demand into per-tier unserved-energy siblings.

gtopt prices unserved energy with a single ``Demand.fcost``.  Source
models that carry a *deficit cost curve* — PLP's ``FALLA_<bus>_k``
centrals (``plp2gtopt --falla-segments``) and PSR SDDP's ``Costo
Deficit`` segments (``sddp2gtopt --deficit-segments``) — are mapped,
in ``all`` mode, to one sibling demand per tier:

* name ``<demand>_<tag><k>`` (``k`` 1-based, ``tag`` = ``falla`` /
  ``deficit``),
* ``lmax`` = ``f_k`` x original ``lmax`` with ``sum(f_k) == 1`` (the last
  tier absorbs the rounding residual),
* ``fcost`` = tier ``k`` cost,
* uid ``u + (k - 1) * offset`` with ``offset`` a power of ten above every
  existing uid, so tier 1 keeps the original uid.

Modes (``--falla-segments`` / ``--deficit-segments``):

* ``first`` (default) keeps a single demand priced at the first tier;
* ``all`` emits the native stepwise curve ``Demand.fail_segments``
  (``[{fraction, cost}, …, {cost}]``, last segment open — see
  :func:`fail_segments`) on a single demand per bus;
* ``split`` keeps the legacy sibling-demand split described above.

Everything here is I/O-free so each converter keeps its own source
parsing.
"""

from __future__ import annotations

from typing import Any, Dict, Final, List, Sequence, Tuple

__all__ = [
    "DEFAULT_TIER_SEGMENTS",
    "TIER_SEGMENT_CHOICES",
    "fail_segments",
    "pct_fractions",
    "split_fractions",
    "tier_name",
    "tier_uid",
    "tier_uid_offset",
]

#: CLI choices shared by ``--falla-segments`` / ``--deficit-segments``.
TIER_SEGMENT_CHOICES: Final[Tuple[str, ...]] = ("first", "all", "split")
#: Default mode — a single demand priced at the first tier.
DEFAULT_TIER_SEGMENTS: Final[str] = "first"


def tier_name(name: str, k: int, tag: str = "falla") -> str:
    """Return the sibling-demand name for tier *k* of demand *name*."""
    return f"{name}_{tag}{k}"


def tier_uid_offset(max_uid: int) -> int:
    """Return a power of ten strictly greater than *max_uid*.

    Tier ``k`` of a demand with uid ``u`` gets ``u + (k - 1) * offset``,
    so tier 1 keeps the original uid and the other tiers never collide
    with any uid ``<= max_uid``.
    """
    return int(10 ** len(str(max(int(max_uid), 1))))


def tier_uid(uid: int, k: int, offset: int) -> int:
    """Return the uid of tier *k* (1-based) for a demand with *uid*."""
    return int(uid) + (k - 1) * offset


def split_fractions(capacities: Sequence[float]) -> List[float]:
    """Normalise per-tier capacities to fractions summing to 1.

    The last fraction absorbs the rounding residual so the siblings
    reproduce the original demand exactly.
    """
    total = float(sum(capacities))
    if total <= 0.0:
        raise ValueError("tier capacities must sum to a positive value")
    fracs = [float(c) / total for c in capacities]
    fracs[-1] = 1.0 - sum(fracs[:-1])
    return fracs


def pct_fractions(pcts: Sequence[float]) -> List[float]:
    """Turn tier depths in percent of the demand into fractions.

    ``f_k = pct_k / 100`` for every tier but the last, which takes the
    remainder ``1 - sum(f_1 .. f_{n-1})`` — the deepest deficit segment
    is open-ended, so the siblings always add up to the full demand
    whatever the last depth says.  Raises ``ValueError`` when a depth is
    not positive or the leading depths already cover 100 % (the last
    tier would be empty).
    """
    if not pcts:
        raise ValueError("no tier depths given")
    if any(float(p) <= 0.0 for p in pcts):
        raise ValueError(f"tier depths must be positive: {list(pcts)}")
    fracs = [float(p) / 100.0 for p in pcts[:-1]]
    last = 1.0 - sum(fracs)
    if last <= 1e-12:
        raise ValueError(f"leading tier depths {list(pcts[:-1])} already cover 100 %")
    fracs.append(last)
    return fracs


def fail_segments(
    fractions: Sequence[float], costs: Sequence[Any]
) -> List[Dict[str, Any]]:
    """Build a native ``Demand.fail_segments`` list from tier data.

    Every segment but the last carries its ``fraction``; the last one is
    left open (``{"cost": c}``) so it covers the residual share exactly —
    fractions from :func:`split_fractions` / :func:`pct_fractions` sum to
    1 by construction.  *costs* may hold scalars or per-stage schedules
    (a list or a ``Demand/`` filename stem).
    """
    if len(fractions) != len(costs) or not fractions:
        raise ValueError(
            "fail_segments: fractions and costs must be non-empty and equal-length"
        )
    segs: List[Dict[str, Any]] = [
        {"fraction": float(f), "cost": c} for f, c in zip(fractions[:-1], costs[:-1])
    ]
    segs.append({"cost": costs[-1]})
    return segs
