# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Top-level ``reduce`` driver: orchestrates the load → simplify → cluster
→ aggregate → rewrite → save pipeline.

This module is intentionally CLI-agnostic: it takes a ``Case`` plus a
``ReduceConfig`` and returns a ``ReduceResult`` ready to be written to
disk. ``main.py`` wraps it with argparse and CSV/JSON dumps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_reduce_network._aggregate import AggregatedLine, aggregate_lines
from gtopt_reduce_network._busmap import AggregatorRow, BusmapRow, LinemapRow
from gtopt_reduce_network._cluster import build_busmap, select_anchors
from gtopt_reduce_network._components import (
    expand_busmap_with_eliminated,
    rewrite_component_buses,
)
from gtopt_reduce_network._distance import (
    ptdf_distance_matrix,
    reactance_shortest_path_matrix,
    zbus_distance_matrix,
)
from gtopt_reduce_network._io import Case, resolve_bus_ref
from gtopt_reduce_network._local_simplify import simplify_local
from gtopt_reduce_network._protected_lines import collect_protected_lines
from gtopt_reduce_network._partition_nx import build_busmap_nx
from gtopt_reduce_network._line_schedules import aggregate_line_schedules
from gtopt_reduce_network._simplify import apply_loss_mode, apply_transport_only
from gtopt_reduce_network._topology import (
    PU_LINE_VOLTAGE,
    LineGraph,
    LineScheduleResolver,
    build_line_graph,
)

logger = logging.getLogger(__name__)


_LOSS_MODES = ("keep", "linear", "off", "uplift", "gen-lossfactor")
_COLLISION_MODES = ("replace", "add", "compound")
_PARTITIONS = ("hac", "louvain-mincut")
_NX_WEIGHTS = ("capacity", "susceptance")


