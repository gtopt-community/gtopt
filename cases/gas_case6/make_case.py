#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""GasModels.jl ``case-6-no-power-limits`` as a native gtopt gas case.

Writes ``gas_case6.json`` (native gas arrays) and ``expected.json`` with
two independent references computed here with scipy:

* ``exact``: the steady-state Weymouth NLP (equality, bidirectional
  flows), which reproduces GasModels' published WP objective −253.683
  (``test/ogf.jl``) and receipt 123.682 kg/s;
* ``tangent_lp``: the same tangent outer approximation gtopt builds
  (pipes oriented as in the exact optimum), the value gtopt must match.

Data: LANL GasModels.jl (BSD-3, MICOT LA-CC-13-108), SI units.  Flows
in kg/s, pressures in bar, so ``weymouth_k`` is in bar²/(kg/s)²:
``K = 16 λ L a² / (π² D⁵) / 1e10``.  The welfare objective of GasModels
(max Σ bid·w − 1.5·R) maps to gtopt's cost form through demand shedding
priced at the bid: ``gtopt_obj = Σ bid·w_max − welfare = 455 + obj_gm``.
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import linprog, minimize

HERE = Path(__file__).resolve().parent
SOUND_SPEED = 371.6643  # m/s
FRICTION = 0.01
#: (from, to, diameter m, length m) as in the matgas file
PIPES = [(5, 2, 0.6, 50000), (2, 3, 0.6, 80000), (6, 4, 0.6, 80000), (3, 4, 0.3, 80000)]
COMPRESSORS = [(1, 5), (2, 6)]  # c_ratio in [1, 2]
#: transfers: (junction, withdrawal_max kg/s, bid $/(kg/s))
TRANSFERS = [
    ("LDC_A", 2, 30, 3.0),
    ("LDC_B", 3, 40, 4.0),
    ("LDC_C", 4, 20, 5.0),
    ("PP_A", 3, 30, 2.5),
    ("PP_B", 4, 10, 3.0),
]
RECEIPT_COST = 1.5
PMIN, PMAX = 30.0, 60.0  # bar
FMAX = 130.0  # total withdrawal capacity [kg/s]
SEGMENTS = 64

K = [
    16 * FRICTION * length * SOUND_SPEED**2 / (math.pi**2 * d**5) / 1e10
    for _, _, d, length in PIPES
]
NV = 18  # pi1..6, f1..4, c1..2, R, w1..5


def _model():
    cost = np.zeros(NV)
    cost[12] = RECEIPT_COST
    for i, (*_, bid) in enumerate(TRANSFERS):
        cost[13 + i] = -bid
    bounds = [(PMIN**2, PMAX**2)] * 6 + [(-FMAX, FMAX)] * 4 + [(0, 1000)] * 3
    bounds += [(0, wmax) for _, _, wmax, _ in TRANSFERS]
    aeq = np.zeros((6, NV))
    for n in range(1, 7):
        for i, (a, b, *_) in enumerate(PIPES):
            aeq[n - 1, 6 + i] = (b == n) - (a == n)
        for i, (a, b) in enumerate(COMPRESSORS):
            aeq[n - 1, 10 + i] = (b == n) - (a == n)
        for i, (_, node, *_) in enumerate(TRANSFERS):
            if node == n:
                aeq[n - 1, 13 + i] = -1
    aeq[0, 12] = 1
    aub, bub = [], []
    for a, b in COMPRESSORS:  # pi_b <= 4 pi_a ; pi_a <= pi_b
        r = np.zeros(NV)
        r[b - 1], r[a - 1] = 1, -4
        aub.append(r)
        bub.append(0.0)
        r = np.zeros(NV)
        r[a - 1], r[b - 1] = 1, -1
        aub.append(r)
        bub.append(0.0)
    return cost, bounds, aeq, aub, bub


def tangent_lp(dirs, nseg=SEGMENTS):
    """gtopt's tangent LP with pipe orientations ``dirs`` (±1)."""
    cost, bounds, aeq, aub, bub = _model()
    aub, bub = list(aub), list(bub)
    for i, (a, b, *_) in enumerate(PIPES):
        s = dirs[i]
        bounds[6 + i] = (0, FMAX) if s > 0 else (-FMAX, 0)
        hi, lo = (a, b) if s > 0 else (b, a)
        for k in range(nseg):
            t = (2 * k + 1) * FMAX / (2 * nseg)
            r = np.zeros(NV)
            r[hi - 1], r[lo - 1], r[6 + i] = -1, 1, 2 * K[i] * t * s
            aub.append(r)
            bub.append(K[i] * t * t)
    return linprog(
        cost,
        A_ub=np.array(aub),
        b_ub=bub,
        A_eq=aeq,
        b_eq=np.zeros(6),
        bounds=bounds,
        method="highs",
    )


