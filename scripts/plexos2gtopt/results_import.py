# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Import a PLEXOS solution as a gtopt output directory.

``plexos2gtopt --results PATH`` reads a PLEXOS solution through
:class:`plexos2gtopt.plexos_solution.PlexosSolution` -- a solution
``.accdb`` (``mdb-export``), a CEN ``RES*.zip[.xz]`` bundle (its nested
``.accdb``) or a directory of ``t_*.csv`` tables (the form
``gtopt2plexos --results`` writes) -- and lays it out as a gtopt run of
the converted case would (:class:`gtopt_shared.results_writer.ImportedResults`).

=================================== =====================================
PLEXOS System collection property    gtopt stream
=================================== =====================================
``Generator.Generation`` [MW]        ``Generator/generation_sol``
``Node.Price`` [$/MWh]               ``Bus/balance_dual``
``Node.Load`` - ``Unserved Energy``  ``Demand/load_sol`` [MW]
``Node.Unserved Energy`` [MW]        ``Demand/fail_sol``
``Line.Flow`` [MW]                   ``Line/flow_sol``
``Line.Loss`` [MW]                   ``Line/loss_sol``
``Storage.End Volume``               ``Reservoir/energy_sol``,
                                     ``efin_sol`` (last block of a stage)
``Storage.Initial Volume``           ``Reservoir/eini_sol`` (first stage)
``Storage.Shadow Price``             ``Reservoir/water_value_dual``
``Battery.Energy`` [MWh]             ``Battery/energy_sol``
``Battery.Generation`` / ``Load``    ``Battery/fout_sol`` / ``finp_sol``
=================================== =====================================

