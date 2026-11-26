# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Map a :class:`~tcheby2gtopt.tcheby_db.TchebySystem` to a gtopt planning.

Mapping (tcheby -> gtopt):

* ``Bus`` -> ``bus_array``; ``PowerSystem.fail_cost`` ->
  ``model_options.demand_fail_cost``.
* ``Generator`` (capacity, pmin, pmax, cvar + their schedules) ->
  ``generator_array`` (``capacity``, ``pmin``, ``pmax``, ``gcost``).
* ``Demand`` (load + schedule) -> ``demand_array.lmax``.
* ``Line`` -> ``line_array``: ``tmax_ab = tmax``; ``tmax_ba = −tmin``
  (tcheby's ``tmin`` is the signed lower bound; a POSITIVE ``tmin`` —
  only in the DB-reader fixtures — is read as the B→A magnitude);
  ``x`` -> ``reactance`` [Ω] with ``voltage`` [kV]; ``r`` ->
  ``resistance`` only with ``ConvertOptions.line_losses`` (tcheby's
  quadratic loss segments become gtopt's loss model).
* ``Junction`` -> ``junction_array``; a junction with no outgoing
  waterway and no reservoir (the sea, river ends) gets ``drain: true``.
* ``Waterway`` -> ``waterway_array`` (``fmin``/``fmax``); ``delay`` is
  NOT mapped (gtopt waterways have no travel time).
* ``Reservoir`` -> ``reservoir_array`` (volumes in hm³, flows in m³/s:
  tcheby's ``Study.vol_scale = 1e6`` gives gtopt's default
  ``flow_conversion_rate = 0.0036``); ``vscale`` (an LP scale) dropped;
  ``spillway_capacity = 0`` unless an ``Overspill`` exists (tcheby has
  no free spill).
* ``Inflow`` -> ``flow_array`` (``discharge``); a simulation DB
  overrides it per stage with the scene of that stage.
* ``HidroTurbine`` -> ``turbine_array`` (``production_factor =
  efficiency``, MW per m³/s).  gtopt's waterway turbine is ``gen ≤
  pf·flow`` (tcheby: ``=``), a relaxation that lets water bypass.
* ``Commitment`` -> ``commitment_array`` (min up/down, start/stop cost,
  start/stop ramp, last state); the generator's ``pmin`` becomes the
  commitment ``pmin``; ``time_resolution`` is dropped.
* ``Passthrough`` -> a run-of-river generator with ``pmax =
  min(capacity, efficiency·flow)``.
* ``Overspill`` -> ``spillway_cost`` (and ``spill_junction`` = the
  waterway's downstream junction).

Dropped (reported per case): every ``*.active_sched`` (the fixtures'
activation dates do not match the simulation window), reserve zones and
generator reserve limits (``ReserveZone``, ``purmax``…), waterway delays,
``Commitment.time_resolution``, the simulation's discount rate, the
stochastic openings (one scenario: the stage→scene map of the first
scenario).
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from gtopt_shared.point_sampling import (
    POINT_STORAGE_INTEGRATION,
    commitment_period_class,
    point_storage_options,
)

from .tcheby_db import StepSchedule, TchebySimulation, TchebySystem
from .timegrid import TimeGrid, _r

logger = logging.getLogger(__name__)


@dataclass
class ConvertOptions:
    """Knobs of the mapping."""

    #: hours from the time zero where the case window starts
    start_hour: float = 0.0
    #: drop ``pmin`` of hydro generators (water-short horizons)
    drop_hydro_pmin: bool = False
    #: ``hard`` keeps generator ``pmin`` as a floor; ``drop`` removes it
    pmin_mode: str = "hard"
    #: emit ``resistance`` (gtopt then models losses, default mode
    #: ``adaptive``); off by default so objectives are hand-checkable
    line_losses: bool = False
    demand_fail_cost: float | None = None
    scale_objective: float = 1.0
    #: per-unit ``commitment_period`` [h] overrides on point stages
    #: (commitment name → hours); others follow ``commitment_period_class``
    commitment_periods: dict[str, float] = field(default_factory=dict)
    #: "class" (per-unit grids on point stages) or "none" (previous: no
    #: commitment_period, the source UC resolution dropped)
    commitment_period_rule: str = "class"
    #: storage_integration on point stages ("none": gtopt defaults)
    point_storage_integration: str = POINT_STORAGE_INTEGRATION


@dataclass
class Conversion:
    """Result: the planning dict and the mapping notes."""

    planning: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def _uids(items: list[Any]) -> dict[int, int]:
    return {it.id: k + 1 for k, it in enumerate(items)}


def convert(  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    sysm: TchebySystem,
    grid: TimeGrid,
    sim: TchebySimulation | None = None,
    opts: ConvertOptions | None = None,
) -> Conversion:
    """Build the gtopt planning of ``sysm`` on ``grid``."""
    o = opts or ConvertOptions()
    notes: list[str] = []
    offsets = np.concatenate(([0.0], np.cumsum(grid.stage_hours)[:-1])) + o.start_hour

    def series(s: StepSchedule) -> Any:
        vals = [
            grid.sample_hourly(k, s.hourly(float(offsets[k]), int(round(h))))
            for k, h in enumerate(grid.stage_hours)
        ]
        return grid.per_stage(vals)

    def stage_const(vals: list[float]) -> Any:
        return grid.per_stage([np.full(st.n, v) for st, v in zip(grid.stages, vals)])

    bus_uid = _uids(sysm.buses)
    gens = [g for g in sysm.generators if g.active]
    gen_uid = _uids(gens)
    hydro_gens = {t.gen_id for t in sysm.turbines}
    committed = {c.gen_id for c in sysm.commitments}

    system: dict[str, Any] = {"name": sysm.name}
    system["bus_array"] = [{"uid": bus_uid[b.id], "name": b.name} for b in sysm.buses]

    gen_array = []
    dropped_pmin: list[str] = []
    for g in gens:
        e: dict[str, Any] = {
            "uid": gen_uid[g.id],
            "name": g.name,
            "bus": bus_uid[g.bus_id],
            "gcost": series(g.cvar),
            "capacity": _r(g.capacity),
        }
        pmax = series(g.pmax)
        if pmax != _r(g.capacity):
            e["pmax"] = pmax
        drop = (
            o.pmin_mode == "drop"
            or (o.drop_hydro_pmin and g.id in hydro_gens)
            or g.id in committed
        )
        pmin = series(g.pmin)
        if pmin != 0 and not drop:
            e["pmin"] = pmin
        elif pmin != 0 and g.id not in committed:
            dropped_pmin.append(g.name)
        gen_array.append(e)
    if dropped_pmin:
        notes.append(
            f"pmin dropped for {len(dropped_pmin)} generator(s): "
            + ", ".join(dropped_pmin[:8])
            + (", ..." if len(dropped_pmin) > 8 else "")
        )
    for p in sysm.passthroughs:
        if not p.active:
            continue
        pmax_pt = min(p.capacity, p.efficiency * p.flow)
        gen_array.append(
            {
                "uid": len(gen_array) + 1,
                "name": p.name,
                "bus": bus_uid[p.bus_id],
                "gcost": _r(p.cvar),
                "capacity": _r(pmax_pt),
            }
        )
        notes.append(
            f"passthrough {p.name} -> run-of-river generator pmax "
            f"min({p.capacity}, {p.efficiency}*{p.flow}) = {pmax_pt}"
        )
    system["generator_array"] = gen_array

    dems = [d for d in sysm.demands if d.active]
    system["demand_array"] = [
        {
            "uid": k + 1,
            "name": d.name,
            "bus": bus_uid[d.bus_id],
            "lmax": series(d.load),
        }
        for k, d in enumerate(dems)
    ]

    if len(sysm.buses) > 1:
        lines = []
        for k, ln in enumerate(x for x in sysm.lines if x.active):
            tmax_ab = series(ln.tmax)
            tmin = ln.tmin
            if any(v > 0 for v in [tmin.default, *tmin.values]):
                notes.append(f"line {ln.name}: positive tmin read as B->A magnitude")
                tmax_ba = series(
                    StepSchedule(
                        abs(tmin.default), tmin.hours, [abs(v) for v in tmin.values]
                    )
                )
            else:
                tmax_ba = series(
                    StepSchedule(-tmin.default, tmin.hours, [-v for v in tmin.values])
                )
            e = {
                "uid": k + 1,
                "name": ln.name,
                "bus_a": bus_uid[ln.bus_a_id],
                "bus_b": bus_uid[ln.bus_b_id],
                "tmax_ab": tmax_ab,
                "tmax_ba": tmax_ba,
            }
            if ln.voltage:
                e["voltage"] = _r(ln.voltage)
            if ln.x:
                e["reactance"] = _r(ln.x)
            if ln.r and o.line_losses:
                e["resistance"] = _r(ln.r)
            lines.append(e)
        system["line_array"] = lines

    if sysm.junctions:
        _hydro(sysm, grid, sim, system, notes, series, stage_const, gen_uid)

    if sysm.commitments:
        system["commitment_array"] = _commitments(sysm, gen_uid, series)
        if grid.mode == "point" and o.commitment_period_rule != "none":
            # binaries on each unit's own uniform grid, never on the nodes
            for e, c in zip(
                system["commitment_array"], _kept(sysm, gen_uid), strict=True
            ):
                cls, hours = commitment_period_class(
                    c.up_time,
                    c.down_time,
                    c.time_resolution,
                    o.commitment_periods.get(c.name),
                )
                e["commitment_period"] = _r(hours)
                # several nodes per cell: SU / SD allowance only where a
                # ramp row crosses a cell boundary (gtopt default "period")
                e["ramp_allowance"] = "boundary"
                notes.append(f"commitment {c.name}: {cls} unit, {hours:g} h grid")
                logger.info("commitment %s: %s unit, %g h grid", c.name, cls, hours)
        else:
            notes.append("commitment time_resolution dropped")

    fail = o.demand_fail_cost if o.demand_fail_cost is not None else sysm.fail_cost
    planning = {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {
                "use_single_bus": len(sysm.buses) <= 1,
                "use_kirchhoff": True,
                "demand_fail_cost": _r(fail),
                "scale_objective": _r(o.scale_objective),
                **(
                    point_storage_options(o.point_storage_integration)
                    if grid.mode == "point"
                    else {}
                ),
            },
        },
        "simulation": grid.simulation(),
        "system": system,
    }
    notes.extend(f"dropped table {t}" for t in sysm.dropped)
    return Conversion(planning, notes)


def _hydro(  # pylint: disable=too-many-arguments,too-many-locals
    sysm: TchebySystem,
    grid: TimeGrid,
    sim: TchebySimulation | None,
    system: dict[str, Any],
    notes: list[str],
    series: Any,
    stage_const: Any,
    gen_uid: dict[int, int],
) -> None:
    jun_uid = _uids(sysm.junctions)
    wws = [w for w in sysm.waterways if w.active]
    ww_uid = _uids(wws)
    outgoing = {w.junc_a_id for w in wws}
    with_res = {r.junc_id for r in sysm.reservoirs if r.active}
    junctions = []
    for j in sysm.junctions:
        e: dict[str, Any] = {"uid": jun_uid[j.id], "name": j.name}
        if j.id not in outgoing and j.id not in with_res:
            e["drain"] = True
        junctions.append(e)
    system["junction_array"] = junctions

    waterways = []
    for w in wws:
        e = {
            "uid": ww_uid[w.id],
            "name": w.name,
            "junction_a": jun_uid[w.junc_a_id],
            "fmin": series(w.fmin),
            "fmax": series(w.fmax),
        }
        if w.junc_b_id is not None and w.junc_b_id in jun_uid:
            e["junction_b"] = jun_uid[w.junc_b_id]
        if w.delay:
            notes.append(f"waterway {w.name}: delay {w.delay} h dropped")
        waterways.append(e)
    system["waterway_array"] = waterways

    spills = {s.reserv_id: s for s in sysm.overspills}
    ww_by_id = {w.id: w for w in wws}
    reservoirs = []
    for k, r in enumerate(x for x in sysm.reservoirs if x.active):
        e = {
            "uid": k + 1,
            "name": r.name,
            "junction": jun_uid[r.junc_id],
            "eini": _r(r.vini),
            "emin": series(r.vmin),
            "emax": series(r.vmax),
            "capacity": _r(max([r.vmax.default, *r.vmax.values])),
        }
        if r.vfin is not None:
            e["efin"] = _r(r.vfin)
        sp = spills.get(r.id)
        if sp is None:
            e["spillway_capacity"] = 0
        else:
            e["spillway_cost"] = _r(sp.spillcost)
            ww = ww_by_id.get(sp.wway_id) if sp.wway_id is not None else None
            if ww is not None and ww.junc_b_id is not None:
                e["spill_junction"] = jun_uid[ww.junc_b_id]
            notes.append(f"overspill {sp.name} -> spillway_cost {sp.spillcost}")
        reservoirs.append(e)
    if reservoirs:
        system["reservoir_array"] = reservoirs

    flows = []
    for k, f in enumerate(x for x in sysm.inflows if x.active):
        if sim is not None and f.name in sim.inflows:
            vals = sim.inflows[f.name]
            if any(np.isnan(v) for v in vals):
                raise ValueError(f"inflow {f.name}: missing stage values")
            discharge = stage_const(vals)
        else:
            discharge = series(f.flow)
        flows.append(
            {
                "uid": k + 1,
                "name": f.name,
                "direction": 1,
                "junction": jun_uid[f.junc_id],
                "discharge": discharge
                if not isinstance(discharge, list)
                else [discharge],
            }
        )
    if flows:
        system["flow_array"] = flows

    turbines = []
    for k, t in enumerate(sysm.turbines):
        if t.gen_id not in gen_uid or t.wway_id not in ww_uid:
            continue
        turbines.append(
            {
                "uid": k + 1,
                "name": t.name,
                "waterway": ww_uid[t.wway_id],
                "generator": gen_uid[t.gen_id],
                "production_factor": _r(t.efficiency),
            }
        )
    if turbines:
        system["turbine_array"] = turbines


def _kept(sysm: TchebySystem, gen_uid: dict[int, int]) -> list[Any]:
    """Commitments whose generator is converted (same order as
    :func:`_commitments`)."""
    return [c for c in sysm.commitments if c.gen_id in gen_uid]


def _commitments(
    sysm: TchebySystem, gen_uid: dict[int, int], series: Any
) -> list[dict[str, Any]]:
    gens = {g.id: g for g in sysm.generators}
    out = []
    for k, c in enumerate(sysm.commitments):
        if c.gen_id not in gen_uid:
            continue
        e: dict[str, Any] = {
            "uid": k + 1,
            "name": c.name,
            "generator": gen_uid[c.gen_id],
        }
        pmin = series(gens[c.gen_id].pmin)
        if pmin != 0:
            e["pmin"] = pmin
        for src, dst in (
            ("up_time", "min_up_time"),
            ("down_time", "min_down_time"),
            ("start_cost", "startup_cost"),
            ("stop_cost", "shutdown_cost"),
            ("start_ramp", "startup_ramp"),
            ("stop_ramp", "shutdown_ramp"),
            ("last_state", "initial_status"),
            ("last_state_period", "initial_hours"),
        ):
            v = getattr(c, src)
            if v is not None:
                e[dst] = _r(v)
        out.append(e)
    return out
