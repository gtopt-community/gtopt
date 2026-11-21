"""AEMO ISP PLEXOS ``Constraint`` objects, purchasers, emissions → gtopt.

Companion of :mod:`plexos2gtopt.aemo_isp`: once the fleet of the dispatch
window is built, this module adds

* **Purchasers** (electrolysers and fixed industrial loads);
* **Hydrogen**: one ``HydrogenNode`` + ``HydrogenDemand`` per PLEXOS H2
  consumption constraint, fed by the flexible electrolysers as
  ``CarrierConverter`` (``h2_model="native"``), or the PLEXOS form
  (electrolysers as price-responsive ``Demand`` + a window-sum
  ``UserConstraint``, ``h2_model="demand"``);
* **Emissions**: ``Emission`` (one per PLEXOS ``Emission`` object),
  ``EmissionSource`` per generator from the generator and fuel
  ``Production Rate`` memberships, and one ``EmissionZone`` per emission
  budget overlapping the window (cap pro-rated to the window);
* **Fuel offtake**: generators carry ``fuel`` + ``heat_rate`` and the
  gas-zone daily offtake limits become ``daily_sum`` user constraints on
  ``Σ_g Heat Rate_g / (1 − Aux Incr_g) · generator(g).generation``;
* **REZ buses**: every REZ whose export / import limit involves only that
  REZ's own injections becomes a bus, connected to its sub-region by a
  radial line with ``tmax_ab`` / ``tmax_ba`` = export / import limit;
* every other operational ``Constraint`` (group / cut-set / hybrid site /
  DNSP / policy / special) as a ``UserConstraint``.

PLEXOS semantics used:

* ``Sense`` −1 ≤, 0 =, 1 ≥ (default −1); ``Penalty Price`` −1 (or absent)
  = hard, > 0 = soft (slack priced at the penalty);
  ``Include in LT Plan`` = 0 disables the constraint.
* ``RHS`` (MW-type) per block, timeslices and Data File traces resolved
  per day; ``RHS Day`` → per-day ``daily_sum`` energy constraint; ``RHS
  Month`` / ``RHS Year`` / ``RHS Custom`` → one window-sum constraint with
  the period RHS **pro-rated** by hours (window ∩ period) / hours(period).
  Period RHS are in GWh (TJ for fuel offtake) and become MWh (GJ); their
  penalties are per GWh and become per MWh.
* Coefficient terms: ``Generation`` (gross, divided by ``1 − Aux Incr``)
  and ``Generation Sent Out`` → ``generator.generation``; battery
  ``Generation`` / ``Load`` → ``discharge`` / ``charge``; line ``Flow`` →
  ``line.flow`` (signed); ``Flow Forward`` / ``Flow Back`` → a
  ``DecisionVariable`` ``flow_fwd_<line>`` / ``flow_back_<line>`` ≥ ±flow
  (exact for convex use: ≤ with a positive coefficient; other uses are
  skipped); node ``Load`` → constant moved to the RHS; purchaser
  ``Load`` → electrolyser input; fuel ``Offtake`` → the fuel's
  generators' aux-adjusted heat-rate × generation terms.
  ``Installed Capacity`` (Units × Max Capacity) and battery ``Generation
  Capacity`` (Units × Max Power) on the start day are constants moved to
  the RHS; ``Capacity Built`` / ``Units Built in Year`` are 0 (no
  expansion).  Terms on objects absent from the case (``Units`` 0,
  candidates, skipped categories) are dropped; ``Pump Load`` terms are
  dropped (pumping itself is modelled by :mod:`aemo_isp`, but in the 2026
  ISP only the yearly Powering Australia Plan and the Snowy 2.0 pump
  limit carry such terms — neither applies to a 2027 week).  A coefficient
  row tagged with a PLEXOS
  ``Variable`` is multiplied by that variable's hourly ``Profile``.

Everything that is a capacity-expansion or build condition (resource,
build, onboarding, system-strength, landholder, firming, CIS, VRET …) is
skipped and counted in the stats with the reason.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .aemo_isp import IspResolver, _Row

logger = logging.getLogger(__name__)

#: PLEXOS ``Sense`` → gtopt operator.
SENSE_OP = {-1: "<=", 0: "=", 1: ">="}

#: Operational (per-dispatch-variable) coefficient properties.
OPERATIONAL_PROPS = frozenset(
    {
        "Generation Coefficient",
        "Generation Sent Out Coefficient",
        "Pump Load Coefficient",
        "Load Coefficient",
        "Flow Coefficient",
        "Flow Forward Coefficient",
        "Flow Back Coefficient",
    }
)
#: Capacity / build coefficient properties (constants for a fixed fleet).
CAPACITY_PROPS = frozenset(
    {
        "Installed Capacity Coefficient",
        "Capacity Built Coefficient",
        "Units Built in Year Coefficient",
        "Generation Capacity Coefficient",
    }
)
#: RHS properties by time basis.
RHS_PROPS = ("RHS", "RHS Day", "RHS Month", "RHS Year", "RHS Custom")
PERIOD_RHS = ("RHS Month", "RHS Year", "RHS Custom")

#: Categories of the PLEXOS H2 consumption constraints.
H2_CATEGORY_PREFIXES = (
    "H2 Subregion Domestic Consumption",
    "H2 Subregion Green Commodities Consumption",
    "H2 REZ Green Commodities Consumption",
)
#: Categories whose constraints are REZ export / import limits.
REZ_CATEGORIES = {
    "REZ Network Export Constraints": "export",
    "REZ Network Import Constraints": "import",
    "DREZ Export Constraints": "export",
    "DREZ Import Constraints": "import",
}

#: Default electrolyser specific consumption [kWh_e / kg H2] — IASR 2026
#: workbook, "Other hydrogen assumptions", PEM 2026-27.
DEFAULT_ELECTROLYSER_KWH_PER_KG = 56.76
#: Lower heating value of hydrogen [kWh / kg].
H2_LHV_KWH_PER_KG = 33.33

_EPS = 1e-9

#: Period RHS longer than this many days are "long" (see
#: :attr:`ConstraintOptions.long_periods`).
LONG_PERIOD_DAYS = 31


def period_span_days(periods: list[dict[str, Any]]) -> int:
    """Longest period (inclusive days) of a :func:`prorated_rhs` result."""
    return max(
        (date.fromisoformat(str(p["to"])) - date.fromisoformat(str(p["from"]))).days + 1
        for p in periods
    )


#: Modelling approximations reported in the case stats.
APPROXIMATIONS = [
    "RHS Month / RHS Year / RHS Custom (and emission budgets) are pro-rated "
    "to the window by hours: a window-sum constraint replaces the period one",
    "period RHS are read as GWh (TJ for fuel offtake) and period penalties as "
    "$/GWh; both are converted to MWh (GJ)",
    "RHS Day becomes one daily_sum row per day (1 h blocks)",
    "H2 consumption targets: electrolyser GWh_e × efficiency (IASR PEM "
    "kWh/kg, not in the XML) → HydrogenDemand MWh_LHV; free intra-window H2 "
    "storage",
    "capacity terms (Installed Capacity, Generation Capacity) are folded with "
    "the start-day Units; build terms are zero (fixed fleet)",
    "period targets longer than one month (RHS Year / RHS Custom policy "
    "targets and emission budgets) are not enforced by default: emission "
    "budgets become EmissionZones without cap (--long-periods prorate "
    "enforces the pro-rated share)",
    "fuel cost, gas-zone fuel offtake and emission rates all use the "
    "sent-out heat rate Heat Rate / (1 − Aux Incr) (the legacy gcost keeps "
    "the unadjusted Heat Rate)",
]


@dataclass
class ConstraintOptions:
    """Switches for the pieces added by this module (all on by default)."""

    constraints: bool = True
    purchasers: bool = True
    emissions: bool = True
    fuels: bool = True
    rez_buses: bool = True
    #: ``"native"`` (CarrierConverter → HydrogenNode → HydrogenDemand) or
    #: ``"demand"`` (electrolyser = Demand + window-sum UserConstraint).
    h2_model: str = "native"
    #: Period RHS longer than ``LONG_PERIOD_DAYS`` (RHS Year / RHS Custom
    #: policy targets, emission budgets): ``"skip"`` does not enforce them
    #: (emission budgets stay as tracking zones without cap) because a
    #: uniform weekly share of a seasonal annual / multi-year target is not
    #: a valid weekly limit; ``"prorate"`` enforces the pro-rated share.
    long_periods: str = "skip"
    #: Expansion mode (:mod:`plexos2gtopt.aemo_isp_expansion`): capacity
    #: terms of the expansion candidates listed in
    #: :attr:`CaseView.expansion` become ``capainst`` variables and the
    #: pure capacity / build constraints (RHS Year over installed or built
    #: capacity) become stage-scope user constraints instead of being
    #: skipped as "fixed fleet".
    expansion: bool = False
    electrolyser_kwh_per_kg: float = DEFAULT_ELECTROLYSER_KWH_PER_KG
    #: Generator ``Min Load`` / ``Mark-up`` / ``Min Capacity Factor Year``
    #: (see :func:`plexos2gtopt.aemo_isp.build_dispatch_case`).
    generator_extras: bool = True

    @property
    def h2_efficiency(self) -> float:
        """Electrolyser efficiency [MWh_LHV / MWh_e]."""
        return H2_LHV_KWH_PER_KG / self.electrolyser_kwh_per_kg


@dataclass
class GenInfo:
    """What the constraint engine needs to know about a case generator."""

    name: str
    bus: str
    aux: float
    heat_rate: float | None = None
    fuel: str | None = None


@dataclass
class PurchaserInfo:
    """Mapping of one PLEXOS purchaser in the case."""

    name: str
    bus: str
    kind: str  # "flexible" | "fixed"
    max_load: float  # MW (flexible) / fixed MW (fixed)
    bid_price: float
    #: per-block fixed load [MW] for fixed purchasers
    fixed: np.ndarray | None = None
    #: name of the H2 consumption constraint feeding it (flexible only)
    h2_constraint: str | None = None


@dataclass
class Term:
    """One LHS term resolved for the window."""

    ref: str  # gtopt element reference, e.g. generator("X").generation
    coef: float | np.ndarray  # scalar or per-block profile
    cls: str  # PLEXOS class of the object
    obj: str  # PLEXOS object name
    #: +1 when the variable is an injection at the element's bus
    #: (generation, battery discharge), −1 when a withdrawal.
    inj: int = 0


@dataclass
class EvalConstraint:
    """A constraint evaluated for the window."""

    name: str
    category: str
    group: str
    sense: int
    penalty: float | None  # None = hard
    rhs_kind: str
    terms: list[Term] = field(default_factory=list)
    #: constant LHS part per block (capacity terms, node loads, fixed loads)
    const: np.ndarray | None = None
    dropped: Counter[str] = field(default_factory=Counter)
    unsupported: set[str] = field(default_factory=set)
    #: fixed-load purchasers folded into ``const``: (name, coefficient)
    fixed_loads: list[tuple[str, float]] = field(default_factory=list)
    #: an RHS / coefficient Data File trace is not available
    missing_data: bool = False


@dataclass
class CaseView:
    """The case pieces the engine reads / mutates."""

    res: IspResolver
    day0: date
    days: int
    gens: dict[str, GenInfo]
    batteries: dict[str, str]  # battery name → bus
    lines: set[str]
    node_load: dict[str, np.ndarray]  # bus → hourly demand MW
    system: dict[str, Any]
    sched: dict[tuple[str, str], dict[int, np.ndarray]]
    purchasers: dict[str, PurchaserInfo] = field(default_factory=dict)
    #: expansion candidates with a ``capainst`` column in this window
    #: (generator name → gtopt class reference prefix, e.g. ``generator``)
    expansion: frozenset[str] = frozenset()

    def res_oid(self, name: str) -> int:
        """Object id of the constraint called ``name``."""
        return _constraint_oid(self, name)

    @property
    def nb(self) -> int:
        """Number of hourly blocks."""
        return 24 * self.days


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _q(name: str) -> str:
    """Quote an element name for a gtopt expression."""
    return '"' + name.replace('"', '\\"') + '"'


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def _days(day0: date, days: int) -> list[date]:
    return [day0 + timedelta(days=k) for k in range(days)]


def fiscal_year(day: date) -> tuple[date, date]:
    """Australian fiscal year (1 July – 30 June) containing ``day``."""
    y = day.year if day.month >= 7 else day.year - 1
    return date(y, 7, 1), date(y + 1, 6, 30)


def calendar_month(day: date) -> tuple[date, date]:
    """First and last day of ``day``'s month."""
    first = day.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt - timedelta(days=1)


