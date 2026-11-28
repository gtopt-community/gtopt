# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Evaluate every project of a solved plan under one market model.

Per project: resolve technology, overnight capex, life and pre-tax
private WACC; ask the market model for its revenue / charge components;
add the market-independent costs (variable cost as modelled in gtopt,
extra VOM, fixed O&M = % of capex); run the pre-tax cash flow (EBITDA,
depreciation, EBIT, NPV, IRR, payback, LCOE); compare with the planning
view (gtopt's annuity and margin from ``gtopt_cost_recovery``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .capex_trajectory import capex_factor
from .case_data import ProjectData, SystemData
from .emissions import co2_tonnes, marginal_emission_factors, read_mef_csv
from .finance import CashFlowResult, build_cash_flows, crf
from .storage_value import notes as storage_notes, valuation_modes
from .fuel_trajectory import (
    IEA_WEO,
    DeclaredCost,
    declared_cost,
    fuel_of,
    fuel_ratio,
    index_table,
    scenarios,
    trajectory_config,
)
from .markets import Component, Components, EvalContext, MarketModel, get_market
from .params import Params
from .risk import RiskResult, measure, risk_config
from .streams import StreamResult, evaluate_streams, stream_config


@dataclass
class ProjectEvaluation:
    """Private (pre-tax, EBIT) evaluation of one project + planning view."""

    kind: str
    name: str
    technology: str
    new_mw: float
    new_mwh: float
    capex: float
    life: int
    wacc: float
    cash: CashFlowResult
    energy_mwh: float
    captured_price: float | None
    planning_margin: float  #: LP energy margin of the new capacity [$/yr]
    planning_annuity: float  #: LP annualised capex [$/yr]
    planning_rate: float
    co2_avoided_t: float = 0.0
    co2_own_t: float = 0.0
    sensitivity: dict[str, dict[str, float]] = field(default_factory=dict)
    #: stream-specific discounting (``finance.stream_rates``; None = off)
    streams: StreamResult | None = None
    #: risk across scenarios (``risk``; None = off)
    risk: RiskResult | None = None
    #: an existing unit evaluated as a whole (``--existing``)
    existing: bool = False
    #: physical quantities behind the revenues (market ``details``)
    details: dict[str, float] = field(default_factory=dict)

    @property
    def planning_profit(self) -> float:
        return self.planning_margin - self.planning_annuity

    @property
    def private_annuity(self) -> float:
        return self.capex * crf(self.wacc, self.life)

    @property
    def year1(self) -> Any:
        return self.cash.years[0]

    def bridge(self) -> dict[str, float]:
        """Year-1 bridge from the planning profit to the private surplus.

        ``planning profit + Σ items = EBITDA₁ − capex·CRF(wacc, life)``.
        """
        y1 = self.year1
        out: dict[str, float] = {}
        energy_rev = y1.revenues.get("energy", 0.0) + y1.revenues.get(
            "congestion_rent", 0.0
        )
        label = (
            "energy reconciliation"
            if self.kind != "Line" or "congestion_rent" in y1.revenues
            else "- congestion rent (to users, regulated line)"
        )
        out[label] = energy_rev - y1.costs.get("variable", 0.0) - self.planning_margin
        for k, v in y1.revenues.items():
            if k not in ("energy", "congestion_rent"):
                out[f"+ {k}"] = v
        for k, v in y1.costs.items():
            if k != "variable":
                out[f"- {k}"] = -v
        out["- annuity difference"] = -(self.private_annuity - self.planning_annuity)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "technology": self.technology,
            "new_mw": self.new_mw,
            "new_mwh": self.new_mwh,
            "capex": self.capex,
            "life": self.life,
            "wacc": self.wacc,
            "energy_mwh": self.energy_mwh,
            "captured_price": self.captured_price,
            "planning": {
                "margin": self.planning_margin,
                "annuity": self.planning_annuity,
                "profit": self.planning_profit,
                "rate": self.planning_rate,
            },
            "private_annuity": self.private_annuity,
            "co2_avoided_t": self.co2_avoided_t,
            "co2_own_t": self.co2_own_t,
            "existing": self.existing,
            "details": self.details,
            "bridge": self.bridge(),
            "sensitivity": self.sensitivity,
            "streams": None if self.streams is None else self.streams.to_dict(),
            "risk": None if self.risk is None else self.risk.to_dict(),
            **self.cash.to_dict(),
        }


