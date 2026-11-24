# `uc_pricing_lit_pjm1_fast_start` — PJM fast-start pricing Example 1, `pricing_mode = "fast_start"`

The [`uc_pricing_lit_pjm1_fixed`](../uc_pricing_lit_pjm1_fixed/README.md)
system (PJM 2017 Appendix B, Example 1) priced PJM style: the
block-loaded `g2` / `g4` (fast-start by the auto rule) have their
commitment relaxed to [0, 1], everything else is the MIP solution.
Prices 117 / 76 / 35 $/MWh at b1 / b2 / b3, as published in PJM's
Table 4; `g2` is then whole (uplift 0).  The dispatch is the MIP
dispatch (`g1` 475, `g2` 100, `g3` 125 MW).  It is a mixed-integer
problem.

Source and comparison table: see
[`uc_pricing_lit_pjm1_fixed`](../uc_pricing_lit_pjm1_fixed/README.md).

## Run

```bash
cd cases/uc_pricing_lit_pjm1_fast_start
gtopt uc_pricing_lit_pjm1_fast_start.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_pjm1_fast_start.json
```
