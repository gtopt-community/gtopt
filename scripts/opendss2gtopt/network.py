# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Balanced phase-equivalent network built from a parsed OpenDSS circuit.

gtopt's distribution model (LinDistFlow, see the formulation document)
is single-phase-equivalent: one squared-voltage variable per bus, total
three-phase P and Q per branch.  This module reduces the OpenDSS
phase-coordinate circuit to that model:

* bus base voltages (line-to-line kV) are propagated from the source
  through lines and transformer windings and snapped to the circuit's
  ``voltagebases``;
* a branch with ``n`` phases gets the positive-sequence impedance
  ``Z1 = mean(Zii) - mean(Zij)`` scaled by ``3/n`` (``phase-count``
  scaling, the default): a single-phase lateral carrying ``P`` then
  shows the same per-unit voltage drop as the loaded phase.  ``pos-seq``
  keeps ``Z1`` unscaled (OpenDSS ``MakePosSeq``);
* single-phase transformer units between the same two buses (regulator
  banks) are merged into one equivalent branch (parallel impedance,
  summed rating);
* loads, generators, PV, storage and capacitors keep their total
  three-phase P / Q at the bus.

The per-phase data (phases, nodes, phase impedance matrices, per-phase
loads) is kept in :attr:`Network.phase_data` so a later unbalanced
model can use it.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .dss_parser import DssCircuit, DssObject
from .dss_values import (
    DssValueError,
    length_factor_m,
    parse_bus,
    split_items,
    strip_delims,
    to_bool,
    to_float,
    to_floats,
    to_int,
)
from .impedance import line_impedance

logger = logging.getLogger(__name__)

SQRT3 = math.sqrt(3.0)
SCALINGS = ("phase-count", "pos-seq")


@dataclass
class NetBus:
    """A bus of the equivalent network."""

    name: str
    kv: float = 0.0  # line-to-line base kV
    phases: set[int] = field(default_factory=set)


@dataclass
class NetBranch:
    """A line, switch or transformer between two buses."""

    name: str
    kind: str  # "line" | "switch" | "transformer"
    bus_a: str
    bus_b: str
    kv: float  # base kV the ohms refer to (bus_b side)
    r: float  # ohm (equivalent)
    x: float  # ohm (equivalent)
    nphases: int
    rating_mva: float | None = None
    emerg_mva: float | None = None
    tap: float = 1.0  # gtopt tap_ratio = V_a/V_b off-nominal ratio
    charging_mvar: float = 0.0  # total line charging at 1 pu
    regulator: dict[str, float] | None = None
    length_m: float | None = None
    members: list[str] = field(default_factory=list)


@dataclass
class NetInjection:
    """Load, generator, PV, storage or capacitor at a bus."""

    name: str
    kind: str
    bus: str
    p_mw: float = 0.0
    q_mvar: float = 0.0
    nphases: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Network:
    """The balanced phase-equivalent network."""

    name: str
    source_bus: str
    source_pu: float
    source_kv: float
    source_r: float = 0.0
    source_x: float = 0.0
    freq: float = 60.0
    buses: dict[str, NetBus] = field(default_factory=dict)
    branches: list[NetBranch] = field(default_factory=list)
    loads: list[NetInjection] = field(default_factory=list)
    generators: list[NetInjection] = field(default_factory=list)
    storages: list[NetInjection] = field(default_factory=list)
    capacitors: list[NetInjection] = field(default_factory=list)
    loadshapes: dict[str, list[float]] = field(default_factory=dict)
    phase_data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _warn(net: Network, msg: str) -> None:
    logger.warning(msg)
    net.warnings.append(msg)


def _fprop(obj: DssObject, key: str, default: float) -> float:
    raw = obj.get(key)
    return default if raw is None else to_float(raw)


def _enabled(obj: DssObject) -> bool:
    raw = obj.get("enabled")
    return True if raw is None else to_bool(raw)


def _voltage_bases(circuit: DssCircuit) -> list[float]:
    raw = circuit.options.get("voltagebases")
    return sorted(to_floats(raw)) if raw else []


def _snap(kv: float, bases: list[float]) -> float:
    if not bases or kv <= 0:
        return kv
    best = min(bases, key=lambda b: abs(math.log(b / kv)))
    return best if abs(best / kv - 1.0) < 0.15 else kv


