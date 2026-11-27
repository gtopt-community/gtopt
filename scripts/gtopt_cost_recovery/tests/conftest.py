# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fixtures: tiny synthetic solved cases written as gtopt CSV outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable

import pytest

REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "cases"

Rows = list[tuple[int, int, int, int, float]]


def write_stream(rdir: Path, stem: str, rows: Rows) -> None:
    """Write ``<rdir>/<stem>_s0_p0.csv`` in gtopt's long layout."""
    path = rdir / f"{stem}_s0_p0.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC)
        w.writerow(["scenario", "stage", "block", "uid", "value"])
        for r in rows:
            w.writerow(list(r))


def write_case(
    rdir: Path, planning: dict[str, Any], streams: dict[str, Rows], obj: float = 0.0
) -> Path:
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "planning.json").write_text(json.dumps(planning), encoding="utf-8")
    (rdir / "solution.csv").write_text(
        f"scene,phase,status,status_name,obj_value\n0,0,0,optimal,{obj}\n",
        encoding="utf-8",
    )
    for stem, rows in streams.items():
        write_stream(rdir, stem, rows)
    return rdir


def sim(durations: list[float], stages: int = 1) -> dict[str, Any]:
    per = len(durations) // stages
    return {
        "block_array": [{"uid": i + 1, "duration": d} for i, d in enumerate(durations)],
        "stage_array": [
            {"uid": s + 1, "first_block": s * per, "count_block": per}
            for s in range(stages)
        ],
        "scenario_array": [{"uid": 1, "probability_factor": 1}],
    }


@pytest.fixture
def gen_case(tmp_path: Path) -> Callable[..., Path]:
    """One bus, two 1-h blocks, one expandable generator ``g`` (10 $/MWh).

    Prices 30 / 10 $/MWh, dispatch 50 / 50 MW, 50 MW built, LP capex 1000 $
    over the 2-h horizon (capacost 10 $/h · 2 h · 50 MW / 50 MW → 20 $/h).
    Revenue 1500 + 500 = 2000, opex 1000, capex 1000 → profit 0.
    """

    def make(capacost_rate: float = 500.0, **gen_extra: Any) -> Path:
        gen = {
            "uid": 1,
            "name": "g",
            "bus": "b1",
            "gcost": 10,
            "capacity": 0,
            "expcap": 10,
            "expmod": 10,
            "annual_capcost": 87600.0,
        }
        gen.update(gen_extra)
        planning = {
            "options": {"annual_discount_rate": 0.0},
            "simulation": sim([1, 1]),
            "system": {
                "bus_array": [{"uid": 1, "name": "b1"}],
                "generator_array": [
                    gen,
                    {"uid": 2, "name": "old", "bus": "b1", "gcost": 30},
                ],
                "demand_array": [{"uid": 1, "name": "d", "bus": "b1", "lmax": 100}],
            },
        }
        streams: dict[str, Rows] = {
            "Bus/balance_dual": [(1, 1, 1, 1, 30.0), (1, 1, 2, 1, 10.0)],
            "Generator/generation_sol": [
                (1, 1, 1, 1, 50.0),
                (1, 1, 2, 1, 50.0),
                (1, 1, 1, 2, 50.0),
            ],
            "Generator/capainst_sol": [(0, 1, 0, 1, 50.0)],
            "Generator/capacost_sol": [(0, 1, 0, 1, capacost_rate)],
            "Generator/capacity_dual": [(1, 1, 1, 1, 20.0), (1, 1, 2, 1, -0.0)],
            "Demand/load_sol": [(1, 1, 1, 1, 100.0), (1, 1, 2, 1, 50.0)],
        }
        return write_case(tmp_path / "gen", planning, streams, obj=2500.0)

    return make
