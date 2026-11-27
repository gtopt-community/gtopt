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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .capex_trajectory import capex_factor
from .case_data import ProjectData, SystemData
from .emissions import co2_tonnes, marginal_emission_factors, read_mef_csv
from .finance import CashFlowResult, build_cash_flows, crf
from .markets import Components, EvalContext, MarketModel, get_market
from .params import Params


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
            "bridge": self.bridge(),
            "sensitivity": self.sensitivity,
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

    def to_dict(self) -> dict[str, Any]:
        return {
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
        unit = proj.planning_overnight(life, fallback_rate=rate) or 0.0
    if proj.kind == "Battery":
        punit = _num(over.get("power_overnight_capcost"))
        if punit is None:
            punit = proj.planning_overnight(life, power=True, fallback_rate=rate) or 0.0
        return unit * proj.new_mwh + punit * proj.new_mw
    return unit * proj.new_mw


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
    if val("insurance_pct_capex") and capex:
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
        var = proj.variable_cost()
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
    for key_rows in sysd.dispatch.values():
        for _uid, name, _bus, _q, _c in key_rows:
            if name not in rate_of:
                tech = params.technology(name, "Generator")
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
    ctx = EvalContext(sysd=sysd, params=params, mef=mef)
    chosen = [p for p in sysd.projects if p.built or include_unbuilt]
    for proj in chosen:
        tech = params.technology(proj.name, proj.kind)
        ctx.tech[proj.name] = tech
        ctx.life[proj.name] = resolve_life(params, proj, tech)
        ctx.wacc[proj.name] = resolve_wacc(params, proj, tech)
        ctx.capex[proj.name] = resolve_capex(
            params, proj, ctx.life[proj.name]
        ) * trajectory_factor(params, proj.name, tech)
    report = EvalReport(
        results_dir=sysd.results_dir,
        market=market.name,
        market_title=market.title,
        notes=market.notes() + system_warnings(sysd),
        peak_demand_mw=sysd.peak_demand_mw,
        scale=sysd.scale,
        sources=params.sources(),
    )
    # LCOE first (cost side only) — the cfd model may strike at it.
    costs_of: dict[str, Components] = {}
    for proj in chosen:
        capex = ctx.capex[proj.name]
        costs_of[proj.name] = _costs(ctx, market, proj, capex)
        lcoe = _cash(ctx, proj, capex, {}, costs_of[proj.name]).lcoe()
        if lcoe is not None:
            ctx.lcoe[proj.name] = lcoe
    for proj in chosen:
        report.projects.append(_evaluate_one(ctx, market, proj, costs_of[proj.name]))
    return report


def _evaluate_one(
    ctx: EvalContext, market: MarketModel, proj: ProjectData, costs: Components
) -> ProjectEvaluation:
    params = ctx.params
    tech = ctx.tech[proj.name]
    capex = ctx.capex[proj.name]
    revenues = market.revenues(ctx, proj)
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
    return ev


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