def _winding_ll_kv(xf: DssObject, k: int) -> float:
    """Line-to-line kV of transformer winding *k*."""
    w = xf.windings[k]
    kv = to_float(w.get("kv", "12.47"))
    phases = to_int(xf.get("phases") or "3")
    conn = (w.get("conn") or "wye").lower()
    if phases == 1 and conn.startswith(("w", "y", "ln")):
        return kv * SQRT3
    return kv


def _scale(n: int, scaling: str) -> float:
    return 3.0 / n if scaling == "phase-count" else 1.0


def build_network(
    circuit: DssCircuit,
    *,
    scaling: str = "phase-count",
    line_charging: bool = True,
) -> Network:
    """Reduce *circuit* to the balanced phase-equivalent :class:`Network`."""
    if scaling not in SCALINGS:
        raise ValueError(f"unknown scaling '{scaling}' (expected {SCALINGS})")
    src = circuit.get("vsource", "source") or next(
        iter(circuit.of("vsource").values()), None
    )
    if src is None:
        raise DssValueError("the script defines no Circuit / Vsource")
    src_bus, _ = parse_bus(src.get("bus1") or "sourcebus")
    freq = to_float(circuit.options.get("defaultbasefrequency", "60"))
    basekv = _fprop(src, "basekv", 115.0)
    net = Network(
        name=circuit.name or src.name,
        source_bus=src_bus,
        source_pu=_fprop(src, "pu", 1.0),
        source_kv=basekv,
        freq=freq,
    )
    _source_impedance(net, src)

    bases = _voltage_bases(circuit)
    net.buses[src_bus] = NetBus(src_bus, kv=_snap(basekv, bases))
    _add_branches(net, circuit, scaling, line_charging)
    _propagate_kv(net, bases)
    _finish_branches(net)
    _add_injections(net, circuit, scaling)
    _collect_loadshapes(net, circuit)
    return net


def _source_impedance(net: Network, src: DssObject) -> None:
    kv = net.source_kv
    if src.get("r1") is not None or src.get("x1") is not None:
        net.source_r = _fprop(src, "r1", 1.65)
        net.source_x = _fprop(src, "x1", 6.6)
        return
    mvasc3 = _fprop(src, "mvasc3", 2000.0)
    x1r1 = _fprop(src, "x1r1", 4.0)
    z = kv * kv / mvasc3
    net.source_x = z / math.sqrt(1.0 + 1.0 / (x1r1 * x1r1))
    net.source_r = net.source_x / x1r1


def _length_m(circuit: DssCircuit, line: DssObject) -> float | None:
    """Line length in metres (None when neither line nor code has units)."""
    units = (line.get("units") or "none").strip().lower()
    if units == "none" and line.get("linecode"):
        code = circuit.get("linecode", (line.get("linecode") or "").strip("\"'"))
        if code is not None:
            units = (code.get("units") or "none").strip().lower()
    if units == "none":
        return None
    return _fprop(line, "length", 1.0) * length_factor_m(units)


def _add_branches(
    net: Network, circuit: DssCircuit, scaling: str, line_charging: bool
) -> None:
    w = 2 * math.pi * net.freq
    lines_pd: list[dict[str, Any]] = []
    for line in circuit.of("line").values():
        if not _enabled(line):
            continue
        a, na = parse_bus(line.get("bus1") or "")
        b, nb = parse_bus(line.get("bus2") or "")
        if not a or not b or a == b:
            _warn(net, f"line.{line.name}: degenerate buses, skipped")
            continue
        imp = line_impedance(circuit, line, net.freq)
        n = imp.nphases
        z1 = imp.z1() * _scale(n, scaling)
        c1 = imp.c1()
        is_switch = to_bool(line.get("switch") or "n")
        br = NetBranch(
            name=line.name,
            kind="switch" if is_switch else "line",
            bus_a=a,
            bus_b=b,
            kv=0.0,
            r=float(z1.real),
            x=float(z1.imag),
            nphases=n,
            length_m=_length_m(circuit, line),
            members=[f"line.{line.name}"],
        )
        br.regulator = {"normamps": imp.normamps, "emergamps": imp.emergamps}
        if line_charging and not is_switch:
            # Total charging at 1 pu is w*C1*V_LL^2*(n/3) -- the kV is
            # applied once the bus base is known (_finish_branches).
            br.charging_mvar = w * c1 * (n / 3.0)
        net.branches.append(br)
        for bus, nodes in ((a, na), (b, nb)):
            net.buses.setdefault(bus, NetBus(bus)).phases.update(nodes or [1, 2, 3][:n])
        lines_pd.append(
            {
                "name": line.name,
                "bus1": a,
                "bus2": b,
                "nodes1": na or list(range(1, n + 1)),
                "nodes2": nb or list(range(1, n + 1)),
                "r_ohm": imp.z.real.tolist(),
                "x_ohm": imp.z.imag.tolist(),
                "c_farad": imp.c.tolist(),
                "normamps": imp.normamps,
            }
        )
    net.phase_data["lines"] = lines_pd

    groups: dict[tuple[str, str], list[NetBranch]] = defaultdict(list)
    xf_pd: list[dict[str, Any]] = []
    regs = _regulators(circuit)
    for xf in circuit.of("transformer").values():
        if not _enabled(xf):
            continue
        for br in _transformer_branches(net, xf, scaling, regs, xf_pd):
            groups[(br.bus_a, br.bus_b)].append(br)
    net.phase_data["transformers"] = xf_pd
    for members in groups.values():
        net.branches.append(_merge_parallel(members))


