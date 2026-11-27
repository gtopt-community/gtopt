# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write external results (PLP, PSR SDDP, PLEXOS) as a gtopt output directory.

The inverse of :mod:`gtopt_shared.results_reader`: the importers
(``plp2gtopt --results``, ``sddp2gtopt --results``, ``plexos2gtopt
--results``) fill an :class:`ImportedResults` with per-element series on
the time grid of the case they converted, and :meth:`ImportedResults.write`
lays them out exactly as a ``gtopt`` run of that case would:

* ``<Class>/<field>.parquet/scene=<S>/phase=<P>/part.parquet`` -- one
  hive partition per (scene, phase) of the planning (``scene=0`` /
  ``phase=0`` when the planning has no ``scene_array`` / ``phase_array``),
* the long schema ``scenario, stage, block, uid`` (``int32``) ``value``
  (``float32`` after rounding to ``output_round_decimals`` = 7, as gtopt
  does by default; ``float64`` without rounding when the planning asks
  for ``output_round_decimals <= 0`` or ``>= 8``),
* rows in gtopt's order (uid, scenario, stage, block), exact zeros of
  ``_sol`` / ``_cost`` streams dropped, ``_dual`` streams dense,
* per-stage streams (``Reservoir/efin_sol``, ``eini_sol``) with
  ``block = 0``,
