"""Inflexible-LNG ("GNL inflexible") tanks modelled as PLEXOS Storage objects.

CEN PCP bundles from 2024 model each committed LNG cargo as a PLEXOS
``Storage`` (``ENGIE_MEJILLONES_GNL_INF``, ``ENEL_QUINTERO_GNL_INF``, …)
that the ``*_GNL_INF`` thermal generators draw from through a
``Head Storage`` membership:

* the tank starts at ``Hydro_InitialVolume.csv`` (volume unit = fuel/24,
  PLEXOS "CMD"),
* each generator depletes it by ``HeatRate · Generation / 24`` per hour
  (verified to machine precision against the PLEXOS solution of
  2024-12-15: 237.25 MW × 0.207232 + 350.425 MW × 0.189063 = 115.42 fuel
  units ⇒ ΔV = 4.8091 per hour),
* ``Hydro_MaxVolume.csv`` drops to 0 at the cargo deadline (the arrival
  of the next ship: the tank must be empty by then),
* the only other exit is the ``Vert_<tank>`` spill waterway (Max Flow 0,
  ``Max Flow Penalty`` 7200 $ per unit of flow·h, i.e. per fuel unit), so
  PLEXOS prices unburnt gas at -7200·24 $/CMD.

Both gtopt models (``--lng-inflexible-mode``) keep the tank as a gtopt
``Reservoir`` (volume in the PLEXOS unit, ``flow_conversion_rate = 1/24``,
``eini`` = cargo, ``emax`` per block from ``Hydro_MaxVolume.csv``
*including* the drop to 0) drained by one ``Turbine`` per generator with
``production_factor = 1 / HeatRate`` (``flow = HR · gen``, so the balance
gives ``ΔV = HR · gen · Δt / 24``).  The reservoir is a state variable
(gtopt default ``use_state_variable = true``), so under SDDP its level is
carried across phases and its ``Reservoir/water_value_dual`` is the gas
opportunity cost [$ per volume unit = $ per 24 fuel units].

``storage`` (default, PLEXOS-faithful)
    The spill carries the PLEXOS ``Vert_*`` penalty (``spillway_cost`` =
    7200 $ per fuel unit) and the generators keep their fuel price.

``offtake`` (take-or-pay, sunk gas)
    Gas left at the deadline is simply lost: the spill is FREE
    (``spillway_cost = 0``; holding gas never has negative value, so a
    free spill only fires when the emax drop forces it), and the tank's
    fuel price is zeroed (the cargo is paid whether burnt or not) — the
    generators keep only their VOM + fuel-transport charge in ``gcost``.
    A fuel shared with generators OUTSIDE the tank keeps its price (with
    a warning): zeroing it would make those units' gas free too.

Detection is structural: a ``Storage`` whose ``Head Storage`` generators
all carry a ``Fuel`` membership (thermal units; hydro turbines have none).
Names are never required — the ``_GNL`` suffix is only quoted in logs.
A tank that starts empty never feeds its generators (``pmax = 0``).
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .entities import (
    FuelSpec,
    GeneratorSpec,
    ReservoirSpec,
    TurbineSpec,
    fuel_transport_per_mwh,
)
from .plexos_csv import read_long, read_wide
from .plexos_loader import PlexosBundle
from .plexos_xml import PlexosDb

logger = logging.getLogger(__name__)

#: Accepted values of ``--lng-inflexible-mode``.
LNG_INFLEXIBLE_MODES = ("storage", "offtake")
#: Default ``--lng-inflexible-mode``.
DEFAULT_LNG_INFLEXIBLE_MODE = "storage"

#: Sources of the ``offtake``-mode terminal gas value (``--lng-inflexible-value``).
LNG_VALUE_SOURCES = ("bundle", "cuts", "diesel")
#: Default ``--lng-inflexible-value``.
DEFAULT_LNG_VALUE_SOURCE = "bundle"

#: PLEXOS tank volume unit = fuel units / 24 (CMD with flow = fuel/h).
FUEL_PER_VOLUME = 24.0


@dataclass(frozen=True)
class LngTank:
    """One PLEXOS Storage detected as an inflexible-LNG tank."""

    name: str
    object_id: int
    #: ``Head Storage`` generators drawing from the tank (sorted).
    generators: tuple[str, ...]
    #: Fuels of those generators (sorted, de-duplicated).
    fuels: tuple[str, ...]
    #: Initial volume [PLEXOS volume = fuel / 24].
    eini: float
    #: Hourly ``Max Volume`` series over the horizon (empty ⇒ static only).
    emax_series: tuple[float, ...]
    #: Static ``Max Volume`` (fallback when the CSV has no column).
    static_emax: float
    #: 0-based hour of the first period whose ``Max Volume`` is 0 (the
    #: cargo deadline: End Volume there must be 0).  ``None`` ⇒ no deadline
    #: inside the horizon.
    deadline_hour: int | None
    #: ``Vert_<tank>`` ``Max Flow Penalty`` [$ per flow·h = $ per fuel unit].
    spill_penalty: float
    #: Name of that spill waterway (``None`` when absent).
    spill_waterway: str | None

    @property
    def cargo_fuel(self) -> float:
        """Initial cargo in fuel units (``eini · 24``)."""
        return self.eini * FUEL_PER_VOLUME


def _head_storage_generators(db: PlexosDb) -> dict[str, list[tuple[int, str]]]:
    """``{storage_name: [(gen_object_id, gen_name), …]}`` via Head Storage."""
    coll = db.collection_for_named("Generator", "Storage", "Head Storage")
    if coll is None:
        return {}
    objs = db.object_by_id()
    out: dict[str, list[tuple[int, str]]] = {}
    for m in db.memberships_of(coll.collection_id):
        gen = objs.get(m.parent_object_id)
        sto = objs.get(m.child_object_id)
        if gen is None or sto is None:
            continue
        out.setdefault(sto.name, []).append((gen.object_id, gen.name))
    return out


def _spill_waterways(db: PlexosDb) -> dict[str, tuple[str, float]]:
    """``{storage_name: (waterway, Max Flow Penalty)}`` for 1-ended spills.

    A spill waterway has a ``Storage From`` and no ``Storage To`` and carries
    a positive ``Max Flow Penalty`` (PLEXOS ``-1`` = disabled is ignored).
    """
    from_coll = db.collection_for_named("Waterway", "Storage", "Storage From")
    to_coll = db.collection_for_named("Waterway", "Storage", "Storage To")
    if from_coll is None:
        return {}
    objs = db.object_by_id()
    p2c_from = db.parent_to_children(from_coll.collection_id)
    p2c_to = db.parent_to_children(to_coll.collection_id) if to_coll else {}
    out: dict[str, tuple[str, float]] = {}
    for ww in db.objects_of_class("Waterway"):
        if p2c_to.get(ww.object_id):
            continue
        srcs = p2c_from.get(ww.object_id, [])
        if not srcs or srcs[0] not in objs:
            continue
        pen = db.static_property("Waterway", ww.object_id, "Max Flow Penalty")
        if pen <= 0.0:
            continue
        src = objs[srcs[0]].name
        prev = out.get(src)
        if prev is None or pen > prev[1]:
            out[src] = (ww.name, float(pen))
    return out


def _deadline_hour(series: Sequence[float]) -> int | None:
    """First 0-based hour whose cap is 0 (``None`` when it never drops)."""
    for h, v in enumerate(series):
        if v <= 0.0:
            return h
    return None


def detect_lng_tanks(
    db: PlexosDb,
    bundle: PlexosBundle | None,
    gen_fuels: Mapping[int, tuple[str, ...]],
) -> dict[str, LngTank]:
    """Return every Storage whose ``Head Storage`` generators are all thermal.

    ``gen_fuels`` is ``{generator_object_id: (fuel, …)}`` (the
    ``Generator → Fuel`` memberships).  A Storage qualifies when it has at
    least one ``Head Storage`` generator and EVERY one of them carries a
    Fuel membership — hydro turbines (water storages) never do, so the
    test needs no naming convention.  Storages without head generators
    (the memberless ``*_GNL_INF`` artifacts of 2025-26 bundles) are not
    tanks and keep their existing handling.
    """
    heads = _head_storage_generators(db)
    if not heads:
        return {}
    emax_csv: dict[str, list[float]] = {}
    eini_csv: dict[str, list[float]] = {}
    n_days = 1
    if bundle is not None:
        n_days = bundle.n_days
        if bundle.has("Hydro_MaxVolume.csv"):
            emax_csv = read_wide(
                bundle.csv("Hydro_MaxVolume.csv"),
                n_days=n_days,
                drop_zero_cols=False,
            )
        if bundle.has("Hydro_InitialVolume.csv"):
            eini_csv = read_long(bundle.csv("Hydro_InitialVolume.csv"))
    spills = _spill_waterways(db)
    out: dict[str, LngTank] = {}
    for storage in db.objects_of_class("Storage"):
        members = heads.get(storage.name)
        if not members:
            continue
        if not all(gid in gen_fuels for gid, _n in members):
            continue
        name = storage.name
        eini_series = eini_csv.get(name, [])
        eini = eini_series[0] if eini_series else 0.0
        if eini == 0.0:
            eini = db.static_property("Storage", storage.object_id, "Initial Volume")
        series = tuple(emax_csv.get(name, ()))
        static_emax = db.static_property("Storage", storage.object_id, "Max Volume")
        deadline = _deadline_hour(series) if series else None
        spill_ww, spill_pen = spills.get(name, (None, 0.0))
        fuels = sorted(
            {f for gid, _n in members for f in gen_fuels.get(gid, ())},
        )
        out[name] = LngTank(
            name=name,
            object_id=storage.object_id,
            generators=tuple(sorted({n for _g, n in members})),
            fuels=tuple(fuels),
            eini=float(eini),
            emax_series=series,
            static_emax=float(static_emax),
            deadline_hour=deadline,
            spill_penalty=spill_pen,
            spill_waterway=spill_ww,
        )
    if out:
        logger.info(
            "lng-inflexible: detected %d PLEXOS Storage tank(s) whose Head "
            "Storage generators are all thermal (fuel-fed): %s",
            len(out),
            ", ".join(
                f"{t.name} (eini={t.eini:g}, gens={len(t.generators)}, "
                f"deadline_h={t.deadline_hour})"
                for t in out.values()
            ),
        )
    return out


def _block_emax_profile(
    tank: LngTank, block_layout: Sequence[Sequence[int]]
) -> tuple[float, ...]:
    """Per-block ``emax`` (min over the block's hours, zeros honoured)."""
    if not tank.emax_series or not block_layout:
        return ()
    series = tank.emax_series
    prof: list[float] = []
    for intervals in block_layout:
        vals = [series[h - 1] for h in intervals if 0 < h <= len(series)]
        prof.append(min(vals) if vals else tank.static_emax)
    return tuple(prof)


def lng_reservoir_spec(
    tank: LngTank,
    block_layout: Sequence[Sequence[int]] = (),
    *,
    mode: str = DEFAULT_LNG_INFLEXIBLE_MODE,
) -> ReservoirSpec:
    """The tank as a gtopt :class:`ReservoirSpec` (both modes).

    ``storage``: the spill penalty goes to ``spill_flow_penalty`` — the
    same field the converter fills from a hydro reservoir's ``Vert_*``
    ``Max Flow Penalty`` and writes verbatim as ``spillway_cost`` [$ per
    flow·h].  With ``flow_conversion_rate = 1/24`` one flow·h removes 1/24
    volume = 1 fuel unit, so the cost per unburnt fuel unit is exactly
    PLEXOS's 7200 (dual −7200·24 per volume unit, as PLEXOS reports).
    ``offtake``: ``spill_flow_penalty = 0`` — the writer then emits the
    reservoir's internal drain at ``spillway_cost = 0`` (gas left at the
    deadline is lost at no cost).
    """
    positive = [v for v in tank.emax_series if v > 0.0]
    emax = max(positive) if positive else tank.static_emax
    if emax <= 0.0:
        emax = tank.eini
    profile = _block_emax_profile(tank, block_layout)
    if profile and len(set(profile)) <= 1:
        emax = profile[0] if profile else emax
        profile = ()
    return ReservoirSpec(
        object_id=tank.object_id,
        name=tank.name,
        emin=0.0,
        emax=emax,
        eini=tank.eini,
        efin=0.0,
        spill_flow_penalty=tank.spill_penalty if mode == "storage" else 0.0,
        emax_profile=profile,
    )


def lng_turbine_specs(
    tank: LngTank, heat_rates: Mapping[str, float]
) -> tuple[TurbineSpec, ...]:
    """``storage`` mode: one turbine per generator, ``pf = 1 / HeatRate``.

    gtopt's turbine row is ``gen = pf · flow`` and the reservoir balance
    debits ``flow_conversion_rate · flow · Δt = flow · Δt / 24``, so
    ``pf = 1/HR`` reproduces PLEXOS's ``ΔV = HR · gen · Δt / 24``.
    Generators without a positive heat rate cannot be mapped and are
    skipped with a warning (they would deplete nothing).
    """
    out: list[TurbineSpec] = []
    for gen in tank.generators:
        hr = heat_rates.get(gen, 0.0)
        if hr <= 0.0:
            logger.warning(
                "lng-inflexible: generator '%s' of tank '%s' has no positive "
                "heat rate — no turbine emitted (it cannot draw from the tank)",
                gen,
                tank.name,
            )
            continue
        out.append(
            TurbineSpec(
                generator_name=gen,
                reservoir_name=tank.name,
                production_factor=1.0 / hr,
            )
        )
    return tuple(out)


def zero_empty_tank_generators(
    generators: Sequence[GeneratorSpec],
    tanks: Mapping[str, LngTank],
) -> tuple[GeneratorSpec, ...]:
    """``pmax = 0`` for the generators of tanks that start empty.

    The tank has no inflow, so with ``eini = 0`` PLEXOS can never run its
    generators; no reservoir / turbine is emitted for such a tank either.
    """
    empty = {g for t in tanks.values() if t.eini <= 0.0 for g in t.generators}
    return tuple(
        replace(g, pmax=0.0, pmax_profile=()) if g.name in empty else g
        for g in generators
    )


def sunk_lng_fuels(
    tanks: Mapping[str, LngTank],
    generators: Sequence[GeneratorSpec],
    fuels: Sequence[FuelSpec],
) -> tuple[FuelSpec, ...]:
    """``offtake`` mode: zero the price of fuels used only by tank generators.

    Take-or-pay: a delivered cargo is paid whether it is burnt or not, so
    its gas has no marginal fuel cost (the opportunity cost comes from the
    tank's state dual instead).  A fuel also used by a generator outside
    every tank keeps its price, with a warning.
    """
    tank_gens = {g for t in tanks.values() if t.eini > 0.0 for g in t.generators}
    tank_fuels = {f for t in tanks.values() if t.eini > 0.0 for f in t.fuels}
    shared = {
        f
        for g in generators
        if g.name not in tank_gens
        for f in g.fuel_names
        if f in tank_fuels
    }
    for f in sorted(shared):
        users = sorted(
            g.name for g in generators if f in g.fuel_names and g.name not in tank_gens
        )
        logger.warning(
            "lng-inflexible offtake: fuel '%s' also feeds generator(s) outside "
            "the tank (%s) — its price is kept, so the tank units still pay it",
            f,
            ", ".join(users),
        )
    sunk = tank_fuels - shared
    out: list[FuelSpec] = []
    for fuel in fuels:
        if fuel.name in sunk:
            logger.info(
                "lng-inflexible offtake: fuel '%s' price %.6g -> 0 (take-or-pay "
                "cargo, sunk cost)",
                fuel.name,
                fuel.price,
            )
            fuel = replace(
                fuel,
                price=0.0,
                price_profile=tuple(0.0 for _ in fuel.price_profile),
            )
        out.append(fuel)
    return tuple(out)


def reference_diesel_cost(
    generators: Sequence[GeneratorSpec], fuels: Sequence[FuelSpec]
) -> float | None:
    """Median SRMC [$/MWh] of the bundle's diesel units.

    A diesel unit is a generator with ``pmax > 0``, a positive heat rate
    and a fuel whose ``type_tag`` is ``diesel``; its SRMC is
    ``heat_rate × fuel price + vom_charge``.  ``None`` when none exists.
    """
    price = {f.name: f.price for f in fuels if f.type_tag == "diesel" and f.price > 0}
    srmc = sorted(
        g.heat_rate * price[g.fuel_names[0]] + g.vom_charge
        for g in generators
        if g.fuel_names
        and g.fuel_names[0] in price
        and g.heat_rate > 0.0
        and (max(g.pmax_profile) if g.pmax_profile else g.pmax) > 0.0
    )
    if not srmc:
        return None
    mid = len(srmc) // 2
    return srmc[mid] if len(srmc) % 2 else 0.5 * (srmc[mid - 1] + srmc[mid])


def read_tank_cut_values(path: str | Path) -> dict[str, float]:
    """``{column: value}`` from a ``boundary_cuts.csv``-format file.

    Format ``scene,rhs,<state1>,…``; a coefficient ``c`` on a state is a
    value ``-c`` per volume unit (more stored ⇒ lower future cost).  Only
    the FIRST cut row is used (a warning lists how many were ignored).
    """
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {}
    if len(rows) > 1:
        logger.warning(
            "lng-inflexible cuts %s: %d cut rows, only the first is used",
            path,
            len(rows),
        )
    out: dict[str, float] = {}
    for key, raw in rows[0].items():
        if key in ("scene", "rhs") or raw in (None, ""):
            continue
        out[key] = -float(raw)
    return out


def tank_terminal_value(  # pylint: disable=too-many-arguments
    tank: LngTank,
    source: str,
    *,
    fuels: Sequence[FuelSpec],
    heat_rates: Mapping[str, float],
    diesel_cost: float | None,
    cut_values: Mapping[str, float] | None = None,
) -> float | None:
    """``offtake`` mode: future value of the tank's gas [$ per volume unit].

    Only tanks whose deadline lies BEYOND the horizon get one (inside the
    horizon the remainder is lost at the deadline: value 0).  Sources:

    * ``cuts`` — the tank's column of a medium-term boundary-cuts file;
      falls back to ``bundle`` when the column is absent;
    * ``bundle`` — the bundle's own price of the tank fuel (the CEN
      weekly LNG opportunity cost) × 24;
    * ``diesel`` — ``diesel_cost / min(HR) × 24`` (the diesel the gas
      displaces in the tank's most efficient unit).
    """
    if tank.eini <= 0.0 or tank.deadline_hour is not None:
        return None
    if source == "cuts":
        if cut_values and tank.name in cut_values:
            return cut_values[tank.name]
        logger.warning(
            "lng-inflexible: no '%s' column in the cuts file — using the "
            "bundle fuel price",
            tank.name,
        )
        source = "bundle"
    if source == "diesel":
        hrs = [heat_rates[g] for g in tank.generators if heat_rates.get(g, 0.0) > 0]
        if not hrs or not diesel_cost:
            return None
        return diesel_cost / min(hrs) * FUEL_PER_VOLUME
    prices = [f.price for f in fuels if f.name in tank.fuels and f.price > 0.0]
    return max(prices) * FUEL_PER_VOLUME if prices else None


def clamp_sunk_gas_costs(
    generators: Sequence[GeneratorSpec], sunk_fuels: frozenset[str]
) -> tuple[GeneratorSpec, ...]:
    """``offtake`` mode: keep a sunk-gas unit's variable cost >= 0.

    With the tank fuel at price 0, a negative fuel ``Transport Charge``
    would leave ``gcost = VOM + transport·HR < 0`` (the LP would be paid
    to burn gas).  The transport adder is raised so the cost is exactly 0.
    """
    out: list[GeneratorSpec] = []
    for g in generators:
        cost = g.vom_charge + fuel_transport_per_mwh(g)
        if g.fuel_names and g.fuel_names[0] in sunk_fuels and cost < 0.0 < g.heat_rate:
            logger.info(
                "lng-inflexible offtake: '%s' VOM %.6g + transport %.6g $/MWh "
                "< 0 with sunk gas — variable cost clamped to 0",
                g.name,
                g.vom_charge,
                fuel_transport_per_mwh(g),
            )
            g = replace(g, fuel_transport=-g.vom_charge / g.heat_rate)
        out.append(g)
    return tuple(out)
