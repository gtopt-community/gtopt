# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a gtopt solution as PLEXOS solution tables (the RES schema).

A PLEXOS run stores its results in a solution ``.accdb``; the tables
:class:`plexos2gtopt.plexos_solution.PlexosSolution` reads are::

    t_class, t_collection, t_property, t_object, t_membership
    t_key       key_id -> (membership, property, phase, band, sample)
    t_data_0    (key_id, period_id, value)
    t_phase_3   interval_id -> period_id   (ST phase: hour -> block)

plus ``t_model``, ``t_unit`` and ``t_period_0`` (interval datetimes) for
a reader of the full schema.  mdbtools only reads Access files, so the
tables are written as one CSV per table (``<table>.csv``, the header and
columns of ``mdb-export``); :func:`csv_table_source` serves them to
:class:`PlexosSolution` exactly like ``mdb_table_source`` serves an
``.accdb``.

Mapping (gtopt output -> PLEXOS System collection property):

==========================================  =================================
``Generator/generation_sol``                ``Generator.Generation`` [MW]
``Demand/load_sol + fail_sol`` (per bus)    ``Node.Load`` [MW]
``Demand/fail_sol`` (per bus)               ``Node.Unserved Energy`` [MW]
``Bus/balance_dual``                        ``Node.Price`` [$/MWh]
``Line/flow_sol``                           ``Line.Flow`` [MW]
``Reservoir/energy_sol`` x volume factor    ``Storage.End Volume`` [CMD]
``Battery/energy_sol``                      ``Battery.Energy`` [MWh]
``Battery/fout_sol`` / ``finp_sol``         ``Battery.Generation`` / ``Load``
==========================================  =================================

