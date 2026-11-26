# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Build the ``cases/cheby_small`` library (block + point JSON per case).

Sources (read-only): the Chebyshev scheduling research code's
``tests/test_dbs`` directory (``--src``).  ``caso2emb`` has no system
DB: it is transcribed here from the PLP input files of
``tests/test_dbs/caso2emb_ver19`` (``plpcnfce.dat``, ``plpcnfli.dat``,
``plpdem.dat``, ``plpaflce.dat``, ``plpeta.dat``), which also hold the
PLP reference solution (``plpcen.csv``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .convert import ConvertOptions, Conversion, convert
from .mini_cases import MINI_CASES, build_mini
from .tcheby_db import (
    Bus,
    Demand,
    Generator,
    Inflow,
    Junction,
    Line,
    Reservoir,
    StepSchedule,
    TchebySimulation,
    TchebySystem,
    Turbine,
    Waterway,
    read_simulation,
    read_system,
)
from .timegrid import TimeGrid


def _c(v: float) -> StepSchedule:
    return StepSchedule(default=v)


def caso2emb_system() -> tuple[TchebySystem, TchebySimulation]:
    """``caso2emb_ver19`` (PLP input files, 3 stages × 100 h).

    Two reservoirs CH / CH2 (bus Antuco220, 450 MW, Rendi 1, Vini 3.6 /
    1.8 hm³, Vmax 10 hm³, spill ≤ 10 m³/s ignored → 0), thermal CT
    (10 $/MWh, 60 MW, bus Valdivia220), failure unit CF (100 $/MWh,
    100 MW, bus Antuco220), line 600 MW (losses off: ``Modela Perdidas
    f``), demand 100 / 50 / 80 MW, inflows CH 30/10/15, CH2 15/5/5 m³/s.
    The hydro network of each plant is reservoir -> turbine waterway ->
    sea (PLP ``SerHid = 0``)."""
    sysm = TchebySystem(name="caso2emb", fail_cost=1000.0)
    sysm.buses = [Bus(1, "Antuco220"), Bus(2, "Valdivia220")]
    sysm.generators = [
        Generator(1, "CH", 1, 450.0, _c(0.0), _c(450.0), _c(0.0)),
        Generator(2, "CH2", 1, 450.0, _c(0.0), _c(450.0), _c(0.0)),
        Generator(3, "CT", 2, 60.0, _c(0.0), _c(60.0), _c(10.0)),
        Generator(4, "CF", 1, 100.0, _c(0.0), _c(100.0), _c(100.0)),
    ]
    load = StepSchedule(
        default=100.0, hours=[0.0, 100.0, 200.0], values=[100.0, 50.0, 80.0]
    )
    sysm.demands = [Demand(1, "Antuco220", 1, load)]
    sysm.lines = [
        Line(
            1,
            "Antuco220->Valdivia220",
            1,
            2,
            600.0,
            _c(-600.0),
            _c(600.0),
            10.0,
            40.0,
            220.0,
        )
    ]
    sysm.junctions = [Junction(1, "CH"), Junction(2, "CH2"), Junction(3, "SEA")]
    sysm.waterways = [
        Waterway(1, "CH_gen", 1, 3, _c(0.0), _c(1e4)),
        Waterway(2, "CH2_gen", 2, 3, _c(0.0), _c(1e4)),
    ]
    sysm.reservoirs = [
        Reservoir(1, "CH", 1, 3.6, _c(0.0), _c(10.0)),
        Reservoir(2, "CH2", 2, 1.8, _c(0.0), _c(10.0)),
    ]
    sysm.inflows = [Inflow(1, "CH_Inf", 1, _c(0.0)), Inflow(2, "CH2_Inf", 2, _c(0.0))]
    sysm.turbines = [
        Turbine(1, "CH_turb", 1, 1, 1.0),
        Turbine(2, "CH2_turb", 2, 2, 1.0),
    ]
    sim = TchebySimulation(
        time_zero=datetime(2016, 1, 1),
        stages=[100.0, 100.0, 100.0],
        inflows={"CH_Inf": [30.0, 10.0, 15.0], "CH2_Inf": [15.0, 5.0, 5.0]},
    )
    return sysm, sim


@dataclass
class CaseSpec:
    """One tcheby-derived case."""

    name: str
    system_db: str | None  #: file in ``--src`` (None: built in code)
    simu_db: str | None = None
    #: stage hours when there is no simulation DB
    hours: list[float] = field(default_factory=lambda: [24.0])
    time_zero: datetime = datetime(2016, 1, 1)
    nodes: int = 24
    opts: ConvertOptions = field(default_factory=ConvertOptions)


TCHEBY_CASES: list[CaseSpec] = [
    CaseSpec("caso1emb", "system1_caso1emb_fact.db", "simu1_caso1emb_fact.db"),
    CaseSpec("caso2emb", None),
    CaseSpec("losses", "system1_losses.db", opts=ConvertOptions(line_losses=True)),
    CaseSpec(
        "system2_week",
        "system2.db",
        hours=[168.0],
        time_zero=datetime(2029, 1, 1),
        nodes=48,
        opts=ConvertOptions(pmin_mode="drop"),
    ),
]


def convert_case(spec: CaseSpec, src: Path, mode: str) -> Conversion:
    """Block or point conversion of one tcheby case."""
    sim: TchebySimulation | None
    if spec.system_db is None:
        sysm, sim = caso2emb_system()
    else:
        sim = read_simulation(src / spec.simu_db) if spec.simu_db else None
        tz = sim.time_zero if sim else spec.time_zero
        sysm = read_system(src / spec.system_db, tz, name=spec.name)
    hours = sim.stages if sim else spec.hours
    grid = TimeGrid(mode, list(hours), nodes=spec.nodes)  # type: ignore[arg-type]
    return convert(sysm, grid, sim, spec.opts)


_NUM_LIST = re.compile(r"\[\s*(-?[0-9][-+0-9.eE]*(?:,\s*-?[0-9][-+0-9.eE]*)*)\s*\]")


def write_json(path: Path, planning: dict[str, Any]) -> None:
    """Indented JSON with every list of numbers on one line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(planning, indent=1)
    text = _NUM_LIST.sub(
        lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]", text
    )
    path.write_text(text + "\n", encoding="utf-8")


def build_library(src: Path | None, out: Path) -> list[Path]:
    """Write every case of the library; returns the written files.
    ``src`` None: only the mini cases and ``caso2emb``."""
    written: list[Path] = []
    for spec in TCHEBY_CASES:
        if spec.system_db is not None and src is None:
            continue
        for mode in ("block", "point"):
            conv = convert_case(spec, src or Path("."), mode)
            p = out / spec.name / f"{spec.name}_{mode}.json"
            write_json(p, conv.planning)
            written.append(p)
            if mode == "block":
                notes = out / spec.name / "conversion_notes.txt"
                notes.write_text("\n".join(conv.notes) + "\n", encoding="utf-8")
                written.append(notes)
    for name in MINI_CASES:
        for mode in ("block", "point"):
            p = out / "mini" / name / f"{name}_{mode}.json"
            write_json(p, build_mini(name, mode))
            written.append(p)
    return written
