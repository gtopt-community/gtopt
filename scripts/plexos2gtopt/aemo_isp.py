"""AEMO ISP PLEXOS model → gtopt **dispatch window** (prototype).

AEMO publishes the Integrated System Plan (ISP) capacity-outlook model
as one PLEXOS XML per scenario plus trace archives.  This module builds
a chronological, hourly gtopt dispatch case for a short window (default
one week) of one scenario, using the fleet that exists on the first day
of the window:

* Buses: the ISP sub-regions (PLEXOS ``Node``).
* Lines: the notional inter-sub-regional flow paths (``Line`` Max/Min
  Flow, timesliced + dated), as a transport model (no Kirchhoff, no
  losses).
* Generators: every ``Generator`` with ``Units > 0`` on the start day;
  hourly ``pmax = Units × Rating (trace or timesliced value, else Max
  Capacity) × (1 − Aux Incr)``; ``gcost = Heat Rate × Fuel Price +
  VO&M`` (legacy) or VO&M + ``fuel`` / ``heat_rate``.  Load-subtracter
  pseudo generators (used only for PLEXOS LDC fitting) are skipped.
* Batteries: every ``Battery`` with ``Units > 0`` (utility BESS,
  coordinated CER storage, V2G); SoC pinned to 50 % at both ends.
* Hydro: ``Storage`` → Reservoir + Junction, ``Waterway`` → Waterway,
  hydro generators with a ``Head Storage`` → Turbine (``Efficiency
  Incr`` MW per m³/s), daily / half-hourly natural inflows → Flow.
  Storage volumes are PLEXOS ML (1000 m³) → hm³.  End volume ≥ start
  volume.  Pumping is not modelled.
* Demand: sub-regional operational demand traces (``Node.Load``).
* Daily energy limits: ``Max Energy Day`` (DSP) and ``Max Energy
  Year`` (non-storage hydro) as ``daily_sum`` user constraints.
* ``Mark-up`` → added to ``gcost``; ``Min Load`` → per-block ``pmin``;
  ``Min Capacity Factor Year`` → window energy floor only with
  ``--long-periods prorate`` (see :data:`GENERATOR_PROPERTY_NOTES`;
  ``--legacy`` turns these off).

* PLEXOS ``Constraint`` objects, purchasers (electrolysers → hydrogen),
  emissions, gas-zone offtake and REZ buses: see
  :mod:`plexos2gtopt.aemo_isp_constraints` (``--legacy`` turns them all
  off and reproduces the earlier output).  With fuel offtake on (the
  default), thermal units carry ``fuel`` + ``heat_rate`` and ``gcost``
  is the VO&M only; the fuel cost comes from ``Fuel.price``.

What it does **not** do (see the scoping report): capacity expansion
(``Max Units Built``/``Build Cost``), capacity / build constraints,
line losses / MLFs, unit outages, reserves.

Run as ``python -m plexos2gtopt.aemo_isp --help``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import aemo_isp_constraints as isp_c
from .isp_traces import (
    TimesliceCalendar,
    TraceLocator,
    parse_month_pattern,
    read_interval_trace,
    read_yearly_column,
)
from .plexos_xml import PlexosDataRow, PlexosDb, load_xml

logger = logging.getLogger(__name__)

#: Generator categories that are not physical plant in a dispatch run.
SKIP_GENERATOR_CATEGORY_PREFIXES = ("Load Subtracters", "Network Augmentation")


# ---------------------------------------------------------------------------
# Property resolution
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    value: float
    date_from: datetime | None
    date_to: datetime | None
    timeslices: tuple[str, ...]
    data_file: str | None  # Data File object name
    other_tag: bool
    #: object ids of the non-Timeslice / non-Data-File tags (e.g. a
    #: ``Variable`` multiplying a constraint coefficient)
    others: tuple[int, ...] = ()


class IspResolver:
    """Resolve System-collection property values for a given day.

    Selection rule for (object, property, day), approximating PLEXOS:

    1. keep band-1 rows whose date window contains ``day`` and whose
       tags are only Timeslice / Data File objects;
    2. timeslice-tagged rows apply only while one of their timeslices is
       active; if any applies, they take precedence over untagged rows;
    3. within the winning group the latest ``date_from`` wins, and a
       Data File reference wins over a plain number at the same date.
    """

    def __init__(
        self,
        db: PlexosDb,
        locator: TraceLocator,
        calendar: TimesliceCalendar | None = None,
    ) -> None:
        self.db = db
        self.locator = locator
        self.obj = {o.object_id: o for o in db.objects}
        self.cls = db.classes_by_id
        self.cat = db.categories_by_id
        sys_cid = db.class_id("System")
        coll_by_id = {c.collection_id: c for c in db.collections}
        prop_by_id = {p.property_id: p for p in db.properties}
        mem_by_id = {m.membership_id: m for m in db.memberships}
        self._system_coll = {
            c.collection_id for c in db.collections if c.parent_class_id == sys_cid
        }
        self.rows: dict[tuple[int, str], list[_Row]] = defaultdict(list)
        self.coll_rows: dict[tuple[int, int, str], list[_Row]] = defaultdict(list)
        df_cid = db.class_id("Data File")
        ts_cid = db.class_id("Timeslice")
        for d in db.data_rows:
            if db.band_for_data.get(d.data_id, 1) != 1:
                continue
            prop = prop_by_id.get(d.property_id)
            mem = mem_by_id.get(d.membership_id)
            if prop is None or mem is None:
                continue
            row = self._make_row(d, df_cid, ts_cid)
            if prop.collection_id in self._system_coll:
                self.rows[(mem.child_object_id, prop.name)].append(row)
            else:
                coll = coll_by_id[prop.collection_id]
                self.coll_rows[
                    (
                        mem.parent_object_id,
                        mem.child_object_id,
                        f"{coll.name}::{prop.name}",
                    )
                ].append(row)
        # Data File object name → Filename text (``Traces\\kind\\x.csv``)
        self.filename: dict[str, str] = {}
        for d in db.data_rows:
            prop = prop_by_id.get(d.property_id)
            if prop is None or prop.name != "Filename":
                continue
            text = db.text_for_data.get(d.data_id)
            mem = mem_by_id.get(d.membership_id)
            if text and mem is not None:
                self.filename[self.obj[mem.child_object_id].name] = text
        self.missing_traces: set[str] = set()
        #: number of trace() calls answered with zeros (file missing)
        self.missing_hits = 0
        #: PLEXOS ``Filename`` texts actually read (for provenance)
        self.used_files: set[str] = set()
        self.calendar = calendar or self.build_calendar()

    def _make_row(
        self, d: PlexosDataRow, df_cid: int | None, ts_cid: int | None
    ) -> _Row:
        timeslices: list[str] = []
        data_file = None
        others: list[int] = []
        for oid in self.db.tag_for_data.get(d.data_id, []):
            o = self.obj.get(oid)
            if o is None:
                continue
            if o.class_id == ts_cid:
                timeslices.append(o.name)
            elif o.class_id == df_cid:
                data_file = o.name
            else:
                others.append(oid)
        return _Row(
            value=d.value,
            date_from=d.date_from,
            date_to=d.date_to,
            timeslices=tuple(timeslices),
            data_file=data_file,
            other_tag=bool(others),
            others=tuple(others),
        )

    # -- object helpers ------------------------------------------------------
    def objects(self, class_name: str) -> list[Any]:
        return self.db.objects_of_class(class_name)

    def category(self, obj: Any) -> str:
        return self.cat.get(obj.category_id, "") if obj.category_id is not None else ""

    def children(self, parent_class: str, collection: str) -> dict[int, list[int]]:
        """parent object id → child object ids for a named collection."""
        pcid = self.db.class_id(parent_class)
        for c in self.db.collections:
            if c.parent_class_id == pcid and c.name == collection:
                return self.db.parent_to_children(c.collection_id)
        return {}

    # -- timeslices ----------------------------------------------------------
    def build_calendar(self) -> TimesliceCalendar:
        cal = TimesliceCalendar()
        state_files: dict[str, list[str]] = defaultdict(list)
        ts_cid = self.db.class_id("Timeslice")
        prop_by_id = {p.property_id: p for p in self.db.properties}
        mem_by_id = {m.membership_id: m for m in self.db.memberships}
        for o in self.objects("Timeslice"):
            cal.add_month_name(o.name)
        for d in self.db.data_rows:
            prop = prop_by_id.get(d.property_id)
            mem = mem_by_id.get(d.membership_id)
            if prop is None or mem is None or prop.name != "Include":
                continue
            o = self.obj[mem.child_object_id]
            if o.class_id != ts_cid:
                continue
            text = self.db.text_for_data.get(d.data_id)
            if text:
                months = parse_month_pattern(text)
                if months:
                    cal.months[o.name] = months
            for tag in self.db.tag_for_data.get(d.data_id, []):
                t = self.obj.get(tag)
                if t is not None and self.cls.get(t.class_id) == "Data File":
                    state_files[t.name].append(o.name)
        for df_name, names in state_files.items():
            fname = self.filename.get(df_name)
            path = self.locator.resolve(fname) if fname else None
            if path is None:
                logger.warning("timeslice file for %s not found (%s)", df_name, fname)
                continue
            cal.add_state_csv(path, names)
            self.used_files.add(str(fname))
        return cal

    # -- value selection -----------------------------------------------------
    def _select(self, rows: list[_Row], day: date) -> _Row | None:
        t = datetime(day.year, day.month, day.day)
        live = [
            r
            for r in rows
            if not r.other_tag
            and (r.date_from is None or r.date_from <= t)
            and (r.date_to is None or r.date_to >= t)
        ]
        if not live:
            return None
        sliced = [
            r
            for r in live
            if r.timeslices
            and any(self.calendar.is_active(n, day) for n in r.timeslices)
        ]
        pool = sliced or [r for r in live if not r.timeslices]
        if not pool:
            return None
        return max(
            pool,
            key=lambda r: (r.date_from or datetime.min, r.data_file is not None),
        )

    def select_row(self, rows: list[_Row], day: date) -> _Row | None:
        """Public form of the row-selection rule (see the class docstring)."""
        return self._select(rows, day)

    def has(self, oid: int, prop: str) -> bool:
        return bool(self.rows.get((oid, prop)))

    def scalar(
        self, oid: int, prop: str, day: date, default: float | None = None
    ) -> float | None:
        """Plain value on ``day`` (a Data File reference yields ``default``)."""
        row = self._select(self.rows.get((oid, prop), []), day)
        if row is None or row.data_file is not None:
            return default
        return row.value

    def hourly(
        self, oid: int, prop: str, start: date, days: int, default: float | None = None
    ) -> np.ndarray | None:
        """Hourly values over the window, following Data File traces."""
        rows = self.rows.get((oid, prop), [])
        if not rows and default is None:
            return None
        out = np.empty(24 * days)
        found = False
        for k in range(days):
            day = start + timedelta(days=k)
            row = self._select(rows, day)
            sl = slice(24 * k, 24 * (k + 1))
            if row is None:
                if default is None:
                    out[sl] = np.nan
                else:
                    out[sl] = default
                continue
            found = True
            if row.data_file is not None:
                out[sl] = self.trace(row.data_file, day, column=self.obj[oid].name)
            else:
                out[sl] = row.value
        if not found and default is None:
            return None
        return out

    def trace(self, data_file: str, day: date, *, column: str) -> np.ndarray:
        """24 hourly values of ``data_file`` for ``day`` (zeros if missing)."""
        fname = self.filename.get(data_file)
        path = self.locator.resolve(fname) if fname else None
        if path is None:
            self.missing_hits += 1
            if data_file not in self.missing_traces:
                logger.warning(
                    "trace for Data File %r not found (%s)", data_file, fname
                )
                self.missing_traces.add(data_file)
            return np.zeros(24)
        self.used_files.add(str(fname))
        with open(path, encoding="utf-8-sig") as fh:
            header = fh.readline().strip().split(",")
        if (
            header
            and header[0].strip() == "Year"
            and len(header) > 1
            and header[1].strip() != "Month"
        ):
            val = read_yearly_column(path, day.year, column)
            return np.full(24, 0.0 if val is None else val)
        return read_interval_trace(path, day, 1)


# ---------------------------------------------------------------------------
# Case builder
# ---------------------------------------------------------------------------


@dataclass
class IspCaseOptions:
    """User options for :func:`build_dispatch_case`."""

    xml: Path
    trace_roots: list[Path]
    start: date
    days: int = 7
    name: str = "isp_week"
    include_hydro: bool = True
    include_batteries: bool = True
    demand_fail_cost: float | None = None
    #: PLEXOS constraints / purchasers / H2 / emissions / fuel offtake /
    #: REZ buses (see :mod:`plexos2gtopt.aemo_isp_constraints`)
    isp: isp_c.ConstraintOptions = field(default_factory=isp_c.ConstraintOptions)


@dataclass
class IspCase:
    """In-memory result: planning dict + per-field schedules."""

    planning: dict[str, Any]
    schedules: dict[tuple[str, str], dict[int, np.ndarray]] = field(
        default_factory=dict
    )
    stats: dict[str, Any] = field(default_factory=dict)


def _pct(v: float | None, default: float) -> float:
    return default if v is None else v / 100.0


def build_dispatch_case(opts: IspCaseOptions, db: PlexosDb | None = None) -> IspCase:
    """Build the gtopt dispatch case for ``opts``."""
    db = db or load_xml(opts.xml)
    roots = list(opts.trace_roots) or [Path(opts.xml).parent]
    res = IspResolver(db, TraceLocator(roots))
    day0, days = opts.start, opts.days
    nb = 24 * days
    sched: dict[tuple[str, str], dict[int, np.ndarray]] = defaultdict(dict)
    stats: dict[str, Any] = defaultdict(int)

    # ---- buses ------------------------------------------------------------
    nodes = sorted(res.objects("Node"), key=lambda o: o.name)
    bus_array = [{"uid": i + 1, "name": o.name} for i, o in enumerate(nodes)]
    node_name = {o.object_id: o.name for o in nodes}
    gen_node = _single_child(res, "Generator", "Nodes")
    bat_node = _single_child(res, "Battery", "Nodes")
    gen_fuel = _single_child(res, "Generator", "Fuels")
    head = _single_child(res, "Generator", "Head Storage")
    tail = _single_child(res, "Generator", "Tail Storage")

    # ---- demand -----------------------------------------------------------
    demand_array: list[dict[str, Any]] = []
    for o in nodes:
        load = res.hourly(o.object_id, "Load", day0, days)
        if load is None:
            continue
        uid = len(demand_array) + 1
        demand_array.append(
            {"uid": uid, "name": f"load_{o.name}", "bus": o.name, "lmax": "lmax"}
        )
        sched[("Demand", "lmax")][uid] = np.nan_to_num(load)
    stats["demand_energy_mwh"] = float(
        sum(v.sum() for v in sched[("Demand", "lmax")].values())
    )
    stats["demand_peak_mw"] = float(
        np.sum(list(sched[("Demand", "lmax")].values()), axis=0).max()
    )

    # ---- generators -------------------------------------------------------
    generator_array: list[dict[str, Any]] = []
    gen_info: dict[str, isp_c.GenInfo] = {}
    fuel_array: list[dict[str, Any]] = []
    fuel_price: dict[str, float] = {}
    turbine_gens: list[tuple[str, int, int | None, float]] = []
    user_constraints: list[dict[str, Any]] = []
    gx_stats: dict[str, Any] = defaultdict(int)
    for o in sorted(res.objects("Generator"), key=lambda o: o.name):
        cat = res.category(o)
        if cat.startswith(SKIP_GENERATOR_CATEGORY_PREFIXES):
            stats["gen_skipped_category"] += 1
            continue
        units = res.scalar(o.object_id, "Units", day0, default=0.0) or 0.0
        if units <= 0:
            stats["gen_skipped_no_units"] += 1
            continue
        bus = node_name.get(gen_node.get(o.object_id, -1))
        if bus is None:
            stats["gen_skipped_no_node"] += 1
            continue
        maxcap = res.hourly(o.object_id, "Max Capacity", day0, days)
        rating = res.hourly(o.object_id, "Rating", day0, days)
        cap = rating if rating is not None else maxcap
        if cap is None:
            logger.debug("generator %s: no Rating / Max Capacity, skipped", o.name)
            stats["gen_skipped_no_capacity"] += 1
            continue
        cap = np.nan_to_num(cap)
        if rating is not None and maxcap is not None:
            # Timesliced Rating rows can leave days uncovered: fall back to
            # Max Capacity there.
            gap = np.isnan(rating)
            cap = np.where(gap, np.nan_to_num(maxcap), np.nan_to_num(rating))
        rfac = res.hourly(o.object_id, "Rating Factor", day0, days)
        if rfac is not None:
            cap = cap * np.where(np.isnan(rfac), 100.0, rfac) / 100.0
        aux = _pct(res.scalar(o.object_id, "Aux Incr", day0), 0.0)
        pmax = np.clip(units * cap * (1.0 - aux), 0.0, None)
        vom = res.scalar(o.object_id, "VO&M Charge", day0, default=0.0) or 0.0
        if opts.isp.generator_extras:
            vom += _generator_extras(res, o, day0, gx_stats)
        hr = res.scalar(o.object_id, "Heat Rate", day0)
        fuel_cost = 0.0
        fuel_oid = gen_fuel.get(o.object_id)
        price = 0.0
        if hr is not None and fuel_oid is not None:
            price = res.scalar(fuel_oid, "Price", day0, default=0.0) or 0.0
            fuel_cost = hr * price
        uid = len(generator_array) + 1
        gtype = _gen_type(cat, o.name)
        item: dict[str, Any] = {
            "uid": uid,
            "name": o.name,
            "bus": bus,
            "type": gtype,
            "gcost": round(fuel_cost + vom, 6),
            "pmax": "pmax",
            "capacity": float(np.max(pmax)) if pmax.size else 0.0,
        }
        fname = res.obj[fuel_oid].name if fuel_oid is not None else None
        if opts.isp.fuels and hr is not None and hr > 0 and fname is not None:
            # native fuel form: gcost = VO&M, fuel cost = heat_rate × price
            item["gcost"] = round(vom, 6)
            item["fuel"] = fname
            item["heat_rate"] = hr
            if fname not in fuel_price:
                fuel_price[fname] = price
                fuel_array.append(
                    {"uid": len(fuel_array) + 1, "name": fname, "price": price}
                )
        gen_info[o.name] = isp_c.GenInfo(
            name=o.name,
            bus=bus,
            aux=aux,
            heat_rate=hr if hr is not None and hr > 0 else None,
            fuel=fname,
        )
        generator_array.append(item)
        sched[("Generator", "pmax")][uid] = pmax
        if opts.isp.generator_extras:
            pmin = _min_load(res, o, day0, days, pmax, gx_stats)
            if pmin is not None:
                item["pmin"] = "pmin"
                sched[("Generator", "pmin")][uid] = pmin
            uc = _min_cf_year_uc(res, o, day0, nb, units, aux, pmax, opts, gx_stats)
            if uc is not None:
                user_constraints.append(uc)
        stats[f"gen_{gtype}"] += 1
        stats[f"cap_{gtype}_mw"] += float(np.max(pmax))
        h = head.get(o.object_id)
        if opts.include_hydro and h is not None:
            eff = res.scalar(o.object_id, "Efficiency Incr", day0)
            if eff and eff > 0:
                turbine_gens.append((o.name, h, tail.get(o.object_id), eff))
        # daily energy limits
        e_day = res.scalar(o.object_id, "Max Energy Day", day0)
        if e_day is not None and e_day > 0:
            user_constraints.append(
                _daily_energy_uc(o.name, e_day * 1000.0, "Max Energy Day")
            )
        if h is None and res.has(o.object_id, "Max Energy Year"):
            ey = res.hourly(o.object_id, "Max Energy Year", day0, 1)
            if ey is not None and float(ey[0]) > 0:
                user_constraints.append(
                    _daily_energy_uc(
                        o.name, float(ey[0]) * 1000.0 / 365.0, "Max Energy Year"
                    )
                )

    if opts.isp.generator_extras:
        stats["generator_properties"] = {
            **dict(sorted(gx_stats.items())),
            "approximations": GENERATOR_PROPERTY_NOTES,
        }

    # ---- batteries --------------------------------------------------------
    battery_array: list[dict[str, Any]] = []
    if opts.include_batteries:
        for o in sorted(res.objects("Battery"), key=lambda o: o.name):
            units = res.scalar(o.object_id, "Units", day0, default=0.0) or 0.0
            bus = node_name.get(bat_node.get(o.object_id, -1))
            if units <= 0 or bus is None:
                continue
            cap_mwh = res.scalar(o.object_id, "Capacity", day0, default=0.0) or 0.0
            pmax_raw = res.hourly(o.object_id, "Max Power", day0, days)
            if cap_mwh <= 0 or pmax_raw is None:
                continue
            pmax = units * np.nan_to_num(pmax_raw)
            if pmax.max() <= 0:
                continue
            emax = units * cap_mwh * _pct(res.scalar(o.object_id, "Max SoC", day0), 1.0)
            emin = units * cap_mwh * _pct(res.scalar(o.object_id, "Min SoC", day0), 0.0)
            ceff = _pct(res.scalar(o.object_id, "Charge Efficiency", day0), 1.0)
            deff = _pct(res.scalar(o.object_id, "Discharge Efficiency", day0), 1.0)
            mid = 0.5 * (emin + emax)
            uid = len(battery_array) + 1
            battery_array.append(
                {
                    "uid": uid,
                    "name": o.name,
                    "bus": bus,
                    "emin": round(emin, 6),
                    "emax": round(emax, 6),
                    "eini": round(mid, 6),
                    "efin": round(mid, 6),
                    # gtopt expands a Battery into an auxiliary charge
                    # Demand / discharge Generator, so a string file
                    # reference would be resolved against those classes:
                    # use the window maximum (Max Power is timesliced but
                    # rarely changes inside a dispatch week).
                    "pmax_charge": round(float(pmax.max()), 6),
                    "pmax_discharge": round(float(pmax.max()), 6),
                    "input_efficiency": ceff,
                    "output_efficiency": deff,
                }
            )
            stats["battery_mw"] += float(pmax.max())
            stats["battery_mwh"] += emax

    # ---- lines ------------------------------------------------------------
    line_array: list[dict[str, Any]] = []
    line_from = _single_child(res, "Line", "Node From")
    line_to = _single_child(res, "Line", "Node To")
    for o in sorted(res.objects("Line"), key=lambda o: o.name):
        units = res.scalar(o.object_id, "Units", day0, default=1.0) or 0.0
        a = node_name.get(line_from.get(o.object_id, -1))
        b = node_name.get(line_to.get(o.object_id, -1))
        if units <= 0 or a is None or b is None:
            stats["line_skipped"] += 1
            continue
        fmax = res.hourly(o.object_id, "Max Flow", day0, days)
        fmin = res.hourly(o.object_id, "Min Flow", day0, days)
        if fmax is None or fmin is None:
            stats["line_skipped"] += 1
            continue
        fmax = _fill_nan_from_day_neighbours(fmax)
        fmin = _fill_nan_from_day_neighbours(fmin)
        uid = len(line_array) + 1
        line_array.append(
            {
                "uid": uid,
                "name": o.name,
                "bus_a": a,
                "bus_b": b,
                "tmax_ab": "tmax_ab",
                "tmax_ba": "tmax_ba",
            }
        )
        sched[("Line", "tmax_ab")][uid] = np.clip(fmax, 0.0, None)
        sched[("Line", "tmax_ba")][uid] = np.clip(-fmin, 0.0, None)

    # ---- hydro ------------------------------------------------------------
    hydro: dict[str, list[dict[str, Any]]] = {}
    if opts.include_hydro:
        hydro = _build_hydro(res, opts, turbine_gens, sched, stats)

    # ---- VoLL / options ---------------------------------------------------
    voll = opts.demand_fail_cost
    if voll is None:
        vals = [
            v
            for r in res.objects("Region")
            if (v := res.scalar(r.object_id, "VoLL", day0))
        ]
        voll = max(vals) if vals else 15000.0

    system: dict[str, Any] = {
        "name": opts.name,
        "bus_array": bus_array,
        "demand_array": demand_array,
        "generator_array": generator_array,
        "line_array": line_array,
    }
    if battery_array:
        system["battery_array"] = battery_array
    if fuel_array:
        system["fuel_array"] = fuel_array
    for key, arr in hydro.items():
        if arr:
            system[key] = arr
    isp = opts.isp
    if isp.constraints or isp.purchasers or isp.emissions or isp.fuels:
        view = isp_c.CaseView(
            res=res,
            day0=day0,
            days=days,
            gens=gen_info,
            batteries={b["name"]: b["bus"] for b in battery_array},
            lines={ln["name"] for ln in line_array},
            node_load={
                d["bus"]: sched[("Demand", "lmax")][d["uid"]] for d in demand_array
            },
            system=system,
            sched=sched,
        )
        user_constraints.extend(isp_c.apply(view, isp, stats))
    if user_constraints:
        for i, uc in enumerate(user_constraints, start=1):
            uc["uid"] = i
            rhs = uc.pop("_rhs", None)
            if rhs is not None:
                sched[("UserConstraint", "rhs")][i] = np.asarray(rhs, dtype=float)
        system["user_constraint_array"] = user_constraints

    planning = {
        "options": {
            "input_format": "parquet",
            "output_format": "parquet",
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": False,
                "demand_fail_cost": voll,
                "scale_objective": 1000.0,
            },
        },
        "simulation": {
            "block_array": [{"uid": i + 1, "duration": 1.0} for i in range(nb)],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": nb, "chronological": True}
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1.0}],
        },
        "system": system,
    }
    stats.update(
        {
            "start": day0.isoformat(),
            "days": days,
            "blocks": nb,
            "buses": len(system["bus_array"]),
            "generators": len(generator_array),
            "batteries": len(battery_array),
            "lines": len(system["line_array"]),
            "demands": len(system["demand_array"]),
            "user_constraints": len(user_constraints),
            "missing_traces": sorted(res.missing_traces),
            "trace_files": sorted(res.used_files),
            "voll": voll,
        }
    )
    for key, arr in hydro.items():
        stats[key.replace("_array", "s")] = len(arr)
    return IspCase(planning=planning, schedules=dict(sched), stats=dict(stats))


#: How the Generator properties outside the core dispatch fields are read.
GENERATOR_PROPERTY_NOTES = [
    "Mark-up [$/MWh] is added to gcost (rooftop PV −1 $/MWh: dispatched "
    "before utility VRE is curtailed)",
    "Min Load [MW, timesliced] is a per-block pmin, capped at pmax",
    "Min Capacity Factor Year [%] is a yearly energy floor: not enforced by "
    "default (long-period policy); --long-periods prorate enforces "
    "CF × Units × Max Capacity × (1 − Aux Incr) × window hours, capped at "
    "the window's Σ pmax",
    "Min Capacity Factor (no period) is not mapped (only a zero value in the XML)",
    "Outage Factor (bands 2-3, partial forced outages) and Maintenance Rate "
    "(new entrants) are not modelled: no outages in a deterministic week",
]


def _generator_extras(
    res: IspResolver, o: Any, day0: date, gx: dict[str, Any]
) -> float:
    """Mark-up [$/MWh] for ``o`` on ``day0`` and the unmapped-property counts."""
    for prop in ("Outage Factor", "Maintenance Rate", "Min Capacity Factor"):
        if res.has(o.object_id, prop):
            gx[f"not modelled: {prop}"] += 1
    markup = res.scalar(o.object_id, "Mark-up", day0)
    if markup is None or markup == 0.0:
        return 0.0
    gx["Mark-up → gcost"] += 1
    return float(markup)


def _min_load(
    res: IspResolver,
    o: Any,
    day0: date,
    days: int,
    pmax: np.ndarray,
    gx: dict[str, Any],
) -> np.ndarray | None:
    """PLEXOS ``Min Load`` [MW] (timesliced) as a per-block pmin ≤ pmax."""
    if not res.has(o.object_id, "Min Load"):
        return None
    ml = res.hourly(o.object_id, "Min Load", day0, days)
    if ml is None or not np.any(np.nan_to_num(ml) > 0):
        return None
    gx["Min Load → pmin"] += 1
    return np.minimum(np.nan_to_num(ml), pmax)


def _min_cf_year_uc(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    res: IspResolver,
    o: Any,
    day0: date,
    nb: int,
    units: float,
    aux: float,
    pmax: np.ndarray,
    opts: IspCaseOptions,
    gx: dict[str, Any],
) -> dict[str, Any] | None:
    """``Min Capacity Factor Year`` → window energy floor (prorate only)."""
    cf = res.scalar(o.object_id, "Min Capacity Factor Year", day0)
    if cf is None or cf <= 0:
        return None
    if opts.isp.long_periods != "prorate":
        gx["Min Capacity Factor Year: not enforced (long period)"] += 1
        return None
    maxcap = res.scalar(o.object_id, "Max Capacity", day0)
    if maxcap is None or maxcap <= 0:
        maxcap = float(np.max(pmax)) / max(units * (1.0 - aux), _TINY)
    floor = cf / 100.0 * units * maxcap * (1.0 - aux) * nb
    floor = min(floor, float(np.sum(pmax)))
    gx["Min Capacity Factor Year → window energy floor"] += 1
    return {
        "name": f"min_cf_year_{_slug(o.name)}",
        "expression": f'sum{{b in stage}} dur[b] * (generator("{o.name}")'
        f".generation) >= {floor:.6g}",
        "scope": "stage",
        "constraint_type": "energy",
        "description": f"PLEXOS Min Capacity Factor Year {cf:g} % pro-rated to "
        "the window [MWh] — APPROXIMATION",
    }


_TINY = 1e-9


def _daily_energy_uc(gen: str, mwh_per_day: float, source: str) -> dict[str, Any]:
    return {
        "name": f"energy_day_{_slug(gen)}",
        "expression": f'generator("{gen}").generation <= {mwh_per_day:.6g}',
        "daily_sum": True,
        "constraint_type": "energy",
        "description": f"PLEXOS {source} → daily energy budget [MWh/day]",
    }


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def _gen_type(category: str, name: str) -> str:
    c = category.lower()
    for key, t in (
        ("solar", "solar"),
        ("rooftop", "solar"),
        ("wind", "wind"),
        ("hydro", "hydro"),
        ("phes", "hydro"),
        ("black coal", "coal"),
        ("brown coal", "coal"),
        ("natural gas", "gas"),
        ("liquid fuel", "liquid"),
        ("biomass", "biomass"),
        ("dsp", "dsp"),
    ):
        if key in c:
            return t
    n = name.upper()
    if n.endswith(("SF1", "SF2", "_SAT", "_FFP")):
        return "solar"
    if "WF" in n:
        return "wind"
    return "other"


def _single_child(
    res: IspResolver, parent_class: str, collection: str
) -> dict[int, int]:
    return {p: ch[0] for p, ch in res.children(parent_class, collection).items() if ch}


def _fill_nan_from_day_neighbours(arr: np.ndarray) -> np.ndarray:
    """Fill uncovered (NaN) hours with the nearest covered value."""
    if not np.isnan(arr).any():
        return arr
    s = pd.Series(arr).ffill().bfill()
    return s.fillna(0.0).to_numpy()


def _build_hydro(
    res: IspResolver,
    opts: IspCaseOptions,
    turbine_gens: list[tuple[str, int, int | None, float]],
    sched: dict[tuple[str, str], dict[int, np.ndarray]],
    stats: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    day0, days = opts.start, opts.days
    storages = sorted(res.objects("Storage"), key=lambda o: o.name)
    sname = {o.object_id: o.name for o in storages}
    junction_array = [
        {"uid": i + 1, "name": o.name, "drain": True} for i, o in enumerate(storages)
    ]
    reservoir_array: list[dict[str, Any]] = []
    flow_array: list[dict[str, Any]] = []
    for o in storages:
        vmax = (
            res.scalar(o.object_id, "Max Volume", day0, default=0.0) or 0.0
        ) / 1000.0
        vmin = (
            res.scalar(o.object_id, "Min Volume", day0, default=0.0) or 0.0
        ) / 1000.0
        v0 = res.scalar(o.object_id, "Initial Volume", day0)
        v0 = vmax * 0.5 if v0 is None else v0 / 1000.0
        v0 = min(max(v0, vmin), vmax)
        reservoir_array.append(
            {
                "uid": len(reservoir_array) + 1,
                "name": o.name,
                "junction": o.name,
                "emin": round(vmin, 6),
                "emax": round(vmax, 6),
                "eini": round(v0, 6),
                "efin": round(v0, 6),
                "efin_cost": 1.0e4,
            }
        )
        inflow = res.hourly(o.object_id, "Natural Inflow", day0, days)
        if inflow is not None and np.nansum(inflow) > 0:
            uid = len(flow_array) + 1
            flow_array.append(
                {
                    "uid": uid,
                    "name": f"inflow_{_slug(o.name)}",
                    "junction": o.name,
                    "discharge": "discharge",
                }
            )
            sched[("Flow", "discharge")][uid] = np.nan_to_num(inflow)
    wfrom = _single_child(res, "Waterway", "Storage From")
    wto = _single_child(res, "Waterway", "Storage To")
    waterway_array: list[dict[str, Any]] = []
    for o in sorted(res.objects("Waterway"), key=lambda o: o.name):
        a = sname.get(wfrom.get(o.object_id, -1))
        if a is None:
            continue
        b = sname.get(wto.get(o.object_id, -1))
        item: dict[str, Any] = {
            "uid": len(waterway_array) + 1,
            "name": o.name,
            "junction_a": a,
        }
        if b is not None:
            item["junction_b"] = b
        fmax = res.scalar(o.object_id, "Max Flow", day0)
        if fmax is not None and fmax < 1e20:
            item["fmax"] = fmax
        waterway_array.append(item)
    turbine_array: list[dict[str, Any]] = []
    for gname, h, t, eff in turbine_gens:
        item = {
            "uid": len(turbine_array) + 1,
            "name": f"turbine_{_slug(gname)}",
            "generator": gname,
            "junction_a": sname[h],
            "production_factor": eff,
            "main_reservoir": sname[h],
        }
        if t is not None and t in sname:
            item["junction_b"] = sname[t]
        else:
            item["drain"] = True
        turbine_array.append(item)
    stats["hydro_storage_hm3"] = float(sum(r["emax"] for r in reservoir_array))
    return {
        "junction_array": junction_array,
        "reservoir_array": reservoir_array,
        "waterway_array": waterway_array,
        "turbine_array": turbine_array,
        "flow_array": flow_array,
    }


# ---------------------------------------------------------------------------
# Writer + CLI
# ---------------------------------------------------------------------------


def write_case(case: IspCase, out_dir: Path, name: str) -> Path:
    """Write ``<out_dir>/<name>.json`` and the Parquet field schedules."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for (cls, fld), cols in case.schedules.items():
        if not cols:
            continue
        nb = len(next(iter(cols.values())))
        frames = []
        for uid, arr in sorted(cols.items()):
            frames.append(
                pd.DataFrame(
                    {
                        "stage": np.ones(nb, dtype=np.int32),
                        "block": np.arange(1, nb + 1, dtype=np.int32),
                        "uid": np.full(nb, uid, dtype=np.int32),
                        "value": np.round(np.asarray(arr, dtype=np.float64), 6),
                    }
                )
            )
        if cls == "Flow":
            for f in frames:
                f.insert(0, "scenario", np.ones(len(f), dtype=np.int32))
        d = out_dir / cls
        d.mkdir(parents=True, exist_ok=True)
        pd.concat(frames, ignore_index=True).to_parquet(
            d / f"{fld}.parquet", index=False
        )
    path = out_dir / f"{name}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(case.planning, fh, indent=1, allow_nan=False)
        fh.write("\n")
    return path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m plexos2gtopt.aemo_isp",
        description="Build an hourly gtopt dispatch case from an AEMO ISP PLEXOS XML.",
    )
    p.add_argument(
        "xml", type=Path, help="scenario XML (e.g. '2026 ISP Step Change Model.xml')"
    )
    p.add_argument(
        "-t",
        "--trace-root",
        type=Path,
        action="append",
        default=[],
        help="directory searched for Traces\\<kind>\\<file>.csv (repeatable; "
        "the XML's folder is always searched first)",
    )
    p.add_argument("-s", "--start", required=True, help="first day, YYYY-MM-DD")
    p.add_argument("-d", "--days", type=int, default=7)
    p.add_argument("-o", "--output-dir", type=Path, required=True)
    p.add_argument("-n", "--name", default=None, help="case name (JSON stem)")
    p.add_argument(
        "--no-hydro", action="store_true", help="hydro generators without water balance"
    )
    p.add_argument("--no-batteries", action="store_true")
    p.add_argument("--demand-fail-cost", type=float, default=None)
    g = p.add_argument_group(
        "PLEXOS constraints, purchasers, hydrogen, emissions (default: all on)"
    )
    g.add_argument(
        "--no-isp-constraints",
        action="store_true",
        help="do not convert PLEXOS Constraint objects (REZ / group / special "
        "limits); H2 consumption is still governed by --no-purchasers",
    )
    g.add_argument(
        "--no-purchasers",
        action="store_true",
        help="no PLEXOS purchasers (electrolysers, fixed industrial loads) and "
        "no H2 consumption constraints",
    )
    g.add_argument(
        "--no-emissions", action="store_true", help="no emission accounting / budgets"
    )
    g.add_argument(
        "--no-fuel-offtake",
        action="store_true",
        help="keep fuel cost folded into gcost; no gas-zone offtake limits",
    )
    g.add_argument(
        "--no-rez-buses",
        action="store_true",
        help="keep REZ export/import limits as user constraints instead of "
        "REZ buses with radial lines",
    )
    g.add_argument(
        "--legacy",
        action="store_true",
        help="all of the above off: reproduces the pre-constraint converter output",
    )
    g.add_argument(
        "--h2-model",
        choices=("native", "demand"),
        default="native",
        help="native: electrolyser CarrierConverter -> HydrogenNode -> "
        "HydrogenDemand (needs a gtopt with HydrogenDemand); demand: "
        "electrolysers as Demand + window-sum user constraint (PLEXOS form)",
    )
    g.add_argument(
        "--long-periods",
        choices=("skip", "prorate"),
        default="skip",
        help="period RHS longer than one month (RHS Year / RHS Custom policy "
        "targets, emission budgets): skip = not enforced (budgets tracked "
        "without cap), prorate = enforce the share pro-rated to the window",
    )
    g.add_argument(
        "--electrolyser-kwh-per-kg",
        type=float,
        default=isp_c.DEFAULT_ELECTROLYSER_KWH_PER_KG,
        help="electrolyser specific consumption [kWh_e/kg H2] used for the H2 "
        "efficiency (default: IASR 2026 PEM 2026-27, %(default)s)",
    )
    p.add_argument("-l", "--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    start = date.fromisoformat(args.start)
    name = args.name or f"isp_{start.strftime('%Y%m%d')}_{args.days}d"
    opts = IspCaseOptions(
        xml=args.xml,
        trace_roots=[args.xml.parent, *args.trace_root],
        start=start,
        days=args.days,
        name=name,
        include_hydro=not args.no_hydro,
        include_batteries=not args.no_batteries,
        demand_fail_cost=args.demand_fail_cost,
        isp=isp_c.ConstraintOptions(
            constraints=not (args.no_isp_constraints or args.legacy),
            purchasers=not (args.no_purchasers or args.legacy),
            emissions=not (args.no_emissions or args.legacy),
            fuels=not (args.no_fuel_offtake or args.legacy),
            rez_buses=not (args.no_rez_buses or args.legacy),
            h2_model=args.h2_model,
            long_periods=args.long_periods,
            electrolyser_kwh_per_kg=args.electrolyser_kwh_per_kg,
            generator_extras=not args.legacy,
        ),
    )
    case = build_dispatch_case(opts)
    path = write_case(case, args.output_dir, name)
    stats_path = args.output_dir / f"{name}.stats.json"
    stats_path.write_text(
        json.dumps(case.stats, indent=1, sort_keys=True, default=_json_default) + "\n"
    )
    logger.info("wrote %s", path)
    logger.info(
        "stats: %s", json.dumps(case.stats, sort_keys=True, default=_json_default)
    )
    return 0


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, float) and math.isnan(o):
        return None
    raise TypeError(type(o))


if __name__ == "__main__":
    raise SystemExit(main())
