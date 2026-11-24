# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Pieces shared by the native and the legacy (generic) emitters.

* :class:`BuildOptions` -- the study switches (season, compressor mode,
  line pack, booster, plant pressure, ...);
* :func:`check_radial` -- the radial fast-path guard;
* :func:`resolve_profile` -- scalar / named profile / season dict to a
  per-block list;
* :func:`power_system` / :func:`planning_skeleton` -- the electric side
  and the simulation/options block, identical in both outputs.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from gasnet2gtopt.physics import (
    ALPHA_NG,
    T_BASE,
    adiabatic_gamma,
    elevation_exponent,
    elevation_k_factor,
    weymouth_k,
)


@dataclass
class BuildOptions:
    """Study switches applied on top of the gasnet description."""

    season: str = "winter"
    compressor: str = "off"
    linepack: bool = False
    segments: int | None = None
    nblocks: int = 24
    fuel_model: str | None = None
    cs_gamma: float | None = None
    booster: str = "off"
    booster_drive: str | None = None
    plant_pmin: dict[str, float] = field(default_factory=dict)
    derate: bool = False
    plant_pressure: bool = True


def gas_properties(gas: dict[str, Any]) -> tuple[float, float, float]:
    """Network ``(gas_specific_gravity, gas_temperature, compressibility)``."""
    return (
        float(gas.get("gas_specific_gravity", 0.6)),
        float(gas.get("gas_temperature", T_BASE)),
        float(gas.get("compressibility", 0.9)),
    )


def pipe_physics(
    p: dict[str, Any], gas: dict[str, Any], nodes: dict[str, dict[str, Any]]
) -> tuple[float, float]:
    """``(K_e, e^s)`` of a pipe: Weymouth K (explicit or from geometry with
    the network gas properties, overridable per pipe) times the
    elevation factor ``(e^s - 1)/s``."""
    grav, temp, z = gas_properties(gas)
    grav = p.get("gravity", grav)
    temp = p.get("temperature", temp)
    z = p.get("z", z)
    kw = p.get("weymouth_k") or weymouth_k(
        p["length"], p["diameter"], p.get("efficiency", 0.92), grav, temp, z
    )
    s = p.get("elevation_exponent")
    if s is None:
        dh = nodes[p["node_b"]].get("elevation", 0.0) - nodes[p["node_a"]].get(
            "elevation", 0.0
        )
        s = elevation_exponent(dh, grav, z, temp) if dh else 0.0
    return kw * elevation_k_factor(s), math.exp(s)


def check_radial(gas: dict[str, Any]) -> None:
    """Radial guard (``network_model == "radial"``, the legacy form's only one).

    Every arc (pipe or compressor) has a fixed direction a -> b, f >= 0,
    and the undirected arc graph must be a forest, so the tangent outer
    approximation of ``K f^2 <= pi_a - pi_b`` is a convex LP.
    """
    model = gas.get("network_model", "radial")
    if model != "radial":
        raise NotImplementedError(
            f"network_model={model!r}: the legacy form is radial only;"
            " use the native output (linearized pipes + SLP)"
        )
    parent = {n["name"]: n["name"] for n in gas["gas_node_array"]}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    arcs = [(p["node_a"], p["node_b"], p["name"]) for p in gas["gas_pipeline_array"]]
    arcs += [
        (c["node_in"], c["node_out"], c["name"])
        for c in gas.get("compressor_station_array", [])
    ]
    for a, b, name in arcs:
        ra, rb = root(a), root(b)
        if ra == rb:
            raise ValueError(
                f"arc {name} closes a loop: network is meshed;"
                " set network_model='meshed' (native output only)"
            )
        parent[ra] = rb


