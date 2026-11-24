#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""3-node chain climbing 0 -> 2000 -> 4000 m (a Paso de Jama-like rise).

S is pinned at 70 bar and feeds a 60 km3/h city load at T over two
100 km, 406 mm pipes.  With one tangent at t = load (fmax = 2 load) the
tangent row is exact at the operating flow, so the LP pressures equal
the closed form of pi_a - e^s pi_b = K_e f^2:

    pi_b = (pi_a - K_e f^2) / e^s

Writes ``gas_elevation_chain.json`` and ``expected.json`` (run from the
repo root with ``scripts`` on PYTHONPATH).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from gasnet2gtopt.physics import elevation_exponent, elevation_k_factor, weymouth_k

HERE = Path(__file__).resolve().parent
LOAD = 60.0
ELEV = {"S": 0.0, "M": 2000.0, "T": 4000.0}


def main() -> None:
    k = weymouth_k(100.0, 406.0)
    s = elevation_exponent(2000.0)
    ke = k * elevation_k_factor(s)
    pi_s = 70.0**2
    pi_m = (pi_s - ke * LOAD**2) / math.exp(s)
    pi_t = (pi_m - ke * LOAD**2) / math.exp(s)
    k_flat = (pi_s - 2 * k * LOAD**2) ** 0.5
    nodes = [
        {
            "uid": i,
            "name": n,
            "pmin": 70.0 if n == "S" else 1.0,
            "pmax": 70.0,
            "elevation": h,
            "pressure_cost": -1e-4,
        }
        for i, (n, h) in enumerate(ELEV.items(), start=1)
    ]
    pipes = [
        {
            "uid": i,
            "name": f"{a}_{b}",
            "gas_node_a": a,
            "gas_node_b": b,
            "length": 100.0,
            "diameter": 406.0,
            "fmax": 2 * LOAD,
            "segments": 1,
        }
        for i, (a, b) in enumerate((("S", "M"), ("M", "T")), start=1)
    ]
    planning = {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {"use_single_bus": True, "scale_objective": 1},
            "gas_options": {
                "gas_specific_gravity": 0.6,
                "gas_temperature": 288.15,
                "compressibility": 0.9,
            },
        },
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": {
            "name": "gas_elevation_chain",
            "bus_array": [{"uid": 1, "name": "B"}],
            "gas_node_array": nodes,
            "gas_supply_array": [
                {
                    "uid": 1,
                    "name": "SUP",
                    "gas_node": "S",
                    "fmax": 1000.0,
                    "cost": 300.0,
                }
            ],
            "gas_demand_array": [
                {
                    "uid": 1,
                    "name": "CITY",
                    "gas_node": "T",
                    "load": LOAD,
                    "fail_cost": 3000.0,
                }
            ],
            "gas_pipe_array": pipes,
        },
    }
    (HERE / "gas_elevation_chain.json").write_text(
        json.dumps(planning, indent=1) + "\n"
    )
    expected = {
        "s": s,
        "exp_s": math.exp(s),
        "K": k,
        "K_e": ke,
        "pressure": {"S": 70.0, "M": math.sqrt(pi_m), "T": math.sqrt(pi_t)},
        "flat_pressure_T": k_flat,
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    print(json.dumps(expected, indent=1))


if __name__ == "__main__":
    main()
