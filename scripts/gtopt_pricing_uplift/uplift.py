# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Uplift (make-whole + lost-opportunity cost) and price statistics."""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._data import (
    Case,
    Period,
    SeriesMap,
    load_case,
    load_series,
    read_pricing_csv,
    resolve_ref,
    sched_value,
)
from ._selfsched import SelfScheduleUnit, self_schedule_profit

log = logging.getLogger(__name__)

#: Price threshold of the "deeply negative" node-hour count [$/MWh].
NEG_PRICE_THRESHOLD = -0.1


@dataclass
class UnitUplift:
    """Uplift of one committable unit (probability-weighted over scenarios)."""

    commitment: str
    generator: str
    bus: str
    energy_mwh: float = 0.0
    energy_margin: float = 0.0  #: Σ (λ - c)·g·Δt
    commitment_cost: float = 0.0  #: no-load + start-up + shut-down
    profit: float = 0.0  #: energy_margin - commitment_cost
    make_whole: float = 0.0
    self_schedule_profit: float = 0.0
    lost_opportunity: float = 0.0

    @property
    def total(self) -> float:
        return self.make_whole + self.lost_opportunity


@dataclass
class PriceStats:
    """Nodal price statistics (probability-weighted hours)."""

    node_hours: float = 0.0
    negative_node_hours: float = 0.0  #: λ < 0
    deep_negative_node_hours: float = 0.0  #: λ < NEG_PRICE_THRESHOLD
    ref_bus: str = ""
    ref_mean: float | None = None  #: duration-weighted
    ref_min: float | None = None
    ref_max: float | None = None
    ref_std: float | None = None  #: duration-weighted standard deviation
    ref_mean_abs_change: float | None = None  #: mean |λ_t - λ_{t-1}|
    congestion_rent: float | None = None  #: Σ (λ_b - λ_a)·f·Δt