def resolve_profile(
    value: Any, profiles: dict[str, Any], nblocks: int, season: str
) -> list[float]:
    """Scalar / profile-name / season dict -> per-block list.

    A daily profile is repeated when ``nblocks`` spans several days
    (``nblocks`` a multiple of the profile length).
    """
    if isinstance(value, dict):
        value = value[season]
    if not isinstance(value, str):
        return [float(value)] * nblocks
    if value == "res":
        base = profiles["res_winter"]
        scale = 1.0 if season == "winter" else profiles["res_summer_scale"]
        day = [scale * v for v in base]
    elif value == "solar":
        day = [1200.0 * v for v in profiles["solar_pu"]]
    else:
        day = [float(v) for v in profiles[value]]
    if nblocks > len(day) and nblocks % len(day) == 0:
        return day * (nblocks // len(day))
    return day


@dataclass
class StationModel:
    """Resolved compressor-station settings (shared by both emitters)."""

    mode: str  # "off" or "series"
    drive: str  # "gas" or "electric"
    model: str  # "fixed_fraction" or "saldarriaga"
    alpha: float
    sgamma: float  # Saldarriaga gamma
    gamma_ff: float  # fixed fuel fraction
    is_booster: bool


def station_model(cs: dict[str, Any], opts: BuildOptions) -> StationModel:
    """Applies the study switches to one ``compressor_station`` entry."""
    is_booster = cs.get("role") == "fuel_gas_booster"
    mode = opts.booster if is_booster else opts.compressor
    if mode == "on":
        mode = "series"
    drive = (opts.booster_drive if is_booster else None) or cs.get("drive", "gas")
    if is_booster:  # always the station's own (Saldarriaga) fuel model
        model = cs.get("fuel_model", "saldarriaga")
    else:
        model = opts.fuel_model or cs.get("fuel_model", "fixed_fraction")
    alpha = cs.get("alpha", ALPHA_NG)
    sgamma = (
        opts.cs_gamma
        or cs.get("gamma")
        or adiabatic_gamma(
            cs.get("efficiency", 0.8), cs.get("driver_efficiency", 0.33), alpha
        )
    )
    gamma_ff = cs.get("fuel_fraction") or sgamma * (cs["max_ratio"] ** alpha - 1.0)
    return StationModel(mode, drive, model, alpha, sgamma, gamma_ff, is_booster)


def operating_point(
    cs: dict[str, Any], node_in: dict[str, Any]
) -> tuple[float, list[float], float]:
    """Saldarriaga operating point ``(psi0, [beta0...], p_in0)``."""
    fmax, cr = cs["fmax"], cs["max_ratio"]
    psi0 = cs.get("op_flow", 0.6 * fmax)
    beta0 = cs.get("op_ratio", 1.0 + 0.5 * (cr - 1.0))
    p_in0 = cs.get("op_suction_pressure", 0.5 * (node_in["pmin"] + node_in["pmax"]))
    betas = beta0 if isinstance(beta0, list) else [beta0]
    return psi0, [float(b) for b in betas], p_in0


def plant_pressure_settings(
    link: dict[str, Any], opts: BuildOptions
) -> tuple[float, float, float | None] | None:
    """``(p_min, band, p_nom)`` of a generator gas link, or None (no gate)."""
    p_min = opts.plant_pmin.get(link["generator"], link.get("min_pressure"))
    if not opts.plant_pressure or p_min is None:
        return None
    band = link.get("pressure_band", 0.5)
    p_nom = p_min * link.get("derate_nominal_factor", 1.15) if opts.derate else None
    return float(p_min), float(band), p_nom


def power_system(
    desc: dict[str, Any], opts: BuildOptions, extra_demands: list[dict[str, Any]]
) -> dict[str, Any]:
    """The electric side with profiles resolved per block."""
    prof = desc.get("profiles", {})
    psys = copy.deepcopy(desc["power_system"])
    for g in psys["generator_array"]:
        for fld in ("pmax", "pmin"):  # profile name -> [[per-block]]
            if isinstance(g.get(fld), str):
                g[fld] = [resolve_profile(g[fld], prof, opts.nblocks, opts.season)]
        if isinstance(g.get("capacity"), str):  # profile name -> its peak
            g["capacity"] = max(
                resolve_profile(g["capacity"], prof, opts.nblocks, opts.season)
            )
    for d in psys["demand_array"]:
        d["lmax"] = [resolve_profile(d["lmax"], prof, opts.nblocks, opts.season)]
    for d in extra_demands:
        d["uid"] = len(psys["demand_array"]) + 1
        psys["demand_array"].append(d)
    return psys


def source_rate(
    s: dict[str, Any], prof: dict[str, Any], opts: BuildOptions
) -> tuple[float, list[float] | None]:
    """``(rate bound, per-block cap or None)`` of a gas source: ``fmax`` is
    a number, a season dict or a profile name (a per-block rate cap)."""
    fmax = s["fmax"][opts.season] if isinstance(s["fmax"], dict) else s["fmax"]
    if isinstance(fmax, str):
        cap = resolve_profile(fmax, prof, opts.nblocks, opts.season)
        return max(cap), cap
    return float(fmax), None


def user_constraints(desc: dict[str, Any], first_uid: int) -> list[dict[str, Any]]:
    """The description's extra PAMPL rows (``user_constraints``)."""
    return [
        {"uid": first_uid + i, "name": u["name"], "expression": u["expression"]}
        for i, u in enumerate(desc.get("user_constraints", []))
    ]


def planning_skeleton(nblocks: int) -> dict[str, Any]:
    """Options + one chronological stage of ``nblocks`` hourly blocks."""
    return {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": True,
                "scale_objective": 1000,
                "demand_fail_cost": 1000,
            },
        },
        "simulation": {
            "block_array": [{"uid": b + 1, "duration": 1} for b in range(nblocks)],
            "stage_array": [
                {
                    "uid": 1,
                    "first_block": 0,
                    "count_block": nblocks,
                    "active": 1,
                    "chronological": True,
                }
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
    }


def system_name(desc: dict[str, Any], opts: BuildOptions) -> str:
    """``<case>_<season>_<compressor>[_lp]``."""
    lp = "_lp" if opts.linepack else ""
    return f"{desc['name']}_{opts.season}_{opts.compressor}{lp}"
