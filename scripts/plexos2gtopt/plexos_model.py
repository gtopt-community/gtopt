"""Materialise one PLEXOS *Model* of an XML-only database for conversion.

CEN PCP bundles ship a PLEXOS XML whose ``t_data`` is already the
data of one run, plus per-class CSV schedules (``Gen_Rating.csv``,
``Nod_Load.csv``, ...).  General PLEXOS databases (e.g. the SEM PLEXOS
Forecast Model 2024-2032 published by the SEM Committee) are different:
one XML holds several Models, the data rows are conditional on
Scenarios, dated (``t_date_from`` / ``t_date_to``), restricted to
timeslice patterns (``M04-09``, ``M01-3,H05``, ``!H18-19``) and refer to
Variables and Data Files (``Rating Factor = StochasticWindROI``, a
Variable whose Profile is a CSV with one column per sample).

:func:`materialise_model` resolves all of that for one Model and one
horizon (start date + days) and writes a CEN-style bundle the existing
extractors understand:

* a filtered copy of the XML: only rows active for the Model, made
  static (scenario / variable tags, dates and month-only patterns
  removed; values = the value at the horizon start);
* per-hour CSV schedules for the quantities that vary in the horizon
  or come from Data Files: ``Nod_Load.csv`` (node load + region Fixed
  Load), ``Gen_Rating.csv`` (per-unit rating x available units, with
  Rating / Rating Factor / Outage Rating / Units / Units Out),
  ``Gen_HeatRate.csv`` (time-varying heat rates), ``Fuel_Price.csv``
  (fuel price plus the emission price x production rate),
  ``Gen_StartCost.csv`` (start fuel offtake x fuel price),
  ``Lin_MaxRating.csv`` / ``Lin_MinRating.csv`` / ``Lin_Units.csv``;
* two structural rewrites that the extractors cannot express:
  a pumped-storage plant modelled as an *energy* Storage pair (head /
  tail in GWh, generators with ``Pump Load``) becomes one Battery, and
  Region ``DSP Bid Quantity`` / ``DSP Bid Price`` bands become
  price-bid generators.

Everything this module does not convert is listed, with row counts, in
the returned :class:`MaterialiseReport` and logged.
"""

from __future__ import annotations

import csv
import logging
import lzma
import math
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .plexos_xml import NS

logger = logging.getLogger(__name__)

#: PLEXOS ``t_action`` ids used by expression tags (``t_tag.action_id``).
_ACTION_SET, _ACTION_MUL, _ACTION_DIV, _ACTION_ADD, _ACTION_SUB = 0, 1, 2, 3, 4

#: Properties read by plexos2gtopt from the materialised XML / CSVs.
#: Any other property with active rows is reported as not converted.
_CONSUMED: frozenset[tuple[str, str]] = frozenset(
    {
        ("Generator", "Max Capacity"),
        ("Generator", "Units"),
        ("Generator", "Units Out"),
        ("Generator", "Rating"),
        ("Generator", "Rating Factor"),
        ("Generator", "Outage Rating"),
        ("Generator", "Min Stable Level"),
        ("Generator", "Heat Rate"),
        ("Generator", "Heat Rate Base"),
        ("Generator", "Heat Rate Incr"),
        ("Generator", "Load Point"),
        ("Generator", "VO&M Charge"),
        ("Generator", "Min Up Time"),
        ("Generator", "Min Down Time"),
        ("Generator", "Max Ramp Up"),
        ("Generator", "Max Ramp Down"),
        ("Generator", "Pump Efficiency"),
        ("Generator", "Pump Load"),
        ("Generator", "Pump Units"),
        ("Fuel", "Ratio"),
        ("Fuel", "Price"),
        ("Fuel", "Offtake at Start"),
        ("Fuel", "Production Rate"),
        ("Emission", "Price"),
        ("Battery", "Units"),
        ("Battery", "Capacity"),
        ("Battery", "Max Power"),
        ("Battery", "Max Load"),
        ("Battery", "Charge Efficiency"),
        ("Battery", "Discharge Efficiency"),
        ("Storage", "Max Volume"),
        ("Storage", "Min Volume"),
        ("Storage", "Initial Volume"),
        ("Line", "Max Flow"),
        ("Line", "Min Flow"),
        ("Line", "Units"),
        ("Line", "Units Out"),
        ("Line", "Outage Max Rating"),
        ("Line", "Outage Min Rating"),
        ("Line", "Wheeling Charge"),
        ("Node", "Load"),
        ("Region", "Fixed Load"),
        ("Region", "VoLL"),
        ("Region", "DSP Bid Quantity"),
        ("Region", "DSP Bid Price"),
        ("Constraint", "Sense"),
        ("Constraint", "RHS"),
        ("Constraint", "RHS Day"),
        ("Constraint", "RHS Week"),
        ("Constraint", "Generation Coefficient"),
        ("Constraint", "Penalty Price"),
        # Expression plumbing (consumed by this module itself).
        ("Variable", "Profile"),
        ("Data File", "Filename"),
        ("Timeslice", "Include"),
    }
)

#: Classes whose properties describe the run, not the system.
_RUN_CLASSES = frozenset(
    {
        "Model",
        "Horizon",
        "Report",
        "Stochastic",
        "ST Schedule",
        "MT Schedule",
        "PASA",
        "LT Plan",
        "Transmission",
        "Production",
        "Competition",
        "Performance",
        "Diagnostic",
        "Scenario",
    }
)


# ---------------------------------------------------------------------------
# Timeslice patterns
# ---------------------------------------------------------------------------

_TERM_RE = re.compile(r"^([A-Za-z]*)(\d+)(?:-(\d+))?$")


def timeslice_matcher(pattern: str) -> Callable[[datetime, int], bool] | None:
    """Compile a PLEXOS timeslice pattern into ``f(day, period) -> bool``.

    Grammar (PLEXOS "Timeslice" syntax as used in ``t_text``): a comma
    separated list of terms ``<T><a>[-<b>]`` with ``T`` in ``M`` (month
    1-12), ``H`` (period of the day 1-24), ``W`` (weekday 1-7, 1 =
    Sunday) or ``D`` (day of month).  A bare number continues the
    previous term's type (``M1-3,10-12`` = months 1-3 or 10-12).  Terms
    of the same type are OR-ed, different types AND-ed.  A leading
    ``!`` negates the whole pattern.  Returns ``None`` for an
    unrecognised pattern (e.g. a reference to a named Timeslice).
    """
    text = pattern.strip()
    negate = text.startswith("!")
    if negate:
        text = text[1:]
    sets: dict[str, set[int]] = {}
    kind = ""
    for raw in text.split(","):
        term = raw.strip()
        if not term:
            continue
        m = _TERM_RE.match(term)
        if m is None:
            return None
        letter = m.group(1).upper() or kind
        if letter not in ("M", "H", "W", "D"):
            return None
        kind = letter
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        sets.setdefault(letter, set()).update(range(lo, hi + 1))
    if not sets:
        return None

    def _match(day: datetime, period: int) -> bool:
        ok = True
        if "M" in sets:
            ok = ok and day.month in sets["M"]
        if "H" in sets:
            ok = ok and period in sets["H"]
        if "W" in sets:
            # PLEXOS W1 = Sunday; Python weekday() 0 = Monday.
            ok = ok and ((day.weekday() + 1) % 7 + 1) in sets["W"]
        if "D" in sets:
            ok = ok and day.day in sets["D"]
        return ok != negate

    return _match


