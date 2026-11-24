# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Native gtopt reserve model for CEN's PLEXOS PCP (``--reserve-model``).

CEN's PCP sizes the primary-frequency reserve (CPF) with linear
regressions written as PLEXOS ``Constraint`` objects over ``Decision
Variable`` objects (minuta GM-DPRO Nº05-2026, Anexo B):

``CPF_Up10Calculation_I..XII`` / ``CPF_Up5Calculation`` / ``CPF_DownCalculation``
    ``R + a·G + b·I − c·C >= RHS`` (one row per plane), ``R`` the
    requirement DV (``CPF10s_Requirement``, …), ``G`` = ``Generation_SEN``
    [GW], ``I`` = ``Inertia_SEN`` [GVAs], ``C`` = ``MaxInjection_SEN`` /
    ``MaxLoad_SEN`` [MW].
``CPF_Up10MinProvision`` / ``CPF_UpMinProvision`` / ``CPF_DownMinProvision``
    ``Σ f_g·r_g + VoRS − R [− Slack] >= 0`` (per-unit effectiveness
    factors ``f_g``, priced shortage ``VoRS``).
``Generation_Calculation`` / ``Inertia_Calculation_e1`` / ``*_Comparison``
    the definitions of ``G`` (``Σ gen − charge − 1000·G``), ``I``
    (``Σ H·S·u − 1000·I >= −const``) and ``C`` (``C >= p_g``, bounds
    ``[300, 400]`` / ``[200, 400]``).

With ``--reserve-model native`` (default) those rows become gtopt's
native plane-based requirement (``ReserveZone.urreq_planes`` /
``drreq_planes``, see ``docs/formulation`` §5.9):

* one ``ReserveZone`` per requirement DV (``CPF10s``, ``CPF5mUp``,
  ``CPF5mDown``) with one plane per regression row; dated coefficient sets
  (PLEXOS ``Date From`` / ``Date To``) become per-hour schedules;
* ``demand_coef = a/k_G`` (``k_G = −1000`` ⇒ ``−a/1000``), ``inertia_coef
  = b/k_I``, ``contingency_coef = c/k_C``; the inertia constant of units
  without a gtopt commitment (``−RHS`` of the inertia row) and the
  contingency lower bound (``MaxInjection_SEN >= 300``, encoded as one
  extra plane per plane with ``C`` fixed at the bound) fold into
  ``alpha``;
* the load term ``D`` is **exogenous** by default: ``demand_load_factor``
  (1.05) × Σ demand ``lmax`` — or a given ``system_load`` — on the row
  RHS, so the bus balances never reach the requirement and the LMP stays
  energy-only.  ``--reserve-load-term endogenous`` reproduces PLEXOS
  (``load_term = "endogenous_generation"``, ``D = Σ p − Σ charge``);
* ``InertiaZone`` ``Inertia_SEN`` + one ``InertiaProvision`` per unit of
  the inertia row (``pf = H·S / P_ref``, ``provision_max = P_ref``, so a
  committed unit counts ``H·S`` MWs);
* one ``urcontingencies`` / ``drcontingencies`` candidate per
  ``*_Comparison`` row (plant modes, ``<bat>_gen`` / ``<bat>_dem`` for
  BESS, line flows, constant offset; the shared
  ``limited_generation_group`` DV expanded through its defining row);
* the provision rows' factors become ``ur_zone_factors`` /
  ``dr_zone_factors`` on the units' existing provisions; the ``VoRS`` DV
  cost becomes ``urcost`` / ``drcost``; a non-zero cost on the
  requirement DV (PLEXOS charges 10 $/MW of requirement) is kept through
  a one-term link row ``R_dv = reserve_zone(Z).up``;
* pure provision-sum rows (``Σ c_g·r_g [+ VoRS] >= RHS``, e.g.
  ``CSF_UpMinProvision`` / ``CTF_*MinProvision``) become fixed-requirement
  zones (``urreq`` / ``drreq`` = RHS, cost = the row penalty).

