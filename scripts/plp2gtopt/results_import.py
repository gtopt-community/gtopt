# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Import a PLP (CEN65) run's output files as a gtopt output directory.

``plp2gtopt --results DIR`` reads the CSV files PLP writes at the end of
a simulation and lays them out as a gtopt run of the converted case would
(:class:`gtopt_shared.results_writer.ImportedResults`), so PLP results
and gtopt results can be compared with the same tools.  The file formats
are those :mod:`gtopt2plp.results` writes (the CEN65 Fortran writers):

============== ============================== ==============================
PLP file       column                         gtopt stream
============== ============================== ==============================
``plpcen.csv`` ``CenPgen`` [MW]               ``Generator/generation_sol``
               ``CenQgen`` [m3/s] (hydro)     ``Turbine/flow_sol``
``plpfal.csv`` ``CenPgen`` [MW] per bus       ``Demand/fail_sol``
``plpbar.csv`` ``CMgBar`` [$/MWh]             ``Bus/balance_dual``
               ``DemBarP`` - deficit [MW]     ``Demand/load_sol``
``plplin.csv`` ``LinFluP`` [MW]               ``Line/flow_sol``
               ``LinPerP`` [MW]               ``Line/loss_sol``
``plpemb.csv`` ``EmbVini`` / ``EmbVfin``     ``Reservoir/eini_sol``,
               x ``EmbFac`` / 1e6 [hm3]       ``efin_sol`` (per stage),
                                              ``Reservoir/energy_sol``
               ``EmbQver`` [m3/s]             spill waterway
                                              (``<name>_ver*``) or
                                              ``Junction/drain_sol``
               ``EmbQFil`` [m3/s]             seepage waterway flow
               ``EmbPsom2`` [$/dam3] x 1000   ``Reservoir/water_value_dual``
                                              [$/hm3]
``plpess.csv`` ``Efin`` / ``DCar`` / ``GDes`` ``Battery/energy_sol`` /
                                              ``finp_sol`` / ``fout_sol``
               ``EfPSom`` [$/MWh]             ``Battery/energy_dual``
``plpcostop``  ``CostoOperActual`` [$]        ``solution.csv`` ``obj_value``
``.csv``                                      (probability-weighted)
============== ============================== ==============================

