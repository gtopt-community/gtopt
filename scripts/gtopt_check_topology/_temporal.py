# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Schedule-aware (per-stage) network model for gtopt_check_topology.

gtopt line / bus / element fields may be scalars, inline per-stage
arrays, or file references (``<input_directory>/<Class>/<name>``).  This
module resolves them, stage by stage, through
:class:`gtopt_shared.field_sched.StageSchedResolver` and builds:

* :class:`ResolvedModel` -- every bus / line / bus-attached element with
  per-stage values (active, reactance, tmax, capacity, ...);
* :func:`analyse_stage_topology` -- connectivity PER STAGE, compressed
  into :class:`TopologyPeriod` ranges (a new period starts only when the
  island partition -- or the set of islands carrying active elements --
  changes), the inter-zone lines and the optional expected-islands check.

A line connects its buses in a stage when the line and both buses are
active and the line is not closed in both directions
(``tmax_ab == tmax_ba == 0`` for the whole stage; an unset ``tmax``
falls back to ``capacity`` and, when that is unset too, is treated as
unbounded -- exactly as the LP does).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import networkx as nx

from gtopt_shared.field_sched import StageSchedResolver

# Islands up to this many buses list every attached element; larger
# islands (the main grids) only report element counts and MW totals.
DEFAULT_DETAIL_MAX_BUSES = 12

_ZONE_FIELDS = ("zone", "country", "area", "region")
_EXPANSION_KEYS = ("expcap", "expmod", "capmax")
_ZONE_PREFIX_RE = re.compile(r"^([A-Za-z]{2,4})_")


def bus_zone(bus: Mapping[str, Any]) -> str | None:
    """Zone label of a bus: an explicit zone field, else a name prefix.

    Explicit ``zone`` / ``country`` / ``area`` / ``region`` string fields
    win; otherwise a 2-4 letter prefix followed by ``_`` (``CL_Foo220``
    -> ``CL``) is used.  ``None`` when neither is available.
    """
    for key in _ZONE_FIELDS:
        val = bus.get(key)
        if isinstance(val, str) and val:
            return val
    name = bus.get("name")
    if isinstance(name, str):
        match = _ZONE_PREFIX_RE.match(name)
        if match:
            return match.group(1).upper()
    return None


def stage_ranges(stage_uids: list[int], idxs: Iterable[int]) -> list[tuple[int, int]]:
    """Compress stage indices into ``(first_uid, last_uid)`` runs."""
    out: list[tuple[int, int]] = []
    prev: int | None = None
    start: int | None = None
    for i in sorted(idxs):
        if prev is not None and start is not None and i == prev + 1:
            prev = i
            continue
        if start is not None and prev is not None:
            out.append((stage_uids[start], stage_uids[prev]))
        start = prev = i
    if start is not None and prev is not None:
        out.append((stage_uids[start], stage_uids[prev]))
    return out


def format_ranges(ranges: list[tuple[int, int]]) -> str:
    """Human label for stage ranges (``1-23, 40``)."""
    if not ranges:
        return "never"
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


# ---------------------------------------------------------------------------
# Resolved model
# ---------------------------------------------------------------------------


@dataclass
class ResolvedLine:
    """A line with every topology-relevant field resolved per stage."""

    uid: Any
    name: str
    bus_a: str
    bus_b: str
    raw: dict[str, Any]
    reactance: list[float | None]
    resistance: list[float | None]
    tmax_ab: list[float | None]
    tmax_ba: list[float | None]
    voltage: list[float | None]
    active: list[bool]
    in_service: list[bool] = field(default_factory=list)

    @property
    def service_idx(self) -> list[int]:
        return [i for i, on in enumerate(self.in_service) if on]

    def _pick(self, values: list[float | None]) -> list[float]:
        idx = self.service_idx or list(range(len(values)))
        return [v for i in idx if (v := values[i]) is not None]

    def has_reactance(self) -> bool:
        """True when a ``reactance`` is set in some in-service stage.

        A line without reactance is a DC link in gtopt (no KVL row); an
        explicit 0 is a zero-impedance tie.
        """
        return bool(self._pick(self.reactance))

    def rep_reactance(self) -> float:
        """Min-|X| over in-service stages (strongest KVL coupling)."""
        vals = self._pick(self.reactance)
        return min(vals, key=abs) if vals else 0.0

    def rep_resistance(self) -> float:
        vals = self._pick(self.resistance)
        return min(vals, key=abs) if vals else 0.0

    def rep_tmax(self) -> float:
        """Max ``tmax_ab`` over in-service stages (0.0 when unset)."""
        vals = self._pick(self.tmax_ab)
        return max(vals) if vals else 0.0

    def min_value(self, values: list[float | None]) -> float | None:
        vals = [v for v in values if v is not None]
        return min(vals) if vals else None


