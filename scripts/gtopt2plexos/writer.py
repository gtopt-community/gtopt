# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a gtopt planning as a PLEXOS dataset (``plexos2gtopt`` dialect).

The inverse of :mod:`plexos2gtopt`.  The output directory is an
uncompressed PLEXOS bundle -- the simplest input
:func:`plexos2gtopt.plexos_loader.locate_bundle` accepts:

* ``DBSEN_PRGDIARIO.xml`` -- the MasterDataSet object database (classes,
  objects, memberships and static ``t_data`` properties);
* per-class Data Files at the root, in the CEN layouts
  :mod:`plexos2gtopt.plexos_csv` reads: long ``NAME, YEAR, MONTH, DAY,
  PERIOD, BAND, VALUE`` (``Gen_Rating``, ``Lin_MaxRating``,
  ``Lin_MinRating``, ``BESS_IniValue``) and wide ``YEAR, MONTH, DAY,
  PERIOD, <object>...`` (``Nod_Load``, ``Hydro_WaterFlows``,
  ``Hydro_MaxVolume``, ``Hydro_MinVolume``);
* ``block_layout.csv`` -- the gtopt blocks as runs of hourly intervals,
  passed to ``plexos2gtopt --block-layout``;
* ``gtopt2plexos.json`` -- name map, read-back flags and warnings.

Time: PLEXOS (as plexos2gtopt reads it) is one scenario and one
chronological stage of hourly intervals.  Every gtopt block becomes
``duration`` consecutive hours holding the block's value, and the
read-back ``--block-layout`` regroups them into the same blocks (their
mean is the block value).  Stages are concatenated; only scenario 1 is
written.

Mapping (gtopt -> PLEXOS class / property):

* Bus -> ``Node`` (``Voltage``), in bus-uid order (plexos2gtopt
  numbers buses 1..n in node order);
* Demand -> ``Nod_Load.csv`` column of its bus; ``demand_fail_cost`` /
  ``fcost`` -> ``Region.VoLL`` (one Region per distinct fail cost);
* Generator -> ``Generator`` + ``Nodes`` membership; effective
  availability ``min(pmax, profile x capacity)`` -> ``Gen_Rating.csv``,
  ``gcost`` -> ``VO&M Charge``;
* Line -> ``Line`` + ``Node From`` / ``Node To``; ``tmax_ab`` / ``-tmax_ba``
  -> ``Max Flow`` / ``Min Flow`` (or ``Lin_Max/MinRating.csv`` when they
  vary); reactance / resistance in per unit on 100 MVA
  (``X_pu = 100 X / V^2``), ``Enforce Limits = 2``;
* Battery -> ``Battery`` + ``Nodes``: ``Capacity``, ``Max Power``
  (discharge), ``Max Load`` (charge), ``Charge/Discharge Efficiency`` [%],
  ``eini`` -> ``BESS_IniValue.csv`` [% of capacity];
* Reservoir -> ``Storage`` (volumes in CMD: ``x 1 / (24 fcr)``), its
  turbine's generator -> ``Head Storage`` (``Tail Storage`` when the
  turbine outlet is another reservoir), ``production_factor`` ->
  ``Efficiency Incr``, inflows -> ``Hydro_WaterFlows.csv`` [m3/s].

What the reader cannot carry is reported in :attr:`PlexosWriter.warnings`.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.case_reader import GtoptCase
from gtopt2plexos.xmldb import MasterDataSet, num_text

logger = logging.getLogger(__name__)

XML_NAME = "DBSEN_PRGDIARIO.xml"
BLOCK_LAYOUT = "block_layout.csv"
META_NAME = "gtopt2plexos.json"

#: Rating written for a line / unit gtopt leaves unbounded (the reader
#: drops PLEXOS' 1e30 "no limit" sentinel, and a missing rating drops
#: the line).
NO_LIMIT = 99999.0
#: ``Max Cycles Day`` no reachable battery attains (plexos2gtopt then
#: emits no cycle row but a chronological battery).
NO_CYCLE_LIMIT = 1.0e9
#: gtopt's default battery self-discharge [p.u./year] when a battery and
#: ``model_options.battery_annual_standing_loss`` leave it unset.
BATTERY_LOSS = 0.48
HOURS_PER_YEAR = 8760.0
#: PLEXOS impedances are per unit on this base (plexos2gtopt writes
#: ``voltage = sqrt(S_BASE)``).
S_BASE_MVA = 100.0
#: gtopt's default ``Reservoir.flow_conversion_rate`` (hm3 per m3/s per h).
DEFAULT_FCR = 0.0036
#: plexos2gtopt's ``flow_conversion_rate`` (CMD per m3/s per h).
PLEXOS_FCR = 1.0 / 24.0

