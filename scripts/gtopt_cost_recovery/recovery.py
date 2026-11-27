# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-project cost recovery of a solved gtopt expansion case."""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gtopt_pricing_uplift._data import (
    Case,
    Period,
    SeriesMap,
    load_case,
    load_series,
    read_pricing_csv,
    resolve_ref,
    sched_value,
)

log = logging.getLogger(__name__)

HOURS_PER_YEAR = 8760.0
#: gtopt's ``model_options.demand_fail_cost`` default [$/MWh]
DEFAULT_DEMAND_FAIL_COST = 1000.0

#: (scenario, stage, uid) -> value of a stage-level stream
StageMap = dict[tuple[int, int, int], float]


def capital_recovery_factor(rate: float, life: float | None) -> float:
    """CRF(r, n) = r(1+r)^n / ((1+r)^n − 1) — mirrors the C++ helper.

    ``life`` unset or ``<= 0`` → perpetuity (``r``); ``r == 0`` →
    straight line (``1/n``); ``r <= −1`` → 0.
    """
    if rate <= -1.0:
        return 0.0
    if life is None or life <= 0.0:
        return max(rate, 0.0)
    if abs(rate) < 1e-12:
        return 1.0 / life
    g = (1.0 + rate) ** life
    return rate * g / (g - 1.0)


def _stage_value(value: Any, stage_index: int) -> float | None:
    """Scalar or per-stage list value of a stage-scheduled field."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list) and value:
        item = value[min(stage_index, len(value) - 1)]
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            return float(item)
    if isinstance(value, str):
        log.warning("file-scheduled cost field %r is not resolved", value)
    return None


def annual_capcost(
    elem: dict[str, Any],
    stage_index: int,
    social_rate: float,
    prefix: str = "",
) -> float:
    """Annualised cost [$/unit-year] the LP charges at a stage.

    ``<prefix>annual_capcost`` when set, else
    ``<prefix>overnight_capcost × CRF(wacc, economic_life)`` with ``wacc``
    defaulting to the social rate, else 0 (gtopt's precedence).
    """
    annual = _stage_value(elem.get(f"{prefix}annual_capcost"), stage_index)
    if annual is not None:
        return annual
    overnight = _stage_value(elem.get(f"{prefix}overnight_capcost"), stage_index)
    if overnight is None:
        return 0.0
    wacc = _stage_value(elem.get("wacc"), stage_index)
    life = _stage_value(elem.get("economic_life"), stage_index)
    return overnight * capital_recovery_factor(
        social_rate if wacc is None else wacc, life
    )


@dataclass
class ProjectRecovery:
    """Cost recovery of one expandable element (all money in $)."""

    kind: str  #: Generator / Line / Battery
    name: str
    uid: int
    unit: str  #: MW or MWh (battery energy)
    existing: float = 0.0  #: installed before expansion (last stage)
    installed: float = 0.0  #: capainst (last stage)
    new: float = 0.0  #: installed − existing
    revenue: float = 0.0  #: Σ λ·q·Δ (whole element)
    opex: float = 0.0  #: Σ c·q·Δ (whole element, variable)
    #: fixed O&M of the new capacity (``fixed_om_cost``; an opex component
    #: the LP charges with the annuity)
    fixed_om: float = 0.0
    capex: float = 0.0  #: LP annuity charge of the new capacity
    #: marginal value of one more unit over the horizon [$/unit]: price-taker
    #: margin Σ max(λ − c, 0)·a·Δ (generator), spread Σ |Δλ|·Δ (line),
    #: capacity dual sum Σ μ·Δ (battery energy)
    value_per_unit: float = 0.0
    cost_per_unit: float = 0.0  #: annual_capcost·T/8760 [$/unit]
    power_new: float | None = None  #: battery power axis [MW]
    power_capex: float | None = None
    power_value_per_unit: float | None = None
    power_cost_per_unit: float | None = None
    #: part of the margin earned from the capacity rent of the price
    #: (λ above the costliest running variable cost: peak-load pricing)
    capacity_rent: float = 0.0
    #: part of the margin earned in blocks with unserved energy (price set
    #: by the failure cost); 0 in a well-posed expansion plan
    failure_rent: float = 0.0

    @property
    def built(self) -> bool:
        return self.new > 1e-6 or (self.power_new or 0.0) > 1e-6

    @property
    def margin(self) -> float:
        return self.revenue - self.opex

    @property
    def variable_margin(self) -> float:
        """Margin from the variable-cost spread (λ at a running unit's cost)."""
        return self.margin - self.capacity_rent - self.failure_rent

    @property
    def margin_new(self) -> float:
        """Margin attributable to the new capacity (pro rata)."""
        if self.installed <= 1e-9:
            return self.margin
        if self.existing <= 1e-9:
            return self.margin
        return self.margin * self.new / self.installed

    @property
    def total_capex(self) -> float:
        return self.capex + (self.power_capex or 0.0)

    @property
    def total_opex(self) -> float:
        """Variable opex plus fixed O&M."""
        return self.opex + self.fixed_om

    @property
    def profit(self) -> float:
        return self.margin_new - self.fixed_om - self.total_capex

    @property
    def profit_over_capex(self) -> float | None:
        return self.profit / self.total_capex if self.total_capex > 1e-9 else None

    @property
    def make_whole(self) -> float:
        return max(0.0, -self.profit) if self.built else 0.0

    @property
    def net_value_per_unit(self) -> float:
        """Marginal value − marginal cost per unit (≤ 0 ⇒ no more wanted)."""
        return self.value_per_unit - self.cost_per_unit

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            built=self.built,
            margin=self.margin,
            variable_margin=self.variable_margin,
            margin_new=self.margin_new,
            total_opex=self.total_opex,
            total_capex=self.total_capex,
            profit=self.profit,
            profit_over_capex=self.profit_over_capex,
            make_whole=self.make_whole,
            net_value_per_unit=self.net_value_per_unit,
        )
        return d


@dataclass
class MarketBalance:
    """Money flows at the marginal prices (the merchandising identity)."""

    load_payments: float = 0.0  #: Σ λ·served load·Δ (battery charge excluded)
    unserved_mwh: float = 0.0
    generator_revenue: float = 0.0  #: battery discharge excluded
    battery_net_revenue: float = 0.0
    congestion_rent: float = 0.0
    #: Σ λ·(electricity injected − withdrawn)·Δ of carrier converters
    #: (an electrolyser buys: negative)
    converter_net_revenue: float = 0.0

    @property
    def residual(self) -> float:
        """load payments − (generators + batteries + converters + lines)."""
        return self.load_payments - (
            self.generator_revenue
            + self.battery_net_revenue
            + self.converter_net_revenue
            + self.congestion_rent
        )


@dataclass
class RecoveryReport:
    """Cost recovery of one solved case."""

    results_dir: str
    horizon_hours: float
    scale: float  #: money multiplier applied (8760/horizon with per_year)
    obj_value: float | None
    pricing_mode: str
    applied_modes: list[str] = field(default_factory=list)
    projects: list[ProjectRecovery] = field(default_factory=list)
    existing: list[ProjectRecovery] = field(default_factory=list)
    market: MarketBalance = field(default_factory=MarketBalance)
    #: per (period, bus) price decomposition (``price_blocks=True`` only)
    blocks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_failure_rent(self) -> float:
        """Margin of the projects earned at failure prices (should be 0)."""
        return sum(p.failure_rent for p in self.projects)

    @property
    def total_capex(self) -> float:
        return sum(p.total_capex for p in self.projects)

    @property
    def total_profit(self) -> float:
        return sum(p.profit for p in self.projects)

    @property
    def total_make_whole(self) -> float:
        return sum(p.make_whole for p in self.projects)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results_dir": self.results_dir,
            "horizon_hours": self.horizon_hours,
            "scale": self.scale,
            "obj_value": self.obj_value,
            "pricing_mode": self.pricing_mode,
            "applied_modes": self.applied_modes,
            "total_capex": self.total_capex,
            "total_profit": self.total_profit,
            "total_make_whole": self.total_make_whole,
            "market": {**asdict(self.market), "residual": self.market.residual},
            "projects": [p.to_dict() for p in self.projects],
            "existing": [p.to_dict() for p in self.existing],
            "total_failure_rent": self.total_failure_rent,
            "blocks": self.blocks,
        }


