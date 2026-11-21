# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hydro Energy Production Factor (EPF) per Reservoir.

The EPF of a reservoir R is the total MW generated per m³/s of water
released, summed over every turbine in the cascade segment between R
and the next downstream reservoir (or the ocean / sink junction).

EPF[R] = Σ turbine.production_factor over every turbine on the path
         from R's output junction, walking the directed
         junction→waterway/turbine→junction graph downstream, STOPPING
         when:
           * the path reaches another reservoir's anchor junction
             (that reservoir owns its own downstream cascade segment), or
           * the path reaches a sink (junction with no outgoing edge —
             the ocean for terminal rivers).

Use cases (consumed by both plexos2gtopt and plp2gtopt):
  * **Emissions-mode terminal value** (issue #519): the carbon shadow
    price per stored cumec held in reservoir R is
        EPF[R] · gas_em · loss_mult       [tCO2eq per (m³/s)·h]
    with `gas_em = 0.5 tCO2eq/MWh` (long-term marginal replacement
    in Chile is gas) and `loss_mult = 0.95` (round-trip efficiency).
  * **Cross-check** against ``reservoir_production_factor_array``
    entries plp2gtopt emits — summing ``mean_production_factor`` over
    matching reservoir entries must equal the EPF derived here.
  * **Generic hydro-cascade analysis** — walking from any node, with or
    without the next-reservoir stopping rule.

The module deliberately depends only on ``networkx`` so it can run in
either converter's process without extra hooks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


_GAS_EMISSION_TCO2_PER_MWH: float = 0.5
"""Long-term marginal-replacement emission rate for Chile (gas)."""

_LOSS_MULTIPLIER: float = 0.95
"""Efficiency / loss multiplier applied to the gas-equivalent rate."""


def build_hydro_graph(system: dict[str, Any]) -> nx.DiGraph:
    """Build a directed junction-graph from a gtopt ``system`` dict.

    Nodes are junction names; edges are turbines (kind='turbine') or
    waterways (kind='waterway'), each carrying:

      * ``kind`` — 'turbine' or 'waterway'
      * ``name`` — element name
      * ``production_factor`` — float (0 for waterways)

    Turbines with no ``junction_b`` are skipped (built-in waterway
    schema where the downstream junction is implicit at the
    ``main_reservoir`` — those bus-only turbines don't contribute a
    cascade arc).
    """
    G: nx.DiGraph = nx.DiGraph()
    for j in system.get("junction_array", []) or []:
        name = j.get("name")
        if name:
            G.add_node(name)

    # (waterway-name → production_factor) from turbines that reference
    # a Waterway by name (plp2gtopt schema).  When multiple turbines
    # share a waterway the production_factors sum.
    pf_on_waterway: dict[str, float] = {}
    for t in system.get("turbine_array", []) or []:
        wname = t.get("waterway")
        if isinstance(wname, str) and wname:
            pf_on_waterway[wname] = pf_on_waterway.get(wname, 0.0) + float(
                t.get("production_factor", 0.0) or 0.0
            )

    # Add waterway edges — when a turbine references this waterway by
    # name, the edge inherits the turbine's production_factor (kind=
    # 'turbine' for cascade attribution).  Plain waterways get PF=0.
    for w in system.get("waterway_array", []) or []:
        a, b = w.get("junction_a"), w.get("junction_b")
        if not a or not b:
            continue
        wname = w.get("name", "")
        pf = pf_on_waterway.get(wname, 0.0)
        G.add_edge(
            a,
            b,
            kind="turbine" if pf > 0 else "waterway",
            name=wname,
            production_factor=pf,
        )

    # plexos2gtopt-style direct-arc turbines (own junction_a /
    # junction_b, no separate Waterway).  Skip turbines already
    # accounted for via the Waterway-name decoration above.
    for t in system.get("turbine_array", []) or []:
        a, b = t.get("junction_a"), t.get("junction_b")
        if not a or not b:
            continue
        if isinstance(t.get("waterway"), str) and t.get("waterway"):
            continue
        G.add_edge(
            a,
            b,
            kind="turbine",
            name=t.get("name", ""),
            production_factor=float(t.get("production_factor", 0.0) or 0.0),
        )
    return G


def epf_per_reservoir(
    system: dict[str, Any],
    *,
    graph: nx.DiGraph | None = None,
) -> dict[str, float]:
    """Return ``{reservoir_name: EPF}`` where EPF is MW per m³/s.

    The walk from each reservoir's anchor junction:
      * adds the ``production_factor`` of every outgoing edge as we
        descend (turbines contribute; waterways contribute 0),
      * STOPS descending when the next node anchors a *different*
        reservoir (that reservoir owns the rest of the cascade), AND
      * stops at sinks (no outgoing edges).
    """
    if graph is None:
        graph = build_hydro_graph(system)

    res_junctions: dict[str, str] = {
        r["name"]: r.get("junction", r["name"])
        for r in (system.get("reservoir_array", []) or [])
    }
    # junction → owning reservoir (1:1 in CEN; first wins on ties).
    junction_owner: dict[str, str] = {}
    for res_name, junc in res_junctions.items():
        junction_owner.setdefault(junc, res_name)

    out: dict[str, float] = {}
    for res_name, anchor in res_junctions.items():
        if anchor not in graph:
            out[res_name] = 0.0
            continue
        epf = 0.0
        visited = {anchor}
        queue: list[str] = [anchor]
        while queue:
            u = queue.pop(0)
            for v, edata in graph[u].items():
                if v in visited:
                    continue
                epf += edata.get("production_factor", 0.0)
                visited.add(v)
                owner = junction_owner.get(v)
                if owner is not None and owner != res_name:
                    # Reached a different reservoir's anchor — stop.
                    continue
                queue.append(v)
        out[res_name] = epf
    return out


def water_emission_value_per_cumec_hour(
    epf: float,
    *,
    gas_em: float = _GAS_EMISSION_TCO2_PER_MWH,
    loss_mult: float = _LOSS_MULTIPLIER,
) -> float:
    """Carbon shadow price of stored water [tCO2eq per (m³/s)·h].

    By assumption (long-term marginal replacement = gas):
        value = EPF · gas_em · loss_mult
              = (MW / (m³/s)) · (tCO2eq / MWh) · (dimensionless)
              = tCO2eq / (m³/s · h)

    Consumed by the emissions-mode terminal-value stamping (issue #519)
    on each reservoir, replacing the dollar-valued boundary cuts /
    water_value the cost-mode LP uses.
    """
    return float(epf) * float(gas_em) * float(loss_mult)


# ---------------------------------------------------------------------------
# Accumulated production factor (rho_acc) of a reservoir chain
# ---------------------------------------------------------------------------
#
# Follows the CEPEL NEWAVE definition of the stored energy of an equivalent
# reservoir (NEWAVE reference manual, "energia armazenavel" /
# "produtibilidade equivalente"): EAR = sum_i V_util,i x rho_acc,i, where the
# ACCUMULATED productivity rho_acc,i is the sum of the equivalent
# productivities of plant i and of every plant downstream of it along the
# TURBINED path (jusante); the equivalent productivity uses the head averaged
# over the useful volume (run-of-river: constant head); plants not in service
# are excluded (they contribute 0 but still pass the water on).  Downstream
# reservoirs only delay the water, so the walk does NOT stop at them.

SEA = "__sea__"
"""Sink node for turbines / waterways whose water leaves the system."""

Resolver = Callable[[str, dict[str, Any], str, Any], Any]
"""``resolver(class_name, element, field, default) -> value`` used to
evaluate schedule-valued fields (``active``, ``production_factor``,
``emin``/``emax``) at the evaluation stage; class names are gtopt's
(``"Turbine"``, ``"Waterway"``, ``"Reservoir"``)."""


def _num(v: Any, default: float = 0.0) -> float:
    return (
        float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    )


def _default_resolver(_cls: str, elem: dict[str, Any], fld: str, default: Any) -> Any:
    v = elem.get(fld, default)
    # schedule references (strings) cannot be evaluated without the tables:
    # treat ``active`` as on and numeric fields as the default
    if isinstance(v, str):
        return default
    return v


def _mean_pf_curve(segments: list[dict[str, Any]], vmin: float, vmax: float) -> float:
    """Average over [vmin, vmax] of pf(V) = min_i(c_i + s_i (V - v_i)) (gtopt /
    PLP ``FRendimientos`` concave envelope) -- the equivalent productivity of
    a head-dependent plant (head averaged over the useful volume)."""
    if not segments:
        return 0.0
    vmax = max(vmax, vmin)
    n = 200
    acc = 0.0
    for k in range(n + 1):
        v = vmin + (vmax - vmin) * k / n
        f = min(
            _num(sg.get("constant"))
            + _num(sg.get("slope")) * (v - _num(sg.get("volume")))
            for sg in segments
        )
        acc += f * (0.5 if k in (0, n) else 1.0)
    return acc / n


def build_chain_graph(
    system: dict[str, Any],
    *,
    resolver: Resolver | None = None,
) -> nx.MultiDiGraph:
    """Junction multigraph for the accumulated production factor.

    Edges carry ``kind`` ('turbine' | 'waterway'), ``name``, ``active`` and
    ``production_factor`` [MW/(m3/s)] at the resolver's stage (0 for
    waterways and inactive turbines -- an inactive turbine still passes the
    water on).  Turbines / waterways without ``junction_b`` end at
    :data:`SEA`.  Head-dependent plants (``reservoir_production_factor_array``)
    use the curve averaged over the reservoir's [emin, emax], or its
    ``mean_production_factor``.  Pumps are not arcs (no ``pump_array``
    elements are read), so pump-back cycles are excluded.
    """
    rv = resolver or _default_resolver
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(SEA)
    for j in system.get("junction_array", []) or []:
        if j.get("name"):
            g.add_node(j["name"])
    reservoirs = {r["name"]: r for r in system.get("reservoir_array", []) or []}
    by_uid = {r.get("uid"): r for r in reservoirs.values()}
    eq_pf: dict[str, float] = {}
    for rp in system.get("reservoir_production_factor_array", []) or []:
        tname = rp.get("turbine")
        res = reservoirs.get(rp.get("reservoir")) or by_uid.get(rp.get("reservoir"))
        segs = rp.get("segments") or []
        if res is not None and segs:
            vmin = _num(rv("Reservoir", res, "emin", 0.0))
            vmax = _num(
                rv("Reservoir", res, "emax", res.get("capacity", 0.0)),
                _num(res.get("capacity")),
            )
            eq_pf[tname] = _mean_pf_curve(segs, vmin, vmax)
        elif rp.get("mean_production_factor") is not None:
            eq_pf[tname] = _num(rp.get("mean_production_factor"))

    def tpf(t: dict[str, Any]) -> float:
        if t.get("name") in eq_pf:
            return eq_pf[t["name"]]
        return _num(rv("Turbine", t, "production_factor", 0.0))

    def is_on(cls: str, e: dict[str, Any]) -> bool:
        return bool(_num(rv(cls, e, "active", 1), 1.0))

    on_ww: dict[str, list[dict[str, Any]]] = {}
    for t in system.get("turbine_array", []) or []:
        w = t.get("waterway")
        if isinstance(w, str) and w:
            on_ww.setdefault(w, []).append(t)
    for w in system.get("waterway_array", []) or []:
        a = w.get("junction_a")
        if not a:
            continue
        b = w.get("junction_b") or SEA
        ts = on_ww.get(w.get("name", ""), [])
        w_on = is_on("Waterway", w)
        pf = sum(tpf(t) for t in ts if is_on("Turbine", t)) if w_on else 0.0
        g.add_edge(
            a,
            b,
            kind="turbine" if ts else "waterway",
            name=w.get("name", ""),
            active=w_on and (not ts or any(is_on("Turbine", t) for t in ts)),
            production_factor=pf,
        )
    for t in system.get("turbine_array", []) or []:
        a = t.get("junction_a")
        if not a or (isinstance(t.get("waterway"), str) and t.get("waterway")):
            continue
        on = is_on("Turbine", t)
        g.add_edge(
            a,
            t.get("junction_b") or SEA,
            kind="turbine",
            name=t.get("name", ""),
            active=on,
            production_factor=tpf(t) if on else 0.0,
        )
    return g


def _turbined_path(
    g: nx.MultiDiGraph, start: str, stop: set[str]
) -> tuple[float, list[str]]:
    """Walk the turbined path from ``start`` (NEWAVE jusante): at each
    junction take the active turbine arc (the best chain if several);
    with no active turbine take the release/pass-through arc (waterway or
    inactive turbine) leading to the best chain.  Stops at the sea, at a
    junction in ``stop`` or on a cycle."""

    memo: dict[str, tuple[float, list[str]]] = {}

    def walk(u: str, onpath: frozenset[str]) -> tuple[float, list[str]]:
        if u == SEA or (u in stop and u != start):
            return 0.0, []
        if u in memo:
            return memo[u]
        arcs = list(g.out_edges(u, data=True))
        tur = [(v, d) for _u, v, d in arcs if d["kind"] == "turbine" and d["active"]]
        cand = tur or [(v, d) for _u, v, d in arcs]
        best: tuple[float, list[str]] = (0.0, [])
        for v, d in cand:
            if v in onpath:
                logger.warning("hydro chain: cycle at %s -> %s ignored", u, v)
                continue
            sub = walk(v, onpath | {v})
            val = d["production_factor"] + sub[0]
            if val > best[0] or not best[1]:
                best = (
                    val,
                    ([d["name"]] if d["production_factor"] > 0 else []) + sub[1],
                )
        memo[u] = best
        return best

    return walk(start, frozenset({start}))


def _max_path(
    g: nx.MultiDiGraph, start: str, stop: set[str]
) -> tuple[float, list[str]]:
    """Longest production-factor path from ``start`` over all arcs (diagnostic)."""
    sub = g.subgraph(nx.descendants(g, start) | {start}).copy()
    if stop:
        sub.remove_edges_from(
            [(u, v, k) for u, v, k in sub.edges(keys=True) if u in stop and u != start]
        )
    if nx.is_directed_acyclic_graph(sub):
        simple = nx.DiGraph()
        for u, v, d in sub.edges(data=True):
            w = d["production_factor"]
            if not simple.has_edge(u, v) or simple[u][v]["weight"] < w:
                simple.add_edge(u, v, weight=w, name=d["name"])
        dist: dict[str, tuple[float, list[str]]] = {}
        for u in reversed(list(nx.topological_sort(simple))):
            best: tuple[float, list[str]] = (0.0, [])
            for v in simple.successors(u):
                d = simple[u][v]
                val = d["weight"] + dist[v][0]
                if val > best[0]:
                    best = (val, ([d["name"]] if d["weight"] > 0 else []) + dist[v][1])
            dist[u] = best
        return dist.get(start, (0.0, []))
    logger.warning(
        "hydro chain from %s has a cycle: longest simple path (cutoff 60)", start
    )
    best = (0.0, [])
    for tgt in sub.nodes:
        for path in nx.all_simple_edge_paths(sub, start, tgt, cutoff=60):
            val = sum(sub.edges[e]["production_factor"] for e in path)
            if val > best[0]:
                best = (
                    val,
                    [
                        sub.edges[e]["name"]
                        for e in path
                        if sub.edges[e]["production_factor"] > 0
                    ],
                )
    return best


def chain_epf_per_reservoir(
    system: dict[str, Any],
    *,
    stage: int | None = None,
    stop_at_reservoirs: bool = False,
    mode: str = "turbined_path",
    resolver: Resolver | None = None,
    with_path: bool = False,
) -> dict[str, Any]:
    """Accumulated production factor ``rho_acc`` [MW/(m3/s)] of each reservoir.

    ``rho_acc[R]`` = sum of the (equivalent) production factors of the
    turbines that the water released from R passes on its way to the sea,
    following the TURBINED path (CEPEL NEWAVE "produtibilidade acumulada";
    see the module notes above).  Downstream reservoirs do not stop the walk
    unless ``stop_at_reservoirs``.  Multiply by ``1/flow_conversion_rate``
    (0.0036 hm3 per (m3/s)h) to get MWh/hm3, see :func:`rho_acc_mwh_per_hm3`.

    ``mode``:
      * ``"turbined_path"`` (default): NEWAVE turbined path;
      * ``"max_path"``: longest pf path over every arc incl. spills
        (diagnostic; equals turbined_path except where routes split);
      * ``"segment"``: the legacy :func:`epf_per_reservoir` (stops at the next
        reservoir, sums every outgoing arc).
    ``stage`` is informative: schedule-valued fields are evaluated by
    ``resolver`` (which should be bound to that stage).  ``with_path``
    returns ``{name: (rho, [turbine names])}``.
    """
    if mode == "segment":
        return dict(epf_per_reservoir(system))
    if mode not in ("turbined_path", "max_path"):
        raise ValueError(f"unknown mode {mode!r}")
    del stage  # evaluation stage is carried by the resolver
    g = build_chain_graph(system, resolver=resolver)
    res = {
        r["name"]: r.get("junction", r["name"])
        for r in system.get("reservoir_array", []) or []
    }
    stop = set(res.values()) if stop_at_reservoirs else set()
    out: dict[str, Any] = {}
    for name, j in res.items():
        if j not in g:
            val: tuple[float, list[str]] = (0.0, [])
        elif mode == "turbined_path":
            val = _turbined_path(g, j, stop)
        else:
            val = _max_path(g, j, stop)
        out[name] = val if with_path else val[0]
    return out


def rho_acc_mwh_per_hm3(
    rho_mw_per_m3s: float, flow_conversion_rate: float = 0.0036
) -> float:
    """MW/(m3/s) -> MWh/hm3: one hm3 released at 1 m3/s lasts
    1/flow_conversion_rate hours (0.0036 hm3 per (m3/s)h)."""
    return float(rho_mw_per_m3s) / float(flow_conversion_rate)


__all__ = [
    "SEA",
    "build_chain_graph",
    "build_hydro_graph",
    "chain_epf_per_reservoir",
    "epf_per_reservoir",
    "rho_acc_mwh_per_hm3",
    "water_emission_value_per_cumec_hour",
    "_GAS_EMISSION_TCO2_PER_MWH",
    "_LOSS_MULTIPLIER",
]