@dataclass
class AttachedElement:
    """A bus-attached element (generator / demand / battery)."""

    kind: str
    name: str
    uid: Any
    bus: str
    tags: list[str]
    active: list[bool]
    capacity: list[float | None]
    expandable: bool = False

    def effective(self, s: int) -> bool:
        """Active in stage ``s`` with a capacity that is not known-zero.

        A 0-MW element (e.g. a placeholder candidate with ``pmax = 0``
        and no expansion fields) cannot inject or draw power, so it does
        not make its island "carry elements".
        """
        if not self.active[s]:
            return False
        cap = self.capacity[s]
        return self.expandable or cap is None or cap > 0.0

    def max_capacity(self, idxs: Iterable[int] | None = None) -> float | None:
        rng = range(len(self.capacity)) if idxs is None else idxs
        vals = [v for i in rng if (v := self.capacity[i]) is not None]
        return max(vals) if vals else None


@dataclass
class ResolvedModel:
    """Buses, lines and attached elements with per-stage values."""

    stage_uids: list[int]
    bus_names: list[str]
    bus_zone: dict[str, str | None]
    bus_active: dict[str, list[bool]]
    lines: list[ResolvedLine]
    elements: list[AttachedElement]
    unresolved: list[str] = field(default_factory=list)

    @property
    def n_stages(self) -> int:
        return len(self.stage_uids)

    def elements_by_bus(self) -> dict[str, list[AttachedElement]]:
        out: dict[str, list[AttachedElement]] = defaultdict(list)
        for elem in self.elements:
            out[elem.bus].append(elem)
        return out


def _active_list(
    res: StageSchedResolver, cls: str, item: Mapping[str, Any]
) -> list[bool]:
    vals = res.per_stage(cls, item.get("active"), item.get("uid"), default=1.0)
    return [v is None or v != 0.0 for v in vals]