# ── loading ────────────────────────────────────────────────────────────


def _load_stage(results_dir: Path, stem: str) -> StageMap:
    """Stage-level stream (block column ignored) as ``{(scen, stage, uid)}``."""
    series = load_series(results_dir, stem)
    out: StageMap = {}
    for (scen, stage, _block, uid), value in (series or {}).items():
        out[(scen, stage, uid)] = value
    return out


@dataclass
class _Ctx:
    case: Case
    rdir: Path
    social_rate: float
    #: stage uid -> (stage index, duration [h], discount factor)
    stages: dict[int, tuple[int, float, float]]
    price: SeriesMap
    cache: dict[str, SeriesMap | None] = field(default_factory=dict)
    stage_cache: dict[str, StageMap] = field(default_factory=dict)
    #: (scenario, stage, block, bus) -> PriceSplit, built on first use
    split: dict[tuple[int, int, int, int], PriceSplit] | None = None

    def series(self, stem: str) -> SeriesMap | None:
        if stem not in self.cache:
            self.cache[stem] = load_series(self.rdir, stem)
        return self.cache[stem]

    def stage_series(self, stem: str) -> StageMap:
        if stem not in self.stage_cache:
            self.stage_cache[stem] = _load_stage(self.rdir, stem)
        return self.stage_cache[stem]

    def weight(self, p: Period) -> float:
        """Probability × discount of a period (duration applied by caller)."""
        disc = self.stages.get(p.stage, (0, 0.0, 1.0))[2]
        return self.case.scenario_weight.get(p.scenario, 1.0) * disc


def _get(series: SeriesMap | None, p: Period, uid: int) -> float:
    if series is None:
        return 0.0
    return series.get((p.scenario, p.stage, p.block, uid), 0.0)


def _stage_table(planning: dict[str, Any], rate: float) -> dict[int, tuple]:
    sim = planning.get("simulation", {}) or {}
    blocks = sim.get("block_array", []) or []
    stages = sim.get("stage_array", []) or [
        {"uid": 1, "first_block": 0, "count_block": len(blocks)}
    ]
    out: dict[int, tuple[int, float, float]] = {}
    t0 = 0.0
    for idx, st in enumerate(stages):
        first = int(st.get("first_block", 0))
        count = int(st.get("count_block", len(blocks) - first))
        dur = sum(float(b.get("duration", 1.0)) for b in blocks[first : first + count])
        user = float(st.get("discount_factor", 1.0) or 1.0)
        disc = (1.0 + rate) ** (-t0 / HOURS_PER_YEAR) * user
        out[int(st.get("uid", idx + 1))] = (idx, dur, disc)
        t0 += dur
    return out


