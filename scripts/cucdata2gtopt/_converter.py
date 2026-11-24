# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Build the gtopt planning JSON of the individual-unit UC (IUC).

Mapping of ``StarGenLite_Clustered-UC.gms`` (first solve, ``ta = t``):

=====================  ==================================================
GAMS                   gtopt
=====================  ==================================================
bus ``i``              ``Bus`` (bus 1 = ``pSlack`` = reference bus)
circuit ``iic``        ``Line`` (reactance ``X``, ``tmax = Pmax``); the
                       PTDF network of GAMS == gtopt DC Kirchhoff
``pDemand*pBusProfile``  ``Demand.lmax`` per bus, ``fcost = pENSCost``
``pIntermGen``         ``Generator`` + ``CapacityProfile``; spillover
                       cost = ``pWindCurtCost``
thermal unit ``t``     ``Generator`` (pmin 0, gcost = variable cost) +
                       ``Commitment`` (pmin, no-load = ``pFixCost``,
                       startup / shutdown cost, min up / down)
``e2ReserveUp/Dw``     ``ReserveZone`` (hard ``urreq`` / ``drreq``) +
                       one ``ReserveProvision`` per unit (urcost/drcost
                       = 0.4 / 0.2 x ``pFixCost``)
``eMaxOut*``,          ``UserConstraint`` rows (``paper`` formulation),
``eRampUp/Dw``         see :func:`unit_constraints`
``vCommit.fx`` (t=0)   ``Commitment.fixed_status`` on the pinned periods
=====================  ==================================================

The ``native`` formulation replaces the user-constraint rows with the
built-in ``Commitment`` ramp / startup-shutdown-ramp fields, which do not
couple the reserves to the ramps (a relaxation of the paper model).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cucdata2gtopt._model import UnitParams, derive_unit
from cucdata2gtopt._reader import CucData

FORMULATIONS = ("paper", "native")
#: ``chronological``: one chronological stage, the full IUC model.
#: ``block``: the same hours as non-chronological blocks -- gtopt's block
#: commitment (per-block ``Pmin u <= g <= Pmax u``, no startup / shutdown
#: costs, min up / down, ramps or other intertemporal rows); a relaxation
#: of the IUC used to measure the error of a commitment without
#: chronology.  Needs a gtopt whose Commitment follows the stage.
MODES = ("chronological", "block")

#: Name of the single system-wide reserve zone.
RESERVE_ZONE = "secondary"


@dataclass(frozen=True)
class CaseOptions:
    """Conversion options (see the CLI for their meaning)."""

    system: int = 39
    reserve_pct: float | None = None
    scale: int | None = None
    formulation: str = "paper"
    mode: str = "chronological"
    name: str | None = None


def published_scale(data: CucData) -> int:
    """Units per cluster in the workbook (10 for both published systems)."""
    counts = set(data.units_per_cluster().values())
    if len(counts) != 1:
        raise ValueError(f"clusters have different unit counts: {sorted(counts)}")
    return counts.pop()


def default_reserve_pct(data: CucData) -> float:
    """The workbook's reserve requirement as % of demand (rounded)."""
    ratios = [r / d for r, d in zip(data.reserve_up, data.demand, strict=True) if d]
    return round(100.0 * max(ratios, default=0.0), 6)


def _fmt(value: float) -> str:
    return f"{value:.12g}"


def _pct_tag(pct: float) -> str:
    return _fmt(pct).replace(".", "p")


def case_name(opts: CaseOptions, data: CucData) -> str:
    """Default case name, e.g. ``cuc39_r10_s10``."""
    if opts.name:
        return opts.name
    pct = (
        opts.reserve_pct if opts.reserve_pct is not None else default_reserve_pct(data)
    )
    scale = opts.scale if opts.scale is not None else published_scale(data)
    suffix = "" if opts.formulation == "paper" else f"_{opts.formulation}"
    if opts.mode != "chronological":
        suffix += f"_{opts.mode}"
    return f"cuc{opts.system}_r{_pct_tag(pct)}_s{scale}{suffix}"


