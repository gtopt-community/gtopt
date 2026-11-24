# `uc_pricing_relaxed` — UC pricing toy, `pricing_mode = "relaxed"`

The [`uc_pricing_fixed`](../uc_pricing_fixed/README.md) system (two
buses, two hours, a slow and a fast-start unit) priced from the LP
relaxation of the whole MIP.  The dispatch is the MIP dispatch
(`g_slow` 100 / 80 MW, `g_fast` 40 / 0 MW).  It is a mixed-integer
problem.

## Results

λ = 60 $/MWh in hour 1 (the `g_fast` start-up spread over its relaxed
capacity) and 12.5 $/MWh in hour 2 (the `g_slow` no-load spread over
120 MW).  Uplift: make-whole 600 (`g_fast`), lost opportunity 1100
(`g_slow`), total 1700 — the lowest of the three modes.  `pricing.csv`:
`relaxed,relaxed,0,1,4,0,0` (4 integer columns relaxed).

See [`uc_pricing_fixed`](../uc_pricing_fixed/README.md) for the system,
the hand derivation and the comparison table.

## Run

```bash
cd cases/uc_pricing_relaxed
gtopt uc_pricing_relaxed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_relaxed.json
```