@dataclass
class UpliftReport:
    """Uplift metrics of one solved case."""

    results_dir: str
    pricing_mode: str
    applied_modes: list[str] = field(default_factory=list)
    pricing_fallbacks: int = 0
    units: list[UnitUplift] = field(default_factory=list)
    prices: PriceStats = field(default_factory=PriceStats)

    @property
    def make_whole(self) -> float:
        return sum(u.make_whole for u in self.units)

    @property
    def lost_opportunity(self) -> float:
        return sum(u.lost_opportunity for u in self.units)

    @property
    def total(self) -> float:
        return self.make_whole + self.lost_opportunity

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly view, totals included."""
        units = []
        for u in self.units:
            d = asdict(u)
            d["total"] = u.total
            units.append(d)
        return {
            "results_dir": self.results_dir,
            "pricing_mode": self.pricing_mode,
            "applied_modes": self.applied_modes,
            "pricing_fallbacks": self.pricing_fallbacks,
            "make_whole": self.make_whole,
            "lost_opportunity": self.lost_opportunity,
            "total_uplift": self.total,
            "prices": asdict(self.prices),
            "units": units,
        }


def _get(series: SeriesMap | None, p: Period, uid: int) -> float:
    if series is None:
        return 0.0
    return series.get((p.scenario, p.stage, p.block, uid), 0.0)


@dataclass
class _Outputs:
    gen: SeriesMap
    price: SeriesMap
    status: SeriesMap | None
    startup: SeriesMap | None
    shutdown: SeriesMap | None
    srmc: SeriesMap | None
    flow: SeriesMap | None


def _load_outputs(results_dir: Path) -> _Outputs:
    gen = load_series(results_dir, "Generator/generation_sol")
    price = load_series(results_dir, "Bus/balance_dual")
    if gen is None or price is None:
        raise FileNotFoundError(
            f"{results_dir}: Generator/generation_sol and Bus/balance_dual are "
            "required (run gtopt with duals in --write-out)"
        )
    return _Outputs(
        gen=gen,
        price=price,
        status=load_series(results_dir, "Commitment/status_sol"),
        startup=load_series(results_dir, "Commitment/startup_sol"),
        shutdown=load_series(results_dir, "Commitment/shutdown_sol"),
        srmc=load_series(results_dir, "Generator/srmc_sol"),
        flow=load_series(results_dir, "Line/flow_sol"),
    )


def _unit_cost(
    case: Case, out: _Outputs, gen: dict[str, Any], gen_uid: int, p: Period
) -> float:
    """Marginal energy cost c_{g,t}: srmc_sol, else the gcost schedule."""
    key = (p.scenario, p.stage, p.block, gen_uid)
    if out.srmc is not None and key in out.srmc:
        return out.srmc[key]
    cost = sched_value(
        gen.get("gcost"),
        p,
        gen_uid,
        class_name="Generator",
        input_dir=case.input_dir,
    )
    return cost if cost is not None else 0.0


def _sched(case: Case, value: Any, p: Period, uid: int, cls: str) -> float | None:
    return sched_value(value, p, uid, class_name=cls, input_dir=case.input_dir)


def _unit_uplift(
    case: Case,
    out: _Outputs,
    cmt: dict[str, Any],
    gen: dict[str, Any],
) -> UnitUplift:
    system = case.planning.get("system", {})
    cuid = int(cmt["uid"])
    guid = int(gen["uid"])
    bus_uid = resolve_ref(gen.get("bus"), system.get("bus_array", []) or [])
    if bus_uid is None:
        bus_uid = -1
    res = UnitUplift(
        commitment=str(cmt.get("name", cuid)),
        generator=str(gen.get("name", guid)),
        bus=case.bus_name.get(bus_uid, str(bus_uid)),
    )
    noload = float(cmt.get("noload_cost") or 0.0)
    gen_pmax_default = gen.get("pmax", gen.get("capacity"))

    for scen, periods in case.periods.items():
        weight = case.scenario_weight.get(scen, 1.0)
        su_unit = SelfScheduleUnit(
            price=[],
            cost=[],
            duration=[],
            pmin=[],
            pmax=[],
            noload_cost=noload,
            min_up=float(cmt.get("min_up_time") or 0.0),
            min_down=float(cmt.get("min_down_time") or 0.0),
            initial_on=float(cmt.get("initial_status") or 0.0) >= 0.5,
            initial_hours=(
                float(cmt["initial_hours"])
                if cmt.get("initial_hours") is not None
                else None
            ),
            must_run=bool(cmt.get("must_run", False)),
        )
        energy = margin = ccost = 0.0
        prev_u = 1.0 if su_unit.initial_on else 0.0
        for p in periods:
            lam = _get(out.price, p, bus_uid)
            c = _unit_cost(case, out, gen, guid, p)
            g = _get(out.gen, p, guid)
            u = _get(out.status, p, cuid)
            su_cost = _sched(case, cmt.get("startup_cost"), p, cuid, "Commitment")
            if su_cost is None:
                su_cost = float(cmt.get("cold_start_cost") or 0.0)
            sd_cost = _sched(case, cmt.get("shutdown_cost"), p, cuid, "Commitment")
            sd_cost = sd_cost if sd_cost is not None else 0.0
            if out.startup is not None:
                v = _get(out.startup, p, cuid)
            else:
                v = max(0.0, u - prev_u)
            if out.shutdown is not None:
                w = _get(out.shutdown, p, cuid)
            else:
                w = max(0.0, prev_u - u)
            prev_u = u

            energy += g * p.duration
            margin += (lam - c) * g * p.duration
            ccost += noload * u * p.duration + su_cost * v + sd_cost * w

            pmin = _sched(case, cmt.get("pmin"), p, cuid, "Commitment")
            if pmin is None:
                pmin = _sched(case, gen.get("pmin"), p, guid, "Generator") or 0.0
            pmax = _sched(case, gen_pmax_default, p, guid, "Generator")
            su_unit.price.append(lam)
            su_unit.cost.append(c)
            su_unit.duration.append(p.duration)
            su_unit.pmin.append(pmin)
            su_unit.pmax.append(pmax if pmax is not None else pmin)
            su_unit.startup_cost.append(su_cost)
            su_unit.shutdown_cost.append(sd_cost)

        profit = margin - ccost
        mwp = max(0.0, -profit)
        best = self_schedule_profit(su_unit)
        loc = max(0.0, best - (profit + mwp))
        res.energy_mwh += weight * energy
        res.energy_margin += weight * margin
        res.commitment_cost += weight * ccost
        res.profit += weight * profit
        res.make_whole += weight * mwp
        res.self_schedule_profit += weight * best
        res.lost_opportunity += weight * loc
    return res


def _weighted_std(values: list[float], weights: list[float], mean: float) -> float:
    """Weighted standard deviation (``sum(weights) > 0`` by the caller)."""
    var = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / sum(weights)
    return math.sqrt(var)


def _price_stats(case: Case, out: _Outputs, ref_bus: str | int | None) -> PriceStats:
    system = case.planning.get("system", {})
    buses = system.get("bus_array", []) or []
    stats = PriceStats()
    if ref_bus is None:
        ref_uid = int(buses[0]["uid"]) if buses else None
    else:
        ref_uid = resolve_ref(ref_bus, buses)
    if ref_uid is not None:
        stats.ref_bus = case.bus_name.get(ref_uid, str(ref_uid))

    bus_uids = [int(b["uid"]) for b in buses]
    ref_vals: list[float] = []
    ref_wts: list[float] = []
    abs_changes: list[float] = []
    for scen, periods in case.periods.items():
        weight = case.scenario_weight.get(scen, 1.0)
        prev: float | None = None
        for p in periods:
            hours = weight * p.duration
            for b in bus_uids:
                lam = _get(out.price, p, b)
                stats.node_hours += hours
                if lam < 0.0:
                    stats.negative_node_hours += hours
                if lam < NEG_PRICE_THRESHOLD:
                    stats.deep_negative_node_hours += hours
            if ref_uid is not None:
                lam_ref = _get(out.price, p, ref_uid)
                ref_vals.append(lam_ref)
                ref_wts.append(hours)
                if prev is not None:
                    abs_changes.append(abs(lam_ref - prev))
                prev = lam_ref

    if ref_vals and sum(ref_wts) > 0.0:
        mean = sum(v * w for v, w in zip(ref_vals, ref_wts)) / sum(ref_wts)
        stats.ref_mean = mean
        stats.ref_min = min(ref_vals)
        stats.ref_max = max(ref_vals)
        stats.ref_std = _weighted_std(ref_vals, ref_wts, mean)
        stats.ref_mean_abs_change = (
            sum(abs_changes) / len(abs_changes) if abs_changes else 0.0
        )

    if out.flow is not None:
        rent = 0.0
        for line in system.get("line_array", []) or []:
            luid = int(line["uid"])
            from_bus = resolve_ref(line.get("bus_a"), buses)
            to_bus = resolve_ref(line.get("bus_b"), buses)
            if from_bus is None or to_bus is None:
                continue
            for scen, periods in case.periods.items():
                weight = case.scenario_weight.get(scen, 1.0)
                for p in periods:
                    f = _get(out.flow, p, luid)
                    dlam = _get(out.price, p, to_bus) - _get(out.price, p, from_bus)
                    rent += weight * dlam * f * p.duration
        stats.congestion_rent = rent
    return stats


def compute_uplift(
    results_dir: str | Path,
    *,
    planning: str | Path | None = None,
    case_dir: str | Path | None = None,
    ref_bus: str | int | None = None,
) -> UpliftReport:
    """Compute the uplift report of one solved case.

    ``planning`` defaults to ``<results_dir>/planning.json`` (written by
    gtopt with every run); ``case_dir`` anchors a relative
    ``input_directory`` for file-scheduled fields (default: the planning
    file's directory).
    """
    rdir = Path(results_dir)
    ppath = Path(planning) if planning is not None else rdir / "planning.json"
    if not ppath.is_file():
        raise FileNotFoundError(f"planning file not found: {ppath}")
    case = load_case(ppath, Path(case_dir) if case_dir is not None else None)
    out = _load_outputs(rdir)

    system = case.planning.get("system", {})
    gens = system.get("generator_array", []) or []
    gen_by_uid = {int(g["uid"]): g for g in gens}
    mode = (
        case.planning.get("options", {}).get("model_options", {}).get("pricing_mode")
        or "fixed"
    )
    rows = read_pricing_csv(rdir)
    report = UpliftReport(
        results_dir=str(rdir),
        pricing_mode=str(mode),
        applied_modes=sorted({r.get("applied_mode", "") for r in rows}),
        pricing_fallbacks=sum(1 for r in rows if r.get("fallback") == "1"),
    )
    for cmt in system.get("commitment_array", []) or []:
        if cmt.get("relax"):
            continue  # an LP (continuous) commitment needs no uplift
        guid = resolve_ref(cmt.get("generator"), gens)
        if guid is None or guid not in gen_by_uid:
            log.warning("commitment %s: generator not found", cmt.get("name"))
            continue
        report.units.append(_unit_uplift(case, out, cmt, gen_by_uid[guid]))
    report.prices = _price_stats(case, out, ref_bus)
    return report
