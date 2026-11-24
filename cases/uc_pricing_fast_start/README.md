# `uc_pricing_fast_start` — UC pricing toy, `pricing_mode = "fast_start"`

The [`uc_pricing_fixed`](../uc_pricing_fixed/README.md) system (two
buses, two hours, a slow and a fast-start unit) priced PJM / NYISO
style: integers fixed at the MIP incumbent except the fast-start unit
`g_fast` (min up/down 1 h ≤ `fast_start_max_hours`), whose status is
relaxed to [0, 1].  The dispatch is the MIP dispatch (`g_slow`
100 / 80 MW, `g_fast` 40 / 0 MW).  It is a mixed-integer problem.

## Results

λ = 60 $/MWh in hour 1 (`g_fast` sets the price at its average cost
50 + 1000/100) and 10 $/MWh in hour 2 (`g_slow` stays fixed, its
no-load is not priced).  Uplift: make-whole 600 (`g_fast`), lost
opportunity 1300 (`g_slow`), total 1900.  `pricing.csv`:
`fast_start,fast_start,0,1,2,2,0` (2 columns relaxed, 2 pinned).

See [`uc_pricing_fixed`](../uc_pricing_fixed/README.md) for the system,
the hand derivation and the comparison table.

## Run

```bash
cd cases/uc_pricing_fast_start
gtopt uc_pricing_fast_start.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_fast_start.json
```
