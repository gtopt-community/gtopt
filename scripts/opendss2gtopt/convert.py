# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a :class:`~opendss2gtopt.network.Network` as a gtopt planning.

Mapping (balanced phase-equivalent, MW / MVAr / kV / ohm):

==================  =====================================================
OpenDSS             gtopt
==================  =====================================================
Circuit / Vsource   slack bus (``reference_voltage`` = ``pu``,
                    ``reference_theta`` = 0) + ``source`` Generator
                    (``--import-cost``) behind the Thevenin impedance
Line / LineCode     Line (``resistance``/``reactance`` ohm at ``voltage``
                    kV, ``tmax`` = normamps rating in MVA); charging ->
                    Bus ``shunt_q``
Line ``switch=y``   Line (1 mohm tie, no rating)
Transformer         Line ``type=transformer`` (ohm referred to bus_b,
                    ``tap_ratio`` = V_a/V_b off-nominal ratio)
RegControl          ``tap_ratio_min``/``tap_ratio_max`` on the regulator's
                    transformer (continuous optimised taps)
Load                Demand (``lmax`` x LoadShape, ``power_factor``)
Generator           Generator (``pmax``, ``power_factor`` or
                    ``qmin``/``qmax`` for model 3)
PVSystem            Generator ``type=pv`` (Pmpp x irradiance, profile)
Storage             Battery (kWh, kW, efficiencies, reserve, SoC)
Capacitor           Bus ``shunt_q`` (rating at 1 pu; switched banks at
                    their present state)
LoadShape           per-block ``lmax`` / generator profile (``daily``)
==================  =====================================================