def pattern_is_month_only(pattern: str) -> bool:
    """True when ``pattern`` restricts only months (no H/W/D terms)."""
    kind = ""
    for raw in pattern.strip().lstrip("!").split(","):
        term = raw.strip()
        if not term:
            continue
        m = _TERM_RE.match(term)
        if m is None:
            return False
        kind = m.group(1).upper() or kind
        if kind != "M":
            return False
    return True


# ---------------------------------------------------------------------------
# Data rows
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    data_id: int
    membership_id: int
    property_id: int
    value: float
    band: int = 1
    date_from: datetime | None = None
    date_to: datetime | None = None
    pattern: str | None = None
    match: Callable[[datetime, int], bool] | None = None
    # (object_id, action_id) of non-scenario tags (Variable / Data File).
    exprs: list[tuple[int, int | None]] = field(default_factory=list)
    scenarios: list[int] = field(default_factory=list)
    text: str | None = None  # non-timeslice t_text (Data File Filename)

    def active(self, ts: datetime, period: int) -> bool:
        """Date window and timeslice test at hour ``period`` of ``ts``'s day."""
        if self.date_from is not None and ts < self.date_from:
            return False
        if self.date_to is not None:
            dt = self.date_to
            if dt.hour == 0 and dt.minute == 0:
                if ts.date() > dt.date():
                    return False
            elif ts > dt:
                return False
        if self.match is not None and not self.match(ts, period):
            return False
        return True

    def priority(self) -> tuple[bool, bool, datetime, bool, int]:
        """Specificity: scenario data, pattern rows, latest date_from, windows.

        Data tagged with an active Scenario overrides base data whenever
        both are active at the same hour (PLEXOS scenario semantics);
        outside the scenario rows' dates the base row still applies.
        """
        return (
            bool(self.scenarios),
            self.pattern is not None,
            self.date_from or datetime.min,
            self.date_to is not None,
            -self.data_id,
        )


def _ftext(elem: ET.Element, tag: str) -> str | None:
    return elem.findtext(f"{NS}{tag}")


def _fint(elem: ET.Element, tag: str) -> int | None:
    raw = _ftext(elem, tag)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _ffloat(elem: ET.Element, tag: str) -> float | None:
    raw = _ftext(elem, tag)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Data File (CSV) reader
# ---------------------------------------------------------------------------


