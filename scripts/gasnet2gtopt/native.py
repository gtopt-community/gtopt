# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Native emitter: the gas network as gtopt's gas elements.

Maps the gasnet description onto ``gas_node_array``,
``gas_supply_array``, ``gas_demand_array``, ``gas_pipe_array``,
``gas_compressor_array`` and ``gas_turbine_array`` (see
``docs/input-data.md``, "Gas network").  The physics (Weymouth K, line
pack, compressor gamma / kappa, tangent points, capability rows) is left
to gtopt, which derives it from the same fields; the study switches
(``BuildOptions``) are resolved here.
"""

from __future__ import annotations

from typing import Any

from gasnet2gtopt.common import (
    BuildOptions,
    check_radial,
    operating_point,
    planning_skeleton,
    plant_pressure_settings,
    power_system,
    resolve_profile,
    source_rate,
    station_model,
    system_name,
    user_constraints,
)

#: Pipe fields copied verbatim when present in the gasnet description.
_PIPE_FIELDS = (
    "weymouth_k",
    "length",
    "diameter",
    "efficiency",
    "gravity",
    "temperature",
    "z",
    "elevation_exponent",
    "tangent_layout",
    "weymouth_mode",
    "linearization",
    "reference_flow",
    "linepack_coeff",
    "linepack_pressure_ref",
    "linepack_ini",
)

#: Network-level gas properties copied to ``options.gas_options``.
_GAS_OPTION_FIELDS = (
    "gas_specific_gravity",
    "gas_temperature",
    "compressibility",
    "slp_iterations",
    "slp_tolerance",
    "slp_damping",
    "slp_min_flow",
    "slp_initial_flow",
    "slp_proximal_cost",
    "linepack_cyclic",
)


def _block_sched(values: list[float]) -> float | list[list[float]]:
    """Scalar when constant, else a ``[[stage blocks]]`` schedule."""
    if all(v == values[0] for v in values):
        return values[0]
    return [values]


def _nodes(gas: dict[str, Any]) -> list[dict[str, Any]]:
    tie = gas.get("pressure_tiebreak", 1e-4)
    out = []
    for n in gas["gas_node_array"]:
        node: dict[str, Any] = {
            "uid": n["uid"],
            "name": n["name"],
            "pmin": n["pmin"],
            "pmax": n["pmax"],
        }
        for key in ("p_init", "elevation"):
            if key in n:
                node[key] = n[key]
        if tie:
            node["pressure_cost"] = -tie
        out.append(node)
    return out


def _supplies(
    gas: dict[str, Any], prof: dict[str, Any], opts: BuildOptions
) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(gas.get("gas_source_array", []), start=1):
        rate, cap = source_rate(s, prof, opts)
        sup: dict[str, Any] = {
            "uid": s.get("uid", i),
            "name": s["name"],
            "gas_node": s["node"],
            "fmax": rate if cap is None else _block_sched(cap),
            "cost": s["cost"],
        }
        for key in ("type", "volume"):
            if s.get(key) is not None:
                sup[key] = s[key]
        out.append(sup)
    return out


def _demands(
    gas: dict[str, Any], prof: dict[str, Any], opts: BuildOptions
) -> list[dict[str, Any]]:
    out = []
    for i, d in enumerate(gas.get("gas_demand_array", []), start=1):
        load = resolve_profile(d["load"], prof, opts.nblocks, opts.season)
        out.append(
            {
                "uid": d.get("uid", i),
                "name": d["name"],
                "gas_node": d["node"],
                "load": _block_sched(load),
                "fail_cost": d["fail_cost"],
            }
        )
    return out


def _pipes(gas: dict[str, Any], opts: BuildOptions) -> list[dict[str, Any]]:
    meshed = gas.get("network_model", "radial") == "meshed"
    out = []
    for i, p in enumerate(gas["gas_pipeline_array"], start=1):
        pipe: dict[str, Any] = {
            "uid": p.get("uid", i),
            "name": p["name"],
            "gas_node_a": p["node_a"],
            "gas_node_b": p["node_b"],
        }
        pipe.update({k: p[k] for k in _PIPE_FIELDS if k in p})
        if p.get("linepack_p_ref") is not None:  # gasnet name
            pipe["linepack_pressure_ref"] = p["linepack_p_ref"]
        pipe["fmax"] = p["fmax"]
        pipe["segments"] = opts.segments or p.get("segments", 8)
        if meshed:
            pipe.setdefault("weymouth_mode", "linearized")
        if opts.linepack and p.get("linepack", False):
            pipe["linepack"] = True
        out.append(pipe)
    # GasTransfer: capacity-only arc (no pressure coupling), e.g. a
    # delivery through a parallel system that would close a loop.
    for t in gas.get("gas_transfer_array", []):
        if t.get("cost"):
            raise NotImplementedError(
                f"gas transfer {t['name']}: a transfer cost is not supported"
                " natively; use --legacy"
            )
        out.append(
            {
                "uid": len(out) + 1,
                "name": t["name"],
                "gas_node_a": t["from_node"],
                "gas_node_b": t["to_node"],
                "fmax": t["fmax"],
                "weymouth_mode": "none",
            }
        )
    return out


def _compressor(
    cs: dict[str, Any], node_in: dict[str, Any], opts: BuildOptions, uid: int
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "uid": cs.get("uid", uid),
        "name": cs["name"],
        "gas_node_in": cs["node_in"],
        "gas_node_out": cs["node_out"],
        "fmax": cs["fmax"],
    }
    if "role" in cs:
        out["type"] = cs["role"]
    if cs.get("bidirectional", False):
        # open valve of a meshed network: signed flow, pi_out = pi_in
        out.update({"max_ratio": 1.0, "fuel_model": "none", "bidirectional": True})
        return out
    sm = station_model(cs, opts)
    if sm.mode == "off":
        # bypassed station: a lossless regulator (pi_out <= pi_in)
        out.update({"max_ratio": 1.0, "fuel_model": "none", "allow_bypass": True})
        return out
    out["max_ratio"] = cs["max_ratio"]
    out["fuel_model"] = sm.model
    out["alpha"] = sm.alpha
    out["gamma"] = sm.sgamma
    if sm.model == "fixed_fraction":
        out["fuel_fraction"] = sm.gamma_ff
        out["allow_bypass"] = True
    elif sm.model == "saldarriaga":
        psi0, betas, p_in0 = operating_point(cs, node_in)
        out.update(
            {
                "op_flow": psi0,
                "op_ratio": betas,
                "op_suction_pressure": p_in0,
                "allow_bypass": bool(cs.get("allow_bypass", False)),
            }
        )
    else:
        raise ValueError(f"{cs['name']}: unknown fuel_model {sm.model!r}")
    if sm.drive == "electric":
        out.update(
            {
                "drive": "electric",
                "bus": cs["bus"],
                "driver_efficiency": cs.get("driver_efficiency", 0.33),
                "motor_efficiency": cs.get("motor_efficiency", 0.95),
                "power_max": cs.get("rating_mw", 5.0),
            }
        )
    elif sm.drive != "gas":
        raise ValueError(f"{cs['name']}: unknown drive {sm.drive!r}")
    return out


def _turbines(gas: dict[str, Any], opts: BuildOptions) -> list[dict[str, Any]]:
    out = []
    for i, g in enumerate(gas.get("generator_gas_link_array", []), start=1):
        gt: dict[str, Any] = {
            "uid": g.get("uid", i),
            "name": f"{g['generator']}_gas",
            "generator": g["generator"],
            "gas_node": g["gas_node"],
            "heat_rate": g["heat_rate"],
        }
        settings = plant_pressure_settings(g, opts)
        if settings is not None:
            p_min, band, p_nom = settings
            gt["min_pressure"] = p_min
            gt["pressure_band"] = band
            if p_nom is not None:
                gt["nominal_pressure"] = p_nom
                if "derate_min_output" in g:
                    gt["derate_min_output"] = g["derate_min_output"]
        out.append(gt)
    return out


def build_native(
    desc: dict[str, Any], opts: BuildOptions
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lowers a gasnet description to native gtopt gas elements.

    Returns ``(planning, meta)``; ``meta`` maps node / pipe / compressor
    names to their uids for result readers.
    """
    gas = desc["gas_network"]
    if gas.get("network_model", "radial") != "meshed":
        check_radial(gas)
    prof = desc.get("profiles", {})
    nodes = {n["name"]: n for n in gas["gas_node_array"]}
    system: dict[str, Any] = {
        "name": system_name(desc, opts),
        **power_system(desc, opts, []),
        "gas_node_array": _nodes(gas),
        "gas_supply_array": _supplies(gas, prof, opts),
        "gas_demand_array": _demands(gas, prof, opts),
        "gas_pipe_array": _pipes(gas, opts),
        "gas_compressor_array": [
            _compressor(cs, nodes[cs["node_in"]], opts, i)
            for i, cs in enumerate(gas.get("compressor_station_array", []), start=1)
        ],
        "gas_turbine_array": _turbines(gas, opts),
    }
    ucs = user_constraints(desc, len(system.get("user_constraint_array", [])) + 1)
    if ucs:
        system.setdefault("user_constraint_array", []).extend(ucs)
    for key in ("gas_compressor_array", "gas_turbine_array"):
        if not system[key]:
            del system[key]
    planning = planning_skeleton(opts.nblocks)
    gas_opts = {k: gas[k] for k in _GAS_OPTION_FIELDS if k in gas}
    if gas_opts:
        planning["options"]["gas_options"] = gas_opts
    planning["system"] = system
    meta = {
        "nodes": {n["name"]: n["uid"] for n in system["gas_node_array"]},
        "pipes": {p["name"]: p["uid"] for p in system["gas_pipe_array"]},
        "compressors": {
            c["name"]: c["uid"] for c in system.get("gas_compressor_array", [])
        },
        "demands": {d["name"]: d["uid"] for d in system["gas_demand_array"]},
    }
    return planning, meta