def _social_rate(planning: dict[str, Any]) -> float:
    sim = planning.get("simulation", {}) or {}
    rate = sim.get("annual_discount_rate")
    if rate is None:
        rate = (planning.get("options", {}) or {}).get("annual_discount_rate")
    return float(rate or 0.0)


# ── capacity bookkeeping (shared by every kind) ────────────────────────


def _capacity(
    ctx: _Ctx,
    cls: str,
    elem: dict[str, Any],
    dual_stems: tuple[str, ...],
    prefix: str = "",
) -> tuple[float, float, float, float, float, float]:
    """(existing, installed, capex, value/unit, cost/unit, fixed O&M) of one axis.

    ``capacost_sol`` holds the annuity plus the fixed O&M of the capacity
    built (``fixed_om_cost``); the fixed O&M part is recomputed from the
    modules built per stage and returned separately (capex excludes it).
    """
    uid = int(elem["uid"])
    capainst = ctx.stage_series(f"{cls}/capainst_sol")
    capacost = ctx.stage_series(f"{cls}/capacost_sol")
    last = max(ctx.stages, key=lambda s: ctx.stages[s][0]) if ctx.stages else 1
    expmod = ctx.stage_series(f"{cls}/expmod_sol")
    installed = 0.0
    capex = 0.0
    cost_per_unit = 0.0
    fixed_om = 0.0
    built_fom = 0.0  #: Σ built·F of the modules built so far [$/year]
    # Capacity is scene-wide (stage-level streams, scenario column 0); the
    # capex carries the scene probability, which sums to 1 here.
    for stage_uid, (s_idx, dur, disc) in sorted(
        ctx.stages.items(), key=lambda kv: kv[1][0]
    ):
        fom = _stage_value(elem.get(f"{prefix}fixed_om_cost"), s_idx) or 0.0
        expcap = _stage_value(elem.get(f"{prefix}expcap"), s_idx) or 0.0
        built_fom += _first(expmod, stage_uid, uid) * expcap * fom
        fixed_om += built_fom * dur / HOURS_PER_YEAR * disc
        capex += _first(capacost, stage_uid, uid) * dur * disc
        cost_per_unit += (
            (annual_capcost(elem, s_idx, ctx.social_rate, prefix) + fom)
            * dur
            / HOURS_PER_YEAR
            * disc
        )
        if stage_uid == last:
            installed = _first(capainst, stage_uid, uid)
    existing = _stage_value(elem.get(f"{prefix}capacity"), len(ctx.stages) - 1)
    existing = existing if existing is not None else 0.0
    if installed == 0.0 and existing > 0.0 and not capainst:
        installed = existing
    value = 0.0
    for stem in dual_stems:
        duals = ctx.series(stem)
        if duals is None:
            continue
        for periods in ctx.case.periods.values():
            for p in periods:
                value += abs(_get(duals, p, uid)) * p.duration * ctx.weight(p)
    return existing, installed, capex - fixed_om, value, cost_per_unit, fixed_om


def _first(series: StageMap, stage_uid: int, uid: int) -> float:
    """Value of a stage-level stream at (stage, uid), any scenario column."""
    for (_scen, stage, elem), value in series.items():
        if stage == stage_uid and elem == uid:
            return value
    return 0.0


def _expandable(elem: dict[str, Any], prefix: str = "") -> bool:
    expcap = elem.get(f"{prefix}expcap")
    expmod = elem.get(f"{prefix}expmod")
    capmax = elem.get(f"{prefix}capmax")
    if capmax is not None and not isinstance(capmax, bool):
        return True
    return expcap is not None and expmod is not None


# ── per kind ──────────────────────────────────────────────────────────


def _bus_of(ctx: _Ctx, ref: Any) -> int:
    buses = ctx.case.planning.get("system", {}).get("bus_array", []) or []
    uid = resolve_ref(ref, buses)
    return -1 if uid is None else uid


def _profile_factor(ctx: _Ctx, gen: dict[str, Any], p: Period) -> float:
    """Availability factor of a generator from an inline GeneratorProfile.

    ``[scenario][stage][block]``, ``[stage][block]`` or a scalar; 1 when
    the generator has no profile (a file-backed profile is read as 1, with
    a warning).
    """
    system = ctx.case.planning.get("system", {})
    # The input's generator_profile_array; gtopt's planning.json carries
    # it as capacity_profile_array (owner_kind "generator").
    profiles = [
        (prof, prof.get("generator"))
        for prof in system.get("generator_profile_array", []) or []
    ] + [
        (prof, prof.get("owner"))
        for prof in system.get("capacity_profile_array", []) or []
        if prof.get("owner_kind", "generator") == "generator"
    ]
    for prof, ref in profiles:
        if ref not in (gen.get("name"), gen.get("uid")):
            continue
        value: Any = prof.get("profile")
        for idx in (p.scenario - 1, ctx.stages[p.stage][0], p.block_index):
            if not isinstance(value, list):
                break
            if not value:
                return 1.0
            value = value[min(max(idx, 0), len(value) - 1)]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        log.warning("profile of %s is not inline: availability 1 assumed", ref)
    return 1.0


