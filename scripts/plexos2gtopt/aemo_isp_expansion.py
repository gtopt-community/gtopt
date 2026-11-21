"""AEMO ISP PLEXOS model → gtopt multi-year **expansion** case (prototype).

Builds a generation-expansion planning case from the AEMO ISP DLT XML
with **monthly stages** on top of the weekly dispatch converter
(:mod:`plexos2gtopt.aemo_isp`):

* Time: one stage per calendar month of the planning (financial) years
  ``first_fy .. first_fy + years - 1`` (FY ending year, e.g. 2028 =
  2027-28).  Each stage is one **representative day** of 24 hour-of-day
  blocks whose duration is the number of days of the month (the block
  value is the month mean of that hour).  Energy, cost, reservoir
  balances and capex (``stage.duration`` = month hours) are exact
  monthly totals; batteries use gtopt's ``daily_cycle`` (the default),
  which rescales the energy balance by ``24 / stage_duration`` and
  closes every stage with ``efin == eini`` — a daily cycle repeated over
  the month.  ``--rep-days 2`` splits every month into a **high** and a
  **low net-load** representative day (daily demand − available wind
  and solar, split at the month median), one stage each, block duration
  = the days of the group; energy and costs stay exact month totals.
* Phases: none (one monolithic LP) by default; ``--phases month`` /
  ``year`` writes one phase per month / per FY for the SDDP method, in
  which case installed capacity (``capainst`` / ``capacost``) and the
  reservoir volumes are the state variables.
* Existing fleet: every month is converted with the weekly converter on
  a window = the whole month, so commissioning, retirements (dated
  ``Units``), dated line limits (CDP network), fuel prices and traces
  follow the month; the hourly schedules are folded to hour-of-day
  means.
* Candidates: every generator with ``Units = 0`` and ``Max Units Built``
  > 0 on some day of the horizon (new-entrant REZ / ODR / DREZ / gas /
  biomass and the ``Network Augmentation`` pseudo-generators — REZ
  transmission, DNSP and system-strength options, which carry no energy
  and only enter constraints).  gtopt expansion fields: ``capacity`` 0,
  ``expcap`` = ``Max Capacity`` (1 MW in the ISP), ``expmod`` = ``Max
  Units Built`` on the first month of each FY from its first year
  (builds happen at FY start, as in the yearly PLEXOS LT plan),
  ``capmax`` = cumulative ``Max Units Built``; ``annual_capcost`` =
  ``Build Cost``·1000·CRF(``WACC``, ``Economic Life``) + ``FO&M``·1000
  [$/MW-yr], the build-year cost being locked in by gtopt's ``capacost``
  accumulator (PLEXOS ``Always Annualize Build Cost``, no end effects).
  Energy candidates carry a ``GeneratorProfile`` (rating trace × (1 −
  Aux Incr), hour-of-day mean per month).
* PLEXOS constraints: the weekly engine in expansion mode — candidate
  capacity terms become ``generator(...).capainst`` and the pure
  capacity / build constraints (``RHS Year`` in MW) become stage-scope
  user constraints; per-block constraints keep their per-block RHS
  (hour-of-day mean), ``RHS Day`` constraints become a stage energy sum
  (× days), period constraints keep the month share.

Not modelled yet (see the scoping report): battery / pumped-hydro
expansion (gtopt expands batteries on the energy axis only), inter-
regional transmission expansion (the CDP 4 augmentations are input),
yearly energy targets as cross-month accumulators, retirements as
decisions, unit outages, purchasers / hydrogen.

Run as ``python -m plexos2gtopt.aemo_isp_expansion --help``.
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import aemo_isp_constraints as isp_c
from .aemo_isp import (
    IspCase,
    IspCaseOptions,
    IspResolver,
    _gen_type,
    _json_default,
    _single_child,
    build_dispatch_case,
)
from .isp_traces import TraceLocator
from .plexos_xml import PlexosDb, load_xml

logger = logging.getLogger(__name__)

#: Marker RHS of a stage where a merged constraint is absent (internal; the
#: written constraint gets a per-stage ``active_stages`` schedule instead).
INACTIVE_RHS = 1.0e9

#: Default discount rate when the XML has no ``LT Plan`` (AEMO: 7 % real).
DEFAULT_DISCOUNT_RATE = 0.07

#: Battery fields that become per-(stage, block) inline matrices.
_BATTERY_TB_FIELDS = ("emin", "emax", "pmax_charge", "pmax_discharge")

_OP_RE = re.compile(r"\s(<=|>=|=)\s(\S+)\s*$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def month_starts(first_fy: int, years: int) -> list[date]:
    """First day of every month of FY ``first_fy`` (ending June) onwards."""
    out: list[date] = []
    for k in range(12 * years):
        m = 7 + k
        y = first_fy - 1 + (m - 1) // 12
        out.append(date(y, (m - 1) % 12 + 1, 1))
    return out


def days_in_month(day: date) -> int:
    """Number of days of ``day``'s month."""
    return calendar.monthrange(day.year, day.month)[1]


def hod_mean(arr: np.ndarray, days: int) -> np.ndarray:
    """Hour-of-day mean (24 values) of an hourly array of ``days`` days."""
    a = np.asarray(arr, dtype=float)
    return np.nanmean(a.reshape(days, 24), axis=0)