@dataclass
class EvalReport:
    """All projects of one case under one market."""

    results_dir: str
    market: str
    market_title: str
    notes: list[str]
    peak_demand_mw: float
    scale: float
    projects: list[ProjectEvaluation] = field(default_factory=list)
    sources: dict[str, Any] = field(default_factory=dict)
    #: storage accounts of the shadow-valued storage types
    #: (:mod:`.storage_value`)
    storages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "storages": self.storages,
            "results_dir": self.results_dir,
            "market": self.market,
            "market_title": self.market_title,
            "notes": self.notes,
            "peak_demand_mw": self.peak_demand_mw,
            "scale": self.scale,
            "analysis": "pre-tax (EBIT): no income tax, no tax shield",
            "projects": [p.to_dict() for p in self.projects],
            "sources": self.sources,
        }


# ── per-project resolution ──────────────────────────────────────────


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, list) and v:
        v = v[-1]
    return float(v) if isinstance(v, (int, float)) else None


def resolve_life(params: Params, proj: ProjectData, tech: str) -> int:
    over = params.project(proj.name).get("economic_life")
    if over is not None:
        return int(over)
    elem = _num(proj.elem.get("economic_life"))
    if elem is not None and elem > 0:
        return int(round(elem))
    t = params.tech(tech).get("life")
    if t is not None:
        return int(t)
    return int(params.finance.get("default_life", 25))


def resolve_wacc(params: Params, proj: ProjectData, tech: str) -> float:
    """Project override → technology → the element's ``wacc`` → finance."""
    over = params.project(proj.name).get("wacc")
    if over is not None:
        return float(over)
    t = params.tech(tech).get("wacc")
    if t is not None:
        return float(t)
    elem = _num(proj.elem.get("wacc"))
    if elem is not None:
        return elem
    return float(params.finance.get("wacc", 0.08))


def resolve_capex(params: Params, proj: ProjectData, life: int) -> float:
    """Overnight capex [$] of the NEW capacity.

    Per-unit cost: project override ``overnight_capcost`` [$/MW or $/MWh]
    (``power_overnight_capcost`` for a battery's MW), else the element's
    ``overnight_capcost``, else its ``annual_capcost`` / CRF(planning
    rate, life).
    """
    over = params.project(proj.name)
    rate = float(params.finance.get("wacc", 0.08))
    unit = _num(over.get("overnight_capcost"))
    if unit is None:
        unit = proj.planning_overnight(life, fallback_rate=rate)
    punit = None
    if proj.kind == "Battery":
        punit = _num(over.get("power_overnight_capcost"))
        if punit is None:
            punit = proj.planning_overnight(life, power=True, fallback_rate=rate)
    if proj.existing and unit is None and punit is None:
        # an existing unit without costs: replacement cost of its
        # technology (``capex_usd_kw`` / ``capex_usd_kwh``, CNE ICTGA)
        tech = params.technology(proj.name, proj.kind, proj.elem_type)
        kw = _num(params.tech(tech).get("capex_usd_kw")) or 0.0
        kwh = _num(params.tech(tech).get("capex_usd_kwh")) or 0.0
        return 1000.0 * (kw * proj.new_mw + kwh * proj.new_mwh)
    if proj.kind == "Battery":
        return (unit or 0.0) * proj.new_mwh + (punit or 0.0) * proj.new_mw
    return (unit or 0.0) * proj.new_mw


def trajectory_factor(
    params: Params,
    name: str,
    tech: str,
    year: float | None = None,
    scenario: str | None = None,
) -> float:
    """Capex multiplier of a project commissioned in ``year`` (or its
    ``cod_year``) relative to the trajectory base year."""
    tr = params.finance.get("capex_trajectory", {}) or {}
    cod = year
    if cod is None:
        cod = params.project(name).get("cod_year", tr.get("cod_year"))
    if cod is None:
        return 1.0
    base = float(tr.get("base_year", cod))
    return capex_factor(
        tech, scenario or str(tr.get("scenario", "moderate")), float(cod), base
    )