def _gen_flows(ctx: _Ctx, gen: dict[str, Any]) -> tuple[float, float, float]:
    """(revenue, opex, price-taker margin per MW) of a generator.

    The margin per MW is ``Σ max(λ − c, 0)·a·Δ`` (``a`` the profile
    factor): what one more MW would earn running whenever profitable.  For
    a unit built inside its bounds it equals the capacity dual sum.
    """
    uid = int(gen["uid"])
    bus = _bus_of(ctx, gen.get("bus"))
    gen_sol = ctx.series("Generator/generation_sol")
    srmc = ctx.series("Generator/srmc_sol")
    revenue = opex = unit_margin = 0.0
    for periods in ctx.case.periods.values():
        for p in periods:
            q = _get(gen_sol, p, uid)
            w = p.duration * ctx.weight(p)
            key = (p.scenario, p.stage, p.block, uid)
            if srmc is not None and key in srmc:
                cost = srmc[key]
            else:
                cost = (
                    sched_value(
                        gen.get("gcost"),
                        p,
                        uid,
                        class_name="Generator",
                        input_dir=ctx.case.input_dir,
                    )
                    or 0.0
                )
            lam = _get(ctx.price, p, bus)
            revenue += lam * q * w
            opex += cost * q * w
            unit_margin += max(lam - cost, 0.0) * _profile_factor(ctx, gen, p) * w
    return revenue, opex, unit_margin


def _generator(ctx: _Ctx, gen: dict[str, Any]) -> ProjectRecovery:
    revenue, opex, unit_margin = _gen_flows(ctx, gen)
    cap_rent, fail_rent = _gen_rents(ctx, gen)
    existing, installed, capex, _dual_value, cpu, fom = _capacity(
        ctx, "Generator", gen, ()
    )
    return ProjectRecovery(
        kind="Generator",
        name=str(gen.get("name", gen["uid"])),
        uid=int(gen["uid"]),
        unit="MW",
        existing=existing,
        installed=installed,
        new=max(0.0, installed - existing),
        revenue=revenue,
        opex=opex,
        capex=capex,
        value_per_unit=unit_margin,
        cost_per_unit=cpu,
        fixed_om=fom,
        capacity_rent=cap_rent,
        failure_rent=fail_rent,
    )


def _line_rent(ctx: _Ctx, line: dict[str, Any]) -> tuple[float, float, float]:
    """(congestion rent, transfer cost, price spread per MW) of a line.

    The spread ``Σ max(|λ_b − λ_a| − tcost, 0)·Δ`` is what one more MW of
    the corridor would earn; for a line built inside its bounds it equals
    the capacity dual sum.
    """
    uid = int(line["uid"])
    bus_a = _bus_of(ctx, line.get("bus_a"))
    bus_b = _bus_of(ctx, line.get("bus_b"))
    flow = ctx.series("Line/flow_sol")
    flowp = ctx.series("Line/flowp_sol")
    flown = ctx.series("Line/flown_sol")
    rent = tcost_total = spread = 0.0
    for periods in ctx.case.periods.values():
        for p in periods:
            w = p.duration * ctx.weight(p)
            dlam = _get(ctx.price, p, bus_b) - _get(ctx.price, p, bus_a)
            tc = sched_value(
                line.get("tcost"),
                p,
                uid,
                class_name="Line",
                input_dir=ctx.case.input_dir,
            )
            spread += max(abs(dlam) - (tc or 0.0), 0.0) * w
            if flow is not None:
                f = _get(flow, p, uid)
                gross = abs(f)
            else:
                fp, fn = _get(flowp, p, uid), _get(flown, p, uid)
                f, gross = fp - fn, fp + fn
            rent += dlam * f * w
            tcost_total += (tc or 0.0) * gross * w
    return rent, tcost_total, spread


def _line(ctx: _Ctx, line: dict[str, Any]) -> ProjectRecovery:
    rent, tcost, spread = _line_rent(ctx, line)
    existing, installed, capex, _dual_value, cpu, fom = _capacity(ctx, "Line", line, ())
    return ProjectRecovery(
        kind="Line",
        name=str(line.get("name", line["uid"])),
        uid=int(line["uid"]),
        unit="MW",
        existing=existing,
        installed=installed,
        new=max(0.0, installed - existing),
        revenue=rent,
        opex=tcost,
        capex=capex,
        value_per_unit=spread,
        cost_per_unit=cpu,
        fixed_om=fom,
    )


def _by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for it in items:
        if it.get("name") == name:
            return it
    return None


def _battery_flows(ctx: _Ctx, bat: dict[str, Any]) -> tuple[float, float]:
    """(arbitrage revenue, discharge + charge cost) of a battery."""
    system = ctx.case.planning.get("system", {})
    name = str(bat.get("name"))
    gen = _by_name(system.get("generator_array", []) or [], f"{name}_gen")
    dem = _by_name(system.get("demand_array", []) or [], f"{name}_dem")
    revenue = opex = 0.0
    if gen is not None:
        revenue, opex, _ = _gen_flows(ctx, gen)
    if dem is not None:
        duid = int(dem["uid"])
        bus = _bus_of(ctx, dem.get("bus"))
        load = ctx.series("Demand/load_sol")
        for periods in ctx.case.periods.values():
            for p in periods:
                q = _get(load, p, duid)
                if q == 0.0:
                    continue
                w = p.duration * ctx.weight(p)
                revenue -= _get(ctx.price, p, bus) * q * w
                cc = sched_value(
                    bat.get("charge_cost"),
                    p,
                    int(bat["uid"]),
                    class_name="Battery",
                    input_dir=ctx.case.input_dir,
                )
                opex += (cc or 0.0) * q * w
    return revenue, opex