def _regulators(circuit: DssCircuit) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for rc in circuit.of("regcontrol").values():
        if not _enabled(rc):
            continue
        xf = (rc.get("transformer") or "").strip("\"'").lower()
        out[xf] = {
            "winding": float(to_int(rc.get("winding") or "1")),
            "vreg": _fprop(rc, "vreg", 120.0),
            "band": _fprop(rc, "band", 3.0),
            "ptratio": _fprop(rc, "ptratio", 60.0),
        }
    return out


def _transformer_branches(
    net: Network,
    xf: DssObject,
    scaling: str,
    regs: dict[str, dict[str, float]],
    xf_pd: list[dict[str, Any]],
) -> list[NetBranch]:
    nw = len(xf.windings)
    phases = to_int(xf.get("phases") or "3")
    buses = [parse_bus(w.get("bus") or "") for w in xf.windings]
    for (bname, nodes), _w in zip(buses, xf.windings):
        if bname:
            net.buses.setdefault(bname, NetBus(bname)).phases.update(
                nodes or [1, 2, 3][:phases]
            )
    kva = to_float(xf.windings[0].get("kva", "1000"))
    xhl = _fprop(xf, "xhl", 7.0)
    xf_pd.append(
        {
            "name": xf.name,
            "phases": phases,
            "windings": [dict(w) for w in xf.windings],
            "xhl": xhl,
        }
    )
    if nw == 2:
        pairs = [(0, 1, xhl)]
    elif nw == 3:
        xht = _fprop(xf, "xht", 35.0)
        xlt = _fprop(xf, "xlt", 30.0)
        _warn(net, f"transformer.{xf.name}: 3-winding, modelled as a star")
        return _three_winding(net, xf, phases, scaling, (xhl, xht, xlt), buses)
    else:
        _warn(net, f"transformer.{xf.name}: {nw} windings unsupported, skipped")
        return []
    out = []
    for i, j, xpct in pairs:
        rpct = to_float(xf.windings[i].get("%r", "0.2")) + to_float(
            xf.windings[j].get("%r", "0.2")
        )
        out.append(
            _xf_branch(net, xf, i, j, rpct, xpct, phases, scaling, kva, regs, buses)
        )
    return out


def _xf_branch(
    net: Network,
    xf: DssObject,
    i: int,
    j: int,
    rpct: float,
    xpct: float,
    phases: int,
    scaling: str,
    kva: float,
    regs: dict[str, dict[str, float]],
    buses: list[tuple[str, list[int]]],
    bus_override: tuple[str, str] | None = None,
) -> NetBranch:
    wi, wj = xf.windings[i], xf.windings[j]
    kv_i = _winding_ll_kv(xf, i)
    kv_j = _winding_ll_kv(xf, j)
    # Per-phase ohms referred to winding j (phase voltage, per-phase
    # rating), then the same 3/n scaling as a line.
    v_ph = kv_j / SQRT3
    s_ph = kva / 1000.0 / phases
    zb = v_ph * v_ph / s_ph * _scale(phases, scaling)
    r = rpct / 100.0 * zb
    x = xpct / 100.0 * zb
    t_i = to_float(wi.get("tap", "1"))
    t_j = to_float(wj.get("tap", "1"))
    a, b = bus_override or (buses[i][0], buses[j][0])
    ratio_key = xf.name if bus_override is None else f"{xf.name}_w{j + 1}"
    net.phase_data.setdefault("xf_ratio", {})[ratio_key] = (
        1.0 if bus_override is not None and a != buses[0][0] and j == 0 else kv_j / kv_i
    )
    reg = regs.get(xf.name.lower())
    regulator = None
    if reg is not None:
        regulator = dict(reg)
        regulator["mintap"] = _fprop(xf, "mintap", 0.9)
        regulator["maxtap"] = _fprop(xf, "maxtap", 1.1)
        regulator["numtaps"] = _fprop(xf, "numtaps", 32.0)
        regulator["v_wdg_kv"] = to_float(
            xf.windings[int(reg["winding"]) - 1].get("kv", "1")
        )
    rating = (_fprop(xf, "normhkva", 1.1 * kva)) / 1000.0
    emerg = (_fprop(xf, "emerghkva", 1.5 * kva)) / 1000.0
    return NetBranch(
        name=xf.name,
        kind="transformer",
        bus_a=a,
        bus_b=b,
        kv=0.0,
        r=r,
        x=x,
        nphases=phases,
        rating_mva=rating,
        emerg_mva=emerg,
        tap=t_i / t_j,
        regulator=regulator,
        members=[f"transformer.{xf.name}"],
    )


