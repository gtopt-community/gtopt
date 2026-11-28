# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The market-model interface and the context every model receives.

A market model turns the solved plan (prices, dispatch, capacity) plus
its parameter block into **yearly revenue and cost components** of one
project.  The engine (``evaluate.py``) adds the market-independent
items — variable cost as modelled in gtopt, extra VOM, fixed O&M as a %
of capex — and runs the pre-tax cash flow.

Adding a market = one module with a :class:`MarketModel` subclass,
registered in ``markets/__init__.py``, plus its block in
``defaults.py`` (every value with a source).

A component value is a constant ``$/yr`` or a per-year list (year 1
first, the last entry repeats), so a model can phase a charge in or out
(e.g. a contract that ends, a transitional toll).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from gtopt_cost_recovery.recovery import capital_recovery_factor

from ..case_data import ProjectData, SystemData
from ..emissions import MefMap, co2_value
from ..params import Params

Component = float | Sequence[float]
Components = dict[str, Component]


@dataclass
class EvalContext:
    """What a market model may read about the system and the project."""

    sysd: SystemData
    params: Params
    mef: MefMap = field(default_factory=dict)
    #: resolved per project: technology, capex [$], life [y], wacc
    tech: dict[str, str] = field(default_factory=dict)
    capex: dict[str, float] = field(default_factory=dict)
    life: dict[str, int] = field(default_factory=dict)
    wacc: dict[str, float] = field(default_factory=dict)
    #: pre-tax LCOE per project (filled before revenues; used by ``cfd``)
    lcoe: dict[str, float] = field(default_factory=dict)
    #: fuel-cost trajectory (``evaluate.FuelPath``; None = flat costs)
    fuel: Any = None
    #: per-evaluation cache of market models (peak hours, pie factor, ...)
    cache: dict[str, Any] = field(default_factory=dict)
    #: storage type -> ``declared`` / ``shadow`` (:mod:`..storage_value`)
    storage_modes: dict[str, str] = field(default_factory=dict)

    def shadow(self, proj: ProjectData) -> bool:
        """True when the storage the unit draws from is shadow-valued."""
        kind = proj.storage_kind
        return kind is not None and self.storage_modes.get(kind) == "shadow"

    def own_rate(self, proj: ProjectData) -> float:
        tech = self.tech.get(proj.name, "thermal")
        return float(self.params.value(proj.name, tech, "emission_rate", 0.0))

    def co2_value(self, proj: ProjectData, price: float) -> float:
        return co2_value(proj, self.mef, self.own_rate(proj), price)

    def contract(self, years: int | None, life: int, value: float) -> Component:
        """``value`` for ``years`` (None = whole life), then 0."""
        if years is None or years >= life:
            return value
        return [value] * max(years, 0) + [0.0]


def interp_factor(table: dict[str, float], hours: float | None) -> float:
    """Linear interpolation in ``{hours: factor}``; 0 below the first key,
    the last factor above the last key (DS 70/2023
    transitory rule; storage derating by duration)."""
    if hours is None or not table:
        return 0.0
    pts = sorted((float(k), float(v)) for k, v in table.items())
    if hours < pts[0][0] - 1e-9:
        return 0.0
    for (h0, f0), (h1, f1) in zip(pts, pts[1:]):
        if hours <= h1 + 1e-9:
            return f0 + (f1 - f0) * (hours - h0) / (h1 - h0)
    return pts[-1][1]


