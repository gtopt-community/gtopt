# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Pre-tax (EBIT) cash-flow engine: EBITDA, depreciation, NPV, IRR, payback.

The analysis is deliberately **pre-tax**: no income tax and no tax
shield.  Cash flow of year ``y >= 1`` is EBITDA = revenues − operating
costs; year 0 carries the overnight capex.  Depreciation is reported
(straight line) only to form EBIT; it is not a cash flow.  Discounting
uses the project's pre-tax private WACC, the same kind of rate gtopt's
annuities use (``annual_capcost = overnight × CRF(wacc, life)``), so the
private NPV and the planning annuity view are directly comparable.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


def crf(rate: float, life: float) -> float:
    """Capital recovery factor ``r(1+r)^n / ((1+r)^n − 1)`` (``1/n`` at 0)."""
    if life <= 0:
        raise ValueError("life must be positive")
    if abs(rate) < 1e-12:
        return 1.0 / life
    g = (1.0 + rate) ** life
    return rate * g / (g - 1.0)


def npv(rate: float, flows: Sequence[float]) -> float:
    """Net present value of ``flows[y]`` at the end of year ``y``."""
    return sum(cf / (1.0 + rate) ** y for y, cf in enumerate(flows))


def irr(flows: Sequence[float], lo: float = -0.99, hi: float = 10.0) -> float | None:
    """Internal rate of return by bisection; ``None`` if no sign change."""
    f_lo, f_hi = npv(lo, flows), npv(hi, flows)
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)) or f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = npv(mid, flows)
        if abs(f_mid) < 1e-9 or hi - lo < 1e-12:
            return mid
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)


def payback(flows: Sequence[float], rate: float = 0.0) -> float | None:
    """Years until the (discounted) cumulative cash flow turns >= 0.

    Linear interpolation inside the year; ``None`` if never.
    """
    cum = 0.0
    for y, cf in enumerate(flows):
        dcf = cf / (1.0 + rate) ** y
        if cum < 0.0 <= cum + dcf and y > 0:
            return (y - 1) + (-cum / dcf if dcf > 0 else 0.0)
        cum += dcf
    return 0.0 if flows and flows[0] >= 0 else None


@dataclass
class YearRow:
    """One operating year (all money in $ of the case currency)."""

    year: int
    revenues: dict[str, float]
    costs: dict[str, float]
    energy_mwh: float
    depreciation: float

    @property
    def revenue(self) -> float:
        return sum(self.revenues.values())

    @property
    def opex(self) -> float:
        return sum(self.costs.values())

    @property
    def ebitda(self) -> float:
        return self.revenue - self.opex

    @property
    def ebit(self) -> float:
        return self.ebitda - self.depreciation

    @property
    def ebit_margin(self) -> float | None:
        return self.ebit / self.revenue if abs(self.revenue) > 1e-9 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "revenues": dict(self.revenues),
            "costs": dict(self.costs),
            "revenue": self.revenue,
            "opex": self.opex,
            "ebitda": self.ebitda,
            "depreciation": self.depreciation,
            "ebit": self.ebit,
            "ebit_margin": self.ebit_margin,
            "energy_mwh": self.energy_mwh,
        }


@dataclass
class CashFlowResult:
    """Pre-tax evaluation of one project."""

    capex: float
    life: int
    wacc: float
    years: list[YearRow] = field(default_factory=list)

    @property
    def flows(self) -> list[float]:
        return [-self.capex] + [r.ebitda for r in self.years]

    @property
    def npv(self) -> float:
        return npv(self.wacc, self.flows)

    @property
    def irr(self) -> float | None:
        return irr(self.flows) if self.capex > 0 else None

    @property
    def payback(self) -> float | None:
        return payback(self.flows)

    @property
    def discounted_payback(self) -> float | None:
        return payback(self.flows, self.wacc)

    @property
    def equivalent_annual(self) -> float:
        """NPV spread as an annuity over the life at the WACC [$/yr]."""
        return self.npv * crf(self.wacc, self.life)

    def lcoe(self) -> float | None:
        """(capex + PV of all operating costs) / PV of energy [$/MWh]."""
        pv_e = sum(r.energy_mwh / (1.0 + self.wacc) ** r.year for r in self.years)
        if pv_e <= 1e-9:
            return None
        pv_c = self.capex + sum(
            r.opex / (1.0 + self.wacc) ** r.year for r in self.years
        )
        return pv_c / pv_e

    def totals(self, attr: str) -> dict[str, float]:
        """Undiscounted life totals of ``revenues`` or ``costs`` by component."""
        out: dict[str, float] = {}
        for r in self.years:
            for k, v in getattr(r, attr).items():
                out[k] = out.get(k, 0.0) + v
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "capex": self.capex,
            "life": self.life,
            "wacc": self.wacc,
            "npv": self.npv,
            "irr": self.irr,
            "payback": self.payback,
            "discounted_payback": self.discounted_payback,
            "equivalent_annual": self.equivalent_annual,
            "lcoe": self.lcoe(),
            "years": [r.to_dict() for r in self.years],
        }


def _at(value: float | Sequence[float], y: int) -> float:
    """Year-``y`` (1-based) value of a constant or a per-year list."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    return float(value[min(y - 1, len(value) - 1)])


def build_cash_flows(
    *,
    capex: float,
    life: int,
    wacc: float,
    revenues: Mapping[str, float | Sequence[float]],
    costs: Mapping[str, float | Sequence[float]],
    energy_mwh: float,
    depreciation_life: int | None = None,
) -> CashFlowResult:
    """Yearly pre-tax cash flows of a project.

    ``revenues`` / ``costs`` map a component name to a constant yearly
    value or to a per-year list (the last entry repeats).  Depreciation is
    straight line over ``depreciation_life`` (default: ``life``).
    """
    if life <= 0:
        raise ValueError("life must be positive")
    dep_life = depreciation_life or life
    dep = capex / dep_life
    res = CashFlowResult(capex=capex, life=life, wacc=wacc)
    for y in range(1, life + 1):
        res.years.append(
            YearRow(
                year=y,
                revenues={k: _at(v, y) for k, v in revenues.items()},
                costs={k: _at(v, y) for k, v in costs.items()},
                energy_mwh=energy_mwh,
                depreciation=dep if y <= dep_life else 0.0,
            )
        )
    return res
