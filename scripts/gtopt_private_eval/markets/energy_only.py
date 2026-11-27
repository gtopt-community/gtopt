# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``energy_only``: nodal energy prices only (scarcity pricing included).

Revenue = Σ λ·(injection − withdrawal)·h, plus the reserve duals when the
case co-optimises reserves.  Scarcity rents are inside λ (the bus price
reaches the value of lost load / ``demand_fail_cost`` when load is
shed), as in an energy-only market with scarcity pricing or an ORDC
(Hogan 2005, 2013).  Lines are merchant (congestion rent).
"""

from __future__ import annotations

from .base import MarketModel


class EnergyOnly(MarketModel):
    """Energy-only market with scarcity pricing."""

    name = "energy_only"
    title = "Energy-only market (nodal prices, scarcity pricing)"

    def notes(self) -> list[str]:
        return [
            "revenue = energy at the bus marginal price (+ reserve duals)",
            "scarcity rents included in the price (demand_fail_cost)",
            f"transmission: {self.get('transmission', 'merchant')}",
        ]
