# `uc_pricing_lit_hb1_fast_start` — Hua & Baldick Example 1, `pricing_mode = "fast_start"`

The [`uc_pricing_lit_hb1_fixed`](../uc_pricing_lit_hb1_fixed/README.md)
system (one period, 35 MW, unit 1 at 35 MW, unit 2 off) priced with
`fast_start`, `unit1` flagged `fast_start: true` and `unit2`
`fast_start: false`: `unit2` stays pinned off, so the pricing LP is the
problem without it — the paper's CHPq. λ = 50 + 100/50 = 52 $/MWh
(published 52); `unit1` make-whole 30 (published 30). The tool also
reports `unit2`'s 2000 lost opportunity, which CHPq does not pay by
design. The dispatch is the MIP dispatch. It is a mixed-integer problem.

Source and comparison table: see
[`uc_pricing_lit_hb1_fixed`](../uc_pricing_lit_hb1_fixed/README.md).

## Run

```bash
cd cases/uc_pricing_lit_hb1_fast_start
gtopt uc_pricing_lit_hb1_fast_start.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_hb1_fast_start.json
```
