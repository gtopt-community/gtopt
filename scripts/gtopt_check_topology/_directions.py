# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Radial-line flow-direction suggestions (``--directions``).

Mirrors the C++ certificate behind ``model_options.line_direction_mode``
(``include/gtopt/line_direction.hpp``) so a modeller can review what
``structural`` would do, or pin ``Line.flow_direction`` by hand.  This
module only reports; it never edits the case.

* Transport multigraph: every line ever in service (AC, DC links, ties,
  expansion candidates); self-loops and unknown buses dropped.
* A **radial corridor** is a bus pair whose parallel circuits together
  are a cut (a bridge of the collapsed simple graph).  A bridge of the
  union graph stays a bridge in every stage, so the verdict holds for
  the whole horizon.
* Each side of the cut is ``empty`` / ``load`` / ``gen`` / ``mixed`` from
  the injection-sign classes of its elements: demands withdraw,
  generators with ``pmin >= 0`` inject (``pmax`` known 0 and not
  expandable: ignored), generators with ``pmin < 0``, standalone
  batteries and anything unresolved count as both.
* Verdict: ``dead_end`` (an empty side, or both sides load / both gen),
  ``ab`` / ``ba`` (toward the load side, away from the gen side) or
  ``both``.  Multi-circuit corridors are certified only when every
  circuit is AC with ``|x| > tol`` (KVL forces one sign); otherwise
  they stay ``both``.
* Lines named in a user constraint (``line("...")`` / ``line(all)``) are
  flagged ``protected``: ``structural`` leaves them untouched.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

import networkx as nx

from gtopt_check_topology._temporal import ResolvedModel, build_resolved_model
from gtopt_shared.field_sched import StageSchedResolver

DC_REACTANCE_TOL = 1e-12  # same as _analyzer: |x| <= tol is a tie / DC

# Side classes.
EMPTY = "empty"
LOAD = "load"
GEN = "gen"
MIXED = "mixed"


@dataclass
class _Flags:
    withdraw: bool = False
    inject: bool = False
    both: bool = False

    def merge(self, other: "_Flags") -> None:
        self.withdraw |= other.withdraw
        self.inject |= other.inject
        self.both |= other.both

    @property
    def side(self) -> str:
        if self.both or (self.withdraw and self.inject):
            return MIXED
        if self.withdraw:
            return LOAD
        if self.inject:
            return GEN
        return EMPTY


def side_verdict(side_a: str, side_b: str) -> str:
    """Verdict of a radial circuit from the classes of its two sides."""
    if EMPTY in (side_a, side_b):
        return "dead_end"
    if side_a == side_b and side_a in (LOAD, GEN):
        return "dead_end"
    if side_b == LOAD or side_a == GEN:
        return "ab"
    if side_a == LOAD or side_b == GEN:
        return "ba"
    return "both"


@dataclass
class DirectionSuggestion:
    """Classification of one line."""

    uid: Any
    name: str
    bus_a: str
    bus_b: str
    radial: bool
    verdict: str
    side_a: str
    side_b: str
    protected: bool = False
    current: str | None = None  # the line's own ``flow_direction``


@dataclass
class DirectionReport:
    """All per-line suggestions plus counts."""

    lines: list[DirectionSuggestion] = field(default_factory=list)

    def count(self, verdict: str) -> int:
        return sum(1 for s in self.lines if s.radial and s.verdict == verdict)

    @property
    def n_radial(self) -> int:
        return sum(1 for s in self.lines if s.radial)

    @property
    def n_one_way(self) -> int:
        return self.count("ab") + self.count("ba")

    @property
    def suggestions(self) -> list[DirectionSuggestion]:
        """Certified lines a modeller could pin (one-way or dead end)."""
        return [
            s
            for s in self.lines
            if s.radial and s.verdict != "both" and not s.protected
        ]

    def to_json(self) -> dict[str, Any]:
        return {
            "summary": {
                "lines": len(self.lines),
                "radial": self.n_radial,
                "one_way": self.n_one_way,
                "dead_end": self.count("dead_end"),
                "mixed": self.count("both"),
                "protected": sum(1 for s in self.lines if s.protected),
            },
            "lines": [
                {
                    "uid": s.uid,
                    "name": s.name,
                    "bus_a": s.bus_a,
                    "bus_b": s.bus_b,
                    "verdict": s.verdict,
                    "side_a": s.side_a,
                    "side_b": s.side_b,
                    "protected": s.protected,
                }
                for s in self.lines
                if s.radial
            ],
        }


