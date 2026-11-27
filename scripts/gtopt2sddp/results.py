# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a solved gtopt run as PSR SDDP result files (``graf`` CSV).

The results half of :mod:`gtopt2sddp`.  PSR SDDP writes each result
variable as one "graf" CSV: a short header, then one row per stage x
series x block and one column per agent::

    Varies per block?       ,1,Unit,GWh,2,1,2030
    Varies per sequence?    ,1
    # of agents             ,2
    Stag,Seq.,Blck,TERMO       ,DIESEL
       1,   1,   1,      0.200000,      0.400000
       1,   1,   2,      ...

Header line 1 holds the per-block flag, the unit, the stage type (``2`` =
monthly) and the first stage (month, year) of the study; line 2 the
per-series flag; line 3 the agent count; line 4 the column names (agent
names padded to 12 characters, as PSR does).  This is the layout
:mod:`sddp2gtopt` reads (``watervcp.csv``, ``volfincp.csv``, ``inflow.csv``:
``dat_parsers._parse_psr_block_csv_profiles`` keys on the ``Stag`` row and
takes the agents from column 4 on).  ``Stag`` is the 1-based stage, ``Seq.``
the 1-based gtopt scenario (PSR series), ``Blck`` the block of the stage
(``1`` in per-stage files, flagged ``Varies per block?,0``).

=========== ======== ======================================================
file        unit     content (agents)
=========== ======== ======================================================
gerter.csv  GWh      thermal generation (thermal plants)
gerhid.csv  GWh      hydro generation (hydro plants, one per junction)
gergnd.csv  GWh      renewable generation (renewable plants)
gerbat.csv  GWh      battery discharge (batteries)
cmgbus.csv  $/MWh    bus marginal cost = ``Bus/balance_dual`` (buses)
cmgdem.csv  $/MWh    system marginal cost: demand-weighted mean of the bus
                     marginal costs (system ``gtopt``)
demxba.csv  GWh      demand per bus, served + unserved (buses)
defbus.csv  GWh      deficit (unserved demand) per bus (buses)
defcit.csv  GWh      system deficit (system ``gtopt``)
cirflw.csv  MW       circuit flow, positive from ``BusFrom`` (circuits)
qturbi.csv  m3/s     turbined outflow (hydro plants)
qverti.csv  m3/s     spilled outflow: spill waterways + drain (hydro plants)
volfin.csv  hm3      end-of-stage volume, per stage (hydro plants w/ storage)
valagu.csv  k$/hm3   water value, stage mean of ``Reservoir/water_value_dual``
                     [$/hm3] / 1000, per stage (hydro plants w/ storage)
=========== ======== ======================================================

Agent names are the input export's (:class:`gtopt2sddp.writer.SddpWriter`
``agents``), so a study exported with inputs and results is consistent.
gtopt duals are written back-scaled (``scale_objective``, probability,
discount and block duration removed), so ``cmgbus`` is the undiscounted
$/MWh price as PSR reports it.  Energies are MW x block hours / 1000.

Assumptions (no PSR sample in this repo): the file names and units are the
usual SDDP output names (``gerter``, ``gerhid``, ``gergnd``, ``cmgbus``,
``cmgdem``, ``defcit``, ``cirflw``, ``qturbi``, ``qverti``, ``volfin``);
``gerbat``, ``demxba``, ``defbus`` and ``valagu`` follow the same naming;
the first header line's trailing fields (stage type, first month, first year)
are informative only -- the readers in this repo key on the ``Stag`` row.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.results_reader import GtoptResults
from gtopt2sddp.writer import SddpWriter

logger = logging.getLogger(__name__)

SYSTEM_NAME = "gtopt"
AGENT_WIDTH = 12


def _uid(el: dict[str, Any]) -> int | None:
    u = el.get("uid")
    return int(u) if isinstance(u, (int, np.integer)) else None