class MarketModel:
    """Base model: energy at the nodal price, reserves at their duals,
    the CO2 value when the carbon price is in the cash flow, and either
    merchant or regulated transmission."""

    name: ClassVar[str] = "base"
    title: ClassVar[str] = ""
    #: keys of ``params.sources()`` this model's defaults rely on
    source_keys: ClassVar[tuple[str, ...]] = ()
    #: component → stream defaults of this market for ``finance.stream_rates``
    #: (on top of ``streams.DEFAULT_STREAM_OF``; a user ``map`` wins)
    stream_map: ClassVar[dict[str, str]] = {}

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.p = params

    # ── helpers ──────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        v = self.p.get(key)
        return default if v is None else v

    def by_tech(self, table: str, tech: str, default: float = 0.0) -> float:
        tab = self.p.get(table, {}) or {}
        v = tab.get(tech, tab.get("default", default))
        return float(v)

    # ── interface ────────────────────────────────────────────────────
    def revenues(self, ctx: EvalContext, proj: ProjectData) -> Components:
        """Yearly revenue components of ``proj`` [$ / yr]."""
        out: Components = {}
        if proj.kind == "Line":
            out.update(self.transmission(ctx, proj))
            return out
        out["energy"] = proj.energy_revenue()
        if proj.reserve_revenue:
            out["reserves"] = proj.reserve_revenue
        co2 = ctx.params.co2
        price = float(co2.get("price", 0.0) or 0.0)
        if price and co2.get("in_cash_flow", True):
            out["co2"] = ctx.co2_value(proj, price)
        return out

    def costs(self, ctx: EvalContext, proj: ProjectData) -> Components:
        """Yearly market charges of ``proj`` [$ / yr] (positive = cost)."""
        del ctx, proj
        return {}

    def transmission(self, ctx: EvalContext, proj: ProjectData) -> Components:
        """Merchant (congestion rent) or regulated (AVI + COMA) revenue."""
        if self.get("transmission", "merchant") == "regulated":
            # an after-tax regulated rate r earns, pre-tax, about r/(1 − t)
            # (Chile: AVI + AEIR, the income-tax adjustment of the VATT)
            rate = float(self.get("regulated_rate", 0.07)) / (
                1.0 - float(self.get("regulated_tax_gross_up", 0.0))
            )
            life = ctx.life[proj.name]
            capex = ctx.capex[proj.name]
            fom = float(ctx.params.value(proj.name, "line", "fom_pct_capex", 0.02))
            # the regulated COMA also pays the owner's insurance
            if ctx.params.finance.get("risk_costs", True):
                fom += float(
                    ctx.params.value(proj.name, "line", "insurance_pct_capex", 0.0)
                    or 0.0
                )
            return {
                "regulated_avi": capex * capital_recovery_factor(rate, life),
                "regulated_coma": capex * fom,
            }
        return {"congestion_rent": proj.line_margin}

    def details(self, ctx: EvalContext, proj: ProjectData) -> dict[str, float]:
        """Physical quantities behind the revenues (reported, not cash):
        time-weighted mean bus price, captured price, energy."""
        hours = sum(r.hours for r in proj.rows)
        out: dict[str, float] = {}
        if hours > 0:
            out["mean_bus_price"] = sum(r.price * r.hours for r in proj.rows) / hours
        cap = proj.captured_price()
        if cap is not None:
            out["captured_price"] = cap
        out["energy_mwh"] = proj.energy_mwh()
        # spread over the scenarios (hydrologies) of the case: yearly
        # energy and captured price of each scenario on its own
        by: dict[int, list[float]] = {}
        for r in proj.rows:
            acc = by.setdefault(r.key[0], [0.0, 0.0])
            h = r.hours / r.prob if r.prob > 0 else r.hours
            acc[0] += r.gen * h
            acc[1] += r.price * r.gen * h
        if len(by) > 1:
            energies = [e for e, _v in by.values()]
            prices = [v / e for e, v in by.values() if e > 0]
            out["energy_mwh_min_scenario"] = min(energies)
            out["energy_mwh_max_scenario"] = max(energies)
            if prices:
                out["captured_price_min_scenario"] = min(prices)
                out["captured_price_max_scenario"] = max(prices)
        if proj.kind == "Battery":
            out["charge_mwh"] = proj.charge_mwh()
            charge = proj.charge_mwh()
            if charge > 0:
                out["charge_price"] = (
                    sum(r.price * r.dem * r.hours for r in proj.rows) / charge
                )
        del ctx
        return out

    def notes(self) -> list[str]:
        """Human-readable assumptions printed with the report."""
        return []