def _battery(ctx: _Ctx, bat: dict[str, Any]) -> ProjectRecovery:
    revenue, opex = _battery_flows(ctx, bat)
    existing, installed, capex, value, cpu, fom = _capacity(
        ctx, "Battery", bat, ("Battery/capacity_dual",)
    )
    rec = ProjectRecovery(
        kind="Battery",
        name=str(bat.get("name", bat["uid"])),
        uid=int(bat["uid"]),
        unit="MWh",
        existing=existing,
        installed=installed,
        new=max(0.0, installed - existing),
        revenue=revenue,
        opex=opex,
        capex=capex,
        value_per_unit=value,
        cost_per_unit=cpu,
        fixed_om=fom,
    )
    system = ctx.case.planning.get("system", {})
    conv = _by_name(system.get("converter_array", []) or [], f"{rec.name}_conv")
    if conv is not None and _expandable(conv):
        # The converter carries the power axis; its annuity inputs are the
        # battery's (power_* cost, shared wacc / economic_life).
        conv_view = dict(conv)
        for key in ("wacc", "economic_life"):
            conv_view.setdefault(key, bat.get(key))
        conv_view.setdefault("overnight_capcost", bat.get("power_overnight_capcost"))
        conv_view.setdefault("fixed_om_cost", bat.get("power_fixed_om_cost"))
        p_exist, p_inst, p_capex, p_value, p_cpu, p_fom = _capacity(
            ctx, "Converter", conv_view, ("Converter/capacity_dual",)
        )
        rec.power_new = max(0.0, p_inst - p_exist)
        rec.power_capex = p_capex
        rec.power_value_per_unit = p_value
        rec.power_cost_per_unit = p_cpu
        rec.fixed_om += p_fom
    return rec


# ── price decomposition (peak-load pricing) ──────────────────────────


@dataclass
class PriceSplit:
    """One bus price split as ``λ = set_cost + capacity_rent + failure_rent``.

    ``set_cost`` is the variable cost of the costliest generator running
    in the bus's price zone (buses with the same λ), or λ itself when a
    flexible demand, a converter or a storage unit operating strictly
    inside its limits sets the price.  When every running unit is at its
    capacity the remainder ``λ − set_cost`` is the capacity rent of the
    peak block (Boiteux / Steiner peak-load pricing: the capacity dual of
    the scarce units divided by the block hours).  In a block with
    unserved energy the remainder is the failure component instead.
    """

    price: float
    set_cost: float
    capacity_rent: float = 0.0
    failure_rent: float = 0.0
    setter: str = ""


_EPS = 1e-6


def _interior(q: float, cap: float | None) -> bool:
    """``0 < q < cap`` with a relative tolerance (``cap`` None: no bound)."""
    tol = _EPS * max(1.0, abs(cap or 0.0))
    if q <= tol:
        return False
    return cap is None or q < cap - tol


def _installed(ctx: _Ctx, cls: str, elem: dict[str, Any], p: Period) -> float | None:
    """Installed capacity of an element at a period's stage (None: unbounded)."""
    uid = int(elem["uid"])
    capainst = ctx.stage_series(f"{cls}/capainst_sol")
    if any(e == uid and st == p.stage for (_s, st, e) in capainst):
        return _first(capainst, p.stage, uid)
    cap = sched_value(
        elem.get("capacity"), p, uid, class_name=cls, input_dir=ctx.case.input_dir
    )
    pmax = sched_value(
        elem.get("pmax"), p, uid, class_name=cls, input_dir=ctx.case.input_dir
    )
    vals = [v for v in (cap, pmax) if v is not None]
    return min(vals) if vals else None


def _gen_cost(ctx: _Ctx, gen: dict[str, Any], p: Period) -> float:
    uid = int(gen["uid"])
    srmc = ctx.series("Generator/srmc_sol")
    key = (p.scenario, p.stage, p.block, uid)
    if srmc is not None and key in srmc:
        return srmc[key]
    return (
        sched_value(
            gen.get("gcost"),
            p,
            uid,
            class_name="Generator",
            input_dir=ctx.case.input_dir,
        )
        or 0.0
    )


def _zone_of(prices: dict[int, float], bus: int) -> list[int]:
    lam = prices.get(bus, 0.0)
    tol = _EPS * max(1.0, abs(lam))
    return [b for b, v in prices.items() if abs(v - lam) <= tol]