def _three_winding(
    net: Network,
    xf: DssObject,
    phases: int,
    scaling: str,
    xs: tuple[float, float, float],
    buses: list[tuple[str, list[int]]],
) -> list[NetBranch]:
    xhl, xht, xlt = xs
    xpct = [(xhl + xht - xlt) / 2, (xhl + xlt - xht) / 2, (xht + xlt - xhl) / 2]
    star = f"{xf.name.lower()}_star"
    net.buses.setdefault(star, NetBus(star))
    kva = to_float(xf.windings[0].get("kva", "1000"))
    out = []
    for k in range(3):
        rpct = to_float(xf.windings[k].get("%r", "0.2"))
        br = _xf_branch(
            net,
            xf,
            0,
            k,
            rpct,
            xpct[k],
            phases,
            scaling,
            kva,
            {},
            buses,
            bus_override=(star, buses[k][0]) if k else (buses[0][0], star),
        )
        br.name = f"{xf.name}_w{k + 1}"
        if k == 0:
            br.tap = 1.0
            net.phase_data["xf_ratio"][br.name] = 1.0
        out.append(br)
    return out


def _merge_parallel(members: list[NetBranch]) -> NetBranch:
    if len(members) == 1:
        return members[0]
    y = sum(1.0 / complex(m.r, m.x) for m in members if complex(m.r, m.x) != 0)
    z = 1.0 / y if y != 0 else 0j
    first = members[0]
    merged = NetBranch(
        name=first.name,
        kind=first.kind,
        bus_a=first.bus_a,
        bus_b=first.bus_b,
        kv=0.0,
        r=float(z.real),
        x=float(z.imag),
        nphases=min(3, sum(m.nphases for m in members)),
        rating_mva=sum(m.rating_mva or 0.0 for m in members) or None,
        emerg_mva=sum(m.emerg_mva or 0.0 for m in members) or None,
        tap=float(np.mean([m.tap for m in members])),
        regulator=first.regulator,
        members=[mm for m in members for mm in m.members],
    )
    return merged


def _propagate_kv(net: Network, bases: list[float]) -> None:
    # Transformer kV ratios come from the windings (stored at build time).
    ratios = net.phase_data.get("xf_ratio", {})
    start = net.source_bus
    net.buses[start].kv = net.buses[start].kv or net.source_kv
    seen = {start}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for br in net.branches:
            if u not in (br.bus_a, br.bus_b):
                continue
            v = br.bus_b if u == br.bus_a else br.bus_a
            if v in seen:
                continue
            kv_u = net.buses[u].kv
            r = ratios.get(br.name)
            if r is not None:
                kv_v = kv_u * (r if u == br.bus_a else 1.0 / r)
            else:
                kv_v = kv_u
            net.buses.setdefault(v, NetBus(v)).kv = _snap(kv_v, bases)
            seen.add(v)
            queue.append(v)
    for bus in net.buses.values():
        if bus.name not in seen:
            _warn(net, f"bus {bus.name}: not connected to the source")
            if bus.kv <= 0:
                bus.kv = net.source_kv


