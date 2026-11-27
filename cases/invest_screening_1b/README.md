# invest_screening_1b — screening curve and peak-load pricing

One bus, one stage of 8760 h split into a six-block load-duration curve,
value of lost load 10,000 \$/MWh (`demand_fail_cost`), four candidate
generators with ample room (up to 2000 MW each): the optimum never
sheds load.  The classic screening-curve / peak-load-pricing problem of
Steiner (1957) and Boiteux (1960); see
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§2, §5 and §7.

| Block | 1 | 2 | 3 | 4 | 5 | 6 |
|-------|--:|--:|--:|--:|--:|--:|
| Hours | 20 | 60 | 400 | 1500 | 3000 | 3780 |
| Load MW | 1020 | 950 | 850 | 700 | 550 | 400 |

| Candidate | Overnight \$/MW | Life y | Variable \$/MWh |
|-----------|----------------:|-------:|-----------------:|
| base | 3.0 M | 30 | 10 |
| mid | 1.1 M | 25 | 45 |
| peak | 0.5 M | 20 | 110 |
| nuclear | 6.0 M | 40 | 5 |

The JSONs carry the annuity (`annual_capcost` = overnight × CRF).
The cost data are illustrative round numbers, not taken from any
operator.

| File | Variant |
|------|---------|
| `invest_screening_lp.json` | 8 % for all, continuous builds (20 modules of 100 MW) |
| `invest_screening_mip.json` | integer modules base 300, mid 200, peak 50, nuclear 300 MW |
| `invest_screening_social.json` | 6 % for all (social rate) |
| `invest_screening_private.json` | private WACC base 11 %, mid 8 %, peak 9 %, nuclear 12 % |
| `invest_screening_fom.json` | 8 % plus fixed O&M (`fixed_om_cost`): base 60, mid 25, peak 10, nuclear 100 k\$/MW-year |

Results (HiGHS 1.16): LP builds base 550 / mid 150 / peak 320 MW (the
whole 1020-MW peak), each with profit 0 (objective 243,147,616 \$);
nuclear is worth 310,282 \$/MW against an annuity of 503,161.  The
peaker is paid by the capacity rent of the top block (price
2,656.31 = 110 + 50,926 / 20 \$/MWh), not by unserved energy.  The MIP
builds 600 / 200 / 250 MW; make-whole uplift 126,690,245 \$ at
fixed-integer prices (the 30 MW surplus removes the capacity rent),
8,326,626 \$ at relaxed prices.  Social 6 %: same mix; private WACC:
400 / 300 / 320, profit 0 for each at its own rate.

```bash
gtopt invest_screening_lp.json --solver highs \
  --output-directory out_lp --write-out all
gtopt invest_screening_mip.json --solver highs \
  --output-directory out_mip --write-out all
gtopt invest_screening_mip.json --solver highs \
  --output-directory out_mip_relaxed --write-out all \
  --set model_options.pricing_mode=relaxed
gtopt invest_screening_social.json --solver highs \
  --output-directory out_social --write-out all
gtopt invest_screening_private.json --solver highs \
  --output-directory out_private --write-out all
gtopt invest_screening_fom.json --solver highs \
  --output-directory out_fom --write-out all
gtopt_cost_recovery out_lp out_mip out_mip_relaxed
gtopt_cost_recovery out_lp --price-blocks   # λ = cost + capacity rent
```

`golden/<variant>/` holds the outputs the tool reads (used by the
`gtopt_cost_recovery` tests).
