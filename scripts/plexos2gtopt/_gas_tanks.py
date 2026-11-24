# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Gas fuel bands as tanks (``--gas-bands-as-tanks``).

CEN PCP prices each gas supply as a *band* Fuel (``Gas_<company>_<band>``)
with a weekly offtake allotment (``Fuel_MaxOfftakeWeek.csv``).  The default
conversion keeps the band as a priced Fuel with a ``max_offtake`` budget
over the (single) stage.  This module models the same band as a TANK:

* one gtopt Reservoir ``gastank_<fuel>`` per band (state variable, so
  under SDDP the remaining allotment carries between phases);
* each generator burning the band drains it through a Turbine with
  ``production_factor = 1/HeatRate``; with the converter-wide
  ``flow_conversion_rate = 1/24`` the volume unit is fuel/24 (the same
  convention as the 2024 inflexible-LNG tanks);
* the band's generators carry NO fuel price (gcost = VOM + transport,
  clamped >= 0): the gas cost becomes the opportunity cost of the
  tank — the terminal value of the leftover in the boundary future-cost
  cut, ``price × 24`` per volume unit (signed like a water value);
* a soft cap (``max_offtake_cost`` set) becomes a purchase Flow into the
  tank priced ``price + max_offtake_cost`` per fuel unit.

For a single-stage solve with ``horizon`` partition this is exactly the
Fuel model minus the constant ``Σ price·allotment``:
``(p+c)·x − p·(V + x − u) = p·u + c·x − p·V``.

``period`` partition: each allotment row of ``Fuel_MaxOfftakeWeek.csv``
(gas weeks run Thursday→Wednesday; the boundaries are taken from the CSV
dates) is an inflow at its sub-period start (the first one is ``eini``).
Every band, INF or manageable, must be empty and ready for the next
allotment / ship: ``emax = 0`` on the block ending each sub-period inside
the horizon, and the remainder is spilled at zero cost (the gas is
worth 0 at that point).  The terminal value (boundary-cut slope) applies
only when the band's last sub-period outlives the horizon: a manageable
band is valued at its price, and an INF band at ``--lng-inflexible-value``
(the bundle INF price by default) in ``offtake`` mode.  A deadline
inside the horizon gives no terminal value.  ``emax`` is only ever a cap:
volume arrives solely through the allotment inflows.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from ._numfmt import fmt_exact
from ._lng_inflexible import FUEL_PER_VOLUME, clamp_sunk_gas_costs
from .entities import (
    DecisionVariableSpec,
    FlowSpec,
    FuelSpec,
    GeneratorSpec,
    JunctionSpec,
    ReservoirSpec,
    TurbineSpec,
    UserConstraintSpec,
)
from .plexos_csv import sniff_delimiter
from .plexos_loader import PlexosBundle

logger = logging.getLogger(__name__)

#: Accepted ``--gas-bands-as-tanks`` values.
GAS_TANK_MODES = ("off", "horizon", "period")
#: Reservoir name prefix of a band tank.
GAS_TANK_PREFIX = "gastank_"
#: TJ → GJ (the fuel-unit basis of the heat rates, as for ``max_offtake``).
_TJ_TO_GJ = 1000.0

#: Accepted ``--gas-usable`` values (see :meth:`GasFutureValue.usable`).
GAS_USABLE_MODES = ("capacity", "full")
#: Name prefix of a tank's future-cost DecisionVariable / UserConstraint.
GAS_FCF_PREFIX = "alpha_gas_"

#: One sub-period: ``(start_hour, allotment_GJ, end_hour)`` (1-based horizon
#: hours; ``end_hour`` > horizon hours ⇒ the sub-period outlives it).
Period = tuple[int, float, int]

#: ``(fuel, generator names, price) -> terminal value per volume unit``.
TerminalValueFn = Callable[[str, tuple[str, ...], float], "float | None"]