def _finish_branches(net: Network) -> None:
    for br in net.branches:
        br.kv = net.buses[br.bus_b].kv
        if br.kind != "transformer":
            amps = (br.regulator or {}).get("normamps", 400.0)
            eamps = (br.regulator or {}).get("emergamps", 600.0)
            br.regulator = None
            br.rating_mva = SQRT3 * br.kv * amps / 1000.0 * (br.nphases / 3.0)
            br.emerg_mva = SQRT3 * br.kv * eamps / 1000.0 * (br.nphases / 3.0)
            br.charging_mvar *= br.kv * br.kv
            if br.kind == "switch":
                br.rating_mva = None
                br.emerg_mva = None


def _load_pq(load: DssObject) -> tuple[float, float]:
    kw = _fprop(load, "kw", 10.0)
    if load.get("kva") is not None and load.get("kw") is None:
        kva = to_float(load.get("kva") or "0")
        pf = _fprop(load, "pf", 0.88)
        kw = kva * abs(pf)
    if load.get("kvar") is not None and _last_of(load, ("kvar", "pf")) == "kvar":
        kvar = to_float(load.get("kvar") or "0")
    else:
        pf = _fprop(load, "pf", 0.88)
        kvar = _q_from_pf(kw, pf)
    return kw / 1000.0, kvar / 1000.0


def _last_of(obj: DssObject, keys: tuple[str, ...]) -> str | None:
    present = [k for k in obj.props if k in keys]
    return present[-1] if present else None


def _q_from_pf(p: float, pf: float) -> float:
    if pf == 0 or abs(pf) >= 1.0:
        return 0.0
    return math.copysign(abs(p) * math.sqrt(1.0 / (pf * pf) - 1.0), pf)