class _DataFile:
    """A PLEXOS Data File: ``Year,Month,Day,Period,<col>...`` (wide) CSV."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.values: dict[tuple[int, int, int, int], list[float]] = {}
        self.columns: list[str] = []
        opener = lzma.open if path.suffix.lower() == ".xz" else open
        with opener(path, "rt", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            header = [h.strip() for h in next(reader)]
            low = [h.lower() for h in header]
            try:
                iy, im, iday, ip = (
                    low.index("year"),
                    low.index("month"),
                    low.index("day"),
                    low.index("period"),
                )
            except ValueError as exc:
                raise ValueError(
                    f"{path}: unsupported Data File layout (need Year, Month, "
                    f"Day, Period columns), header={header}"
                ) from exc
            data_idx = [i for i in range(len(header)) if i not in (iy, im, iday, ip)]
            self.columns = [header[i] for i in data_idx]
            for rec in reader:
                if not rec:
                    continue
                key = (int(rec[iy]), int(rec[im]), int(rec[iday]), int(rec[ip]))
                self.values[key] = [float(rec[i]) for i in data_idx]

    def value(self, ts: datetime, period: int, column: str) -> float:
        """Value at ``(ts's day, period)`` in ``column`` (sample index)."""
        rec = self.values.get((ts.year, ts.month, ts.day, period))
        if rec is None:
            raise KeyError(f"{self.path.name}: no row for {ts.date()} period {period}")
        if column in self.columns:
            return rec[self.columns.index(column)]
        if len(self.columns) == 1:
            return rec[0]
        raise KeyError(f"{self.path.name}: no column {column!r} in {self.columns}")


class MissingDataFileError(FileNotFoundError):
    """A PLEXOS Data File named in the XML is not under the data dirs."""

    def __init__(self, data_file: str, filename: str, roots: Iterable[Path]) -> None:
        super().__init__(
            f"PLEXOS Data File {data_file!r}: {filename!r} not found under "
            f"{[str(r) for r in roots]} (pass --plexos-data-dir)"
        )
        self.data_file = data_file
        self.filename = filename


def find_data_file(name: str, roots: Iterable[Path]) -> Path | None:
    """Locate a PLEXOS ``Filename`` (Windows path) under ``roots``.

    Matches the basename case-insensitively, also as ``<name>.xz``,
    searching each root recursively.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    wanted = {base, base + ".xz"}
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name.lower() in wanted:
                return p
    return None


# ---------------------------------------------------------------------------
# Options / report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterialiseOptions:
    """What to materialise."""

    model: str
    start: datetime
    n_days: int = 1
    sample: int = 1
    data_dirs: tuple[Path, ...] = ()


@dataclass
class MaterialiseReport:
    """What :func:`materialise_model` did, for logs and provenance."""

    model: str = ""
    scenarios: list[str] = field(default_factory=list)
    start: str = ""
    n_days: int = 0
    sample: int = 1
    data_files: dict[str, str] = field(default_factory=dict)
    csv_written: dict[str, int] = field(default_factory=dict)
    rows_kept: int = 0
    rows_dropped_scenario: int = 0
    rows_dropped_inactive: int = 0
    frozen_at_start: dict[str, int] = field(default_factory=dict)
    not_converted: dict[str, int] = field(default_factory=dict)
    rewrites: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """JSON-friendly view."""
        return {
            "model": self.model,
            "scenarios": self.scenarios,
            "start": self.start,
            "n_days": self.n_days,
            "sample": self.sample,
            "data_files": self.data_files,
            "csv_written": self.csv_written,
            "rows_kept": self.rows_kept,
            "rows_dropped_scenario": self.rows_dropped_scenario,
            "rows_dropped_inactive": self.rows_dropped_inactive,
            "frozen_at_start": self.frozen_at_start,
            "not_converted": self.not_converted,
            "rewrites": self.rewrites,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# The materialiser
# ---------------------------------------------------------------------------


class _Db:
    """Indexes over a parsed MasterDataSet needed for resolution."""

    def __init__(self, root: ET.Element) -> None:
        self.root = root
        self.class_name: dict[int, str] = {}
        self.class_id: dict[str, int] = {}
        for e in root.findall(f"{NS}t_class"):
            cid, name = _fint(e, "class_id"), _ftext(e, "name")
            if cid is not None and name:
                self.class_name[cid] = name
                self.class_id.setdefault(name, cid)
        self.obj_name: dict[int, str] = {}
        self.obj_class: dict[int, int] = {}
        for e in root.findall(f"{NS}t_object"):
            oid, cid = _fint(e, "object_id"), _fint(e, "class_id")
            if oid is not None and cid is not None:
                self.obj_name[oid] = _ftext(e, "name") or ""
                self.obj_class[oid] = cid
        self.coll: dict[int, tuple[int, int, str]] = {}
        for e in root.findall(f"{NS}t_collection"):
            cid = _fint(e, "collection_id")
            p, c = _fint(e, "parent_class_id"), _fint(e, "child_class_id")
            if cid is not None and p is not None and c is not None:
                self.coll[cid] = (p, c, _ftext(e, "name") or "")
        self.mem: dict[int, tuple[int, int, int]] = {}
        for e in root.findall(f"{NS}t_membership"):
            mid, cid = _fint(e, "membership_id"), _fint(e, "collection_id")
            po, co = _fint(e, "parent_object_id"), _fint(e, "child_object_id")
            if None not in (mid, cid, po, co):
                self.mem[mid] = (cid, po, co)  # type: ignore[index,assignment]
        self.prop_name: dict[int, str] = {}
        self.prop_coll: dict[int, int] = {}
        for e in root.findall(f"{NS}t_property"):
            pid, cid = _fint(e, "property_id"), _fint(e, "collection_id")
            if pid is not None and cid is not None:
                self.prop_name[pid] = _ftext(e, "name") or ""
                self.prop_coll[pid] = cid
        self.actions: dict[int, str] = {
            _fint(e, "action_id") or 0: _ftext(e, "action_symbol") or ""
            for e in root.findall(f"{NS}t_action")
        }

    def cls_of(self, oid: int) -> str:
        return self.class_name.get(self.obj_class.get(oid, -1), "")

    def child_class_of_membership(self, mid: int) -> str:
        cid, _, _ = self.mem[mid]
        return self.class_name.get(self.coll[cid][1], "")

    def collection_name_of_membership(self, mid: int) -> str:
        return self.coll[self.mem[mid][0]][2]

    def find_collection(self, parent: str, child: str, name: str | None = None) -> int:
        pid, cid = self.class_id.get(parent), self.class_id.get(child)
        for coll_id, (p, c, n) in sorted(self.coll.items()):
            if p == pid and c == cid and (name is None or n == name):
                return coll_id
        raise KeyError(f"no collection {parent}->{child} {name or ''}")

    def find_property(self, coll_id: int, name: str) -> int:
        for pid, cid in sorted(self.prop_coll.items()):
            if cid == coll_id and self.prop_name[pid] == name:
                return pid
        raise KeyError(f"no property {name!r} in collection {coll_id}")


def _model_scenarios(db: _Db, model: str) -> tuple[int, list[int]]:
    model_cid = db.class_id.get("Model")
    scn_cid = db.class_id.get("Scenario")
    models = {
        oid: name for oid, name in db.obj_name.items() if db.obj_class[oid] == model_cid
    }
    match = [oid for oid, name in models.items() if name == model]
    if not match:
        raise ValueError(
            f"PLEXOS model {model!r} not found; available: {sorted(models.values())}"
        )
    moid = match[0]
    scen = sorted(
        co
        for (_, po, co) in db.mem.values()
        if po == moid and db.obj_class.get(co) == scn_cid
    )
    return moid, scen


def _hours(opts: MaterialiseOptions) -> list[tuple[datetime, int]]:
    """``(timestamp, period)`` for every hour of the horizon (period 1..24)."""
    base = datetime(opts.start.year, opts.start.month, opts.start.day)
    out = []
    for d in range(opts.n_days):
        day = base + timedelta(days=d)
        for p in range(1, 25):
            out.append((day + timedelta(hours=p - 1), p))
    return out


class _Resolver:
    """Evaluate property values (with expressions) at an hour."""

    def __init__(
        self,
        db: _Db,
        groups: dict[tuple[int, int, int], list[_Row]],
        opts: MaterialiseOptions,
        roots: list[Path],
        report: MaterialiseReport,
    ) -> None:
        self.db = db
        self.groups = groups
        self.opts = opts
        self.roots = roots
        self.report = report
        self.sys_mem: dict[int, int] = {}
        sys_cid = db.class_id.get("System")
        for mid, (cid, _po, co) in db.mem.items():
            if db.coll[cid][0] == sys_cid:
                self.sys_mem[co] = mid
        self.by_obj_prop: dict[tuple[int, str], list[int]] = defaultdict(list)
        self.index: dict[tuple[int, str, int], list[_Row]] = {}
        for (mid, pid, band), rows in groups.items():
            self.by_obj_prop[(mid, db.prop_name[pid])].append(band)
            self.index[(mid, db.prop_name[pid], band)] = rows
        self._files: dict[int, _DataFile | None] = {}

    # -- generic -------------------------------------------------------
    def rows(self, mid: int, prop: str, band: int = 1) -> list[_Row]:
        return self.index.get((mid, prop, band), [])

    def bands(self, mid: int, prop: str) -> list[int]:
        return sorted(set(self.by_obj_prop.get((mid, prop), [])))

    def has(self, mid: int, prop: str) -> bool:
        return bool(self.by_obj_prop.get((mid, prop)))

    def pick(self, rows: list[_Row], ts: datetime, period: int) -> _Row | None:
        act = [r for r in rows if r.active(ts, period)]
        if not act:
            return None
        return max(act, key=_Row.priority)

    def value(
        self, mid: int, prop: str, ts: datetime, period: int, band: int = 1
    ) -> float | None:
        row = self.pick(self.rows(mid, prop, band), ts, period)
        if row is None:
            return None
        return self.row_value(row, ts, period)

    def row_value(self, row: _Row, ts: datetime, period: int) -> float:
        val = row.value
        for oid, action in row.exprs:
            cls = self.db.cls_of(oid)
            operand: float | None
            if cls == "Data File":
                operand = self.data_file_value(oid, ts, period)
            elif cls == "Variable":
                operand = self.variable_value(oid, ts, period)
            else:
                continue
            if operand is None:
                continue
            act = _ACTION_SET if action is None else action
            if act == _ACTION_SET:
                val = operand
            elif act == _ACTION_MUL:
                val *= operand
            elif act == _ACTION_DIV:
                val = val / operand if operand else val
            elif act == _ACTION_ADD:
                val += operand
            elif act == _ACTION_SUB:
                val -= operand
        return val

    def variable_value(self, oid: int, ts: datetime, period: int) -> float | None:
        """Variable ``Profile`` at the hour; ``None`` when undefined there."""
        mid = self.sys_mem.get(oid)
        if mid is None:
            return None
        bands = self.bands(mid, "Profile")
        if not bands:
            return None
        return self.value(mid, "Profile", ts, period, bands[0])

    def data_file(self, oid: int) -> _DataFile | None:
        if oid in self._files:
            return self._files[oid]
        mid = self.sys_mem.get(oid)
        df: _DataFile | None = None
        if mid is not None:
            row = self.pick(self.rows(mid, "Filename"), self.opts.start, 1)
            if row is not None and row.text:
                path = find_data_file(row.text, self.roots)
                if path is None:
                    raise MissingDataFileError(
                        self.db.obj_name[oid], row.text, self.roots
                    )
                try:
                    df = _DataFile(path)
                except ValueError as exc:
                    # e.g. a ``Name,Pattern,Value`` loss-factor table:
                    # rows referring to it keep their own value.
                    self.report.notes.append(f"Data File not read: {exc}")
                    df = None
                else:
                    self.report.data_files[self.db.obj_name[oid]] = path.name
        self._files[oid] = df
        return df

    def data_file_value(self, oid: int, ts: datetime, period: int) -> float | None:
        """Data File value at the hour (sample column); ``None`` if unread."""
        df = self.data_file(oid)
        if df is None:
            return None
        return df.value(ts, period, str(self.opts.sample))

    def series(
        self, mid: int, prop: str, hours: list[tuple[datetime, int]], band: int = 1
    ) -> list[float | None]:
        rows = self.rows(mid, prop, band)
        out: list[float | None] = []
        for ts, p in hours:
            r = self.pick(rows, ts, p)
            out.append(None if r is None else self.row_value(r, ts, p))
        return out


def _parse_rows(
    db: _Db,
    root: ET.Element,
    active_scenarios: set[int],
    report: MaterialiseReport,
) -> tuple[dict[tuple[int, int, int], list[_Row]], set[int]]:
    """Parse ``t_data`` + companions; return active groups and dropped ids."""
    band: dict[int, int] = {}
    for e in root.findall(f"{NS}t_band"):
        did, b = _fint(e, "data_id"), _fint(e, "band_id")
        if did is not None and b is not None:
            band[did] = b
    dfrom: dict[int, datetime] = {}
    dto: dict[int, datetime] = {}
    for table, dest in (("t_date_from", dfrom), ("t_date_to", dto)):
        for e in root.findall(f"{NS}{table}"):
            did, raw = _fint(e, "data_id"), _ftext(e, "date")
            if did is not None and raw:
                try:
                    dest[did] = datetime.fromisoformat(raw)
                except ValueError:
                    continue
    ts_cid = db.class_id.get("Timeslice")
    pattern: dict[int, str] = {}
    text: dict[int, str] = {}
    for e in root.findall(f"{NS}t_text"):
        did, cid, txt = _fint(e, "data_id"), _fint(e, "class_id"), _ftext(e, "value")
        if did is None or txt is None:
            continue
        if cid == ts_cid:
            pattern[did] = txt.strip()
        else:
            text[did] = txt
    scn_cid = db.class_id.get("Scenario")
    tags: dict[int, list[tuple[int, int | None]]] = defaultdict(list)
    for e in root.findall(f"{NS}t_tag"):
        did, oid = _fint(e, "data_id"), _fint(e, "object_id")
        if did is not None and oid is not None:
            tags[did].append((oid, _fint(e, "action_id")))

    all_rows: dict[tuple[int, int, int], list[_Row]] = defaultdict(list)
    dropped: set[int] = set()
    for e in root.findall(f"{NS}t_data"):
        did, mid, pid = (
            _fint(e, "data_id"),
            _fint(e, "membership_id"),
            _fint(e, "property_id"),
        )
        val = _ffloat(e, "value")
        if did is None or mid is None or pid is None or val is None:
            continue
        scen = [o for o, _ in tags.get(did, []) if db.obj_class.get(o) == scn_cid]
        exprs = [(o, a) for o, a in tags.get(did, []) if db.obj_class.get(o) != scn_cid]
        if scen and not any(s in active_scenarios for s in scen):
            dropped.add(did)
            report.rows_dropped_scenario += 1
            continue
        pat = pattern.get(did)
        match = timeslice_matcher(pat) if pat else None
        if pat and match is None:
            report.notes.append(f"unparsed timeslice pattern {pat!r} (row ignored)")
            dropped.add(did)
            continue
        row = _Row(
            data_id=did,
            membership_id=mid,
            property_id=pid,
            value=val,
            band=band.get(did, 1),
            date_from=dfrom.get(did),
            date_to=dto.get(did),
            pattern=pat,
            match=match,
            exprs=exprs,
            scenarios=scen,
            text=text.get(did),
        )
        all_rows[(mid, pid, row.band)].append(row)
    return dict(all_rows), dropped


def _write_long(path: Path, series: dict[str, list[float]], hours) -> int:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["NAME", "YEAR", "MONTH", "DAY", "PERIOD", "BAND", "VALUE"])
        for name in sorted(series):
            for (ts, p), v in zip(hours, series[name], strict=True):
                w.writerow([name, ts.year, ts.month, ts.day, p, 1, _fmt(v)])
    return len(series)


def _write_wide(path: Path, series: dict[str, list[float]], hours) -> int:
    names = sorted(series)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["YEAR", "MONTH", "DAY", "PERIOD", *names])
        for i, (ts, p) in enumerate(hours):
            w.writerow(
                [ts.year, ts.month, ts.day, p, *(_fmt(series[n][i]) for n in names)]
            )
    return len(names)


def _fmt(v: float) -> str:
    return f"{v:.10g}"


def _const(values: list[float]) -> bool:
    return max(values) - min(values) <= 1e-12 * max(1.0, abs(max(values)))


class _XmlEditor:
    """Minimal in-place editing of the MasterDataSet tables."""

    def __init__(self, root: ET.Element, db: _Db) -> None:
        self.root = root
        self.db = db
        self._next_data = 1 + max(
            (_fint(e, "data_id") or 0 for e in root.findall(f"{NS}t_data")),
            default=0,
        )
        self._next_obj = 1 + max(db.obj_name, default=0)
        self._next_mem = 1 + max(db.mem, default=0)

    def _append(self, table: str, fields: dict[str, object]) -> None:
        elem = ET.SubElement(self.root, f"{NS}{table}")
        for k, v in fields.items():
            ET.SubElement(elem, f"{NS}{k}").text = str(v)

    def add_object(self, cls: str, name: str) -> int:
        oid = self._next_obj
        self._next_obj += 1
        self._append(
            "t_object",
            {
                "object_id": oid,
                "class_id": self.db.class_id[cls],
                "name": name,
                "category_id": "-1",
            },
        )
        self.db.obj_name[oid] = name
        self.db.obj_class[oid] = self.db.class_id[cls]
        return oid

    def add_membership(self, coll_id: int, parent: int, child: int) -> int:
        mid = self._next_mem
        self._next_mem += 1
        p, c, _ = self.db.coll[coll_id]
        self._append(
            "t_membership",
            {
                "membership_id": mid,
                "parent_class_id": p,
                "parent_object_id": parent,
                "collection_id": coll_id,
                "child_class_id": c,
                "child_object_id": child,
            },
        )
        self.db.mem[mid] = (coll_id, parent, child)
        return mid

    def add_data(self, mid: int, pid: int, value: float, band: int = 1) -> int:
        did = self._next_data
        self._next_data += 1
        self._append(
            "t_data",
            {"data_id": did, "membership_id": mid, "property_id": pid, "value": value},
        )
        if band != 1:
            self._append("t_band", {"data_id": did, "band_id": band})
        return did

    def _filter(self, table: str, key: str, dead: set[int]) -> None:
        """Remove every ``table`` element whose ``key`` is in ``dead``.

        Rebuilds the root's child list in one pass (``Element.remove``
        is linear, so per-element removal is quadratic on 100k rows).
        """
        if not dead:
            return
        tag = f"{NS}{table}"
        keep = [e for e in self.root if e.tag != tag or _fint(e, key) not in dead]
        self.root[:] = keep

    def remove_objects(self, oids: set[int]) -> None:
        """Drop objects, their memberships and those memberships' data."""
        dead_mem = {
            mid for mid, (_, po, co) in self.db.mem.items() if po in oids or co in oids
        }
        dead_data = {
            d
            for e in self.root.findall(f"{NS}t_data")
            if _fint(e, "membership_id") in dead_mem
            and (d := _fint(e, "data_id")) is not None
        }
        self._filter("t_object", "object_id", oids)
        self._filter("t_membership", "membership_id", dead_mem)
        self.remove_data(dead_data)
        for mid in dead_mem:
            self.db.mem.pop(mid, None)
        for oid in oids:
            self.db.obj_name.pop(oid, None)
            self.db.obj_class.pop(oid, None)

    def remove_data(self, dids: set[int]) -> None:
        """Drop ``t_data`` rows and their tag / text / date / band companions."""
        for table in (
            "t_data",
            "t_tag",
            "t_text",
            "t_date_from",
            "t_date_to",
            "t_band",
        ):
            self._filter(table, "data_id", dids)


