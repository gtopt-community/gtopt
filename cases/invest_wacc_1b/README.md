# invest_wacc_1b — small modules vs one large block, per-candidate rates

The load curve of [`invest_screening_1b`](../invest_screening_1b/).
Base load can be built as `base_small` (50 MW modules, up to 16,
3.2 M\$/MW) or as `base_large` (one 600 MW block, 2.6 M\$/MW: economies
of scale), both 30 years; `mid` (100 MW × 20, 1.1 M\$/MW, 25 y) and
`peak` (50 MW × 40, 0.5 M\$/MW, 20 y).  Every candidate gives
`overnight_capcost` and `economic_life`; the annuity is computed by
gtopt.  See
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§6 and §7.

| File | Rates |
|------|-------|
| `invest_wacc_uniform_lp.json`, `_mip.json` | no `wacc`: every candidate at `simulation.annual_discount_rate` = 8 % |
| `invest_wacc_project_lp.json`, `_mip.json` | `wacc` per candidate: small 7 %, large 10 %, mid and peak 8 %; social rate 6 % |

Results (HiGHS 1.16; VOLL 10,000 \$/MWh, no load shed): with one rate
the LP builds 550 MW of `base_large` (`base_small` worth 230,951 \$/MW
vs its 284,248 annuity); with per-candidate rates it builds 550 MW of
`base_small` (`base_large` worth 257,876 vs 275,806).  Every built
candidate recovers its annuity at its own rate; the peaker (320 MW) is
paid by the capacity rent of the top block.  MIP uplift at
fixed-integer prices: 103,279,598 \$ (uniform, 600 MW block) vs
58,381,714 \$ (per-candidate, 50 MW modules); relaxed: 4,458,015 vs
2,501,290 \$.

```bash
gtopt invest_wacc_uniform_lp.json --solver highs \
  --output-directory out_uniform_lp --write-out all
gtopt invest_wacc_project_lp.json --solver highs \
  --output-directory out_project_lp --write-out all
gtopt invest_wacc_uniform_mip.json --solver highs \
  --output-directory out_uniform_mip --write-out all
gtopt invest_wacc_project_mip.json --solver highs \
  --output-directory out_project_mip --write-out all
gtopt_cost_recovery out_uniform_lp out_project_lp
```
