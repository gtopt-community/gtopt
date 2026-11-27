# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fixtures: tiny synthetic solved cases (gtopt CSV outputs) and goldens."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable

import pytest

REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "cases"
CONFIGS = Path(__file__).resolve().parents[1] / "configs"

Rows = list[tuple[int, int, int, int, float]]


def write_stream(rdir: Path, stem: str, rows: Rows) -> None:
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


def sim(durations: list[float]) -> dict[str, Any]:
    return {
        "block_array": [{"uid": i + 1, "duration": d} for i, d in enumerate(durations)],
        "stage_array": [{"uid": 1, "first_block": 0, "count_block": len(durations)}],
        "scenario_array": [{"uid": 1, "probability_factor": 1}],
    }


@pytest.fixture
def gen_case(tmp_path: Path) -> Callable[..., Path]:
    """One bus, two 4380-h blocks (one year), one built generator ``g``.

    Prices 30 / 10 $/MWh, g (10 $/MWh) runs 50 MW in both blocks; the
    existing unit ``old`` (30 $/MWh) runs 50 MW in block 1 and sets the
    price there; load 100 / 50 MW.  ``g`` annual_capcost 87,600 $/MW-yr ×
    50 MW = 4.38 M$/yr = its margin (20 $/MWh × 50 MW × 4380 h).
    """

    def make(extra_system: dict[str, Any] | None = None, **gen_extra: Any) -> Path:
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
        system: dict[str, Any] = {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [
                gen,
                {"uid": 2, "name": "old", "bus": "b1", "gcost": 30, "capacity": 80},
            ],
            "demand_array": [{"uid": 1, "name": "d", "bus": "b1", "lmax": 100}],
        }
        system.update(extra_system or {})
        planning = {
            "options": {"annual_discount_rate": 0.08},
            "simulation": sim([4380, 4380]),
            "system": system,
        }
        streams: dict[str, Rows] = {
            "Bus/balance_dual": [(1, 1, 1, 1, 30.0), (1, 1, 2, 1, 10.0)],
            "Generator/generation_sol": [
                (1, 1, 1, 1, 50.0),
                (1, 1, 2, 1, 50.0),
                (1, 1, 1, 2, 50.0),
            ],
            "Generator/capainst_sol": [(0, 1, 0, 1, 50.0), (0, 1, 0, 2, 80.0)],
            "Generator/capacost_sol": [(0, 1, 0, 1, 500.0)],
            "Demand/load_sol": [(1, 1, 1, 1, 100.0), (1, 1, 2, 1, 50.0)],
        }
        return write_case(tmp_path / "gen", planning, streams, obj=1.0)

    return make


def golden(rel: str) -> Path:
    path = CASES / rel
    if not (path / "planning.json").is_file():
        pytest.skip(f"golden {rel} not present")
    return path
