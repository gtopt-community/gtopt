# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Import a PSR SDDP run's result files as a gtopt output directory.

``sddp2gtopt --results DIR`` reads the PSR "graf" CSV result files and
lays them out as a gtopt run of the converted case would
(:class:`gtopt_shared.results_writer.ImportedResults`).  Each graf file
has a short header and a ``Stag,Seq.,Blck,<agent>...`` row, then one row
per stage x series x block (the layout :mod:`gtopt2sddp.results` writes):

============== ========= ============================================
file           unit      gtopt stream
============== ========= ============================================
``gerter.csv`` GWh       ``Generator/generation_sol`` [MW] (thermal)
``gergnd.csv`` GWh       ``Generator/generation_sol`` [MW] (renewable)
``gerhid.csv`` GWh       ``Generator/generation_sol`` [MW] (hydro)
``gerbat.csv`` GWh       ``Battery/fout_sol`` [MW]
``cmgbus.csv`` $/MWh     ``Bus/balance_dual``
``demxba.csv`` GWh       ``Demand/load_sol`` (bus demand - deficit)
``defbus.csv`` GWh       ``Demand/fail_sol`` [MW]
``cirflw.csv`` MW        ``Line/flow_sol``
``qturbi.csv`` m3/s      ``Turbine/flow_sol``
``qverti.csv`` m3/s      spill waterway (``s_<plant>``) or
                         ``Junction/drain_sol``
``volfin.csv`` hm3       ``Reservoir/efin_sol`` (per stage),
                         ``eini_sol``, ``energy_sol``
``valagu.csv`` k$/hm3    ``Reservoir/water_value_dual`` [$/hm3]
============== ========= ============================================

Energies [GWh] become mean power [MW] over the block hours of the case.
``Stag`` is the 1-based stage, ``Seq.`` the 1-based scenario (PSR series)
and ``Blck`` the block within the stage; per-stage files (``Varies per
block?,0``) are broadcast to the stage's blocks (``valagu``) or kept as
stage values (``volfin``).  Agents are matched by name with the prefixes
sddp2gtopt gives the hydro elements (``r_`` / ``rs_`` reservoir, ``tb_``
turbine, ``j_`` junction, ``s_`` spill, ``d_`` demand) and its ``_<code>``
line suffix.  PSR writes no operating cost in these files, so
``solution.csv`` gets the cost estimated from generation x ``gcost`` plus
deficit x fail cost (a warning says so).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.compressed_open import find_compressed_path, read_text
from gtopt_shared.results_writer import ImportedResults, uid_of

logger = logging.getLogger(__name__)

SOURCE = "sddp"

RESULT_FILES = (
    "gerter.csv",
    "gergnd.csv",
    "gerhid.csv",
    "gerbat.csv",
    "cmgbus.csv",
    "demxba.csv",
    "defbus.csv",
    "cirflw.csv",
    "qturbi.csv",
    "qverti.csv",
    "volfin.csv",
    "valagu.csv",
)


@dataclass
class Graf:
    """One PSR graf CSV: unit, per-block flag and ``agent -> {cell: value}``."""

    unit: str = ""
    per_block: bool = True
    agents: dict[str, dict[tuple[int, int, int], float]] = field(default_factory=dict)


def _float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return float("nan")


def read_graf(path: Path) -> Graf:
    """Parse a PSR graf CSV (see module doc)."""
    lines = read_text(path, encoding="latin-1", errors="replace").splitlines()
    out = Graf()
    head = None
    for i, ln in enumerate(lines):
        cells = [c.strip() for c in ln.split(",")]
        low = cells[0].lower() if cells else ""
        if low.startswith("varies per block"):
            out.per_block = len(cells) < 2 or cells[1] != "0"
            if "Unit" in cells:
                k = cells.index("Unit")
                out.unit = cells[k + 1] if k + 1 < len(cells) else ""
        elif low.startswith("stag"):
            head = i
            break
    if head is None:
        logger.warning("%s: no 'Stag' header row", path.name)
        return out
    names = [c.strip() for c in lines[head].split(",")][3:]
    for name in names:
        out.agents.setdefault(name, {})
    for ln in lines[head + 1 :]:
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) < 4 or not cells[0]:
            continue
        try:
            key = (int(cells[0]), int(cells[1]), int(cells[2]))
        except ValueError:
            continue
        for name, text in zip(names, cells[3:], strict=False):
            if text:
                out.agents[name][key] = _float(text)
    return out