@dataclass
class SddpResultsWriter:
    """Export the results of a solved gtopt run as PSR graf CSV files."""

    case: GtoptCase
    results_dir: Path
    out_dir: Path
    writer: SddpWriter | None = None
    files: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Bind the reader and the time grid."""
        g = self.case.grid
        self.res = GtoptResults(self.results_dir, g)
        self.dur = np.asarray(g.durations, dtype=float)
        self.zero = np.zeros((g.n_scenarios, g.n_blocks))

    # ── helpers ────────────────────────────────────────────────────────
    def _blk(self, cls: str, fld: str, uid: int | None) -> np.ndarray:
        return self.res.block_of(cls, fld, uid)

    def _gwh(self, mw: np.ndarray) -> np.ndarray:
        return np.asarray(mw * self.dur * 1e-3, dtype=float)

    def _header(self, unit: str, n: int, per_block: bool) -> list[str]:
        w = self.writer
        y, m = w.dates[0] if w is not None and w.dates else (2030, 1)
        return [
            f"Varies per block?       ,{1 if per_block else 0},Unit,{unit},2,{m},{y}",
            "Varies per sequence?    ,1",
            f"# of agents             ,{n}",
        ]

    def _write_graf(
        self,
        name: str,
        unit: str,
        agents: list[tuple[str, np.ndarray]],
        per_block: bool = True,
    ) -> None:
        """Write one graf CSV; values ``(n_scen, n_blocks | n_stages)``."""
        if not agents:
            return
        g = self.case.grid
        lines = self._header(unit, len(agents), per_block)
        lines.append(
            "Stag,Seq.,Blck," + ",".join(a.ljust(AGENT_WIDTH) for a, _ in agents)
        )
        for k, st in enumerate(g.stages):
            for s in range(g.n_scenarios):
                cols = st.blocks if per_block else range(k, k + 1)
                for j, b in enumerate(cols):
                    vals = ",".join(f"{float(v[s, b]) + 0.0:14.6f}" for _, v in agents)
                    lines.append(f"{k + 1:4d},{s + 1:4d},{j + 1:4d},{vals}")
        path = self.out_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="latin-1", errors="replace")
        self.files.append(path)

    def _agent(self, kind: str, uid: int | None) -> str | None:
        w = self.writer
        assert w is not None
        return None if uid is None else w.agents.get((kind, uid))

    # ── main entry ─────────────────────────────────────────────────────
    def write(self) -> list[Path]:
        """Write every result file; returns their paths."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if not self.res.has("Bus", "balance_dual") and not self.res.has(
            "Generator", "generation_sol"
        ):
            raise ValueError(
                f"{self.results_dir}: no gtopt results (Bus/balance_dual, "
                "Generator/generation_sol) found"
            )
        if self.writer is None or not self.writer.agents:
            # The agent names are assigned while the input files are
            # written: write them to a scratch dir and keep the mapping.
            w = self.writer or SddpWriter(self.case, self.out_dir)
            keep = w.out_dir
            with tempfile.TemporaryDirectory() as tmp:
                w.out_dir = Path(tmp)
                try:
                    w.write()
                finally:
                    w.out_dir = keep
            self.writer = w
        self._generation()
        self._buses()
        self._circuits()
        self._hydro()
        return self.files

    # ── sections ───────────────────────────────────────────────────────
    def _generation(self) -> None:
        c = self.case
        for kind, fname in (("thermal", "gerter.csv"), ("renewable", "gergnd.csv")):
            agents = []
            for gen in c.elements("generator_array"):
                name = self._agent(kind, _uid(gen))
                if name is not None:
                    mw = self._blk("Generator", "generation_sol", _uid(gen))
                    agents.append((name, self._gwh(mw)))
            self._write_graf(fname, "GWh", agents)
        w = self.writer
        assert w is not None
        hydro = []
        for plant in w.plants:
            gen = plant.generator or {}
            mw = (
                self._blk("Generator", "generation_sol", _uid(gen))
                if gen
                else self.zero
            )
            hydro.append((plant.name, self._gwh(mw)))
        self._write_graf("gerhid.csv", "GWh", hydro)
        bats = []
        for bat in c.elements("battery_array"):
            name = self._agent("battery", _uid(bat))
            if name is not None:
                bats.append(
                    (name, self._gwh(self._blk("Battery", "fout_sol", _uid(bat))))
                )
        self._write_graf("gerbat.csv", "GWh", bats)

    def _buses(self) -> None:
        c = self.case
        w = self.writer
        assert w is not None
        buses = c.elements("bus_array")
        dem: dict[int, np.ndarray] = {}
        fail: dict[int, np.ndarray] = {}
        for d in c.elements("demand_array"):
            bus = w._bus_uid(d.get("bus"))
            if bus is None:
                continue
            f = self._blk("Demand", "fail_sol", _uid(d))
            dem[bus] = (
                dem.get(bus, self.zero) + self._blk("Demand", "load_sol", _uid(d)) + f
            )
            fail[bus] = fail.get(bus, self.zero) + f
        cmg, demx, defb = [], [], []
        num = np.zeros_like(self.zero)
        den = np.zeros_like(self.zero)
        for b in buses:
            u = _uid(b)
            name = self._agent("bus", u)
            if u is None or name is None:
                continue
            price = self._blk("Bus", "balance_dual", u)
            cmg.append((name, price))
            load = dem.get(u, self.zero)
            demx.append((name, self._gwh(load)))
            defb.append((name, self._gwh(fail.get(u, self.zero))))
            num += price * load
            den += load
        self._write_graf("cmgbus.csv", "$/MWh", cmg)
        self._write_graf("demxba.csv", "GWh", demx)
        self._write_graf("defbus.csv", "GWh", defb)
        if cmg:
            mean = np.mean([p for _, p in cmg], axis=0)
            sys_cmg = np.where(den > 0, num / np.where(den > 0, den, 1.0), mean)
            self._write_graf("cmgdem.csv", "$/MWh", [(SYSTEM_NAME, sys_cmg)])
            total = sum((v for _, v in defb), np.zeros_like(self.zero))
            self._write_graf("defcit.csv", "GWh", [(SYSTEM_NAME, total)])

    def _circuits(self) -> None:
        agents = []
        for ln in self.case.elements("line_array"):
            name = self._agent("line", _uid(ln))
            if name is None:
                continue
            flow = self._blk("Line", "flow_sol", _uid(ln))
            if not self.res.has("Line", "flow_sol"):
                flow = self._blk("Line", "flowp_sol", _uid(ln)) - self._blk(
                    "Line", "flown_sol", _uid(ln)
                )
            agents.append((name, flow))
        self._write_graf("cirflw.csv", "MW", agents)

    def _hydro(self) -> None:
        c = self.case
        w = self.writer
        assert w is not None
        g = c.grid
        seep = {s.get("waterway") for s in c.elements("reservoir_seepage_array")}
        qt, qv, vol, wv = [], [], [], []
        for p in w.plants:
            j = p.junction
            refs = {j.get("name"), j.get("uid")} - {None}
            t = p.turbine or {}
            tww = t.get("_waterway")
            outlet = j.get("_outlet")
            if t and tww is not None and not self.res.has("Turbine", "flow_sol"):
                q = self._blk("Waterway", "flow_sol", _uid(tww))
            elif t:
                q = self._blk("Turbine", "flow_sol", _uid(t))
            elif outlet is not None:
                q = self._blk("Waterway", "flow_sol", _uid(outlet))
            else:
                q = self.zero
            qt.append((p.name, q))
            spill = self._blk("Junction", "drain_sol", _uid(j))
            for ww in c.elements("waterway_array"):
                if ww.get("junction_a") not in refs:
                    continue
                if ww is tww or ww is outlet:
                    continue  # the turbine / outlet arc, already in qturbi
                if ww.get("name") in seep or ww.get("uid") in seep:
                    continue
                spill = spill + self._blk("Waterway", "flow_sol", _uid(ww))
            qv.append((p.name, spill))
            r = p.reservoir
            if r is not None:
                vol.append(
                    (p.name, self.res.stage_of("Reservoir", "efin_sol", _uid(r)))
                )
                dual = self._blk("Reservoir", "water_value_dual", _uid(r))
                stage_mean = np.stack(
                    [dual[:, st.blocks].mean(axis=1) for st in g.stages], axis=1
                )
                wv.append((p.name, stage_mean * 1e-3))
        self._write_graf("qturbi.csv", "m3/s", qt)
        self._write_graf("qverti.csv", "m3/s", qv)
        self._write_graf("volfin.csv", "hm3", vol, per_block=False)
        self._write_graf("valagu.csv", "k$/hm3", wv, per_block=False)


def write_results(
    case: GtoptCase,
    results_dir: Path,
    out_dir: Path,
    writer: SddpWriter | None = None,
) -> list[Path]:
    """Write the PSR result files of a solved run; returns their paths."""
    return SddpResultsWriter(case, Path(results_dir), Path(out_dir), writer).write()