Every replaced row stays in the JSON as an inactive provenance stub.
Only ACTIVE rows are read: every row with ``Include in ST Schedule <= 0``
(extracted ``active=False``) — notably the northern-corridor family
``SpecialCPF/CSF/CTFNorth(LW)``, ``*_Definition``, ``SpecialCPFTxFlow``,
``CPF_North_Tx``, whose requirement is the endogenous corridor flow — is
never converted and stays an inactive stub.  Rows outside these patterns
(``CSF_*_Def`` with an endogenous DV, and the comparison rows themselves,
which also cap ``C`` at 400 MW) are left untouched.  ``--reserve-model
uc`` keeps the legacy UserConstraint emulation.
"""

from __future__ import annotations

import csv
import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .entities import (
    InertiaCreditSpec,
    InertiaProvisionSpec,
    InertiaZoneSpec,
    PlexosCase,
    ReserveContingencySpec,
    ReservePlaneSpec,
    ReserveProvisionSpec,
    ReserveSpec,
    UserConstraintSpec,
)
from .plexos_model import timeslice_matcher
from .plexos_xml import PlexosDataRow, PlexosDb

logger = logging.getLogger(__name__)

#: ``--reserve-model`` choices.
RESERVE_MODELS = ("native", "uc")
#: ``--reserve-load-term`` choices.
RESERVE_LOAD_TERMS = ("exogenous", "endogenous")
#: ``--reserve-inertia-term`` choices (first = default): ``"commitment"``
#: (``I = Σ I_c·u_c`` from the InertiaProvisions and the unit status),
#: ``"exogenous"`` (``system_inertia`` = must-run H·S, data) and
#: ``"endogenous"`` (PLEXOS-compat dispatch provision, reconciliation).
RESERVE_INERTIA_TERMS = ("commitment", "exogenous", "endogenous")
#: Inertia terms that read ``I`` from the ``Inertia_SEN`` InertiaZone.
_ZONE_INERTIA_TERMS = ("commitment", "endogenous")
#: Default ``--reserve-load-factor``.  (Gen − BESS charge)/demand has median
#: 1.025–1.032 over the CEN PCP 2024–2026 history, but PLEXOS'
#: Generation_SEN / Σ demand reaches 1.044–1.052 on single days (2026-10-24,
#: 2026-11-07); 1.05 errs on the side of a slightly larger load term.
DEFAULT_RESERVE_LOAD_FACTOR = 1.05
#: PLEXOS DV names of the regressors (CEN PCP).
DEFAULT_GENERATION_DV = "Generation_SEN"
DEFAULT_INERTIA_DV = "Inertia_SEN"
#: MW → DV scale fallback (``Generation_SEN`` / ``Inertia_SEN`` are in GW
#: / GVAs: ``k = −1000`` in their defining rows).
_DEFAULT_DEF_COEF = -1000.0
#: Inertia reference power [MW] of a unit without ``Pmin`` (see
#: :func:`_inertia_ref_power`).
_INERTIA_REF_MW = 1.0
_NOTE = "--reserve-model native"
#: PLEXOS "no value / no limit" sentinel (``±1e30``): ``|v| >= 1e20`` is
#: treated as absent, as the extractor's ``_horizon_value`` does.
_NO_VALUE = 1.0e20
_FLOOR_TAG = " (contingency at its lower bound)"
#: CEN's northern-corridor rows (all ``Include in ST Schedule = 0``).
_NORTH_RE = re.compile(r"^Special(CPF|CSF|CTF)North(LW)?$")

_NUM = r"[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?"
_TERM_RE = re.compile(
    rf"\s*(?P<sign>[+-]?)\s*(?:(?P<num>{_NUM})\s*\*\s*)?"
    r'(?P<kind>[a-z_]+)\("(?P<ref>[^"]+)"\)\.(?P<attr>[a-z_]+)\s*'
)
_TAIL_RE = re.compile(rf"(>=|<=|=)\s*([+-]?\s*{_NUM})\s*;?\s*$")


def plane_support_issues(zone: ReserveSpec) -> list[str]:
    """Plane terms of ``zone`` without the support gtopt requires.

    Mirrors gtopt's strict-mode checks (``validate_planning``): an
    ``inertia_coef`` needs ``system_inertia`` (exogenous) or an
    ``inertia_zone``; a ``contingency_coef`` needs a candidate in its
    direction and must be ≥ 0; a ``flow_coef`` needs ``flow_lines``.
    """

    def uses(planes: tuple[ReservePlaneSpec, ...], key: str) -> bool:
        return any(_nonzero(getattr(p, key)) for p in planes)

    both = (*zone.urreq_planes, *zone.drreq_planes)
    out: list[str] = []
    exo = zone.inertia_term == "exogenous" and bool(zone.system_inertia)
    if uses(both, "inertia_coef") and not exo and not zone.inertia_zone:
        out.append(f"{zone.name}: inertia_coef without system_inertia/inertia_zone")
    for planes, cands, singles, tag in (
        (zone.urreq_planes, zone.urcontingencies, zone.contingency_generators, "up"),
        (zone.drreq_planes, zone.drcontingencies, zone.contingency_demands, "down"),
    ):
        if uses(planes, "contingency_coef") and not cands and not singles:
            out.append(f"{zone.name}: {tag} contingency_coef without candidates")
    for p in both:
        coef = p.contingency_coef
        values = coef if isinstance(coef, tuple) else (coef or 0.0,)
        if min(values) < 0.0:
            out.append(f"{zone.name}: negative contingency_coef ({p.source})")
    if uses(both, "flow_coef") and not zone.flow_lines:
        out.append(f"{zone.name}: flow_coef without flow_lines")
    return out


def _nonzero(value: float | tuple[float, ...] | None) -> bool:
    if value is None:
        return False
    if isinstance(value, tuple):
        return any(v != 0.0 for v in value)
    return value != 0.0


# ── options ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class NativeReserveOptions:
    """Resolved ``--reserve-*`` options."""

    model: str = "native"
    load_term: str = "exogenous"
    load_factor: float = DEFAULT_RESERVE_LOAD_FACTOR
    #: Plane inertia term: ``"commitment"`` (default, ``I = Σ I_c·u_c``),
    #: ``"exogenous"`` (``system_inertia`` = must-run inertia) or
    #: ``"endogenous"`` (PLEXOS-compat dispatch provision).
    inertia_term: str = "commitment"
    #: Given system load [MW] per hour (``--reserve-system-load``).
    system_load: tuple[float, ...] = ()
    #: Convert the ST-excluded northern-corridor rows (opt-in).
    north_special: bool = False

    @property
    def endogenous(self) -> bool:
        """``True`` for the PLEXOS-compatible endogenous load term."""
        return self.load_term == "endogenous"

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> NativeReserveOptions:
        """Build from the converter ``options`` dict (CLI keys)."""
        model = str(options.get("reserve_model") or "native")
        load_term = str(options.get("reserve_load_term") or "exogenous")
        inertia_term = str(options.get("reserve_inertia_term") or "commitment")
        if inertia_term not in RESERVE_INERTIA_TERMS:
            raise ValueError(f"unknown reserve_inertia_term {inertia_term!r}")
        if model not in RESERVE_MODELS:
            raise ValueError(f"unknown reserve_model {model!r}")
        if load_term not in RESERVE_LOAD_TERMS:
            raise ValueError(f"unknown reserve_load_term {load_term!r}")
        factor = options.get("reserve_load_factor")
        load_factor = DEFAULT_RESERVE_LOAD_FACTOR if factor is None else float(factor)
        if load_factor <= 0.0:
            raise ValueError(f"reserve_load_factor must be > 0: {load_factor}")
        csv_path = options.get("reserve_system_load")
        endogenous = load_term == "endogenous"
        if factor is not None and (endogenous or csv_path):
            logger.warning(
                "%s: --reserve-load-factor ignored (%s)",
                _NOTE,
                "endogenous load term" if endogenous else "--reserve-system-load given",
            )
        if csv_path and endogenous:
            logger.warning(
                "%s: --reserve-system-load ignored (endogenous load term)", _NOTE
            )
        system_load = (
            read_system_load_csv(Path(csv_path)) if csv_path and not endogenous else ()
        )
        return cls(
            model=model,
            load_term=load_term,
            inertia_term=inertia_term,
            load_factor=load_factor,
            system_load=system_load,
            north_special=bool(options.get("reserve_north_special")),
        )


def read_system_load_csv(path: Path) -> tuple[float, ...]:
    """Per-hour system load [MW] for ``--reserve-system-load``.

    Column ``system_load`` / ``value`` [MW], or ``generation_sen`` [GW,
    PLEXOS DV units, ×1000], else the last column; rows sorted by an
    optional ``hour`` column.  Every value must be a finite number > 0
    (``ValueError`` naming the file and row otherwise); the length is
    checked against the horizon by :func:`apply_native_reserve`.
    """
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: empty system-load CSV")
    cols = list(rows[0].keys())
    lower = {c.strip().lower(): c for c in cols}
    scale = 1.0
    col = lower.get("system_load") or lower.get("value")
    if col is None and "generation_sen" in lower:
        col, scale = lower["generation_sen"], 1000.0
    col = col or cols[-1]
    if "hour" in lower:
        hcol = lower["hour"]
        rows.sort(key=lambda r: int(float(r[hcol])))
    out: list[float] = []
    for i, r in enumerate(rows):
        raw = (r.get(col) or "").strip()
        try:
            value = scale * float(raw)
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                f"{path}: data row {i + 1}: system load {raw!r} in column "
                f"{col!r} is not a finite number > 0"
            )
        out.append(value)
    return tuple(out)


# ── expression parsing ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Term:
    """``coef * kind("ref").attr``."""

    coef: float
    kind: str
    ref: str
    attr: str


@dataclass(frozen=True)
class Row:
    """A parsed user constraint."""

    spec: UserConstraintSpec
    terms: tuple[Term, ...]
    op: str
    rhs: float

    def coef(self, kind: str, ref: str) -> float:
        """Summed coefficient of ``kind(ref)`` (0 when absent)."""
        return sum(t.coef for t in self.terms if t.kind == kind and t.ref == ref)

    def of_kind(self, kind: str) -> list[Term]:
        """Terms of one element kind."""
        return [t for t in self.terms if t.kind == kind]

    @property
    def at_least(self) -> bool:
        """``>=`` or ``=`` row: its LHS is bounded below by the RHS."""
        return self.op in (">=", "=")

    def dvs(self) -> set[str]:
        """Decision variables referenced by the row."""
        return {t.ref for t in self.terms if t.kind == "decision_variable"}


def parse_expression(expression: str) -> tuple[tuple[Term, ...], str, float] | None:
    """Split ``Σ coef * kind("ref").attr <op> rhs`` into terms.

    Returns ``None`` when the expression is not a plain linear sum (sum
    operators, parameters, …) so callers leave such rows alone.
    """
    tail = _TAIL_RE.search(expression)
    if tail is None:
        return None
    lhs = expression[: tail.start()]
    terms: list[Term] = []
    pos = 0
    for m in _TERM_RE.finditer(lhs):
        if m.start() != pos or (terms and not m.group("sign")):
            return None
        pos = m.end()
        coef = float(m.group("num")) if m.group("num") else 1.0
        if m.group("sign") == "-":
            coef = -coef
        terms.append(Term(coef, m.group("kind"), m.group("ref"), m.group("attr")))
    if not terms or lhs[pos:].strip():
        return None
    return tuple(terms), tail.group(1), float(tail.group(2).replace(" ", ""))


def _parse_rows(ucs: Iterable[UserConstraintSpec]) -> dict[str, Row]:
    out: dict[str, Row] = {}
    for uc in ucs:
        if uc.active is False or uc.daily_sum or uc.scope:
            continue
        parsed = parse_expression(uc.expression)
        if parsed is not None:
            out[uc.name] = Row(uc, *parsed)
    return out


# ── dated (Date From / Date To) values ─────────────────────────────────────
class DatedIndex:
    """``(constraint, member, property) → data rows`` of a PLEXOS DB."""

    def __init__(self, db: PlexosDb | None) -> None:
        self.start: datetime | None = db.horizon_start if db is not None else None
        self._rows: dict[tuple[str, str, str], list[PlexosDataRow]] = {}
        self._by_prop: dict[tuple[str, str], list[PlexosDataRow]] = {}
        #: Constraints that have at least one data row (known to the DB).
        self.constraints: set[str] = set()
        if db is None:
            return
        objs = db.object_by_id()
        props = {p.property_id: p.name for p in db.properties}
        con_id = db.classes_by_name.get("Constraint")
        mem_pair = {
            m.membership_id: (m.parent_object_id, m.child_object_id)
            for m in db.memberships
        }
        for row in db.data_rows:
            pair = mem_pair.get(row.membership_id)
            if pair is None:
                continue
            parent, child = objs.get(pair[0]), objs.get(pair[1])
            if parent is None or child is None or child.class_id != con_id:
                continue
            key = (child.name, parent.name, props.get(row.property_id, ""))
            self._rows.setdefault(key, []).append(row)
            self._by_prop.setdefault((key[0], key[2]), []).append(row)
            self.constraints.add(child.name)

    def rows(self, constraint: str, member: str, prop: str) -> list[PlexosDataRow]:
        """Data rows of ``member``'s ``prop`` on ``constraint``."""
        return self._rows.get((constraint, member, prop), [])

    def prop_rows(self, constraint: str, prop: str) -> list[PlexosDataRow]:
        """``prop`` rows of ``constraint`` (any parent object)."""
        return self._by_prop.get((constraint, prop), [])

    def rhs_rows(self, constraint: str) -> list[PlexosDataRow]:
        """``RHS`` rows of ``constraint`` (any parent object)."""
        return self.prop_rows(constraint, "RHS")

    def sense(self, constraint: str) -> float | None:
        """PLEXOS ``Sense`` (−1 ``<=``, 0 ``=``, 1 ``>=``; unset = 0).

        ``None`` when the DB is unknown or does not hold ``constraint``
        (the extracted operator is then all there is; callers use it and
        say so, see :meth:`_Builder._is_equality`).
        """
        if constraint not in self.constraints:
            return None
        rows = self.prop_rows(constraint, "Sense")
        return min(rows, key=lambda r: r.data_id).value if rows else 0.0