#: element fields that carry a fixed O&M cost already charged by the LP
#: [$/MW-yr or $/MWh-yr] (first match wins; ``finance.case_fom_fields``)
CASE_FOM_FIELDS = (
    "fixed_om_cost",
    "annual_fixed_om",
    "fom_cost",
    "annual_fom",
    "fixed_om",
)


def _field_value(elem: dict[str, Any], fields: Any) -> float | None:
    for fld in fields:
        val = elem.get(fld)
        if isinstance(val, list) and val:
            val = val[-1]
            while isinstance(val, list) and val:
                val = val[-1]
        if isinstance(val, (int, float)):
            return float(val)
    return None


def case_fixed_om(params: Params, proj: ProjectData) -> float | None:
    """Fixed O&M the solved case already charges [$/yr], ``None`` if none.

    Reads the first of ``finance.case_fom_fields`` present on the element
    (battery: energy element × new MWh + converter × new MW).
    """
    fields = params.finance.get("case_fom_fields") or CASE_FOM_FIELDS
    energy = _field_value(proj.elem, fields)
    power = _field_value(proj.power_elem, fields) if proj.power_elem else None
    if energy is None and power is None:
        return None
    if proj.kind == "Battery":
        return (energy or 0.0) * proj.new_mwh + (power or 0.0) * proj.new_mw
    return (energy or 0.0) * proj.new_mw


def fixed_om(ctx: EvalContext, proj: ProjectData, capex: float) -> float:
    """Yearly fixed O&M [$/yr] without double counting the LP's own.

    ``finance.fom_in_case``: ``"auto"`` (default) uses the case's fixed
    O&M field when the element has one, else ``fom_pct_capex × capex``;
    ``true`` = the case already includes fixed O&M inside
    ``annual_capcost`` (the tool adds none); ``false`` = always the
    percentage.
    """
    params = ctx.params
    tech = ctx.tech[proj.name]
    mode = params.finance.get("fom_in_case", "auto")
    if mode is True:
        return 0.0
    if mode == "auto":
        case = case_fixed_om(params, proj)
        if case is not None:
            return case
    pct = float(params.value(proj.name, tech, "fom_pct_capex", 0.02))
    return pct * capex if capex else 0.0


def risk_costs(ctx: EvalContext, proj: ProjectData, capex: float) -> Components:
    """Insurance, warranty / LTSA and generic guarantees [$ / yr].

    * ``insurance`` = ``insurance_pct_capex`` × capex (operational
      all-risk property + business interruption + liability);
    * ``warranty`` = ``warranty_pct_capex`` × capex +
      ``warranty_usd_kw_year`` × kW (extended OEM warranty, LTSA,
      storage performance guarantee / augmentation reserve);
    * ``guarantee_fee`` = ``guarantee_pct_capex`` × capex ×
      ``guarantee_fee_pct`` for ``guarantee_years`` years (a bank
      guarantee / standby LC posted for a contract or a permit; the
      Chilean CEN payment guarantee is in the ``cl`` model).

    Each looked up project → technology → finance (``Params.value``);
    ``finance.risk_costs: false`` drops all three.
    """
    params = ctx.params
    tech = ctx.tech[proj.name]
    if not params.finance.get("risk_costs", True):
        return {}

    def val(key: str) -> float:
        return float(params.value(proj.name, tech, key, 0.0) or 0.0)

    out: Components = {}
    # ``insurance_in_fom``: the fixed O&M % (CNE ICTGA 1-3 % of capex)
    # already pays the insurance -> no separate line (no double count)
    if (
        val("insurance_pct_capex")
        and capex
        and not params.value(proj.name, tech, "insurance_in_fom", False)
    ):
        out["insurance"] = val("insurance_pct_capex") * capex
    kw = (proj.new_mw or 0.0) * 1000.0
    warranty = val("warranty_pct_capex") * capex + val("warranty_usd_kw_year") * kw
    if warranty:
        out["warranty"] = warranty
    amount = val("guarantee_pct_capex") * capex
    fee = val("guarantee_fee_pct")
    if amount and fee:
        years = int(val("guarantee_years")) or ctx.life[proj.name]
        out["guarantee_fee"] = [amount * fee] * years + [0.0]
    return out


