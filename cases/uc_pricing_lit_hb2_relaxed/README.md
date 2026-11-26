# `uc_pricing_lit_hb2_relaxed` — Hua & Baldick Example 2, `pricing_mode = "relaxed"`

The [`uc_pricing_lit_hb2_fixed`](../uc_pricing_lit_hb2_fixed/README.md)
system (three hours, ramp-limited units) priced from the LP relaxation:
60 / 60 / 820/13 ≈ 63.08 $/MWh.  The published convex-hull price is
60 / 60 / 65.6.  The difference is expected: with ramp limits the LP
relaxation of the 3-binary UC is weaker than the convex hull (see the
fixed case's README).  The dispatch is the MIP dispatch.  It is a
mixed-integer problem.

## Run

```bash
cd cases/uc_pricing_lit_hb2_relaxed
gtopt uc_pricing_lit_hb2_relaxed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_hb2_relaxed.json
```
