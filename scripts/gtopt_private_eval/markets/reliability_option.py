# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``reliability_option``: capacity premium with a strike-price refund.

A reliability option (Vázquez, Rivier & Pérez-Arriaga 2002; I-SEM, Italy)
is a call option sold by the unit: it receives the premium
``P_clear × derated MW`` and pays back, every hour, the difference
between the market price and the strike on its obligated (derated) MW:

``refund = Σ_t max(λ_t − K, 0) · MW_new · d_tech · h_t``

whether or not it is producing — the energy revenue above the strike is
exchanged for a stable premium.
"""

from __future__ import annotations

from ..case_data import ProjectData
from .base import Components, EvalContext
from .capacity_market import CapacityMarket


class ReliabilityOption(CapacityMarket):
    """Capacity premium minus the difference payments above the strike."""

    name = "reliability_option"
    title = "Reliability options (premium, refund above the strike price)"

    def strike(self) -> float:
        return float(self.get("strike_price", 500.0))

    def costs(self, ctx: EvalContext, proj: ProjectData) -> Components:
        if proj.kind == "Line":
            return {}
        mw = proj.new_mw * self.derating(ctx, proj)
        k = self.strike()
        refund = sum(max(r.price - k, 0.0) * r.hours for r in proj.rows) * mw
        return {"ro_refund": refund}

    def notes(self) -> list[str]:
        return [*super().notes(), f"strike price {self.strike():,.1f} $/MWh"]