def period_of(rhs_kind: str, row: _Row, day: date) -> tuple[date, date]:
    """Period ``[first, last]`` (inclusive days) a period RHS row covers."""
    if rhs_kind == "RHS Month":
        return calendar_month(day)
    if row.date_from is not None and row.date_to is not None:
        return row.date_from.date(), row.date_to.date()
    if rhs_kind == "RHS Year":
        return fiscal_year(day)
    # RHS Custom without both dates: open-ended → treat as one year
    return fiscal_year(day)


def prorated_rhs(
    res: IspResolver, oid: int, rhs_kind: str, day0: date, days: int
) -> tuple[float | None, list[dict[str, Any]]]:
    """Window share of a period RHS (PLEXOS units) and the periods used.

    Each window day contributes ``value × 24 / hours(period)`` of the row
    live on that day; days with no live row contribute nothing.  Returns
    ``(None, [])`` when no day of the window has a live row.
    """
    rows = res.rows.get((oid, rhs_kind), [])
    total = 0.0
    found = False
    used: dict[tuple[date, date, float], int] = {}
    for d in _days(day0, days):
        row = res.select_row(rows, d)
        if row is None or row.data_file is not None:
            continue
        first, last = period_of(rhs_kind, row, d)
        if not first <= d <= last:
            continue
        hours = 24.0 * ((last - first).days + 1)
        total += row.value * 24.0 / hours
        found = True
        key = (first, last, row.value)
        used[key] = used.get(key, 0) + 1
    if not found:
        return None, []
    periods = [
        {
            "from": a.isoformat(),
            "to": b.isoformat(),
            "value": v,
            "window_days": n,
            "share": n / ((b - a).days + 1),
        }
        for (a, b, v), n in used.items()
    ]
    return total, periods


# ---------------------------------------------------------------------------
# index of the PLEXOS constraint data
# ---------------------------------------------------------------------------


@dataclass
class ConstraintIndex:
    """Coefficient rows and system properties of every Constraint object."""

    #: constraint oid → list of (parent oid, property, rows)
    coefs: dict[int, list[tuple[int, str, list[_Row]]]]

    @classmethod
    def build(cls, res: IspResolver) -> ConstraintIndex:
        """Index ``res.coll_rows`` by constraint object."""
        ccid = res.db.class_id("Constraint")
        coefs: dict[int, list[tuple[int, str, list[_Row]]]] = defaultdict(list)
        for (parent, child, key), rows in res.coll_rows.items():
            coll, _, prop = key.partition("::")
            if coll != "Constraints":
                continue
            o = res.obj.get(child)
            if o is None or o.class_id != ccid:
                continue
            coefs[child].append((parent, prop, rows))
        return cls(coefs=coefs)

    def coef_kinds(self, res: IspResolver, oid: int) -> set[tuple[str, str]]:
        """(PLEXOS class, property) pairs of the constraint's coefficients."""
        return {(res.cls[res.obj[p].class_id], prop) for p, prop, _ in self.coefs[oid]}