def materialise_model(
    xml_path: Path, opts: MaterialiseOptions, out_dir: Path
) -> MaterialiseReport:
    """Resolve ``opts.model`` of ``xml_path`` into a bundle at ``out_dir``.

    Writes ``out_dir/<xml name>`` (resolved, static XML) plus the CSV
    schedules listed in the module docstring.  Data Files named by the
    XML are searched under ``opts.data_dirs`` and the XML's directory.
    """
    report = MaterialiseReport(
        model=opts.model,
        start=opts.start.date().isoformat(),
        n_days=opts.n_days,
        sample=opts.sample,
    )
    tree = ET.parse(xml_path)
    root = tree.getroot()
    db = _Db(root)
    _moid, scen = _model_scenarios(db, opts.model)
    report.scenarios = [db.obj_name[s] for s in scen]
    groups, dropped = _parse_rows(db, root, set(scen), report)
    roots = [*opts.data_dirs, xml_path.parent]
    res = _Resolver(db, groups, opts, roots, report)
    hours = _hours(opts)
    t0, p0 = hours[0]

    editor = _XmlEditor(root, db)
    csvs: dict[str, dict[str, list[float]]] = defaultdict(dict)

    # ---- per-object derived schedules --------------------------------
    gen_cid = db.class_id.get("Generator")
    gens = sorted(o for o, c in db.obj_class.items() if c == gen_cid)
    sysm = res.sys_mem

    def ser(oid: int, prop: str, default: float, band: int = 1) -> list[float]:
        mid = sysm.get(oid)
        if mid is None:
            return [default] * len(hours)
        return [default if v is None else v for v in res.series(mid, prop, hours, band)]

    # Fuel effective price: price + sum(emission price x production rate).
    fuel_cid = db.class_id.get("Fuel")
    fuels = sorted(o for o, c in db.obj_class.items() if c == fuel_cid)
    fuel_price: dict[int, list[float]] = {f: ser(f, "Price", 0.0) for f in fuels}
    em_cid = db.class_id.get("Emission")
    try:
        em_fuel_coll = db.find_collection("Emission", "Fuel")
    except KeyError:
        em_fuel_coll = -1
    em_price: dict[int, list[float]] = {}
    for mid, (cid, po, co) in db.mem.items():
        if cid != em_fuel_coll or db.obj_class.get(po) != em_cid:
            continue
        rate = res.series(mid, "Production Rate", hours)
        if all(v is None or v == 0.0 for v in rate):
            continue
        if po not in em_price:
            em_price[po] = ser(po, "Price", 0.0)
        for i, r in enumerate(rate):
            if r:
                fuel_price[co][i] += r * em_price[po][i]
    if em_price:
        report.notes.append(
            "emission prices folded into fuel prices: "
            + ", ".join(
                f"{db.obj_name[e]}={p[0]:g}/kg at start" for e, p in em_price.items()
            )
        )
    for f in fuels:
        if any(fuel_price[f]):
            csvs["Fuel_Price.csv"][db.obj_name[f]] = fuel_price[f]

    # Generator -> Fuels: drop fuels with Ratio 0 at start, primary =
    # largest ratio.  Start costs from Start Fuels offtake.
    try:
        gf_coll = db.find_collection("Generator", "Fuel", "Fuels")
        sf_coll = db.find_collection("Generator", "Fuel", "Start Fuels")
    except KeyError:
        gf_coll = sf_coll = -1
    dead_fuel_mem: set[int] = set()
    fuel_order: dict[int, list[tuple[float, int]]] = defaultdict(list)
    start_cost: dict[int, list[float]] = {}
    for mid, (cid, po, co) in sorted(db.mem.items()):
        if cid == gf_coll:
            ratio = res.value(mid, "Ratio", t0, p0)
            r = 1.0 if ratio is None else ratio
            if r <= 0.0:
                dead_fuel_mem.add(mid)
            else:
                fuel_order[po].append((r, mid))
        elif cid == sf_coll:
            off = res.series(mid, "Offtake at Start", hours)
            if any(v for v in off if v):
                acc = start_cost.setdefault(po, [0.0] * len(hours))
                for i, v in enumerate(off):
                    if v:
                        acc[i] += v * fuel_price.get(co, [0.0] * len(hours))[i]
    multi = {g: v for g, v in fuel_order.items() if len(v) > 1}
    if multi:
        report.notes.append(
            "fixed-ratio multi-fuel generators use their largest-ratio fuel only: "
            + ", ".join(sorted(db.obj_name[g] for g in multi))
        )
    for g, start in start_cost.items():
        csvs["Gen_StartCost.csv"][db.obj_name[g]] = start
    _reorder_fuel_memberships(root, fuel_order)
    editor_pre = _XmlEditor(root, db)
    editor_pre._filter(  # pylint: disable=protected-access
        "t_membership", "membership_id", dead_fuel_mem
    )
    for mid in dead_fuel_mem:
        db.mem.pop(mid, None)

    # Generator ratings (MW available per hour) and heat rates.
    pumped: dict[int, list[int]] = _pumped_storage_generators(db, res)
    pumped_gens = {g for gs in pumped.values() for g in gs}
    for g in gens:
        if g in pumped_gens:
            continue
        gmid = sysm.get(g)
        if gmid is None:
            continue
        mid = gmid
        units = ser(g, "Units", 1.0 if not res.has(mid, "Units") else 0.0)
        out = ser(g, "Units Out", 0.0)
        maxcap = ser(g, "Max Capacity", 0.0)
        rating = res.series(mid, "Rating", hours) if res.has(mid, "Rating") else None
        rf = (
            res.series(mid, "Rating Factor", hours)
            if res.has(mid, "Rating Factor")
            else None
        )
        orating = (
            res.series(mid, "Outage Rating", hours)
            if res.has(mid, "Outage Rating")
            else None
        )
        mw: list[float] = []
        for i in range(len(hours)):
            cap = maxcap[i]
            rf_i = None if rf is None else rf[i]
            rating_i = None if rating is None else rating[i]
            orating_i = None if orating is None else orating[i]
            if rf_i is not None:
                cap = cap * rf_i / 100.0
            if rating_i is not None:
                cap = min(cap, rating_i) if rf is not None else rating_i
            if orating_i is not None:
                cap = min(cap, orating_i)
            avail = max(units[i] - out[i], 0.0)
            mw.append(max(cap, 0.0) * avail)
        csvs["Gen_Rating.csv"][db.obj_name[g]] = mw
        if res.has(mid, "Heat Rate"):
            hr = ser(g, "Heat Rate", 0.0)
            if not _const(hr):
                csvs["Gen_HeatRate.csv"][db.obj_name[g]] = hr

    # Node load (+ region Fixed Load on single-node regions).
    node_cid = db.class_id.get("Node")
    load: dict[int, list[float]] = {}
    for n in sorted(o for o, c in db.obj_class.items() if c == node_cid):
        if sysm.get(n) is not None and res.has(sysm[n], "Load"):
            load[n] = ser(n, "Load", 0.0)
    region_nodes: dict[int, list[int]] = defaultdict(list)
    try:
        nr_coll = db.find_collection("Node", "Region")
        for cid, po, co in db.mem.values():
            if cid == nr_coll:
                region_nodes[co].append(po)
    except KeyError:
        pass
    for reg, nodes in region_nodes.items():
        rmid = sysm.get(reg)
        if rmid is None or not res.has(rmid, "Fixed Load"):
            continue
        fixed = ser(reg, "Fixed Load", 0.0)
        if len(nodes) != 1:
            report.notes.append(
                f"Region {db.obj_name[reg]!r} Fixed Load not applied "
                f"({len(nodes)} nodes)"
            )
            continue
        acc = load.setdefault(nodes[0], [0.0] * len(hours))
        for i, v in enumerate(fixed):
            acc[i] += v
        report.notes.append(
            f"Region {db.obj_name[reg]!r} Fixed Load {fixed[0]:g} MW added to "
            f"node {db.obj_name[nodes[0]]!r} load"
        )
    for n, series in load.items():
        csvs["Nod_Load.csv"][db.obj_name[n]] = [max(v, 0.0) for v in series]

    # Lines: hourly ratings and units.
    line_cid = db.class_id.get("Line")
    for ln in sorted(o for o, c in db.obj_class.items() if c == line_cid):
        lmid = sysm.get(ln)
        if lmid is None:
            continue
        mid = lmid
        units = ser(ln, "Units", 1.0)
        out = ser(ln, "Units Out", 0.0)
        fmax = ser(ln, "Max Flow", 0.0)
        fmin = ser(ln, "Min Flow", 0.0)
        omax = res.series(mid, "Outage Max Rating", hours)
        omin = res.series(mid, "Outage Min Rating", hours)
        name = db.obj_name[ln]
        # Only lines that carry the property get a schedule: the line
        # extractor reads an all-zero / absent Min Flow as "symmetric".
        if res.has(mid, "Max Flow") or res.has(mid, "Outage Max Rating"):
            csvs["Lin_MaxRating.csv"][name] = [
                fmax[i] if (om := omax[i]) is None else min(fmax[i], om)
                for i in range(len(hours))
            ]
        if res.has(mid, "Min Flow") or res.has(mid, "Outage Min Rating"):
            csvs["Lin_MinRating.csv"][name] = [
                fmin[i] if (on := omin[i]) is None else max(fmin[i], -abs(on))
                for i in range(len(hours))
            ]
        csvs["Lin_Units.csv"][name] = [
            max(units[i] - out[i], 0.0) for i in range(len(hours))
        ]

    # ---- structural rewrites ----------------------------------------
    new_rows: list[tuple[int, int, float, int]] = []  # (mid, pid, value, band)
    _rewrite_pumped_storage(
        db, res, editor, pumped, t0, p0, report, csvs, new_rows, len(hours)
    )
    _rewrite_dsp_bids(db, res, editor, hours, report, csvs, new_rows)
    dead_batt = _battery_units(db, res, t0, p0, report)
    if dead_batt:
        editor.remove_objects(dead_batt)

    # ---- make the remaining rows static -------------------------------
    keep: dict[int, float] = {}
    for (mid, pid, _band), rows in groups.items():
        if mid not in db.mem:
            continue
        child_cls = db.child_class_of_membership(mid)
        pname = db.prop_name[pid]
        row = res.pick(rows, t0, p0)
        if row is None:
            dropped.update(r.data_id for r in rows)
            report.rows_dropped_inactive += len(rows)
            continue
        key = f"{child_cls}.{pname}"
        converted = child_cls in _RUN_CLASSES or (child_cls, pname) in _CONSUMED
        try:
            val = res.row_value(row, t0, p0)
        except MissingDataFileError as exc:
            if converted:
                raise
            # A Data File used only by a property that is not converted
            # (e.g. a loss-factor table) need not be present.
            val = row.value
            note = (
                f"{key}: Data File {exc.data_file!r} ({exc.filename!r}) not "
                "found; property not converted"
            )
            if note not in report.notes:
                report.notes.append(note)
        if (child_cls, pname) in (
            ("Battery", "Capacity"),
            ("Battery", "Max Power"),
            ("Battery", "Max Load"),
        ):
            val *= _battery_unit_count(res, db, mid, t0, p0)
        keep[row.data_id] = val
        dropped.update(r.data_id for r in rows if r.data_id != row.data_id)
        report.rows_kept += 1
        if not converted:
            report.not_converted[key] = report.not_converted.get(key, 0) + 1
        elif _varies(res, rows, hours) and not _scheduled(child_cls, pname):
            report.frozen_at_start[key] = report.frozen_at_start.get(key, 0) + 1

    editor.remove_data(dropped)
    kept_ids = set(keep)
    for e in root.findall(f"{NS}t_data"):
        did = _fint(e, "data_id")
        if did in kept_ids:
            vel = e.find(f"{NS}value")
            if vel is not None and did is not None:
                vel.text = _fmt(keep[did])
    # Strip dates, scenario / expression tags and patterns of kept rows.
    ts_tag = f"{NS}t_text"
    strip = {f"{NS}t_tag", f"{NS}t_date_from", f"{NS}t_date_to"}
    ts_cid = db.class_id.get("Timeslice")
    root[:] = [
        e
        for e in root
        if not (
            (e.tag in strip or (e.tag == ts_tag and _fint(e, "class_id") == ts_cid))
            and _fint(e, "data_id") in kept_ids
        )
    ]
    for mid, pid, value, band in new_rows:
        editor.add_data(mid, pid, value, band)

    # ---- write ---------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    tree.write(out_dir / xml_path.name, encoding="utf-8", xml_declaration=True)
    for csv_name, table in sorted(csvs.items()):
        if not table:
            continue
        writer = (
            _write_wide
            if csv_name in ("Nod_Load.csv", "Lin_Units.csv")
            else _write_long
        )
        report.csv_written[csv_name] = writer(out_dir / csv_name, table, hours)
    _log_report(report)
    return report