def _costs(
    ctx: EvalContext, market: MarketModel, proj: ProjectData, capex: float
) -> Components:
    """Market-independent + market costs (positive)."""
    params = ctx.params
    tech = ctx.tech[proj.name]
    out: Components = {}
    if proj.kind != "Line":
        var: Component = proj.variable_cost(ctx.shadow(proj))
        if ctx.fuel is not None:
            var = ctx.fuel.variable(proj) or var
        if var:
            out["variable"] = var
        vom = float(params.value(proj.name, tech, "vom", 0.0) or 0.0)
        if vom:
            out["vom_extra"] = vom * (proj.energy_mwh() + proj.charge_mwh())
    fom = fixed_om(ctx, proj, capex)
    if fom:
        out["fixed_om"] = fom
    out.update(risk_costs(ctx, proj, capex))
    out.update(market.costs(ctx, proj))
    return out


def _cash(
    ctx: EvalContext,
    proj: ProjectData,
    capex: float,
    revenues: Components,
    costs: Components,
) -> CashFlowResult:
    dep = ctx.params.finance.get("depreciation_life")
    return build_cash_flows(
        capex=capex,
        life=ctx.life[proj.name],
        wacc=ctx.wacc[proj.name],
        revenues=revenues,
        costs=costs,
        energy_mwh=proj.energy_mwh() if proj.kind != "Line" else 0.0,
        depreciation_life=int(dep) if dep else None,
    )