def build_units(data: CucData, scale: int) -> list[UnitParams]:
    """Individual units of the IUC model at *scale* units per cluster.

    At the published scale the ``Generation`` sheet is used as is; any
    other scale replicates each cluster's parameters *scale* times.
    """
    if scale == published_scale(data):
        return [derive_unit(u) for u in data.units]
    return [
        derive_unit(c, f"{c.name}_{k + 1}") for c in data.clusters for k in range(scale)
    ]


def _status(unit: str) -> str:
    return f"commitment('{unit}_uc')"


def _terms(pairs: list[tuple[float, str]]) -> str:
    out: list[str] = []
    for coef, ref in pairs:
        if coef == 0.0:
            continue
        sign = "-" if coef < 0 else "+"
        mag = abs(coef)
        body = ref if mag == 1.0 else f"{_fmt(mag)} * {ref}"
        out.append(f"{sign} {body}")
    text = " ".join(out)
    return text[2:] if text.startswith("+ ") else text


def unit_constraints(u: UnitParams, nblocks: int) -> list[tuple[str, str]]:
    """``(name, expression)`` user constraints transcribing the GAMS
    eMaxOut/eMaxOut2/eMaxOut3 and eRampUp/eRampDw rows of unit *u*.

    gtopt variables: ``g`` generation (total output), ``u/y/z`` commitment
    status/startup/shutdown, ``r+/r-`` reserve provision.  GAMS writes them
    on the output above minimum ``p1 = g - Pmin u``.  ``z_{t+1}`` and the
    ramp lags use the ``_prev`` attributes on blocks ``2..T``.
    """
    gen = f"generator('{u.name}')"
    res = f"reserve_provision('{u.name}_res')"
    com = _status(u.name)
    last = nblocks
    lag = f"for(block in 2..{last})"
    rows: list[tuple[str, str]] = []

    # SU/SD capability: g_t + r+_t - P u_t + a_y y_t + a_z z_{t+1} <= 0
    for k, (a_y, a_z) in enumerate(u.capacity_rows(), start=1):
        if a_z == 0.0:
            if a_y > 0.0:
                expr = _terms(
                    [
                        (1.0, f"{gen}.generation"),
                        (1.0, f"{res}.up"),
                        (-u.pmax, f"{com}.status"),
                        (a_y, f"{com}.startup"),
                    ]
                )
                rows.append((f"{u.name}_maxout{k}", f"{expr} <= 0"))
            continue
        expr = _terms(
            [
                (1.0, f"{gen}.generation_prev"),
                (1.0, f"{res}.up_prev"),
                (-u.pmax, f"{com}.status_prev"),
                (a_y, f"{com}.startup_prev"),
                (a_z, f"{com}.shutdown"),
            ]
        )
        rows.append((f"{u.name}_maxout{k}", f"{expr} <= 0, {lag}"))
        if a_y > 0.0:  # t = T: z_{T+1} = 0
            expr = _terms(
                [
                    (1.0, f"{gen}.generation"),
                    (1.0, f"{res}.up"),
                    (-u.pmax, f"{com}.status"),
                    (a_y, f"{com}.startup"),
                ]
            )
            rows.append(
                (f"{u.name}_maxout{k}_last", f"{expr} <= 0, for(block = {last})")
            )

    # eMinOutput: p1_t - r-_t >= 0, i.e. g - r- >= Pmin u.  gtopt's own
    # down-headroom row uses the generator floor (0 here) on master, so
    # the commitment-conditional floor is stated explicitly.
    rows.append(
        (
            f"{u.name}_minout",
            _terms(
                [
                    (1.0, f"{gen}.generation"),
                    (-1.0, f"{res}.dn"),
                    (-u.pmin, f"{com}.status"),
                ]
            )
            + " >= 0",
        )
    )

    ini = (u.ini_out - u.pmin) * u.ini_uc
    # eRampUp: p1_t - p1_{t-1} + r+_t <= RU u_t
    rows.append(
        (
            f"{u.name}_rampup",
            _terms(
                [
                    (1.0, f"{gen}.generation"),
                    (-1.0, f"{gen}.generation_prev"),
                    (-(u.pmin + u.ramp_up), f"{com}.status"),
                    (u.pmin, f"{com}.status_prev"),
                    (1.0, f"{res}.up"),
                ]
            )
            + f" <= 0, {lag}",
        )
    )
    rows.append(
        (
            f"{u.name}_rampup_first",
            _terms(
                [
                    (1.0, f"{gen}.generation"),
                    (-(u.pmin + u.ramp_up), f"{com}.status"),
                    (1.0, f"{res}.up"),
                ]
            )
            + f" <= {_fmt(ini)}, for(block = 1)",
        )
    )
    # eRampDw: p1_t - p1_{t-1} - r-_t >= -RD u_{t-1}
    rows.append(
        (
            f"{u.name}_rampdn",
            _terms(
                [
                    (1.0, f"{gen}.generation"),
                    (-1.0, f"{gen}.generation_prev"),
                    (-u.pmin, f"{com}.status"),
                    (u.pmin + u.ramp_dn, f"{com}.status_prev"),
                    (-1.0, f"{res}.dn"),
                ]
            )
            + f" >= 0, {lag}",
        )
    )
    rows.append(
        (
            f"{u.name}_rampdn_first",
            _terms(
                [
                    (1.0, f"{gen}.generation"),
                    (-u.pmin, f"{com}.status"),
                    (-1.0, f"{res}.dn"),
                ]
            )
            + f" >= {_fmt(ini - u.ramp_dn * u.ini_uc)}, for(block = 1)",
        )
    )
    return rows