def day_masks(net_load: np.ndarray, rep_days: int) -> list[np.ndarray]:
    """Split the days of a month into ``rep_days`` representative-day groups.

    ``rep_days = 1``: every day.  ``rep_days = 2``: **high / low net-load**
    days (daily demand minus available wind + solar, split at the month
    median).  Net load, not weekday / weekend, drives what an expansion
    plan has to cover — firming and storage are sized by the
    low-renewable, high-demand days that a single month-mean day averages
    away.  Both groups are kept non-empty.
    """
    nd = len(net_load)
    if rep_days <= 1 or nd < 2:
        return [np.ones(nd, dtype=bool)]
    if rep_days != 2:
        raise ValueError(f"rep_days must be 1 or 2, got {rep_days}")
    order = np.argsort(-np.asarray(net_load, dtype=float), kind="stable")
    high = np.zeros(nd, dtype=bool)
    high[order[: (nd + 1) // 2]] = True
    return [high, ~high]


def rep_mean(arr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Hour-of-day mean (24 values) over the days selected by ``mask``."""
    nd = len(mask)
    a = np.asarray(arr, dtype=np.float64)[: nd * 24].reshape(nd, 24)
    return np.nanmean(a[mask], axis=0)


def life_factor(life: float, remaining_years: float) -> float:
    """Annuity scale when ``life`` ends before the horizon (else 1)."""
    if life <= 0 or remaining_years <= life:
        return 1.0
    return life / remaining_years


def crf(rate: float, life: float) -> float:
    """Capital recovery factor (annuity per unit of present value)."""
    if life <= 0:
        return 1.0
    if abs(rate) < 1e-12:
        return 1.0 / life
    return rate / (1.0 - (1.0 + rate) ** (-life))


def annual_capcost(
    build_cost_kw: float, wacc_pct: float, life: float, fom_kw_yr: float
) -> float:
    """Annualised capacity cost [$/MW-yr] from PLEXOS $/kW data."""
    return 1000.0 * (build_cost_kw * crf(wacc_pct / 100.0, life) + fom_kw_yr)


def split_expression(expr: str) -> tuple[str, str, float]:
    """``"LHS op RHS"`` → (LHS, op, RHS)."""
    m = _OP_RE.search(expr)
    if m is None:
        raise ValueError(f"cannot split constraint expression: {expr!r}")
    return expr[: m.start()].strip(), m.group(1), float(m.group(2))


def lt_plan_discount_rate(xml: Path) -> float | None:
    """``LT Plan`` ``Discount Rate`` attribute [p.u.] (None if absent)."""
    import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel

    root = ET.parse(xml).getroot()  # noqa: S314 - local trusted model file
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    cls = {
        c.findtext(ns + "class_id"): c.findtext(ns + "name")
        for c in root.iter(ns + "t_class")
    }
    lt_objs = {
        o.findtext(ns + "object_id")
        for o in root.iter(ns + "t_object")
        if cls.get(o.findtext(ns + "class_id")) == "LT Plan"
    }
    attrs = {
        a.findtext(ns + "attribute_id")
        for a in root.iter(ns + "t_attribute")
        if a.findtext(ns + "name") == "Discount Rate"
        and cls.get(a.findtext(ns + "class_id")) == "LT Plan"
    }
    for ad in root.iter(ns + "t_attribute_data"):
        if (
            ad.findtext(ns + "object_id") in lt_objs
            and ad.findtext(ns + "attribute_id") in attrs
        ):
            return float(ad.findtext(ns + "value") or 0.0) / 100.0
    return None


# ---------------------------------------------------------------------------
# options / candidates
# ---------------------------------------------------------------------------


@dataclass
class ExpansionOptions:
    """User options for :func:`build_expansion_case`."""

    xml: Path
    trace_roots: list[Path]
    first_fy: int = 2028
    years: int = 3
    name: str = "isp_expansion"
    #: "none" (monolithic), "month" or "year" (one SDDP phase each)
    phases: str = "none"
    #: representative days per month (1: month mean; 2: high / low
    #: net-load days), one stage each
    rep_days: int = 1
    discount_rate: float | None = None
    include_hydro: bool = True
    include_batteries: bool = True
    constraints: bool = True
    demand_fail_cost: float | None = None
    #: slack cost for the hard yearly capacity constraints (PLEXOS Penalty
    #: Price -1); None keeps them hard.  SDDP needs them soft: a month
    #: cannot repair the capacity a previous FY built (feasibility cuts on
    #: a capacity state converge one coordinate per iteration).
    capacity_penalty: float | None = None
    #: add battery / pumped-hydro storage candidates (power + energy)
    battery_candidates: bool = True
    #: restrict candidates to categories starting with one of these
    candidate_prefixes: tuple[str, ...] = ()


@dataclass
class Candidate:
    """One expansion candidate generator."""

    name: str
    oid: int
    bus: str
    category: str
    unit_mw: float
    wacc: float
    life: float
    fom: float
    vom: float
    aux: float
    heat_rate: float | None
    fuel_oid: int | None
    energy: bool  # False: network-augmentation capacity token
    #: per stage: cumulative max units, build cost $/kW, buildable flag
    max_units: list[float] = field(default_factory=list)
    build_cost: list[float] = field(default_factory=list)
    #: "generator" or "battery" (battery / pumped-hydro storage candidate)
    kind: str = "generator"
    #: storage: energy per unit [MWh] (``Capacity``) and efficiencies
    unit_mwh: float = 0.0
    eff_in: float = 1.0
    eff_out: float = 1.0


def find_candidates(
    res: IspResolver, months: list[date], prefixes: tuple[str, ...] = ()
) -> list[Candidate]:
    """Generators with ``Units`` 0 and ``Max Units Built`` > 0 in the horizon."""
    gen_node = _single_child(res, "Generator", "Nodes")
    gen_fuel = _single_child(res, "Generator", "Fuels")
    nodes = {o.object_id: o.name for o in res.objects("Node")}
    out: list[Candidate] = []
    for o in sorted(res.objects("Generator"), key=lambda o: o.name):
        cat = res.category(o)
        if cat.startswith("Load Subtracters"):
            continue
        if prefixes and not cat.startswith(prefixes):
            continue
        mub = [
            res.scalar(o.object_id, "Max Units Built", d, 0.0) or 0.0 for d in months
        ]
        if max(mub) <= 0:
            continue
        if (res.scalar(o.object_id, "Units", months[0], 0.0) or 0.0) > 0:
            continue
        bus = nodes.get(gen_node.get(o.object_id, -1))
        if bus is None:
            logger.warning("candidate %s has no node, skipped", o.name)
            continue
        unit = res.scalar(o.object_id, "Max Capacity", months[-1], 1.0) or 1.0
        cost = [res.scalar(o.object_id, "Build Cost", d, 0.0) or 0.0 for d in months]
        energy = not cat.startswith("Network Augmentation")
        out.append(
            Candidate(
                name=o.name,
                oid=o.object_id,
                bus=bus,
                category=cat,
                unit_mw=unit,
                wacc=res.scalar(o.object_id, "WACC", months[0], 7.0) or 7.0,
                life=res.scalar(o.object_id, "Economic Life", months[0], 30.0) or 30.0,
                fom=res.scalar(o.object_id, "FO&M Charge", months[0], 0.0) or 0.0,
                vom=res.scalar(o.object_id, "VO&M Charge", months[0], 0.0) or 0.0,
                aux=(res.scalar(o.object_id, "Aux Incr", months[0], 0.0) or 0.0)
                / 100.0,
                heat_rate=res.scalar(o.object_id, "Heat Rate", months[0]),
                fuel_oid=gen_fuel.get(o.object_id),
                energy=energy,
                max_units=mub,
                build_cost=cost,
            )
        )
    return out


def find_battery_candidates(
    res: IspResolver, months: list[date], prefixes: tuple[str, ...] = ()
) -> list[Candidate]:
    """Batteries / PHES with ``Units`` 0 and ``Max Units Built`` > 0.

    One PLEXOS unit = ``Max Power`` MW and ``Capacity`` MWh; ``Build Cost``
    is $/kW of power, so the whole annuity sits on the power axis and the
    energy axis follows through ``energy_power_ratio`` (fixed duration).
    """
    bat_node = _single_child(res, "Battery", "Nodes")
    nodes = {o.object_id: o.name for o in res.objects("Node")}
    out: list[Candidate] = []
    for o in sorted(res.objects("Battery"), key=lambda o: o.name):
        cat = res.category(o)
        if prefixes and not cat.startswith(prefixes):
            continue
        mub = [
            res.scalar(o.object_id, "Max Units Built", d, 0.0) or 0.0 for d in months
        ]
        if max(mub) <= 0:
            continue
        if (res.scalar(o.object_id, "Units", months[0], 0.0) or 0.0) > 0:
            continue
        bus = nodes.get(bat_node.get(o.object_id, -1))
        if bus is None:
            logger.warning("battery candidate %s has no node, skipped", o.name)
            continue

        def val(prop: str, default: float, oid: int = o.object_id) -> float:
            v = res.scalar(oid, prop, months[0], default)
            return default if v is None else float(v)

        out.append(
            Candidate(
                name=o.name,
                oid=o.object_id,
                bus=bus,
                category=cat,
                unit_mw=val("Max Power", 1.0) or 1.0,
                wacc=val("WACC", 7.0),
                life=val("Economic Life", 20.0),
                fom=val("FO&M Charge", 0.0),
                vom=val("VO&M Charge", 0.0),
                aux=0.0,
                heat_rate=None,
                fuel_oid=None,
                energy=True,
                max_units=mub,
                build_cost=[
                    res.scalar(o.object_id, "Build Cost", d, 0.0) or 0.0 for d in months
                ],
                kind="battery",
                unit_mwh=val("Capacity", 0.0),
                eff_in=val("Charge Efficiency", 100.0) / 100.0,
                eff_out=val("Discharge Efficiency", 100.0) / 100.0,
            )
        )
    return out


def fy_start_stages(months: list[date], rep_days: int = 1) -> list[bool]:
    """Per stage: first stage of a FY (July, or the horizon start)."""
    return [
        k == 0 and (m == 0 or d.month == 7)
        for m, d in enumerate(months)
        for k in range(rep_days)
    ]


def build_stages(months: list[date], cand: Candidate, rep_days: int = 1) -> list[bool]:
    """Stages where ``cand`` may build: FY start (July) or horizon start."""
    return [
        f and cand.max_units[s // rep_days] > 0
        for s, f in enumerate(fy_start_stages(months, rep_days))
    ]


def capainst_stages(
    months: list[date], cand: Candidate, rep_days: int = 1
) -> list[bool]:
    """Stages where gtopt creates the candidate's ``capainst`` column."""
    out: list[bool] = []
    seen = False
    for b in build_stages(months, cand, rep_days):
        seen = seen or b
        out.append(seen)
    return out


# ---------------------------------------------------------------------------
# case builder
# ---------------------------------------------------------------------------


@dataclass
class _UcVariant:
    """One distinct LHS of a merged user constraint."""

    base: dict[str, Any]
    op: str
    rhs: dict[int, Any] = field(default_factory=dict)  # stage → float | (24,)


def _fold_uc(
    uc: dict[str, Any], rhs_h: np.ndarray | None, mask: np.ndarray
) -> tuple[str, dict[str, Any], str, Any]:
    """Window user constraint → (key, base dict, op, stage rhs).

    ``mask`` selects the days of the month the stage represents.
    """
    days = int(np.sum(mask))
    lhs, op, rhs = split_expression(uc["expression"])
    base = {
        k: v for k, v in uc.items() if k not in ("uid", "expression", "rhs", "_rhs")
    }
    if uc.get("daily_sum"):
        # RHS Day → one stage energy sum over the representative day × days
        base.pop("daily_sum", None)
        base["scope"] = "stage"
        weight = "" if uc.get("constraint_type") == "raw" else "dur[b] * "
        lhs = f"sum{{b in stage}} {weight}({lhs})"
        mean_day = float(np.mean(rep_mean(rhs_h, mask))) if rhs_h is not None else rhs
        return f"stage|{lhs}", {**base, "_lhs": lhs}, op, mean_day * days
    if uc.get("scope") == "stage":
        return f"stage|{lhs}", {**base, "_lhs": lhs}, op, rhs
    prof = rep_mean(rhs_h, mask) if rhs_h is not None else np.full(24, rhs)
    return f"block|{lhs}", {**base, "_lhs": lhs}, op, prof


def _fy_capacity_rhs(
    rhs: np.ndarray, fy_start: list[bool], op: str, off: float
) -> np.ndarray:
    """Yearly capacity constraint → one row at each FY's first stage.

    Installed capacity only changes at the FY build stage, so the PLEXOS
    ``RHS Year`` constraint is enforced once per FY with the tightest
    monthly RHS (the existing fleet folded into it may change during the
    year); the other months are inactive.  This keeps every month
    feasible for any state the build stage hands over — needed by SDDP,
    where a later month cannot change the capacity decided in July.
    """
    out = np.full_like(rhs, off)
    pick = np.min if op == "<=" else np.max
    for s, first in enumerate(fy_start):
        if not first:
            continue
        fy = [s]
        for k in range(s + 1, len(fy_start)):
            if fy_start[k]:
                break
            fy.append(k)
        live = [k for k in fy if abs(rhs[k, 0]) < abs(off)]
        if live:
            out[s] = pick(rhs[live, 0])
    return out


def _battery_candidate(
    c: Candidate,
    bstage: list[bool],
    nrep: int,
    opts: ExpansionOptions,
    fy_index: list[int],
) -> dict[str, Any]:
    """gtopt Battery with power + energy expansion for a storage candidate.

    Units of ``Max Power`` MW / ``Capacity`` MWh; the annuity
    (``Build Cost`` $/kW of power) is charged on the power axis and the
    energy axis is tied to it by ``energy_power_ratio`` = duration [h].
    """
    n_st = len(bstage)
    mub = [c.max_units[s // nrep] for s in range(n_st)]
    pmax = max(mub) * c.unit_mw
    cap = [
        round(
            annual_capcost(c.build_cost[s // nrep], c.wacc, c.life, c.fom)
            * life_factor(c.life, opts.years - fy_index[s]),
            3,
        )
        for s in range(n_st)
    ]
    expmod = [mub[s] if bstage[s] else 0.0 for s in range(n_st)]
    return {
        "name": c.name,
        "bus": c.bus,
        "type": "candidate_" + ("phes" if "PHES" in c.category else "battery"),
        "description": f"storage expansion candidate [{c.category}]",
        "input_efficiency": c.eff_in,
        "output_efficiency": c.eff_out,
        "emin": 0.0,
        "emax": max(mub) * c.unit_mwh,
        "pmax_charge": pmax,
        "pmax_discharge": pmax,
        "capacity": 0.0,
        "expcap": c.unit_mwh,
        "expmod": expmod,
        "capmax": [m * c.unit_mwh for m in mub],
        "annual_capcost": 0.0,
        "power_capacity": 0.0,
        "power_expcap": c.unit_mw,
        "power_expmod": expmod,
        "power_capmax": [m * c.unit_mw for m in mub],
        "power_annual_capcost": cap,
        "energy_power_ratio": c.unit_mwh / c.unit_mw,
    }


def _daily_net_load(
    system: dict[str, Any], ws: dict[tuple[str, str], Any], nd: int
) -> np.ndarray:
    """Daily energy: demand minus available wind + solar of the month window."""
    net = np.zeros(nd)
    for d in system["demand_array"]:
        arr = ws.get(("Demand", "lmax"), {}).get(d["uid"])
        if arr is not None:
            net += np.nansum(np.asarray(arr, float)[: nd * 24].reshape(nd, 24), 1)
    for g in system["generator_array"]:
        if g.get("type") not in ("solar", "wind"):
            continue
        arr = ws.get(("Generator", "pmax"), {}).get(g["uid"])
        if arr is not None:
            net -= np.nansum(np.asarray(arr, float)[: nd * 24].reshape(nd, 24), 1)
    return net


def build_expansion_case(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    opts: ExpansionOptions, db: PlexosDb | None = None
) -> IspCase:
    """Build the monthly-stage expansion case for ``opts``."""
    db = db or load_xml(opts.xml)
    roots = list(opts.trace_roots) or [Path(opts.xml).parent]
    res = IspResolver(db, TraceLocator(roots))
    months = month_starts(opts.first_fy, opts.years)
    nrep = max(1, opts.rep_days)
    n_st = len(months) * nrep
    ndays = [days_in_month(d) for d in months]
    #: days represented by each stage (filled per month below)
    stage_days = [0] * n_st
    month_masks: list[list[np.ndarray]] = []
    fy_start = fy_start_stages(months, nrep)
    fy_index = list(np.cumsum(fy_start) - 1)
    cands = find_candidates(res, months, opts.candidate_prefixes)
    if opts.include_batteries and opts.battery_candidates:
        cands += find_battery_candidates(res, months, opts.candidate_prefixes)
    cap_on = {c.name: capainst_stages(months, c, nrep) for c in cands}
    stats: dict[str, Any] = defaultdict(int)
    sched: dict[tuple[str, str], dict[int, np.ndarray]] = defaultdict(dict)

    rate = opts.discount_rate
    if rate is None:
        rate = (
            lt_plan_discount_rate(Path(opts.xml)) if Path(opts.xml).exists() else None
        )
        rate = DEFAULT_DISCOUNT_RATE if rate is None else rate

    # ---- per-month dispatch windows ------------------------------------
    gens: dict[str, dict[str, Any]] = {}
    gen_pmax: dict[str, np.ndarray] = {}
    gen_gcost: dict[str, np.ndarray] = {}
    dems: dict[str, dict[str, Any]] = {}
    dem_lmax: dict[str, np.ndarray] = {}
    lines: dict[str, dict[str, Any]] = {}
    line_t: dict[tuple[str, str], np.ndarray] = {}
    bats: dict[str, dict[str, Any]] = {}
    bat_tb: dict[tuple[str, str], np.ndarray] = {}
    hydro: dict[str, list[dict[str, Any]]] = {}
    inflow: dict[str, np.ndarray] = {}
    ucs: dict[str, dict[str, _UcVariant]] = defaultdict(dict)
    buses: list[dict[str, Any]] = []
    dvars: dict[str, dict[str, Any]] = {}
    gen_obj = {o.name: o for o in res.objects("Generator")}
    bat_obj = {o.name: o for o in res.objects("Battery")}
    #: existing installed capacity per stage (Units × Max Capacity / Power)
    inst: dict[str, tuple[str, str, str, np.ndarray]] = {}
    voll = opts.demand_fail_cost
    groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for m, (day0, nd) in enumerate(zip(months, ndays)):
        s0 = m * nrep
        names = frozenset(c.name for c in cands if cap_on[c.name][s0])
        wopts = IspCaseOptions(
            xml=opts.xml,
            trace_roots=roots,
            start=day0,
            days=nd,
            name=opts.name,
            include_hydro=opts.include_hydro,
            include_batteries=opts.include_batteries,
            demand_fail_cost=opts.demand_fail_cost,
            isp=isp_c.ConstraintOptions(
                constraints=opts.constraints,
                purchasers=False,
                emissions=False,
                fuels=False,
                rez_buses=False,
                long_periods="prorate",
                expansion=True,
            ),
            expansion_names=names,
        )
        wk = build_dispatch_case(wopts, db=db)
        system = wk.planning["system"]
        voll = wk.planning["options"]["model_options"]["demand_fail_cost"]
        ws = wk.schedules
        masks = day_masks(_daily_net_load(system, ws, nd), nrep)
        month_masks.append(masks)
        for k, mk in enumerate(masks):
            stage_days[s0 + k] = int(mk.sum())
        stg = range(s0, s0 + nrep)
        if m == 0:
            buses = system["bus_array"]
            hydro = {
                k: system.get(k, [])
                for k in (
                    "junction_array",
                    "reservoir_array",
                    "waterway_array",
                    "turbine_array",
                    "flow_array",
                )
            }
        for k, v in wk.stats.get("constraint_groups", {}).items():
            for r, n in v.items():
                groups[k][r] += n
        for g in system["generator_array"]:
            name = g["name"]
            o = gen_obj[name]
            inst.setdefault(
                name, ("Generator", res.category(o), g.get("type", ""), np.zeros(n_st))
            )[3][stg] = (res.scalar(o.object_id, "Units", day0, 0.0) or 0.0) * (
                res.scalar(o.object_id, "Max Capacity", day0, 0.0) or 0.0
            )
            if name not in gens:
                gens[name] = {
                    k: v
                    for k, v in g.items()
                    if k not in ("uid", "pmax", "capacity", "gcost")
                }
                gen_pmax[name] = np.zeros((n_st, 24))
                gen_gcost[name] = np.full(n_st, float(g["gcost"]))
            for k, mk in enumerate(masks):
                gen_pmax[name][s0 + k] = rep_mean(
                    ws[("Generator", "pmax")][g["uid"]], mk
                )
            gen_gcost[name][stg] = float(g["gcost"])
        for d in system["demand_array"]:
            name = d["name"]
            if name not in dems:
                dems[name] = {k: v for k, v in d.items() if k not in ("uid",)}
                dem_lmax[name] = np.zeros((n_st, 24))
            for k, mk in enumerate(masks):
                dem_lmax[name][s0 + k] = rep_mean(ws[("Demand", "lmax")][d["uid"]], mk)
        for ln in system["line_array"]:
            name = ln["name"]
            if name not in lines:
                lines[name] = {k: v for k, v in ln.items() if k != "uid"}
                for f in ("tmax_ab", "tmax_ba"):
                    line_t[(name, f)] = np.zeros((n_st, 24))
            for f in ("tmax_ab", "tmax_ba"):
                for k, mk in enumerate(masks):
                    line_t[(name, f)][s0 + k] = rep_mean(ws[("Line", f)][ln["uid"]], mk)
        for b in system.get("battery_array", []):
            name = b["name"]
            inst.setdefault(
                name,
                ("Battery", res.category(bat_obj[name]), "battery", np.zeros(n_st)),
            )[3][stg] = float(b["pmax_discharge"])
            if name not in bats:
                bats[name] = {
                    k: v
                    for k, v in b.items()
                    if k not in ("uid", *_BATTERY_TB_FIELDS, "eini", "efin")
                }
                bats[name]["_first"] = s0
                bats[name]["_eini"] = b.get("eini")
                for f in _BATTERY_TB_FIELDS:
                    bat_tb[(name, f)] = np.zeros((n_st, 24))
            for f in _BATTERY_TB_FIELDS:
                bat_tb[(name, f)][stg] = float(b[f])
        for fl in system.get("flow_array", []):
            arr = ws.get(("Flow", "discharge"), {}).get(fl["uid"])
            if arr is not None:
                q = inflow.setdefault(fl["name"], np.zeros((n_st, 24)))
                for k, mk in enumerate(masks):
                    q[s0 + k] = rep_mean(arr, mk)
        for dv in system.get("decision_variable_array", []):
            dvars.setdefault(dv["name"], {k: v for k, v in dv.items() if k != "uid"})
        uc_rhs = ws.get(("UserConstraint", "rhs"), {})
        for uc in system.get("user_constraint_array", []):
            if "[" in uc["expression"]:
                stats["uc_skipped_profile_coefficient"] += 1
                continue
            try:
                folded = [_fold_uc(uc, uc_rhs.get(uc["uid"]), mk) for mk in masks]
            except ValueError:
                stats["uc_skipped_unparsed"] += 1
                continue
            for k, (key, base, op, rhs) in enumerate(folded):
                var = ucs[uc["name"]].setdefault(key, _UcVariant(base=base, op=op))
                var.rhs[s0 + k] = rhs
        logger.info(
            "month %s: %d gens, %d ucs, %d candidates with capainst",
            day0,
            len(system["generator_array"]),
            len(system.get("user_constraint_array", [])),
            len(names),
        )

    # ---- assemble ---------------------------------------------------------
    generator_array: list[dict[str, Any]] = []
    for name, g in gens.items():
        uid = len(generator_array) + 1
        # file-backed schedules of the base (hourly) conversion other than
        # the regenerated pmax / gcost (e.g. a fixed-load ``pmin``) have no
        # monthly table here: drop them rather than reference missing files.
        for fld in [k for k, v in g.items() if v == k and k not in ("pmax", "gcost")]:
            logger.warning("generator %s: dropped file field %s", name, fld)
            stats["dropped_file_fields"] += 1
            g = {k: v for k, v in g.items() if k != fld}
        generator_array.append(
            {
                **g,
                "uid": uid,
                "pmax": "pmax",
                "gcost": "gcost",
                "capacity": round(float(gen_pmax[name].max()), 6),
            }
        )
        sched[("Generator", "pmax")][uid] = gen_pmax[name]
        sched[("Generator", "gcost")][uid] = np.repeat(
            gen_gcost[name][:, None], 24, axis=1
        )
        stats[f"existing_{g.get('type', 'other')}_max_mw"] += float(
            gen_pmax[name].max()
        )
    profile_array: list[dict[str, Any]] = []
    gen_fuel_price: dict[int, list[float]] = {}
    battery_candidates: list[dict[str, Any]] = []
    for c in cands:
        if c.kind == "battery":
            battery_candidates.append(
                _battery_candidate(
                    c, build_stages(months, c, nrep), nrep, opts, fy_index
                )
            )
            stats["candidates"] += 1
            stats["candidates_storage"] += 1
            continue
        uid = len(generator_array) + 1
        bstage = build_stages(months, c, nrep)
        # Economic life shorter than the rest of the horizon: gtopt's
        # capacost keeps charging the annuity to the horizon end, so scale
        # it by life / remaining years (same undiscounted total payment).
        cap = [
            annual_capcost(c.build_cost[s // nrep], c.wacc, c.life, c.fom)
            * life_factor(c.life, opts.years - fy_index[s])
            for s in range(n_st)
        ]
        price = [0.0] * n_st
        if c.heat_rate and c.fuel_oid is not None:
            price = gen_fuel_price.setdefault(
                c.fuel_oid,
                [
                    res.scalar(c.fuel_oid, "Price", months[s // nrep], 0.0) or 0.0
                    for s in range(n_st)
                ],
            )
        gcost = [round(c.vom + (c.heat_rate or 0.0) * p, 6) for p in price]
        generator_array.append(
            {
                "uid": uid,
                "name": c.name,
                "bus": c.bus,
                "type": "candidate_" + _gen_type(c.category, c.name),
                "description": f"expansion candidate [{c.category}]",
                # gcost is a (stage, block) schedule: one row per stage
                "gcost": [[v] * 24 for v in gcost] if len(set(gcost)) > 1 else gcost[0],
                "capacity": 0.0,
                "expcap": c.unit_mw,
                "expmod": [
                    c.max_units[s // nrep] if bstage[s] else 0.0 for s in range(n_st)
                ],
                "capmax": [c.max_units[s // nrep] * c.unit_mw for s in range(n_st)],
                "annual_capcost": [round(v, 3) for v in cap],
            }
        )
        prof = np.zeros((n_st, 24))
        if c.energy:
            for m, (day0, nd) in enumerate(zip(months, ndays)):
                r = res.hourly(c.oid, "Rating", day0, nd)
                stg = range(m * nrep, (m + 1) * nrep)
                if r is None or not np.any(np.nan_to_num(r) > 0):
                    prof[stg] = 1.0
                    continue
                rf = res.hourly(c.oid, "Rating Factor", day0, nd)
                if rf is not None:
                    r = r * np.where(np.isnan(rf), 100.0, rf) / 100.0
                for k, mk in enumerate(month_masks[m]):
                    prof[m * nrep + k] = np.clip(
                        rep_mean(np.nan_to_num(r), mk) / c.unit_mw, 0.0, 1.0
                    )
            prof *= 1.0 - c.aux
        pid = len(profile_array) + 1
        profile_array.append(
            {
                "uid": pid,
                "name": f"profile_{c.name}",
                "generator": c.name,
                "profile": "profile",
            }
        )
        sched[("GeneratorProfile", "profile")][pid] = prof
        stats["candidates"] += 1
        stats["candidates_energy" if c.energy else "candidates_network"] += 1
        stats["candidate_max_mw"] += max(c.max_units) * c.unit_mw

    demand_array: list[dict[str, Any]] = []
    for name, d in dems.items():
        uid = len(demand_array) + 1
        demand_array.append({**d, "uid": uid, "lmax": "lmax"})
        sched[("Demand", "lmax")][uid] = dem_lmax[name]
    line_array: list[dict[str, Any]] = []
    for name, ln in lines.items():
        uid = len(line_array) + 1
        line_array.append(
            {**ln, "uid": uid, "tmax_ab": "tmax_ab", "tmax_ba": "tmax_ba"}
        )
        for f in ("tmax_ab", "tmax_ba"):
            sched[("Line", f)][uid] = line_t[(name, f)]
    battery_array: list[dict[str, Any]] = []
    for name, b in bats.items():
        item = {k: v for k, v in b.items() if not k.startswith("_")}
        item["uid"] = len(battery_array) + 1
        for f in _BATTERY_TB_FIELDS:
            item[f] = [[round(float(v), 6) for v in row] for row in bat_tb[(name, f)]]
        if b["_first"] == 0 and b["_eini"] is not None:
            item["eini"] = b["_eini"]
        battery_array.append(item)
    for item in battery_candidates:
        if item["name"] in bats:
            logger.warning("battery candidate %s is also existing", item["name"])
            continue
        battery_array.append({**item, "uid": len(battery_array) + 1})
    flow_array: list[dict[str, Any]] = []
    for fl in hydro.get("flow_array", []):
        if fl["name"] in inflow:
            item = {**fl, "uid": len(flow_array) + 1}
            flow_array.append(item)
            sched[("Flow", "discharge")][item["uid"]] = inflow[fl["name"]]
    for r in hydro.get("reservoir_array", []):
        r["efin"] = r["eini"]

    user_constraint_array: list[dict[str, Any]] = []
    for name, variants in sorted(ucs.items()):
        multi = len(variants) > 1
        for k, var in enumerate(variants.values()):
            if var.op == "=" and len(var.rhs) < n_st:
                stats["uc_skipped_equality_partial"] += 1
                continue
            uid = len(user_constraint_array) + 1
            base = dict(var.base)
            lhs = base.pop("_lhs")
            is_capacity = base.pop("_capacity", False)
            off = INACTIVE_RHS if var.op == "<=" else -INACTIVE_RHS
            stage_scope = base.get("scope") == "stage"
            first = next(iter(var.rhs.values()))
            item = {
                **base,
                "uid": uid,
                "name": f"{name}_v{k + 1}" if multi else name,
                "expression": f"{lhs} {var.op} {isp_c._fmt(float(np.max(first)))}",
            }
            rhs = np.full((n_st, 24), off)
            for s, v in var.rhs.items():
                rhs[s] = v
            if is_capacity and opts.capacity_penalty and "penalty" not in base:
                item["penalty"] = opts.capacity_penalty
            if is_capacity:
                rhs = _fy_capacity_rhs(rhs, fy_start, var.op, off)
            # stages without the constraint: per-stage ``active_stages`` (no LP
            # row) instead of a huge never-binding RHS
            live = np.abs(rhs[:, 0]) < abs(off)
            if not live.any():
                stats["uc_skipped_never_active"] += 1
                continue
            if not live.all():
                item["active_stages"] = [int(v) for v in live]
                rhs[~live] = rhs[live][0]
            item["expression"] = f"{lhs} {var.op} {isp_c._fmt(float(rhs[live][0, 0]))}"
            if stage_scope:
                vals = rhs[:, 0]
                if not np.allclose(vals, vals[0]):
                    # TB matrix (one row per stage; a stage row needs
                    # only one value: the stage-scope row reads block 0)
                    item["rhs"] = [[round(float(v), 6)] * 24 for v in vals]
            elif not np.allclose(rhs, rhs.flat[0]):
                item["rhs"] = "rhs"
                sched[("UserConstraint", "rhs")][uid] = rhs
            user_constraint_array.append(item)

    # ---- simulation -------------------------------------------------------
    block_array: list[dict[str, Any]] = []
    stage_array: list[dict[str, Any]] = []
    for s, nd in enumerate(stage_days):
        stage_array.append({"uid": s + 1, "first_block": 24 * s, "count_block": 24})
        block_array.extend(
            {"uid": 24 * s + h + 1, "duration": float(nd)} for h in range(24)
        )
    simulation: dict[str, Any] = {
        "block_array": block_array,
        "stage_array": stage_array,
        "scenario_array": [{"uid": 1, "probability_factor": 1.0}],
    }
    if opts.phases != "none":
        per = nrep if opts.phases == "month" else 12 * nrep
        simulation["phase_array"] = [
            {"uid": k + 1, "first_stage": k * per, "count_stage": per}
            for k in range(n_st // per)
        ]

    system_out: dict[str, Any] = {
        "name": opts.name,
        "bus_array": buses,
        "demand_array": demand_array,
        "generator_array": generator_array,
        "generator_profile_array": profile_array,
        "line_array": line_array,
    }
    if battery_array:
        system_out["battery_array"] = battery_array
    for key, elems in hydro.items():
        elems = flow_array if key == "flow_array" else elems
        if elems:
            system_out[key] = elems
    if user_constraint_array:
        system_out["user_constraint_array"] = user_constraint_array
    if dvars:
        system_out["decision_variable_array"] = [
            {**dv, "uid": i + 1} for i, dv in enumerate(dvars.values())
        ]
    planning = {
        "options": {
            "input_directory": ".",
            "input_format": "parquet",
            "output_format": "parquet",
            "annual_discount_rate": rate,
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": False,
                "demand_fail_cost": voll,
                "scale_objective": 1000.0,
            },
        },
        "simulation": simulation,
        "system": system_out,
    }
    stats.update(
        {
            "first_fy": opts.first_fy,
            "years": opts.years,
            "stages": n_st,
            "rep_days": nrep,
            "stage_days": stage_days,
            "blocks": 24 * n_st,
            "phases": len(simulation.get("phase_array", [])) or 1,
            "discount_rate": rate,
            "generators": len(generator_array),
            "batteries": len(battery_array),
            "user_constraints": len(user_constraint_array),
            "constraint_groups": {k: dict(v) for k, v in sorted(groups.items())},
            "existing_installed_mw": [
                {
                    "name": n,
                    "class": c,
                    "category": cat,
                    "type": t,
                    "mw": [round(float(v), 3) for v in mw],
                }
                for n, (c, cat, t, mw) in sorted(inst.items())
            ],
            "candidate_categories": {c.name: c.category for c in cands},
        }
    )
    return IspCase(planning=planning, schedules=dict(sched), stats=dict(stats))


# ---------------------------------------------------------------------------
# writer + CLI
# ---------------------------------------------------------------------------

#: classes whose schedules are per-(scenario, stage, block)
_STB_CLASSES = {"Flow", "GeneratorProfile"}


def write_expansion_case(case: IspCase, out_dir: Path, name: str) -> Path:
    """Write ``<out_dir>/<name>.json`` and the (stage, block) Parquet files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for (cls, fld), cols in case.schedules.items():
        if not cols:
            continue
        frames = []
        for uid, arr in sorted(cols.items()):
            a = np.asarray(arr, dtype=np.float64)
            n_st, nb = a.shape
            stage = np.repeat(np.arange(1, n_st + 1, dtype=np.int32), nb)
            block = np.arange(1, n_st * nb + 1, dtype=np.int32)
            df = pd.DataFrame(
                {
                    "stage": stage,
                    "block": block,
                    "uid": np.full(n_st * nb, uid, dtype=np.int32),
                    "value": np.round(a.reshape(-1), 6),
                }
            )
            if cls in _STB_CLASSES:
                df.insert(0, "scenario", np.ones(len(df), dtype=np.int32))
            frames.append(df)
        d = out_dir / cls
        d.mkdir(parents=True, exist_ok=True)
        pd.concat(frames, ignore_index=True).to_parquet(
            d / f"{fld}.parquet", index=False
        )
    path = out_dir / f"{name}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(case.planning, fh, indent=1, allow_nan=False, default=_json_default)
        fh.write("\n")
    return path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m plexos2gtopt.aemo_isp_expansion",
        description="Build a monthly-stage gtopt expansion case from an AEMO ISP PLEXOS XML.",
    )
    p.add_argument("xml", type=Path)
    p.add_argument("-t", "--trace-root", type=Path, action="append", default=[])
    p.add_argument(
        "--first-fy",
        type=int,
        default=2028,
        help="first FY (ending year, 2028 = 2027-28)",
    )
    p.add_argument("--years", type=int, default=3)
    p.add_argument("--phases", choices=("none", "month", "year"), default="none")
    p.add_argument(
        "--rep-days",
        type=int,
        choices=(1, 2),
        default=1,
        help="representative days per month (2: high / low net-load days)",
    )
    p.add_argument(
        "--discount-rate",
        type=float,
        default=None,
        help="default: LT Plan Discount Rate",
    )
    p.add_argument("--no-hydro", action="store_true")
    p.add_argument("--no-batteries", action="store_true")
    p.add_argument("--no-isp-constraints", action="store_true")
    p.add_argument("--demand-fail-cost", type=float, default=None)
    p.add_argument(
        "--capacity-penalty",
        type=float,
        default=None,
        help="slack cost of the hard yearly capacity constraints "
        "(recommended with --phases month|year)",
    )
    p.add_argument(
        "--candidate-prefix",
        action="append",
        default=[],
        help="keep candidates whose category starts with this (repeatable)",
    )
    p.add_argument("-o", "--output-dir", type=Path, required=True)
    p.add_argument("-n", "--name", default=None)
    p.add_argument("-l", "--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    name = args.name or f"isp_exp_fy{args.first_fy}_{args.years}y"
    opts = ExpansionOptions(
        xml=args.xml,
        trace_roots=[args.xml.parent, *args.trace_root],
        first_fy=args.first_fy,
        years=args.years,
        name=name,
        phases=args.phases,
        rep_days=args.rep_days,
        discount_rate=args.discount_rate,
        include_hydro=not args.no_hydro,
        include_batteries=not args.no_batteries,
        constraints=not args.no_isp_constraints,
        demand_fail_cost=args.demand_fail_cost,
        capacity_penalty=args.capacity_penalty,
        candidate_prefixes=tuple(args.candidate_prefix),
    )
    case = build_expansion_case(opts)
    path = write_expansion_case(case, args.output_dir, name)
    (args.output_dir / f"{name}.stats.json").write_text(
        json.dumps(case.stats, indent=1, sort_keys=True, default=_json_default) + "\n"
    )
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