@dataclass(frozen=True)
class GasFutureValue:
    """End-of-horizon value of one gas tank's leftover (monolithic FCF).

    ``value`` is g [$ per volume unit]; ``cap_end`` the tank's emax at the
    horizon end; ``burn_rate`` the volume its online units can burn per
    hour (Σ pmax·HR / 24); ``hours_after`` the hours from the horizon end
    to the tank's deadline / gas-week end (``None`` = unknown).
    """

    tank: str
    value: float
    cap_end: float
    burn_rate: float = 0.0
    hours_after: float | None = None

    def usable(self, mode: str = "capacity") -> float:
        """V_usable: the leftover that can still be burnt after the horizon.

        ``capacity``: ``min(cap_end, burn_rate × hours_after)`` — gas the
        units cannot burn before the deadline is worth 0 (``cap_end`` when
        ``hours_after`` is unknown).  ``full``: ``cap_end`` (no
        saturation: every unit of leftover is worth ``value``).
        """
        if mode == "full" or self.hours_after is None:
            return self.cap_end
        return min(self.cap_end, self.burn_rate * max(self.hours_after, 0.0))


def gas_future_cost_specs(
    values: Sequence[GasFutureValue], *, last_block: int, mode: str = "capacity"
) -> tuple[tuple[DecisionVariableSpec, ...], tuple[UserConstraintSpec, ...]]:
    """One future-cost variable α_i per tank, separate from the hydro α.

    ``α_i ≥ g_i·(V_usable_i − T_i)``, ``α_i ≥ 0``, objective ``+ α_i``,
    with ``T_i`` = ``reservoir(i).efin`` (end-of-horizon volume) on the
    last block: the future cost is ``g·max(0, V_usable − T)``.  The α is a
    per-block DecisionVariable with ``raw`` cost 1 (face value — no
    probability / discount / duration weighting); only its last-block
    column is constrained, the others sit at 0.  A single shared α (the
    hydro boundary cut) would take the MAX over cuts, not the SUM, and a
    slope merged into the hydro cut never saturates.
    """
    if mode not in GAS_USABLE_MODES:
        raise ValueError(f"unknown gas-usable mode {mode!r}")
    dvs: list[DecisionVariableSpec] = []
    ucs: list[UserConstraintSpec] = []
    for fv in values:
        usable = fv.usable(mode)
        if fv.value <= 0.0 or usable <= 0.0:
            continue
        alpha = f"{GAS_FCF_PREFIX}{fv.tank}"
        dvs.append(DecisionVariableSpec(name=alpha, lower_bound=0.0, cost=1.0))
        ucs.append(
            UserConstraintSpec(
                name=f"{alpha}_cut",
                expression=(
                    f'decision_variable("{alpha}").value + {fmt_exact(fv.value)} * '
                    f'reservoir("{fv.tank}").efin >= {fmt_exact(fv.value * usable)}, '
                    f"for(block in {{{last_block}}})"
                ),
                description=(
                    f"gas future value of tank {fv.tank}: "
                    f"{fv.value:.6g} $/vol x max(0, {usable:.6g} - end volume) "
                    f"(--gas-usable {mode})"
                ),
            )
        )
    return tuple(dvs), tuple(ucs)


@dataclass(frozen=True)
class GasTankModel:
    """Elements added by :func:`build_gas_band_tanks`."""

    reservoirs: tuple[ReservoirSpec, ...] = ()
    junctions: tuple[JunctionSpec, ...] = ()
    turbines: tuple[TurbineSpec, ...] = ()
    flows: tuple[FlowSpec, ...] = ()
    fuels: tuple[FuelSpec, ...] = ()
    generators: tuple[GeneratorSpec, ...] = ()
    #: Terminal value of each tank whose gas outlives the horizon.
    futures: tuple[GasFutureValue, ...] = ()
    #: ``Σ price · allotment`` [$] — the constant the tank model drops.
    dropped_constant: float = 0.0


