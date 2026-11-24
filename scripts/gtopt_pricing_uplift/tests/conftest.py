# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Synthetic solved-case fixtures for gtopt_pricing_uplift.

The toy is ``cases/uc_pricing_*``: two buses, two hours, a slow unit
(10 $/MWh, no-load 300 $/h, min up/down 4 h, on) and a fast-start peaker
(50 $/MWh, pmin 40, start-up 1000 $, off).  MIP dispatch 100/80 and 40/0.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "cases"

TOY_PLANNING: dict[str, Any] = {
    "options": {"model_options": {"pricing_mode": "fixed"}},
    "simulation": {
        "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
        "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2}],
        "scenario_array": [{"uid": 1, "probability_factor": 1}],
    },
    "system": {
        "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
        "line_array": [{"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2"}],
        "generator_array": [
            {"uid": 1, "name": "g_slow", "bus": "b1", "pmax": 120, "gcost": 10},
            {"uid": 2, "name": "g_fast", "bus": "b2", "pmax": 100, "gcost": 50},
        ],
        "commitment_array": [
            {
                "uid": 1,
                "name": "g_slow_uc",
                "generator": "g_slow",
                "pmin": 50,
                "noload_cost": 300,
                "min_up_time": 4,
                "min_down_time": 4,
                "initial_status": 1,
                "initial_hours": 10,
            },
            {
                "uid": 2,
                "name": "g_fast_uc",
                "generator": "g_fast",
                "pmin": 40,
                "startup_cost": 1000,
                "min_up_time": 1,
                "min_down_time": 1,
                "initial_status": 0,
                "initial_hours": 10,
            },
        ],
    },
}

#: MIP dispatch shared by every pricing mode.
GEN = {(1, 1): 100.0, (2, 1): 80.0, (1, 2): 40.0}  # (block, uid) -> MW
STATUS = {(1, 1): 1.0, (2, 1): 1.0, (1, 2): 1.0}
STARTUP = {(1, 2): 1.0}

#: Hand-derived prices and uplift per mode (see README of uc_pricing_fixed).
PRICES = {"fixed": (10.0, 10.0), "fast_start": (60.0, 10.0), "relaxed": (60.0, 12.5)}
UPLIFT = {
    # mode: (make-whole, LOC, total)
    "fixed": (3200.0, 0.0, 3200.0),
    "fast_start": (600.0, 1300.0, 1900.0),
    "relaxed": (600.0, 1100.0, 1700.0),
}


def _write_long(path: Path, rows: dict[tuple[int, int], float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"scenario","stage","block","uid","value"']
    lines += [f"1,1,{b},{u},{v:g}" for (b, u), v in sorted(rows.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_toy_results(
    root: Path,
    mode: str,
    *,
    prices: tuple[float, float] | None = None,
    planning: dict[str, Any] | None = None,
    with_startup: bool = True,
    with_flow: bool = True,
    pricing_csv: str | None = None,
) -> Path:
    """Write a solved-toy output directory (CSV shards, long layout)."""
    root.mkdir(parents=True, exist_ok=True)
    plan = copy.deepcopy(planning if planning is not None else TOY_PLANNING)
    plan["options"]["model_options"]["pricing_mode"] = mode
    (root / "planning.json").write_text(json.dumps(plan), encoding="utf-8")
    p1, p2 = prices if prices is not None else PRICES[mode]
    lam = {(1, 1): p1, (1, 2): p1, (2, 1): p2, (2, 2): p2}
    _write_long(root / "Bus" / "balance_dual_s0_p0.csv", lam)
    _write_long(root / "Generator" / "generation_sol_s0_p0.csv", GEN)
    _write_long(root / "Commitment" / "status_sol_s0_p0.csv", STATUS)
    if with_startup:
        _write_long(root / "Commitment" / "startup_sol_s0_p0.csv", STARTUP)
    if with_flow:
        _write_long(root / "Line" / "flow_sol_s0_p0.csv", {(1, 1): 100.0, (2, 1): 80.0})
    if pricing_csv is not None:
        (root / "pricing.csv").write_text(pricing_csv, encoding="utf-8")
    return root


@pytest.fixture
def toy_results(tmp_path: Path):
    """Factory: ``toy_results(mode, **kw) -> results dir``."""

    def make(mode: str, **kw: Any) -> Path:
        return write_toy_results(tmp_path / mode, mode, **kw)

    return make