class FuelPath:
    """Fuel-cost trajectory of one evaluation (``finance.fuel_trajectory``).

    Variable cost of a fuel-burning project in operating year ``y``:
    base × factor(y), with the factor applied to the fuel component of
    the declared cost (:mod:`fuel_trajectory`).  With ``price_scaling =
    thermal_marginal`` the bus price of every period whose marginal unit
    burns fuel is scaled by that unit's factor.
    """

    def __init__(self, ctx: EvalContext, market: str, cfg: Mapping[str, Any]) -> None:
        self.ctx = ctx
        self.market = market
        self.cfg = cfg
        self.table = index_table(cfg)
        self.scenario = str(cfg.get("scenario", IEA_WEO.get("default_scenario")))
        self.base_year = float(cfg.get("base_year", IEA_WEO.get("base_year", 2024)))
        self.scale_prices = cfg.get("price_scaling", "thermal_marginal") != "none"
        self._decl: dict[str, DeclaredCost | None] = {}
        self.unsplit: list[str] = []

    # ── units ───────────────────────────────────────────────────────
    def unit(self, name: str, kind: str, case_cost: float) -> DeclaredCost | None:
        """Declared cost of a fuel-burning unit (None: no fuel)."""
        if name not in self._decl and name in self._shadow_units():
            # unit valued at a storage shadow value: its gcost is VOM
            # only and the fuel / water is not a declared / indexed cost.
            self._decl[name] = None
        if name not in self._decl:
            params = self.ctx.params
            tech = params.technology(name, kind)
            fuel = fuel_of(params, name, tech) if kind == "Generator" else None
            dc = None
            if fuel is not None and fuel in self.table:
                dc = declared_cost(params, self.market, name, tech, case_cost)
                if dc.fuel is None and dc.total > 0.0:
                    self.unsplit.append(name)
            self._decl[name] = dc
        return self._decl[name]

    def ratios(
        self, name: str, kind: str, start: float, life: int, scen: str
    ) -> list[float]:
        """Fuel-price ratio of each operating year."""
        params = self.ctx.params
        fuel = fuel_of(params, name, params.technology(name, kind))
        return [
            fuel_ratio(str(fuel), scen, start + y, self.base_year, self.table)
            for y in range(life)
        ]

    def cod(self, name: str) -> float:
        """First operating year: project, trajectory, capex trajectory, base."""
        params = self.ctx.params
        cap = params.finance.get("capex_trajectory", {}) or {}
        for v in (
            params.project(name).get("cod_year"),
            self.cfg.get("cod_year"),
            cap.get("cod_year"),
        ):
            if v is not None:
                return float(v)
        return self.base_year

    def _shadow_units(self) -> set[str]:
        return {p.name for p in self.ctx.sysd.projects if self.ctx.shadow(p)}

    # ── per project ─────────────────────────────────────────────────
    def variable(self, proj: ProjectData, scen: str | None = None) -> Component | None:
        """Yearly variable cost of a fuel-burning project, None otherwise."""
        shadow = self.ctx.shadow(proj)
        var = proj.variable_cost(shadow)
        energy = proj.energy_mwh()
        case_cost = var / energy if energy > 1e-9 else 0.0
        if proj.kind != "Generator":
            return None
        dc = self.unit(proj.name, proj.kind, case_cost)
        if dc is None:
            return None
        if dc.source.startswith("declared") or dc.source.startswith("heat_rate"):
            var = dc.total * energy
        life = self.ctx.life[proj.name]
        rs = self.ratios(
            proj.name, proj.kind, self.cod(proj.name), life, scen or self.scenario
        )
        return [var * dc.factor(r) for r in rs]

    def _setter(self, key: Any, price: float) -> tuple[str, DeclaredCost] | None:
        """Name and declared cost of the fuel-burning unit setting ``price``."""
        tol = 1e-6 * max(1.0, abs(price))
        for _uid, name, _bus, gen, srmc in self.ctx.sysd.dispatch.get(key, []):
            if gen > 1e-6 and abs(srmc - price) <= tol:
                dc = self.unit(name, "Generator", srmc)
                if dc is not None:
                    return name, dc
        return None

    def energy(self, proj: ProjectData, scen: str | None = None) -> list[float] | None:
        """Yearly energy revenue with thermal-marginal prices scaled."""
        if not self.scale_prices or proj.kind == "Line":
            return None
        life = self.ctx.life[proj.name]
        start = self.cod(proj.name)
        sc = scen or self.scenario
        out = [0.0] * life
        cache: dict[str, list[float]] = {}
        for r in proj.rows:
            base = r.price * r.net * r.hours
            found = self._setter(r.key, r.price)
            if found is None:
                for y in range(life):
                    out[y] += base
                continue
            setter, dc = found
            if setter not in cache:
                cache[setter] = self.ratios(setter, "Generator", start, life, sc)
            for y, ratio in enumerate(cache[setter]):
                out[y] += base * dc.factor(ratio)
        return out

    def notes(self) -> list[str]:
        src = IEA_WEO.get("edition", "user index")
        out = [
            f"fuel cost: declared base x {src} index, scenario {self.scenario},"
            f" base year {self.base_year:g}"
            + (
                "; CMg scaled in thermal-marginal hours (approximation: base-year"
                " dispatch -- rerun gtopt with 'fuel-trajectory --planning' for a"
                " consistent dispatch)"
                if self.scale_prices
                else "; CMg kept at the base year (price_scaling none)"
            )
        ]
        if self.unsplit:
            out.append(
                "fuel index applied to the whole declared cost (not split into"
                f" fuel / non-fuel): {', '.join(sorted(set(self.unsplit)))}"
            )
        return out


def _apply_fuel(
    fp: FuelPath,
    proj: ProjectData,
    revenues: Components,
    costs: Components,
    scen: str | None = None,
) -> tuple[Components, Components]:
    """Revenues and costs with the fuel trajectory of scenario ``scen``."""
    rv, cs = dict(revenues), dict(costs)
    var = fp.variable(proj, scen)
    if var is not None:
        cs["variable"] = var
    energy = fp.energy(proj, scen)
    if energy is not None and "energy" in rv:
        base = proj.energy_revenue()
        rv["energy"] = energy
        cfd = rv.get("cfd_difference")
        if cfd is not None:
            # a CfD pays strike − price: the hedged total stays strike × energy
            vals = (
                [float(cfd)] * len(energy)
                if isinstance(cfd, (int, float))
                else [float(v) for v in cfd]
            )
            rv["cfd_difference"] = [
                (v - (energy[y] - base)) if abs(v) > 1e-12 else v
                for y, v in enumerate(vals[: len(energy)])
            ]
    return rv, cs


