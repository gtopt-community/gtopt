#!/usr/bin/env python3
"""Add `water_value_hint` per reservoir from a boundary-cuts CSV.

Usage: water_value_hints.py case.json boundary_cuts.csv out.json
           [--target T] [--clamp K]

The hint is the median |coefficient| ($/hm3) of each reservoir's column in
the cuts file (header: iteration,scene,rhs,<reservoir names...>).  With
--target the JSON also gets `lp_matrix_options.water_dual_target` (and
`water_dual_clamp` with --clamp).  See docs/planning-options.md.
"""

import argparse
import csv
import json
import statistics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("cuts")
    ap.add_argument("out")
    ap.add_argument("--target", type=float)
    ap.add_argument("--clamp", type=float)
    a = ap.parse_args()
    with open(a.cuts, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    hdr = rows[0]
    hint = {}
    for j, name in enumerate(hdr[3:], start=3):
        v = [abs(float(r[j])) for r in rows[1:] if r[j] not in ("", "0")]
        if v:
            hint[name] = statistics.median(v)
    with open(a.case, encoding="utf-8") as f:
        d = json.load(f)
    for r in d["system"].get("reservoir_array", []):
        if r["name"] in hint:
            r["water_value_hint"] = hint[r["name"]]
    lm = d["options"].setdefault("lp_matrix_options", {})
    if a.target:
        lm["water_dual_target"] = a.target
    if a.clamp:
        lm["water_dual_clamp"] = a.clamp
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)


if __name__ == "__main__":
    main()