Periods are the gtopt blocks in simulation order (``period_id`` = 1..n),
and ``t_phase_3`` maps each hour of a block to it, so PLEXOS' "value of
the block holding hour h" reading gives the block value.  Scenario
``s`` (1-based) is sample ``s``; sample 0 is the probability-weighted
mean, the sample :class:`PlexosSolution` reads.  Object names are those
:class:`gtopt2plexos.writer.PlexosWriter` gives the input dataset.
"""

from __future__ import annotations

import csv
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gtopt_shared.case_reader import GtoptCase
from gtopt2plexos.writer import PlexosWriter, block_hours

logger = logging.getLogger(__name__)

SOLUTION_DIR = "plexos_solution"
ST_PHASE = 3
MODEL_NAME = "gtopt"

#: (PLEXOS class, property, unit) in output order.
PROPERTIES: tuple[tuple[str, str, str], ...] = (
    ("Generator", "Generation", "MW"),
    ("Node", "Load", "MW"),
    ("Node", "Unserved Energy", "MW"),
    ("Node", "Price", "$/MWh"),
    ("Line", "Flow", "MW"),
    ("Storage", "End Volume", "CMD"),
    ("Battery", "Energy", "MWh"),
    ("Battery", "Generation", "MW"),
    ("Battery", "Load", "MW"),
)

_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "t_class": ("class_id", "name"),
    "t_collection": ("collection_id", "parent_class_id", "child_class_id", "name"),
    "t_property": ("property_id", "collection_id", "name", "unit_id"),
    "t_unit": ("unit_id", "value"),
    "t_object": ("object_id", "class_id", "name"),
    "t_membership": (
        "membership_id",
        "parent_class_id",
        "child_class_id",
        "collection_id",
        "parent_object_id",
        "child_object_id",
    ),
    "t_model": ("model_id", "name"),
    "t_key": (
        "key_id",
        "membership_id",
        "model_id",
        "phase_id",
        "property_id",
        "period_type_id",
        "band_id",
        "sample_id",
        "timeslice_id",
    ),
    "t_data_0": ("key_id", "period_id", "value"),
    "t_phase_3": ("interval_id", "period_id"),
    "t_period_0": ("interval_id", "hour_id", "day_id", "datetime", "period_of_day"),
}


def read_output(results: Path, cls: str, name: str) -> pd.DataFrame:
    """A gtopt output table as long ``scenario, stage, block, uid, value``.

    Accepts ``<Class>/<name>.parquet`` (file or hive directory),
    ``<name>_s<scene>_p<phase>.parquet`` pieces and the ``.csv`` forms;
    legacy wide ``uid:<N>`` columns are melted.  Missing table: empty.
    """
    base = results / cls
    frames: list[pd.DataFrame] = []
    if base.is_dir():
        for path in sorted(base.iterdir()):
            stem = path.name.split(".", 1)[0]
            if stem != name and not stem.startswith(f"{name}_s"):
                continue
            if ".parquet" in path.name:
                frames.append(pd.read_parquet(path))
            elif ".csv" in path.name:
                frames.append(pd.read_csv(path))
    cols = ["scenario", "stage", "block", "uid", "value"]
    if not frames:
        return pd.DataFrame(columns=cols)
    df = pd.concat(frames, ignore_index=True)
    if "uid" not in df.columns:
        ids = [c for c in ("scenario", "stage", "block") if c in df.columns]
        uid_cols = [c for c in df.columns if str(c).startswith("uid:")]
        df = df.melt(id_vars=ids, value_vars=uid_cols, var_name="uid")
        df["uid"] = df["uid"].str.slice(4)
    for c in ("scenario", "stage", "block", "uid"):
        df[c] = pd.to_numeric(df[c]).astype(int)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    return df[cols]


@dataclass
class PlexosResultsWriter:
    """Write the gtopt solution in ``results`` as PLEXOS solution tables."""

    case: GtoptCase
    results: Path
    out_dir: Path
    writer: PlexosWriter | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Index the case (names from the input writer, uids, periods)."""
        if self.writer is None:
            self.writer = PlexosWriter(self.case, self.out_dir)
            self.writer.build()
        self.tables: dict[str, list[tuple[Any, ...]]] = {t: [] for t in _TABLE_COLUMNS}
        g = self.case.grid
        try:
            self.hours = block_hours(g.durations)
        except ValueError:
            self.warn("non-integer block durations: one interval per block")
            self.hours = [1] * g.n_blocks
        self.period_of: dict[int, int] = {u: k + 1 for k, u in enumerate(g.block_uids)}
        self.scen_pos: dict[int, int] = {u: k for k, u in enumerate(g.scenario_uids)}

    def warn(self, msg: str) -> None:
        """Record and log a warning (once per text)."""
        if msg not in self.warnings:
            self.warnings.append(msg)
            logger.warning("%s", msg)

    # ── series ─────────────────────────────────────────────────────────
    def _matrix(self, cls: str, name: str) -> dict[int, np.ndarray]:
        """``uid -> (n_scenarios, n_blocks)`` of one output table (0 = absent)."""
        g = self.case.grid
        df = read_output(self.results, cls, name)
        out: dict[int, np.ndarray] = {}
        for uid, sub in df.groupby("uid"):
            arr = np.zeros((g.n_scenarios, g.n_blocks))
            for s, b, v in zip(
                sub["scenario"], sub["block"], sub["value"], strict=True
            ):
                si, bi = self.scen_pos.get(int(s)), self.period_of.get(int(b))
                if si is not None and bi is not None:
                    arr[si, bi - 1] = float(v)
            out[int(uid)] = arr
        return out

    def _names(self, cls: str) -> Mapping[str, str]:
        assert self.writer is not None
        return self.writer.names.get(cls, {})

    def _by_name(
        self, array: str, cls: str, table: Mapping[int, np.ndarray]
    ) -> dict[str, np.ndarray]:
        """PLEXOS object name -> series, for the elements the dataset holds."""
        names = self._names(cls)
        out: dict[str, np.ndarray] = {}
        for elem in self.case.elements(array):
            pname = names.get(str(elem.get("name")))
            if pname is None:
                continue
            arr = table.get(int(elem.get("uid", -1)))
            if arr is not None:
                out[pname] = arr
        return out

    def series(self) -> dict[tuple[str, str], dict[str, np.ndarray]]:
        """``(class, property) -> {object: (n_scenarios, n_blocks)}``."""
        assert self.writer is not None
        w = self.writer
        g = self.case.grid
        out: dict[tuple[str, str], dict[str, np.ndarray]] = {}
        out[("Generator", "Generation")] = self._by_name(
            "generator_array", "Generator", self._matrix("Generator", "generation_sol")
        )
        load = self._matrix("Demand", "load_sol")
        fail = self._matrix("Demand", "fail_sol")
        node_load: dict[str, np.ndarray] = {}
        node_fail: dict[str, np.ndarray] = {}
        zero = np.zeros((g.n_scenarios, g.n_blocks))
        for d in self.case.elements("demand_array"):
            node = w.node_of(d.get("bus"))
            if node is None:
                continue
            uid = int(d.get("uid", -1))
            served, unserved = load.get(uid, zero), fail.get(uid, zero)
            node_load[node] = node_load.get(node, zero) + served + unserved
            node_fail[node] = node_fail.get(node, zero) + unserved
        out[("Node", "Load")] = node_load
        out[("Node", "Unserved Energy")] = node_fail
        price = self._matrix("Bus", "balance_dual")
        out[("Node", "Price")] = {
            node: price[uid] for uid, node in w.bus_node.items() if uid in price
        }
        out[("Line", "Flow")] = self._by_name(
            "line_array", "Line", self._matrix("Line", "flow_sol")
        )
        vol = self._by_name(
            "reservoir_array", "Storage", self._matrix("Reservoir", "energy_sol")
        )
        out[("Storage", "End Volume")] = {
            n: a * w.volume_factor.get(n, 1.0) for n, a in vol.items()
        }
        for prop, fld in (
            ("Energy", "energy_sol"),
            ("Generation", "fout_sol"),
            ("Load", "finp_sol"),
        ):
            out[("Battery", prop)] = self._by_name(
                "battery_array", "Battery", self._matrix("Battery", fld)
            )
        return out

    # ── tables ─────────────────────────────────────────────────────────
    def build(self) -> None:
        """Fill the solution tables in memory."""
        t = self.tables
        classes = ["System", *dict.fromkeys(c for c, _p, _u in PROPERTIES)]
        cid = {c: k + 1 for k, c in enumerate(classes)}
        t["t_class"] = [(cid[c], c) for c in classes]
        coll = {c: k for k, c in enumerate(classes[1:], start=1)}
        assert self.writer is not None
        plural = {
            "Battery": "Batteries",
            "Storage": "Storages",
        }
        t["t_collection"] = [
            (coll[c], cid["System"], cid[c], plural.get(c, c + "s")) for c in coll
        ]
        units = list(dict.fromkeys(u for _c, _p, u in PROPERTIES))
        t["t_unit"] = list(enumerate(units))
        prop_id: dict[tuple[str, str], int] = {}
        for k, (c, p, u) in enumerate(PROPERTIES, start=1):
            prop_id[(c, p)] = k
            t["t_property"].append((k, coll[c], p, units.index(u)))
        t["t_model"] = [(1, MODEL_NAME)]
        t["t_object"] = [(1, cid["System"], "System")]
        member: dict[tuple[str, str], int] = {}
        oid = 1
        for cls in coll:
            for name in self._objects(cls):
                oid += 1
                t["t_object"].append((oid, cid[cls], name))
                mid = len(t["t_membership"]) + 1
                t["t_membership"].append(
                    (mid, cid["System"], cid[cls], coll[cls], 1, oid)
                )
                member[(cls, name)] = mid
        self._data(prop_id, member)
        self._time()

    def _objects(self, cls: str) -> list[str]:
        assert self.writer is not None
        return self.writer.db.objects_of(cls)

    def _samples(self, arr: np.ndarray) -> list[tuple[int, np.ndarray]]:
        """``(sample_id, per-block values)``: 0 = mean, s = scenario s."""
        g = self.case.grid
        probs = np.asarray(g.probabilities, dtype=float)
        weights = probs / probs.sum() if probs.sum() > 0 else probs
        mean = (arr * weights[:, None]).sum(axis=0)
        if g.n_scenarios == 1:
            return [(0, mean)]
        return [(0, mean), *((s + 1, arr[s]) for s in range(g.n_scenarios))]

    def _data(
        self,
        prop_id: Mapping[tuple[str, str], int],
        member: Mapping[tuple[str, str], int],
    ) -> None:
        t = self.tables
        for (cls, prop), objs in self.series().items():
            for name, arr in objs.items():
                mid = member.get((cls, name))
                if mid is None:
                    continue
                for sample, values in self._samples(arr):
                    key = len(t["t_key"]) + 1
                    t["t_key"].append(
                        (key, mid, 1, ST_PHASE, prop_id[(cls, prop)], 0, 1, sample, 0)
                    )
                    t["t_data_0"].extend(
                        (key, p + 1, float(v)) for p, v in enumerate(values)
                    )

    def _time(self) -> None:
        assert self.writer is not None
        start = datetime.combine(self.writer.start_date, datetime.min.time())
        interval = 0
        for period, n in enumerate(self.hours, start=1):
            for _ in range(n):
                interval += 1
                self.tables["t_phase_3"].append((interval, period))
                when = start + timedelta(hours=interval - 1)
                self.tables["t_period_0"].append(
                    (
                        interval,
                        interval,
                        math.ceil(interval / 24),
                        when.strftime("%m/%d/%y %H:%M:%S"),
                        when.hour + 1,
                    )
                )

    def write(self) -> Path:
        """Write ``<out_dir>/<table>.csv`` for every table; returns ``out_dir``."""
        self.build()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for table, cols in _TABLE_COLUMNS.items():
            with (self.out_dir / f"{table}.csv").open(
                "w", encoding="utf-8", newline=""
            ) as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                w.writerows(
                    tuple(repr(v) if isinstance(v, float) else v for v in row)
                    for row in self.tables[table]
                )
        return self.out_dir


def load_tables(directory: Path) -> dict[str, str]:
    """``{table: csv text}`` of a directory written by :class:`PlexosResultsWriter`.

    Feed it to :func:`plexos2gtopt.plexos_solution.csv_table_source`.
    """
    return {
        p.stem: p.read_text(encoding="utf-8") for p in sorted(directory.glob("t_*.csv"))
    }
