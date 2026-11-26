"""Curtailable renewables: no PLEXOS Min Stable Level / forced-ON floor.

CEN PCP bundles give hundreds of (near) zero-cost solar, wind and run-of-river
units a ``Gen_MinStableLevel`` (e.g. ``TAMARICO_FV``: 5.17 MW, clamped to
its first positive rating 0.43 MW) and some a ``Gen_Commit`` = +1 (forced
ON) profile.  On a unit whose energy is free, a "committed" floor only
matters when the unit would rather curtail: it forces injection at a bus
whose price is negative and so manufactures the negative LMP it is
supposed to explain (PCP 2025-06-26 h16: ``TAMARICO_FV`` held at 0.43 MW
with a 0.64 $/MWh floor dual, λ = −0.63 at Maitencillo220).

``--renewable-floors`` selects the treatment:

* ``free`` (default, clean): a solar / wind / run-of-river
  commitment keeps every other datum (status column, forced-OFF blocks,
  references from user constraints) but loses its ``pmin`` /
  ``pmin_profile`` and its forced-ON (+1) periods, which become free
  (-1).  The unit can always curtail to 0.  A PLEXOS ``Fixed Load`` on a
  zero-cost renewable is already a curtailable cap (``gtopt_writer``), so
  no floor survives.
* ``plexos`` (``--legacy-profile cen``): PLEXOS's Min Stable Level and
  Commit forcing are kept as given.

Scope: ``origin == "generator"`` commitments (never the hydro
``<plant>min`` / ``<plant>max`` constraint commitments) of generators
whose type tag is ``renewable:solar`` / ``renewable:wind``, or
``renewable:hydro`` without a turbine drawing from a reservoir with
storage (``emax > 0``) — run-of-river.  Storage hydro and concentrated
solar (``*_CS``) keep their turbine minimum, and a unit with a
start-up / shut-down or no-load cost is left alone
(:func:`has_no_on_off_cost`): its status ``u`` carries a price.
A small energy cost (CEN ships 0.1 $/MWh VO&M on some PV) does not make
a unit a must-take, so it stays in scope.  Ramp limits and minimum
up / down times stay on the commitment: they constrain changes, not the
level, and with ``pmin = 0`` the unit can still reach 0.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from ._commitment_policy import ZERO_COST_EPS
from .entities import CommitmentSpec, GeneratorSpec, PlexosCase

logger = logging.getLogger(__name__)

#: ``--renewable-floors`` choices; the first is the clean default.
RENEWABLE_FLOOR_MODES: tuple[str, ...] = ("free", "plexos")

#: Variable-renewable families (always in scope).  The parser tags a
#: generator with its bare family (``"solar"``); the writer composes the
#: ``"renewable:<family>"`` JSON type — both spellings are accepted.
_VRE = frozenset({"solar", "wind"})
_HYDRO = "hydro"
#: CEN suffix of concentrated-solar (CSP) units (``CERRO_DOMINADOR_CS``):
#: a steam turbine fed by thermal storage, whose Min Stable Level is a
#: real turbine minimum — kept, like storage hydro.
_CSP_SUFFIX = "_CS"


def storage_hydro_generators(case: PlexosCase) -> frozenset[str]:
    """Generators with a turbine whose head reservoir has storage."""
    with_storage = {r.name for r in case.reservoirs if r.emax > 0.0}
    return frozenset(
        t.generator_name for t in case.turbines if t.reservoir_name in with_storage
    )


def curtailable_kind(gen: GeneratorSpec, storage_gens: Iterable[str]) -> str | None:
    """``"solar"`` / ``"wind"`` / ``"ror"`` for an in-scope unit, else None."""
    tag = gen.type_tag.removeprefix("renewable:")
    if gen.name.endswith(_CSP_SUFFIX):
        return None  # concentrated solar: steam turbine on a heat store
    if tag in _VRE:
        return tag
    if tag == _HYDRO and gen.name not in storage_gens:
        return "ror"
    return None


def has_no_on_off_cost(c: CommitmentSpec) -> bool:
    """True for a generator commitment whose status carries no cost."""
    if c.origin != "generator":
        return False
    on_off = (
        c.startup_cost,
        c.shutdown_cost,
        max(c.startup_cost_profile, default=0.0),
        max(c.shutdown_cost_profile, default=0.0),
        c.noload_cost,
        c.noload_fuel,
    )
    return all(v <= ZERO_COST_EPS for v in on_off)


@dataclass
class RenewableFloorReport:
    """What :func:`free_renewable_floors` removed, per unit kind."""

    pmin_dropped: dict[str, list[str]] = field(default_factory=dict)
    forced_on_dropped: dict[str, list[str]] = field(default_factory=dict)

    def counts(self) -> dict[str, dict[str, int]]:
        """``{"pmin": {kind: n}, "forced_on": {kind: n}}``."""
        return {
            "pmin": {k: len(v) for k, v in sorted(self.pmin_dropped.items())},
            "forced_on": {k: len(v) for k, v in sorted(self.forced_on_dropped.items())},
        }


def free_renewable_floors(
    case: PlexosCase, mode: str = "free"
) -> tuple[PlexosCase, RenewableFloorReport]:
    """Drop Min Stable Level / forced-ON floors of curtailable renewables.

    Raises:
        ValueError: on an unknown *mode*.
    """
    if mode not in RENEWABLE_FLOOR_MODES:
        raise ValueError(
            f"unknown --renewable-floors mode {mode!r}; "
            f"expected {RENEWABLE_FLOOR_MODES}"
        )
    report = RenewableFloorReport()
    if mode == "plexos" or not case.commitments:
        return case, report
    gen_by_name = {g.name: g for g in case.generators}
    storage_gens = storage_hydro_generators(case)
    out = []
    changed = False
    for c in case.commitments:
        gen = gen_by_name.get(c.generator_name)
        kind = curtailable_kind(gen, storage_gens) if gen is not None else None
        if kind is None or not has_no_on_off_cost(c):
            out.append(c)
            continue
        updates: dict[str, object] = {}
        if c.pmin > 0.0 or any(v > 0.0 for v in c.pmin_profile):
            updates["pmin"] = 0.0
            updates["pmin_profile"] = ()
            report.pmin_dropped.setdefault(kind, []).append(c.generator_name)
        if any(v == 1 for v in c.commit_status_profile):
            prof = tuple(-1 if v == 1 else v for v in c.commit_status_profile)
            updates["commit_status_profile"] = prof if any(v == 0 for v in prof) else ()
            report.forced_on_dropped.setdefault(kind, []).append(c.generator_name)
        if updates:
            changed = True
            out.append(replace(c, **updates))  # type: ignore[arg-type]
        else:
            out.append(c)
    counts = report.counts()
    if counts["pmin"] or counts["forced_on"]:
        logger.info(
            "renewable floors freed (clean; --renewable-floors plexos keeps "
            "them): Min Stable Level dropped %s, forced-ON dropped %s",
            counts["pmin"],
            counts["forced_on"],
        )
    if not changed:
        return case, report
    return replace(case, commitments=tuple(out)), report