def _scheduled(cls: str, prop: str) -> bool:
    """True when the property's time variation reaches the CSV schedules."""
    return (cls, prop) in {
        ("Generator", "Max Capacity"),
        ("Generator", "Units"),
        ("Generator", "Units Out"),
        ("Generator", "Rating"),
        ("Generator", "Rating Factor"),
        ("Generator", "Outage Rating"),
        ("Generator", "Heat Rate"),
        ("Fuel", "Price"),
        ("Fuel", "Offtake at Start"),
        ("Emission", "Price"),
        ("Line", "Max Flow"),
        ("Line", "Min Flow"),
        ("Line", "Units"),
        ("Line", "Units Out"),
        ("Line", "Outage Max Rating"),
        ("Line", "Outage Min Rating"),
        ("Node", "Load"),
        ("Region", "Fixed Load"),
        ("Region", "DSP Bid Quantity"),
        ("Variable", "Profile"),
        ("Data File", "Filename"),
        ("Timeslice", "Include"),
    }


def _varies(res: _Resolver, rows: list[_Row], hours) -> bool:
    if len(rows) == 1 and not rows[0].exprs and rows[0].pattern is None:
        return False
    vals = []
    for ts, p in hours:
        r = res.pick(rows, ts, p)
        vals.append(math.nan if r is None else res.row_value(r, ts, p))
    finite = [v for v in vals if not math.isnan(v)]
    return len(finite) != len(vals) or (bool(finite) and not _const(finite))