def classify(res: IspResolver, index: ConstraintIndex, oid: int) -> str:
    """Coefficient-type group of a constraint (see the module docstring)."""
    kinds = index.coef_kinds(res, oid)
    if not kinds:
        return "no_coefficients"
    classes = {k[0] for k in kinds}
    if "Company" in classes:
        return "company_vret"
    if "Emission" in classes:
        return "emissions"
    if "Fuel" in classes:
        return "fuel_offtake"
    if "Purchaser" in classes:
        cat = res.category(res.obj[oid])
        if cat.startswith(H2_CATEGORY_PREFIXES):
            return "h2_consumption"
        return "purchaser_network"
    op = any(k[1] in OPERATIONAL_PROPS for k in kinds)
    cap = any(k[1] in CAPACITY_PROPS for k in kinds)
    if op and cap:
        return "operational_capacity"
    if op:
        return "operational"
    return "capacity_build"


def rhs_kind(res: IspResolver, oid: int, day0: date, days: int) -> str | None:
    """The RHS property live in the window (first match in RHS_PROPS)."""
    for prop in RHS_PROPS:
        rows = res.rows.get((oid, prop), [])
        if not rows:
            continue
        if prop in PERIOD_RHS or any(
            res.select_row(rows, d) is not None for d in _days(day0, days)
        ):
            return prop
    return None


# ---------------------------------------------------------------------------
# purchasers
# ---------------------------------------------------------------------------


def build_purchasers(
    view: CaseView,
    index: ConstraintIndex,
    opts: ConstraintOptions,
    stats: dict[str, Any],
) -> None:
    """Resolve PLEXOS purchasers for the window and add them to the case.

    Flexible electrolysers (``Max Load`` > 0, no ``Fixed Load``) become
    ``CarrierConverter`` (native H2 model) or ``Demand`` with a zero
    curtailment cost (demand model; PLEXOS ``Bid Price`` is the value of
    the load).  Fixed loads (``Fixed Load`` > 0) become a ``Demand`` with
    ``lmax`` = fixed load, curtailed at VoLL like the sub-regional load.
    """
    res, day0, days = view.res, view.day0, view.days
    from .aemo_isp import _single_child  # pylint: disable=import-outside-toplevel

    pnode = _single_child(res, "Purchaser", "Nodes")
    node_name = {o.object_id: o.name for o in res.objects("Node")}
    # purchaser → H2 consumption constraint
    h2_of: dict[int, str] = {}
    for coid, coefs in index.coefs.items():
        if classify(res, index, coid) != "h2_consumption":
            continue
        for parent, prop, _rows in coefs:
            if prop == "Load Coefficient":
                h2_of[parent] = res.obj[coid].name
    pstats: Counter[str] = Counter()
    for o in sorted(res.objects("Purchaser"), key=lambda o: o.name):
        units = res.scalar(o.object_id, "Units", day0, default=0.0) or 0.0
        bus = node_name.get(pnode.get(o.object_id, -1))
        if units <= 0 or bus is None:
            pstats["skipped_no_units"] += 1
            continue
        fixed = res.hourly(o.object_id, "Fixed Load", day0, days)
        maxl = res.hourly(o.object_id, "Max Load", day0, days)
        bid = res.scalar(o.object_id, "Bid Price", day0, default=0.0) or 0.0
        fixed = None if fixed is None else units * np.nan_to_num(fixed)
        maxl_v = 0.0 if maxl is None else units * float(np.nanmax(maxl))
        if fixed is not None and fixed.max() > _EPS:
            view.purchasers[o.name] = PurchaserInfo(
                name=o.name,
                bus=bus,
                kind="fixed",
                max_load=float(fixed.max()),
                bid_price=bid,
                fixed=fixed,
            )
            pstats["fixed"] += 1
            stats["purchaser_fixed_mw"] += float(fixed.max())
        elif maxl_v > _EPS:
            view.purchasers[o.name] = PurchaserInfo(
                name=o.name,
                bus=bus,
                kind="flexible",
                max_load=maxl_v,
                bid_price=bid,
                h2_constraint=h2_of.get(o.object_id),
            )
            pstats["flexible"] += 1
            stats["electrolyser_mw"] += maxl_v
        else:
            pstats["skipped_zero_load"] += 1
    stats["purchasers"] = dict(pstats)
    _emit_purchasers(view, opts)


def _emit_purchasers(view: CaseView, opts: ConstraintOptions) -> None:
    system = view.system
    demands: list[dict[str, Any]] = system.setdefault("demand_array", [])
    converters: list[dict[str, Any]] = []
    for p in view.purchasers.values():
        if p.kind == "fixed":
            uid = len(demands) + 1
            demands.append(
                {
                    "uid": uid,
                    "name": p.name,
                    "bus": p.bus,
                    "type": "purchaser_fixed",
                    "lmax": "lmax",
                }
            )
            assert p.fixed is not None
            view.sched[("Demand", "lmax")][uid] = p.fixed
        elif opts.h2_model == "native":
            item: dict[str, Any] = {
                "uid": len(converters) + 1,
                "name": p.name,
                "type": "electrolyser",
                "from_carrier": "electric",
                "from_node": p.bus,
                "capacity": round(p.max_load, 6),
                "ocost": -p.bid_price,
            }
            if p.h2_constraint is not None:
                item["to_carrier"] = "hydrogen"
                item["to_node"] = h2_node_name(p.h2_constraint)
                item["efficiency"] = round(opts.h2_efficiency, 6)
            converters.append(item)
        else:
            demands.append(
                {
                    "uid": len(demands) + 1,
                    "name": p.name,
                    "bus": p.bus,
                    "type": "electrolyser",
                    "lmax": round(p.max_load, 6),
                    # PLEXOS Bid Price: the load is worth this much; a
                    # zero bid makes it fully flexible (curtailment free)
                    "fcost": p.bid_price,
                }
            )
    if converters:
        system["carrier_converter_array"] = converters


def purchaser_ref(view: CaseView, opts: ConstraintOptions, name: str) -> str:
    """gtopt variable reference of a flexible purchaser's electric load."""
    if opts.h2_model == "native":
        return f"carrier_converter({_q(name)}).input"
    return f"demand({_q(name)}).load"


def split_flow_name(line: str, side: str) -> str:
    """DecisionVariable name of the directional part of a line flow."""
    return f"flow_{side}_{_slug(line)}"


def _split_flow_terms(ev: EvalConstraint) -> list[Term]:
    return [t for t in ev.terms if t.ref.startswith("decision_variable(")]


def _split_flow_convex(ev: EvalConstraint) -> bool:
    """max(0, ±flow) terms must enter ≤ with c > 0 (≥ with c < 0)."""
    for t in _split_flow_terms(ev):
        c = np.asarray(t.coef)
        if ev.sense == -1 and not np.all(c > 0):
            return False
        if ev.sense == 1 and not np.all(c < 0):
            return False
        if ev.sense == 0:
            return False
    return True


def _emit_split_flows(view: CaseView, ucs: list[dict[str, Any]]) -> int:
    """DecisionVariables + defining rows for the directional flows used."""
    used: set[tuple[str, str]] = set()
    pat = re.compile(r'decision_variable\("(flow_(fwd|back)_[^"]+)"\)')
    names = {
        split_flow_name(ln, side): (ln, side)
        for ln in view.lines
        for side in ("fwd", "back")
    }
    for uc in ucs:
        for m in pat.finditer(uc.get("expression", "")):
            if m.group(1) in names:
                used.add(names[m.group(1)])
    dvs = view.system.setdefault("decision_variable_array", [])
    for ln, side in sorted(used):
        name = split_flow_name(ln, side)
        dvs.append(
            {
                "uid": len(dvs) + 1,
                "name": name,
                "lower_bound": 0.0,
                "description": f"max(0, {'+' if side == 'fwd' else '-'}flow) of {ln}",
            }
        )
        sign = "-" if side == "fwd" else "+"
        ucs.append(
            {
                "name": f"def_{name}",
                "description": f"{name} >= {'+' if side == 'fwd' else '-'}flow "
                f"of line {ln}",
                "expression": f"decision_variable({_q(name)}).value "
                f"{sign} line({_q(ln)}).flow >= 0",
            }
        )
    return len(used)


