# `uc_pricing_lit_hb2_relaxed` — Hua & Baldick Example 2, `pricing_mode = "relaxed"`

The [`uc_pricing_lit_hb2_fixed`](../uc_pricing_lit_hb2_fixed/README.md)
system (three hours, ramp-limited units) priced from the LP relaxation:
60 / 60 / 314/5 = 62.8 $/MWh.  The published convex-hull price is
60 / 60 / 65.6.  The difference is expected: the relaxed-LP price is
not the convex-hull price, and with ramp limits the LP relaxation of
the 3-binary UC is weaker than the convex hull (see the fixed case's
README).

The startup / shutdown status rows `v ≤ u`, `w ≤ 1 − u`
(`transition_status_rows`, default on) tighten that relaxation: they
cut off fractional points where a unit starts by more than it is on
(without them, hour 2 has `v = 0.244 > u = 0.146` for `unit2`) and
raise the LP bound from 20569.23 to 20583.75 (MIP 20960).  The hour-3
price then moves from 820/13 ≈ 63.08 to 62.625; a tighter primal does
not move the dual monotonically.  The first-block ramp-up row on the
start-up column (`p₁ − SU·v₁ ≤ 0` instead of the constant `p₁ ≤ SU`)
tightens it further: LP bound 20596.00, hour-3 price 314/5 = 62.8,
closer to the published 64 / 65.6.  `transition_status_rows = false`
reproduces the previous 820/13.  The dispatch is the MIP dispatch.  It is a
mixed-integer problem.

## Run

```bash
cd cases/uc_pricing_lit_hb2_relaxed
gtopt uc_pricing_lit_hb2_relaxed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_hb2_relaxed.json
```