class SddpResultsImporter:
    """Fill an :class:`ImportedResults` from PSR SDDP graf CSV files."""

    def __init__(
        self,
        results_dir: Path,
        planning: dict[str, Any],
        case_dir: Path | None = None,
    ) -> None:
        """Bind the result directory and the converted planning."""
        self.dir = Path(results_dir)
        self.res = ImportedResults(planning, SOURCE, case_dir=case_dir)
        self._out_of_case: set[str] = set()

    def warn(self, msg: str) -> None:
        """Record a warning."""
        self.res.warn(msg)

    def _graf(self, name: str) -> Graf | None:
        path = find_compressed_path(self.dir / name)
        if path is None:
            logger.info("%s: %s not found, skipped", self.dir, name)
            return None
        return read_graf(path)

    def _blocks(
        self, graf: Graf, values: dict[tuple[int, int, int], float]
    ) -> np.ndarray:
        """``(n_scen, n_blocks)`` of one agent (per-stage files broadcast)."""
        g = self.res.grid
        out = self.res.zeros()
        for (stag, seq, blck), v in values.items():
            k, s = stag - 1, seq - 1
            if not (0 <= k < g.n_stages and 0 <= s < g.n_scenarios):
                self._out_of_case.add(f"stage {stag} / series {seq}")
                continue
            st = g.stages[k]
            if graf.per_block:
                if not 1 <= blck <= st.count_block:
                    self._out_of_case.add(f"stage {stag} block {blck}")
                    continue
                out[s, st.first_block + blck - 1] = v
            else:
                out[s, list(st.blocks)] = v
        return out

    def _stages(self, values: dict[tuple[int, int, int], float]) -> np.ndarray:
        g = self.res.grid
        out = np.zeros((g.n_scenarios, g.n_stages))
        for (stag, seq, _blck), v in values.items():
            k, s = stag - 1, seq - 1
            if 0 <= k < g.n_stages and 0 <= s < g.n_scenarios:
                out[s, k] = v
            else:
                self._out_of_case.add(f"stage {stag} / series {seq}")
        return out

    def _mw(self, graf: Graf, values: dict[tuple[int, int, int], float]) -> np.ndarray:
        arr = self._blocks(graf, values)
        if graf.unit.lower() != "gwh":
            return arr
        dur = np.asarray(self.res.grid.durations, dtype=float)
        return np.where(dur > 0, arr * 1e3 / np.where(dur > 0, dur, 1.0), 0.0)

    # ── sections ───────────────────────────────────────────────────────
    def run(self) -> ImportedResults:
        """Read every graf file found; returns the filled results."""
        if not any(find_compressed_path(self.dir / f) for f in RESULT_FILES):
            raise FileNotFoundError(
                f"{self.dir}: no PSR SDDP result files ({', '.join(RESULT_FILES)})"
            )
        self._generation()
        self._buses()
        self._circuits()
        self._hydro()
        if self._out_of_case:
            self.warn(
                f"{SOURCE}: result cells outside the case skipped: "
                + ", ".join(sorted(self._out_of_case)[:10])
            )
        self.res.estimate_cost()
        return self.res

    def _generation(self) -> None:
        r = self.res
        for fname in ("gerter.csv", "gergnd.csv", "gerhid.csv"):
            graf = self._graf(fname)
            if graf is None:
                continue
            for name, values in graf.agents.items():
                gen = r.index.find("generator_array", name)
                gu = uid_of(gen)
                if gu is None:
                    self.warn(f"{SOURCE}: {fname} agent {name} has no generator")
                    continue
                r.add_blocks("Generator", "generation_sol", gu, self._mw(graf, values))
        graf = self._graf("gerbat.csv")
        if graf is not None:
            for name, values in graf.agents.items():
                bu = uid_of(r.index.find("battery_array", name))
                if bu is None:
                    self.warn(f"{SOURCE}: gerbat.csv agent {name} has no battery")
                    continue
                r.add_blocks("Battery", "fout_sol", bu, self._mw(graf, values))

    def _bus_series(self, fname: str) -> dict[str, np.ndarray]:
        graf = self._graf(fname)
        if graf is None:
            return {}
        return {n: self._mw(graf, v) for n, v in graf.agents.items()}

    def _buses(self) -> None:
        r = self.res
        graf = self._graf("cmgbus.csv")
        cmg = (
            {}
            if graf is None
            else {n: self._blocks(graf, v) for n, v in graf.agents.items()}
        )
        dem = self._bus_series("demxba.csv")
        fail = self._bus_series("defbus.csv")
        for name in sorted({*cmg, *dem, *fail}):
            bus = r.index.find("bus_array", name)
            bu = uid_of(bus)
            if bus is None or bu is None:
                self.warn(f"{SOURCE}: bus {name} is not in the case")
                continue
            if name in cmg:
                r.add_blocks("Bus", "balance_dual", bu, cmg[name])
            if name in dem or name in fail:
                f = fail.get(name, r.zeros())
                r.assign_bus_load(bus, dem.get(name, f) - f, f)

    def _circuits(self) -> None:
        r = self.res
        graf = self._graf("cirflw.csv")
        if graf is None:
            return
        for name, values in graf.agents.items():
            lu = uid_of(r.index.find("line_array", name))
            if lu is None:
                self.warn(f"{SOURCE}: circuit {name} is not in the case")
                continue
            r.add_blocks("Line", "flow_sol", lu, self._blocks(graf, values))

    def _turbine(self, plant: str) -> int | None:
        idx = self.res.index
        t = idx.find("turbine_array", plant, prefixes=("tb_",))
        if t is not None:
            return uid_of(t)
        gen = idx.find("generator_array", plant)
        refs = set() if gen is None else {gen.get("uid"), gen.get("name")} - {None}
        for tu in idx.elements("turbine_array"):
            if tu.get("generator") in refs:
                return uid_of(tu)
        return None

    def _hydro(self) -> None:
        r = self.res
        idx = r.index
        graf = self._graf("qturbi.csv")
        for name, values in (graf.agents if graf else {}).items():
            tu = self._turbine(name)
            if tu is None:
                self.warn(f"{SOURCE}: qturbi.csv plant {name} has no turbine")
                continue
            assert graf is not None
            r.add_blocks("Turbine", "flow_sol", tu, self._blocks(graf, values))
        graf = self._graf("qverti.csv")
        for name, values in (graf.agents if graf else {}).items():
            assert graf is not None
            q = self._blocks(graf, values)
            if not np.any(q):
                continue
            ww = idx.find("waterway_array", name, prefixes=("s_", "sp_"))
            j = idx.find("junction_array", name, prefixes=("j_",))
            if ww is not None and uid_of(ww) is not None:
                r.add_blocks("Waterway", "flow_sol", uid_of(ww) or 0, q, add=True)
            elif j is not None and uid_of(j) is not None:
                r.add_blocks("Junction", "drain_sol", uid_of(j) or 0, q, add=True)
            else:
                self.warn(f"{SOURCE}: qverti.csv plant {name}: no spill element")
        graf = self._graf("volfin.csv")
        for name, values in (graf.agents if graf else {}).items():
            ru = uid_of(idx.find("reservoir_array", name, prefixes=("r_", "rs_")))
            if ru is None:
                self.warn(f"{SOURCE}: volfin.csv plant {name} has no reservoir")
                continue
            r.set_volumes(ru, None, self._stages(values))
        graf = self._graf("valagu.csv")
        for name, values in (graf.agents if graf else {}).items():
            ru = uid_of(idx.find("reservoir_array", name, prefixes=("r_", "rs_")))
            if ru is None:
                self.warn(f"{SOURCE}: valagu.csv plant {name} has no reservoir")
                continue
            assert graf is not None
            r.add_blocks(
                "Reservoir", "water_value_dual", ru, self._blocks(graf, values) * 1e3
            )


def import_sddp_results(
    results_dir: Path,
    planning: dict[str, Any],
    out_dir: Path,
    case_dir: Path | None = None,
) -> ImportedResults:
    """Import ``results_dir`` (PSR graf CSV) into ``out_dir`` (gtopt output)."""
    res = SddpResultsImporter(results_dir, planning, case_dir).run()
    res.write(out_dir)
    return res
