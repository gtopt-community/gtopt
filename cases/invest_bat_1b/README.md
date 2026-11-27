# invest_bat_1b — battery arbitrage pays energy and power

One bus, one representative day of 24 hourly blocks.  The demand is
twice the hourly demand of [`exp_bat_4b_24`](../exp_bat_4b_24/) (both
loads summed), the solar plant (400 MW) uses the same profile; a coal
unit (120 MW, 35 \$/MWh) and a gas unit (200 MW, 90 \$/MWh) complete the
supply.  The candidate battery has 92 % charge and discharge efficiency,
no standing loss, starts empty, and separate annuities at 8 % over 15
years: energy 200 k\$/MWh → 23,365.91 \$/MWh-year, power 100 k\$/MW →
11,682.95 \$/MW-year (on its converter).  See
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§4 and §5.

| File | Variant |
|------|---------|
| `invest_bat_lp.json` | continuous energy and power |
| `invest_bat_mip.json` | energy in 50 MWh and power in 25 MW modules (`integer_expmod` makes both axes integer) |

Results (HiGHS 1.16): the LP builds 288.88 MWh / 84 MW.  Per day the
arbitrage revenue 21,182 \$ equals energy capex 18,493 \$ + power capex
2,689 \$.  The MIP builds 300 MWh / 100 MW: −8,979 \$/day (−40.1 %) at
fixed-integer prices, −306 \$/day (−1.4 %) at relaxed prices.

```bash
gtopt invest_bat_lp.json --solver highs \
  --output-directory out_lp --write-out all
gtopt invest_bat_mip.json --solver highs \
  --output-directory out_mip --write-out all
gtopt invest_bat_mip.json --solver highs \
  --output-directory out_mip_relaxed --write-out all \
  --set model_options.pricing_mode=relaxed
gtopt_cost_recovery out_lp --per-year
```