@dataclass(slots=True)
class ReduceConfig:
    target_buses: int
    distance: str = "reactance-shortest-path"  # | "zbus" | "ptdf"
    reactance_rule: str = "series-parallel"
    user_anchor_uids: tuple[int, ...] = ()
    min_load_mw: float | None = None
    min_gen_capacity_mw: float | None = None
    include_reservoir_hosts: bool = True
    skip_local_simplify: bool = False
    drop_lines_below_mw: float | None = None
    integer_uid_offset: int = 1_000_000  # synth bus uid = offset + cluster idx
    # --- DC-line classification thresholds --------------------------------
    # A line is treated as DC (no KVL, transport capacity only) when its
    # reactance X <= dc_reactance_threshold (near-jumper, pu; default 1e-4
    # = PSS/E THRSHZ closed-switch standard), its voltage level (from bus
    # names) <= dc_voltage_threshold [kV] (sub-transmission), or its
    # capacity max(tmax_ab, tmax_ba) <= dc_power_threshold [MW].  The
    # voltage and power rules are disabled at 0.
    dc_reactance_threshold: float = 1e-4
    dc_voltage_threshold: float = 0.0
    dc_power_threshold: float = 0.0  # off by default; ≤30 MW recommended
    # --- Constraint-referenced line protection ----------------------------
    # Keep every line referenced by a full-model constraint (user
    # constraints, pampl files, line commitment) INTACT (original
    # uid/name/params, endpoints pinned as anchors) so its constraint stays
    # valid in the reduced case — essential when the reduced commitment is
    # used as a MIP warm-start seed for the full model.
    protect_constraint_lines: bool = False
    # Line uids to keep intact (same treatment as constraint-referenced
    # lines), e.g. an interconnector the study is about.
    protected_line_uids: tuple[int, ...] = ()
    # --- Partition backend -------------------------------------------------
    partition: str = "hac"  # hac | louvain-mincut
    nx_weight: str = "capacity"  # capacity | susceptance (louvain-mincut only)
    seed: int = 0  # louvain community seed (louvain-mincut only)
    # --- Transport / loss simplifications ---------------------------------
    transport_only: bool = False  # set options.use_kirchhoff=false
    loss_mode: str = "keep"  # keep | linear | off | uplift
    loss_uplift_pct: float = 3.0  # only used when loss_mode="uplift"
    loss_uplift_collision: str = "replace"  # replace | add | compound
    # When non-empty, also aggregate per-line parquet schedules
    # (Line/<field>.parquet) into sibling Line/<field>_<reduced_tag>.parquet
    # files; the reduced JSON's per-line field references the new stem.
    reduced_tag: str = ""
    # Directory containing the original case's Line/ subtree.  Defaults
    # to the parent dir of the input JSON; the cascade-reduced flow
    # passes it explicitly.
    parquet_case_dir: str | None = None
    # --- Fixed bus grouping (zonal / country equivalents) -----------------
    # When non-empty, the partition is NOT computed: every bus uid is
    # mapped to the named group given here (e.g. {"CL", "PE"} for a
    # two-node interconnection model), local simplification is skipped,
    # and ``target_buses`` is ignored (= number of groups).  The group
    # representative bus is the smallest user anchor uid in the group,
    # else the smallest bus uid.  Lines between groups are aggregated
    # (capacities summed); lines inside a group vanish.
    bus_groups: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.loss_mode not in _LOSS_MODES:
            raise ValueError(
                f"loss_mode must be one of {_LOSS_MODES}, got {self.loss_mode!r}"
            )
        if self.loss_uplift_collision not in _COLLISION_MODES:
            raise ValueError(
                f"loss_uplift_collision must be one of {_COLLISION_MODES}, "
                f"got {self.loss_uplift_collision!r}"
            )
        if self.partition not in _PARTITIONS:
            raise ValueError(
                f"partition must be one of {_PARTITIONS}, got {self.partition!r}"
            )
        if self.nx_weight not in _NX_WEIGHTS:
            raise ValueError(
                f"nx_weight must be one of {_NX_WEIGHTS}, got {self.nx_weight!r}"
            )
        if self.dc_reactance_threshold < 0.0:
            raise ValueError(
                "dc_reactance_threshold must be >= 0, got "
                f"{self.dc_reactance_threshold}"
            )
        if self.dc_voltage_threshold < 0.0:
            raise ValueError(
                f"dc_voltage_threshold must be >= 0, got {self.dc_voltage_threshold}"
            )
        if self.dc_power_threshold < 0.0:
            raise ValueError(
                f"dc_power_threshold must be >= 0, got {self.dc_power_threshold}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_buses": self.target_buses,
            "distance": self.distance,
            "reactance_rule": self.reactance_rule,
            "user_anchor_uids": list(self.user_anchor_uids),
            "min_load_mw": self.min_load_mw,
            "min_gen_capacity_mw": self.min_gen_capacity_mw,
            "include_reservoir_hosts": self.include_reservoir_hosts,
            "skip_local_simplify": self.skip_local_simplify,
            "drop_lines_below_mw": self.drop_lines_below_mw,
            "integer_uid_offset": self.integer_uid_offset,
            "dc_reactance_threshold": self.dc_reactance_threshold,
            "dc_voltage_threshold": self.dc_voltage_threshold,
            "dc_power_threshold": self.dc_power_threshold,
            "protect_constraint_lines": self.protect_constraint_lines,
            "protected_line_uids": list(self.protected_line_uids),
            "partition": self.partition,
            "nx_weight": self.nx_weight,
            "seed": self.seed,
            "transport_only": self.transport_only,
            "loss_mode": self.loss_mode,
            "loss_uplift_pct": self.loss_uplift_pct,
            "loss_uplift_collision": self.loss_uplift_collision,
            "reduced_tag": self.reduced_tag,
            "parquet_case_dir": self.parquet_case_dir,
            "bus_groups": {str(k): v for k, v in sorted(self.bus_groups.items())},
        }


@dataclass(slots=True)
class ReduceResult:
    case: Case  # the reduced case (mutated copy)
    busmap: list[BusmapRow]
    linemap: list[LinemapRow]
    aggregator: list[AggregatorRow]
    aggregated_lines: list[AggregatedLine]
    eliminated_buses: list[int] = field(default_factory=list)
    anchor_uids: list[int] = field(default_factory=list)