def _reorder_fuel_memberships(
    root: ET.Element, fuel_order: dict[int, list[tuple[float, int]]]
) -> None:
    """Put each multi-fuel generator's largest-ratio fuel membership first.

    The extractors take a generator's first ``Fuels`` membership as its
    primary fuel.
    """
    rank: dict[int, int] = {}
    for entries in fuel_order.values():
        if len(entries) > 1:
            ordered = sorted(entries, key=lambda e: (-e[0], e[1]))
            for k, (_r, mid) in enumerate(ordered):
                rank[mid] = k
    if not rank:
        return
    tag = f"{NS}t_membership"
    target = [e for e in root if e.tag == tag and _fint(e, "membership_id") in rank]
    target.sort(key=lambda e: (rank[_fint(e, "membership_id") or 0],))
    root[:] = [e for e in root if not (e.tag == tag and e in target)] + target


def _battery_unit_count(
    res: _Resolver, db: _Db, mid: int, ts: datetime, period: int
) -> float:
    if not res.has(mid, "Units"):
        return 1.0
    v = res.value(mid, "Units", ts, period)
    return 1.0 if v is None else max(v, 0.0)


def _battery_units(
    db: _Db, res: _Resolver, ts: datetime, period: int, report: MaterialiseReport
) -> set[int]:
    """Batteries with zero Units at the start (not yet built / retired)."""
    bcid = db.class_id.get("Battery")
    dead = set()
    for oid, cid in db.obj_class.items():
        if cid != bcid:
            continue
        mid = res.sys_mem.get(oid)
        if mid is not None and res.has(mid, "Units"):
            if _battery_unit_count(res, db, mid, ts, period) <= 0.0:
                dead.add(oid)
    if dead:
        report.rewrites.append(
            f"{len(dead)} batteries with Units = 0 at the start removed: "
            + ", ".join(sorted(db.obj_name[o] for o in dead))
        )
    return dead


