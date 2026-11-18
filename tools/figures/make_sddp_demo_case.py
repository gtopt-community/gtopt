#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build the stochastic SDDP demo case used by the documentation figures.

The case is derived from ``cases/hydro_valley_sddpjl`` (a two-reservoir
hydro cascade adapted from the SDDP.jl examples).  It keeps the cascade
topology and adds:

* 24 monthly phases (one stage of four 6-hour blocks each),
* ``--scenarios`` equiprobable inflow scenarios, one scene per scenario,
* a stepped thermal supply curve (eight 25 MW units, 20-200 $/MWh),
* a seasonal, intra-day demand profile.

The result is a small but non-trivial multi-stage stochastic problem whose
SDDP lower and upper bounds take several iterations to meet, which is what
the convergence figure needs.  All data are synthetic.

Usage::

    python tools/figures/make_sddp_demo_case.py \\
        cases/hydro_valley_sddpjl/system_hydro_valley_sddpjl.json \\
        /tmp/figs/sddp_hydro_valley_demo.json --scenarios 8
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

N_STAGES = 24
N_BLOCKS = 4
BLOCK_HOURS = 6.0
THERMAL_COSTS = (20.0, 32.0, 45.0, 60.0, 80.0, 110.0, 150.0, 200.0)
THERMAL_MW = 25.0
INFLOW_BASE = (40.0, 20.0)  # upper and lower reservoir mean inflow


def _simulation(n_scen: int) -> dict[str, Any]:
    return {
        "block_array": [
            {"uid": i + 1, "duration": BLOCK_HOURS} for i in range(N_STAGES * N_BLOCKS)
        ],
        "stage_array": [
            {"uid": s + 1, "first_block": s * N_BLOCKS, "count_block": N_BLOCKS}
            for s in range(N_STAGES)
        ],
        "phase_array": [
            {"uid": s + 1, "first_stage": s, "count_stage": 1} for s in range(N_STAGES)
        ],
        "scene_array": [
            {"uid": k + 1, "first_scenario": k, "count_scenario": 1}
            for k in range(n_scen)
        ],
        "scenario_array": [
            {"uid": k + 1, "probability_factor": 1.0 / n_scen} for k in range(n_scen)
        ],
    }


def _inflow(base: float, n_scen: int) -> list[list[list[float]]]:
    """Seasonal inflow, scaled per scenario from dry (0.4x) to wet (1.6x)."""
    out = []
    for k in range(n_scen):
        wetness = 0.4 + 1.2 * ((k * 5) % n_scen) / max(1, n_scen - 1)
        rows = []
        for s in range(N_STAGES):
            season = 1.0 + 0.8 * math.cos(2.0 * math.pi * s / 12.0)
            noise = 1.0 + 0.3 * math.sin(1.7 * s + k)
            rows.append([round(wetness * base * season * noise, 3)] * N_BLOCKS)
        out.append(rows)
    return out


def _demand() -> list[list[float]]:
    return [
        [
            round(
                150.0
                + 40.0 * math.sin(math.pi * b / (N_BLOCKS - 1))
                + 20.0 * math.cos(2.0 * math.pi * s / 12.0),
                3,
            )
            for b in range(N_BLOCKS)
        ]
        for s in range(N_STAGES)
    ]


def build_case(base: dict[str, Any], n_scen: int) -> dict[str, Any]:
    """Return the demo planning dict derived from ``base``."""
    case = json.loads(json.dumps(base))
    case["simulation"] = _simulation(n_scen)
    case.setdefault("options", {})["method"] = "sddp"
    system = case["system"]
    system["name"] = "sddp_hydro_valley_demo"

    gens = [g for g in system["generator_array"] if g["name"].startswith("g_hydro")]
    for gen in gens:
        gen["capacity"] = 120.0
    for i, cost in enumerate(THERMAL_COSTS):
        gens.append(
            {
                "uid": 10 + i,
                "name": f"g_thermal{i + 1}",
                "bus": 1,
                "gcost": cost,
                "capacity": THERMAL_MW,
            }
        )
    system["generator_array"] = gens

    for rsv in system["reservoir_array"]:
        rsv.update(capacity=3000.0, emax=3000.0, eini=1000.0)
    for ww in system["waterway_array"]:
        if ww["name"].startswith("ww_turb"):
            ww["fmax"] = 110.0
    for flow, base_inflow in zip(system["flow_array"], INFLOW_BASE, strict=False):
        flow["discharge"] = _inflow(base_inflow, n_scen)

    dem = system["demand_array"][0]
    dem.pop("capacity", None)
    dem["lmax"] = _demand()
    return case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path, help="hydro_valley_sddpjl case JSON")
    parser.add_argument("output", type=Path, help="output case JSON")
    parser.add_argument("--scenarios", type=int, default=8)
    args = parser.parse_args()

    base = json.loads(args.base.read_text(encoding="utf-8"))
    case = build_case(base, args.scenarios)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(case, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
