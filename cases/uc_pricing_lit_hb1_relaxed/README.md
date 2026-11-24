# `uc_pricing_lit_hb1_relaxed` — Hua & Baldick Example 1, `pricing_mode = "relaxed"`

The [`uc_pricing_lit_hb1_fixed`](../uc_pricing_lit_hb1_fixed/README.md)
system (one period, 35 MW, unit 1 at 35 MW, unit 2 off) priced from the
LP relaxation: the block-loaded `unit2` becomes divisible at its average
cost 10 + 100/50 = 12 $/MWh, the published convex-hull price (CHP).
Uplift: `unit1` 1430 (its loss at 12 $/MWh), `unit2` 0 — published 1430
/ 0. The dispatch is the MIP dispatch. It is a mixed-integer problem.

Source and comparison table: see
[`uc_pricing_lit_hb1_fixed`](../uc_pricing_lit_hb1_fixed/README.md).

## Run

```bash
cd cases/uc_pricing_lit_hb1_relaxed
gtopt uc_pricing_lit_hb1_relaxed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_hb1_relaxed.json
```