def system_warnings(sysd: SystemData) -> list[str]:
    """Flags on the solved case that change how its prices can be read."""
    out: list[str] = []
    if sysd.unserved_mwh > 1e-6:
        out.append(
            f"WARNING: the case has unserved energy ({sysd.unserved_mwh:,.0f}"
            f" MWh/yr; max price {sysd.max_price:,.2f} $/MWh vs highest"
            f" generator cost {sysd.max_srmc:,.2f}): its prices carry"
            " scarcity rents"
        )
    return out


def evaluate(
    sysd: SystemData,
    params: Params,
    market_name: str,
    *,
    mef_file: str | Path | None = None,
    include_unbuilt: bool = False,
) -> EvalReport:
    """Evaluate every (built) project of ``sysd`` under ``market_name``."""
    market = get_market(market_name, params.market(market_name))
    bat_gens = {f"{p.name}_gen" for p in sysd.projects if p.kind == "Battery"}
    rate_of: dict[str, float] = {}
    types = {u.name: u.elem_type for u in sysd.units}
    for key_rows in sysd.dispatch.values():
        for _uid, name, _bus, _q, _c in key_rows:
            if name not in rate_of:
                tech = params.technology(name, "Generator", types.get(name))
                rate_of[name] = float(
                    params.value(name, tech, "emission_rate", 0.0) or 0.0
                )
        break
    if mef_file is not None:
        mef = read_mef_csv(Path(mef_file))
    else:
        mef = marginal_emission_factors(
            sysd,
            rate_of,
            tol=float(params.co2.get("mef_tolerance", 1e-4)),
            exclude=bat_gens,
        )
    ctx = EvalContext(
        sysd=sysd,
        params=params,
        mef=mef,
        storage_modes=valuation_modes(params.cfg.get("storage_valuation"), market.name),
    )
    chosen = [p for p in sysd.projects if p.built or include_unbuilt]
    for proj in chosen:
        tech = params.technology(proj.name, proj.kind, proj.elem_type)
        ctx.tech[proj.name] = tech
        ctx.life[proj.name] = resolve_life(params, proj, tech)
        ctx.wacc[proj.name] = resolve_wacc(params, proj, tech)
        ctx.capex[proj.name] = resolve_capex(
            params, proj, ctx.life[proj.name]
        ) * trajectory_factor(params, proj.name, tech)
    fcfg = trajectory_config(params.finance)
    if fcfg is not None:
        ctx.fuel = FuelPath(ctx, market.name, fcfg)
    if market.name == "cl":
        # validate: every fuel-burning project has a declared cost
        check = ctx.fuel or FuelPath(ctx, "cl", {"enabled": True})
        for proj in chosen:
            check.variable(proj)
    report = EvalReport(
        results_dir=sysd.results_dir,
        market=market.name,
        market_title=market.title,
        notes=market.notes() + system_warnings(sysd),
        peak_demand_mw=sysd.peak_demand_mw,
        scale=sysd.scale,
        sources=params.sources(),
    )
    shadow_kinds = {k for k, m in ctx.storage_modes.items() if m == "shadow"}
    if sysd.storage is not None:
        # cash (default): the LNG terminal / owner account of the cargo
        # purchases; shadow (diagnostic): the accounts of those types
        accounts = sysd.storage.accounts(shadow_kinds | {"lng_terminal"})
        report.storages = [a.to_dict() for a in accounts]
        if accounts or shadow_kinds:
            report.notes.extend(storage_notes(shadow_kinds, accounts))
    # LCOE first (cost side only) — the cfd model may strike at it.
    costs_of: dict[str, Components] = {}
    for proj in chosen:
        capex = ctx.capex[proj.name]
        costs_of[proj.name] = _costs(ctx, market, proj, capex)
        lcoe = _cash(ctx, proj, capex, {}, costs_of[proj.name]).lcoe()
        if lcoe is not None:
            ctx.lcoe[proj.name] = lcoe
    for proj in chosen:
        ev = _evaluate_one(ctx, market, proj, costs_of[proj.name])
        report.projects.append(ev)
        if ev.streams is not None:
            report.notes.extend(f"{ev.name}: {w}" for w in ev.streams.warnings)
        if ev.risk is not None:
            report.notes.extend(f"{ev.name}: risk: {n}" for n in ev.risk.notes)
            if not ev.risk.passes_cvar:
                report.notes.append(
                    f"{ev.name}: FAILS the private-CVaR check (NPV CVaR"
                    f" {ev.risk.cvar / 1e6:,.1f} M$ < 0"
                    + (
                        ", although its expected NPV is positive"
                        if ev.risk.passes_expectation
                        else ""
                    )
                    + "): unpaid private risk -- a contract, sufficiency /"
                    " capacity payment or regulated remuneration would be"
                    " needed to finance it"
                )
    if ctx.fuel is not None:
        report.notes.extend(ctx.fuel.notes())
    return report