def _element_flags(
    planning: Mapping[str, Any],
    resolver: StageSchedResolver,
    model: ResolvedModel,
) -> dict[str, _Flags]:
    """Bus name -> injection-sign flags of the elements attached to it."""
    sys = planning.get("system", {}) or {}
    flags: dict[str, _Flags] = defaultdict(_Flags)
    raw_by_key = {
        ("generator", g.get("uid"), str(g.get("name", g.get("uid", "?")))): g
        for g in sys.get("generator_array", []) or []
    }
    for elem in model.elements:
        if not any(elem.active):
            continue
        if elem.kind == "demand":
            flags[elem.bus].merge(_Flags(withdraw=True))
            continue
        if elem.kind == "battery":
            flags[elem.bus].merge(_Flags(both=True))
            continue
        # generator
        caps = [c for c in elem.capacity if c is not None]
        if not elem.expandable and caps and max(caps) <= 0.0:
            continue  # pmax known 0 everywhere: never injects
        raw = raw_by_key.get(("generator", elem.uid, elem.name), {})
        pmin = resolver.per_stage(
            "Generator", raw.get("pmin"), elem.uid, reduce="min", default=0.0
        )
        if any(v is None or v < 0.0 for v in pmin):
            flags[elem.bus].merge(_Flags(both=True))
        else:
            flags[elem.bus].merge(_Flags(inject=True))
    bus_names = set(model.bus_names)
    name_by_uid = {
        b.get("uid"): str(b.get("name")) for b in sys.get("bus_array", []) or []
    }
    for comp in sys.get("gas_compressor_array", []) or []:
        bus = comp.get("bus")
        bus = name_by_uid.get(bus, bus) if not isinstance(bus, str) else bus
        if bus in bus_names:
            flags[bus].merge(_Flags(withdraw=True))
    for conv in sys.get("carrier_converter_array", []) or []:
        for carrier, node in (("from_carrier", "from_node"), ("to_carrier", "to_node")):
            if str(conv.get(carrier, "")).lower() != "electric":
                continue
            bus = conv.get(node)
            bus = name_by_uid.get(bus, bus) if not isinstance(bus, str) else bus
            if isinstance(bus, str) and bus in bus_names:
                flags[bus].merge(_Flags(both=True))
    return flags


_REF_RE = re.compile(r"(?<![A-Za-z0-9_])line\s*\(([^)]*)\)", re.IGNORECASE)
_ID_RE = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)')\s*(?:,|$)""")


def referenced_lines(planning: Mapping[str, Any]) -> tuple[set[str], bool]:
    """Line ids named in user-constraint expressions; ``True`` = all lines."""
    sys = planning.get("system", {}) or {}
    exprs: list[str] = [
        str(uc.get("expression", ""))
        for uc in sys.get("user_constraint_array", []) or []
    ]
    for um in sys.get("user_model_array", []) or []:
        exprs += [
            str(uc.get("expression", "")) for uc in um.get("constraint_array", []) or []
        ]
    ids: set[str] = set()
    all_lines = False
    for expr in exprs:
        for match in _REF_RE.finditer(expr):
            body = match.group(1)
            pos = 0
            while pos < len(body):
                m = _ID_RE.match(body, pos)
                if m is None:
                    all_lines = True
                    break
                ids.add(m.group(1) if m.group(1) is not None else m.group(2))
                pos = m.end()
    for lc in sys.get("line_commitment_array", []) or []:
        if lc.get("line") is not None:
            ids.add(str(lc["line"]))
    return ids, all_lines