def _commitment(
    u: UnitParams, uid: int, nblocks: int, formulation: str
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "uid": uid,
        "name": f"{u.name}_uc",
        "generator": u.name,
        "pmin": u.pmin,
        "noload_cost": u.fix_cost,
        "startup_cost": u.startup_cost,
        "shutdown_cost": u.shutdown_cost,
        "min_up_time": u.min_up_periods,
        "min_down_time": u.min_dn_periods,
        "initial_status": u.ini_uc,
        # gtopt does not carry the remaining min-up/down time across t=0:
        # the GAMS fixing (vCommit.fx for ord(p) <= pIniUp + pIniDw) goes to
        # fixed_status below.
        "initial_hours": max(abs(u.ini_state), u.min_up, u.min_dn),
        "initial_power": u.ini_out if u.ini_uc else 0.0,
    }
    pinned = min(u.pinned_periods, nblocks)
    if pinned:
        entry["fixed_status"] = [
            [float(u.ini_uc) if b < pinned else -1.0 for b in range(nblocks)]
        ]
    if formulation == "native":
        entry["ramp_up"] = u.ramp_up
        entry["ramp_down"] = u.ramp_dn
        entry["startup_ramp"] = u.su_cap
        entry["shutdown_ramp"] = u.sd_cap
    return entry