def _evaluate_one(
    ctx: EvalContext, market: MarketModel, proj: ProjectData, costs: Components
) -> ProjectEvaluation:
    params = ctx.params
    tech = ctx.tech[proj.name]
    capex = ctx.capex[proj.name]
    revenues = market.revenues(ctx, proj)
    if ctx.fuel is not None:
        revenues, costs = _apply_fuel(ctx.fuel, proj, revenues, costs)
    cash = _cash(ctx, proj, capex, revenues, costs)
    rec = proj.planning
    avoided, own = (0.0, 0.0)
    if proj.kind != "Line":
        avoided, own = co2_tonnes(proj, ctx.mef, ctx.own_rate(proj))
    ev = ProjectEvaluation(
        kind=proj.kind,
        name=proj.name,
        technology=tech,
        new_mw=proj.new_mw,
        new_mwh=proj.new_mwh,
        capex=capex,
        life=ctx.life[proj.name],
        wacc=ctx.wacc[proj.name],
        cash=cash,
        energy_mwh=proj.energy_mwh(),
        captured_price=proj.captured_price(),
        planning_margin=rec.margin_new if rec is not None else 0.0,
        planning_annuity=rec.total_capex if rec is not None else 0.0,
        planning_rate=proj.planning_rate(),
        co2_avoided_t=avoided,
        co2_own_t=own,
        existing=proj.existing,
        details=market.details(ctx, proj),
    )
    if proj.kind != "Line":
        sens: dict[str, float] = {}
        for price in params.co2.get("sensitivity", []) or []:
            rv = {k: v for k, v in revenues.items() if k != "co2"}
            if price:
                rv["co2"] = ctx.co2_value(proj, float(price))
            cs = {k: v for k, v in costs.items() if k != "green_tax"}
            sens[f"{float(price):g}"] = _cash(ctx, proj, capex, rv, cs).npv
        ev.sensitivity["npv_vs_co2_price"] = sens
    _scenario_sensitivity(ctx, proj, ev, revenues, costs)
    if ctx.fuel is not None and proj.kind != "Line":
        fp = ctx.fuel
        base_rv = market.revenues(ctx, proj)
        ev.sensitivity["npv_vs_fuel_scenario"] = {
            sc: _cash(ctx, proj, capex, *_apply_fuel(fp, proj, base_rv, costs, sc)).npv
            for sc in scenarios(fp.table)
        }
    scfg = stream_config(params.finance, params.project(proj.name))
    if scfg is not None:
        ev.streams = evaluate_streams(
            cash,
            scfg,
            risk_adjusted_prices=bool(params.finance.get("risk_adjusted_prices")),
            market_map=market.stream_map,
        )
    rng = params.tech(tech).get("capex_range")
    if rng and proj.new_mw > 0:
        out: dict[str, float] = {}
        for label, per_kw in zip(("low", "high"), rng):
            cx = float(per_kw) * 1000.0 * proj.new_mw
            cs = dict(costs)
            fom = fixed_om(ctx, proj, cx)
            if fom:
                cs["fixed_om"] = fom
            out[f"{label} ({float(per_kw):g} $/kW)"] = _cash(
                ctx, proj, cx, revenues, cs
            ).npv
        ev.sensitivity["npv_vs_capex"] = out
    rcfg = risk_config(params.cfg, params.project(proj.name))
    if rcfg is not None and proj.kind != "Line":
        ev.risk = _risk(ctx, market, proj, capex, rcfg, ev)
    return ev