def dated_row(
    rows: Sequence[PlexosDataRow], when: datetime, skip_sentinel: bool = True
) -> PlexosDataRow | None:
    """The row in force on ``when``'s day.

    Dated rows apply on ``[date_from, date_to]`` (whole days, closed; an
    open end runs until superseded); when several overlap the latest
    ``date_from`` wins.  Undated rows (lowest ``data_id``) apply outside
    every window.  A "no value" sentinel row (``|v| >= 1e20``) counts as
    absent unless ``skip_sentinel`` is false (a row whose value is a data
    file reference, or whose sentinel means "no limit").  ``None`` when no
    row applies.
    """
    usable = [r for r in rows if not skip_sentinel or abs(r.value) < _NO_VALUE]
    dated = [
        r
        for r in usable
        if (r.date_from is not None or r.date_to is not None) and _in_window(r, when)
    ]
    if dated:
        return max(dated, key=lambda r: (r.date_from or datetime.min, r.data_id))
    undated = [r for r in usable if r.date_from is None and r.date_to is None]
    if undated:
        return min(undated, key=lambda r: r.data_id)
    return None


def resolve_dated(rows: Sequence[PlexosDataRow], when: datetime) -> float | None:
    """Value in force on ``when``'s day (:func:`dated_row`); ``None`` if none."""
    row = dated_row(rows, when)
    return None if row is None else row.value


def slice_matcher(tag: str) -> Callable[[datetime, int], bool] | None:
    """PLEXOS timeslice tag → ``f(day, period)``; ``;`` OR-s sub-patterns.

    ``"M1-3,H9-19;M10-12,H9-19"`` = (months 1-3 and hours 9-19) or
    (months 10-12 and hours 9-19).  ``None`` when a part is not
    understood (e.g. a named Timeslice object).
    """
    parts = [timeslice_matcher(p) for p in tag.split(";") if p.strip()]
    if not parts or any(m is None for m in parts):
        return None
    matchers = [m for m in parts if m is not None]
    return lambda day, period: any(m(day, period) for m in matchers)


def _finite(value: float | None) -> float | None:
    """``value``, or ``None`` for a missing / "no limit" (``|v| >= 1e20``) one."""
    return None if value is None or abs(value) >= _NO_VALUE else value


def _in_window(row: PlexosDataRow, when: datetime) -> bool:
    day = when.date()
    return (row.date_from is None or row.date_from.date() <= day) and (
        row.date_to is None or day <= row.date_to.date()
    )


def hourly_values(
    rows: Sequence[PlexosDataRow],
    start: datetime | None,
    n_hours: int,
    fallback: float,
) -> tuple[float, ...]:
    """Per-hour value of a (possibly dated / timesliced) property.

    Untagged rows resolve by date (:func:`resolve_dated`, ``fallback`` if
    none applies); a timeslice-tagged row (``H9-19``, ``M4-9,H10-17``)
    overrides them in the hours its pattern and date window cover, the
    highest ``data_id`` winning among overlapping tags.  Unknown tags and
    "no value" sentinel rows (``|v| >= 1e20``) are ignored.
    """
    if start is None or not rows:
        return (fallback,) * n_hours
    plain = [r for r in rows if not r.timeslice]
    tagged: list[tuple[PlexosDataRow, Callable[[datetime, int], bool]]] = []
    tagged_rows = (r for r in rows if r.timeslice and abs(r.value) < _NO_VALUE)
    for r in sorted(tagged_rows, key=lambda r: r.data_id):
        match = slice_matcher(str(r.timeslice))
        if match is not None:
            tagged.append((r, match))
    out: list[float] = []
    for h in range(n_hours):
        when = start + timedelta(hours=h)
        val = resolve_dated(plain, when)
        val = fallback if val is None else val
        for r, match in tagged:
            if _in_window(r, when) and match(when, when.hour + 1):
                val = r.value
        out.append(val)
    return tuple(out)


def _compress(values: tuple[float, ...]) -> float | tuple[float, ...] | None:
    """Scalar when constant, ``None`` when identically 0 (or empty), else
    the profile."""
    if not values:
        return None
    if all(math.isclose(v, values[0], rel_tol=0.0, abs_tol=1e-12) for v in values):
        return None if values[0] == 0.0 else values[0]
    return values