_IGNORED_ARRAYS = (
    "converter_array",
    "reserve_zone_array",
    "reserve_provision_array",
    "user_constraint_array",
    "flow_right_array",
    "volume_right_array",
    "emission_zone_array",
    "fuel_array",
    "commitment_array",
    "reservoir_seepage_array",
    "lng_terminal_array",
    "inertia_zone_array",
    "future_cost_array",
)


def sanitize(name: Any) -> str:
    """A PLEXOS object name safe in CSV headers (no commas / quotes)."""
    return re.sub(r"[,\"\r\n;]+", "_", str(name)).strip() or "X"


def block_hours(durations: tuple[float, ...]) -> list[int]:
    """Whole hours of every block; ``ValueError`` when one is not whole."""
    out: list[int] = []
    for k, dur in enumerate(durations):
        n = round(dur)
        if n < 1 or not math.isclose(dur, n, abs_tol=1e-9):
            raise ValueError(
                f"block {k + 1} lasts {dur} h: PLEXOS intervals are hourly, so "
                "every block duration must be a whole number of hours"
            )
        out.append(int(n))
    return out


@dataclass
class PlexosWriter:
    """Export a :class:`GtoptCase` as a PLEXOS bundle directory."""

    case: GtoptCase
    out_dir: Path
    start_date: date = date(2030, 1, 1)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Declare the state :meth:`write` fills."""
        self.db = MasterDataSet()
        self.hours: list[int] = []
        self.n_days = 0
        self.names: dict[str, dict[str, str]] = {}
        self._used: dict[str, set[str]] = {}
        self.bus_node: dict[int, str] = {}
        self._bus_ref: dict[Any, dict[str, Any]] = {}
        self.long_rows: dict[str, list[tuple[str, np.ndarray]]] = {}
        self.wide_cols: dict[str, dict[str, np.ndarray]] = {}
        self.volume_factor: dict[str, float] = {}
        self._gen_node: dict[str, str] = {}

    # ── helpers ────────────────────────────────────────────────────────
    def warn(self, msg: str) -> None:
        """Record and log a lossy-conversion warning (once per text)."""
        if msg not in self.warnings:
            self.warnings.append(msg)
            logger.warning("%s", msg)

    def _name(self, cls: str, elem: dict[str, Any], fallback: str) -> str:
        """Unique PLEXOS name of ``elem`` in ``cls`` (recorded in the map)."""
        used = self._used.setdefault(cls, set())
        base = sanitize(elem.get("name", fallback))
        out, k = base, 1
        while out in used:
            k += 1
            out = f"{base}_{k}"
        used.add(out)
        self.names.setdefault(cls, {})[str(elem.get("name", fallback))] = out
        return out

    def _first(self, arr: np.ndarray, what: str) -> np.ndarray:
        """Scenario-1 row of ``arr``; warns when other scenarios differ."""
        if arr.shape[0] > 1 and not np.allclose(arr, arr[0], equal_nan=True):
            self.warn(f"{what} varies by scenario; PLEXOS keeps scenario 1 only")
        return np.asarray(arr[0], dtype=float)

    def _field(
        self, cls: str, elem: dict[str, Any], name: str, default: float
    ) -> np.ndarray:
        what = f"{cls.lower()} {elem.get('name')}: {name}"
        return self._first(self.case.field(cls, elem, name, default=default), what)

    def _hourly(self, per_block: np.ndarray) -> np.ndarray:
        """Spread a per-block series over the hours (padded to whole days)."""
        out = np.repeat(np.asarray(per_block, dtype=float), self.hours)
        pad = self.n_days * 24 - out.size
        return np.concatenate([out, np.full(pad, out[-1] if out.size else 0.0)])

    def _long(self, csv_name: str, obj: str, per_block: np.ndarray) -> None:
        self.long_rows.setdefault(csv_name, []).append((obj, self._hourly(per_block)))

    def _wide(self, csv_name: str, obj: str, per_block: np.ndarray) -> None:
        cols = self.wide_cols.setdefault(csv_name, {})
        series = self._hourly(per_block)
        cols[obj] = cols[obj] + series if obj in cols else series

    def node_of(self, ref: Any) -> str | None:
        """PLEXOS Node of a gtopt bus reference (uid or name)."""
        bus = self._bus_ref.get(ref)
        return None if bus is None else self.bus_node.get(int(bus["uid"]))

    @staticmethod
    def _varies(arr: np.ndarray) -> bool:
        return bool(arr.size) and not np.allclose(arr, arr[0])

    # ── driver ─────────────────────────────────────────────────────────
    def build(self) -> None:
        """Map the case to PLEXOS objects and data series (no file written)."""
        g = self.case.grid
        self.hours = block_hours(g.durations)
        self.n_days = max(1, math.ceil(sum(self.hours) / 24))
        self._check_time()
        self._buses()
        self._demands()
        self._generators()
        self._lines()
        self._batteries()
        self._hydro()
        for array in _IGNORED_ARRAYS:
            if self.case.elements(array):
                self.warn(f"{array} is not exported (no PLEXOS object written)")
        if not self.db.objects_of("Generator") and not self.db.objects_of("Battery"):
            raise ValueError("the case has no generator or battery to export")

    def write(self) -> Path:
        """Write every file; returns the output directory."""
        self.build()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db.write(self.out_dir / XML_NAME)
        self._write_csvs()
        g = self.case.grid
        with (self.out_dir / BLOCK_LAYOUT).open("w", encoding="utf-8") as fh:
            fh.write("block_uid,duration\n")
            for uid, h in zip(g.block_uids, self.hours, strict=True):
                fh.write(f"{uid},{h}\n")
        meta = {
            "source": "gtopt2plexos",
            "start_date": self.start_date.isoformat(),
            "hours": sum(self.hours),
            "names": self.names,
            "volume_factor": self.volume_factor,
            "plexos2gtopt_flags": self.plexos2gtopt_flags(),
            "warnings": self.warnings,
        }
        (self.out_dir / META_NAME).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return self.out_dir

    def plexos2gtopt_flags(self) -> list[str]:
        """Flags for ``plexos2gtopt`` that restore the block structure."""
        flags = [
            "--block-layout",
            str(self.out_dir / BLOCK_LAYOUT),
            "--line-losses-mode",
            "tangent_signed_flow" if self._has_losses() else "none",
        ]
        if self.case.elements("reservoir_array"):
            # plexos2gtopt otherwise adds estimated reservoir fmin/fmax bounds.
            flags.append("--no-reservoir-flow-estimate")
        return flags

    def _has_losses(self) -> bool:
        return any(
            float(np.nanmax(self.case.field("Line", ln, "resistance", default=0.0)))
            > 0.0
            for ln in self.case.elements("line_array")
        )

    def _check_time(self) -> None:
        g = self.case.grid
        if g.n_scenarios > 1:
            self.warn(
                f"{g.n_scenarios} scenarios: plexos2gtopt reads one scenario; "
                "scenario 1 is written"
            )
        if g.n_stages > 1:
            self.warn(
                f"{g.n_stages} stages are written as one chronological stage "
                "(plexos2gtopt reads a single stage)"
            )
        if any(not math.isclose(st.discount_factor, 1.0) for st in g.stages):
            self.warn("stage discount factors are not exported (PLEXOS: 1)")
        expected = [st.first_block for st in g.stages]
        acc = 0
        for k, st in enumerate(g.stages):
            if expected[k] != acc:
                raise ValueError("PLEXOS needs the blocks of each stage in order")
            acc += st.count_block

    # ── network ────────────────────────────────────────────────────────
    def _buses(self) -> None:
        buses = sorted(self.case.elements("bus_array"), key=lambda b: int(b["uid"]))
        self._bus_ref = self.case.ref_index(buses)
        uids = [int(b["uid"]) for b in buses]
        if uids != list(range(1, len(uids) + 1)):
            self.warn("bus uids are not 1..n: plexos2gtopt renumbers them 1..n")
        for bus in buses:
            node = self._name("Node", bus, f"B{bus['uid']}")
            self.db.add_object("Node", node)
            self.bus_node[int(bus["uid"])] = node
            v = bus.get("voltage")
            if isinstance(v, (int, float)) and v > 0:
                self.db.set_property("Node", node, "Voltage", float(v))

    def _demands(self) -> None:
        c = self.case
        mo = c.options.get("model_options", {}) or {}
        default = mo.get("demand_fail_cost", c.options.get("demand_fail_cost"))
        default = float(default) if default else 1000.0
        fcost_of: dict[str, float] = {}
        for d in c.elements("demand_array"):
            node = self.node_of(d.get("bus"))
            if node is None:
                self.warn(f"demand {d.get('name')}: unknown bus; dropped")
                continue
            lmax = np.nan_to_num(self._field("Demand", d, "lmax", 0.0))
            self._wide("Nod_Load.csv", node, lmax)
            self.names.setdefault("Demand", {})[str(d.get("name"))] = f"load_{node}"
            fc = d.get("fcost")
            fc = float(fc) if isinstance(fc, (int, float)) and fc > 0 else default
            if node in fcost_of and not math.isclose(fcost_of[node], fc):
                self.warn(
                    f"bus {node}: demands with different fail costs; the lowest is kept"
                )
            fcost_of[node] = min(fc, fcost_of.get(node, fc))
            for extra in ("lmin", "emin", "ecost", "lossfactor", "capacity", "expcap"):
                if d.get(extra) not in (None, 0, 0.0):
                    self.warn(f"demand {d.get('name')}: {extra} is not exported")
        # One Region per distinct VoLL; buses without demand join the default.
        regions: dict[float, str] = {}
        for node in self.bus_node.values():
            voll = fcost_of.get(node, default)
            if voll not in regions:
                regions[voll] = "Region" if not regions else f"Region_{len(regions)}"
                self.db.add_object("Region", regions[voll])
                self.db.set_property("Region", regions[voll], "VoLL", voll)
            self.db.add_membership("Node", node, "Region", regions[voll], "Region")

    def _lines(self) -> None:
        c = self.case
        for ln in c.elements("line_array"):
            na, nb = self.node_of(ln.get("bus_a")), self.node_of(ln.get("bus_b"))
            if na is None or nb is None:
                self.warn(f"line {ln.get('name')}: unknown bus; dropped")
                continue
            name = self._name("Line", ln, f"L{ln.get('uid')}")
            self.db.add_object("Line", name)
            self.db.add_membership("Line", name, "Node", na, "Node From")
            self.db.add_membership("Line", name, "Node", nb, "Node To")
            tab = self._field("Line", ln, "tmax_ab", np.nan)
            tba = self._field("Line", ln, "tmax_ba", np.nan)
            if np.isnan(tab).any() or np.isnan(tba).any():
                self.warn(
                    f"line {name}: unbounded flow written as a {NO_LIMIT:g} MW rating"
                )
            tab = np.where(np.isnan(tab), NO_LIMIT, tab)
            tba = np.where(np.isnan(tba), tab, tba)
            if float(tab.max()) <= 0.0:
                self.warn(
                    f"line {name}: tmax_ab <= 0 everywhere; plexos2gtopt drops it"
                )
            if float(np.abs(tba).max()) <= 0.0:
                self.warn(
                    f"line {name}: tmax_ba = 0 is read back as tmax_ab "
                    "(PLEXOS Min Flow 0 means unset)"
                )
            if self._varies(tab) or self._varies(tba):
                self._long("Lin_MaxRating.csv", name, tab)
                self._long("Lin_MinRating.csv", name, -np.abs(tba))
            else:
                self.db.set_property("Line", name, "Max Flow", float(tab[0]))
                self.db.set_property("Line", name, "Min Flow", -abs(float(tba[0])))
            self.db.set_property("Line", name, "Enforce Limits", 2.0)
            v = self._field("Line", ln, "voltage", 1.0)
            x = self._field("Line", ln, "reactance", np.nan)
            r = self._field("Line", ln, "resistance", 0.0)
            for what, arr in (("voltage", v), ("reactance", x), ("resistance", r)):
                if self._varies(np.nan_to_num(arr)):
                    self.warn(f"line {name}: time-varying {what}; block 1 is kept")
            v0 = float(v[0]) if v[0] > 0 else 1.0
            if not np.isnan(x[0]) and x[0] > 0:
                self.db.set_property(
                    "Line", name, "Reactance", S_BASE_MVA * float(x[0]) / v0**2
                )
            if r[0] > 0:
                self.db.set_property(
                    "Line", name, "Resistance", S_BASE_MVA * float(r[0]) / v0**2
                )
                self.warn(
                    f"line {name}: resistance exported; plexos2gtopt applies its "
                    "own loss model (--line-losses-mode)"
                )
            tcost = self._field("Line", ln, "tcost", 0.0)
            if float(np.abs(np.nan_to_num(tcost)).max()) > 0.0:
                self.warn(f"line {name}: tcost is not exported")
            active = ln.get("active")
            if active not in (None, True, 1, 1.0):
                self.warn(f"line {name}: active schedule is not exported")
            for extra in ("expcap", "expmod", "annual_capcost", "capacity"):
                if ln.get(extra) not in (None, 0, 0.0):
                    self.warn(f"line {name}: {extra} is not exported")

    # ── units ──────────────────────────────────────────────────────────
    def _profiles(self) -> dict[Any, dict[str, Any]]:
        out: dict[Any, dict[str, Any]] = {}
        for p in self.case.elements("generator_profile_array"):
            out[p.get("generator")] = p
        for p in self.case.elements("capacity_profile_array"):
            if p.get("owner_kind", "generator") == "generator":
                out[p.get("owner")] = p
        return out

    def _generators(self) -> None:
        c = self.case
        profiles = self._profiles()
        for gen in c.elements("generator_array"):
            node = self.node_of(gen.get("bus"))
            if node is None:
                self.warn(f"generator {gen.get('name')}: unknown bus; dropped")
                continue
            name = self._name("Generator", gen, f"G{gen.get('uid')}")
            self.db.add_object("Generator", name)
            self.db.add_membership("Generator", name, "Node", node, "Nodes")
            self._gen_node[str(gen.get("name"))] = name
            cap = gen.get("capacity")
            capv = float(cap) if isinstance(cap, (int, float)) else math.nan
            pmax = self._field("Generator", gen, "pmax", np.nan)
            if np.isnan(pmax).any():
                fill = capv if not math.isnan(capv) else NO_LIMIT
                if math.isnan(capv):
                    self.warn(
                        f"generator {name}: no pmax/capacity; written as {NO_LIMIT:g} MW"
                    )
                pmax = np.where(np.isnan(pmax), fill, pmax)
            prof = profiles.get(gen.get("name"), profiles.get(gen.get("uid")))
            if prof is not None:
                pr = np.nan_to_num(
                    self._first(
                        c.field("GeneratorProfile", prof, "profile", default=1.0),
                        f"profile {prof.get('name')}",
                    ),
                    nan=1.0,
                )
                base = capv if not math.isnan(capv) else float(pmax.max())
                pmax = np.minimum(pmax, pr * base)
            self.db.set_property("Generator", name, "Max Capacity", float(pmax.max()))
            self._long("Gen_Rating.csv", name, pmax)
            pmin = np.nan_to_num(self._field("Generator", gen, "pmin", 0.0))
            if float(pmin.max()) > 0.0:
                self.warn(
                    f"generator {name}: pmin > 0 is not exported (plexos2gtopt "
                    "reads Min Stable Level only through unit commitment)"
                )
            gcost = np.nan_to_num(self._field("Generator", gen, "gcost", 0.0))
            dur = np.asarray(c.grid.durations)
            mean = float((gcost * dur).sum() / max(dur.sum(), 1e-300))
            if self._varies(gcost):
                self.warn(
                    f"generator {name}: time-varying gcost; PLEXOS VO&M Charge "
                    "takes the duration-weighted mean"
                )
            if mean != 0.0:
                self.db.set_property("Generator", name, "VO&M Charge", mean)
            for extra in (
                "expcap",
                "expmod",
                "annual_capcost",
                "emission_factor",
                "fuel",
                "heat_rate",
                "lossfactor",
            ):
                if gen.get(extra) not in (None, 0, 0.0):
                    self.warn(f"generator {name}: {extra} is not exported")

    def _batteries(self) -> None:
        c = self.case
        gen_names = self._used.get("Generator", set())
        for bat in c.elements("battery_array"):
            node = self.node_of(bat.get("bus"))
            if node is None:
                self.warn(
                    f"battery {bat.get('name')}: no bus (converter-based batteries "
                    "are not exported)"
                )
                continue
            # plexos2gtopt reads a battery's Gen_Rating row by name: keep
            # battery names distinct from every generator name.
            self._used.setdefault("Battery", set()).update(gen_names)
            name = self._name("Battery", bat, f"BESS{bat.get('uid')}")
            self.db.add_object("Battery", name)
            self.db.add_membership("Battery", name, "Node", node, "Nodes")
            cap = bat.get("capacity")
            emax = self._field("Battery", bat, "emax", np.nan)
            if isinstance(cap, (int, float)):
                emax = np.where(np.isnan(emax), float(cap), emax)
            if np.isnan(emax).any():
                self.warn(f"battery {name}: no emax/capacity; written as {NO_LIMIT:g}")
                emax = np.where(np.isnan(emax), NO_LIMIT, emax)
            if self._varies(emax):
                self.warn(f"battery {name}: time-varying emax; its minimum is kept")
            capacity = float(emax.min())
            self.db.set_property("Battery", name, "Capacity", capacity)
            for fld, prop in (
                ("pmax_discharge", "Max Power"),
                ("pmax_charge", "Max Load"),
            ):
                arr = self._field("Battery", bat, fld, np.nan)
                if np.isnan(arr).any():
                    self.warn(
                        f"battery {name}: {fld} unset; written as {NO_LIMIT:g} MW"
                    )
                    arr = np.where(np.isnan(arr), NO_LIMIT, arr)
                if self._varies(arr):
                    self.warn(
                        f"battery {name}: time-varying {fld}; its maximum is kept"
                    )
                self.db.set_property("Battery", name, prop, float(arr.max()))
            for fld, prop in (
                ("input_efficiency", "Charge Efficiency"),
                ("output_efficiency", "Discharge Efficiency"),
            ):
                eff = float(self._field("Battery", bat, fld, 1.0)[0])
                self.db.set_property("Battery", name, prop, 100.0 * eff)
            emin = np.nan_to_num(self._field("Battery", bat, "emin", 0.0))
            if float(emin.max()) > 0.0:
                self.warn(
                    f"battery {name}: emin > 0 is not exported (plexos2gtopt "
                    "reads emin = 0)"
                )
            eini = bat.get("eini")
            if isinstance(eini, (int, float)) and eini > 0 and capacity > 0:
                self._long(
                    "BESS_IniValue.csv",
                    name,
                    np.full(c.grid.n_blocks, 100.0 * float(eini) / capacity),
                )
            efin = bat.get("efin")
            if efin is not None and not (
                isinstance(efin, (int, float))
                and math.isclose(float(efin), float(eini or 0.0))
            ):
                self.warn(
                    f"battery {name}: efin is not exported (plexos2gtopt pins "
                    "efin = eini)"
                )
            self._battery_cycle(bat, name)
            loss = self._field("Battery", bat, "annual_standing_loss", np.nan)
            if np.isnan(loss).any():
                mo = c.options.get("model_options", {}) or {}
                dflt = mo.get("battery_annual_standing_loss")
                loss = np.where(
                    np.isnan(loss),
                    float(dflt) if isinstance(dflt, (int, float)) else BATTERY_LOSS,
                    loss,
                )
            if self._varies(loss):
                self.warn(
                    f"battery {name}: time-varying annual_standing_loss; block 1 is kept"
                )
            # PLEXOS Self Discharge Rate is % per hour.
            self.db.set_property(
                "Battery",
                name,
                "Self Discharge Rate",
                100.0 * float(loss[0]) / HOURS_PER_YEAR,
            )
            for extra in ("discharge_cost", "expcap", "expmod"):
                if bat.get(extra) not in (None, 0, 0.0):
                    self.warn(f"battery {name}: {extra} is not exported")

    def _merged_stage_rescales(self) -> bool:
        """gtopt's daily-cycle 24/T rescale would apply to the read-back stage."""
        total = sum(self.hours)
        return total > 24 and total / max(len(self.hours), 1) > 1.0

    def _battery_cycle(self, bat: dict[str, Any], name: str) -> None:
        """Carry ``daily_cycle`` / ``max_cycles_day`` as ``Max Cycles Day``.

        plexos2gtopt turns a battery with ``Max Cycles Day > 0`` into a
        chronological state-variable battery (``daily_cycle = false``) and
        emits the cycle row only when the limit is reachable; any other
        battery keeps gtopt's daily-cycle default.
        """
        cycles = bat.get("max_cycles_day")
        daily = bat.get("daily_cycle")
        if isinstance(cycles, (int, float)) and cycles > 0:
            self.db.set_property("Battery", name, "Max Cycles Day", float(cycles))
            if daily is not False:
                self.warn(
                    f"battery {name}: max_cycles_day makes plexos2gtopt read a "
                    "chronological battery (daily_cycle = false)"
                )
            return
        if daily is False or self._merged_stage_rescales():
            # Unreachable limit: no cycle row, chronological battery.
            self.db.set_property("Battery", name, "Max Cycles Day", NO_CYCLE_LIMIT)
            if daily is not False:
                self.warn(
                    f"battery {name}: written as a chronological battery "
                    "(daily_cycle = false): a daily cycle over the merged "
                    f"{sum(self.hours)} h stage would be rescaled by gtopt"
                )

    # ── hydro ──────────────────────────────────────────────────────────
    def _hydro(self) -> None:
        c = self.case
        junctions = c.elements("junction_array")
        if not junctions and not c.elements("turbine_array"):
            return
        jref = c.ref_index(junctions)
        storage_of: dict[int, str] = {}
        for res in c.elements("reservoir_array"):
            j = jref.get(res.get("junction"))
            if j is None:
                self.warn(f"reservoir {res.get('name')}: unknown junction; dropped")
                continue
            storage_of[id(j)] = self._storage(res)
        for j in junctions:
            if id(j) not in storage_of:
                self.warn(
                    f"junction {j.get('name')}: junctions without a reservoir are "
                    "not exported"
                )
            elif j.get("drain") is False:
                self.warn(
                    f"junction {j.get('name')}: drain = false is not exported "
                    "(plexos2gtopt lets every reservoir spill at a cost)"
                )
        for tu in c.elements("turbine_array"):
            self._turbine(tu, jref, storage_of)
        for fl in c.elements("flow_array"):
            j = jref.get(fl.get("junction"))
            if j is None or id(j) not in storage_of:
                self.warn(
                    f"flow {fl.get('name')}: only inflows into a reservoir are "
                    "exported (Hydro_WaterFlows.csv)"
                )
                continue
            q = np.nan_to_num(self._field("Flow", fl, "discharge", 0.0))
            if int(fl.get("direction", 1) or 1) < 0:
                q = -q
                self.warn(
                    f"flow {fl.get('name')}: direction -1 written as negative inflow"
                )
            self._wide("Hydro_WaterFlows.csv", storage_of[id(j)], q)
        if c.elements("waterway_array"):
            self.warn("waterway_array is not exported (spills leave the system)")
        if c.elements("reservoir_production_factor_array"):
            self.warn(
                "reservoir_production_factor_array is not exported (constant "
                "Efficiency Incr)"
            )

    def _storage(self, res: dict[str, Any]) -> str:
        name = self._name("Storage", res, f"S{res.get('uid')}")
        self.db.add_object("Storage", name)
        fcr = res.get("flow_conversion_rate")
        fcr = float(fcr) if isinstance(fcr, (int, float)) and fcr > 0 else DEFAULT_FCR
        k = PLEXOS_FCR / fcr
        self.volume_factor[name] = k
        cap = res.get("capacity")
        emax = self._field("Reservoir", res, "emax", np.nan)
        if isinstance(cap, (int, float)):
            emax = np.where(np.isnan(emax), float(cap), emax)
        emax = np.where(np.isnan(emax), NO_LIMIT / k, emax)
        emin = np.nan_to_num(self._field("Reservoir", res, "emin", 0.0))
        for fld, arr, prop, csv_name in (
            ("emax", emax, "Max Volume", "Hydro_MaxVolume.csv"),
            ("emin", emin, "Min Volume", "Hydro_MinVolume.csv"),
        ):
            if self._varies(arr):
                self._wide(csv_name, name, k * arr)
                self.warn(
                    f"reservoir {name}: time-varying {fld} written per hour; "
                    "plexos2gtopt reshapes per-block volume bounds"
                )
            self.db.set_property("Storage", name, prop, k * float(arr.max()))
        eini = res.get("eini")
        if isinstance(eini, (int, float)):
            self.db.set_property("Storage", name, "Initial Volume", k * float(eini))
        for extra in (
            "efin",
            "efin_cost",
            "soft_emin",
            "fmin",
            "fmax",
            "spillway_cost",
            "spillway_capacity",
            "annual_loss",
        ):
            if res.get(extra) not in (None, 0, 0.0):
                self.warn(f"reservoir {name}: {extra} is not exported")
        return name

    def _turbine(
        self,
        tu: dict[str, Any],
        jref: dict[Any, dict[str, Any]],
        storage_of: dict[int, str],
    ) -> None:
        ja = jref.get(tu.get("junction_a"))
        gen = self._gen_node.get(str(tu.get("generator")))
        if gen is None:
            gens = self.case.ref_index(self.case.elements("generator_array"))
            g = gens.get(tu.get("generator"))
            gen = self._gen_node.get(str((g or {}).get("name")))
        if ja is None or id(ja) not in storage_of or gen is None:
            self.warn(
                f"turbine {tu.get('name')}: only reservoir-fed turbines with a "
                "generator are exported"
            )
            return
        self.db.add_membership(
            "Generator", gen, "Storage", storage_of[id(ja)], "Head Storage"
        )
        jb = jref.get(tu.get("junction_b"))
        if jb is not None and id(jb) in storage_of:
            self.db.add_membership(
                "Generator", gen, "Storage", storage_of[id(jb)], "Tail Storage"
            )
        elif tu.get("junction_b") is not None:
            self.warn(
                f"turbine {tu.get('name')}: outlet without a reservoir; the water "
                "leaves the system"
            )
        pf = self._field("Turbine", tu, "production_factor", 1.0)
        if self._varies(pf):
            self.warn(
                f"turbine {tu.get('name')}: time-varying production factor; "
                "block 1 is kept"
            )
        self.db.set_property("Generator", gen, "Efficiency Incr", float(pf[0]))
        if tu.get("capacity") is not None:
            self.warn(f"turbine {tu.get('name')}: flow capacity is not exported")

    # ── data files ─────────────────────────────────────────────────────
    def _dates(self) -> list[tuple[int, int, int, int]]:
        out = []
        for h in range(self.n_days * 24):
            d = self.start_date + timedelta(days=h // 24)
            out.append((d.year, d.month, d.day, h % 24 + 1))
        return out

    def _write_csvs(self) -> None:
        dates = self._dates()
        for csv_name, rows in self.long_rows.items():
            with (self.out_dir / csv_name).open(
                "w", encoding="utf-8", newline=""
            ) as fh:
                w = csv.writer(fh)
                w.writerow(["NAME", "YEAR", "MONTH", "DAY", "PERIOD", "BAND", "VALUE"])
                for obj, series in rows:
                    for (y, m, d, p), v in zip(dates, series, strict=True):
                        w.writerow([obj, y, m, d, p, 1, num_text(v)])
        for csv_name, cols in self.wide_cols.items():
            names = list(cols)
            with (self.out_dir / csv_name).open(
                "w", encoding="utf-8", newline=""
            ) as fh:
                w = csv.writer(fh)
                w.writerow(["YEAR", "MONTH", "DAY", "PERIOD", *names])
                for h, (y, m, d, p) in enumerate(dates):
                    w.writerow([y, m, d, p, *(num_text(cols[n][h]) for n in names)])