def hydrology_index(
    ctx: EvalContext, cfg: Mapping[str, Any]
) -> dict[int, float] | None:
    """Wetness per scenario: the user's ``hydrology_index``, else the
    energy of the case's hydro units; None when neither exists."""
    user = cfg.get("hydrology_index")
    if isinstance(user, Mapping) and user:
        return {int(k): float(v) for k, v in user.items()}
    sysd = ctx.sysd
    dur = {
        (p.scenario, p.stage, p.block): p.duration
        for periods in sysd.case.periods.values()
        for p in periods
    }
    out: dict[int, float] = {}
    for key, rows in sysd.dispatch.items():
        for _uid, name, _bus, gen, _cost in rows:
            if ctx.params.technology(name, "Generator") == "hydro":
                out[key[0]] = out.get(key[0], 0.0) + gen * dur.get(key, 0.0)
    return out if any(v > 0.0 for v in out.values()) else None


def _risk(
    ctx: EvalContext,
    market: MarketModel,
    proj: ProjectData,
    capex: float,
    cfg: Mapping[str, Any],
    ev: ProjectEvaluation,
) -> RiskResult:
    """NPV of each scenario evaluated on its own, then the risk measures."""
    weights = ctx.sysd.case.scenario_weight
    present = sorted({r.key[0] for r in proj.rows})
    total = sum(weights.get(sc, 1.0) for sc in present) or 1.0
    probs = {sc: weights.get(sc, 1.0) / total for sc in present}
    npvs: dict[int, float] = {}
    irrs: dict[int, float | None] = {}
    for sc in present:
        view = replace(
            proj,
            rows=[
                replace(r, hours=r.hours / (probs[sc] * total))
                for r in proj.rows
                if r.key[0] == sc
            ],
        )
        rv = market.revenues(ctx, view)
        cs = _costs(ctx, market, view, capex)
        if ctx.fuel is not None:
            rv, cs = _apply_fuel(ctx.fuel, view, rv, cs)
        cash = _cash(ctx, view, capex, rv, cs)
        npvs[sc] = cash.npv
        irrs[sc] = cash.irr
    res = measure(cfg, probs, npvs, irrs, ev.cash.irr, hydrology_index(ctx, cfg))
    if ev.streams is not None:
        res.notes.append(
            "combined with finance.stream_rates: a risk premium in the stream"
            " rates and this measure across scenarios both price the"
            " hydrology risk -- count it once (risk-free / contract-level"
            " rates with the risk measure, or premia without it)"
        )
    return res


def _scenario_sensitivity(
    ctx: EvalContext,
    proj: ProjectData,
    ev: ProjectEvaluation,
    revenues: Components,
    costs: Components,
) -> None:
    """NPV with the capex of each trajectory scenario at the sensitivity
    year (the project keeps its revenues; fixed O&M follows capex)."""
    params = ctx.params
    tech = ctx.tech[proj.name]
    tr = params.finance.get("capex_trajectory", {}) or {}
    year = tr.get("sensitivity_year")
    if year is None:
        return
    now = trajectory_factor(params, proj.name, tech)
    base_capex = ctx.capex[proj.name] / now if now else ctx.capex[proj.name]
    out: dict[str, float] = {}
    for scen in tr.get("scenarios", []) or []:
        cx = base_capex * trajectory_factor(params, proj.name, tech, float(year), scen)
        cs = dict(costs)
        fom = fixed_om(ctx, proj, cx)
        if fom:
            cs["fixed_om"] = fom
        out[f"{scen} {int(year)}"] = _cash(ctx, proj, cx, revenues, cs).npv
    ev.sensitivity["npv_vs_capex_scenario"] = out