def exact_nlp(x0):
    """Weymouth equality NLP (SLSQP) from ``x0``."""
    cost, bounds, aeq, aub, bub = _model()
    cons = [
        {"type": "eq", "fun": lambda x: aeq @ x},
        {"type": "ineq", "fun": lambda x: np.array(bub) - np.array(aub) @ x},
    ]
    for i, (a, b, *_) in enumerate(PIPES):
        cons.append(
            {
                "type": "eq",
                "fun": lambda x, i=i, a=a, b=b: (
                    (x[a - 1] - x[b - 1] - K[i] * x[6 + i] * abs(x[6 + i])) / 1000.0
                ),
            }
        )
    return minimize(
        lambda x: cost @ x,
        x0,
        bounds=bounds,
        constraints=cons,
        method="SLSQP",
        options={"maxiter": 3000, "ftol": 1e-12},
    )


def main() -> None:
    best_lp = None
    for dirs in itertools.product([1, -1], repeat=len(PIPES)):
        r = tangent_lp(dirs)
        if r.status == 0 and (best_lp is None or r.fun < best_lp[1].fun):
            best_lp = (dirs, r)
    assert best_lp is not None
    dirs, lp = best_lp
    nlp = exact_nlp(lp.x)
    assert nlp.success, nlp.message
    welfare_max = sum(w * bid for _, _, w, bid in TRANSFERS)

    pipes = []
    for i, ((a, b, *_), s) in enumerate(zip(PIPES, dirs), start=1):
        fa, fb = (a, b) if s > 0 else (b, a)
        pipes.append(
            {
                "uid": i,
                "name": f"P{i}",
                "gas_node_a": f"N{fa}",
                "gas_node_b": f"N{fb}",
                "weymouth_k": K[i - 1],
                "fmax": FMAX,
                "segments": SEGMENTS,
            }
        )
    system = {
        "name": "gasmodels_case6",
        "bus_array": [{"uid": 1, "name": "B"}],
        "gas_node_array": [
            {"uid": n, "name": f"N{n}", "pmin": PMIN, "pmax": PMAX} for n in range(1, 7)
        ],
        "gas_supply_array": [
            {
                "uid": 1,
                "name": "R1",
                "gas_node": "N1",
                "fmax": 1000.0,
                "cost": RECEIPT_COST,
            }
        ],
        "gas_demand_array": [
            {
                "uid": i,
                "name": name,
                "gas_node": f"N{node}",
                "load": wmax,
                "fail_cost": bid,
            }
            for i, (name, node, wmax, bid) in enumerate(TRANSFERS, start=1)
        ],
        "gas_pipe_array": pipes,
        "gas_compressor_array": [
            {
                "uid": i,
                "name": f"C{i}",
                "gas_node_in": f"N{a}",
                "gas_node_out": f"N{b}",
                "fmax": 1000.0,
                "max_ratio": 2.0,
                "fuel_model": "none",
                "allow_bypass": False,
            }
            for i, (a, b) in enumerate(COMPRESSORS, start=1)
        ],
    }
    planning = {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {"use_single_bus": True, "scale_objective": 1},
        },
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": system,
    }
    (HERE / "gas_case6.json").write_text(json.dumps(planning, indent=1) + "\n")
    expected = {
        "published": {
            "wp_objective": -253.683,
            "wp_receipt": 123.68219,
            "lrwp_objective": -260.001,
            "lrwp_receipt": 130.0004,
        },
        "welfare_offset": welfare_max,
        "pipe_orientation": [p["gas_node_a"] + "->" + p["gas_node_b"] for p in pipes],
        "exact": {
            "objective": nlp.fun,
            "receipt": nlp.x[12],
            "pressure": {f"N{n}": math.sqrt(nlp.x[n - 1]) for n in range(1, 7)},
        },
        "tangent_lp": {
            "objective": lp.fun,
            "receipt": lp.x[12],
            "gtopt_objective": welfare_max + lp.fun,
        },
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    print(json.dumps(expected, indent=1))


if __name__ == "__main__":
    main()
