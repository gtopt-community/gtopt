# `uc_pricing_lit_scarf_fixed` — Scarf's example, `pricing_mode = "fixed"`

Scarf's Smokestack / High Tech economy as adapted by Hogan & Ring (2003)
with a Med Tech type, at a demand of 61 units, single period.  Solved
with [`fixed`](.) (O'Neill et al. "IP" pricing) and
[`relaxed`](../uc_pricing_lit_scarf_relaxed/) (here equal to the
convex-hull / minimum-uplift price).  It is a mixed-integer problem.

## Source

- R. P. O'Neill, P. M. Sotkiewicz, B. F. Hobbs, M. H. Rothkopf,
  W. R. Stewart Jr., "Efficient market-clearing prices in markets with
  nonconvexities", *European Journal of Operational Research*
  164(1):269-285, 2005,
  [doi:10.1016/j.ejor.2003.12.011](https://doi.org/10.1016/j.ejor.2003.12.011)
  — Tables 1-3 (Smokestack / High Tech, demands 55-70).
- W. W. Hogan, B. J. Ring, "On Minimum-Uplift Pricing for Electricity
  Markets", Harvard Electricity Policy Group, 19 Mar 2003, section "An
  Example" and the appended table (demands 1-161) —
  <https://lmpmarketdesign.com/papers/Hogan_Ring_minuplift_031903.pdf>.

The unit parameters below are typed in from those papers; no data file
is vendored.

## System

One bus, demand 61.  Each unit is a generator plus a commitment whose
fixed (start-up) cost is the no-load cost of the 1-hour period; units
start on (`initial_status = 1`) so `u` carries the whole fixed cost.

| Type | Units | Capacity | Min output | Fixed cost $ | Marginal cost $/unit |
|---|---|---|---|---|---|
| Smokestack (`ss1..6`) | 6 | 16 | 0 | 53 | 3 |
| High Tech (`ht1..5`) | 5 | 7 | 0 | 30 | 2 |
| Med Tech (`mt1..5`) | 5 | 6 | 2 | 0 | 7 |

## Results

| | Published | gtopt |
|---|---|---|
| Commitment / output | 3 Smokestack (47), 2 High Tech (14), cost 388 | same |
| `fixed` price | IP commodity price 3 | 3 |
| Start-up prices (fixed) | Smokestack 53, High Tech 23 (O'Neill Table 3, set I) | 53, 23 (u reduced cost + capacity rent) |
| IP uplift Σ start-up prices | 205 | make-whole 205 |
| `relaxed` price | MinUP price 6.312 | 101/16 = 6.3125 |
| Uplift at the `relaxed` price | 3.875 | 3.875 |

`fast_start` is not run here: no unit has min up / down times, so every
unit is fast-start by the auto rule and `fast_start` = `relaxed`.  The
unit test `test/source/test_pricing_literature.cpp` covers demands 1, 10,
22, 40, 61, 88 and 145 in all three modes, and
`scripts/gtopt_pricing_uplift/tests/test_literature.py` the uplift.

## Run

```bash
cd cases/uc_pricing_lit_scarf_fixed
gtopt uc_pricing_lit_scarf_fixed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_scarf_fixed.json
```