def _price_split(ctx: _Ctx) -> dict[tuple[int, int, int, int], PriceSplit]:
    """λ = set_cost + capacity rent (+ failure) per (period, bus)."""
    if ctx.split is not None:
        return ctx.split
    system = ctx.case.planning.get("system", {})
    bat_names = {str(b.get("name")) for b in system.get("battery_array", []) or []}
    gens = system.get("generator_array", []) or []
    dems = system.get("demand_array", []) or []
    convs = system.get("converter_array", []) or []
    ccs = system.get("carrier_converter_array", []) or []
    gen_sol = ctx.series("Generator/generation_sol")
    load = ctx.series("Demand/load_sol")
    fail = ctx.series("Demand/fail_sol")
    x = ctx.series("CarrierConverter/input_sol")
    out: dict[tuple[int, int, int, int], PriceSplit] = {}
    for periods in ctx.case.periods.values():
        for p in periods:
            prices = {
                b: v
                for (sc, st, bl, b), v in ctx.price.items()
                if (sc, st, bl) == (p.scenario, p.stage, p.block)
            }
            # per bus: costliest running generator, flexible setter, failure
            run_cost: dict[int, float] = {}
            flexible: dict[int, str] = {}
            failing: set[int] = set()
            for gen in gens:
                name = str(gen.get("name"))
                bus = _bus_of(ctx, gen.get("bus"))
                q = _get(gen_sol, p, int(gen["uid"]))
                if name.endswith("_gen") and name[: -len("_gen")] in bat_names:
                    conv = _by_name(convs, f"{name[: -len('_gen')]}_conv")
                    cap = (
                        None if conv is None else _installed(ctx, "Converter", conv, p)
                    )
                    if _interior(q, cap):
                        flexible.setdefault(bus, f"storage {name[: -len('_gen')]}")
                    continue
                if q <= _EPS:
                    continue
                cost = _gen_cost(ctx, gen, p)
                run_cost[bus] = max(run_cost.get(bus, -math.inf), cost)
            for dem in dems:
                name = str(dem.get("name"))
                duid = int(dem["uid"])
                bus = _bus_of(ctx, dem.get("bus"))
                q = _get(load, p, duid)
                if name.endswith("_dem") and name[: -len("_dem")] in bat_names:
                    conv = _by_name(convs, f"{name[: -len('_dem')]}_conv")
                    cap = (
                        None if conv is None else _installed(ctx, "Converter", conv, p)
                    )
                    if _interior(q, cap):
                        flexible.setdefault(bus, f"storage {name[: -len('_dem')]}")
                    continue
                if _expandable(dem):
                    if _interior(q, _installed(ctx, "Demand", dem, p)):
                        flexible.setdefault(bus, f"demand {name}")
                    continue
                # Unserved energy, or a price at the failure cost itself
                # (a degenerate dual: capacity exactly equal to load, the
                # solver may return any λ up to the failure cost).
                if _get(fail, p, duid) > _EPS or prices.get(bus, 0.0) >= (
                    _demand_value(ctx, dem, p) * (1.0 - _EPS)
                ):
                    failing.add(bus)
            for cc in ccs:
                q = _get(x, p, int(cc["uid"]))
                if str(cc.get("from_carrier", "electric")) != "electric":
                    continue
                if _interior(q, _installed(ctx, "CarrierConverter", cc, p)):
                    _, n_in = _node_price(ctx, "electric", cc.get("from_node"))
                    flexible.setdefault(n_in, f"converter {cc.get('name')}")
            for bus, lam in prices.items():
                zone = _zone_of(prices, bus)
                setter = next((flexible[b] for b in zone if b in flexible), "")
                costs = [run_cost[b] for b in zone if b in run_cost]
                fails = any(b in failing for b in zone)
                key = (p.scenario, p.stage, p.block, bus)
                if setter and not fails:
                    out[key] = PriceSplit(lam, lam, setter=setter)
                    continue
                top = max(costs) if costs else lam
                rest = max(0.0, lam - top)
                out[key] = PriceSplit(
                    lam,
                    min(top, lam),
                    capacity_rent=0.0 if fails else rest,
                    failure_rent=rest if fails else 0.0,
                    setter="unserved energy" if fails else "",
                )
    ctx.split = out
    return out


def _gen_rents(ctx: _Ctx, gen: dict[str, Any]) -> tuple[float, float]:
    """(capacity-rent, failure-rent) parts of a generator's margin."""
    uid = int(gen["uid"])
    bus = _bus_of(ctx, gen.get("bus"))
    gen_sol = ctx.series("Generator/generation_sol")
    split = _price_split(ctx)
    cap_rent = fail_rent = 0.0
    for periods in ctx.case.periods.values():
        for p in periods:
            q = _get(gen_sol, p, uid)
            ps = split.get((p.scenario, p.stage, p.block, bus))
            if q <= _EPS or ps is None:
                continue
            w = p.duration * ctx.weight(p)
            spread = max(0.0, ps.price - _gen_cost(ctx, gen, p))
            cap_rent += min(ps.capacity_rent, spread) * q * w
            fail_rent += min(ps.failure_rent, spread) * q * w
    return cap_rent, fail_rent


def price_blocks_rows(ctx: _Ctx) -> list[dict[str, Any]]:
    """Per (period, bus) price decomposition rows (for tables / JSON)."""
    rows = []
    for (sc, st, bl, bus), ps in sorted(_price_split(ctx).items()):
        rows.append(
            {
                "scenario": sc,
                "stage": st,
                "block": bl,
                "bus": bus,
                **asdict(ps),
            }
        )
    return rows


# ── market balance ─────────────────────────────────────────────────────


# ── demand-side projects ─────────────────────────────────────────────

#: carrier name -> (node array, node balance-dual stem)
_CARRIER_NODES = {
    "electric": ("bus_array", "Bus/balance_dual"),
    "hydrogen": ("hydrogen_node_array", "HydrogenNode/balance_dual"),
    "thermal": ("thermal_node_array", "ThermalNode/balance_dual"),
    "ammonia": ("ammonia_node_array", "AmmoniaNode/balance_dual"),
}


def _demand_value(ctx: _Ctx, dem: dict[str, Any], p: Period) -> float:
    """Value of served load [$/MWh]: its own ``fcost`` or the global one."""
    v = sched_value(
        dem.get("fcost"),
        p,
        int(dem["uid"]),
        class_name="Demand",
        input_dir=ctx.case.input_dir,
    )
    if v is not None:
        return v
    opts = ctx.case.planning.get("options", {}) or {}
    glob = (opts.get("model_options", {}) or {}).get("demand_fail_cost")
    glob = opts.get("demand_fail_cost", glob)
    return float(glob) if glob is not None else DEFAULT_DEMAND_FAIL_COST