def h2_node_name(constraint: str) -> str:
    """HydrogenNode name of a PLEXOS H2 consumption constraint."""
    return "h2_" + _slug(constraint)


# ---------------------------------------------------------------------------
# term resolution
# ---------------------------------------------------------------------------


def evaluate(
    view: CaseView,
    index: ConstraintIndex,
    opts: ConstraintOptions,
    oid: int,
    group: str,
) -> EvalConstraint | None:
    """Resolve sense / penalty / terms of one constraint for the window."""
    res, day0 = view.res, view.day0
    o = res.obj[oid]
    kind = rhs_kind(res, oid, day0, view.days)
    if kind is None:
        return None
    sense = int(res.scalar(oid, "Sense", day0, default=-1.0) or 0.0)
    pen = res.scalar(oid, "Penalty Price", day0, default=-1.0)
    ev = EvalConstraint(
        name=o.name,
        category=res.category(o),
        group=group,
        sense=sense,
        penalty=pen if pen is not None and pen > 0 else None,
        rhs_kind=kind,
        const=np.zeros(view.nb),
    )
    hits0 = res.missing_hits
    for parent, prop, rows in index.coefs[oid]:
        _add_term(view, opts, ev, parent, prop, rows)
    if kind not in PERIOD_RHS:
        res.hourly(oid, kind, day0, view.days)  # touches the RHS traces
    ev.missing_data = res.missing_hits > hits0
    return ev


def _row_coef(view: CaseView, rows: list[_Row]) -> tuple[float | np.ndarray] | None:
    """Coefficient live on the start day; a Variable tag → hourly profile."""
    res = view.res
    plain = res.select_row([r for r in rows if not r.others], view.day0)
    tagged = [r for r in rows if r.others]
    if tagged:
        live = [
            r
            for r in tagged
            if (r.date_from is None or r.date_from.date() <= view.day0)
            and (r.date_to is None or r.date_to.date() >= view.day0)
        ]
        if live:
            r = max(live, key=lambda r: r.date_from or datetime.min)
            prof = np.ones(view.nb)
            for vid in r.others:
                if res.cls.get(res.obj[vid].class_id) != "Variable":
                    continue
                p = res.hourly(vid, "Profile", view.day0, view.days)
                if p is not None:
                    prof = prof * np.nan_to_num(p)
            return (r.value * prof,)
    if plain is None or plain.data_file is not None:
        return None
    return (plain.value,)


def _add_term(  # pylint: disable=too-many-branches,too-many-return-statements
    view: CaseView,
    opts: ConstraintOptions,
    ev: EvalConstraint,
    parent: int,
    prop: str,
    rows: list[_Row],
) -> None:
    res = view.res
    po = res.obj[parent]
    pcls = res.cls[po.class_id]
    got = _row_coef(view, rows)
    if got is None:
        ev.dropped["no_live_coefficient"] += 1
        return
    c = got[0]
    if np.all(np.abs(np.asarray(c)) < _EPS):
        ev.dropped["zero_coefficient"] += 1
        return
    name = po.name
    if pcls == "Generator":
        if name in view.expansion and prop in (
            "Installed Capacity Coefficient",
            "Capacity Built Coefficient",
        ):
            # candidate (Units = 0): installed = built = capainst
            ev.terms.append(
                Term(f"generator({_q(name)}).capainst", _simplify(c), pcls, name)
            )
            ev.dropped["capacity_variable"] += 1
            return
        if prop == "Installed Capacity Coefficient":
            units = res.scalar(parent, "Units", view.day0, default=0.0) or 0.0
            cap = res.scalar(parent, "Max Capacity", view.day0, default=0.0) or 0.0
            assert ev.const is not None
            ev.const += np.asarray(c) * units * cap
            ev.dropped["capacity_folded" if units * cap else "capacity_zero"] += 1
            return
        if prop in ("Capacity Built Coefficient", "Units Built in Year Coefficient"):
            if (
                opts.expansion
                and prop == "Units Built in Year Coefficient"
                and name in view.expansion
            ):
                # in-year build of a candidate = its expansion modules
                # (expcap = Max Capacity = one unit); builds happen at the
                # FY start, where the yearly constraint is enforced
                ev.terms.append(
                    Term(f"generator({_q(name)}).expmod", _simplify(c), pcls, name)
                )
                ev.dropped["build_variable"] += 1
                return
            ev.dropped["build_term_zero"] += 1
            return
        if prop == "Pump Load Coefficient":
            ev.dropped["pump_load_not_modelled"] += 1
            return
        g = view.gens.get(name)
        if g is None and name in view.expansion:
            # expansion candidate: in the case, but not in the start-day fleet
            aux = (res.scalar(parent, "Aux Incr", view.day0, default=0.0) or 0.0) / 100
            g = GenInfo(name=name, bus="", aux=aux)
        if g is None:
            ev.dropped["object_not_in_case"] += 1
            return
        if prop == "Generation Coefficient":
            c = np.asarray(c) / max(1.0 - g.aux, _EPS)
        elif prop != "Generation Sent Out Coefficient":
            ev.unsupported.add(f"{pcls}.{prop}")
            return
        ev.terms.append(
            Term(f"generator({_q(name)}).generation", _simplify(c), pcls, name, 1)
        )
        return
    if pcls == "Battery":
        if prop == "Generation Capacity Coefficient":
            units = res.scalar(parent, "Units", view.day0, default=0.0) or 0.0
            pmax = res.scalar(parent, "Max Power", view.day0, default=0.0) or 0.0
            if opts.expansion and units <= 0 and name in view.expansion:
                # candidate battery: installed power = its converter's
                # capainst (Battery.power_* expansion fields)
                ev.terms.append(
                    Term(
                        f"converter({_q(name + '_conv')}).capainst",
                        _simplify(c),
                        pcls,
                        name,
                    )
                )
                ev.dropped["capacity_variable"] += 1
                return
            assert ev.const is not None
            ev.const += np.asarray(c) * units * pmax
            ev.dropped["capacity_folded" if units * pmax else "capacity_zero"] += 1
            return
        if name not in view.batteries and name not in view.expansion:
            ev.dropped["object_not_in_case"] += 1
            return
        if prop == "Generation Coefficient":
            ev.terms.append(
                Term(f"battery({_q(name)}).discharge", _simplify(c), pcls, name, 1)
            )
        elif prop == "Load Coefficient":
            ev.terms.append(
                Term(f"battery({_q(name)}).charge", _simplify(c), pcls, name, -1)
            )
        else:
            ev.unsupported.add(f"{pcls}.{prop}")
        return
    if pcls == "Line":
        if name not in view.lines:
            ev.dropped["object_not_in_case"] += 1
            return
        if prop == "Flow Coefficient":  # signed flowp − flown
            ev.terms.append(Term(f"line({_q(name)}).flow", _simplify(c), pcls, name))
            return
        side = {"Flow Forward Coefficient": "fwd", "Flow Back Coefficient": "back"}
        if prop not in side:
            ev.unsupported.add(f"{pcls}.{prop}")
            return
        # Lossless lines carry one signed flow column: the directional
        # part max(0, ±flow) is a DecisionVariable bounded below by ±flow
        # (exact when the term enters the constraint convexly).
        ev.terms.append(
            Term(
                f"decision_variable({_q(split_flow_name(name, side[prop]))}).value",
                _simplify(c),
                pcls,
                name,
            )
        )
        return
    if pcls == "Node":
        load = view.node_load.get(name)
        if load is None:
            ev.dropped["object_not_in_case"] += 1
            return
        assert ev.const is not None
        ev.const += np.asarray(c) * load
        ev.dropped["node_load_folded"] += 1
        return
    if pcls == "Purchaser":
        p = view.purchasers.get(name)
        if p is None:
            ev.dropped["object_not_in_case"] += 1
            return
        if p.kind == "fixed":
            assert ev.const is not None and p.fixed is not None
            ev.const += np.asarray(c) * p.fixed
            ev.fixed_loads.append((name, float(np.max(c))))
            ev.dropped["fixed_load_folded"] += 1
            return
        ev.terms.append(
            Term(purchaser_ref(view, opts, name), _simplify(c), pcls, name, -1)
        )
        return
    if pcls == "Fuel":
        users = [g for g in view.gens.values() if g.fuel == name and g.heat_rate]
        if not users:
            ev.dropped["object_not_in_case"] += 1
            return
        # Offtake [GJ/h] = Σ_g Heat Rate_g [GJ/MWh gross] × gross generation;
        # gtopt generation is sent out, so each unit's coefficient is
        # Heat Rate / (1 − Aux Incr) — the same gross-to-sent-out factor
        # as the emission rates.  ``fuel("X").offtake`` would use the
        # unadjusted ``heat_rate`` (which also drives the fuel cost), so
        # the terms are spelled out per generator.
        for g in sorted(users, key=lambda u: u.name):
            assert g.heat_rate is not None
            k = g.heat_rate / max(1.0 - g.aux, _EPS)
            ev.terms.append(
                Term(
                    f"generator({_q(g.name)}).generation",
                    _simplify(np.asarray(c) * k),
                    "Generator",
                    g.name,
                )
            )
        return
    ev.unsupported.add(f"{pcls}.{prop}")


