# `uc_pricing_lit_scarf_relaxed` — Scarf's example, `pricing_mode = "relaxed"`

The [`uc_pricing_lit_scarf_fixed`](../uc_pricing_lit_scarf_fixed/README.md)
system (Scarf's example as adapted by Hogan & Ring 2003, demand 61)
priced from the LP relaxation.  Single period, so each unit's relaxation
is the convex hull of its own feasible set and the relaxed price is the
exact convex-hull (minimum-uplift) price: 3 + 53/16 = 6.3125, the
Smokestack average cost at capacity (published 6.312).  Uplift at that
price: 3.875 (published 3.875) — the partly loaded Smokestack's loss
3.3125 plus 3 × 0.1875 lost opportunity of the idle High Tech units.
The dispatch is the MIP dispatch.  It is a mixed-integer problem.

Sources and the comparison table: see
[`uc_pricing_lit_scarf_fixed`](../uc_pricing_lit_scarf_fixed/README.md).

## Run

```bash
cd cases/uc_pricing_lit_scarf_relaxed
gtopt uc_pricing_lit_scarf_relaxed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_scarf_relaxed.json
```