def _pumped_storage_generators(db: _Db, res: _Resolver) -> dict[int, list[int]]:
    """``{head storage -> [generators]}`` for energy-model pumped storage."""
    try:
        head = db.find_collection("Generator", "Storage", "Head Storage")
    except KeyError:
        return {}
    out: dict[int, list[int]] = defaultdict(list)
    for cid, po, co in db.mem.values():
        if cid != head:
            continue
        mid = res.sys_mem.get(po)
        if mid is not None and res.has(mid, "Pump Load"):
            out[co].append(po)
    return {k: sorted(v) for k, v in out.items()}


def _rewrite_pumped_storage(
    db: _Db,
    res: _Resolver,
    editor: _XmlEditor,
    pumped: dict[int, list[int]],
    ts: datetime,
    period: int,
    report: MaterialiseReport,
    csvs: dict[str, dict[str, list[float]]],
    new_rows: list[tuple[int, int, float, int]],
    n_hours: int,
) -> None:
    """Replace each energy-model pumped-storage plant by one Battery.

    PLEXOS models e.g. Turlough Hill as a head and a tail Storage in
    GWh plus generators that move energy head -> tail when generating
    and tail -> head (with ``Pump Efficiency``) when pumping.  The
    battery keeps the usable head energy band ``[Min, Max] Volume``,
    the summed generator and pump ratings and the pump efficiency.
    """
    if not pumped:
        return
    bcid = db.class_id.get("Battery")
    if bcid is None:
        report.notes.append("pumped storage kept as hydro: no Battery class")
        return
    sys_oid = next(o for o, c in db.obj_class.items() if db.class_name[c] == "System")
    sys_batt = db.find_collection("System", "Battery")
    batt_node = db.find_collection("Battery", "Node")
    gen_node = db.find_collection("Generator", "Node")
    tail_coll = db.find_collection("Generator", "Storage", "Tail Storage")
    for head, gens in pumped.items():
        hmid = res.sys_mem[head]

        def hv(prop: str, default: float, _mid: int = hmid) -> float:
            v = res.value(_mid, prop, ts, period)
            return default if v is None else v

        vmax, vmin, vini = (
            hv("Max Volume", 0.0),
            hv("Min Volume", 0.0),
            hv("Initial Volume", 0.0),
        )
        gen_mw = pump_mw = 0.0
        effs = []
        nodes = set()
        tails = set()
        for g in gens:
            gm = res.sys_mem[g]
            units = res.value(gm, "Units", ts, period)
            u = 1.0 if units is None else units
            gen_mw += (res.value(gm, "Max Capacity", ts, period) or 0.0) * u
            pu = res.value(gm, "Pump Units", ts, period)
            pump_mw += (res.value(gm, "Pump Load", ts, period) or 0.0) * (
                u if pu is None or pu >= 1e20 else pu
            )
            eff = res.value(gm, "Pump Efficiency", ts, period)
            effs.append(70.0 if eff is None else eff)
            for cid, po, co in db.mem.values():
                if po == g and cid == gen_node:
                    nodes.add(co)
                if po == g and cid == tail_coll:
                    tails.add(co)
        if len(nodes) != 1:
            report.notes.append(
                f"pumped storage {db.obj_name[head]!r} not rewritten "
                f"({len(nodes)} nodes)"
            )
            continue
        name = f"{db.obj_name[head]} pumped storage"
        boid = editor.add_object("Battery", name)
        bmid = editor.add_membership(sys_batt, sys_oid, boid)
        editor.add_membership(batt_node, boid, next(iter(nodes)))
        cap = max(vmax - vmin, 0.0) * 1000.0
        eff = sum(effs) / len(effs)
        for prop, value in (
            ("Capacity", cap),
            ("Max Power", gen_mw),
            ("Max Load", pump_mw),
            ("Charge Efficiency", eff),
            ("Discharge Efficiency", 100.0),
        ):
            new_rows.append((bmid, db.find_property(sys_batt, prop), value, 1))
        ini = max(min(vini, vmax) - vmin, 0.0) * 1000.0
        if cap > 0.0:
            csvs["BESS_IniValue.csv"][name] = [100.0 * ini / cap] * n_hours
        report.rewrites.append(
            f"pumped storage {db.obj_name[head]!r} + {len(gens)} units "
            f"({', '.join(db.obj_name.get(g, str(g)) for g in gens)}) -> Battery "
            f"{name!r}: {cap:g} MWh usable, {gen_mw:g} MW generating, "
            f"{pump_mw:g} MW pumping, pump efficiency {eff:g} %, "
            f"initial {ini:g} MWh"
        )
        editor.remove_objects(set(gens) | {head} | tails)