def _add_injections(net: Network, circuit: DssCircuit, scaling: str) -> None:
    del scaling
    load_pd = []
    for load in circuit.of("load").values():
        if not _enabled(load):
            continue
        bus, nodes = parse_bus(load.get("bus1") or "")
        p, q = _load_pq(load)
        n = to_int(load.get("phases") or "3")
        shape = (load.get("daily") or load.get("yearly") or "").strip("\"'").lower()
        net.loads.append(
            NetInjection(
                load.name,
                "load",
                bus,
                p,
                q,
                n,
                extra={"shape": shape, "model": to_int(load.get("model") or "1")},
            )
        )
        load_pd.append(
            {
                "name": load.name,
                "bus": bus,
                "nodes": nodes,
                "p_mw": p,
                "q_mvar": q,
                "conn": (load.get("conn") or "wye").lower(),
            }
        )
    net.phase_data["loads"] = load_pd

    for gen in circuit.of("generator").values():
        if not _enabled(gen):
            continue
        bus, _ = parse_bus(gen.get("bus1") or "")
        kw = _fprop(gen, "kw", 1000.0)
        pf = _fprop(gen, "pf", 0.80)
        kvar = (
            to_float(gen.get("kvar") or "0")
            if _last_of(gen, ("kvar", "pf")) == "kvar"
            else _q_from_pf(kw, pf)
        )
        extra: dict[str, Any] = {
            "shape": (gen.get("daily") or gen.get("yearly") or "").strip("\"'").lower()
        }
        if to_int(gen.get("model") or "1") == 3:
            extra["qmin"] = _fprop(gen, "minkvar", -abs(kvar) * 2) / 1000.0
            extra["qmax"] = _fprop(gen, "maxkvar", abs(kvar) * 2) / 1000.0
        if gen.get("kva") is not None:
            extra["smax"] = to_float(gen.get("kva") or "0") / 1000.0
        net.generators.append(
            NetInjection(
                gen.name,
                "generator",
                bus,
                kw / 1000.0,
                kvar / 1000.0,
                to_int(gen.get("phases") or "3"),
                extra,
            )
        )

    for pv in circuit.of("pvsystem").values():
        if not _enabled(pv):
            continue
        bus, _ = parse_bus(pv.get("bus1") or "")
        pmpp = _fprop(pv, "pmpp", 500.0)
        irr = _fprop(pv, "irradiance", 1.0)
        pct = _fprop(pv, "%pmpp", 100.0)
        p = pmpp * irr * pct / 100.0
        kva = _fprop(pv, "kva", 500.0)
        extra = {
            "shape": (pv.get("daily") or pv.get("yearly") or "").strip("\"'").lower(),
            "smax": kva / 1000.0,
            "pmpp": pmpp / 1000.0,
            "pv": True,
        }
        if pv.get("kvarmax") is not None:
            extra["qmax"] = to_float(pv.get("kvarmax") or "0") / 1000.0
            extra["qmin"] = (
                -to_float(pv.get("kvarmaxabs") or pv.get("kvarmax") or "0") / 1000.0
            )
        pf = _fprop(pv, "pf", 1.0)
        q = (
            to_float(pv.get("kvar") or "0")
            if _last_of(pv, ("kvar", "pf")) == "kvar"
            else _q_from_pf(p, pf)
        )
        net.generators.append(
            NetInjection(
                pv.name,
                "pv",
                bus,
                p / 1000.0,
                q / 1000.0,
                to_int(pv.get("phases") or "3"),
                extra,
            )
        )

    for st in circuit.of("storage").values():
        if not _enabled(st):
            continue
        bus, _ = parse_bus(st.get("bus1") or "")
        kw = _fprop(st, "kwrated", 25.0)
        kwh = _fprop(st, "kwhrated", 50.0)
        stored = _fprop(st, "%stored", 100.0)
        if st.get("kwhstored") is not None:
            stored = to_float(st.get("kwhstored") or "0") / kwh * 100.0
        extra = {
            "kwh": kwh / 1000.0,
            "reserve": _fprop(st, "%reserve", 20.0) / 100.0,
            "stored": stored / 100.0,
            "eff_charge": _fprop(st, "%effcharge", 90.0) / 100.0,
            "eff_discharge": _fprop(st, "%effdischarge", 90.0) / 100.0,
            "pct_charge": _fprop(st, "%charge", 100.0) / 100.0,
            "pct_discharge": _fprop(st, "%discharge", 100.0) / 100.0,
            "kva": _fprop(st, "kva", kw) / 1000.0,
        }
        net.storages.append(
            NetInjection(
                st.name,
                "storage",
                bus,
                kw / 1000.0,
                0.0,
                to_int(st.get("phases") or "3"),
                extra,
            )
        )

    switched = {
        (cc.get("capacitor") or "").strip("\"'").lower()
        for cc in circuit.of("capcontrol").values()
        if _enabled(cc)
    }
    for cap in circuit.of("capacitor").values():
        if not _enabled(cap):
            continue
        if cap.get("bus2") is not None:
            _warn(net, f"capacitor.{cap.name}: series capacitor unsupported, skipped")
            continue
        bus, _ = parse_bus(cap.get("bus1") or "")
        n = to_int(cap.get("phases") or "3")
        kvars = to_floats(cap.get("kvar") or "1200")
        kv = _fprop(cap, "kv", 12.47)
        conn = (cap.get("conn") or "wye").lower()
        nsteps = to_int(cap.get("numsteps") or str(len(kvars)))
        if len(kvars) < nsteps:
            kvars = kvars + [kvars[-1]] * (nsteps - len(kvars))
        raw_states = cap.get("states")
        states = (
            [int(float(s)) for s in split_items(raw_states)]
            if raw_states
            else [1] * nsteps
        )
        net.capacitors.append(
            NetInjection(
                cap.name,
                "capacitor",
                bus,
                0.0,
                sum(kvars) / 1000.0,
                n,
                extra={
                    "kv": kv,
                    "conn": conn,
                    "steps": [k / 1000.0 for k in kvars],
                    "states": states,
                    "switched": cap.name.lower() in switched,
                },
            )
        )


def finalize_capacitor_ratings(net: Network) -> None:
    """Refer capacitor kvar ratings to 1 pu of the bus base voltage."""
    for cap in net.capacitors:
        bus_kv = net.buses[cap.bus].kv if cap.bus in net.buses else cap.extra["kv"]
        rated = cap.extra["kv"]
        if cap.nphases == 1 and not cap.extra["conn"].startswith("d"):
            base = bus_kv / SQRT3
        else:
            base = bus_kv
        f = (base / rated) ** 2 if rated > 0 else 1.0
        cap.extra["steps"] = [s * f for s in cap.extra["steps"]]
        cap.q_mvar = sum(cap.extra["steps"])


def _collect_loadshapes(net: Network, circuit: DssCircuit) -> None:
    for name, ls in circuit.of("loadshape").items():
        raw = ls.get("pmult") or ls.get("mult")
        if raw is None:
            continue
        inner = strip_delims(raw)
        if inner.lower().startswith(("file=", "sngfile=", "dblfile=")):
            _warn(net, f"loadshape.{name}: file-based multipliers unsupported")
            continue
        net.loadshapes[name] = to_floats(raw)