def _simplify(c: float | np.ndarray) -> float | np.ndarray:
    arr = np.asarray(c, dtype=float)
    if arr.ndim == 0:
        return float(arr)
    if np.allclose(arr, arr[0]):
        return float(arr[0])
    return arr


# ---------------------------------------------------------------------------
# expression writer
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    return f"{v:.10g}"


def lhs_text(terms: list[Term]) -> str:
    """Linear LHS text of ``terms`` (per-block profiles as ``[v…] * x``)."""
    parts: list[str] = []
    for t in terms:
        if isinstance(t.coef, np.ndarray):
            prof = ", ".join(_fmt(float(v)) for v in t.coef)
            piece = f"[{prof}] * {t.ref}"
            parts.append(piece if not parts else f"+ {piece}")
            continue
        c = float(t.coef)
        mag = abs(c)
        body = t.ref if abs(mag - 1.0) < 1e-12 else f"{_fmt(mag)} * {t.ref}"
        if not parts:
            parts.append(f"-{body}" if c < 0 else body)
        else:
            parts.append(f"{'-' if c < 0 else '+'} {body}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------


def apply(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    view: CaseView,
    opts: ConstraintOptions,
    stats: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add purchasers, H2, emissions, fuel offtake, REZ buses and constraints.

    Returns the new user constraints (without uid; the caller numbers them
    and moves any ``_rhs`` per-block schedule into the case schedules).
    """
    res = view.res
    index = ConstraintIndex.build(res)
    ucs: list[dict[str, Any]] = []
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    notes: list[str] = []

    if opts.purchasers:
        build_purchasers(view, index, opts, stats)
    if opts.emissions:
        build_emissions(view, index, opts, stats, groups, notes)

    evaluated: list[EvalConstraint] = []
    for o in sorted(res.objects("Constraint"), key=lambda o: o.name):
        group = classify(res, index, o.object_id)
        groups[group]["total"] += 1
        if group == "emissions":
            continue  # handled by build_emissions
        include = res.scalar(o.object_id, "Include in LT Plan", view.day0, -1.0)
        if include is not None and include == 0:
            groups[group]["skipped: disabled (Include in LT Plan = 0)"] += 1
            continue
        if group == "capacity_build" and opts.expansion:
            ev = evaluate(view, index, opts, o.object_id, group)
            uc = _capacity_constraint(view, ev, groups) if ev is not None else None
            if ev is None:
                groups[group]["skipped: no RHS live in the window"] += 1
            if uc is not None:
                ucs.append(uc)
            continue
        if group in ("capacity_build", "no_coefficients", "company_vret"):
            reason = {
                "capacity_build": "capacity / build condition (fixed fleet)",
                "no_coefficients": "no coefficients in the XML",
                "company_vret": "company-level yearly policy (VRET)",
            }[group]
            groups[group][f"skipped: {reason}"] += 1
            continue
        if not opts.constraints and group != "h2_consumption":
            groups[group]["skipped: --no-isp-constraints"] += 1
            continue
        if group == "fuel_offtake" and not opts.fuels:
            groups[group]["skipped: --no-fuel-offtake"] += 1
            continue
        if group in ("h2_consumption", "purchaser_network") and not opts.purchasers:
            groups[group]["skipped: --no-purchasers"] += 1
            continue
        ev = evaluate(view, index, opts, o.object_id, group)
        if ev is None:
            groups[group]["skipped: no RHS live in the window"] += 1
            continue
        if ev.missing_data:
            groups[group]["skipped: Data File trace not available"] += 1
            continue
        if ev.unsupported:
            groups[group][
                "skipped: unsupported term " + ",".join(sorted(ev.unsupported))
            ] += 1
            continue
        if not _split_flow_convex(ev):
            groups[group][
                "skipped: Flow Forward/Back term in a non-convex position"
            ] += 1
            continue
        evaluated.append(ev)

    rez_done: set[str] = set()
    if opts.rez_buses and opts.constraints:
        rez_done = build_rez_buses(view, evaluated, stats, groups)

    h2_rows: list[dict[str, Any]] = []
    for ev in evaluated:
        if ev.name in rez_done:
            continue
        if ev.group == "h2_consumption" and opts.h2_model == "native":
            item = _h2_demand(view, opts, ev, groups, notes)
            if item is not None:
                h2_rows.append(item)
            continue
        uc = _to_user_constraint(view, opts, ev, groups, notes)
        if uc is not None:
            ucs.append(uc)

    if h2_rows:
        view.system["hydrogen_node_array"] = [
            {"uid": i + 1, "name": r["hydrogen_node"]} for i, r in enumerate(h2_rows)
        ]
        for i, r in enumerate(h2_rows):
            r["uid"] = i + 1
        view.system["hydrogen_demand_array"] = h2_rows
        # every converter must feed an existing node
        nodes = {r["hydrogen_node"] for r in h2_rows}
        for cc in view.system.get("carrier_converter_array", []):
            if cc.get("to_node") is not None and cc["to_node"] not in nodes:
                for k in ("to_node", "to_carrier", "efficiency"):
                    cc.pop(k, None)

    stats["constraint_groups"] = {k: dict(v) for k, v in sorted(groups.items())}
    stats["constraint_notes"] = notes
    stats["approximations"] = APPROXIMATIONS
    n_split = _emit_split_flows(view, ucs)
    if n_split:
        stats["directional_flow_variables"] = n_split
    return ucs


def _capacity_constraint(
    view: CaseView, ev: EvalConstraint, groups: dict[str, Counter[str]]
) -> dict[str, Any] | None:
    """Stage-scope user constraint over installed capacity (expansion mode).

    PLEXOS capacity / build constraints carry an ``RHS Year`` in MW (not a
    period energy): the value live on the window's first day bounds the
    capacity installed in that year.  Existing units are folded into the
    RHS (``Units × Max Capacity`` on the first day); candidates enter as
    ``generator(...).capainst``.
    """
    group = ev.group
    if ev.unsupported:
        groups[group][
            "skipped: unsupported term " + ",".join(sorted(ev.unsupported))
        ] += 1
        return None
    rows = view.res.rows.get((view.res_oid(ev.name), ev.rhs_kind), [])
    row = view.res.select_row(rows, view.day0)
    if row is None or row.data_file is not None:
        groups[group]["skipped: no RHS live in the window"] += 1
        return None
    assert ev.const is not None
    if any(isinstance(t.coef, np.ndarray) for t in ev.terms):
        groups[group]["skipped: time-varying capacity coefficient"] += 1
        return None
    op = SENSE_OP.get(ev.sense, "<=")
    rhs = row.value - float(ev.const[0])
    if not ev.terms:
        _constant_only(ev, rhs, groups)
        return None
    uc: dict[str, Any] = {
        "name": f"isp_{_slug(ev.name)}",
        "description": f"PLEXOS Constraint '{ev.name}' [{ev.category}], "
        f"{ev.rhs_kind} [MW] over installed capacity",
        "expression": f"{lhs_text(ev.terms)} {op} {_fmt(rhs)}",
        "scope": "stage",
        "constraint_type": "raw",
        #: marker for the expansion merger (yearly capacity semantics)
        "_capacity": True,
    }
    if ev.penalty is not None:
        uc["penalty"] = ev.penalty
    groups[group]["converted: stage capacity"] += 1
    return uc


def _constraint_oid(view: CaseView, name: str) -> int:
    for o in view.res.objects("Constraint"):
        if o.name == name:
            oid: int = o.object_id
            return oid
    raise KeyError(name)


def _to_user_constraint(  # pylint: disable=too-many-return-statements,too-many-branches,too-many-arguments,too-many-positional-arguments
    view: CaseView,
    opts: ConstraintOptions,
    ev: EvalConstraint,
    groups: dict[str, Counter[str]],
    notes: list[str],
) -> dict[str, Any] | None:
    """Translate an evaluated constraint into a gtopt UserConstraint."""
    op = SENSE_OP.get(ev.sense, "<=")
    assert ev.const is not None
    desc = f"PLEXOS Constraint '{ev.name}' [{ev.category}], {ev.rhs_kind}"
    uc: dict[str, Any] = {"name": f"isp_{_slug(ev.name)}", "description": desc}
    if ev.rhs_kind in PERIOD_RHS:
        if np.any(np.abs(ev.const) > _EPS):
            groups[ev.group]["skipped: capacity term in a period constraint"] += 1
            return None
        oid = _constraint_oid(view, ev.name)
        total, periods = prorated_rhs(view.res, oid, ev.rhs_kind, view.day0, view.days)
        if total is None:
            groups[ev.group]["skipped: RHS period does not overlap the window"] += 1
            return None
        if opts.long_periods == "skip" and period_span_days(periods) > LONG_PERIOD_DAYS:
            groups[ev.group][
                "skipped: long-period target (> 1 month) not enforced in a week"
            ] += 1
            notes.append(
                f"{ev.name}: {ev.rhs_kind} over "
                + "; ".join(f"{p['from']}..{p['to']}" for p in periods)
                + " not enforced (--long-periods prorate to enforce the "
                f"pro-rated share {total * 1000.0:,.1f})"
            )
            return None
        rhs = total * 1000.0  # GWh (TJ) → MWh (GJ)
        if not ev.terms:
            _constant_only(ev, rhs, groups)
            return None
        raw = all(t.cls == "Fuel" for t in ev.terms)
        weight = "" if raw else "dur[b] * "
        uc.update(
            {
                "expression": f"sum{{b in stage}} {weight}({lhs_text(ev.terms)})"
                f" {op} {_fmt(rhs)}",
                "scope": "stage",
                "constraint_type": "raw" if raw else "energy",
            }
        )
        uc["description"] += (
            " — APPROXIMATION: period RHS pro-rated to the window "
            + "; ".join(
                f"{p['value']:g} over {p['from']}..{p['to']} × {p['share']:.4f}"
                for p in periods
            )
        )
        groups[ev.group]["converted: window-sum (pro-rated period RHS)"] += 1
        notes.append(
            f"{ev.name}: {ev.rhs_kind} pro-rated → {rhs:,.1f} MWh-equivalent "
            "for the window"
        )
        if ev.penalty is not None:
            uc["penalty"] = ev.penalty / 1000.0
        return uc
    oid = _constraint_oid(view, ev.name)
    prof = view.res.hourly(oid, ev.rhs_kind, view.day0, view.days)
    if prof is None or np.isnan(prof).all():
        groups[ev.group]["skipped: no RHS live in the window"] += 1
        return None
    from .aemo_isp import _fill_nan_from_day_neighbours  # pylint: disable=import-outside-toplevel

    prof = _fill_nan_from_day_neighbours(prof)
    if ev.rhs_kind == "RHS Day":
        daily = prof * 1000.0  # GWh/day (TJ/day) → MWh (GJ) per day
        # constants per block contribute Σ_day const_b (1 h blocks)
        day_const = np.repeat(ev.const.reshape(view.days, 24).sum(axis=1), 24)
        rhs = daily - day_const
        if not ev.terms:
            _constant_only(ev, float(rhs.min()), groups)
            return None
        raw = all(t.cls == "Fuel" for t in ev.terms)
        uc.update(
            {
                "expression": f"{lhs_text(ev.terms)} {op} {_fmt(float(rhs[0]))}",
                "daily_sum": True,
                "constraint_type": "raw" if raw else "energy",
            }
        )
        if not np.allclose(rhs, rhs[0]):
            uc["rhs"] = "rhs"
            uc["_rhs"] = rhs
        if ev.penalty is not None:
            uc["penalty"] = ev.penalty / 1000.0
        groups[ev.group]["converted: daily_sum"] += 1
        return uc
    rhs = prof - ev.const
    if not ev.terms:
        _constant_only(ev, float(rhs.min() if op == "<=" else rhs.max()), groups)
        return None
    uc["expression"] = f"{lhs_text(ev.terms)} {op} {_fmt(float(rhs[0]))}"
    if not np.allclose(rhs, rhs[0]):
        uc["rhs"] = "rhs"
        uc["_rhs"] = rhs
    if ev.penalty is not None:
        uc["penalty"] = ev.penalty
    groups[ev.group]["converted: per block"] += 1
    return uc


def _constant_only(
    ev: EvalConstraint, rhs: float, groups: dict[str, Counter[str]]
) -> None:
    """A constraint with no variable term left: check it, never emit it."""
    assert ev.const is not None
    ok = {
        -1: 0.0 <= rhs + 1e-6,
        0: abs(rhs) <= 1e-6,
        1: 0.0 >= rhs - 1e-6,
    }.get(ev.sense, True)
    if ok:
        groups[ev.group]["skipped: no term in the case (trivially satisfied)"] += 1
    else:
        groups[ev.group]["skipped: no term in the case (VIOLATED by constants)"] += 1
        logger.warning("constraint %s: constant-only and violated", ev.name)


# ---------------------------------------------------------------------------
# hydrogen
# ---------------------------------------------------------------------------


def _h2_demand(
    view: CaseView,
    opts: ConstraintOptions,
    ev: EvalConstraint,
    groups: dict[str, Counter[str]],
    notes: list[str],
) -> dict[str, Any] | None:
    """PLEXOS monthly H2 consumption constraint → HydrogenDemand."""
    oid = _constraint_oid(view, ev.name)
    if ev.rhs_kind not in PERIOD_RHS:
        groups[ev.group]["skipped: H2 constraint without a period RHS"] += 1
        return None
    total, periods = prorated_rhs(view.res, oid, ev.rhs_kind, view.day0, view.days)
    if total is None:
        groups[ev.group]["skipped: RHS period does not overlap the window"] += 1
        return None
    feeders = [t.obj for t in ev.terms if t.cls == "Purchaser"]
    if not feeders:
        ok = total <= _EPS
        groups[ev.group][
            "skipped: no electrolyser in the case"
            + ("" if ok else " (NONZERO H2 demand dropped)")
        ] += 1
        return None
    if any(abs(float(np.max(t.coef)) - 1.0) > 1e-9 for t in ev.terms):
        groups[ev.group]["skipped: non-unit purchaser coefficient"] += 1
        return None
    e_mwh = total * 1000.0 * opts.h2_efficiency  # GWh_e → MWh_LHV
    cap_mwh_e = sum(
        view.purchasers[f].max_load for f in feeders if f in view.purchasers
    ) * (24.0 * view.days)
    if ev.sense in (0, 1) and total * 1000.0 > cap_mwh_e + 1e-6:
        notes.append(
            f"WARNING {ev.name}: window H2 target {total * 1000:,.1f} MWh_e "
            f"exceeds electrolyser capacity {cap_mwh_e:,.1f} MWh_e (infeasible "
            "if hard)"
        )
    item: dict[str, Any] = {
        "name": "h2dem_" + _slug(ev.name),
        "type": "isp_h2_consumption",
        "hydrogen_node": h2_node_name(ev.name),
    }
    if ev.sense in (0, 1):
        item["min_energy"] = round(e_mwh, 6)
    if ev.sense in (0, -1):
        item["max_energy"] = round(e_mwh, 6)
    if ev.penalty is not None:
        item["min_energy_cost"] = ev.penalty / 1000.0 / opts.h2_efficiency
    groups[ev.group]["converted: HydrogenDemand (pro-rated period RHS)"] += 1
    notes.append(
        f"{ev.name}: {ev.rhs_kind} pro-rated → {total * 1000:,.1f} MWh_e "
        f"= {e_mwh:,.1f} MWh_LHV H2 for the window ({len(feeders)} electrolysers; "
        + "; ".join(f"{p['value']:g} GWh {p['from']}..{p['to']}" for p in periods)
        + ")"
    )
    return item


# ---------------------------------------------------------------------------
# emissions
# ---------------------------------------------------------------------------


def build_emissions(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    view: CaseView,
    index: ConstraintIndex,
    opts: ConstraintOptions,
    stats: dict[str, Any],
    groups: dict[str, Counter[str]],
    notes: list[str],
) -> None:
    """Emission registry, per-generator sources and pro-rated budget zones.

    Rates: generator ``Production Rate`` [kg/MWh gross] plus fuel
    ``Production Rate`` [kg/GJ] × heat rate [GJ/MWh gross], divided by
    ``1 − Aux Incr`` (gtopt generation is sent out) → t/MWh.  Budgets:
    PLEXOS ``Production Coefficient`` × production [t] ≤ RHS Custom, so a
    zone weight = coefficient and cap = RHS pro-rated to the window.
    A system-wide reporting zone ``isp_co2_total`` (no cap, no price)
    always collects every source.  PLEXOS emission prices are kept only
    when non-zero (none in the 2026 ISP model).
    """
    res, day0 = view.res, view.day0
    emis = sorted(res.objects("Emission"), key=lambda o: o.name)
    if not emis:
        return
    gen_oid = {o.name: o.object_id for o in res.objects("Generator")}
    fuel_oid = {o.name: o.object_id for o in res.objects("Fuel")}
    rate: dict[tuple[str, str], float] = defaultdict(float)  # (gen, emission)
    for e in emis:
        for g in view.gens.values():
            oid = gen_oid.get(g.name)
            if oid is None:
                continue
            kg = 0.0
            row = res.select_row(
                res.coll_rows.get(
                    (e.object_id, oid, "Generators::Production Rate"), []
                ),
                day0,
            )
            if row is not None and row.data_file is None:
                kg += row.value
            if g.fuel is not None and g.heat_rate:
                frow = res.select_row(
                    res.coll_rows.get(
                        (
                            e.object_id,
                            fuel_oid.get(g.fuel, -1),
                            "Fuels::Production Rate",
                        ),
                        [],
                    ),
                    day0,
                )
                if frow is not None and frow.data_file is None:
                    kg += frow.value * g.heat_rate
            if kg > 0:
                rate[(g.name, e.name)] = kg / 1000.0 / max(1.0 - g.aux, _EPS)
    if not rate:
        return
    zones: list[dict[str, Any]] = [
        {
            "uid": 1,
            "name": "isp_co2_total",
            "emissions": [{"emission": e.name, "weight": 1.0} for e in emis],
        }
    ]
    for e in emis:
        price = res.scalar(e.object_id, "Price", day0, default=0.0) or 0.0
        if price > 0:
            zones[0].setdefault("price_by_emission", {})[e.name] = price
    zones[0].pop("price_by_emission", None)  # no per-emission price in gtopt
    # budgets
    for o in sorted(res.objects("Constraint"), key=lambda o: o.name):
        if classify(res, index, o.object_id) != "emissions":
            continue
        include = res.scalar(o.object_id, "Include in LT Plan", day0, -1.0)
        if include == 0:
            groups["emissions"]["skipped: disabled (Include in LT Plan = 0)"] += 1
            continue
        kind = rhs_kind(res, o.object_id, day0, view.days) or "RHS Custom"
        total, periods = prorated_rhs(res, o.object_id, kind, day0, view.days)
        if total is None:
            groups["emissions"][
                "skipped: budget period does not overlap the window"
            ] += 1
            continue
        weights: dict[str, float] = {}
        for parent, prop, rows in index.coefs[o.object_id]:
            got = _row_coef(view, rows)
            if prop != "Production Coefficient" or got is None:
                continue
            weights[res.obj[parent].name] = float(np.max(got[0]))
        if not weights:
            groups["emissions"]["skipped: no live coefficient"] += 1
            continue
        # normalise to tonnes: weight 1 ⇒ cap in t
        k = 1.0 / max(weights.values())
        sense = int(res.scalar(o.object_id, "Sense", day0, default=-1.0) or 0.0)
        if sense != -1:
            groups["emissions"]["skipped: non ≤ emission constraint"] += 1
            continue
        pen = res.scalar(o.object_id, "Penalty Price", day0, default=-1.0)
        zone: dict[str, Any] = {
            "uid": len(zones) + 1,
            "name": "isp_" + _slug(o.name),
            "emissions": [
                {"emission": n, "weight": round(w * k, 12)} for n, w in weights.items()
            ],
        }
        capped = (
            opts.long_periods == "prorate"
            or period_span_days(periods) <= LONG_PERIOD_DAYS
        )
        if capped:
            zone["cap"] = round(total * k, 3)
            if pen is not None and pen > 0:
                zone["cap_cost"] = pen / k
            groups["emissions"]["converted: EmissionZone cap (pro-rated budget)"] += 1
        else:
            groups["emissions"][
                "converted: EmissionZone tracking only (long-period budget, no cap)"
            ] += 1
        zones.append(zone)
        notes.append(
            f"{o.name}: {kind} pro-rated → {'cap' if capped else 'reference (not enforced)'}"
            f" {total * k:,.0f} t for the window ("
            + "; ".join(
                f"{p['value']:g} × {1 / k:g} t over {p['from']}..{p['to']} × "
                f"{p['share']:.5f}"
                for p in periods
            )
            + ") — APPROXIMATION"
        )
    sources: list[dict[str, Any]] = []
    for z in zones:
        covered = {f["emission"] for f in z["emissions"]}
        for (gname, e), r in sorted(rate.items()):
            if e not in covered:
                continue
            sources.append(
                {
                    "uid": len(sources) + 1,
                    "name": f"{gname}|{z['name']}|{e}",
                    "generator": gname,
                    "zone": z["name"],
                    "emission": e,
                    "rate": round(r, 9),
                }
            )
    view.system["emission_array"] = [
        {"uid": i + 1, "name": e.name} for i, e in enumerate(emis)
    ]
    view.system["emission_zone_array"] = zones
    view.system["emission_source_array"] = sources
    stats["emission_sources"] = len(sources)
    stats["emission_zones"] = len(zones)
    stats["emitting_generators"] = len({g for g, _ in rate})


# ---------------------------------------------------------------------------
# REZ buses
# ---------------------------------------------------------------------------


def _rez_key(name: str) -> str:
    for pre in ("ExportLimit_", "ImportLimit_"):
        if name.startswith(pre):
            return name[len(pre) :]
    return name


def build_rez_buses(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    view: CaseView,
    evaluated: list[EvalConstraint],
    stats: dict[str, Any],
    groups: dict[str, Counter[str]],
) -> set[str]:
    """Turn REZ export/import limits on the REZ's own injections into lines.

    A REZ qualifies when its export (and, if enabled, import) limit is a
    hard per-block ``≤`` constraint whose variable terms are exactly
    ``+1 × injection`` (generator sent-out generation, battery discharge)
    and ``−1 × withdrawal`` (battery charge, electrolyser load) for the
    export limit (signs flipped for the import limit), on elements that
    all sit on one sub-region bus and belong to no other qualifying REZ.
    Constant terms (capacity, fixed loads) move into the line limit.
    Returns the names of the constraints replaced by lines.
    """
    by_rez: dict[str, dict[str, EvalConstraint]] = defaultdict(dict)
    for ev in evaluated:
        side = REZ_CATEGORIES.get(ev.category)
        if side is not None:
            by_rez[_rez_key(ev.name)][side] = ev
    elem_bus = _element_buses(view)
    owner: dict[tuple[str, str], str] = {}
    plan: dict[str, tuple[set[tuple[str, str]], str]] = {}
    reasons: Counter[str] = Counter()
    for rez, sides in sorted(by_rez.items()):
        why = _rez_reject_reason(sides, elem_bus)
        if why is not None:
            reasons[why] += 1
            continue
        elems = {(t.cls, t.obj) for ev in sides.values() for t in ev.terms}
        buses = {elem_bus[e] for e in elems}
        if len(buses) != 1:
            reasons["elements on several sub-region buses"] += 1
            continue
        clash = [e for e in elems if e in owner]
        if clash:
            reasons["element shared with another REZ"] += 1
            plan.pop(owner[clash[0]], None)
            continue
        for e in elems:
            owner[e] = rez
        plan[rez] = (elems, buses.pop())
    done: set[str] = set()
    bus_array = view.system["bus_array"]
    line_array = view.system["line_array"]
    nb = view.nb
    rez_stats: dict[str, Any] = {}
    for rez, (elems, sub_bus) in sorted(plan.items()):
        sides = by_rez[rez]
        limits: dict[str, np.ndarray] = {}
        for side, ev in sides.items():
            prof = view.res.hourly(
                _constraint_oid(view, ev.name), ev.rhs_kind, view.day0, view.days
            )
            assert prof is not None and ev.const is not None
            from .aemo_isp import _fill_nan_from_day_neighbours  # pylint: disable=import-outside-toplevel

            const = ev.const.copy()
            for pname, c in ev.fixed_loads:
                fixed = view.purchasers[pname].fixed
                assert fixed is not None
                const -= c * fixed  # the fixed load moves to the REZ bus
                elems.add(("Purchaser", pname))
            limits[side] = _fill_nan_from_day_neighbours(prof) - const
        if any(v.min() < -1e-6 for v in limits.values()):
            reasons["negative limit after folding constants"] += 1
            continue
        bname = f"REZ_{_slug(rez)}"
        bus_array.append({"uid": len(bus_array) + 1, "name": bname})
        uid = len(line_array) + 1
        exp = limits.get("export")
        imp = limits.get("import")
        cap_load = _rez_load_capacity(view, elems)
        cap_gen = _rez_gen_capacity(view, elems)
        tab = np.clip(exp, 0, None) if exp is not None else np.full(nb, cap_gen + 1.0)
        tba = np.clip(imp, 0, None) if imp is not None else np.full(nb, cap_load + 1.0)
        line_array.append(
            {
                "uid": uid,
                "name": f"rez_{_slug(rez)}",
                "type": "rez",
                "bus_a": bname,
                "bus_b": sub_bus,
                "tmax_ab": "tmax_ab",
                "tmax_ba": "tmax_ba",
            }
        )
        view.sched[("Line", "tmax_ab")][uid] = tab
        view.sched[("Line", "tmax_ba")][uid] = tba
        _move_elements(view, elems, bname)
        for side, ev in sides.items():
            done.add(ev.name)
            groups[ev.group][f"converted: REZ radial line ({side} limit)"] += 1
        rez_stats[rez] = {
            "bus": bname,
            "sub_region": sub_bus,
            "elements": len(elems),
            "export_mw": [float(tab.min()), float(tab.max())],
            "import_mw": [float(tba.min()), float(tba.max())],
            "import_limit": imp is not None,
        }
    stats["rez_buses"] = rez_stats
    stats["rez_not_line_equivalent"] = dict(reasons)
    return done


def _element_buses(view: CaseView) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for g in view.gens.values():
        out[("Generator", g.name)] = g.bus
    for b, bus in view.batteries.items():
        out[("Battery", b)] = bus
    for p in view.purchasers.values():
        out[("Purchaser", p.name)] = p.bus
    return out


def _rez_reject_reason(
    sides: dict[str, EvalConstraint], elem_bus: dict[tuple[str, str], str]
) -> str | None:
    if "export" not in sides:
        return "import limit without an export limit"
    sets = []
    for side, ev in sides.items():
        if ev.rhs_kind != "RHS" or ev.sense != -1 or ev.penalty is not None:
            return "not a hard per-block <= limit"
        if not ev.terms:
            return "no element in the case"
        want = 1 if side == "export" else -1
        for t in ev.terms:
            if t.inj == 0 or (t.cls, t.obj) not in elem_bus:
                return "term is not a REZ injection (flow / fuel / other)"
            if isinstance(t.coef, np.ndarray) or abs(t.coef - want * t.inj) > 1e-9:
                return "non-unit coefficient"
        want_fixed = -want
        if any(abs(c - want_fixed) > 1e-9 for _, c in ev.fixed_loads):
            return "non-unit coefficient"
        sets.append(
            {(t.cls, t.obj) for t in ev.terms}
            | {("Purchaser", n) for n, _ in ev.fixed_loads}
        )
    if len(sets) == 2 and sets[0] != sets[1]:
        return "export and import limits cover different elements"
    return None


def _rez_load_capacity(view: CaseView, elems: set[tuple[str, str]]) -> float:
    tot = 0.0
    for cls, name in elems:
        if cls == "Purchaser":
            tot += view.purchasers[name].max_load
        elif cls == "Battery":
            for b in view.system.get("battery_array", []):
                if b["name"] == name:
                    tot += float(b["pmax_charge"])
    return tot


def _rez_gen_capacity(view: CaseView, elems: set[tuple[str, str]]) -> float:
    tot = 0.0
    for cls, name in elems:
        if cls == "Generator":
            for g in view.system["generator_array"]:
                if g["name"] == name:
                    tot += float(g.get("capacity", 0.0))
        elif cls == "Battery":
            for b in view.system.get("battery_array", []):
                if b["name"] == name:
                    tot += float(b["pmax_discharge"])
    return tot


def _move_elements(view: CaseView, elems: set[tuple[str, str]], bus: str) -> None:
    names = defaultdict(set)
    for cls, n in elems:
        names[cls].add(n)
    for g in view.system["generator_array"]:
        if g["name"] in names["Generator"]:
            g["bus"] = bus
            view.gens[g["name"]].bus = bus
    for b in view.system.get("battery_array", []):
        if b["name"] in names["Battery"]:
            b["bus"] = bus
            view.batteries[b["name"]] = bus
    for d in view.system.get("demand_array", []):
        if d["name"] in names["Purchaser"]:
            d["bus"] = bus
    for c in view.system.get("carrier_converter_array", []):
        if c["name"] in names["Purchaser"]:
            c["from_node"] = bus
    for n in names["Purchaser"]:
        view.purchasers[n].bus = bus