def _rewrite_dsp_bids(
    db: _Db,
    res: _Resolver,
    editor: _XmlEditor,
    hours: list[tuple[datetime, int]],
    report: MaterialiseReport,
    csvs: dict[str, dict[str, list[float]]],
    new_rows: list[tuple[int, int, float, int]],
) -> None:
    """Region ``DSP Bid Quantity``/``Price`` bands -> price-bid generators.

    PLEXOS demand-side participation reduces load at the bid price; a
    generator with ``pmax`` = quantity and cost = price at the region's
    node is the same in a single-node region.
    """
    rcid = db.class_id.get("Region")
    try:
        nr_coll = db.find_collection("Node", "Region")
    except KeyError:
        return
    sys_oid = next(o for o, c in db.obj_class.items() if db.class_name[c] == "System")
    sys_gen = db.find_collection("System", "Generator")
    gen_node = db.find_collection("Generator", "Node")
    vom = db.find_property(sys_gen, "VO&M Charge")
    for reg, cid in sorted(db.obj_class.items()):
        if cid != rcid:
            continue
        rmid = res.sys_mem.get(reg)
        if rmid is None or not res.has(rmid, "DSP Bid Quantity"):
            continue
        nodes = [po for (c, po, co) in db.mem.values() if c == nr_coll and co == reg]
        if len(nodes) != 1:
            report.notes.append(f"DSP bids of region {db.obj_name[reg]!r} skipped")
            continue
        made = []
        for band in res.bands(rmid, "DSP Bid Quantity"):
            qty = [
                0.0 if v is None else v
                for v in res.series(rmid, "DSP Bid Quantity", hours, band)
            ]
            price = res.value(rmid, "DSP Bid Price", hours[0][0], hours[0][1], band)
            if not any(qty):
                continue
            name = f"DSP {db.obj_name[reg]} {band}"
            goid = editor.add_object("Generator", name)
            gmid = editor.add_membership(sys_gen, sys_oid, goid)
            editor.add_membership(gen_node, goid, nodes[0])
            new_rows.append((gmid, vom, 10000.0 if price is None else price, 1))
            csvs["Gen_Rating.csv"][name] = qty
            made.append(f"{name} ({max(qty):g} MW @ {price:g})")
        if made:
            report.rewrites.append("DSP bid bands -> generators: " + ", ".join(made))


def _log_report(report: MaterialiseReport) -> None:
    logger.info(
        "materialised PLEXOS model %r from %s for %d day(s), sample %d; scenarios: %s",
        report.model,
        report.start,
        report.n_days,
        report.sample,
        ", ".join(report.scenarios) or "(none)",
    )
    logger.info(
        "rows: %d kept (static at start), %d dropped by scenario, %d inactive",
        report.rows_kept,
        report.rows_dropped_scenario,
        report.rows_dropped_inactive,
    )
    for name, n in sorted(report.csv_written.items()):
        logger.info("  wrote %s (%d series)", name, n)
    for line in report.rewrites:
        logger.info("  rewrite: %s", line)
    for line in report.notes:
        logger.info("  note: %s", line)
    for key, n in sorted(report.frozen_at_start.items()):
        logger.warning(
            "  %s varies inside the horizon but is frozen at its start value "
            "(%d object(s))",
            key,
            n,
        )
    if report.not_converted:
        logger.warning(
            "PLEXOS properties with active data NOT converted (ignored): %s",
            ", ".join(f"{k} ({n})" for k, n in sorted(report.not_converted.items())),
        )


def parse_start_date(text: str) -> datetime:
    """Parse ``YYYY-MM-DD`` into a midnight :class:`datetime`."""
    return datetime.combine(date.fromisoformat(text), datetime.min.time())


def materialise_bundle_dir(
    xml_path: Path, opts: MaterialiseOptions
) -> tuple[tempfile.TemporaryDirectory[str], MaterialiseReport]:
    """Materialise into a fresh temporary directory (caller owns it)."""
    # The caller owns the directory (``PlexosBundle.close`` cleans it up).
    tmp = tempfile.TemporaryDirectory(  # pylint: disable=consider-using-with
        prefix="plexos2gtopt-model-"
    )
    try:
        report = materialise_model(xml_path, opts, Path(tmp.name))
    except Exception:
        tmp.cleanup()
        raise
    return tmp, report


__all__ = [
    "MaterialiseOptions",
    "MaterialiseReport",
    "MissingDataFileError",
    "find_data_file",
    "materialise_bundle_dir",
    "materialise_model",
    "parse_start_date",
    "pattern_is_month_only",
    "timeslice_matcher",
]