def reduce_case(case: Case, config: ReduceConfig) -> ReduceResult:
    """Run the full reduction pipeline against ``case`` (not mutated)."""
    # Operate on a deep copy so the caller's Case is untouched.
    work = case.deepcopy()
    schedules = None
    bus_schedules = None
    if config.parquet_case_dir:
        input_directory = work.options.get("input_directory", ".") or "."
        input_root = Path(config.parquet_case_dir) / str(input_directory)
        schedules = LineScheduleResolver(input_root / "Line")
        bus_schedules = LineScheduleResolver(input_root / "Bus")
    graph = build_line_graph(
        work,
        dc_reactance_threshold=config.dc_reactance_threshold,
        dc_voltage_threshold=config.dc_voltage_threshold,
        dc_power_threshold=config.dc_power_threshold,
        schedules=schedules,
    )
    logger.info(
        "loaded %d buses, %d lines (%d skipped, %d DC)",
        graph.n_buses,
        graph.n_lines,
        len(graph.skipped_line_uids),
        len(graph.dc_line_uids),
    )

    # 0. Constraint-referenced lines to keep intact ------------------------
    protected_line_uids: set[int] = set()
    protected_bus_uids: set[int] = set()
    if config.protect_constraint_lines:
        input_dir = Path(config.parquet_case_dir) if config.parquet_case_dir else None
        protected_line_uids, protected_bus_uids = collect_protected_lines(
            work, input_dir=input_dir
        )
    if config.protected_line_uids:
        by_uid = {int(ln["uid"]): ln for ln in work.array("line_array")}
        for u in config.protected_line_uids:
            ln = by_uid.get(int(u))
            if ln is None:
                raise ValueError(f"protected line uid {u} not in line_array")
            protected_line_uids.add(int(u))
            for end in ("bus_a", "bus_b"):
                b = resolve_bus_ref(work, ln.get(end))
                if b is not None:
                    protected_bus_uids.add(int(b))

    # 1. Local simplify ----------------------------------------------------
    dc_mask = graph.line_is_dc
    assert dc_mask is not None
    simplified_buses = list(graph.bus_uids)
    surviving_uids = list(graph.line_uids)
    surviving_a_uids = [
        graph.bus_uids[int(graph.line_a[i])] for i in range(graph.n_lines)
    ]
    surviving_b_uids = [
        graph.bus_uids[int(graph.line_b[i])] for i in range(graph.n_lines)
    ]
    surviving_x = list(map(float, graph.line_x))
    surviving_r = list(map(float, graph.line_r))
    surviving_fab = list(map(float, graph.line_fab))
    surviving_fba = list(map(float, graph.line_fba))
    surviving_dc = [bool(v) for v in dc_mask]
    local_linemap: list[LinemapRow] = []
    eliminated_buses: list[int] = []
    parent_of_eliminated: dict[int, int] = {}

    if not config.skip_local_simplify and not config.bus_groups:
        # DC lines are controllable devices, not impedances: exclude them
        # from the (exact-for-DC-OPF) X-based merges and pin their endpoint
        # buses so degree-2 elimination cannot swallow a converter station.
        injection_set = (
            _injection_bus_set(work) | _dc_endpoint_bus_uids(graph) | protected_bus_uids
        )
        sr = simplify_local(
            _ac_only_graph(graph, frozenset(protected_line_uids)),
            injection_bus_uids=injection_set,
        )
        # Track parent-of-eliminated for busmap expansion below.
        # The simplest assignment: each eliminated bus points to whichever
        # surviving bus appears first in its outgoing series-merged lines.
        parent_of_eliminated = _eliminated_to_parent(graph, sr.eliminated_buses)
        simplified_buses = list(sr.surviving_buses)
        surviving_uids = list(sr.surviving_line_uids)
        surviving_a_uids = list(sr.surviving_a)
        surviving_b_uids = list(sr.surviving_b)
        surviving_x = list(sr.surviving_x)
        surviving_r = list(sr.surviving_r)
        surviving_fab = list(sr.surviving_fab)
        surviving_fba = list(sr.surviving_fba)
        surviving_dc = [False] * len(surviving_uids)
        for pos in range(graph.n_lines):  # re-append the untouched DC lines
            if not dc_mask[pos]:
                continue
            surviving_uids.append(graph.line_uids[pos])
            surviving_a_uids.append(graph.bus_uids[int(graph.line_a[pos])])
            surviving_b_uids.append(graph.bus_uids[int(graph.line_b[pos])])
            surviving_x.append(float("inf"))
            surviving_r.append(float(graph.line_r[pos]))
            surviving_fab.append(float(graph.line_fab[pos]))
            surviving_fba.append(float(graph.line_fba[pos]))
            surviving_dc.append(True)
        local_linemap = list(sr.linemap)
        eliminated_buses = list(sr.eliminated_buses)
        logger.info(
            "local-simplify: %d→%d buses, %d→%d lines",
            graph.n_buses,
            len(simplified_buses),
            graph.n_lines,
            len(surviving_uids),
        )

    # 2. Sub-graph over surviving buses/lines ------------------------------
    sub_graph = LineGraph(
        bus_uids=simplified_buses,
        bus_index={u: i for i, u in enumerate(simplified_buses)},
        line_uids=surviving_uids,
        line_a=np.asarray(
            [simplified_buses.index(b) for b in surviving_a_uids], dtype=np.int64
        ),
        line_b=np.asarray(
            [simplified_buses.index(b) for b in surviving_b_uids], dtype=np.int64
        ),
        line_x=np.asarray(surviving_x, dtype=float),
        line_r=np.asarray(surviving_r, dtype=float),
        line_fab=np.asarray(surviving_fab, dtype=float),
        line_fba=np.asarray(surviving_fba, dtype=float),
        line_is_dc=np.asarray(surviving_dc, dtype=bool),
    )

    # 3. Anchors + cluster -------------------------------------------------
    # Pin the endpoints of protected lines so both survive in DISTINCT
    # clusters → the protected line stays an inter-cluster edge (kept
    # below with its original identity).
    anchor_uids = tuple(config.user_anchor_uids) + tuple(
        b for b in protected_bus_uids if b in set(simplified_buses)
    )
    anchor_bus_uids: list[int] = list(config.user_anchor_uids)
    if not config.bus_groups:
        anchor_bus_uids = list(
            select_anchors(
                work,
                target_buses=config.target_buses,
                surviving_bus_uids=simplified_buses,
                user_anchor_uids=anchor_uids,
                min_load_mw=config.min_load_mw,
                min_gen_capacity_mw=config.min_gen_capacity_mw,
                include_reservoir_hosts=config.include_reservoir_hosts,
            ).bus_uids
        )
    if config.bus_groups:
        # Fixed groups: no anchor selection (the groups ARE the partition).
        busmap = busmap_from_groups(
            simplified_buses, config.bus_groups, config.user_anchor_uids
        )
    elif config.partition == "louvain-mincut":
        busmap = build_busmap_nx(
            sub_graph,
            target_buses=config.target_buses,
            anchors=anchor_bus_uids,
            weight_mode=config.nx_weight,
            seed=config.seed,
        )
    else:
        # The distance matrix is only needed by the HAC path (the ptdf
        # metric in particular is dense and expensive — skip when unused).
        distance = _distance_matrix(sub_graph, config.distance)
        busmap = build_busmap(
            simplified_buses,
            distance,
            target_buses=config.target_buses,
            anchors=anchor_bus_uids,
        )
    cluster_of_bus_simplified = {
        row.original_bus_uid: row.cluster_bus_uid for row in busmap
    }

    # 4. Expand busmap to cover eliminated buses too ----------------------
    full_busmap = expand_busmap_with_eliminated(
        busmap,
        eliminated_buses,
        parent_bus_of_eliminated=parent_of_eliminated,
    )
    cluster_of_bus_full = {
        row.original_bus_uid: row.cluster_bus_uid for row in full_busmap
    }

    # 5. Aggregate inter-cluster lines ------------------------------------
    # Protected (constraint-referenced) lines bypass aggregation: they are
    # re-emitted UNCHANGED below (original uid/name/params) so their
    # constraint stays valid.  Filter them out of the aggregation input so
    # their capacity is not also folded into a corridor (double count).
    if protected_line_uids:
        keep = [i for i, u in enumerate(surviving_uids) if u not in protected_line_uids]
        surviving_uids = [surviving_uids[i] for i in keep]
        surviving_a_uids = [surviving_a_uids[i] for i in keep]
        surviving_b_uids = [surviving_b_uids[i] for i in keep]
        surviving_x = [surviving_x[i] for i in keep]
        surviving_r = [surviving_r[i] for i in keep]
        surviving_fab = [surviving_fab[i] for i in keep]
        surviving_fba = [surviving_fba[i] for i in keep]
        surviving_dc = [surviving_dc[i] for i in keep]

    next_uid = (max(graph.line_uids) if graph.line_uids else 0) + 100_001
    agg_lines, agg_linemap = aggregate_lines(
        surviving_uids,
        surviving_a_uids,
        surviving_b_uids,
        surviving_x,
        surviving_r,
        surviving_fab,
        surviving_fba,
        cluster_of_bus_simplified,
        rule=config.reactance_rule,
        next_line_uid=next_uid,
        surviving_is_dc=surviving_dc,
    )

    # Optional cap pruning.
    if config.drop_lines_below_mw is not None:
        thr = float(config.drop_lines_below_mw)
        agg_lines = [ln for ln in agg_lines if max(ln.tmax_ab, ln.tmax_ba) >= thr]

    # 6. Build the reduced JSON system arrays ------------------------------
    # Snapshot the ORIGINAL line_array before it gets replaced below — the
    # schedule aggregator needs scalar fallback values keyed by original uid.
    original_line_array = list(work.system.get("line_array", []))
    cluster_bus_uids = sorted({row.cluster_bus_uid for row in full_busmap})
    work.system["bus_array"] = _build_bus_array(
        work,
        cluster_bus_uids,
        work.bus_name_by_uid,
        members=_members_of(full_busmap),
        bus_schedules=bus_schedules,
    )
    reduced_lines = _build_line_array(agg_lines, work.bus_name_by_uid)
    # Re-emit protected lines UNCHANGED (original uid/name/reactance/tmax/
    # voltage/lossfactor/type): their endpoints are anchors so they survive
    # in their own clusters, keeping each line's identity — and its
    # constraint — valid.  Bus refs are remapped to the endpoint's cluster
    # representative (itself, for an anchor) for robustness.
    if protected_line_uids:
        # Protected uids were collected from (or validated against) this
        # very line_array.
        by_uid = {int(ln["uid"]): ln for ln in original_line_array}
        for u in sorted(protected_line_uids):
            src = by_uid[u]
            if config.bus_groups:
                ends = []
                for end in ("bus_a", "bus_b"):
                    try:
                        b = resolve_bus_ref(work, src.get(end))
                    except (KeyError, TypeError):
                        b = None
                    ends.append(cluster_of_bus_full.get(b, b))
                if ends[0] == ends[1]:
                    logger.warning(
                        "protected line uid=%d lies inside one bus group; "
                        "dropped (intra-group lines vanish)",
                        u,
                    )
                    continue
            entry = dict(src)
            for end in ("bus_a", "bus_b"):
                try:
                    b = resolve_bus_ref(work, src.get(end))
                except (KeyError, TypeError):
                    b = None
                if b is not None:
                    rep = cluster_of_bus_full.get(b, b)
                    entry[end] = work.bus_name_by_uid.get(rep, rep)
            reduced_lines.append(entry)
        logger.info(
            "re-emitted %d protected lines with original identity",
            len(protected_line_uids),
        )
    work.system["line_array"] = reduced_lines

    # 7. Rewrite component bus refs ---------------------------------------
    aggregator = rewrite_component_buses(
        work,
        cluster_of_bus_full,
        bus_name_by_uid={
            u: work.bus_name_by_uid.get(u, f"bus_{u}") for u in cluster_bus_uids
        },
    )

    # Reindex bus name maps for the reduced case.
    work.bus_uid_by_name = {
        n: u for u, n in work.bus_name_by_uid.items() if u in set(cluster_bus_uids)
    }
    work.bus_name_by_uid = {
        u: n for u, n in work.bus_name_by_uid.items() if u in set(cluster_bus_uids)
    }

    # 8. Optional model-side simplifications ------------------------------
    if config.transport_only:
        apply_transport_only(work)
    if config.loss_mode != "keep":
        apply_loss_mode(
            work,
            config.loss_mode,
            config.loss_uplift_pct,
            collision=config.loss_uplift_collision,
        )

    # 9. Optional per-line parquet schedule aggregation -------------------
    if config.reduced_tag and config.parquet_case_dir:
        input_directory = work.options.get("input_directory", ".") or "."
        ac_agg_lines = [ln for ln in agg_lines if not ln.is_dc]
        if len(ac_agg_lines) != len(agg_lines):
            logger.info(
                "line-schedule: %d DC equivalent lines keep their scalar "
                "fields (schedule aggregation covers AC corridors only)",
                len(agg_lines) - len(ac_agg_lines),
            )
        field_to_stem = aggregate_line_schedules(
            case_dir=Path(config.parquet_case_dir),
            input_directory=str(input_directory),
            reduced_tag=config.reduced_tag,
            aggregated_lines=ac_agg_lines,
            original_line_array=original_line_array,
        )
        # Rewrite each reduced line's JSON entry: replace the scalar value
        # of any aggregated field with the new parquet stem.  DC equivalent
        # lines keep their scalar fields (no X schedules to point at).
        if field_to_stem:
            for ln in work.array("line_array"):
                if ln.get("type") == "dc":
                    continue
                for field, new_stem in field_to_stem.items():
                    ln[field] = new_stem
            logger.info(
                "line-schedule: rewrote %d reduced line entries with stems for %s",
                len(work.array("line_array")),
                list(field_to_stem),
            )

    return ReduceResult(
        case=work,
        busmap=full_busmap,
        linemap=local_linemap + agg_linemap,
        aggregator=aggregator,
        aggregated_lines=agg_lines,
        eliminated_buses=eliminated_buses,
        anchor_uids=anchor_bus_uids,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def busmap_from_groups(
    bus_uids: list[int],
    groups: dict[int, str],
    anchor_uids: tuple[int, ...] = (),
) -> list[BusmapRow]:
    """Busmap for a fixed bus → group assignment.

    Every bus in ``bus_uids`` must have a group.  The representative
    (cluster) bus of a group is its smallest anchor uid, else its smallest
    bus uid — so the reduced bus keeps a real bus identity/name.
    """
    missing = [u for u in bus_uids if u not in groups]
    if missing:
        raise ValueError(
            f"{len(missing)} bus(es) have no group (first: {missing[:5]}); "
            "every bus must be assigned when bus groups are given"
        )
    anchors = set(anchor_uids)
    members: dict[str, list[int]] = {}
    for u in bus_uids:
        members.setdefault(groups[u], []).append(u)
    rep_of: dict[str, int] = {}
    for g, us in members.items():
        g_anchors = sorted(a for a in us if a in anchors)
        rep_of[g] = g_anchors[0] if g_anchors else min(us)
    logger.info(
        "bus groups: %s",
        ", ".join(
            f"{g}={len(us)} buses (rep {rep_of[g]})" for g, us in members.items()
        ),
    )
    return [
        BusmapRow(original_bus_uid=u, cluster_bus_uid=rep_of[groups[u]])
        for u in bus_uids
    ]


def bus_groups_from_regex(case: Case, pattern: str) -> dict[int, str]:
    """Group buses by a regex on the bus name (group 1, else whole match).

    Buses whose name does not match are an error — the grouping must be
    total so no bus silently forms its own node.
    """
    import re  # pylint: disable=import-outside-toplevel

    rx = re.compile(pattern)
    out: dict[int, str] = {}
    unmatched: list[str] = []
    for uid, name in case.bus_name_by_uid.items():
        m = rx.search(str(name))
        if m is None:
            unmatched.append(str(name))
            continue
        out[int(uid)] = m.group(1) if m.groups() else m.group(0)
    if unmatched:
        raise ValueError(
            f"--bus-group-regex {pattern!r} does not match {len(unmatched)} "
            f"bus name(s) (first: {unmatched[:5]})"
        )
    return out


def bus_groups_from_csv(case: Case, path: Path) -> dict[int, str]:
    """Read a ``bus,group`` CSV (bus = uid or name) into a uid → group map."""
    import csv  # pylint: disable=import-outside-toplevel

    out: dict[int, str] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ref: Any = row["bus"].strip()
            if ref.lstrip("-").isdigit():
                ref = int(ref)
            uid = resolve_bus_ref(case, ref)
            assert uid is not None
            out[int(uid)] = row["group"].strip()
    return out


def _distance_matrix(graph: LineGraph, kind: str) -> np.ndarray:
    if kind == "reactance-shortest-path":
        return reactance_shortest_path_matrix(graph)
    if kind == "zbus":
        return zbus_distance_matrix(graph)
    if kind == "ptdf":
        return ptdf_distance_matrix(graph)
    raise ValueError(
        f"unknown distance metric {kind!r}; "
        "supported: reactance-shortest-path | zbus | ptdf"
    )


def _ac_only_graph(
    graph: LineGraph, exclude_uids: frozenset[int] = frozenset()
) -> LineGraph:
    """Copy of ``graph`` with DC and ``exclude_uids`` lines removed.

    ``exclude_uids`` holds protected (constraint-referenced) lines that
    must not be parallel-merged by local-simplify — they are re-emitted
    with their original identity later.  All buses are kept.
    """
    mask = graph.line_is_dc
    assert mask is not None
    keep = [
        p
        for p in range(graph.n_lines)
        if not mask[p] and graph.line_uids[p] not in exclude_uids
    ]
    return LineGraph(
        bus_uids=list(graph.bus_uids),
        bus_index=dict(graph.bus_index),
        line_uids=[graph.line_uids[p] for p in keep],
        line_a=graph.line_a[keep],
        line_b=graph.line_b[keep],
        line_x=graph.line_x[keep],
        line_r=graph.line_r[keep],
        line_fab=graph.line_fab[keep],
        line_fba=graph.line_fba[keep],
    )


def _dc_endpoint_bus_uids(graph: LineGraph) -> set[int]:
    """Bus uids hosting a DC-line terminal (converter stations)."""
    mask = graph.line_is_dc
    assert mask is not None
    out: set[int] = set()
    for pos in range(graph.n_lines):
        if mask[pos]:
            out.add(graph.bus_uids[int(graph.line_a[pos])])
            out.add(graph.bus_uids[int(graph.line_b[pos])])
    return out


def _injection_bus_set(case: Case) -> set[int]:
    out: set[int] = set()
    for arr in (
        "generator_array",
        "demand_array",
        "battery_array",
        "turbine_array",
    ):
        for elem in case.array(arr):
            try:
                bus_uid = resolve_bus_ref(case, elem.get("bus"))
            except (KeyError, TypeError):
                continue
            if bus_uid is not None:
                out.add(bus_uid)
    return out


def _eliminated_to_parent(
    graph: LineGraph, eliminated_buses: list[int]
) -> dict[int, int]:
    """Map each eliminated bus to a surviving neighbour via its incidence."""
    out: dict[int, int] = {}
    incident: dict[int, list[int]] = {u: [] for u in graph.bus_uids}
    for pos in range(graph.n_lines):
        a = graph.bus_uids[int(graph.line_a[pos])]
        b = graph.bus_uids[int(graph.line_b[pos])]
        incident[a].append(b)
        incident[b].append(a)
    elim_set = set(int(u) for u in eliminated_buses)
    for eb in eliminated_buses:
        for nb in incident.get(int(eb), ()):
            if nb not in elim_set:
                out[int(eb)] = int(nb)
                break
    return out


def _members_of(busmap: list[BusmapRow]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for row in busmap:
        out.setdefault(row.cluster_bus_uid, []).append(row.original_bus_uid)
    return out


def _bus_ever_active(
    bus: dict[str, Any] | None, bus_schedules: LineScheduleResolver | None
) -> bool | None:
    """True/False when known whether ``bus`` is active in some stage.

    None when the bus has a schedule-valued ``active`` that cannot be
    resolved (no case dir) — treated as "possibly active" by the caller.
    """
    if bus is None or "active" not in bus:
        return True
    value = bus["active"]
    if isinstance(value, str):
        if bus_schedules is None:
            return None
        arr = bus_schedules.values(value, int(bus["uid"]))
        return None if arr is None else bool(np.any(arr > 0))
    if isinstance(value, list):
        return bool(np.any(np.asarray(value, dtype=float) > 0))
    return bool(value)


def _build_bus_array(
    case: Case,
    cluster_bus_uids: list[int],
    name_by_uid: dict[int, str],
    members: dict[int, list[int]] | None = None,
    bus_schedules: LineScheduleResolver | None = None,
) -> list[dict[str, Any]]:
    """Reduced bus_array: one entry per cluster representative.

    Optional fields (voltage, region, ...) of a surviving representative
    are preserved — except ``active``: a cluster is in service whenever
    ANY member is (the same any-stage relaxation as the aggregated lines),
    so the representative's own schedule is dropped when the cluster has
    a member that is active in some stage.  Otherwise a representative
    from an old network vintage (active only in early stages) would switch
    off a cluster whose other members belong to the later network,
    islanding its components.
    """
    by_uid = {int(b["uid"]): b for b in case.array("bus_array")}
    out: list[dict[str, Any]] = []
    for u in cluster_bus_uids:
        if u in by_uid:
            entry = dict(by_uid[u])
            group = (members or {}).get(u, [u])
            if "active" in entry and len(group) > 1:
                ever = [_bus_ever_active(by_uid.get(m), bus_schedules) for m in group]
                if any(e is not False for e in ever):
                    del entry["active"]
            out.append(entry)
        else:
            out.append({"uid": int(u), "name": name_by_uid.get(u, f"bus_{u}")})
    return out


def _build_line_array(
    agg_lines: list[AggregatedLine],
    name_by_uid: dict[int, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in agg_lines:
        entry: dict[str, Any] = {
            "uid": ln.uid,
            "name": ln.name,
            "bus_a": name_by_uid.get(ln.bus_a_uid, ln.bus_a_uid),
            "bus_b": name_by_uid.get(ln.bus_b_uid, ln.bus_b_uid),
            "tmax_ab": ln.tmax_ab,
            "tmax_ba": ln.tmax_ba,
        }
        # Aggregated impedances are per-unit on MVA_BASE (see
        # ``build_line_graph``); V = √MVA_BASE makes gtopt's X/V² and
        # R/V² equal the originals' whatever their source convention.
        if ln.is_dc:
            # DC equivalent: no reactance — gtopt's KVL emitter skips the
            # line, leaving pure transport capacity (kirchhoff_node_angle).
            entry["type"] = "dc"
            if ln.resistance > 0.0:
                entry["resistance"] = ln.resistance
                entry["voltage"] = PU_LINE_VOLTAGE
        else:
            entry["reactance"] = ln.reactance
            entry["resistance"] = ln.resistance
            entry["voltage"] = PU_LINE_VOLTAGE
        out.append(entry)
    return out
