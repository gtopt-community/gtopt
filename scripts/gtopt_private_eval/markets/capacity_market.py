# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``capacity_market``: energy + a forward capacity market.

Capacity revenue of a new unit = clearing price × derated capacity,
for ``contract_years`` (new-build agreements: GB up to 15 years, PJM one
delivery year at a time) and ``price_after`` afterwards:

``R_cap = P_clear [$/MW-yr] × MW_new × d_tech``

``d_tech`` is the de-rating factor (GB) or ELCC class rating (PJM); for
storage it depends on the duration (``storage_derating`` by hours,
linear in between; the default table starts at 0 h = 0, an assumption
below PJM's shortest 4-h class).
"""

from __future__ import annotations

from ..case_data import ProjectData
from .base import Components, EvalContext, MarketModel, interp_factor


class CapacityMarket(MarketModel):
    """Energy + capacity market (clearing price × derated MW)."""

    name = "capacity_market"
    title = "Forward capacity market (clearing price x derated capacity)"

    def derating(self, ctx: EvalContext, proj: ProjectData) -> float:
        tech = ctx.tech.get(proj.name, "thermal")
        if proj.kind == "Battery":
            return interp_factor(self.get("storage_derating", {}), proj.duration_h())
        return self.by_tech("derating", tech)

    def capacity_price(self) -> float:
        return float(self.get("price_per_mw_year", 0.0))

    def revenues(self, ctx: EvalContext, proj: ProjectData) -> Components:
        out = super().revenues(ctx, proj)
        if proj.kind == "Line":
            return out
        mw = proj.new_mw * self.derating(ctx, proj)
        price = self.capacity_price()
        after = float(self.get("price_after", price))
        years = self.get("contract_years")
        life = ctx.life[proj.name]
        if years is None or int(years) >= life:
            out["capacity"] = price * mw
        else:
            out["capacity"] = [price * mw] * int(years) + [after * mw]
        return out

    def notes(self) -> list[str]:
        return [
            f"capacity price {self.capacity_price():,.0f} $/MW-yr x derated MW",
            f"contract years {self.get('contract_years')}, "
            f"then {float(self.get('price_after', self.capacity_price())):,.0f}",
        ]