def _blocks_to_hours(
    profile: tuple[float, ...],
    layout: tuple[tuple[int, ...], ...],
    n_hours: int,
    where: str = "profile",
) -> tuple[float, ...]:
    """Expand a per-block profile to hours (identity when already hourly).

    A per-block profile (``len(layout)`` values) is spread over its hours;
    a whole-day pattern starting at the horizon start is repeated (the
    24-value ``Res_Requirement`` day on a multi-day horizon) or cut to the
    horizon (its 168-hour week on a 1-day run).  ``ValueError`` naming
    ``where`` for any other length (never padded).
    """
    if len(profile) == n_hours:
        return profile
    if layout and len(profile) == len(layout):
        return _spread(profile, layout, n_hours)
    if profile and len(profile) % 24 == 0:
        # Whole days from the horizon start: a one-day pattern repeats, a
        # longer one (CEN's 168-hour Res_Requirement week on a 1-day run)
        # is cut to the horizon.
        if n_hours % len(profile) == 0:
            return profile * (n_hours // len(profile))
        if len(profile) > n_hours:
            return profile[:n_hours]
    blocks = f" or {len(layout)} blocks" if layout else ""
    raise ValueError(
        f"{where}: RHS profile has {len(profile)} values, expected "
        f"{n_hours} hours{blocks} (or a whole-day pattern)"
    )


def _spread(
    profile: tuple[float, ...],
    layout: tuple[tuple[int, ...], ...],
    n_hours: int,
) -> tuple[float, ...]:
    hours = [0.0] * n_hours
    for value, intervals in zip(profile, layout, strict=True):
        for i in intervals:
            if 1 <= i <= n_hours:
                hours[i - 1] = value
    return tuple(hours)


@dataclass
class _CandidateTerms:
    """Accumulates the terms of one contingency candidate."""

    offset: float = 0.0
    gens: dict[str, float] = field(default_factory=dict)
    dems: dict[str, float] = field(default_factory=dict)
    lines: dict[str, float] = field(default_factory=dict)

    def add(self, t: Term, coef: float) -> bool:
        """Add ``coef·x`` for a unit / line term; ``False`` otherwise."""
        if t.kind == "generator" and t.attr == "generation":
            bucket, name = self.gens, t.ref
        elif t.kind == "battery" and t.attr == "discharge":
            bucket, name = self.gens, f"{t.ref}_gen"
        elif t.kind == "battery" and t.attr == "charge":
            bucket, name = self.dems, f"{t.ref}_dem"
        elif t.kind == "line" and t.attr == "flow":
            bucket, name = self.lines, t.ref
        else:
            return False
        bucket[name] = bucket.get(name, 0.0) + coef
        return True

    def spec(self, name: str) -> ReserveContingencySpec:
        """The gtopt candidate (zero-coefficient terms dropped)."""
        gens = {k: v for k, v in self.gens.items() if v != 0.0}
        dems = {k: v for k, v in self.dems.items() if v != 0.0}
        lines = {k: v for k, v in self.lines.items() if v != 0.0}
        return ReserveContingencySpec(
            name=name,
            generators=tuple(gens),
            generator_coefs=tuple(gens.values()),
            demands=tuple(dems),
            demand_coefs=tuple(dems.values()),
            lines=tuple(lines),
            line_coefs=tuple(lines.values()),
            offset=self.offset,
        )


# ── the transform ──────────────────────────────────────────────────────────
@dataclass
class _Definition:
    """``k·DV + Σ terms (op) rhs`` defining a regressor DV."""

    coef: float
    rhs: float
    row: Row


@dataclass
class _Zone:
    """A native requirement zone under construction."""

    name: str
    req_dv: str
    direction: str  # "up" | "dn"
    calc_rows: list[Row]
    prov_row: Row
    shortage_cost: float | None = None
    surplus_cost: float | None = None
    planes: list[ReservePlaneSpec] = field(default_factory=list)
    uses_inertia: bool = False
    contingency_dvs: set[str] = field(default_factory=set)


# The builder keeps the case indexes and the per-pass outputs side by side
# so each pass (planes, fixed zones, definitions, inertia minima) reads what
# the earlier ones produced; splitting them would only add indirection.
class _Builder:  # pylint: disable=too-many-instance-attributes
    """Holds the case-wide indexes while the zones are built."""

    def __init__(
        self,
        case: PlexosCase,
        opts: NativeReserveOptions,
        dated: DatedIndex,
    ) -> None:
        self.case = case
        self.opts = opts
        self.dated = dated
        self.n_hours = max(1, case.bundle.n_days) * 24
        self.rows = _parse_rows(case.user_constraints)
        self.dvs = {d.name: d for d in case.decision_variables}
        # Inertia reference power: the commitment's minimum stable level
        # (``Commitment.pmin``, the floor when committed), else Pmin.
        self._pmin = {g.name: g.pmin for g in case.generators}
        for c in case.commitments:
            floor = max((c.pmin, *c.pmin_profile))
            if floor > 0.0:
                self._pmin[c.generator_name] = floor
        self.replaced: dict[str, str] = {}
        self.extra_ucs: list[UserConstraintSpec] = []
        self.zone_factors: dict[str, dict[str, tuple[float, float]]] = {}
        self.inertia_zones: list[InertiaZoneSpec] = []
        self.inertia_provisions: list[InertiaProvisionSpec] = []
        #: Exogenous plane inertia [MWs] per hour (must-run units).
        self.system_inertia: tuple[float, ...] = ()
        self.new_zones: list[ReserveSpec] = []
        self.warnings: list[str] = []
        #: Rows rewritten in place (``CSF_*_Def`` definitions).
        self.rewritten: dict[str, UserConstraintSpec] = {}
        #: Extractor zones a native zone supersedes → that native zone.
        self.retired: dict[str, str] = {}

    # -- discovery ---------------------------------------------------------
    def requirement_dvs(self) -> dict[str, Row]:
        """``{R: provision row}`` for ``Σ f·r − R … >= 0`` rows."""
        out: dict[str, Row] = {}
        for row in self.rows.values():
            prov = row.of_kind("reserve_provision")
            if not prov or not row.at_least:
                continue
            for t in row.of_kind("decision_variable"):
                if t.coef < 0.0 and self._has_calc_row(t.ref):
                    out.setdefault(t.ref, row)
        return out

    def _has_calc_row(self, dv: str) -> bool:
        return any(self._is_calc_row(row, dv) for row in self.rows.values())

    def _is_calc_row(self, row: Row, req_dv: str) -> bool:
        if not row.at_least or not row.terms:
            return False
        if any(t.kind != "decision_variable" for t in row.terms):
            return False
        if row.coef("decision_variable", req_dv) <= 0.0:
            return False
        regressors = {DEFAULT_GENERATION_DV, DEFAULT_INERTIA_DV}
        others = row.dvs() - {req_dv}
        return bool(others & regressors) or any(
            row.coef("decision_variable", dv) < 0.0 for dv in others
        )

    def definition(
        self, dv: str, partner_kinds: tuple[str, ...], unit_partners: bool = False
    ) -> _Definition | None:
        """Row defining ``dv`` from ``partner_kinds`` terms (``=``, or ``>=``).

        The row is used as ``k·dv + Σ terms = rhs``.  A ``>=`` row that is
        not a PLEXOS equality (Sense 0) is warned about: it only defines
        ``dv`` while the LP pushes ``dv`` against it.  ``unit_partners``
        (the load definition ``Σ gen − Σ charge − 1000·G``) also warns
        about partner coefficients other than ±1, which the load term
        ``D = Σ gen − Σ charge`` cannot represent.
        """
        for row in self.rows.values():
            k = row.coef("decision_variable", dv)
            if k == 0.0 or row.op == "<=":
                continue
            partners = [t for t in row.terms if t.kind in partner_kinds]
            if not partners:
                continue
            name = row.spec.name
            if row.op == ">=" and self.dated.sense(name) != 0.0:
                self.warnings.append(
                    f"{name}: defines {dv} with '>=' (not a PLEXOS equality); "
                    f"used as '=', exact only while {dv} is pushed against it"
                )
            odd = [t for t in partners if not math.isclose(abs(t.coef), 1.0)]
            if unit_partners and odd:
                self.warnings.append(
                    f"{name}: {len(odd)} term(s) of the {dv} definition with "
                    f"|coef| != 1 (e.g. {odd[0].coef:g}·{odd[0].ref}); the load "
                    "term D = Σ gen − Σ charge counts them at 1"
                )
            return _Definition(coef=k, rhs=row.rhs, row=row)
        return None

    # -- zones -------------------------------------------------------------
    def build(self) -> None:
        """Build every native zone the case supports."""
        req_dvs = self.requirement_dvs()
        if not req_dvs:
            return
        gen_def = self.definition(
            DEFAULT_GENERATION_DV, ("generator", "battery"), unit_partners=True
        )
        inertia_def = self.definition(DEFAULT_INERTIA_DV, ("commitment",))
        zones = [z for dv, prow in req_dvs.items() if (z := self._zone(dv, prow))]
        if not zones:
            return
        inertia_used = False
        for zone in zones:
            self._planes(zone, gen_def, inertia_def)
            inertia_used |= zone.uses_inertia
        if inertia_used and inertia_def is not None:
            if self.opts.inertia_term in _ZONE_INERTIA_TERMS:
                self._inertia(inertia_def)
            else:
                self.system_inertia = self._must_run_inertia(inertia_def)
        contingency = self._contingency_sets(
            set().union(*(z.contingency_dvs for z in zones))
        )
        for zone in zones:
            self._finish_zone(zone, contingency)
        if gen_def is not None:
            self._retire_definition(DEFAULT_GENERATION_DV, gen_def.row)

    def _zone(self, req_dv: str, prov_row: Row) -> _Zone | None:
        calc_rows = [r for r in self.rows.values() if self._is_calc_row(r, req_dv)]
        prov = prov_row.of_kind("reserve_provision")
        directions = {t.attr for t in prov}
        k_r = -prov_row.coef("decision_variable", req_dv)
        if (
            not _single_direction(prov)
            or prov_row.rhs != 0.0
            or prov_row.spec.rhs_profile
            or k_r <= 0.0
        ):
            self.warnings.append(
                f"{prov_row.spec.name}: provision row not of the form "
                f"'Σ f·r + VoRS − {req_dv} >= 0'; left as a user constraint"
            )
            return None
        shortage: float | None = None
        surplus: float | None = None
        for t in prov_row.of_kind("decision_variable"):
            if t.ref == req_dv:
                continue
            cost = self.dvs[t.ref].cost if t.ref in self.dvs else 0.0
            if t.coef > 0.0 and cost > 0.0:
                c = cost * k_r / t.coef
                shortage = c if shortage is None else min(shortage, c)
            elif t.coef < 0.0 < cost:
                # ``− Slack``: the priced excess of an equality balance.
                c = cost * k_r / -t.coef
                surplus = c if surplus is None else min(surplus, c)
            else:
                self.warnings.append(
                    f"{prov_row.spec.name}: dropped {t.coef:+g}·{t.ref} "
                    "(it only raises the requirement at a cost)"
                )
        name = req_dv.removesuffix("_Requirement") or req_dv
        taken = {r.name for r in self.case.reserves}
        if name in taken:
            name = f"{name}_native"
        return _Zone(
            name=name,
            req_dv=req_dv,
            direction=directions.pop(),
            calc_rows=calc_rows,
            prov_row=prov_row,
            shortage_cost=shortage,
            surplus_cost=surplus,
        )

    def _member(self, row: Row, dv: str) -> tuple[float, ...]:
        """Hourly coefficient of ``dv`` in ``row`` (dated sets resolved)."""
        parsed = row.coef("decision_variable", dv)
        rows = self.dated.rows(row.spec.name, dv, "Value Coefficient")
        values = hourly_values(rows, self.dated.start, self.n_hours, parsed)
        if not math.isclose(values[0], parsed, rel_tol=1e-9, abs_tol=1e-12):
            self.warnings.append(
                f"{row.spec.name}: {dv} coefficient {values[0]:g} at the "
                f"horizon start (dated sets) differs from the extracted "
                f"{parsed:g}; using the dated value"
            )
        return values

    def _rhs(self, row: Row) -> tuple[float, ...]:
        """Hourly RHS: the extractor's profile (data files, partial-horizon
        overlays) wins; else the dated XML rows; else the scalar."""
        name = row.spec.name
        if row.spec.rhs_profile:
            values = _blocks_to_hours(
                row.spec.rhs_profile,
                self.case.bundle.block_layout,
                self.n_hours,
                where=name,
            )
        else:
            rows = self.dated.rhs_rows(name)
            values = hourly_values(rows, self.dated.start, self.n_hours, row.rhs)
            if values and not math.isclose(
                values[0], row.rhs, rel_tol=1e-9, abs_tol=1e-9
            ):
                self.warnings.append(
                    f"{name}: RHS {values[0]:g} at the horizon start (dated "
                    f"rows) differs from the extracted {row.rhs:g}; using the "
                    "dated value"
                )
        bad = sum(abs(v) >= _NO_VALUE for v in values)
        if bad:
            self.warnings.append(
                f"{name}: 'no limit' RHS sentinel (|v| >= 1e20) in {bad} "
                f"hour(s) replaced by {row.rhs:g}"
            )
            values = tuple(row.rhs if abs(v) >= _NO_VALUE else v for v in values)
        return values

    # One pass per row builds four parallel hourly vectors (alpha, demand,
    # inertia, contingency) plus the floor shift; they share the loop.
    def _planes(  # pylint: disable=too-many-locals
        self,
        zone: _Zone,
        gen_def: _Definition | None,
        inertia_def: _Definition | None,
    ) -> None:
        gen_dv, inertia_dv = DEFAULT_GENERATION_DV, DEFAULT_INERTIA_DV
        k_g = gen_def.coef if gen_def else _DEFAULT_DEF_COEF
        rhs_g = gen_def.rhs if gen_def else 0.0
        if gen_def is None and any(gen_dv in r.dvs() for r in zone.calc_rows):
            self.warnings.append(
                f"{gen_dv}: no defining row found; assuming {_DEFAULT_DEF_COEF:g}"
            )
        n = self.n_hours
        for row in zone.calc_rows:
            k_r = self._member(row, zone.req_dv)
            alpha = [rh / k for rh, k in zip(self._rhs(row), k_r, strict=True)]
            demand = [0.0] * n
            inertia = [0.0] * n
            cont = [0.0] * n
            floor_shift = [0.0] * n
            cont_dvs: list[str] = []
            for dv in sorted(row.dvs() - {zone.req_dv}):
                coef = [c / k for c, k in zip(self._member(row, dv), k_r, strict=True)]
                if dv == gen_dv:
                    for h in range(n):
                        demand[h] = coef[h] / k_g
                        alpha[h] -= coef[h] / k_g * rhs_g
                elif dv == inertia_dv:
                    if inertia_def is None:
                        self.warnings.append(
                            f"{row.spec.name}: {inertia_dv} has no defining "
                            "row; inertia term dropped"
                        )
                        continue
                    zone.uses_inertia = True
                    for h in range(n):
                        inertia[h] = coef[h] / inertia_def.coef
                        alpha[h] -= coef[h] / inertia_def.coef * inertia_def.rhs
                else:
                    cont_dvs.append(dv)
                    lower = (
                        _finite(self.dvs[dv].lower_bound) if dv in self.dvs else None
                    )
                    k_c = self._contingency_coef(dv)
                    for h in range(n):
                        cont[h] -= coef[h] / k_c
                        if lower is not None:
                            floor_shift[h] -= coef[h] * lower
            plane = ReservePlaneSpec(
                alpha=_compress(tuple(alpha)),
                demand_coef=_compress(tuple(demand)),
                inertia_coef=_compress(tuple(inertia)),
                contingency_coef=_compress(tuple(cont)),
                source=row.spec.name,
            )
            if plane.alpha is None:
                plane = replace(plane, alpha=0.0)
            zone.planes.append(plane)
            if cont_dvs:
                zone.contingency_dvs.update(cont_dvs)
                if any(floor_shift):
                    floor = [a + f for a, f in zip(alpha, floor_shift, strict=True)]
                    zone.planes.append(
                        replace(
                            plane,
                            alpha=_compress(tuple(floor)) or 0.0,
                            contingency_coef=None,
                            source=f"{row.spec.name}{_FLOOR_TAG}",
                        )
                    )

    def _contingency_coef(self, dv: str) -> float:
        """``k_C`` of ``k_C·C − p >= 0`` (1 for MW-valued DVs)."""
        for row in self.rows.values():
            k = row.coef("decision_variable", dv)
            if k > 0.0 and row.at_least and self._unit_terms(row):
                return k
        return 1.0

    @staticmethod
    def _unit_terms(row: Row) -> list[Term]:
        return [
            t
            for t in row.terms
            if t.coef < 0.0
            and (
                (t.kind == "generator" and t.attr == "generation")
                or (t.kind == "battery" and t.attr in ("discharge", "charge"))
            )
        ]

    def _contingency_sets(
        self, dvs: set[str]
    ) -> dict[str, list[ReserveContingencySpec]]:
        """``{C: candidates}``, one per ``k·C + Σ c·x >= rhs`` comparison row.

        ``C >= Σ (−c/k)·x + rhs/k``, scaled by ``k_C`` (:meth:`_contingency_coef`)
        to the MW-valued ``k_C·C`` the planes multiply by ``c/k_C``: a DV in
        GW (``k_C = 1000``) is not scaled twice.  Generators, BESS discharge
        (``<bat>_gen``) / charge (``<bat>_dem``), line flows and the
        constant become one ``ReserveContingency``; a shared group DV
        (``limited_generation_group``) is expanded through its own defining
        row.
        """
        out: dict[str, list[ReserveContingencySpec]] = {}
        for dv in sorted(dvs):
            cands: list[ReserveContingencySpec] = []
            k_c = self._contingency_coef(dv)
            for row in self.rows.values():
                k = row.coef("decision_variable", dv)
                if not row.at_least or k <= 0.0 or not self._unit_terms(row):
                    continue
                scale = k_c / k
                acc = _CandidateTerms(offset=row.rhs * scale)
                for t in row.terms:
                    if t.kind == "decision_variable" and t.ref == dv:
                        continue
                    self._add_candidate_term(acc, t, -t.coef * scale, row.spec.name)
                cands.append(acc.spec(row.spec.name))
            out[dv] = cands
        return out

    def _add_candidate_term(
        self, acc: _CandidateTerms, t: Term, coef: float, where: str
    ) -> None:
        if acc.add(t, coef):
            return
        group = self._group_definition(t.ref, where) if coef > 0.0 else None
        if t.kind != "decision_variable" or group is None:
            self.warnings.append(
                f"{where}: term {t.coef:+g}·{t.kind}({t.ref}).{t.attr} not "
                "representable in a contingency candidate; dropped"
            )
            return
        k_x = group.coef("decision_variable", t.ref)
        acc.offset += coef * group.rhs / k_x
        for g in group.terms:
            if g.kind == "decision_variable" and g.ref == t.ref:
                continue
            if not acc.add(g, -coef * g.coef / k_x):
                self.warnings.append(
                    f"{where}: term {g.coef:+g}·{g.kind}({g.ref}).{g.attr} of "
                    f"{t.ref}'s definition not representable; dropped"
                )

    def _group_definition(self, dv: str, where: str) -> Row | None:
        """``k·X + Σ c·y >= rhs`` (``k > 0``) defining a group DV ``X``."""
        for row in self.rows.values():
            if row.spec.name == where or not row.at_least:
                continue
            if row.coef("decision_variable", dv) > 0.0 and any(
                t.kind != "decision_variable" for t in row.terms
            ):
                return row
        return None

    def _inertia_units(self, inertia_def: _Definition) -> list[tuple[str, float]]:
        """``[(generator, H·S)]`` of the ``commitment(uc_<g>).status`` terms."""
        gen_of = {
            f"uc_{c.generator_name}": c.generator_name for c in self.case.commitments
        }
        scale = -1.0 if inertia_def.coef > 0.0 else 1.0
        out: list[tuple[str, float]] = []
        for t in inertia_def.row.terms:
            if t.kind == "decision_variable":
                continue
            gen = gen_of.get(t.ref) if t.kind == "commitment" else None
            hs = scale * t.coef
            if gen is None or t.attr != "status" or hs <= 0.0:
                self.warnings.append(
                    f"{inertia_def.row.spec.name}: term {t.coef:+g}·{t.kind}"
                    f"({t.ref}).{t.attr} not mapped to a unit inertia"
                )
                continue
            out.append((gen, hs))
        return out

    def _must_run_inertia(self, inertia_def: _Definition) -> tuple[float, ...]:
        """Exogenous ``I`` [MWs] per hour: Σ H·S of the units forced on.

        A unit counts in an hour only when PLEXOS pins its commitment on
        (``Gen_Commit = 1``): the minimum committed inertia, so the
        requirement (``inertia_coef ≤ 0``) is never underestimated by a
        commitment the LP might not make.  The inertia of the units
        without a commitment is already in the planes' constant
        (:meth:`_planes`).
        """
        status = {
            c.generator_name: c.commit_status_profile for c in self.case.commitments
        }
        out = [0.0] * self.n_hours
        for gen, hs in self._inertia_units(inertia_def):
            prof = status.get(gen, ())
            for h in range(min(self.n_hours, len(prof))):
                if prof[h] == 1:
                    out[h] += hs
        if not any(out):
            self.warnings.append(
                f"{inertia_def.row.spec.name}: no unit is forced on; the "
                "exogenous system_inertia is 0 (largest requirement)"
            )
        return tuple(out)

    def _inertia(self, inertia_def: _Definition) -> None:
        """``I`` from the ``Inertia_SEN`` zone: one InertiaProvision per unit
        (``commitment``: ``I_c = pf·provision_max = H·S`` times the unit
        status; ``endogenous``: the dispatch-based provision)."""
        zone = DEFAULT_INERTIA_DV
        for gen, hs in self._inertia_units(inertia_def):
            p_ref = _inertia_ref_power(self._pmin.get(gen, 0.0))
            self.inertia_provisions.append(
                InertiaProvisionSpec(
                    name=f"inertia_{gen}",
                    generator=gen,
                    inertia_zones=(zone,),
                    provision_factor=hs / p_ref,
                    provision_max=p_ref,
                )
            )
        self.inertia_zones.append(InertiaZoneSpec(name=zone))

    def _finish_zone(
        self,
        zone: _Zone,
        contingency: Mapping[str, list[ReserveContingencySpec]],
    ) -> None:
        up = zone.direction == "up"
        cands = [c for dv in sorted(zone.contingency_dvs) for c in contingency[dv]]
        planes = zone.planes
        if not cands and any(p.contingency_coef is not None for p in planes):
            # No unit can raise C above its bound: the bound plane is exact;
            # without a bound the contingency term is dropped (C = 0).
            floors = {p.source for p in planes}
            bounded = any(p.source.endswith(_FLOOR_TAG) for p in planes)
            planes = [
                replace(p, contingency_coef=None)
                for p in planes
                if p.contingency_coef is None or f"{p.source}{_FLOOR_TAG}" not in floors
            ]
            self.warnings.append(
                f"{zone.name}: no contingency comparison rows; "
                + (
                    "contingency fixed at its lower bound"
                    if bounded
                    else "contingency DV has no lower bound either: its term "
                    "is dropped (C = 0), the requirement may be underestimated"
                )
            )
        planes = [self._nonnegative_contingency(p) for p in planes]
        load = self.opts
        sources = ", ".join(r.spec.name for r in zone.calc_rows)
        spec = ReserveSpec(
            object_id=0,
            name=zone.name,
            urcost=(zone.shortage_cost or 0.0) if up else 0.0,
            drcost=0.0 if up else (zone.shortage_cost or 0.0),
            urreq_planes=tuple(planes) if up else (),
            drreq_planes=() if up else tuple(planes),
            load_term="endogenous_generation" if load.endogenous else "exogenous",
            system_load=() if load.endogenous else load.system_load,
            demand_load_factor=1.0 if load.endogenous else load.load_factor,
            **self._inertia_fields(zone.uses_inertia),
            urcontingencies=tuple(cands) if up else (),
            drcontingencies=() if up else tuple(cands),
            **self._sense_fields(zone.prov_row, zone.surplus_cost, up, planes=True),
            description=(
                f"{_NOTE}: CEN PLEXOS requirement {zone.req_dv} = max of "
                f"{len(zone.calc_rows)} plane(s) ({sources}); provisions from "
                f"{zone.prov_row.spec.name}"
            ),
            omit_zero_requirement=True,
        )
        self.new_zones.append(spec)
        self._add_factors(zone.prov_row, zone.name, zone.direction, zone.req_dv)
        for row in zone.calc_rows:
            self.replaced[row.spec.name] = f"ReserveZone '{zone.name}' plane"
        self.replaced[zone.prov_row.spec.name] = f"ReserveZone '{zone.name}'"
        req_spec = self.dvs.get(zone.req_dv)
        if req_spec is not None and req_spec.cost != 0.0:
            attr = "up" if up else "dn"
            self.extra_ucs.append(
                UserConstraintSpec(
                    name=f"{zone.name}_RequirementCost",
                    expression=(
                        f'1 * decision_variable("{zone.req_dv}").value - 1 * '
                        f'reserve_zone("{zone.name}").{attr} = 0'
                    ),
                    description=(
                        f"{_NOTE}: carries the PLEXOS cost of {zone.req_dv} "
                        f"({req_spec.cost:g} $/MW) onto the native requirement"
                    ),
                )
            )

    def _nonnegative_contingency(self, plane: ReservePlaneSpec) -> ReservePlaneSpec:
        """Drop a ``contingency_coef`` with a negative value.

        gtopt rejects it: a negative coefficient turns the largest unit
        into a requirement credit.  PLEXOS' CPF rows always carry it ≥ 0;
        another sign is a data error, said once per plane.
        """
        coef = plane.contingency_coef
        values = coef if isinstance(coef, tuple) else (coef or 0.0,)
        if min(values) >= 0.0:
            return plane
        self.warnings.append(
            f"{plane.source}: negative contingency coefficient "
            f"({min(values):g}); contingency term dropped"
        )
        return replace(plane, contingency_coef=None)

    def _inertia_fields(self, uses_inertia: bool) -> dict[str, Any]:
        """``inertia_term`` with its source: ``inertia_zone`` (commitment,
        endogenous) or ``system_inertia`` (exogenous); nothing when no
        plane uses ``I``.
        """
        if not uses_inertia:
            return {}
        term = self.opts.inertia_term
        if term in _ZONE_INERTIA_TERMS:
            return {"inertia_term": term, "inertia_zone": DEFAULT_INERTIA_DV}
        return {"inertia_term": "exogenous", "system_inertia": self.system_inertia}

    def _sense_fields(
        self, row: Row, surplus: float | None, up: bool, planes: bool = False
    ) -> dict[str, Any]:
        """``requirement_sense`` / surplus cost of a balance row.

        PLEXOS Sense 0 (or unset) is an equality: ``Σ f·r + VoRS − R −
        Slack = 0`` (:meth:`_is_equality`).  A plane zone (``planes``)
        gets no surplus cost: its requirement ``R`` is a column above the
        planes that absorbs any excess, so gtopt builds no surplus column.
        """
        if planes and surplus is not None:
            self.warnings.append(
                f"{row.spec.name}: surplus (−Slack) cost dropped (a plane "
                "requirement R absorbs the excess provision)"
            )
            surplus = None
        if not self._is_equality(row):
            return {}
        out: dict[str, Any] = {"requirement_sense": "eq"}
        if surplus is not None:
            out["ursurplus_cost" if up else "drsurplus_cost"] = surplus
        return out

    def _is_equality(self, row: Row) -> bool:
        """PLEXOS Sense 0 (DB), else the extracted operator ``=``.

        A row the DB does not hold (or no DB) falls back to the extracted
        operator, said once per row when a DB is present.
        """
        sense = self.dated.sense(row.spec.name)
        if sense is not None:
            return sense == 0.0
        if self.dated.constraints:
            self.warnings.append(
                f"{row.spec.name}: no Sense / RHS rows in the PLEXOS DB; "
                f"extracted operator '{row.op}' used"
            )
        return row.op == "="

    def _add_factors(self, row: Row, zone: str, direction: str, req_dv: str) -> None:
        k_r = -row.coef("decision_variable", req_dv) if req_dv else 1.0
        for t in row.of_kind("reserve_provision"):
            per_zone = self.zone_factors.setdefault(t.ref, {})
            up, dn = per_zone.get(zone, (0.0, 0.0))
            if direction == "up":
                up += t.coef / k_r
            else:
                dn += t.coef / k_r
            per_zone[zone] = (up, dn)

    def _retire_definition(self, dv: str, row: Row) -> None:
        """Make ``dv``'s defining row inactive once nothing else uses ``dv``."""
        for other in self.rows.values():
            name = other.spec.name
            if name == row.spec.name or name in self.replaced:
                continue
            if dv in other.dvs():
                logger.info(
                    "%s: %s kept active (%s still uses %s)",
                    _NOTE,
                    row.spec.name,
                    name,
                    dv,
                )
                return
        self.replaced[row.spec.name] = "native load term (ReserveZone.load_term)"

    # -- fixed-requirement zones ------------------------------------------
    def fixed_zones(self) -> None:
        """``Σ c·r [+ VoRS] >= RHS`` rows → fixed-requirement zones."""
        taken = {r.name for r in self.case.reserves} | {z.name for z in self.new_zones}
        for row in self.rows.values():
            name = row.spec.name
            if name in self.replaced or not row.at_least or name in taken:
                continue
            prov = row.of_kind("reserve_provision")
            others = [t for t in row.terms if t.kind != "reserve_provision"]
            directions = {t.attr for t in prov}
            if not _single_direction(prov):
                continue
            cost = row.spec.penalty if row.spec.penalty > 0.0 else None
            pure = True
            for t in others:
                dv = self.dvs.get(t.ref)
                if (
                    t.kind == "decision_variable"
                    and t.coef > 0.0
                    and dv
                    and dv.cost > 0.0
                ):
                    cost = (
                        dv.cost / t.coef
                        if cost is None
                        else min(cost, dv.cost / t.coef)
                    )
                else:
                    pure = False
            req = self._rhs(row)
            if not pure or min(req) < 0.0 or max(req) <= 0.0:
                continue
            up = directions == {"up"}
            self.new_zones.append(
                ReserveSpec(
                    object_id=0,
                    name=name,
                    ur_requirement=req if up else (),
                    dr_requirement=() if up else req,
                    urcost=(cost or 0.0) if up else 0.0,
                    drcost=0.0 if up else (cost or 0.0),
                    # An equality row's penalty prices both sides.
                    **self._sense_fields(
                        row,
                        row.spec.penalty or None if self._is_equality(row) else None,
                        up,
                    ),
                    description=(
                        f"{_NOTE}: PLEXOS provision-sum constraint {name} "
                        "(fixed requirement = its RHS)"
                    ),
                    omit_zero_requirement=True,
                )
            )
            self._add_factors(row, name, "up" if up else "dn", "")
            self.replaced[name] = f"ReserveZone '{name}'"
            self._retire_base_zones(name)

    def _retire_base_zones(self, row_name: str) -> None:
        """Supersede the extractor zones whose Provision ``row_name`` sums.

        PLEXOS' Reserve objects carry no requirement of their own here
        (their Price is the negated dual of the ``*MinProvision`` row and
        their Min Provision does not bind — CTF_RS provides 77 MW with Min
        Provision 305); the requirement lives in the row.  Keeping both
        the extractor's Reserve zone and the native row zone duplicates
        the requirement and splits its dual between the two (794 / −752).
        """
        for rsv in self.case.reserves:
            if any(
                r.value != 0.0
                for r in self.dated.rows(row_name, rsv.name, "Provision Coefficient")
            ):
                self.retired.setdefault(rsv.name, row_name)

    # -- definitions (CSF_*_Def) -----------------------------------------------
    def definitions(self) -> None:
        """``Σ c·r − X (op) 0`` rows defining a DV ``X`` stay user constraints.

        They are definitions, not requirements (``CSF_RS_Requirement :=
        Σ provision``, read by the ``MAXCSF_*`` caps), so their PLEXOS RHS
        (0) and Sense (unset = equality) are restored when the extractor
        attached a reserve-requirement RHS profile or wrote ``>=``.  Needs
        the DB (RHS and Sense are read from it).
        """
        for row in list(self.rows.values()):
            name = row.spec.name
            dvs = row.of_kind("decision_variable")
            prov = row.of_kind("reserve_provision")
            if name in self.replaced or not prov or len(dvs) != 1:
                continue
            if dvs[0].coef >= 0.0 or len(prov) + 1 != len(row.terms):
                continue
            if not _single_direction(prov):
                continue
            sense = self.dated.sense(name)
            if sense is None:
                continue
            db_rhs = hourly_values(
                self.dated.rhs_rows(name), self.dated.start, self.n_hours, row.rhs
            )
            if any(v != 0.0 for v in db_rhs):
                continue
            op = {-1.0: "<=", 1.0: ">="}.get(sense, "=")
            if op == row.op and row.rhs == 0.0 and not row.spec.rhs_profile:
                continue
            tail = _TAIL_RE.search(row.spec.expression)
            if tail is None:
                raise ValueError(f"{name}: expression has no '(op) rhs' tail")
            self.rewritten[name] = replace(
                row.spec,
                expression=f"{row.spec.expression[: tail.start()].rstrip()} {op} 0",
                rhs_profile=(),
                description=(
                    f"{row.spec.description} [{_NOTE}: definition of "
                    f"{dvs[0].ref}; PLEXOS RHS 0, Sense {op}]"
                ).strip(),
            )

    # -- inertia minima (InertiaNRMin_*) -----------------------------------
    def _inertia_definitions(self) -> list[dict[str, float]]:
        """Commitment coefficients of every ``Σ H·u − k·I (op) rhs`` row.

        ``I`` must be unbounded above (a selector ``y ∈ [0, 1]`` of a
        commit row ``Σ u − k·y >= 0`` is not an inertia aggregate).
        """
        out: list[dict[str, float]] = []
        for row in self.rows.values():
            dvs = row.of_kind("decision_variable")
            units = row.of_kind("commitment")
            if len(dvs) != 1 or dvs[0].coef >= 0.0 or not units:
                continue
            spec = self.dvs.get(dvs[0].ref)
            if spec is not None and _finite(spec.upper_bound) is not None:
                continue
            if len(units) + 1 == len(row.terms) and all(
                t.attr == "status" and t.coef > 0.0 for t in units
            ):
                out.append({t.ref: t.coef for t in units})
        return out

    def inertia_minima(self) -> None:
        """``Σ H·u [+ c·y] >= RHS`` rows → ``InertiaZone`` (+ credits).

        A row qualifies when every commitment coefficient equals the one
        of an inertia definition row (``Inertia_Calculation_e*``), so only
        inertia minima are taken.  Each DV ``y`` of the row must have a
        commit row ``Σ u_g − k·y >= 0`` (``InertiaCommitGuacolda2``) and
        bounds ``[0, U]``: it becomes a credit ``{units k·U, credit c·U}``
        with a continuous selector (PLEXOS).
        """
        defs = self._inertia_definitions()
        gen_of = {
            f"uc_{c.generator_name}": c.generator_name for c in self.case.commitments
        }
        for row in list(self.rows.values()):
            name = row.spec.name
            units = row.of_kind("commitment")
            ys = row.of_kind("decision_variable")
            if name in self.replaced or not row.at_least or not units:
                continue
            if len(units) + len(ys) != len(row.terms) or any(t.coef <= 0.0 for t in ys):
                continue
            if not any(_same_units(units, d) for d in defs):
                continue
            found = [self._credit(name, y, gen_of) for y in ys]
            if any(c is None for c in found):
                continue
            requirement = self._rhs(row)
            if max(requirement) <= 0.0:
                continue
            zone = name
            for t in units:
                gen = gen_of.get(t.ref)
                if gen is None or t.attr != "status":
                    self.warnings.append(
                        f"{name}: term {t.coef:+g}·{t.kind}({t.ref}).{t.attr} "
                        "not mapped to an InertiaProvision"
                    )
                    continue
                self._add_inertia_provision(gen, zone, t.coef)
            self.inertia_zones.append(
                InertiaZoneSpec(
                    name=zone,
                    requirement=_compress(requirement) or 0.0,
                    cost=row.spec.penalty if row.spec.penalty > 0.0 else None,
                    requirement_credits=tuple(c for c in found if c is not None),
                )
            )
            self.replaced[name] = f"InertiaZone '{zone}'"

    def _credit(
        self, where: str, y: Term, gen_of: Mapping[str, str]
    ) -> InertiaCreditSpec | None:
        dv = self.dvs.get(y.ref)
        upper = _finite(dv.upper_bound) if dv is not None else None
        lower = dv.lower_bound if dv is not None else None
        # A selector y ∈ [0, U], 0 < U < "no limit" (unset lower = 0).
        selector = upper is not None and upper > 0.0 and (lower or 0.0) == 0.0
        for row in self.rows.values():
            k = -row.coef("decision_variable", y.ref)
            units = row.of_kind("commitment")
            if k <= 0.0 or not row.at_least or row.rhs != 0.0 or not units:
                continue
            if len(units) + 1 != len(row.terms):
                continue
            c_u = units[0].coef
            gens = [gen_of.get(t.ref) for t in units]
            if (
                upper is None
                or not selector
                or c_u <= 0.0
                or any(not math.isclose(t.coef, c_u) for t in units)
                or any(g is None for g in gens)
            ):
                break
            self.replaced[row.spec.name] = f"InertiaZone '{where}' credit"
            return InertiaCreditSpec(
                name=y.ref,
                generators=tuple(g for g in gens if g is not None),
                units=k / c_u * upper,
                credit=y.coef * upper,
            )
        self.warnings.append(
            f"{where}: {y.ref} has no 'Σ u − k·y >= 0' commit row with "
            "bounds [0, U]; inertia minimum left as a user constraint"
        )
        return None

    def _add_inertia_provision(self, gen: str, zone: str, hs: float) -> None:
        """Count ``H·S`` of ``gen`` in ``zone`` (sharing a same-factor one)."""
        p_ref = _inertia_ref_power(self._pmin.get(gen, 0.0))
        pf = hs / p_ref
        for i, p in enumerate(self.inertia_provisions):
            if p.generator == gen and math.isclose(p.provision_factor, pf):
                if zone not in p.inertia_zones:
                    self.inertia_provisions[i] = replace(
                        p, inertia_zones=(*p.inertia_zones, zone)
                    )
                return
        taken = {p.name for p in self.inertia_provisions}
        name = f"inertia_{gen}"
        if name in taken:
            name = f"inertia_{gen}__{zone}"
        self.inertia_provisions.append(
            InertiaProvisionSpec(
                name=name,
                generator=gen,
                inertia_zones=(zone,),
                provision_factor=pf,
                provision_max=p_ref,
            )
        )

    # -- northern-corridor specials (opt-in) ---------------------------------
    def north_special(self) -> None:
        """``SpecialCPF/CSF/CTFNorth(LW)`` → zones with a dated ``alpha``.

        Opt-in (``--reserve-north-special``): every row is ``Include in ST
        Schedule = 0`` in CEN PCP.  The requirement is the scheduled one —
        the row's dated RHS, minus the requirement DVs at their own dated
        definition value (``X >= rhs/k`` with the corridor flow
        ``CPF_North_Tx`` dropped).  The flow-coupled form (``flow_coef``)
        is never emitted.
        """
        every = {
            uc.name: Row(uc, *parsed)
            for uc in self.case.user_constraints
            if (parsed := parse_expression(uc.expression)) is not None
        }
        for name, row in every.items():
            if not _NORTH_RE.match(name):
                continue
            prov = row.of_kind("reserve_provision")
            if not _single_direction(prov):
                continue
            alpha = list(
                hourly_values(
                    self.dated.rhs_rows(name), self.dated.start, self.n_hours, row.rhs
                )
            )
            for t in row.of_kind("decision_variable"):
                sched = self._dv_schedule(t.ref, every)
                for h in range(self.n_hours):
                    alpha[h] -= t.coef * sched[h]
            up = prov[0].attr == "up"
            plane = ReservePlaneSpec(alpha=_compress(tuple(alpha)) or 0.0, source=name)
            zone = name
            self.new_zones.append(
                ReserveSpec(
                    object_id=0,
                    name=zone,
                    urcost=row.spec.penalty if up else 0.0,
                    drcost=0.0 if up else row.spec.penalty,
                    urreq_planes=(plane,) if up else (),
                    drreq_planes=() if up else (plane,),
                    **self._sense_fields(row, None, up, planes=True),
                    description=(
                        f"{_NOTE}: PLEXOS {name} (Include in ST Schedule = 0; "
                        "opt-in --reserve-north-special): scheduled requirement "
                        "from the dated RHS, corridor flow not coupled"
                    ),
                    omit_zero_requirement=True,
                )
            )
            self._add_factors(row, zone, "up" if up else "dn", "")

    def _dv_schedule(self, dv: str, every: Mapping[str, Row]) -> tuple[float, ...]:
        """Dated value of a DV from its ``k·X − … >= rhs`` definition row."""
        for row in every.values():
            k = row.coef("decision_variable", dv)
            if k <= 0.0 or any(t.kind != "decision_variable" for t in row.terms):
                continue
            rhs = hourly_values(
                self.dated.rhs_rows(row.spec.name),
                self.dated.start,
                self.n_hours,
                row.rhs,
            )
            return tuple(v / k for v in rhs)
        return (0.0,) * self.n_hours

    # -- assembly ------------------------------------------------------------
    def provisions(self) -> tuple[ReserveProvisionSpec, ...]:
        """Existing provisions with the new zones / factors appended."""
        out: list[ReserveProvisionSpec] = []
        seen: set[str] = set()
        for rp in self.case.reserve_provisions:
            name = rp.name or f"provision_{rp.generator_name}"
            add = self.zone_factors.get(name)
            if not add:
                out.append(rp)
                continue
            seen.add(name)
            zones = list(rp.reserve_zones)
            ur = _pad(rp.ur_zone_factors, len(zones))
            dr = _pad(rp.dr_zone_factors, len(zones))
            for zone, (fu, fd) in add.items():
                zones.append(zone)
                ur.append(fu)
                dr.append(fd)
            out.append(
                replace(
                    rp,
                    reserve_zones=tuple(zones),
                    ur_zone_factors=tuple(ur),
                    dr_zone_factors=tuple(dr),
                )
            )
        out = [self._without_retired(rp) for rp in out]
        if any(
            v != 1.0
            for rp in self.case.reserve_provisions
            if (rp.name or f"provision_{rp.generator_name}") in seen
            for v in rp.ur_provision_factor_profile + rp.dr_provision_factor_profile
        ):
            self.warnings.append(
                "provision factor profiles != 1 multiply the zone factors "
                "(use --bess-reserve native for gross BESS provisions)"
            )
        missing = sorted(set(self.zone_factors) - seen)
        if missing:
            self.warnings.append(
                f"{len(missing)} provision reference(s) without a "
                f"ReserveProvision (ignored): {', '.join(missing[:5])}"
            )
        return tuple(out)

    def _without_retired(self, rp: ReserveProvisionSpec) -> ReserveProvisionSpec:
        """``rp`` without its retired zones (kept when nothing else is left)."""
        keep = [i for i, z in enumerate(rp.reserve_zones) if z not in self.retired]
        if len(keep) == len(rp.reserve_zones) or not keep:
            return rp
        n = len(rp.reserve_zones)
        ur = _pad(rp.ur_zone_factors, n)
        dr = _pad(rp.dr_zone_factors, n)
        return replace(
            rp,
            reserve_zones=tuple(rp.reserve_zones[i] for i in keep),
            ur_zone_factors=tuple(ur[i] for i in keep) if rp.ur_zone_factors else (),
            dr_zone_factors=tuple(dr[i] for i in keep) if rp.dr_zone_factors else (),
        )

    def reserves(
        self, provisions: tuple[ReserveProvisionSpec, ...]
    ) -> tuple[ReserveSpec, ...]:
        """Extractor zones minus the retired ones, plus the native zones.

        A retired zone some provision still lists (its only zone) stays,
        stripped of its requirement, floor and costs.
        """
        used = {z for rp in provisions for z in rp.reserve_zones}
        out: list[ReserveSpec] = []
        for rsv in self.case.reserves:
            if rsv.name not in self.retired:
                out.append(rsv)
            elif rsv.name in used:
                out.append(
                    replace(
                        rsv,
                        ur_requirement=(),
                        dr_requirement=(),
                        ur_min_provision=0.0,
                        dr_min_provision=0.0,
                        urcost=0.0,
                        drcost=0.0,
                        omit_zero_requirement=True,
                        description=(
                            f"{_NOTE}: requirement moved to ReserveZone "
                            f"'{self.retired[rsv.name]}'"
                        ),
                    )
                )
        return tuple(out) + tuple(self.new_zones)

    def user_constraints(self) -> tuple[UserConstraintSpec, ...]:
        """User constraints with the replaced rows made inactive."""
        out: list[UserConstraintSpec] = []
        for uc in self.case.user_constraints:
            why = self.replaced.get(uc.name)
            if why is None:
                out.append(self.rewritten.get(uc.name, uc))
                continue
            note = f"[{_NOTE}: replaced by {why}]"
            out.append(
                replace(
                    uc,
                    active=False,
                    description=f"{uc.description} {note}".strip(),
                )
            )
        out.extend(self.extra_ucs)
        return tuple(out)


def _same_units(units: Sequence[Term], definition: Mapping[str, float]) -> bool:
    """``units`` ⊆ an inertia definition, ≥ 90 % with the same ``H·S``.

    CEN data carries the odd per-row discrepancy (``CERRO_PABELLON_U2``
    33.4 vs 44.7 MWs between ``InertiaNRMin_*`` and
    ``Inertia_Calculation_e2``), so an exact match is too strict; each
    row's own coefficients are used for its provisions.
    """
    if any(t.ref not in definition for t in units):
        return False
    same = sum(math.isclose(definition[t.ref], t.coef, rel_tol=1e-9) for t in units)
    return same >= 0.9 * len(units)


def _has_provision_rows(case: PlexosCase) -> bool:
    return any("reserve_provision(" in uc.expression for uc in case.user_constraints)


def _single_direction(prov: Sequence[Term]) -> bool:
    """Positive ``reserve_provision`` terms all on ``.up`` or all on ``.dn``."""
    directions = {t.attr for t in prov}
    return (
        len(directions) == 1
        and directions <= {"up", "dn"}
        and all(t.coef > 0.0 for t in prov)
    )


def _inertia_ref_power(pmin: float) -> float:
    """Reference power ``P_ref`` [MW] of an inertia provision.

    ``r <= min(p, P_ref)`` with ``P_ref = Pmin`` (gtopt's own default,
    ``FE = H·S/Pmin``) makes ``r = Pmin`` as soon as the unit is committed
    (``p >= Pmin``), so ``pf·r = H·S`` exactly — the commitment status of
    PLEXOS' inertia row — while a relaxed unit running below ``Pmin``
    counts ``H·S·p/Pmin`` only.  (A smaller ``P_ref`` counts the full
    ``H·S`` from the first MW: in an LP relaxation, where units run below
    ``Pmin``, that inflated the CEN 2026-11-07 day-1 inertia 2.6x.)
    Units without ``Pmin`` use 1 MW.
    """
    return pmin if pmin > 0.0 else _INERTIA_REF_MW


def _pad(values: tuple[float, ...], n: int) -> list[float]:
    out = list(values[:n])
    out.extend([1.0] * (n - len(out)))
    return out


def apply_native_reserve(
    case: PlexosCase,
    opts: NativeReserveOptions,
    db: PlexosDb | None = None,
    db_loader: Callable[[], PlexosDb | None] | None = None,
) -> PlexosCase:
    """Rewrite CEN's PLEXOS reserve rows into gtopt's native reserve model.

    No-op for ``--reserve-model uc`` or when the case has no CPF-style
    requirement rows.  ``db`` (the parsed PLEXOS XML) resolves dated
    coefficient sets into per-hour schedules; without it the coefficients
    the extractor picked at the horizon start are used; ``db_loader`` parses
    it lazily, only when the case has reserve-provision rows.
    """
    if opts.model != "native":
        return case
    n_hours = max(1, case.bundle.n_days) * 24
    if opts.system_load and not opts.endogenous and len(opts.system_load) != n_hours:
        raise ValueError(
            f"--reserve-system-load: {len(opts.system_load)} values, the horizon "
            f"has {n_hours} hours (one value per hour, no padding)"
        )
    if db is None and db_loader is not None and _has_provision_rows(case):
        db = db_loader()
    builder = _Builder(case, opts, DatedIndex(db))
    builder.build()
    builder.fixed_zones()
    builder.definitions()
    builder.inertia_minima()
    if opts.north_special:
        builder.north_special()
    issues = [msg for z in builder.new_zones for msg in plane_support_issues(z)]
    if issues:
        raise ValueError(f"{_NOTE}: inconsistent plane support: " + "; ".join(issues))
    changed = bool(builder.new_zones or builder.inertia_zones or builder.rewritten)
    provisions = builder.provisions() if changed else ()
    for msg in dict.fromkeys(builder.warnings):
        logger.warning("%s: %s", _NOTE, msg)
    if not changed:
        return case
    logger.info(
        "%s: %d zone(s) (%s), load term %s%s; %d inertia zone(s), %d inertia "
        "provision(s); %d PLEXOS row(s) replaced, %d rewritten",
        _NOTE,
        len(builder.new_zones),
        ", ".join(z.name for z in builder.new_zones),
        "endogenous_generation" if opts.endogenous else "exogenous",
        ""
        if opts.endogenous
        else (
            " (given system load)"
            if opts.system_load
            else f" (factor {opts.load_factor:g} x demand)"
        ),
        len(builder.inertia_zones),
        len(builder.inertia_provisions),
        len(builder.replaced),
        len(builder.rewritten),
    )
    if builder.retired:
        logger.info(
            "%s: %d extractor zone(s) superseded by native zones: %s",
            _NOTE,
            len(builder.retired),
            ", ".join(f"{k}→{v}" for k, v in sorted(builder.retired.items())),
        )
    return replace(
        case,
        reserves=builder.reserves(provisions),
        reserve_provisions=provisions,
        inertia_zones=case.inertia_zones + tuple(builder.inertia_zones),
        inertia_provisions=case.inertia_provisions + tuple(builder.inertia_provisions),
        user_constraints=builder.user_constraints(),
    )
