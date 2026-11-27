# invest_wacc_2c — per-candidate rate, end-to-end check

One bus, one 8760-h block, 100 MW of load, two candidates with the same
variable cost (10 \$/MWh) and 25-year life:

| Candidate | Modules | Overnight \$/MW | `wacc` | Annuity \$/MW-year |
|-----------|---------|----------------:|-------:|--------------------:|
| `small` | 10 MW × 20 | 1.2 M | 9 % | 122,168 |
| `large` | 100 MW × 2 | 1.0 M | 7 % | 85,811 |

`simulation.annual_discount_rate` = 6 % (only the fallback rate here).
The LP builds 100 MW of `large`; its revenue 17,341,052 \$ equals opex
8,760,000 + capex 8,581,052.  `output/` is the e2e golden
(`add_e2e_case(invest_wacc_2c ...)`).  See
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§6.
