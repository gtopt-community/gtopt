# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``cl``: Chile's SEN remuneration scheme (see
``docs/methods/private-investor-evaluation-cl.md``).

Revenues

* **energy** — injections and withdrawals valued at the bus marginal
  cost (CEN *balance de transferencias de energía*): Σ CMg·(iny − ret)·h.
  Chile pays no scarcity rent: the CMg is the cost of the most expensive
  unit dispatched, and load shedding is rationing.  A gtopt price above
  ``scarcity_cap`` (``"case"`` = the highest generator variable cost of
  the case; a number; ``null`` = off) is a VOLL-type scarcity price, and
  its excess over the cap is removed as the negative component
  ``scarcity_excluded`` (Σ max(λ − cap, 0)·net·h).
* **sufficiency** (*pago por potencia de suficiencia*):
  ``P_suf,def × P_nudo,pot × 12 × 1000`` [$/yr] with
  ``P_suf,prelim = MW_new × f_tech`` (storage: ``f`` by duration) and
  ``P_suf,def = P_suf,prelim × D_peak / Σ_units P_suf,prelim`` — the
  capacity "pie" (peak demand × capacity price) shared in proportion to
  sufficiency.  ``prorate``: ``"case"`` (from the solved case),
  ``"none"`` (k = 1) or a number (an observed system factor).
* **sscc** — reserve duals when the case co-optimises reserves, else an
  assumption: ``MW_new × share_tech × price × 8760``.
* **co2** — only when the carbon price is in the cash flow (the green
  tax is *not* in the marginal cost in Chile; default off, reported as a
  sensitivity).

Costs

* **green_tax** — impuesto verde on own CO2 (5 $/t) for units above the
  25 kt/yr threshold.
* **transmission_injection** — transitional injection toll (Ley 20.936,
  art. 25 transitorio), $/MWh injected × the law's yearly adjustment
  factor (0 from 2034).  New injections are exempt: default 0.
* **cen_payment_guarantee** — DS 125 art. 156–157 as replaced by DS
  32/2025: to take part in the short-term market a company posts (bank
  guarantee, deposit certificate or standby LC) at least three months of
  its energy and capacity balance deficit: the 3 months of largest
  deficit between its generation at CMg and its contract withdrawals,
  plus the 3 months of largest deficit between its definitive
  sufficiency and its contracted peak withdrawals, both at the node
  price.  Modelled for a baseload contract of ``ppa_withdrawal_share`` ×
  the project's yearly energy: amount = months × peak factor × (mean
  monthly energy deficit⁺ + capacity deficit⁺) (``contract_mw`` gives
  the baseload contract directly); yearly cost = amount × ``fee_pct``.
  A merchant project or storage arbitrage (no withdrawals for final
  customers) posts none.
* **supply_contract_bond** — fee of the supply-tender performance bond
  (UF per GWh contracted, held ``months``) on the PPA share.
* **withdrawal_charges** — the prorata of operating overcosts, SSCC
  provision and green-tax compensations is paid by the withdrawals that
  supply final customers: ``rate × ppa_withdrawal_share × energy``
  (default share 0: a merchant project selling to the spot market).
