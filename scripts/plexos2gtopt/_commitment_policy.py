"""Which PLEXOS commitments ship as integer ``u`` and which are LP-relaxed.

A gtopt ``Commitment`` adds one binary status column ``u`` per block.  On
the CEN PCP bundles most of them are economically meaningless: of the 1100
commitments of PCP 2026-04-22, 915 have free ``u`` cells and 617 of those
belong to zero-cost solar / wind / run-of-river units whose only UC datum
is a ``Min Stable Level``.  Their binaries cost the MIP a lot (branching,
heuristics, a weaker root bound) and buy nothing: with no start-up cost,
no minimum up / down time, no ramp and a zero energy cost, switching the
unit off is never worth anything, and the semi-continuous region
``(0, Min Stable Level)`` only matters when the unit is curtailed — which a
zero-cost unit is anyway free to do down to 0.

The rule (:func:`commitment_relax_reason`) emits ``Commitment.relax =
true`` — ``u`` (and ``v`` / ``w``) continuous in ``[0, 1]``, i.e. the
commitment's LINEAR RELAXATION — when either:

1. **PLEXOS solves the unit as a linear relaxation** — the run Model's
   effective ``Generator.Unit Commitment Optimality`` is ``0`` (Linear
   Relaxation).  This is exactly PLEXOS's own semantics for the unit:
   ``Units Generating`` is a continuous variable and the Min Stable Level
   row ``gen >= MSL * units`` relaxes with it.  On PCP 2026-04-22, 816
   generators carry it (untagged) in ``PRGdia_Full_Definitivo``, among
   them 532 of the zero-cost units above.

2. **The commitment is cost-free** (``auto`` mode only) — ALL of:

   * zero energy cost: ``VO&M + fuel transport + heat rate x fuel price``
     (maximum over the heat-rate / fuel-price profiles, piecewise segments
     included) ``<= ZERO_COST_EPS`` $/MWh;
   * no start-up / shut-down cost in any period, no no-load cost and no
     no-load fuel burn;
   * no minimum up / down time and no start-count limit;
   * no ramp limit (``Max Ramp Up/Down``, ``Run Up Rate``) — the ramp rows
     involve ``u`` (start-up / shut-down envelopes), so they keep it binary.

   This covers the zero-cost units PLEXOS marks Integer Optimal (``2``)
   or leaves unset (e.g. ``ABANICO``, ``TALINAY_*_EO``,
   ``VALDIVIA_BL1_EUCA``).  It is a deliberate, documented relaxation of
   the PLEXOS model: the only behaviour it changes is that the unit may be
   curtailed into ``(0, MSL)``; cost, start-ups and every other constraint
   are unaffected.

``Min Stable Level`` and PLEXOS ``Commit`` forcing (``Gen_Commit``) do NOT
block the relaxation.  The commitment is still emitted (not dropped) so:

* forced blocks keep their pin — ``fixed_status`` / ``must_run`` fix ``u``
  at 0 / 1 through bounds, so a forced-OFF solar night still gets
  ``gen = 0`` and a forced-ON unit still honours its MSL (bounded by its
  availability, see ``extract_commitments``);
* every ``commitment("uc_<gen>").status`` reference in the PAMPL user
  constraints (inertia, reserves, comparison rows) stays valid — with the
  same fractional semantics PLEXOS gives a Linear-Relaxation unit.

Never relaxed: commitments synthesised from PLEXOS hydro ``<plant>min`` /
``<plant>max`` Constraints (``origin != "generator"``), whose binary
encodes a PLEXOS constraint rather than a unit's UC data.

Modes (CLI ``--uc-relax``): ``auto`` (default: rules 1 + 2), ``plexos``
(rule 1 only — strictly PLEXOS's Unit Commitment Optimality) and ``none``
(every commitment integer, the pre-2026-11 behaviour).  ``--lp-relax``
still relaxes everything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .entities import CommitmentSpec, FuelSpec, GeneratorSpec

#: $/MWh below which a unit's energy cost counts as zero.
ZERO_COST_EPS: float = 1.0e-6

#: Accepted ``--uc-relax`` modes.
UC_RELAX_MODES: tuple[str, ...] = ("auto", "plexos", "none")

#: Reason tags returned by :func:`commitment_relax_reason`.
REASON_PLEXOS_LINEAR = "plexos_linear_relaxation"
REASON_COST_FREE = "cost_free"

#: PLEXOS ``Unit Commitment Optimality`` enum value for Linear Relaxation.
UCO_LINEAR_RELAXATION = 0


def _max_or(values: Iterable[float], default: float) -> float:
    return max(values, default=default)


def fuel_price_ceiling(fuels: Iterable[FuelSpec]) -> dict[str, float]:
    """``{fuel name: highest price over its profile}`` (scalar included)."""
    return {f.name: _max_or(f.price_profile, f.price) for f in fuels}


def energy_cost_ceiling(gen: GeneratorSpec, fuel_price: Mapping[str, float]) -> float:
    """Upper bound of *gen*'s energy cost [$/MWh] over the horizon.

    ``vom_charge + fuel_transport + heat_rate x price`` with the maximum
    heat rate (scalar, profile or piecewise segment) and the maximum price
    of any of the unit's fuels (or its ``fuel_price_override``).
    """
    heat_rate = max(
        gen.heat_rate,
        _max_or(gen.heat_rate_profile, 0.0),
        _max_or(gen.heat_rate_segments, 0.0),
    )
    price = gen.fuel_price_override
    if price <= 0.0:
        price = _max_or((fuel_price.get(n, 0.0) for n in gen.fuel_names), 0.0)
    return gen.vom_charge + gen.fuel_transport + heat_rate * price


def commitment_is_cost_free(
    c: CommitmentSpec,
    gen: GeneratorSpec | None,
    fuel_price: Mapping[str, float],
) -> bool:
    """True when switching *c*'s unit on / off has no cost or dynamics.

    See the module docstring (rule 2) for the exact conditions.
    """
    if gen is None or c.origin != "generator":
        return False
    if energy_cost_ceiling(gen, fuel_price) > ZERO_COST_EPS:
        return False
    on_off_costs = (
        c.startup_cost,
        c.shutdown_cost,
        _max_or(c.startup_cost_profile, 0.0),
        _max_or(c.shutdown_cost_profile, 0.0),
        c.noload_cost,
        c.noload_fuel,
    )
    if any(v > ZERO_COST_EPS for v in on_off_costs):
        return False
    if c.min_up_time > 0.0 or c.min_down_time > 0.0:
        return False
    if c.max_starts > 0 or c.min_starts > 0:
        return False
    ramps = (
        c.ramp_up,
        c.ramp_down,
        c.startup_ramp,
        _max_or(c.ramp_up_profile, 0.0),
        _max_or(c.ramp_down_profile, 0.0),
    )
    return not any(v > 0.0 for v in ramps)


def commitment_relax_reason(
    c: CommitmentSpec,
    gen: GeneratorSpec | None,
    fuel_price: Mapping[str, float],
    mode: str = "auto",
) -> str | None:
    """Why *c* ships LP-relaxed (``relax: true``), or ``None`` to keep it MIP.

    Raises:
        ValueError: on an unknown *mode*.
    """
    if mode not in UC_RELAX_MODES:
        raise ValueError(f"unknown --uc-relax mode {mode!r}; expected {UC_RELAX_MODES}")
    if mode == "none" or c.origin != "generator":
        return None
    if c.uc_optimality == UCO_LINEAR_RELAXATION:
        return REASON_PLEXOS_LINEAR
    if mode == "auto" and commitment_is_cost_free(c, gen, fuel_price):
        return REASON_COST_FREE
    return None


@dataclass(frozen=True)
class CommitmentRelaxPlan:
    """Outcome of :func:`plan_commitment_relaxation`.

    ``reasons`` maps generator name -> reason tag for every relaxed
    commitment; ``total`` counts every commitment considered.
    """

    reasons: dict[str, str]
    total: int

    def count(self, reason: str) -> int:
        """Number of commitments relaxed for *reason*."""
        return sum(1 for r in self.reasons.values() if r == reason)


def plan_commitment_relaxation(
    commitments: Iterable[CommitmentSpec],
    generators: Iterable[GeneratorSpec],
    fuels: Iterable[FuelSpec],
    mode: str = "auto",
) -> CommitmentRelaxPlan:
    """Apply :func:`commitment_relax_reason` to every commitment."""
    gen_by_name = {g.name: g for g in generators}
    fuel_price = fuel_price_ceiling(fuels)
    reasons: dict[str, str] = {}
    total = 0
    for c in commitments:
        total += 1
        reason = commitment_relax_reason(
            c, gen_by_name.get(c.generator_name), fuel_price, mode
        )
        if reason is not None:
            reasons[c.generator_name] = reason
    return CommitmentRelaxPlan(reasons=reasons, total=total)
