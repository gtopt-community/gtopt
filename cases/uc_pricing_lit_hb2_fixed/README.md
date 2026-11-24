# `uc_pricing_lit_hb2_fixed` — Hua & Baldick Example 2, `pricing_mode = "fixed"`

Three hours, two units with ramp limits; unit 2 is cheap per MWh but has
a 600 $/h no-load cost and a 60 MW/h ramp.  Solved with [`fixed`](.)
(LMP) and [`relaxed`](../uc_pricing_lit_hb2_relaxed/).  It is a
mixed-integer problem.

## Source

B. Hua and R. Baldick, "A Convex Primal Formulation for Convex Hull
Pricing", *IEEE Transactions on Power Systems* 32(5):3814-3823, 2017,
[doi:10.1109/TPWRS.2016.2637718](https://doi.org/10.1109/TPWRS.2016.2637718),
[arXiv:1605.05002](https://arxiv.org/abs/1605.05002) — §5.2, Tables 3-5.

## System

| Unit | Energy $/MWh | No-load $/h | Max MW | Ramp / start-up ramp MW/h | Initial |
|---|---|---|---|---|---|
| `unit1` | 60 | 0 | 100 | 120 / 120 | off |
| `unit2` | 56 | 600 | 100 | 60 / 60 | off |

Demand 70 / 100 / 170 MW.

## Results

MIP dispatch (published Table 4 and gtopt): `unit1` 70 / 40 / 70,
`unit2` 0 / 60 / 100 MW (on in hours 2-3); cost 20960.

| Scheme (paper) | gtopt mode | Published prices | gtopt prices | `unit2` uplift published / tool |
|---|---|---|---|---|
| LMP | `fixed` | 60 / 60 / 60 | 60 / 60 / 60 | 560 / 560 |
| aCHP1 | — | 60 / 60 / 64 | — | 160 / 360 |
| CHP (aCHP2) | — | 60 / 60 / 65.6 | — | 0 / 360 |
| — | `relaxed` | — | 60 / 60 / 820/13 ≈ 63.08 | — |

Expected differences:

- `relaxed` is the LP relaxation of the 3-binary UC with ramp rows
  `p_t − p_{t−1} ≤ RU·u_{t−1} + SU·v_t`.  With ramps that is not the
  convex hull of each unit's multi-period set (H&B add valid
  inequalities to get there), so the hour-3 price 63.08 is below both
  aCHP1 (64) and CHP (65.6).  The same LP written out by hand gives the
  same duals.
- The uplift tool's self-schedule ignores ramps, so at the aCHP1 / CHP
  prices it credits `unit2` with a start in hour 3 at 100 MW (ramp
  infeasible) and reports 360 $ instead of 160 / 0.  `unit1` uplift
  (0 / 120 / 168) matches.

## Run

```bash
cd cases/uc_pricing_lit_hb2_fixed
gtopt uc_pricing_lit_hb2_fixed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_hb2_fixed.json
```