def _demand(ctx: _Ctx, dem: dict[str, Any]) -> ProjectRecovery:
    """An expandable demand (e.g. a desalination plant) as a project.

    Revenue is the value of what it serves (``fcost`` = social benefit per
    MWh consumed), opex its electricity bill at the bus price; the margin
    per MW is ``Σ max(v − λ, 0)·Δ``.
    """
    duid = int(dem["uid"])
    bus = _bus_of(ctx, dem.get("bus"))
    load = ctx.series("Demand/load_sol")
    revenue = opex = unit_margin = 0.0
    for periods in ctx.case.periods.values():
        for p in periods:
            w = p.duration * ctx.weight(p)
            value = _demand_value(ctx, dem, p)
            lam = _get(ctx.price, p, bus)
            q = _get(load, p, duid)
            revenue += value * q * w
            opex += lam * q * w
            unit_margin += max(value - lam, 0.0) * w
    existing, installed, capex, _dual_value, cpu, fom = _capacity(
        ctx, "Demand", dem, ()
    )
    return ProjectRecovery(
        kind="Demand",
        name=str(dem.get("name", dem["uid"])),
        uid=duid,
        unit="MW",
        existing=existing,
        installed=installed,
        new=max(0.0, installed - existing),
        revenue=revenue,
        opex=opex,
        capex=capex,
        value_per_unit=unit_margin,
        cost_per_unit=cpu,
        fixed_om=fom,
    )


def _node_price(ctx: _Ctx, carrier: str, ref: Any) -> tuple[SeriesMap | None, int]:
    array, stem = _CARRIER_NODES.get(carrier, ("", ""))
    if not array:
        log.warning("carrier %r has no node prices: price 0 assumed", carrier)
        return None, -1
    nodes = ctx.case.planning.get("system", {}).get(array, []) or []
    uid = resolve_ref(ref, nodes)
    series = ctx.price if carrier == "electric" else ctx.series(stem)
    return series, -1 if uid is None else uid


def _converter_flows(
    ctx: _Ctx, cc: dict[str, Any]
) -> tuple[float, float, float, float]:
    """(output revenue, input + ocost cost, margin per MW, electric net).

    Output is valued at the destination-node price, input at the source
    node price; ``electric net`` is the converter's electricity sales
    minus purchases (for the market balance).
    """
    uid = int(cc["uid"])
    src = str(cc.get("from_carrier", "electric"))
    dst = str(cc.get("to_carrier", "electric"))
    p_in, n_in = _node_price(ctx, src, cc.get("from_node"))
    p_out, n_out = _node_price(ctx, dst, cc.get("to_node"))
    x = ctx.series("CarrierConverter/input_sol")
    revenue = cost = unit_margin = elec = 0.0
    for periods in ctx.case.periods.values():
        for p in periods:
            w = p.duration * ctx.weight(p)
            eta = sched_value(
                cc.get("efficiency"),
                p,
                uid,
                class_name="CarrierConverter",
                input_dir=ctx.case.input_dir,
            )
            eta = 1.0 if eta is None else eta
            oc = (
                sched_value(
                    cc.get("ocost"),
                    p,
                    uid,
                    class_name="CarrierConverter",
                    input_dir=ctx.case.input_dir,
                )
                or 0.0
            )
            lam_in = _get(p_in, p, n_in)
            lam_out = _get(p_out, p, n_out)
            q = _get(x, p, uid)
            revenue += lam_out * eta * q * w
            cost += (lam_in + oc) * q * w
            unit_margin += max(lam_out * eta - lam_in - oc, 0.0) * w
            if src == "electric":
                elec -= lam_in * q * w
            if dst == "electric":
                elec += lam_out * eta * q * w
    return revenue, cost, unit_margin, elec


def _carrier_converter(ctx: _Ctx, cc: dict[str, Any]) -> ProjectRecovery:
    revenue, cost, unit_margin, _ = _converter_flows(ctx, cc)
    existing, installed, capex, _dual_value, cpu, fom = _capacity(
        ctx, "CarrierConverter", cc, ()
    )
    return ProjectRecovery(
        kind="CarrierConverter",
        name=str(cc.get("name", cc["uid"])),
        uid=int(cc["uid"]),
        unit="MW",
        existing=existing,
        installed=installed,
        new=max(0.0, installed - existing),
        revenue=revenue,
        opex=cost,
        capex=capex,
        value_per_unit=unit_margin,
        cost_per_unit=cpu,
        fixed_om=fom,
    )


