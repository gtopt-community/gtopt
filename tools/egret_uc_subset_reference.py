#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Egret reference objectives for the gtopt-representable UC subsets.

Builds the matchable subsets of an Egret unit-commitment instance (default
``tiny_uc_1`` from Egret's ``egret/models/tests/uc_test_instances``,
Revised BSD) and solves each with Egret, printing the total cost that
``test/source/test_reserve_bench_egret_tiny_uc.cpp`` checks gtopt against.

Subsets (cumulative; see the C++ test header for the rationale):

  s1  commitment, no-load, single (hot) startup cost, identical PWL cost
      (K secants pmin -> pmax), the system ``reserve_requirement``; ramps
      and startup / shutdown capacity neutralised; every other reserve
      product dropped
  s2  s1 + ramps and startup / shutdown capacity, no reserve
  s3  s2 + reserve
  s4  s1 + lag-dependent (hot / cold) startup cost

Requires ``pip install gridx-egret pyomo highspy`` for ``--solve``.

Usage::

    python tools/egret_uc_subset_reference.py tiny_uc_1.json --solve
    python tools/egret_uc_subset_reference.py tiny_uc_1.json --out DIR
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

SEGMENTS = 4
VARIANTS = ("s1", "s2", "s3", "s4")
_DROP_GEN_FIELDS = (
    "agc_capable",
    "ramp_agc",
    "p_min_agc",
    "p_max_agc",
    "fast_start",
    "supplemental_start",
    "supplemental_non_spinning_capacity",
    "non_spinning_capacity",
)


def poly_cost(gen: dict[str, Any]) -> tuple[float, float, float]:
    """(c0, c1, c2) of a polynomial Egret ``p_cost``."""
    pc = gen["p_cost"]
    if isinstance(pc, str):
        pc = json.loads(pc)
    if pc.get("cost_curve_type") != "polynomial":
        raise ValueError("only polynomial p_cost curves are supported")
    v = pc["values"]
    return float(v["0"]), float(v.get("1", 0.0)), float(v.get("2", 0.0))


def pwl_points(gen: dict[str, Any], k: int = SEGMENTS) -> list[list[float]]:
    """K + 1 points (p, cost) of the quadratic between pmin and pmax."""
    c0, c1, c2 = poly_cost(gen)
    pmin, pmax = float(gen["p_min"]), float(gen["p_max"])
    xs = [pmin + (pmax - pmin) * i / k for i in range(k + 1)]
    return [[x, c0 + c1 * x + c2 * x * x] for x in xs]


def subset(md: dict[str, Any], variant: str) -> dict[str, Any]:
    """Return the Egret model data of ``variant`` (input is not modified)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    out = copy.deepcopy(md)
    sysd = out["system"]
    for key in [k for k in sysd if k.endswith("_requirement")]:
        if key != "reserve_requirement" or variant == "s2":
            del sysd[key]
    for area in out["elements"].get("area", {}).values():
        for key in [k for k in area if k.endswith("_requirement")]:
            del area[key]
    keep_ramp = variant in ("s2", "s3")
    for gen in out["elements"]["generator"].values():
        gen["p_cost"] = {
            "data_type": "cost_curve",
            "cost_curve_type": "piecewise",
            "values": pwl_points(gen),
        }
        if not keep_ramp:
            for field in (
                "ramp_up_60min",
                "ramp_down_60min",
                "startup_capacity",
                "shutdown_capacity",
            ):
                gen[field] = gen["p_max"]
        if variant != "s4":
            gen["startup_cost"] = [gen["startup_cost"][0]]
        for field in _DROP_GEN_FIELDS:
            gen.pop(field, None)
    return out


def solve(md: dict[str, Any], solver: str = "highs") -> float:
    """Total cost of ``md`` solved by Egret (optional dependency)."""
    from egret.data.model_data import ModelData  # pylint: disable=import-outside-toplevel,import-error
    from egret.models.unit_commitment import (  # pylint: disable=import-outside-toplevel,import-error
        solve_unit_commitment,
    )

    result = solve_unit_commitment(ModelData(md), solver, mipgap=1e-9)
    return float(result.data["system"]["total_cost"])


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("instance", type=Path, help="Egret UC JSON instance")
    ap.add_argument("--out", type=Path, default=None, help="write subset JSONs")
    ap.add_argument("--solve", action="store_true", help="solve with Egret")
    ap.add_argument("--solver", default="highs")
    args = ap.parse_args(argv)
    md = json.loads(args.instance.read_text(encoding="utf-8"))
    for variant in VARIANTS:
        sub = subset(md, variant)
        if args.out is not None:
            args.out.mkdir(parents=True, exist_ok=True)
            path = args.out / f"{args.instance.stem}_{variant}.json"
            path.write_text(json.dumps(sub, indent=1), encoding="utf-8")
        if args.solve:
            print(f"{variant} total_cost {solve(sub, args.solver):.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
