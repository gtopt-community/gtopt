# `uc_pricing_lit_hb1_fixed` — Hua & Baldick Example 1, `pricing_mode = "fixed"`

One period, 35 MW, two units both off initially; unit 2 is block-loaded.
Solved with [`fixed`](.) (LMP), [`relaxed`](../uc_pricing_lit_hb1_relaxed/)
(convex-hull price) and [`fast_start`](../uc_pricing_lit_hb1_fast_start/)
with only unit 1 fast-start (CHPq).  It is a mixed-integer problem.

## Source

B. Hua and R. Baldick, "A Convex Primal Formulation for Convex Hull
Pricing", *IEEE Transactions on Power Systems* 32(5):3814-3823, 2017,
[doi:10.1109/TPWRS.2016.2637718](https://doi.org/10.1109/TPWRS.2016.2637718),
[arXiv:1605.05002](https://arxiv.org/abs/1605.05002) — §5.1, Tables 1-2
(after Schiro et al. 2015, with start-up costs added).

## System

| Unit | Start-up $ | Energy $/MWh | Min MW | Max MW |
|---|---|---|---|---|
| `unit1` | 100 | 50 | 10 | 50 |
| `unit2` | 100 | 10 | 50 | 50 (block-loaded) |

Demand 35 MW.  The only feasible commitment is unit 1 alone at 35 MW
(cost 1850).

## Results

| Scheme (paper) | gtopt mode | Published π / U1 / U2 | gtopt π | gtopt uplift U1 / U2 |
|---|---|---|---|---|
| LMP | `fixed` | 50 / 100 / 1900 | 50 | 100 / 1900 |
| CHP | `relaxed` | 12 / 1430 / 0 | 12 | 1430 / 0 |
| CHPq | `fast_start`, only `unit1` fast-start | 52 / 30 / — | 52 | 30 / 2000 |

CHPq solves the convex-hull problem without the unqualified unit 2.
`fast_start` with `unit2` not fast-start pins its `u` at the incumbent
0, which removes it from the pricing LP — the same problem.  The paper
does not compensate unit 2 under CHPq ("—"); the uplift tool reports
every commitment, hence its 2000 lost opportunity (50·(52 − 10) − 100).

## Run

```bash
cd cases/uc_pricing_lit_hb1_fixed
gtopt uc_pricing_lit_hb1_fixed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_hb1_fixed.json
```