def _market(ctx: _Ctx) -> MarketBalance:
    system = ctx.case.planning.get("system", {})
    bat_names = {str(b.get("name")) for b in system.get("battery_array", []) or []}
    bal = MarketBalance()
    load = ctx.series("Demand/load_sol")
    fail = ctx.series("Demand/fail_sol")
    for dem in system.get("demand_array", []) or []:
        name = str(dem.get("name"))
        if name.endswith("_dem") and name[: -len("_dem")] in bat_names:
            continue
        duid = int(dem["uid"])
        bus = _bus_of(ctx, dem.get("bus"))
        for periods in ctx.case.periods.values():
            for p in periods:
                w = p.duration * ctx.weight(p)
                bal.load_payments += _get(ctx.price, p, bus) * _get(load, p, duid) * w
                bal.unserved_mwh += _get(fail, p, duid) * w
    for gen in system.get("generator_array", []) or []:
        name = str(gen.get("name"))
        if name.endswith("_gen") and name[: -len("_gen")] in bat_names:
            continue
        bal.generator_revenue += _gen_flows(ctx, gen)[0]
    for bat in system.get("battery_array", []) or []:
        bal.battery_net_revenue += _battery_flows(ctx, bat)[0]
    for line in system.get("line_array", []) or []:
        bal.congestion_rent += _line_rent(ctx, line)[0]
    for cc in system.get("carrier_converter_array", []) or []:
        bal.converter_net_revenue += _converter_flows(ctx, cc)[3]
    return bal


def _scaled(p: ProjectRecovery, k: float) -> ProjectRecovery:
    for attr in (
        "revenue",
        "opex",
        "fixed_om",
        "capex",
        "value_per_unit",
        "cost_per_unit",
        "power_capex",
        "power_value_per_unit",
        "power_cost_per_unit",
        "capacity_rent",
        "failure_rent",
    ):
        v = getattr(p, attr)
        if v is not None:
            setattr(p, attr, v * k)
    return p


def _read_obj(rdir: Path) -> float | None:
    path = rdir / "solution.csv"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    try:
        return sum(float(r["obj_value"]) for r in rows)
    except (KeyError, ValueError):
        return None


def compute_recovery(
    results_dir: str | Path,
    *,
    planning: str | Path | None = None,
    case_dir: str | Path | None = None,
    per_year: bool = False,
    include_existing: bool = False,
    price_blocks: bool = False,
) -> RecoveryReport:
    """Cost-recovery report of one solved case.

    ``planning`` defaults to ``<results_dir>/planning.json`` (written by
    every gtopt run, batteries already expanded into ``<bat>_gen`` /
    ``<bat>_dem`` / ``<bat>_conv``).
    """
    rdir = Path(results_dir)
    ppath = Path(planning) if planning is not None else rdir / "planning.json"
    if not ppath.is_file():
        raise FileNotFoundError(f"planning file not found: {ppath}")
    case = load_case(ppath, Path(case_dir) if case_dir is not None else None)
    price = load_series(rdir, "Bus/balance_dual")
    if price is None:
        raise FileNotFoundError(
            f"{rdir}: Bus/balance_dual is required (run gtopt with --write-out all)"
        )
    rate = _social_rate(case.planning)
    ctx = _Ctx(
        case=case,
        rdir=rdir,
        social_rate=rate,
        stages=_stage_table(case.planning, rate),
        price=price,
    )
    horizon = sum(d for _, d, _ in ctx.stages.values())
    scale = HOURS_PER_YEAR / horizon if per_year and horizon > 0 else 1.0
    mode = (
        case.planning.get("options", {}).get("model_options", {}).get("pricing_mode")
        or "fixed"
    )
    rows = read_pricing_csv(rdir)
    report = RecoveryReport(
        results_dir=str(rdir),
        horizon_hours=horizon,
        scale=scale,
        obj_value=_read_obj(rdir),
        pricing_mode=str(mode),
        applied_modes=sorted({r.get("applied_mode", "") for r in rows} - {""}),
    )
    system = case.planning.get("system", {})
    bat_names = {str(b.get("name")) for b in system.get("battery_array", []) or []}
    for gen in system.get("generator_array", []) or []:
        name = str(gen.get("name"))
        if name.endswith("_gen") and name[: -len("_gen")] in bat_names:
            continue
        if _expandable(gen):
            report.projects.append(_scaled(_generator(ctx, gen), scale))
        elif include_existing:
            report.existing.append(_scaled(_generator(ctx, gen), scale))
    for line in system.get("line_array", []) or []:
        if _expandable(line):
            report.projects.append(_scaled(_line(ctx, line), scale))
    for bat in system.get("battery_array", []) or []:
        conv = _by_name(
            system.get("converter_array", []) or [], f"{bat.get('name')}_conv"
        )
        if _expandable(bat) or (conv is not None and _expandable(conv)):
            report.projects.append(_scaled(_battery(ctx, bat), scale))
    for dem in system.get("demand_array", []) or []:
        name = str(dem.get("name"))
        if name.endswith("_dem") and name[: -len("_dem")] in bat_names:
            continue
        if _expandable(dem):
            report.projects.append(_scaled(_demand(ctx, dem), scale))
    for cc in system.get("carrier_converter_array", []) or []:
        if _expandable(cc):
            report.projects.append(_scaled(_carrier_converter(ctx, cc), scale))
    bal = _market(ctx)
    for attr in (
        "load_payments",
        "generator_revenue",
        "battery_net_revenue",
        "congestion_rent",
        "converter_net_revenue",
        "unserved_mwh",
    ):
        setattr(bal, attr, getattr(bal, attr) * scale)
    report.market = bal
    if price_blocks:
        report.blocks = price_blocks_rows(ctx)
    if report.total_failure_rent > 1e-6 * max(1.0, report.total_capex):
        log.warning(
            "%s: projects earn %.0f at failure prices (unserved energy, or a "
            "price at the failure cost); "
            "add candidate capacity so the plan never sheds load",
            rdir,
            report.total_failure_rent,
        )
    if not math.isfinite(report.total_profit):
        log.warning("non-finite profit in %s", rdir)
    return report