* Parquet ``zstd`` (or the planning's ``output_compression``),
  ``BYTE_STREAM_SPLIT`` on ``value`` with no dictionary, no statistics,
* the run files gtopt writes next to the streams: ``solution.csv`` (one
  row per (scene, phase), gtopt's header), ``solver_status.json``,
  ``planning.json`` (the converted planning) and a ``README.md``.

``output_format = csv`` in the planning gives gtopt's CSV shards instead
(``<field>_s<S>_p<P>.csv``, compressed per ``output_compression``).

Units are gtopt's (MW, $/MWh, m3/s, the reservoir's own volume unit, MWh
for batteries); converting the source's units is the importer's job.
``solution.csv`` reports, per (scene, phase), the probability-weighted
operating cost the source reports for that cell (``obj_value``, physical
units, ``scale_objective = 1``) -- an approximation of gtopt's objective
that leaves out what the source does not report (future-cost / boundary
terms, penalties it does not export).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from gtopt_shared.case_reader import TimeGrid

logger = logging.getLogger(__name__)

#: gtopt's default ``output_round_decimals`` (``PlanningOptionsLP``).
DEFAULT_ROUND_DECIMALS = 7
#: gtopt's default ``output_compression``.
DEFAULT_COMPRESSION = "zstd"

SOLUTION_HEADER = (
    "scene",
    "phase",
    "status",
    "status_name",
    "obj_value",
    "gap",
    "gap_change",
    "solve_ticks",
    "solve_time_s",
    "solve_calls",
    "infeasible_count",
    "scale_objective",
    "gtopt_version",
    "gtopt_revision",
)

_SCHEMA_IDS = ("scenario", "stage", "block", "uid")


@dataclass(frozen=True)
class Cell:
    """One (scene, phase) partition: its uids and grid positions."""

    scene_uid: int
    phase_uid: int
    scenarios: tuple[int, ...]  # 0-based scenario positions
    stages: tuple[int, ...]  # 0-based stage positions


def planning_cells(planning: Mapping[str, Any], grid: TimeGrid) -> list[Cell]:
    """The (scene, phase) cells gtopt writes for ``planning``."""
    sim = planning.get("simulation", {}) or {}
    scenes = sim.get("scene_array") or []
    phases = sim.get("phase_array") or []
    all_scen = tuple(range(grid.n_scenarios))
    all_stage = tuple(range(grid.n_stages))
    scene_list: list[tuple[int, tuple[int, ...]]] = []
    for i, sc in enumerate(scenes):
        first = int(sc.get("first_scenario", 0))
        count = int(sc.get("count_scenario", grid.n_scenarios - first))
        scene_list.append(
            (
                int(sc.get("uid", i)),
                tuple(k for k in range(first, first + count) if k < grid.n_scenarios),
            )
        )
    if not scene_list:
        scene_list = [(0, all_scen)]
    phase_list: list[tuple[int, tuple[int, ...]]] = []
    for i, ph in enumerate(phases):
        first = int(ph.get("first_stage", 0))
        count = int(ph.get("count_stage", grid.n_stages - first))
        phase_list.append(
            (
                int(ph.get("uid", i)),
                tuple(k for k in range(first, first + count) if k < grid.n_stages),
            )
        )
    if not phase_list:
        phase_list = [(0, all_stage)]
    return [
        Cell(su, pu, scen, stg) for su, scen in scene_list for pu, stg in phase_list
    ]


_SUFFIX_CODE = re.compile(r"^(.*)_[A-Za-z]*\d+$")


def _norm(name: str) -> str:
    return re.sub(r"\s+", "_", str(name).strip()).casefold()


class ElementIndex:
    """Find the elements of a converted planning by an external name.

    Matching, first hit wins: the exact name; the name with blanks as
    ``_`` and case folded; each ``prefixes`` entry + name (``r_LAGO``,
    ``tb_LAGO`` ...); and a name with a trailing ``_<code>`` the
    converter appended to keep names unique (``Line_1`` for ``Line``)
    when exactly one element carries it.
    """

    def __init__(self, planning: Mapping[str, Any]) -> None:
        """Index every ``system.*_array`` of ``planning``."""
        self.system: Mapping[str, Any] = planning.get("system", {}) or {}
        self._exact: dict[str, dict[str, dict[str, Any]]] = {}
        self._norm: dict[str, dict[str, dict[str, Any]]] = {}
        self._stem: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def elements(self, array: str) -> list[dict[str, Any]]:
        """Elements of ``system.<array>``."""
        return list(self.system.get(array, []) or [])

    def _build(self, array: str) -> None:
        if array in self._exact:
            return
        exact: dict[str, dict[str, Any]] = {}
        norm: dict[str, dict[str, Any]] = {}
        stem: dict[str, list[dict[str, Any]]] = {}
        for el in self.elements(array):
            name = el.get("name")
            if name is None:
                continue
            exact.setdefault(str(name), el)
            norm.setdefault(_norm(str(name)), el)
            m = _SUFFIX_CODE.match(_norm(str(name)))
            if m:
                stem.setdefault(m.group(1), []).append(el)
        self._exact[array], self._norm[array], self._stem[array] = exact, norm, stem

    def find(
        self, array: str, name: str, prefixes: Iterable[str] = ()
    ) -> dict[str, Any] | None:
        """Element of ``array`` named ``name`` (see class doc), or ``None``."""
        self._build(array)
        exact, norm, stem = self._exact[array], self._norm[array], self._stem[array]
        name = str(name).strip()
        for cand in (name, *(p + name for p in prefixes)):
            if cand in exact:
                return exact[cand]
            hit = norm.get(_norm(cand))
            if hit is not None:
                return hit
        for cand in (name, *(p + name for p in prefixes)):
            got = stem.get(_norm(cand), [])
            if len(got) == 1:
                return got[0]
        return None

    def ref(self, array: str, ref: Any) -> dict[str, Any] | None:
        """Element of ``array`` referenced by uid or name (a JSON reference)."""
        for el in self.elements(array):
            if ref is not None and ref in (el.get("uid"), el.get("name")):
                return el
        return None

    def by_bus(self, array: str, bus: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Elements of ``array`` whose ``bus`` references ``bus``."""
        refs = {bus.get("uid"), bus.get("name")} - {None}
        return [el for el in self.elements(array) if el.get("bus") in refs]


def uid_of(el: Mapping[str, Any] | None) -> int | None:
    """Integer uid of an element (``None`` when it has none)."""
    if el is None:
        return None
    u = el.get("uid")
    return int(u) if isinstance(u, (int, np.integer)) else None


@dataclass
class ImportedResults:
    """External results on the time grid of a converted gtopt planning."""

    planning: dict[str, Any]
    source: str
    warnings: list[str] = field(default_factory=list)
    #: Directory the planning's ``input_directory`` is relative to (input
    #: tables for :meth:`estimate_cost`).
    case_dir: Path | None = None

    def __post_init__(self) -> None:
        """Bind the time grid and the empty stream store."""
        self.grid = TimeGrid.from_planning(self.planning)
        self.index = ElementIndex(self.planning)
        self.block_data: dict[tuple[str, str], dict[int, np.ndarray]] = {}
        self.stage_data: dict[tuple[str, str], dict[int, np.ndarray]] = {}
        #: ``(n_scenarios, n_blocks)`` operating cost [$] the source reports.
        self.cost: np.ndarray | None = None
        self.files: list[Path] = []
        self._out = Path()

    # ── filling ────────────────────────────────────────────────────────
    def warn(self, msg: str) -> None:
        """Record and log a warning (once per text)."""
        if msg not in self.warnings:
            self.warnings.append(msg)
            logger.warning("%s", msg)

    def zeros(self) -> np.ndarray:
        """A ``(n_scenarios, n_blocks)`` zero array."""
        return np.zeros((self.grid.n_scenarios, self.grid.n_blocks))

    def add_blocks(
        self, cls: str, fld: str, uid: int, values: np.ndarray, *, add: bool = False
    ) -> None:
        """Set (or, with ``add``, accumulate) one element's per-block series."""
        arr = np.asarray(values, dtype=float)
        if arr.shape != (self.grid.n_scenarios, self.grid.n_blocks):
            raise ValueError(f"{cls}/{fld} uid {uid}: bad shape {arr.shape}")
        store = self.block_data.setdefault((cls, fld), {})
        if add and uid in store:
            store[uid] = store[uid] + arr
        else:
            store[uid] = arr.copy()

    def add_stages(self, cls: str, fld: str, uid: int, values: np.ndarray) -> None:
        """Set one element's per-stage series ``(n_scenarios, n_stages)``."""
        arr = np.asarray(values, dtype=float)
        if arr.shape != (self.grid.n_scenarios, self.grid.n_stages):
            raise ValueError(f"{cls}/{fld} uid {uid}: bad shape {arr.shape}")
        self.stage_data.setdefault((cls, fld), {})[uid] = arr.copy()

    def has(self, cls: str, fld: str) -> bool:
        """Whether a stream was filled."""
        return bool(self.block_data.get((cls, fld)) or self.stage_data.get((cls, fld)))

    def get_blocks(self, cls: str, fld: str, uid: int) -> np.ndarray | None:
        """A filled per-block series, or ``None``."""
        return self.block_data.get((cls, fld), {}).get(uid)

    # ── shared mappings ────────────────────────────────────────────────
    def assign_bus_load(
        self, bus: Mapping[str, Any], served: np.ndarray, fail: np.ndarray
    ) -> None:
        """Put a bus's served load and deficit [MW] on its demand(s).

        One demand takes both.  Several demands split them by their
        scalar ``lmax`` share; otherwise the first demand takes both (with
        a warning).  A bus without demands only warns when it has load.
        """
        name = bus.get("name", bus.get("uid"))
        demands = [d for d in self.index.by_bus("demand_array", bus) if uid_of(d)]
        if not demands:
            if np.any(served) or np.any(fail):
                self.warn(f"{self.source}: bus {name} has load but no demand")
            return
        shares: list[tuple[int, float]] = []
        lmax = [d.get("lmax") for d in demands]
        if len(demands) > 1 and all(isinstance(v, (int, float)) for v in lmax):
            tot = sum(float(v) for v in lmax if isinstance(v, (int, float)))
            if tot > 0:
                shares = [
                    (uid_of(d) or 0, float(v) / tot)
                    for d, v in zip(demands, lmax, strict=True)
                    if isinstance(v, (int, float))
                ]
        if not shares:
            if len(demands) > 1:
                self.warn(
                    f"{self.source}: bus {name} has {len(demands)} demands; its "
                    "load and deficit go to the first one"
                )
            shares = [(uid_of(demands[0]) or 0, 1.0)]
        for du, share in shares:
            self.add_blocks("Demand", "load_sol", du, served * share, add=True)
            self.add_blocks("Demand", "fail_sol", du, fail * share, add=True)

    def set_volumes(self, uid: int, vini: np.ndarray | None, vfin: np.ndarray) -> None:
        """Stage volumes ``(n_scenarios, n_stages)`` of a reservoir.

        Writes ``Reservoir/efin_sol`` / ``eini_sol`` (``vini`` defaults to
        the previous stage's ``vfin``, the first stage's to the planning
        ``eini``) and ``Reservoir/energy_sol``: the end-of-block volume,
        linear in time between the stage's initial and final volume (the
        last block of a stage is exact, inner blocks an estimate).
        """
        g = self.grid
        vfin = np.asarray(vfin, dtype=float)
        if vini is None:
            vini = np.empty_like(vfin)
            vini[:, 1:] = vfin[:, :-1]
            res = next(
                (r for r in self.index.elements("reservoir_array") if uid_of(r) == uid),
                {},
            )
            e0 = res.get("eini")
            vini[:, 0] = float(e0) if isinstance(e0, (int, float)) else vfin[:, 0]
        vini = np.asarray(vini, dtype=float)
        self.add_stages("Reservoir", "efin_sol", uid, vfin)
        self.add_stages("Reservoir", "eini_sol", uid, vini)
        dur = np.asarray(g.durations, dtype=float)
        out = self.zeros()
        for k, st in enumerate(g.stages):
            blocks = list(st.blocks)
            if not blocks:
                continue
            hours = np.cumsum(dur[blocks])
            frac = hours / hours[-1] if hours[-1] > 0 else np.ones_like(hours)
            v0, v1 = vini[:, [k]], vfin[:, [k]]
            out[:, blocks] = v0 + (v1 - v0) * frac[None, :]
        self.add_blocks("Reservoir", "energy_sol", uid, out)

    def estimate_cost(self) -> None:
        """Operating cost when the source reports none.

        ``generation x gcost + deficit x fail cost`` (the demand's
        ``fcost`` or ``model_options.demand_fail_cost``) x block hours x
        stage discount factor, from the imported streams and the planning.
        """
        if self.cost is not None:
            return
        # pylint: disable=import-outside-toplevel
        from gtopt_shared.case_reader import GtoptCase

        case = GtoptCase(self.planning, self.case_dir or Path())
        g = self.grid
        hours = np.asarray(g.durations, dtype=float) * np.asarray(
            [g.stages[int(k)].discount_factor for k in g.stage_of_block()], dtype=float
        )
        cost = self.zeros()
        gens = self.block_data.get(("Generator", "generation_sol"), {})
        for gen in case.elements("generator_array"):
            p = gens.get(uid_of(gen) or -1)
            if p is not None:
                cost += p * np.nan_to_num(case.field("Generator", gen, "gcost", 0.0))
        model = (self.planning.get("options", {}) or {}).get("model_options", {}) or {}
        dfc = float(model.get("demand_fail_cost", 0.0) or 0.0)
        fails = self.block_data.get(("Demand", "fail_sol"), {})
        for dem in case.elements("demand_array"):
            f = fails.get(uid_of(dem) or -1)
            if f is not None:
                cost += f * np.nan_to_num(
                    case.field("Demand", dem, "fcost", dfc), nan=dfc
                )
        self.cost = cost * hours[None, :]
        self.warn(
            f"{self.source}: no operating cost in the results; obj_value estimated "
            "from generation x gcost + deficit x fail cost"
        )

    # ── layout ─────────────────────────────────────────────────────────
    @property
    def options(self) -> Mapping[str, Any]:
        """The planning's ``options``."""
        return self.planning.get("options", {}) or {}

    def _round_decimals(self) -> int:
        v = self.options.get("output_round_decimals")
        return DEFAULT_ROUND_DECIMALS if v is None else int(v)

    def _compression(self) -> str:
        return str(self.options.get("output_compression") or DEFAULT_COMPRESSION)

    def _format(self) -> str:
        fmt = str(self.options.get("output_format") or "parquet")
        return "csv" if fmt == "csv" else "parquet"

    def _table(
        self,
        cell: Cell,
        series: Mapping[int, np.ndarray],
        per_stage: bool,
        keep_zeros: bool,
    ) -> pa.Table:
        g = self.grid
        cols: dict[str, list[int]] = {c: [] for c in _SCHEMA_IDS}
        vals: list[float] = []
        stages = [g.stages[k] for k in cell.stages]
        for uid, arr in series.items():
            for s in cell.scenarios:
                su = g.scenario_uids[s]
                for k, st in zip(cell.stages, stages, strict=True):
                    slots = (
                        [(k, 0)]
                        if per_stage
                        else [(b, g.block_uids[b]) for b in st.blocks]
                    )
                    for col, bu in slots:
                        v = float(arr[s, col])
                        if np.isnan(v) or (v == 0.0 and not keep_zeros):
                            continue
                        cols["scenario"].append(su)
                        cols["stage"].append(st.uid)
                        cols["block"].append(bu)
                        cols["uid"].append(int(uid))
                        vals.append(v)
        digits = self._round_decimals()
        arrays = [pa.array(cols[c], type=pa.int32()) for c in _SCHEMA_IDS]
        if 1 <= digits <= 7:
            arrays.append(
                pa.array(
                    np.round(np.asarray(vals, dtype=float), digits).astype(np.float32)
                )
            )
        elif digits >= 8:
            arrays.append(pa.array(np.round(np.asarray(vals, dtype=float), digits)))
        else:
            arrays.append(pa.array(np.asarray(vals, dtype=float)))
        return pa.Table.from_arrays(arrays, names=[*_SCHEMA_IDS, "value"])

    def _write_table(self, table: pa.Table, cls: str, fld: str, cell: Cell) -> Path:
        base = Path(self._out) / cls
        if self._format() == "parquet":
            part = (
                base
                / f"{fld}.parquet"
                / f"scene={cell.scene_uid}"
                / f"phase={cell.phase_uid}"
            )
            part.mkdir(parents=True, exist_ok=True)
            path = part / "part.parquet"
            comp = self._compression()
            pq.write_table(
                table,
                path,
                compression=None if comp in ("", "none", "uncompressed") else comp,
                use_dictionary=list(_SCHEMA_IDS),
                column_encoding={"value": "BYTE_STREAM_SPLIT"},
                write_statistics=False,
            )
            return path
        base.mkdir(parents=True, exist_ok=True)
        comp = self._compression()
        ext = {"zstd": ".csv.zst", "gzip": ".csv.gz"}.get(comp, ".csv")
        path = base / f"{fld}_s{cell.scene_uid}_p{cell.phase_uid}{ext}"
        if ext == ".csv":
            pacsv.write_csv(table, path)
        else:
            with pa.CompressedOutputStream(str(path), comp) as out:
                pacsv.write_csv(table, out)
        return path

    def _cell_cost(self, cell: Cell) -> float:
        """Objective of a (scene, phase) cell, as gtopt reports it.

        The probability-weighted cost of the scene's scenarios (gtopt's
        global probability weights, so the cells of a phase add up to the
        expected cost) over the phase's stages and every later stage -- the
        phase LP objective including its future-cost term in a phased
        (SDDP) planning, the whole horizon otherwise.
        """
        if self.cost is None:
            return 0.0
        g = self.grid
        probs = np.asarray(g.probabilities, dtype=float)
        total = probs.sum()
        w = probs / total if total > 0 else np.full(g.n_scenarios, 1.0 / g.n_scenarios)
        first = min(cell.stages, default=0)
        blocks = [b for st in g.stages[first:] for b in st.blocks]
        return float(sum(w[s] * self.cost[s, blocks].sum() for s in cell.scenarios))

    def _expand_batteries(self) -> None:
        """Add the elements gtopt synthesises for a battery with a ``bus``.

        Mirrors ``System::expand_batteries``: ``<battery>_gen`` (discharge,
        ``Generator/generation_sol = Battery/fout_sol``), ``<battery>_dem``
        (charge, ``Demand/load_sol = Battery/finp_sol``) and the
        ``<battery>_conv`` converter, with uids after the largest of each
        array, so ``planning.json`` and the streams match a gtopt run.
        """
        system = self.planning.setdefault("system", {})

        def next_uid(array: str) -> int:
            uids = [uid_of(e) for e in system.get(array, []) or []]
            return max((u for u in uids if u is not None), default=0) + 1

        gen_uid = next_uid("generator_array")
        dem_uid = next_uid("demand_array")
        conv_uid = next_uid("converter_array")
        for bat in list(system.get("battery_array", []) or []):
            if bat.get("bus") is None:
                continue
            name = str(bat.get("name", f"battery{bat.get('uid')}"))
            gname, dname = f"{name}_gen", f"{name}_dem"
            if self.index.find("generator_array", gname) is not None:
                continue  # already expanded (a gtopt planning.json)
            system.setdefault("generator_array", []).append(
                {"uid": gen_uid, "name": gname, "bus": bat["bus"]}
            )
            system.setdefault("demand_array", []).append(
                {"uid": dem_uid, "name": dname, "bus": bat["bus"]}
            )
            system.setdefault("converter_array", []).append(
                {
                    "uid": conv_uid,
                    "name": f"{name}_conv",
                    "battery": name,
                    "generator": gname,
                    "demand": dname,
                }
            )
            bu = uid_of(bat)
            if bu is not None:
                fout = self.get_blocks("Battery", "fout_sol", bu)
                finp = self.get_blocks("Battery", "finp_sol", bu)
                if fout is not None:
                    self.add_blocks("Generator", "generation_sol", gen_uid, fout)
                if finp is not None:
                    self.add_blocks("Demand", "load_sol", dem_uid, finp)
            gen_uid, dem_uid, conv_uid = gen_uid + 1, dem_uid + 1, conv_uid + 1
        self.index = ElementIndex(self.planning)

    def write(self, out_dir: str | Path) -> list[Path]:
        """Write the gtopt output directory; returns the files written."""
        self._out = Path(out_dir)
        self._out.mkdir(parents=True, exist_ok=True)
        self._expand_batteries()
        cells = planning_cells(self.planning, self.grid)
        files: list[Path] = []
        for store, per_stage in ((self.block_data, False), (self.stage_data, True)):
            for (cls, fld), series in sorted(store.items()):
                if not series:
                    continue
                keep = fld.endswith("_dual")
                for cell in cells:
                    files.append(
                        self._write_table(
                            self._table(cell, series, per_stage, keep), cls, fld, cell
                        )
                    )
        files += self._write_run_files(cells)
        self.files = files
        return files

    def _write_run_files(self, cells: list[Cell]) -> list[Path]:
        out = self._out
        sol = out / "solution.csv"
        with sol.open("w", encoding="utf-8", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(SOLUTION_HEADER)
            for cell in cells:
                wr.writerow(
                    [
                        cell.scene_uid,
                        cell.phase_uid,
                        0,
                        "optimal",
                        repr(self._cell_cost(cell)),
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        1,
                        f"imported-{self.source}",
                        self.source,
                    ]
                )
        status = out / "solver_status.json"
        status.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "done",
                    "method": "imported",
                    "source": self.source,
                    "solver": self.source,
                    "total_scenes": len({c.scene_uid for c in cells}),
                    "scenes_done": len({c.scene_uid for c in cells}),
                    "warnings": self.warnings,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        plan = out / "planning.json"
        plan.write_text(json.dumps(self.planning, indent=2) + "\n", encoding="utf-8")
        readme = out / "README.md"
        streams = sorted({*self.block_data, *self.stage_data})
        readme.write_text(
            "# gtopt output directory (imported)\n\n"
            f"Results of a {self.source} run, imported by `{self.source}2gtopt "
            "--results` in the layout of a gtopt run of the converted case "
            "(`planning.json`).  Readers of gtopt output (gtopt_results_summary, "
            "gtopt_compare_runs, run_gtopt) load it unchanged.\n\n"
            "Streams:\n\n"
            + "".join(f"- `{c}/{f}`\n" for c, f in streams)
            + (
                "\nWarnings:\n\n" + "".join(f"- {w}\n" for w in self.warnings)
                if self.warnings
                else ""
            ),
            encoding="utf-8",
        )
        return [sol, status, plan, readme]


def find_planning_json(case_dir: Path) -> Path:
    """The planning JSON a converter wrote into ``case_dir``.

    ``<case_dir>/<case_dir.name>.json`` when it exists, else the only
    ``*.json`` there holding a ``system`` block.
    """
    own = case_dir / f"{case_dir.name}.json"
    if own.is_file():
        return own
    found = []
    for p in sorted(case_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "system" in data and "simulation" in data:
            found.append(p)
    if len(found) != 1:
        raise FileNotFoundError(
            f"{case_dir}: expected one planning JSON, found {len(found)}"
        )
    return found[0]


def load_planning(path: Path) -> dict[str, Any]:
    """Read a planning JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a planning JSON object")
    return data


def per_hour_to_mw(gwh: np.ndarray, durations: Iterable[float]) -> np.ndarray:
    """Block energy [GWh] -> mean power [MW] (``GWh * 1000 / hours``)."""
    dur = np.asarray(list(durations), dtype=float)
    return np.where(
        dur > 0, np.asarray(gwh, dtype=float) * 1e3 / np.where(dur > 0, dur, 1.0), 0.0
    )


# ── converter CLI glue ─────────────────────────────────────────────────
#: ``--results`` given without a path: the converter's default location.
RESULTS_AUTO = "auto"


def add_results_arguments(
    parser: argparse.ArgumentParser, *, source: str, what: str, auto: str
) -> None:
    """Add ``--results [DIR]``, ``--results-only`` and ``--results-output DIR``.

    ``what`` names the source files read, ``auto`` where ``--results``
    without a path looks for them.
    """
    grp = parser.add_argument_group(
        "results import",
        f"Import the results of a {source} run as a gtopt output directory "
        "(same Parquet layout, uids and units as a gtopt run of the converted "
        "case), to compare them with gtopt runs (gtopt_compare_runs).",
    )
    grp.add_argument(
        "--results",
        nargs="?",
        const=RESULTS_AUTO,
        default=None,
        metavar="PATH",
        help=f"{source} results to import ({what}); without PATH: {auto}",
    )
    grp.add_argument(
        "--results-only",
        action="store_true",
        help=(
            "with --results: do not convert the case, import the results "
            "against the planning JSON already in the output directory"
        ),
    )
    grp.add_argument(
        "--results-output",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "directory of the imported results "
            f"(default: <output dir>/results_{source.lower()})"
        ),
    )


def results_output_dir(case_dir: Path, source: str, explicit: Path | None) -> Path:
    """``--results-output`` or ``<case_dir>/results_<source>``."""
    return Path(explicit) if explicit is not None else case_dir / f"results_{source}"


def report_import(res: ImportedResults, out_dir: Path) -> None:
    """Print the one-line summary of an import."""
    streams = len({*res.block_data, *res.stage_data})
    print(
        f"imported {res.source} results: {streams} stream(s) -> {out_dir} "
        f"({len(res.warnings)} warning(s))"
    )