Distribution-model fields (``vmin_pu``/``vmax_pu``/``reference_voltage``/
``shunt_q``, ``power_factor``, ``qmin``/``qmax``, taps) are
written only with ``network_model = "lindistflow"``; the default ``dc``
output is a plain DC OPF case.
"""

from __future__ import annotations

import math
from typing import Any

from .network import Network, NetBranch, finalize_capacitor_ratings

NETWORK_MODELS = ("dc", "lindistflow")
TIME_MODES = ("snapshot", "daily")

_BIG_MVA = 1.0e4
# One loss segment is the secant at the full rating: on long rural
# feeders (IEEE 34) it overstates the losses several-fold and the
# inflated flows sink the voltages.  16 segments keep the secant error
# below 1/16 of the rating's loss slope.
LDF_LOSS_SEGMENTS = 16


def _pf(p: float, q: float) -> float | None:
    """Signed power factor (negative = leading); None when unity/undefined."""
    s = math.hypot(p, q)
    if s <= 0.0 or abs(q) < 1e-12 * max(1.0, s):
        return None
    pf = abs(p) / s
    return round(math.copysign(pf, q), 9)


def _profile(net: Network, shape: str, nblocks: int) -> list[float]:
    if nblocks == 1 or not shape or shape not in net.loadshapes:
        return [1.0] * nblocks
    mult = net.loadshapes[shape]
    if not mult:
        return [1.0] * nblocks
    if len(mult) >= nblocks:
        step = len(mult) / nblocks
        return [float(mult[int(i * step)]) for i in range(nblocks)]
    return [float(mult[i % len(mult)]) for i in range(nblocks)]


def _r(v: float, nd: int = 9) -> float:
    return float(round(v, nd))


def build_planning(
    net: Network,
    *,
    name: str | None = None,
    network_model: str = "dc",
    time_mode: str = "snapshot",
    import_cost: float = 50.0,
    der_cost: float = 0.0,
    demand_fail_cost: float = 1000.0,
    vmin: float = 0.95,
    vmax: float = 1.05,
    optimize_taps: bool = True,
    source_impedance: bool = True,
    loss_segments: int | None = None,
) -> dict[str, Any]:
    """Return the gtopt planning dict for *net*."""
    if network_model not in NETWORK_MODELS:
        raise ValueError(f"unknown network_model '{network_model}'")
    if time_mode not in TIME_MODES:
        raise ValueError(f"unknown time_mode '{time_mode}'")
    finalize_capacitor_ratings(net)
    ldf = network_model == "lindistflow"
    nblocks = 24 if time_mode == "daily" else 1
    case = name or net.name

    shunt: dict[str, float] = {}
    for br in net.branches:
        if br.charging_mvar > 0.0:
            for b in (br.bus_a, br.bus_b):
                shunt[b] = shunt.get(b, 0.0) + br.charging_mvar / 2.0

    if ldf:
        # Capacitor banks are bus shunts Q = B·|V|² (rating at 1 p.u.);
        # switched banks enter at their present (solved-case) state.
        for cap in net.capacitors:
            steps = cap.extra["steps"]
            states = cap.extra["states"]
            on = sum(st_q for st_q, st in zip(steps, states) if st)
            if on > 0.0:
                shunt[cap.bus] = shunt.get(cap.bus, 0.0) + on

    src_ideal = f"{net.source_bus}_src"
    use_src_z = source_impedance and (net.source_r > 0.0 or net.source_x > 0.0)
    slack = src_ideal if use_src_z else net.source_bus

    buses: list[dict[str, Any]] = []
    names = [slack] + ([net.source_bus] if use_src_z else [])
    names += [b for b in net.buses if b not in names]
    for uid, bname in enumerate(names, start=1):
        kv = net.buses[bname].kv if bname in net.buses else net.source_kv
        bus: dict[str, Any] = {"uid": uid, "name": bname, "voltage": _r(kv, 6)}
        if bname == slack:
            bus["reference_theta"] = 0.0
            if ldf:
                bus["reference_voltage"] = _r(net.source_pu, 6)
        elif ldf:
            bus["vmin_pu"] = vmin
            bus["vmax_pu"] = vmax
        if ldf and shunt.get(bname, 0.0) > 0.0:
            bus["shunt_q"] = _r(shunt[bname])
        buses.append(bus)

    lines: list[dict[str, Any]] = []
    if use_src_z:
        lines.append(
            {
                "uid": 1,
                "name": "source_impedance",
                "bus_a": src_ideal,
                "bus_b": net.source_bus,
                "voltage": _r(net.source_kv, 6),
                "resistance": _r(net.source_r),
                "reactance": _r(max(net.source_x, 1e-9)),
            }
        )
    for br in net.branches:
        lines.append(_line_json(br, len(lines) + 1, ldf, optimize_taps))

    blocks = [{"uid": i + 1, "duration": 1} for i in range(nblocks)]
    demands = []
    for uid, ld in enumerate(net.loads, start=1):
        prof = _profile(net, ld.extra.get("shape", ""), nblocks)
        d: dict[str, Any] = {
            "uid": uid,
            "name": ld.name,
            "bus": ld.bus,
            "lmax": [[_r(ld.p_mw * m) for m in prof]],
        }
        pf = _pf(ld.p_mw, ld.q_mvar)
        if ldf and pf is not None:
            d["power_factor"] = pf
        demands.append(d)

    gens: list[dict[str, Any]] = [
        {
            "uid": 1,
            "name": "source",
            "bus": slack,
            "pmin": -_BIG_MVA,
            "pmax": _BIG_MVA,
            "gcost": import_cost,
            "type": "import",
        }
    ]
    if ldf:
        gens[0]["qmin"] = -_BIG_MVA
        gens[0]["qmax"] = _BIG_MVA
    profiles: list[dict[str, Any]] = []
    for g in net.generators:
        uid = len(gens) + 1
        gj: dict[str, Any] = {
            "uid": uid,
            "name": g.name,
            "bus": g.bus,
            "pmin": 0.0,
            "pmax": _r(g.p_mw),
            "gcost": der_cost,
            "capacity": _r(g.extra.get("pmpp", g.p_mw)),
        }
        if g.kind == "pv":
            gj["type"] = "pv"
        if ldf:
            if "qmin" in g.extra or "qmax" in g.extra:
                gj["qmin"] = _r(g.extra.get("qmin", 0.0))
                gj["qmax"] = _r(g.extra.get("qmax", 0.0))
            else:
                pf = _pf(g.p_mw, g.q_mvar)
                if pf is not None:
                    gj["power_factor"] = pf
        gens.append(gj)
        shape = g.extra.get("shape", "")
        if nblocks > 1 and shape:
            profiles.append(
                {
                    "uid": len(profiles) + 1,
                    "name": f"{g.name}_profile",
                    "generator": g.name,
                    "profile": [[_profile(net, shape, nblocks)]],
                }
            )

    batteries = []
    for uid, st in enumerate(net.storages, start=1):
        kwh = st.extra["kwh"]
        batteries.append(
            {
                "uid": uid,
                "name": st.name,
                "bus": st.bus,
                "emin": _r(kwh * st.extra["reserve"]),
                "emax": _r(kwh),
                "eini": _r(kwh * st.extra["stored"]),
                "capacity": _r(kwh),
                "input_efficiency": st.extra["eff_charge"],
                "output_efficiency": st.extra["eff_discharge"],
                "pmax_charge": _r(st.p_mw * st.extra["pct_charge"]),
                "pmax_discharge": _r(st.p_mw * st.extra["pct_discharge"]),
            }
        )

    model_options: dict[str, Any] = {
        "use_single_bus": False,
        "use_kirchhoff": True,
        "demand_fail_cost": demand_fail_cost,
        "scale_objective": 1,
    }
    if ldf:
        model_options["network_model"] = "lindistflow"
    if loss_segments is None and ldf:
        loss_segments = LDF_LOSS_SEGMENTS
    if loss_segments is not None:
        model_options["loss_segments"] = int(loss_segments)
    system: dict[str, Any] = {
        "name": case,
        "bus_array": buses,
        "generator_array": gens,
        "demand_array": demands,
        "line_array": lines,
    }
    if batteries:
        system["battery_array"] = batteries
    if profiles:
        system["generator_profile_array"] = profiles
    return {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": model_options,
        },
        "simulation": {
            "block_array": blocks,
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": nblocks, "active": 1}
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": system,
    }


def _line_json(
    br: NetBranch, uid: int, ldf: bool, optimize_taps: bool
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "uid": uid,
        "name": br.name,
        "bus_a": br.bus_a,
        "bus_b": br.bus_b,
        "voltage": _r(br.kv, 6),
        "resistance": _r(br.r),
        "reactance": _r(br.x),
    }
    if br.kind == "transformer":
        line["type"] = "transformer"
        if abs(br.tap - 1.0) > 1e-12:
            line["tap_ratio"] = _r(br.tap)
    elif br.kind == "switch":
        line["type"] = "switch"
    if br.rating_mva:
        line["tmax_ab"] = _r(br.rating_mva, 6)
        line["tmax_ba"] = _r(br.rating_mva, 6)
    reg = br.regulator
    if ldf and optimize_taps and reg is not None:
        # OpenDSS taps the regulated winding (usually 2): V_b/V_a = t, so
        # gtopt's V_a/V_b ratio spans [1/maxtap, 1/mintap].
        line["tap_ratio_min"] = _r(1.0 / reg["maxtap"])
        line["tap_ratio_max"] = _r(1.0 / reg["mintap"])
    return line