Matching: elements by name (``CenNom`` -> generator, ``BarNom`` -> bus,
``LinNom`` -> line, ``EmbNom`` -> reservoir, ``EssNom`` -> battery);
``Bloque`` is the block uid (plp2gtopt keeps PLP's block numbers);
``Hidro = "Sim N"`` is the scenario of simulation N's stage-1 hydrology
(``plpidsim.dat`` of the PLP case when it exists, else hydrology ``N``;
plp2gtopt stores the 0-based class in ``scenario.hydrology``), or the
N-th scenario when the scenarios carry no hydrology.  ``MEDIA`` rows and simulations not in the
case are skipped.  Unmatched names are reported as warnings.

The deficit and served load of a bus go to its demand; a bus with several
demands gets its served load split by their ``lmax`` share when it is
scalar, else on the first one (with a warning).  The PLP CSV files carry
two decimals (water values ``EmbPsom2`` two decimals of $/dam3), which
bounds the precision of the import.

PLP volumes are stage quantities (``EmbVini`` / ``EmbVfin`` repeat on
every block of a stage), while gtopt's ``Reservoir/energy_sol`` is the
volume at the end of each block: it is interpolated linearly in time
between the stage's initial and final volume, so the last block of a
stage is the PLP final volume exactly and the inner blocks an estimate.
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.compressed_open import compressed_open, find_compressed_path
from gtopt_shared.results_writer import ImportedResults, uid_of

logger = logging.getLogger(__name__)

SOURCE = "plp"
_SIM = re.compile(r"^\s*sim\s*(\d+)\s*$", re.IGNORECASE)

#: PLP output files read (the first found variant wins: plain or compressed).
RESULT_FILES = (
    "plpcen.csv",
    "plpfal.csv",
    "plpbar.csv",
    "plplin.csv",
    "plpemb.csv",
    "plpess.csv",
    "plpcostop.csv",
)


def _find(results_dir: Path, name: str) -> Path | None:
    return find_compressed_path(results_dir / name)


def read_plp_csv(path: Path) -> Iterator[dict[str, str]]:
    """Rows of a PLP output CSV as header-keyed, stripped strings."""
    with compressed_open(path, encoding="latin-1") as fh:
        rd = csv.reader(fh)
        head = [h.strip() for h in next(rd, [])]
        for row in rd:
            if not row or not any(c.strip() for c in row):
                continue
            yield {h: c.strip() for h, c in zip(head, row, strict=False) if h}


def _sim_hydrologies(input_dir: Path) -> dict[int, int]:
    """``{simulation (1-based): stage-1 hydrology (1-based)}`` of plpidsim.dat."""
    path = find_compressed_path(Path(input_dir) / "plpidsim.dat")
    if path is None:
        return {}
    # pylint: disable=import-outside-toplevel
    from .idsim_parser import IdSimParser

    parser = IdSimParser(path)
    parser.parse()
    out: dict[int, int] = {}
    for sim in range(parser.num_simulations):
        h = parser.get_index(sim, 1)
        if h is not None:
            out[sim + 1] = int(h)
    return out


def _num(row: Mapping[str, str], key: str) -> float:
    try:
        return float(row.get(key, "") or 0.0)
    except ValueError:
        return float("nan")


class PlpResultsImporter:
    """Fill an :class:`ImportedResults` from a PLP output directory."""

    def __init__(
        self,
        results_dir: Path,
        planning: dict[str, Any],
        input_dir: Path | None = None,
    ) -> None:
        """Bind the PLP output directory and the converted planning.

        ``input_dir`` (the PLP case) supplies ``plpidsim.dat``, PLP's
        simulation -> hydrology map, when the case has one.
        """
        self.dir = Path(results_dir)
        self.res = ImportedResults(planning, SOURCE)
        g = self.res.grid
        hydro_pos = {
            int(h) + 1: k for k, h in enumerate(g.hydrologies) if h is not None
        }
        sim_hydro = _sim_hydrologies(input_dir) if input_dir is not None else {}
        if sim_hydro and hydro_pos:
            self.sim_pos = {
                sim: hydro_pos[h] for sim, h in sim_hydro.items() if h in hydro_pos
            }
        elif hydro_pos:
            self.sim_pos = hydro_pos
        else:
            self.sim_pos = {k + 1: k for k in range(g.n_scenarios)}
        uid_pos = {u: k for k, u in enumerate(g.block_uids)}
        self.block_pos = uid_pos
        self._skipped_sims: set[str] = set()

    # ── helpers ────────────────────────────────────────────────────────
    def warn(self, msg: str) -> None:
        """Record a warning."""
        self.res.warn(msg)

    def _cell(self, row: Mapping[str, str], block_key: str) -> tuple[int, int] | None:
        """``(scenario position, block position)`` of a row, or ``None``."""
        hid = row.get("Hidro", "")
        m = _SIM.match(hid)
        if m is None:
            return None  # MEDIA and other summary rows
        s = self.sim_pos.get(int(m.group(1)))
        if s is None:
            self._skipped_sims.add(hid)
            return None
        try:
            b = self.block_pos.get(int(float(row.get(block_key, ""))))
        except ValueError:
            return None
        if b is None:
            self.warn(f"{SOURCE}: block {row.get(block_key)} is not in the case")
            return None
        return s, b

    def _rows(self, name: str) -> Iterator[dict[str, str]]:
        path = _find(self.dir, name)
        if path is None:
            logger.info("%s: %s not found, skipped", self.dir, name)
            return
        yield from read_plp_csv(path)

    def _collect(
        self, name: str, key: str, cols: tuple[str, ...], block_key: str = "Bloque"
    ) -> dict[str, dict[str, np.ndarray]]:
        """``{element name: {column: (n_scen, n_blocks)}}`` of one file."""
        out: dict[str, dict[str, np.ndarray]] = {}
        for row in self._rows(name):
            cell = self._cell(row, block_key)
            if cell is None:
                continue
            el = row.get(key, "")
            per = out.setdefault(el, {c: self.res.zeros() for c in cols})
            for c in cols:
                if c in row:
                    per[c][cell] = _num(row, c)
        return out

    # ── sections ───────────────────────────────────────────────────────
    def run(self) -> ImportedResults:
        """Read every PLP output file found; returns the filled results."""
        if not any(_find(self.dir, f) for f in RESULT_FILES):
            raise FileNotFoundError(
                f"{self.dir}: no PLP output files ({', '.join(RESULT_FILES)})"
            )
        self._centrals()
        fail = self._fail()
        self._buses(fail)
        self._lines()
        self._reservoirs()
        self._ess()
        self._costop()
        if self._skipped_sims:
            self.warn(
                f"{SOURCE}: simulations not in the case skipped: "
                + ", ".join(sorted(self._skipped_sims))
            )
        return self.res

    def _centrals(self) -> None:
        r = self.res
        idx = r.index
        data = self._collect("plpcen.csv", "CenNom", ("CenPgen", "CenQgen"))
        tips: dict[str, str] = {}
        for row in self._rows("plpcen.csv"):
            tips.setdefault(row.get("CenNom", ""), row.get("CenTip", ""))
        for name, cols in data.items():
            tip = tips.get(name, "")
            if tip.upper() == "BAT":
                bat = idx.find("battery_array", name)
                if bat is not None and uid_of(bat) is not None:
                    r.add_blocks(
                        "Battery", "fout_sol", uid_of(bat) or 0, cols["CenPgen"]
                    )
                    continue
            gen = idx.find("generator_array", name)
            gu = uid_of(gen)
            if gen is None or gu is None:
                self.warn(
                    f"{SOURCE}: central {name} ({tip}) has no generator in the case"
                )
                continue
            r.add_blocks("Generator", "generation_sol", gu, cols["CenPgen"])
            if tip in ("Emb", "Ser", "Pas"):
                tur = self._turbine_of(gen)
                if tur is not None:
                    r.add_blocks("Turbine", "flow_sol", tur, cols["CenQgen"])

    def _turbine_of(self, gen: Mapping[str, Any]) -> int | None:
        refs = {gen.get("uid"), gen.get("name")} - {None}
        for t in self.res.index.elements("turbine_array"):
            if t.get("generator") in refs:
                return uid_of(t)
        return None

    def _fail(self) -> dict[str, np.ndarray]:
        """Deficit [MW] per bus name (``plpfal.csv``)."""
        out: dict[str, np.ndarray] = {}
        for row in self._rows("plpfal.csv"):
            cell = self._cell(row, "Bloque")
            if cell is None:
                continue
            bus = row.get("BarNom", "")
            out.setdefault(bus, self.res.zeros())[cell] += _num(row, "CenPgen")
        return out

    def _buses(self, fail: dict[str, np.ndarray]) -> None:
        r = self.res
        idx = r.index
        data = self._collect("plpbar.csv", "BarNom", ("CMgBar", "DemBarP"))
        for name in set(fail) - set(data):
            data[name] = {"CMgBar": r.zeros(), "DemBarP": fail[name]}
            self.warn(f"{SOURCE}: deficit at bus {name} without a plpbar.csv row")
        for name, cols in data.items():
            bus = idx.find("bus_array", name)
            bu = uid_of(bus)
            if bus is None or bu is None:
                self.warn(f"{SOURCE}: bus {name} is not in the case")
                continue
            if "CMgBar" in cols and _find(self.dir, "plpbar.csv") is not None:
                r.add_blocks("Bus", "balance_dual", bu, cols["CMgBar"])
            f = fail.get(name, r.zeros())
            r.assign_bus_load(bus, cols["DemBarP"] - f, f)

    def _lines(self) -> None:
        r = self.res
        data = self._collect("plplin.csv", "LinNom", ("LinFluP", "LinPerP"))
        for name, cols in data.items():
            ln = r.index.find("line_array", name)
            lu = uid_of(ln)
            if lu is None:
                self.warn(f"{SOURCE}: line {name} is not in the case")
                continue
            r.add_blocks("Line", "flow_sol", lu, cols["LinFluP"])
            if np.any(cols["LinPerP"]):
                r.add_blocks("Line", "loss_sol", lu, cols["LinPerP"])

    def _spill_target(
        self, name: str, res: Mapping[str, Any]
    ) -> tuple[str, int] | None:
        idx = self.res.index
        jref = res.get("junction")
        prefix = f"{name}_ver".casefold()
        ver = [
            w
            for w in idx.elements("waterway_array")
            if str(w.get("name", "")).casefold().startswith(prefix)
            and (jref is None or w.get("junction_a") == jref)
        ]
        if len(ver) == 1 and uid_of(ver[0]) is not None:
            return "Waterway", uid_of(ver[0]) or 0
        j = idx.ref("junction_array", jref)
        if j is not None and uid_of(j) is not None:
            return "Junction", uid_of(j) or 0
        return None

    def _seepage_target(self, res: Mapping[str, Any]) -> int | None:
        idx = self.res.index
        refs = {res.get("uid"), res.get("name")} - {None}
        for s in idx.elements("reservoir_seepage_array"):
            if s.get("reservoir") in refs:
                return uid_of(idx.ref("waterway_array", s.get("waterway")))
        return None

    def _reservoirs(self) -> None:
        r = self.res
        g = r.grid
        cols = (
            "EmbFac",
            "EmbVini",
            "EmbVfin",
            "EmbQgen",
            "EmbQver",
            "EmbPsom2",
            "EmbQFil",
        )
        data = self._collect("plpemb.csv", "EmbNom", cols)
        first = [st.first_block for st in g.stages]
        last = [st.first_block + st.count_block - 1 for st in g.stages]
        for name, c in data.items():
            res = r.index.find("reservoir_array", name)
            ru = uid_of(res)
            if res is None or ru is None:
                self.warn(f"{SOURCE}: reservoir {name} is not in the case")
                continue
            fac = np.where(c["EmbFac"] > 0, c["EmbFac"], 1.0e6) / 1.0e6  # -> hm3
            vfin = c["EmbVfin"] * fac
            vini = c["EmbVini"] * fac
            r.set_volumes(ru, vini[:, first], vfin[:, last])
            r.add_blocks("Reservoir", "water_value_dual", ru, c["EmbPsom2"] * 1.0e3)
            gen = r.index.find("generator_array", name)
            tur = self._turbine_of(gen) if gen is not None else None
            if tur is not None and r.get_blocks("Turbine", "flow_sol", tur) is None:
                r.add_blocks("Turbine", "flow_sol", tur, c["EmbQgen"])
            if np.any(c["EmbQver"]):
                tgt = self._spill_target(name, res)
                if tgt is None:
                    self.warn(
                        f"{SOURCE}: reservoir {name}: spill has no waterway/junction"
                    )
                else:
                    fld = "flow_sol" if tgt[0] == "Waterway" else "drain_sol"
                    r.add_blocks(tgt[0], fld, tgt[1], c["EmbQver"], add=True)
            if np.any(c["EmbQFil"]):
                su = self._seepage_target(res)
                if su is None:
                    self.warn(f"{SOURCE}: reservoir {name}: seepage has no waterway")
                else:
                    r.add_blocks("Waterway", "flow_sol", su, c["EmbQFil"], add=True)

    def _ess(self) -> None:
        r = self.res
        data = self._collect("plpess.csv", "EssNom", ("Efin", "DCar", "GDes", "EfPSom"))
        for name, c in data.items():
            bat = r.index.find("battery_array", name)
            bu = uid_of(bat)
            if bu is None:
                self.warn(f"{SOURCE}: ESS {name} has no battery in the case")
                continue
            r.add_blocks("Battery", "energy_sol", bu, c["Efin"])
            r.add_blocks("Battery", "finp_sol", bu, c["DCar"])
            r.add_blocks("Battery", "fout_sol", bu, c["GDes"])
            r.add_blocks("Battery", "energy_dual", bu, c["EfPSom"])

    def _costop(self) -> None:
        cost = self.res.zeros()
        found = False
        for row in self._rows("plpcostop.csv"):
            cell = self._cell(row, "IBlo")
            if cell is None:
                continue
            cost[cell] += _num(row, "CostoOperActual")
            found = True
        if found:
            self.res.cost = cost


def import_plp_results(
    results_dir: Path,
    planning: dict[str, Any],
    out_dir: Path,
    input_dir: Path | None = None,
) -> ImportedResults:
    """Import ``results_dir`` (PLP output) into ``out_dir`` (gtopt output)."""
    res = PlpResultsImporter(results_dir, planning, input_dir).run()
    res.write(out_dir)
    return res