"""

from __future__ import annotations

from ..case_data import ProjectData, SystemData
from ..finance import crf
from .base import Component, Components, EvalContext, MarketModel, interp_factor

KW_MONTH_TO_MW_YEAR = 12.0 * 1000.0


def peak_unit_price(pu: dict[str, float]) -> float:
    """CNE precio básico de la potencia [$/kW-month] from the peak unit.

    ``[(CTG·FRC_TG + CSE·FRC_SE + CLT·FRC_LT)·CF + Cfijo]·(1+MRT)·(1+FP)``
    with ``FRC`` the monthly capital recovery factor at the monthly
    equivalent of the annual rate over the life in months.
    """
    rate = float(pu.get("rate", 0.10))
    monthly = (1.0 + rate) ** (1.0 / 12.0) - 1.0

    def frc(life: float) -> float:
        return crf(monthly, 12.0 * life)

    inv = (
        float(pu["ctg_usd_kw"]) * frc(float(pu.get("life_ctg", 25)))
        + float(pu.get("cse_usd_kw", 0.0)) * frc(float(pu.get("life_cse", 41)))
        + float(pu.get("clt_usd_kw", 0.0)) * frc(float(pu.get("life_clt", 48)))
    )
    base = inv * float(pu.get("cf", 1.0)) + float(pu.get("cfijo_usd_kw_month", 0.0))
    return base * (1.0 + float(pu.get("mrt", 0.0))) * (1.0 + float(pu.get("fp", 0.0)))


class Chile(MarketModel):
    """Chilean SEN: energy at CMg + sufficiency + SSCC − charges."""

    name = "cl"
    title = "Chile SEN (costo marginal + potencia de suficiencia + SSCC)"

    # ── sufficiency ─────────────────────────────────────────────────
    def sufficiency_factor(
        self, tech: str, kind: str, duration_h: float | None
    ) -> float:
        if kind == "Battery" or tech == "bess":
            return interp_factor(
                self.get("storage_sufficiency", {}), duration_h
            ) * float(self.get("storage_availability", 1.0))
        return self.by_tech("sufficiency_factors", tech)

    def preliminary(self, ctx: EvalContext, sysd: SystemData) -> float:
        """Σ preliminary sufficiency of every unit of the case [MW]."""
        total = 0.0
        for u in sysd.units:
            tech = ctx.tech.get(u.name) or ctx.params.technology(u.name, u.kind)
            total += u.installed_mw * self.sufficiency_factor(
                tech, u.kind, u.duration_h
            )
        return total

    def prorate_factor(self, ctx: EvalContext) -> float:
        mode = self.get("prorate", "case")
        if mode == "none":
            return 1.0
        if isinstance(mode, (int, float)):
            return float(mode)
        total = self.preliminary(ctx, ctx.sysd)
        if total <= 1e-9:
            return 1.0
        k = ctx.sysd.peak_demand_mw / total
        cap = self.get("prorate_cap")
        return k if cap is None else min(k, float(cap))

    def capacity_price_kw_month(self) -> float:
        if self.get("capacity_price_mode", "value") == "peak_unit":
            return peak_unit_price(self.get("peak_unit", {}))
        return float(self.get("capacity_price_usd_kw_month", 0.0))

    def capacity_price_mw_year(self) -> float:
        return (
            self.capacity_price_kw_month()
            * KW_MONTH_TO_MW_YEAR
            * float(self.get("capacity_price_factor", 1.0))
        )

    def sufficiency(self, ctx: EvalContext, proj: ProjectData) -> float:
        tech = ctx.tech.get(proj.name, "thermal")
        prelim = proj.new_mw * self.sufficiency_factor(
            tech, proj.kind, proj.duration_h()
        )
        return prelim * self.prorate_factor(ctx) * self.capacity_price_mw_year()

    # ── SSCC ────────────────────────────────────────────────────────
    def sscc(self, ctx: EvalContext, proj: ProjectData) -> float:
        tech = ctx.tech.get(proj.name, "thermal")
        tab = self.get("sscc", {}) or {}
        row = tab.get(tech, tab.get("default", {})) or {}
        share = float(row.get("share", 0.0))
        price = float(row.get("price_usd_mw_h", 0.0))
        return proj.new_mw * share * price * 8760.0

    # ── scarcity ────────────────────────────────────────────────────
    def scarcity_cap(self, sysd: SystemData) -> float | None:
        """Price ceiling of the Chilean CMg [$/MWh] (``None`` = no cap)."""
        mode = self.p.get("scarcity_cap", "case")
        if mode is None or mode == "off":
            return None
        if isinstance(mode, (int, float)):
            return float(mode)
        return sysd.max_srmc if sysd.max_srmc > 0 else None

    def scarcity_rent(self, ctx: EvalContext, proj: ProjectData) -> float:
        """Σ max(λ − cap, 0)·(gen − dem)·h — the part Chile does not pay."""
        cap = self.scarcity_cap(ctx.sysd)
        if cap is None:
            return 0.0
        return sum(max(r.price - cap, 0.0) * r.net * r.hours for r in proj.rows)

    # ── interface ───────────────────────────────────────────────────
    def revenues(self, ctx: EvalContext, proj: ProjectData) -> Components:
        out = super().revenues(ctx, proj)
        if proj.kind == "Line":
            return out
        scarcity = self.scarcity_rent(ctx, proj)
        if abs(scarcity) > 1e-6:
            out["scarcity_excluded"] = -scarcity
        out["sufficiency"] = self.sufficiency(ctx, proj)
        if not ctx.sysd.has_reserves:
            sscc = self.sscc(ctx, proj)
            if sscc:
                out["sscc"] = sscc
        return out

    def toll(self, ctx: EvalContext, proj: ProjectData) -> Component:
        """Transitional injection toll × the law's yearly adjustment factor."""
        rate = float(self.get("injection_toll_usd_mwh", 0.0))
        if not rate:
            return 0.0
        energy = proj.energy_mwh()
        cod = int(self.get("cod_year", 2027))
        table = {
            int(k): float(v) for k, v in (self.get("toll_factors", {}) or {}).items()
        }
        last = max(table) if table else cod
        out: list[float] = []
        for y in range(ctx.life[proj.name]):
            year = cod + y
            out.append(rate * energy * table.get(year, 0.0 if year > last else 1.0))
            if year > last:
                break
        return out

    def cen_guarantee_amount(self, ctx: EvalContext, proj: ProjectData) -> float:
        """Required CEN payment guarantee [$] (DS 125 art. 157)."""
        g = self.get("cen_guarantee", {}) or {}
        if "amount_usd" in g:
            return float(g["amount_usd"])
        share = float(self.get("ppa_withdrawal_share", 0.0))
        hours = sum(r.hours for r in proj.rows)
        if hours <= 0:
            return 0.0
        if g.get("contract_mw") is not None:  # baseload contract [MW]
            q = float(g["contract_mw"])
        else:
            q = share * proj.energy_mwh() / hours
        if q <= 0:
            return 0.0
        # withdrawals at CMg minus own net position at CMg, per month
        energy = sum(r.price * (q - r.net) * r.hours for r in proj.rows) / 12.0
        tech = ctx.tech.get(proj.name, "thermal")
        suff = (
            proj.new_mw
            * self.sufficiency_factor(tech, proj.kind, proj.duration_h())
            * self.prorate_factor(ctx)
        )
        capacity = (q - suff) * self.capacity_price_kw_month() * 1000.0
        months = float(g.get("months", 3))
        factor = float(g.get("peak_month_factor", 1.0))
        return months * factor * (max(energy, 0.0) + max(capacity, 0.0))

    def supply_bond(self, ctx: EvalContext, proj: ProjectData) -> Component:
        """Fee of the supply-contract performance bond [$ / yr per year].

        ``uf_per_gwh`` × contracted GWh/yr × ``uf_usd`` × ``fee_pct``
        during the first ``months`` of supply (CNE supply-tender bases).
        """
        share = float(self.get("ppa_withdrawal_share", 0.0))
        b = self.get("supply_contract_bond", {}) or {}
        fee = float(b.get("fee_pct", 0.0))
        if share <= 0 or not fee:
            return 0.0
        gwh = share * proj.energy_mwh() / 1000.0
        cost = float(b.get("uf_per_gwh", 0.0)) * gwh * float(b.get("uf_usd", 0.0))
        cost *= fee
        months = float(b.get("months", 0.0))
        full, part = int(months // 12), (months % 12) / 12.0
        years = [cost] * full + ([cost * part] if part else [])
        del ctx
        return years + [0.0] if years else 0.0

    def costs(self, ctx: EvalContext, proj: ProjectData) -> Components:
        out: Components = {}
        if proj.kind == "Line":
            return out
        fee = float((self.get("cen_guarantee", {}) or {}).get("fee_pct", 0.0))
        amount = self.cen_guarantee_amount(ctx, proj)
        if amount and fee:
            out["cen_payment_guarantee"] = amount * fee
        bond = self.supply_bond(ctx, proj)
        if bond:
            out["supply_contract_bond"] = bond
        gt = self.get("green_tax", {}) or {}
        own_t = ctx.own_rate(proj) * proj.energy_mwh()
        if own_t >= float(gt.get("threshold_t_year", 25000.0)) and own_t > 0:
            out["green_tax"] = own_t * float(gt.get("price_usd_t", 5.0))
        toll = self.toll(ctx, proj)
        if toll:
            out["transmission_injection"] = toll
        share = float(self.get("ppa_withdrawal_share", 0.0))
        rate = float(self.get("withdrawal_charges_usd_mwh", 0.0))
        if share and rate:
            out["withdrawal_charges"] = rate * share * proj.energy_mwh()
        return out

    def notes(self) -> list[str]:
        return [
            "energy: injections and withdrawals at the bus marginal cost;"
            f" scarcity cap: {self.p.get('scarcity_cap', 'case')}"
            " (prices above it are not paid in Chile)",
            f"capacity price {self.capacity_price_kw_month():.2f} $/kW-month"
            f" = {self.capacity_price_mw_year():,.0f} $/MW-yr",
            f"sufficiency pro-rating: {self.get('prorate', 'case')}",
            f"transmission: {self.get('transmission', 'regulated')} "
            f"(rate {self.get('regulated_rate')})",
        ]