def _bridge_sides(
    graph: nx.Graph,
    bridges: set[tuple[str, str]],
    flags: Mapping[str, _Flags],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Side classes ``(side of key[0], side of key[1])`` of every bridge.

    The 2-edge-connected components (graph minus its bridges) contract
    to a forest whose edges are the bridges; one pass of subtree sums
    gives both sides of every bridge in O(V + E).
    """
    cut = graph.copy()
    cut.remove_edges_from(bridges)
    comp: dict[str, int] = {}
    comp_flags: list[_Flags] = []
    for cid, nodes in enumerate(nx.connected_components(cut)):
        agg = _Flags()
        for bus in nodes:
            comp[bus] = cid
            agg.merge(flags.get(bus, _Flags()))
        comp_flags.append(agg)
    forest: nx.Graph = nx.Graph()
    forest.add_nodes_from(range(len(comp_flags)))
    forest.add_edges_from((comp[a], comp[b]) for a, b in bridges)

    counts = [[0, 0, 0] for _ in comp_flags]  # withdraw, inject, both
    parent: dict[int, int] = {}
    root_of: dict[int, int] = {}
    for root in forest.nodes:
        if root in root_of:
            continue
        order = list(nx.dfs_preorder_nodes(forest, root))
        for node in order:
            root_of[node] = root
        for u, v in nx.dfs_edges(forest, root):
            parent[v] = u
        for node in reversed(order):
            f = comp_flags[node]
            c = counts[node]
            c[0] += f.withdraw
            c[1] += f.inject
            c[2] += f.both
            if node in parent:
                pc = counts[parent[node]]
                for k in range(3):
                    pc[k] += c[k]

    def side(c: list[int]) -> str:
        return _Flags(withdraw=c[0] > 0, inject=c[1] > 0, both=c[2] > 0).side

    out: dict[tuple[str, str], tuple[str, str]] = {}
    for a, b in bridges:
        ca, cb = comp[a], comp[b]
        child = cb if parent.get(cb) == ca else ca
        total = counts[root_of[child]]
        inner = counts[child]
        outer = [t - i for t, i in zip(total, inner)]
        sa, sb = (outer, inner) if child == cb else (inner, outer)
        out[(a, b)] = (side(sa), side(sb))
    return out


def analyse_directions(
    planning: Mapping[str, Any],
    resolver: StageSchedResolver,
    model: ResolvedModel,
) -> DirectionReport:
    """Classify every line of the case (see the module docstring)."""
    flags = _element_flags(planning, resolver, model)
    ref_ids, ref_all = referenced_lines(planning)
    known = set(model.bus_names)

    # Collapse parallel circuits into corridors.
    corridors: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, line in enumerate(model.lines):
        a, b = line.bus_a, line.bus_b
        if a not in known or b not in known or a == b or not any(line.in_service):
            continue
        corridors[(a, b) if a < b else (b, a)].append(i)

    graph: nx.Graph = nx.Graph()
    graph.add_nodes_from(model.bus_names)
    graph.add_edges_from(corridors)
    bridges = {(a, b) if a < b else (b, a) for a, b in nx.bridges(graph)}
    side_of = _bridge_sides(graph, bridges, flags)
    mo = (planning.get("options", {}) or {}).get("model_options", {}) or {}
    kvl_active = mo.get("use_kirchhoff", True) is not False and not mo.get(
        "use_single_bus", False
    )

    report = DirectionReport()
    for key, idxs in corridors.items():
        radial = key in bridges
        coupled = kvl_active and all(
            model.lines[i].has_reactance()
            and abs(model.lines[i].rep_reactance()) > DC_REACTANCE_TOL
            for i in idxs
        )
        for i in idxs:
            line = model.lines[i]
            sa, sb = MIXED, MIXED
            verdict = "both"
            if radial:
                lo, hi = side_of[key]
                sa, sb = (lo, hi) if line.bus_a == key[0] else (hi, lo)
                verdict = side_verdict(sa, sb)
                if len(idxs) > 1 and not coupled:
                    verdict = "both"  # circulation between circuits possible
            protected = ref_all or bool(
                {line.name, str(line.uid), f"uid:{line.uid}"} & ref_ids
            )
            report.lines.append(
                DirectionSuggestion(
                    uid=line.uid,
                    name=line.name,
                    bus_a=line.bus_a,
                    bus_b=line.bus_b,
                    radial=radial,
                    verdict=verdict,
                    side_a=sa,
                    side_b=sb,
                    protected=protected,
                    current=line.raw.get("flow_direction"),
                )
            )
    return report


def directions_for_planning(
    planning: Mapping[str, Any], case_dir: Any = None
) -> DirectionReport:
    """Resolve the planning per stage and classify its lines."""
    base = case_dir if case_dir is not None else planning.get("_pampl_dir", ".")
    resolver = StageSchedResolver.from_planning(planning, base)
    model = build_resolved_model(planning, resolver)
    return analyse_directions(planning, resolver, model)


def render_directions(report: DirectionReport, console: Any, quiet: bool) -> None:
    """Print the summary and the certified lines (suggestions only)."""
    console.print(
        f"\n[bold]Line directions[/bold]: {len(report.lines)} lines, "
        f"{report.n_radial} radial, {report.n_one_way} one-way, "
        f"{report.count('dead_end')} dead-end, {report.count('both')} mixed"
    )
    console.print(
        "  suggestions only -- `model_options.line_direction_mode = "
        "structural` applies them in the LP; or pin `Line.flow_direction`."
    )
    for s in report.lines:
        if not s.radial or (quiet and (s.verdict == "both" or s.protected)):
            continue
        note = " (protected: user constraint)" if s.protected else ""
        if s.current is not None:
            note += f" (flow_direction = {s.current})"
        console.print(
            f"  {s.name}: {s.bus_a} [{s.side_a}] -> {s.bus_b} [{s.side_b}]"
            f"  => {s.verdict}{note}",
            highlight=False,
            markup=False,
        )