def build_case(data: CucData, opts: CaseOptions) -> dict[str, Any]:
    """Return the gtopt planning dict of the IUC case."""
    if opts.formulation not in FORMULATIONS:
        raise ValueError(f"formulation must be one of {FORMULATIONS}")
    if opts.mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    active = data.active_scenarios()
    if len(active) != 1:
        raise ValueError(f"exactly one active scenario supported, got {active}")
    scen = active[0]

    base = published_scale(data)
    scale = opts.scale if opts.scale is not None else base
    if scale < 1:
        raise ValueError("scale must be >= 1")
    factor = scale / base
    nblocks = len(data.periods)
    demand = [d * factor for d in data.demand]
    if opts.reserve_pct is None:
        res_up = [r * factor for r in data.reserve_up]
        res_dn = [r * factor for r in data.reserve_dn]
    else:
        res_up = [d * opts.reserve_pct / 100.0 for d in demand]
        res_dn = list(res_up)

    slack = int(data.params.get("pSlack", 1))
    bus_array: list[dict[str, Any]] = []
    for bus in data.buses:
        entry: dict[str, Any] = {"uid": bus, "name": f"b{bus}"}
        if bus == slack:
            entry["reference_theta"] = 0.0
        bus_array.append(entry)

    line_array = [
        {
            "uid": k,
            "name": f"l{ln.bus_from}_{ln.bus_to}_{ln.circuit}",
            "bus_a": f"b{ln.bus_from}",
            "bus_b": f"b{ln.bus_to}",
            "reactance": ln.x,
            "tmax_ab": ln.pmax * factor,
            "tmax_ba": ln.pmax * factor,
        }
        for k, ln in enumerate((ln for ln in data.lines if ln.in_service), start=1)
    ]

    ens_cost = data.params.get("pENSCost", 10000.0)
    demand_array: list[dict[str, Any]] = []
    for bus in data.buses:
        bf = data.bus_factor.get(bus, 0.0)
        if bf <= 0.0:
            continue
        demand_array.append(
            {
                "uid": len(demand_array) + 1,
                "name": f"d{bus}",
                "bus": f"b{bus}",
                "lmax": [[d * bf for d in demand]],
                "fcost": ens_cost,
            }
        )

    units = build_units(data, scale)
    generator_array: list[dict[str, Any]] = []
    commitment_array: list[dict[str, Any]] = []
    provision_array: list[dict[str, Any]] = []
    user_constraints: list[dict[str, Any]] = []
    for u in units:
        uid = len(generator_array) + 1
        generator_array.append(
            {
                "uid": uid,
                "name": u.name,
                "bus": f"b{u.bus}",
                "pmin": 0.0,
                "pmax": u.pmax,
                "capacity": u.pmax,
                "gcost": u.var_cost,
            }
        )
        commitment_array.append(_commitment(u, uid, nblocks, opts.formulation))
        provision_array.append(
            {
                "uid": uid,
                "name": f"{u.name}_res",
                "generator": u.name,
                "reserve_zones": [RESERVE_ZONE],
                "ur_provision_factor": 1.0,
                "dr_provision_factor": 1.0,
                "urmax": u.reserve_max,
                "drmax": u.reserve_max,
                "urcost": u.reserve_up_cost,
                "drcost": u.reserve_dn_cost,
            }
        )
        # The user rows are intertemporal (lags, startup / shutdown):
        # the block formulation has no chronology to attach them to.
        if opts.formulation == "paper" and opts.mode == "chronological":
            for name, expr in unit_constraints(u, nblocks):
                user_constraints.append(
                    {"uid": len(user_constraints) + 1, "name": name, "expression": expr}
                )

    profile_array: list[dict[str, Any]] = []
    curt_cost = data.params.get("pWindCurtCost", 0.0)
    for bus, series in sorted(data.intermittent.get(scen, {}).items()):
        cap = max(series) * factor
        if cap <= 0.0:
            continue
        name = f"ig{bus}"
        uid = len(generator_array) + 1
        generator_array.append(
            {
                "uid": uid,
                "name": name,
                "bus": f"b{bus}",
                "pmin": 0.0,
                "pmax": cap,
                "capacity": cap,
                "gcost": 0.0,
            }
        )
        profile_array.append(
            {
                "uid": len(profile_array) + 1,
                "name": f"{name}_profile",
                "owner_kind": "generator",
                "owner": name,
                "profile": [[[v * factor / cap for v in series]]],
                "scost": curt_cost,
            }
        )

    system: dict[str, Any] = {
        "name": case_name(opts, data),
        "bus_array": bus_array,
        "line_array": line_array,
        "demand_array": demand_array,
        "generator_array": generator_array,
        "commitment_array": commitment_array,
        "reserve_zone_array": [
            {"uid": 1, "name": RESERVE_ZONE, "urreq": [res_up], "drreq": [res_dn]}
        ],
        "reserve_provision_array": provision_array,
    }
    if profile_array:
        system["capacity_profile_array"] = profile_array
    if user_constraints:
        system["user_constraint_array"] = user_constraints

    simulation = {
        "annual_discount_rate": 0.0,
        "block_array": [{"uid": b + 1, "duration": 1} for b in range(nblocks)],
        "stage_array": [
            {
                "uid": 1,
                "first_block": 0,
                "count_block": nblocks,
                "active": 1,
                "chronological": opts.mode == "chronological",
            }
        ],
        "scenario_array": [{"uid": 1, "probability_factor": 1}],
    }
    options = {
        "output_format": "csv",
        "output_compression": "uncompressed",
        "model_options": {
            "use_single_bus": False,
            "use_kirchhoff": True,
            "line_losses_mode": "none",
            "demand_fail_cost": ens_cost,
        },
    }
    return {"options": options, "simulation": simulation, "system": system}


def write_case(case: dict[str, Any], out_dir: Path) -> Path:
    """Write ``<out_dir>/<name>.json``; return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case['system']['name']}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(case, fh, indent=1)
        fh.write("\n")
    return path
