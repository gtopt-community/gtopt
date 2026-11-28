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

import fnmatch
from collections.abc import Callable, Mapping

from ..case_data import PKey, ProjectData, SystemData
from ..finance import crf
from ..peak import PeakHours, peak_hours
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
    #: Cost-based market: the variable cost is the audited cost declared
    #: to the CEN and sets the CMg, so fuel is not an independent stream;
    #: the risky quantity is the energy margin (CMg − cost) × dispatch.
    #: Fixed O&M, insurance and warranty are known % of capex: they are
    #: discounted with the capital (the project WACC of the annuity).
    stream_map = {
        "variable": "energy",
        "vom_extra": "energy",
        "green_tax": "energy",
        "fixed_om": "capital",
        "insurance": "capital",
        "warranty": "capital",
    }

    # ── sufficiency ─────────────────────────────────────────────────
    def sufficiency_factor(
        self, tech: str, kind: str, duration_h: float | None
    ) -> float:
        """Preliminary sufficiency / Pmax from the technology tables."""
        if kind == "Battery" or tech == "bess":
            return interp_factor(
                self.get("storage_sufficiency", {}), duration_h
            ) * float(self.get("storage_availability", 1.0))
        return self.by_tech("sufficiency_factors", tech)

    @property
    def from_case(self) -> bool:
        """``sufficiency_method: case`` — factors and peak from the case."""
        return self.get("sufficiency_method", "factors") == "case"

    def peaks(self, ctx: EvalContext) -> dict[int, PeakHours]:
        """Peak hours per scenario (cached)."""
        ckey = f"cl_peaks:{id(self)}"
        if ckey not in ctx.cache:
            ctx.cache[ckey] = peak_hours(
                ctx.sysd,
                int(self.get("peak_hours", 52)),
                self.p.get("peak_window"),
            )
        out: dict[int, PeakHours] = ctx.cache[ckey]
        return out

    def peak_demand(self, ctx: EvalContext) -> float:
        """*Demanda de punta* [MW]: ``case`` method = probability-weighted
        mean over scenarios of the mean of the peak hours; else the
        highest block load of the case."""
        if not self.from_case:
            return ctx.sysd.peak_demand_mw
        w = ctx.sysd.case.scenario_weight
        return sum(w.get(s, 0.0) * ph.demand_mw for s, ph in self.peaks(ctx).items())

    def _durations(self, ctx: EvalContext) -> dict[PKey, float]:
        if "cl_dur" not in ctx.cache:
            ctx.cache["cl_dur"] = {
                (p.scenario, p.stage, p.block): p.duration for p, _w in ctx.sysd.periods
            }
        out: dict[PKey, float] = ctx.cache["cl_dur"]
        return out

    def dry_scenarios(self, ctx: EvalContext) -> list[int]:
        """The ``dry_hydrologies`` scenarios with the least hydro energy —
        the CEN's hydro *potencia inicial* uses the 2 driest years of the
        hydrological statistic [ds62_nt]."""
        ckey = f"cl_dry:{id(self)}"
        if ckey in ctx.cache:
            dry: list[int] = ctx.cache[ckey]
            return dry
        hydro = set(self.get("hydro_technologies", ["hydro"]))
        dur = self._durations(ctx)
        energy = dict.fromkeys(ctx.sysd.scenarios, 0.0)
        for u in ctx.sysd.units:
            if ctx.params.technology(u.name, u.kind, u.elem_type) not in hydro:
                continue
            for k, q in u.gen.items():
                energy[k[0]] = energy.get(k[0], 0.0) + q * dur.get(k, 0.0)
        n = max(1, int(self.get("dry_hydrologies", 2)))
        dry = sorted(energy, key=lambda s: (energy[s], s))[:n]
        ctx.cache[ckey] = dry
        return dry

    def case_factor(
        self,
        ctx: EvalContext,
        tech: str,
        kind: str,
        duration_h: float | None,
        gen: Mapping[PKey, float],
        avail: Mapping[PKey, float],
        pmax: float,
    ) -> float:
        """Preliminary sufficiency / Pmax measured on the solved case.

        * hydro: mean output in the peak hours of the ``dry_hydrologies``
          driest scenarios / Pmax (NT: the 2 driest hydrological years);
        * PV / wind (``profile_technologies``): min(lowest annual capacity
          factor over the scenarios, mean capacity factor in the peak
          hours) from the availability profile (the dispatch when the unit
          has no profile);
          — when the case cannot resolve the peak hours (they fall in
          fewer than ``min_peak_blocks`` distinct blocks of a scenario,
          e.g. a load-duration block layout) and ``renewable_peak_rule``
          is ``auto`` (default), the lowest annual capacity factor alone;
          ``min`` always applies the CEN rule, ``annual`` never does;
        * storage and thermal: the tables (DS 70 transitory rule; fuel
          availability × (1 − IFOR), an assumption).
        """
        if kind == "Battery" or tech == "bess" or pmax <= 1e-9:
            return self.sufficiency_factor(tech, kind, duration_h)
        peaks = self.peaks(ctx)
        if tech in set(self.get("hydro_technologies", ["hydro"])):
            vals = [peaks[s].mean(gen) / pmax for s in self.dry_scenarios(ctx)]
            return min(1.0, sum(vals) / len(vals)) if vals else 0.0
        if tech in set(self.get("profile_technologies", ["solar_pv", "wind"])):
            if avail:
                series: Mapping[PKey, float] = avail
                default = 1.0
            else:
                series = {k: v / pmax for k, v in gen.items()}
                default = 0.0
            w = ctx.sysd.case.scenario_weight
            peak_cf = sum(
                w.get(s, 0.0) * ph.mean(series, default) for s, ph in peaks.items()
            )
            dur = self._durations(ctx)
            annual: dict[int, list[float]] = {}
            for k, d in dur.items():
                acc = annual.setdefault(k[0], [0.0, 0.0])
                acc[0] += series.get(k, default) * d
                acc[1] += d
            low = min((a / b for a, b in annual.values() if b > 0), default=0.0)
            if not self.peak_resolved(ctx):
                return max(0.0, low)
            return max(0.0, min(peak_cf, low))
        return self.sufficiency_factor(tech, kind, duration_h)

    def peak_blocks(self, ctx: EvalContext) -> int:
        """Fewest distinct blocks that hold a scenario's peak hours."""
        peaks = self.peaks(ctx)
        return min(
            (len({k[1:] for k in ph.weight}) for ph in peaks.values()),
            default=0,
        )

    def peak_resolved(self, ctx: EvalContext) -> bool:
        """Whether PV / wind are measured in the peak hours (see
        :meth:`case_factor`)."""
        rule = str(self.get("renewable_peak_rule", "auto"))
        if rule == "min":
            return True
        if rule == "annual":
            return False
        if rule != "auto":
            raise ValueError(f"renewable_peak_rule: unknown value {rule!r}")
        return self.peak_blocks(ctx) >= int(self.get("min_peak_blocks", 3))

    def unit_factor(self, name: str) -> float | None:
        """A unit's own preliminary sufficiency / Pmax from
        ``unit_sufficiency_factors`` ({fnmatch pattern: factor}; the
        first match wins), e.g. the plant's value in the CEN
        calculation; None when no pattern matches."""
        table = self.get("unit_sufficiency_factors", {}) or {}
        for pat, val in table.items():
            if fnmatch.fnmatchcase(name, str(pat)):
                return float(val)
        return None

    def _excluded(self, name: str) -> bool:
        pats = self.get("sufficiency_exclude", []) or []
        return any(fnmatch.fnmatchcase(name, pat) for pat in pats)

    def unit_preliminary(self, ctx: EvalContext) -> dict[str, float]:
        """Preliminary sufficiency [MW] of every unit of the case (cached)."""
        units = ctx.sysd.units
        ckey = f"cl_units:{id(self)}:{id(units)}:{len(units)}"
        if ckey in ctx.cache:
            cached: dict[str, float] = ctx.cache[ckey]
            return cached
        out: dict[str, float] = {}
        for u in ctx.sysd.units:
            if self._excluded(u.name):
                continue
            tech = ctx.tech.get(u.name) or ctx.params.technology(
                u.name, u.kind, u.elem_type
            )
            mw = u.available_mw if self.from_case else u.installed_mw
            f = self.unit_factor(u.name)
            if f is None and self.from_case:
                f = self.case_factor(
                    ctx, tech, u.kind, u.duration_h, u.gen, u.avail, mw
                )
            elif f is None:
                f = self.sufficiency_factor(tech, u.kind, u.duration_h)
            out[u.name] = mw * f
        ctx.cache[ckey] = out
        return out

    def preliminary(self, ctx: EvalContext, sysd: SystemData) -> float:
        """Σ preliminary sufficiency of every unit of the case [MW]."""
        del sysd
        return sum(self.unit_preliminary(ctx).values())

    def prorate_factor(self, ctx: EvalContext) -> float:
        mode = self.get("prorate", "case")
        if mode == "none":
            return 1.0
        if isinstance(mode, (int, float)):
            return float(mode)
        total = self.preliminary(ctx, ctx.sysd)
        if total <= 1e-9:
            return 1.0
        k = self.peak_demand(ctx) / total
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

    def _per_project(
        self,
        ctx: EvalContext,
        what: str,
        proj: ProjectData,
        fn: Callable[[EvalContext, ProjectData], float],
    ) -> float:
        """``fn(ctx, proj)`` computed once per project and evaluation.

        Sufficiency and the CEN guarantee are set ex ante, over every
        hydrology of the case (the driest scenarios, the worst month of
        the worst hydrology).  A per-scenario view of the project (the
        private CVaR, ``risk``) must get the same values, not ones
        measured on its own scenario only: the first call — on the whole
        project, by :func:`evaluate._evaluate_one` — fixes them.
        """
        ckey = f"cl_{what}:{id(self)}:{proj.kind}:{proj.name}"
        if ckey not in ctx.cache:
            ctx.cache[ckey] = float(fn(ctx, proj))
        val: float = ctx.cache[ckey]
        return val

    def project_factor(self, ctx: EvalContext, proj: ProjectData) -> float:
        """Preliminary sufficiency / MW of a project (its new MW)."""
        return self._per_project(ctx, "factor", proj, self._project_factor)

    def _project_factor(self, ctx: EvalContext, proj: ProjectData) -> float:
        tech = ctx.tech.get(proj.name, "thermal")
        own = self.unit_factor(proj.name)
        if own is not None:
            return own
        if not self.from_case:
            return self.sufficiency_factor(tech, proj.kind, proj.duration_h())
        gen = {r.key: r.gen for r in proj.rows if r.gen}
        avail = {r.key: r.avail for r in proj.rows if r.avail != 1.0}
        return self.case_factor(
            ctx, tech, proj.kind, proj.duration_h(), gen, avail, proj.new_mw
        )

    def preliminary_mw(self, ctx: EvalContext, proj: ProjectData) -> float:
        return proj.new_mw * self.project_factor(ctx, proj)

    def definitive_mw(self, ctx: EvalContext, proj: ProjectData) -> float:
        return self.preliminary_mw(ctx, proj) * self.prorate_factor(ctx)

    def sufficiency(self, ctx: EvalContext, proj: ProjectData) -> float:
        return self.definitive_mw(ctx, proj) * self.capacity_price_mw_year()

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
        """Price ceiling of the Chilean CMg [$/MWh] (``None`` = no cap).

        ``"case"``: the highest variable cost of any generator of the
        case; a number; ``"dispatched"`` is per period (see
        :meth:`period_cap`) and returns the case-wide ceiling here.
        """
        mode = self.p.get("scarcity_cap", "case")
        if mode is None or mode == "off":
            return None
        if isinstance(mode, (int, float)):
            return float(mode)
        return sysd.max_srmc if sysd.max_srmc > 0 else None

    def period_cap(self, sysd: SystemData, key: PKey) -> float | None:
        """Ceiling of one period: with ``scarcity_cap: "dispatched"`` the
        variable cost of the most expensive unit running in that period
        × (1 + ``scarcity_cap_margin``) — the CMg is the cost of the
        costliest unit dispatched; nodal loss factors lift it a little."""
        if self.p.get("scarcity_cap", "case") != "dispatched":
            return self.scarcity_cap(sysd)
        top = sysd.dispatched_srmc.get(key, 0.0)
        if top <= 0:
            return sysd.max_srmc if sysd.max_srmc > 0 else None
        return top * (1.0 + float(self.get("scarcity_cap_margin", 0.0)))

    def scarcity_rent(self, ctx: EvalContext, proj: ProjectData) -> float:
        """Σ max(λ − cap, 0)·(gen − dem)·h — the part Chile does not pay."""
        total = 0.0
        for r in proj.rows:
            cap = self.period_cap(ctx.sysd, r.key)
            if cap is not None:
                total += max(r.price - cap, 0.0) * r.net * r.hours
        return total

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
        """Required CEN payment guarantee [$] (DS 125 art. 157); fixed
        once per project over every hydrology (see :meth:`_per_project`)."""
        return self._per_project(ctx, "guarantee", proj, self._cen_guarantee)

    def _cen_guarantee(self, ctx: EvalContext, proj: ProjectData) -> float:
        """Required CEN payment guarantee [$] (DS 125 art. 157).

        ``months`` largest monthly energy deficits (contract withdrawals −
        own net injection, at CMg) of the hydrology (scenario) with the
        largest total, plus ``months`` × the monthly capacity deficit
        (contract − definitive sufficiency, at the node price).  The
        monthly deficits come from the case when its stages are calendar
        months (``monthly: auto``; ``off`` forces the old estimate);
        otherwise ``months × peak_month_factor × (mean monthly energy
        deficit⁺ + capacity deficit⁺)``.
        """
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
        capacity = max(
            (q - self.definitive_mw(ctx, proj))
            * self.capacity_price_kw_month()
            * 1000.0,
            0.0,
        )
        months = float(g.get("months", 3))
        sysd = ctx.sysd
        if g.get("monthly", "auto") != "off" and sysd.monthly_stages():
            by: dict[int, dict[int, float]] = {}
            for r in proj.rows:
                month = sysd.stage_month[r.key[1]]
                scen = by.setdefault(r.key[0], {})
                scen[month] = scen.get(month, 0.0) + r.price * (q - r.net) * r.dur * (
                    sysd.scale
                )
            n = int(round(months))
            worst = max(
                (
                    sum(sorted((max(v, 0.0) for v in m.values()), reverse=True)[:n])
                    for m in by.values()
                ),
                default=0.0,
            )
            return worst + months * capacity
        # withdrawals at CMg minus own net position at CMg, per month
        energy = sum(r.price * (q - r.net) * r.hours for r in proj.rows) / 12.0
        factor = float(g.get("peak_month_factor", 1.0))
        return months * factor * (max(energy, 0.0) + capacity)

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

    def details(self, ctx: EvalContext, proj: ProjectData) -> dict[str, float]:
        out = super().details(ctx, proj)
        if proj.kind == "Line":
            return out
        out.update(
            sufficiency_factor=self.project_factor(ctx, proj),
            sufficiency_preliminary_mw=self.preliminary_mw(ctx, proj),
            sufficiency_definitive_mw=self.definitive_mw(ctx, proj),
            prorate_factor=self.prorate_factor(ctx),
            peak_demand_mw=self.peak_demand(ctx),
            system_preliminary_mw=self.preliminary(ctx, ctx.sysd),
            capacity_price_usd_kw_month=self.capacity_price_kw_month(),
            cen_guarantee_usd=self.cen_guarantee_amount(ctx, proj),
        )
        if self.from_case:
            out["dry_scenarios"] = float(len(self.dry_scenarios(ctx)))
            out["peak_blocks"] = float(self.peak_blocks(ctx))
            out["peak_resolved"] = float(self.peak_resolved(ctx))
        return out

    def notes(self) -> list[str]:
        return [
            "energy: injections and withdrawals at the bus marginal cost;"
            f" scarcity cap: {self.p.get('scarcity_cap', 'case')}"
            " (prices above it are not paid in Chile)",
            f"capacity price {self.capacity_price_kw_month():.2f} $/kW-month"
            f" = {self.capacity_price_mw_year():,.0f} $/MW-yr",
            f"sufficiency pro-rating: {self.get('prorate', 'case')};"
            f" method: {self.get('sufficiency_method', 'factors')}",
            f"transmission: {self.get('transmission', 'regulated')} "
            f"(rate {self.get('regulated_rate')})",
        ]