def fuel_offtake_periods(
    bundle: PlexosBundle, horizon_start: datetime | None
) -> dict[str, tuple[Period, ...]]:
    """``{fuel: ((start_hour, allotment_GJ, end_hour), …)}`` per gas week.

    Gas weeks run Thursday→Wednesday; the boundaries come from the CSV
    dates (week-start rows), not calendar weeks.  ``start_hour`` is the
    1-based horizon hour where the sub-period starts (1 for the week
    already running at the horizon start) and ``end_hour`` its last hour
    (possibly beyond the horizon); the allotment
    is pro-rated by the days of the week inside the horizon — the same
    weighting as ``_extract_fuel_max_offtake_week`` so the sum equals the
    Fuel ``max_offtake``.  Empty without a horizon start or the CSV.
    """
    csv_name = "Fuel_MaxOfftakeWeek.csv"
    if horizon_start is None or not bundle.has(csv_name):
        return {}
    ref = horizon_start.date()
    days = max(int(bundle.n_days) or 1, 1)
    last = ref + timedelta(days=days - 1)
    out: dict[str, list[Period]] = {}
    with bundle.csv(csv_name).open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=sniff_delimiter(fh)):
            name = (row.get("NAME") or "").strip()
            try:
                wk = date(int(row["YEAR"]), int(row["MONTH"]), int(row["DAY"]))
                cap_tj = float(row.get("VALUE") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            lo = max(wk, ref)
            overlap = (min(wk + timedelta(days=6), last) - lo).days + 1
            if not name or overlap <= 0:
                continue
            start = (lo - ref).days * 24 + 1
            end = (wk + timedelta(days=7) - ref).days * 24
            out.setdefault(name, []).append(
                (start, cap_tj * _TJ_TO_GJ * overlap / 7.0, end)
            )
    return {k: tuple(sorted(v)) for k, v in out.items()}


def parse_gas_refill(spec: str | None) -> int:
    """``--gas-refill``: ``instant`` → 0, ``ramp:<hours>`` → hours (>= 1)."""
    if not spec or spec == "instant":
        return 0
    kind, _, hours = spec.partition(":")
    try:
        value = int(hours)
    except ValueError:
        value = 0
    if kind != "ramp" or value < 1:
        raise ValueError(
            f"--gas-refill: expected 'instant' or 'ramp:<hours>', got {spec!r}"
        )
    return value


def fill_forward_defined(series: Sequence[float]) -> list[float]:
    """Zeros = undefined periods: fill forward (leading ones backward).

    Same rule as the writer's ``_fill_forward_defined`` for
    ``Fuel_Price.csv`` series (kept here to avoid an import cycle).
    """
    first = next((v for v in series if v != 0.0), None)
    out: list[float] = []
    last = first
    for v in series:
        last = v if v != 0.0 else last
        out.append(last if last is not None else 0.0)
    return out


def _last_pmax(g: GeneratorSpec) -> float:
    """Availability at the horizon end (last profile value, else pmax)."""
    return g.pmax_profile[-1] if g.pmax_profile else g.pmax


def _peak(g: GeneratorSpec) -> float:
    return max(g.pmax_profile) if g.pmax_profile else g.pmax


def _band_reject_reason(fuel: FuelSpec, users: Sequence[GeneratorSpec]) -> str | None:
    """Why a capped gas band cannot become an exact tank (``None`` = ok)."""
    if not any(_peak(g) > 0.0 for g in users):
        return "no online generator"
    for g in users:
        if len(g.fuel_names) != 1:
            return f"'{g.name}' burns several fuels"
        if g.fuel_price_override > 0.0:
            return f"'{g.name}' has a fuel price override"
        if g.heat_rate <= 0.0 or g.heat_rate_segments:
            return f"'{g.name}' has no scalar heat rate"
        hrp = g.heat_rate_profile
        if hrp and max(hrp) - min(hrp) > 1e-9:
            return f"'{g.name}' has a time-varying heat rate"
    return None


def _block_last_hours(
    block_layout: Sequence[Sequence[int]], horizon_hours: int
) -> list[int]:
    if block_layout:
        return [max(b) for b in block_layout]
    return list(range(1, horizon_hours + 1))


def _period_profiles(
    periods: Sequence[Period],
    *,
    cap: float,
    last_hours: Sequence[int],
    horizon_hours: int,
    refill_hours: int = 0,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    """``(emax per block, hourly allotment inflow)`` or ``None`` (misaligned).

    Every sub-period ending inside the horizon empties the tank there
    (``emax = 0`` on the block ending at its last hour): the tank must be
    ready for the next allotment/ship, the remainder is lost at no cost.
    The next allotment arrives at the sub-period start: all of it in the
    first hour (``refill_hours <= 1``, instantaneous) or at a constant
    rate over ``refill_hours`` hours (a ship's unloading ramp; the part
    past the horizon end never arrives inside it).
    """
    inflow = [0.0] * horizon_hours
    emax = [cap] * len(last_hours)
    hours = list(last_hours)
    for start, gj, end in periods:
        if start > 1:
            span = max(refill_hours, 1)
            for h in range(start - 1, min(start - 1 + span, horizon_hours)):
                inflow[h] += gj / span
        if end <= horizon_hours:
            if end not in hours:
                return None
            emax[hours.index(end)] = 0.0
    return tuple(emax), tuple(inflow)


def build_gas_band_tanks(  # pylint: disable=too-many-arguments,too-many-locals
    fuels: Sequence[FuelSpec],
    generators: Sequence[GeneratorSpec],
    *,
    mode: str,
    periods: Mapping[str, Sequence[Period]],
    block_layout: Sequence[Sequence[int]],
    horizon_hours: int,
    skip_fuels: frozenset[str] = frozenset(),
    terminal_value: TerminalValueFn | None = None,
    refill_hours: int = 0,
) -> GasTankModel:
    """Turn every capped gas band into a tank (see the module docstring)."""
    if mode not in GAS_TANK_MODES:
        raise ValueError(f"unknown gas-bands-as-tanks mode {mode!r}")
    if mode == "off":
        return GasTankModel(fuels=tuple(fuels), generators=tuple(generators))
    last_hours = _block_last_hours(block_layout, horizon_hours)
    res: list[ReservoirSpec] = []
    juncs: list[JunctionSpec] = []
    turbs: list[TurbineSpec] = []
    flows: list[FlowSpec] = []
    futures: list[GasFutureValue] = []
    sunk: dict[str, FuelSpec] = {}
    tanked: set[str] = set()
    dropped = 0.0
    for fuel in fuels:
        cap_v = fuel.max_offtake
        if fuel.name in skip_fuels and cap_v is not None:
            logger.info(
                "gas tanks: '%s' is drawn by an LNG Storage tank — the tank "
                "supersedes the band cap (max_offtake dropped)",
                fuel.name,
            )
            sunk[fuel.name] = replace(fuel, max_offtake=None, max_offtake_cost=None)
            continue
        if fuel.type_tag != "gas" or not cap_v or cap_v <= 0.0:
            continue
        users = [g for g in generators if fuel.name in g.fuel_names]
        why = _band_reject_reason(fuel, users)
        if why is not None:
            logger.info("gas tanks: '%s' kept as a priced Fuel (%s)", fuel.name, why)
            continue
        filled = fill_forward_defined(fuel.price_profile)
        varying = bool(filled) and max(filled) - min(filled) > 1e-9
        price = filled[-1] if filled else fuel.price
        name = f"{GAS_TANK_PREFIX}{fuel.name}"
        soft = fuel.max_offtake_cost is not None and fuel.max_offtake_cost > 0.0
        burn_gj = sum(_peak(g) * g.heat_rate for g in users) * horizon_hours
        cap = (cap_v + (burn_gj if soft else 0.0)) / FUEL_PER_VOLUME
        whole: tuple[Period, ...] = ((1, cap_v, horizon_hours + 1),)
        band_periods: Sequence[Period] = whole
        if mode == "period" and fuel.name in periods:
            cand = periods[fuel.name]
            if abs(sum(p[1] for p in cand) - cap_v) <= 1e-6 * max(cap_v, 1.0):
                band_periods = cand
            else:
                logger.warning(
                    "gas tanks: '%s' weekly rows do not add up to max_offtake "
                    "%.6g — single horizon allotment used",
                    fuel.name,
                    cap_v,
                )
        prof = _period_profiles(
            band_periods,
            cap=cap,
            last_hours=last_hours,
            horizon_hours=horizon_hours,
            refill_hours=refill_hours,
        )
        if prof is None:
            logger.warning(
                "gas tanks: '%s' sub-period boundary falls inside a block — "
                "single horizon allotment used",
                fuel.name,
            )
            band_periods = whole
            prof = ((), ())
        emax_prof, inflow = prof
        eini_gj = sum(p[1] for p in band_periods if p[0] <= 1)
        # Deadline inside the horizon ⇒ the gas is worthless afterwards.
        expires = max(p[2] for p in band_periods) <= horizon_hours
        res.append(
            ReservoirSpec(
                object_id=0,
                name=name,
                emax=cap,
                eini=eini_gj / FUEL_PER_VOLUME,
                emax_profile=emax_prof if 0.0 in emax_prof else (),
            )
        )
        juncs.append(JunctionSpec(name=name))
        turbs.extend(
            TurbineSpec(
                generator_name=g.name,
                reservoir_name=name,
                production_factor=1.0 / g.heat_rate,
            )
            for g in users
            if _peak(g) > 0.0
        )
        if any(inflow):
            flows.append(
                FlowSpec(
                    name=f"{name}_allotment",
                    junction_name=name,
                    discharge_profile=inflow,
                )
            )
        if soft:
            flows.append(
                FlowSpec(
                    name=f"{name}_purchase",
                    junction_name=name,
                    fcost=price + float(fuel.max_offtake_cost or 0.0),
                )
            )
        value = price * FUEL_PER_VOLUME
        tv = (
            terminal_value(fuel.name, tuple(g.name for g in users), price)
            if terminal_value is not None
            else None
        )
        # Sunk (INF band in offtake mode): no price at all.  Otherwise
        # the units keep the residual ``p_t - p_ref`` (p_ref = the last
        # price, the terminal value), so Σ p_t·u_t − p_ref·V holds exactly.
        residual = tuple(v - price for v in filled) if varying and tv is None else ()
        if tv is not None:
            value = tv
        if expires:
            value = 0.0
        data_end = max((p[2] for p in periods.get(fuel.name, ())), default=None)
        if value > 0.0:
            futures.append(
                GasFutureValue(
                    tank=name,
                    value=value,
                    cap_end=emax_prof[-1] if emax_prof else cap,
                    burn_rate=sum(_last_pmax(g) * g.heat_rate for g in users)
                    / FUEL_PER_VOLUME,
                    hours_after=(
                        None if data_end is None else float(data_end - horizon_hours)
                    ),
                )
            )
        dropped += price * cap_v
        tanked.add(fuel.name)
        sunk[fuel.name] = replace(
            fuel,
            price=0.0,
            price_profile=residual,
            price_profile_complete=bool(residual),
            max_offtake=None,
            max_offtake_cost=None,
        )
        logger.info(
            "gas tanks: '%s' -> %s (allotment %.6g in %d sub-period(s)%s%s%s, "
            "terminal value %.6g $/vol)",
            fuel.name,
            name,
            cap_v,
            len(band_periods),
            ", emptied at each sub-period end" if len(band_periods) > 1 else "",
            ", soft purchase" if soft else "",
            ", residual price profile" if residual else "",
            value,
        )
    out_fuels = tuple(sunk.get(f.name, f) for f in fuels)
    out_gens = clamp_sunk_gas_costs(generators, frozenset(tanked))
    if res:
        logger.info(
            "gas tanks: %d band tank(s) (%s partition), dropped objective "
            "constant Σ price·allotment = %.6g $",
            len(res),
            mode,
            dropped,
        )
    return GasTankModel(
        reservoirs=tuple(res),
        junctions=tuple(juncs),
        turbines=tuple(turbs),
        flows=tuple(flows),
        fuels=out_fuels,
        generators=out_gens,
        futures=tuple(futures),
        dropped_constant=dropped,
    )