plexos2gtopt keeps PLEXOS object names and units (reservoir volumes stay
in the dataset's volume unit, e.g. CMD), so objects are matched by name
and values are copied as they are.  PLEXOS period ``p`` (the ST block) is
the ``p``-th block of the case; a solution with a different number of
periods is truncated / zero-filled with a warning.  Scenario ``s`` of a
multi-scenario case reads sample ``s`` when the solution holds it, else
the sample-0 mean (with a warning).  No operating cost is read, so
``solution.csv`` gets the cost estimated from generation x ``gcost`` plus
deficit x fail cost (a warning says so).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.results_writer import ImportedResults, uid_of

from .plexos_solution import PlexosSolution, SeriesKey, csv_table_source

logger = logging.getLogger(__name__)

SOURCE = "plexos"

#: (PLEXOS class, property) pairs read.
WANTED: tuple[tuple[str, str], ...] = (
    ("Generator", "Generation"),
    ("Node", "Price"),
    ("Node", "Load"),
    ("Node", "Unserved Energy"),
    ("Line", "Flow"),
    ("Line", "Loss"),
    ("Storage", "End Volume"),
    ("Storage", "Initial Volume"),
    ("Storage", "Shadow Price"),
    ("Battery", "Energy"),
    ("Battery", "Generation"),
    ("Battery", "Load"),
)

Series = dict[tuple[str, str], dict[str, np.ndarray]]


def open_results(path: Path) -> PlexosSolution | None:
    """A :class:`PlexosSolution` over ``path`` (``.accdb``, RES zip, CSV dir)."""
    path = Path(path)
    if path.is_dir():
        tables = {
            p.stem: p.read_text(encoding="utf-8") for p in sorted(path.glob("t_*.csv"))
        }
        if not tables:
            raise FileNotFoundError(f"{path}: no PLEXOS solution tables (t_*.csv)")
        return PlexosSolution(csv_table_source(tables))
    if not path.is_file():
        raise FileNotFoundError(f"{path}: no such PLEXOS solution")
    name = path.name.lower()
    if name.endswith((".zip", ".zip.xz")):
        # pylint: disable=import-outside-toplevel
        from .plexos_block_layout import extract_accdb_from_res_zip

        accdb = extract_accdb_from_res_zip(path)
        if accdb is None:
            raise FileNotFoundError(f"{path}: no solution .accdb inside")
        path = Path(accdb)
    return PlexosSolution.from_accdb(path)


class PlexosResultsImporter:
    """Fill an :class:`ImportedResults` from a PLEXOS solution."""

    def __init__(
        self,
        solution: PlexosSolution,
        planning: dict[str, Any],
        case_dir: Path | None = None,
    ) -> None:
        """Bind the solution and the converted planning."""
        self.sol = solution
        self.res = ImportedResults(planning, SOURCE, case_dir=case_dir)

    def warn(self, msg: str) -> None:
        """Record a warning."""
        self.res.warn(msg)

    def _series(self) -> Series:
        """``(class, property) -> {object: (n_scen, n_blocks)}``."""
        g = self.res.grid
        mean = self.sol.fetch(WANTED)
        per_sample: list[dict[SeriesKey, dict[int, float]]] = []
        if g.n_scenarios > 1:
            per_sample = [
                self.sol.fetch(WANTED, str(s + 1)) for s in range(g.n_scenarios)
            ]
            if not any(per_sample):
                self.warn(
                    f"{SOURCE}: the solution has no per-sample values; every "
                    "scenario gets the sample-0 mean"
                )
                per_sample = []
        n_periods = max(
            (max(v, default=0) for d in [mean, *per_sample] for v in d.values()),
            default=0,
        )
        if n_periods and n_periods != g.n_blocks:
            self.warn(
                f"{SOURCE}: the solution has {n_periods} periods, the case "
                f"{g.n_blocks} blocks; periods are matched to blocks in order"
            )
        out: Series = {}
        keys = set(mean) | {k for d in per_sample for k in d}
        for key in keys:
            arr = self.res.zeros()
            for s in range(g.n_scenarios):
                src = per_sample[s].get(key) if per_sample else None
                values = src if src is not None else mean.get(key, {})
                for period, v in values.items():
                    if 1 <= period <= g.n_blocks:
                        arr[s, period - 1] = v
            out.setdefault((key.class_name, key.property_name), {})[key.object_name] = (
                arr
            )
        return out

    def run(self) -> ImportedResults:
        """Read the solution; returns the filled results."""
        series = self._series()
        if not series:
            raise ValueError("the PLEXOS solution holds none of the read properties")
        self._simple(
            series,
            ("Generator", "Generation"),
            "generator_array",
            "Generator",
            "generation_sol",
        )
        self._simple(series, ("Line", "Flow"), "line_array", "Line", "flow_sol")
        self._simple(series, ("Line", "Loss"), "line_array", "Line", "loss_sol")
        self._simple(series, ("Node", "Price"), "bus_array", "Bus", "balance_dual")
        for prop, fld in (
            ("Energy", "energy_sol"),
            ("Generation", "fout_sol"),
            ("Load", "finp_sol"),
        ):
            self._simple(series, ("Battery", prop), "battery_array", "Battery", fld)
        self._loads(series)
        self._storages(series)
        self.res.estimate_cost()
        return self.res

    def _simple(
        self,
        series: Series,
        key: tuple[str, str],
        array: str,
        cls: str,
        fld: str,
    ) -> None:
        for name, arr in series.get(key, {}).items():
            uid = uid_of(self.res.index.find(array, name))
            if uid is None:
                self.warn(f"{SOURCE}: {key[0]} {name} is not in the case ({cls})")
                continue
            self.res.add_blocks(cls, fld, uid, arr)

    def _loads(self, series: Series) -> None:
        r = self.res
        load = series.get(("Node", "Load"), {})
        use = series.get(("Node", "Unserved Energy"), {})
        for name in sorted({*load, *use}):
            bus = r.index.find("bus_array", name)
            if bus is None:
                self.warn(f"{SOURCE}: Node {name} is not in the case")
                continue
            f = use.get(name, r.zeros())
            r.assign_bus_load(bus, load.get(name, f) - f, f)

    def _storages(self, series: Series) -> None:
        r = self.res
        g = r.grid
        end = series.get(("Storage", "End Volume"), {})
        ini = series.get(("Storage", "Initial Volume"), {})
        price = series.get(("Storage", "Shadow Price"), {})
        last = [st.first_block + st.count_block - 1 for st in g.stages]
        for name in sorted({*end, *price}):
            res = r.index.find("reservoir_array", name)
            ru = uid_of(res)
            if res is None or ru is None:
                self.warn(f"{SOURCE}: Storage {name} is not a reservoir of the case")
                continue
            if name in price:
                r.add_blocks("Reservoir", "water_value_dual", ru, price[name])
            if name not in end:
                continue
            vol = end[name]
            vfin = vol[:, last]
            vini = np.empty_like(vfin)
            vini[:, 1:] = vfin[:, :-1]
            vini[:, 0] = self._initial(res, ini.get(name), vol)
            r.add_blocks("Reservoir", "energy_sol", ru, vol)
            r.add_stages("Reservoir", "efin_sol", ru, vfin)
            r.add_stages("Reservoir", "eini_sol", ru, vini)

    @staticmethod
    def _initial(
        res: Mapping[str, Any], ini: np.ndarray | None, vol: np.ndarray
    ) -> np.ndarray:
        if ini is not None:
            return ini[:, 0]
        e0 = res.get("eini")
        if isinstance(e0, (int, float)):
            return np.full(vol.shape[0], float(e0))
        return vol[:, 0]


def import_plexos_results(
    solution: PlexosSolution,
    planning: dict[str, Any],
    out_dir: Path,
    case_dir: Path | None = None,
) -> ImportedResults:
    """Import ``solution`` into ``out_dir`` (gtopt output)."""
    res = PlexosResultsImporter(solution, planning, case_dir).run()
    res.write(out_dir)
    return res