def _first_present(item: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if item.get(key) is not None:
            return key
    return None


def _resolve_capacity(
    res: StageSchedResolver,
    cls: str,
    item: Mapping[str, Any],
    keys: tuple[str, ...],
) -> list[float | None]:
    key = _first_present(item, keys)
    if key is None:
        return [None] * res.n_stages
    return res.per_stage(cls, item.get(key), item.get("uid"), reduce="max")


def build_resolved_model(
    planning: Mapping[str, Any], resolver: StageSchedResolver
) -> ResolvedModel:
    """Resolve buses, lines and bus-attached elements per stage."""
    sys = planning.get("system", {}) or {}
    n = resolver.n_stages

    bus_names: list[str] = []
    name_by_uid: dict[Any, str] = {}
    zones: dict[str, str | None] = {}
    bus_active: dict[str, list[bool]] = {}
    for bus in sys.get("bus_array", []) or []:
        name = bus.get("name")
        if not name:
            continue
        name = str(name)
        bus_names.append(name)
        if bus.get("uid") is not None:
            name_by_uid[bus.get("uid")] = name
        zones[name] = bus_zone(bus)
        bus_active[name] = _active_list(resolver, "Bus", bus)

    def bus_ref(ref: Any) -> str:
        if ref is None:
            return ""
        if isinstance(ref, str):
            return ref
        return name_by_uid.get(ref, str(ref))

    lines: list[ResolvedLine] = []
    for raw in sys.get("line_array", []) or []:
        uid = raw.get("uid")
        line = ResolvedLine(
            uid=uid,
            name=str(raw.get("name", uid if uid is not None else "?")),
            bus_a=bus_ref(raw.get("bus_a")),
            bus_b=bus_ref(raw.get("bus_b")),
            raw=raw,
            reactance=resolver.per_stage(
                "Line", raw.get("reactance"), uid, reduce="min"
            ),
            resistance=resolver.per_stage(
                "Line", raw.get("resistance"), uid, reduce="min"
            ),
            tmax_ab=resolver.per_stage("Line", raw.get("tmax_ab"), uid),
            tmax_ba=resolver.per_stage("Line", raw.get("tmax_ba"), uid),
            voltage=resolver.per_stage("Line", raw.get("voltage"), uid),
            active=_active_list(resolver, "Line", raw),
        )
        cap = resolver.per_stage("Line", raw.get("capacity"), uid)
        line.tmax_ab = [c if t is None else t for t, c in zip(line.tmax_ab, cap)]
        line.tmax_ba = [c if t is None else t for t, c in zip(line.tmax_ba, cap)]
        act_a = bus_active.get(line.bus_a, [True] * n)
        act_b = bus_active.get(line.bus_b, [True] * n)
        line.in_service = [
            line.active[s]
            and act_a[s]
            and act_b[s]
            and not (line.tmax_ab[s] == 0.0 and line.tmax_ba[s] == 0.0)
            for s in range(n)
        ]
        lines.append(line)

    elements = _resolve_elements(sys, resolver, bus_ref)

    unresolved = sorted(resolver.missing_files)
    unresolved += [
        f"{path} (uid {uid} missing)" for path, uid in sorted(resolver.missing_uids)
    ]
    return ResolvedModel(
        stage_uids=list(resolver.stage_uids),
        bus_names=bus_names,
        bus_zone=zones,
        bus_active=bus_active,
        lines=lines,
        elements=elements,
        unresolved=unresolved,
    )


def _resolve_elements(
    sys: Mapping[str, Any],
    resolver: StageSchedResolver,
    bus_ref: Any,
) -> list[AttachedElement]:
    turbine_gens = {t.get("generator") for t in sys.get("turbine_array", []) or []} - {
        None
    }
    conv_gens: set[Any] = set()
    conv_dems: set[Any] = set()
    for conv in sys.get("converter_array", []) or []:
        conv_gens.add(conv.get("generator"))
        conv_dems.add(conv.get("demand"))

    def tags_for(
        item: Mapping[str, Any], linked: list[tuple[set[Any], str]]
    ) -> list[str]:
        tags: list[str] = []
        if isinstance(item.get("type"), str):
            tags.append(item["type"])
        for refs, tag in linked:
            if item.get("name") in refs or item.get("uid") in refs:
                tags.append(tag)
        return tags

    specs: list[tuple[str, str, str, tuple[str, ...], list[tuple[set[Any], str]]]] = [
        (
            "generator",
            "generator_array",
            "Generator",
            ("pmax", "capacity"),
            [(turbine_gens, "turbine"), (conv_gens, "converter")],
        ),
        (
            "demand",
            "demand_array",
            "Demand",
            ("lmax", "capacity"),
            [(conv_dems, "converter")],
        ),
        (
            "battery",
            "battery_array",
            "Battery",
            ("pmax_discharge", "capacity"),
            [],
        ),
    ]
    out: list[AttachedElement] = []
    for kind, array, cls, cap_keys, linked in specs:
        for item in sys.get(array, []) or []:
            bus = bus_ref(item.get("bus"))
            if not bus:
                continue  # e.g. a battery wired through source_generator
            out.append(
                AttachedElement(
                    kind=kind,
                    name=str(item.get("name", item.get("uid", "?"))),
                    uid=item.get("uid"),
                    bus=bus,
                    tags=tags_for(item, linked),
                    active=_active_list(resolver, cls, item),
                    capacity=_resolve_capacity(resolver, cls, item, cap_keys),
                    expandable=any(
                        item.get(k) not in (None, 0, 0.0) for k in _EXPANSION_KEYS
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Per-stage islands
# ---------------------------------------------------------------------------


@dataclass
class IslandElement:
    """One attached element as reported for a (small) island."""

    kind: str
    name: str
    uid: Any
    bus: str
    tags: list[str]
    capacity_mw: float | None


@dataclass
class StageIsland:
    """A connected component during a :class:`TopologyPeriod`."""

    buses: list[str]
    zones: list[str]
    n_elements: int
    mw_by_kind: dict[str, float]
    elements: list[IslandElement] = field(default_factory=list)
    # Active but 0-MW, non-expandable elements (ignored for bearing).
    n_inert: int = 0

    @property
    def has_elements(self) -> bool:
        return self.n_elements > 0

    @property
    def zone_label(self) -> str:
        return "+".join(self.zones) if self.zones else "?"


@dataclass
class TopologyPeriod:
    """Consecutive stages sharing the same island partition."""

    first_stage: int
    last_stage: int
    n_stages: int
    islands: list[StageIsland]
    n_inactive_buses: int = 0

    @property
    def islands_with_elements(self) -> list[StageIsland]:
        return [i for i in self.islands if i.has_elements]

    @property
    def stage_label(self) -> str:
        if self.first_stage == self.last_stage:
            return str(self.first_stage)
        return f"{self.first_stage}-{self.last_stage}"


@dataclass
class InterZoneLine:
    """A line whose endpoints belong to different zones."""

    uid: Any
    name: str
    bus_a: str
    bus_b: str
    zone_a: str
    zone_b: str
    service: list[tuple[int, int]]
    tmax: float

    @property
    def first_stage(self) -> int | None:
        return self.service[0][0] if self.service else None


@dataclass
class ExpectationViolation:
    """A period with more element-bearing islands than expected."""

    first_stage: int
    last_stage: int
    n_islands_with_elements: int
    offenders: list[StageIsland]


@dataclass
class IslandExpectation:
    """Result of ``--expect-islands`` / ``--expect-zones``."""

    expected: int
    zones: list[str]
    violations: list[ExpectationViolation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


@dataclass
class ElementOnInactiveBus:
    """An element with capacity whose bus is inactive in some stages."""

    kind: str
    name: str
    uid: Any
    bus: str
    stages: list[tuple[int, int]]
    capacity_mw: float | None


@dataclass
class StageTopology:
    """Everything :func:`analyse_stage_topology` produces."""

    n_stages: int
    periods: list[TopologyPeriod]
    inter_zone_lines: list[InterZoneLine]
    never_in_service: list[ResolvedLine]
    expectation: IslandExpectation | None = None
    elements_on_inactive_bus: list[ElementOnInactiveBus] = field(default_factory=list)


def _stage_components(model: ResolvedModel, s: int) -> tuple[list[frozenset[str]], int]:
    graph: nx.Graph = nx.Graph()
    inactive = 0
    for name in model.bus_names:
        if model.bus_active[name][s]:
            graph.add_node(name)
        else:
            inactive += 1
    for line in model.lines:
        if not line.in_service[s] or line.bus_a == line.bus_b:
            continue
        if line.bus_a in graph and line.bus_b in graph:
            graph.add_edge(line.bus_a, line.bus_b)
    comps = [frozenset(c) for c in nx.connected_components(graph)]
    return comps, inactive


def _build_island(
    model: ResolvedModel,
    comp: frozenset[str],
    elems_by_bus: dict[str, list[AttachedElement]],
    idxs: list[int],
    detail_max_buses: int,
) -> StageIsland:
    buses = sorted(comp)
    zones = sorted({z for b in buses if (z := model.bus_zone.get(b))})
    mw: dict[str, float] = defaultdict(float)
    details: list[IslandElement] = []
    n_elem = 0
    n_inert = 0
    for bus in buses:
        for elem in elems_by_bus.get(bus, []):
            on = [i for i in idxs if elem.effective(i)]
            if not on:
                if any(elem.active[i] for i in idxs):
                    n_inert += 1
                continue
            n_elem += 1
            cap = elem.max_capacity(on)
            mw[elem.kind] += cap or 0.0
            if len(buses) <= detail_max_buses:
                details.append(
                    IslandElement(
                        kind=elem.kind,
                        name=elem.name,
                        uid=elem.uid,
                        bus=bus,
                        tags=list(elem.tags),
                        capacity_mw=cap,
                    )
                )
    return StageIsland(
        buses=buses,
        zones=zones,
        n_elements=n_elem,
        mw_by_kind=dict(mw),
        elements=details,
        n_inert=n_inert,
    )


def _check_expectation(
    periods: list[TopologyPeriod],
    expect_islands: int | None,
    expect_zones: list[str] | None,
) -> IslandExpectation | None:
    if expect_islands is None and not expect_zones:
        return None
    zones = [z.upper() for z in (expect_zones or [])]
    expected = expect_islands if expect_islands is not None else len(zones)
    result = IslandExpectation(expected=expected, zones=zones)
    for period in periods:
        bearing = sorted(period.islands_with_elements, key=lambda i: -len(i.buses))
        if zones:
            keep: list[StageIsland] = []
            for zone in zones:
                for isl in bearing:
                    if zone in {z.upper() for z in isl.zones}:
                        if all(isl is not k for k in keep):
                            keep.append(isl)
                        break
            offenders = [i for i in bearing if all(i is not k for k in keep)]
            if len(bearing) - len(offenders) > expected:
                offenders += [k for k in keep[expected:] if k not in offenders]
        else:
            offenders = bearing[expected:]
        if offenders:
            result.violations.append(
                ExpectationViolation(
                    first_stage=period.first_stage,
                    last_stage=period.last_stage,
                    n_islands_with_elements=len(bearing),
                    offenders=offenders,
                )
            )
    return result


def analyse_stage_topology(
    model: ResolvedModel,
    *,
    expect_islands: int | None = None,
    expect_zones: list[str] | None = None,
    detail_max_buses: int = DEFAULT_DETAIL_MAX_BUSES,
) -> StageTopology:
    """Per-stage connectivity compressed into topology periods."""
    n = model.n_stages
    elems_by_bus = model.elements_by_bus()

    # Group consecutive stages with identical partition + bearing set.
    groups: list[tuple[list[int], list[frozenset[str]], int]] = []
    prev_key: Any = None
    for s in range(n):
        comps, inactive = _stage_components(model, s)
        bearing = frozenset(
            c
            for c in comps
            if any(e.effective(s) for b in c for e in elems_by_bus.get(b, []))
        )
        key = (frozenset(comps), bearing)
        if groups and key == prev_key:
            groups[-1][0].append(s)
        else:
            groups.append(([s], comps, inactive))
        prev_key = key

    periods: list[TopologyPeriod] = []
    for idxs, comps, inactive in groups:
        islands = [
            _build_island(model, c, elems_by_bus, idxs, detail_max_buses) for c in comps
        ]
        islands.sort(key=lambda i: (-len(i.buses), i.buses[0] if i.buses else ""))
        periods.append(
            TopologyPeriod(
                first_stage=model.stage_uids[idxs[0]],
                last_stage=model.stage_uids[idxs[-1]],
                n_stages=len(idxs),
                islands=islands,
                n_inactive_buses=inactive,
            )
        )

    inter: list[InterZoneLine] = []
    for line in model.lines:
        za = model.bus_zone.get(line.bus_a)
        zb = model.bus_zone.get(line.bus_b)
        if za is None or zb is None or za == zb:
            continue
        inter.append(
            InterZoneLine(
                uid=line.uid,
                name=line.name,
                bus_a=line.bus_a,
                bus_b=line.bus_b,
                zone_a=za,
                zone_b=zb,
                service=stage_ranges(model.stage_uids, line.service_idx),
                tmax=line.rep_tmax(),
            )
        )

    on_dead_bus: list[ElementOnInactiveBus] = []
    for elem in model.elements:
        bus_on = model.bus_active.get(elem.bus)
        if bus_on is None:
            continue
        idxs = [s for s in range(n) if elem.effective(s) and not bus_on[s]]
        if idxs:
            on_dead_bus.append(
                ElementOnInactiveBus(
                    kind=elem.kind,
                    name=elem.name,
                    uid=elem.uid,
                    bus=elem.bus,
                    stages=stage_ranges(model.stage_uids, idxs),
                    capacity_mw=elem.max_capacity(idxs),
                )
            )

    return StageTopology(
        n_stages=n,
        elements_on_inactive_bus=on_dead_bus,
        periods=periods,
        inter_zone_lines=inter,
        never_in_service=[ln for ln in model.lines if not any(ln.in_service)],
        expectation=_check_expectation(periods, expect_islands, expect_zones),
    )
