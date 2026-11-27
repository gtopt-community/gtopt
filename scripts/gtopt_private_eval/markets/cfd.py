# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``cfd``: two-sided contract for difference on the metered output.

The generator sells at the market and settles the difference with the
counterparty (UK CfD; EU Regulation 2024/1747 two-sided CfDs):

``D = Σ_t (S − λ_t) · gen_t · h_t``  (negative when the price is above S)

so energy + difference = ``S × energy`` for ``contract_years``; merchant
afterwards.  ``strike`` is a number, a per-project mapping, or
``"lcoe"`` (each project's own pre-tax LCOE at its private WACC — the
break-even strike of a whole-life contract).  The reference price here
is the hourly bus price (UK intermittent CfDs use the day-ahead hourly
price).  Storage and lines get no CfD.
"""

from __future__ import annotations

from typing import Any

from ..case_data import ProjectData
from .base import Components, EvalContext, MarketModel


class ContractForDifference(MarketModel):
    """Energy at the nodal price + two-sided CfD difference payment."""

    name = "cfd"
    title = "Two-sided contract for difference (strike on metered energy)"

    def strike_of(self, ctx: EvalContext, proj: ProjectData) -> float | None:
        raw: Any = self.get("strike", "lcoe")
        if isinstance(raw, dict):
            raw = raw.get(proj.name, raw.get("default", "lcoe"))
        if raw == "lcoe":
            return ctx.lcoe.get(proj.name)
        return float(raw)

    def revenues(self, ctx: EvalContext, proj: ProjectData) -> Components:
        out = super().revenues(ctx, proj)
        if proj.kind != "Generator":
            return out
        strike = self.strike_of(ctx, proj)
        if strike is None:
            return out
        diff = sum((strike - r.price) * r.gen * r.hours for r in proj.rows)
        years = self.get("contract_years")
        out["cfd_difference"] = ctx.contract(
            None if years is None else int(years), ctx.life[proj.name], diff
        )
        return out

    def notes(self) -> list[str]:
        return [
            f"strike {self.get('strike', 'lcoe')}, "
            f"contract years {self.get('contract_years')}",
        ]
