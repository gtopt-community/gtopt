# invest_fom_1c — fixed O&M of an expansion candidate, end-to-end check

One bus, one 8760-h block, 100 MW of load, one candidate `plant`
(10 \$/MWh, 10-MW modules, 1.0 M\$/MW overnight, 25 years, `wacc` 7 %:
annuity 85,811 \$/MW-year) with `fixed_om_cost` 25,000 \$/MW-year.

The LP builds 100 MW and charges annuity + fixed O&M on them: objective
8,760,000 + 100 × (85,811 + 25,000) = 19,841,052 \$.  The price is
10 + 110,811 / 8760 = 22.65 \$/MWh, so the plant's revenue 19,841,052 \$
= opex 8,760,000 + fixed O&M 2,500,000 + capex 8,581,052: zero profit
with fixed O&M.  `output/` is the e2e golden
(`add_e2e_case(invest_fom_1c ...)`).  See
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§7.3.
