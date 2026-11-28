# invest_multi_3b — supply, network, storage and demand-side projects

Three buses (north, city, coast), one representative 24-h day, VOLL
10,000 \$/MWh, social `annual_discount_rate` 6 %.  Existing: coal
250 MW at 40 \$/MWh (city), gas 400 MW at 90 \$/MWh (coast), corridors
north–city 100 MW and city–coast 150 MW.  Candidates:

| Candidate | Kind | Cost | Life | Rate |
|-----------|------|------|-----:|-----:|
| solar (north, with profile) | Generator | 0.7 M\$/MW | 25 | 7 % |
| ccgt (city, 55 \$/MWh) | Generator | 1.0 M\$/MW | 25 | 8 % |
| north_city, city_coast, north_coast | Line | 0.3 / 0.25 / 0.6 M\$/MW | 40 | 5 % |
| bess (city) | Battery | 200 k\$/MWh + 100 k\$/MW | 15 | 8 % |
| electrolyser (north → h2) | CarrierConverter | 0.9 M\$/MW<sub>e</sub>, η 0.7, 2 \$/MWh | 20 | 9 % (private) |
| desal (coast, 3.5 kWh/m³) | Demand, `fcost` 342.86 \$/MWh = 1.2 \$/m³ | 6.86 M\$/MW<sub>e</sub> | 25 | social (no `wacc`) |

Hydrogen offtake: `HydrogenDemand` up to 150 MW<sub>LHV</sub> at
3 \$/kg (90.01 \$/MWh<sub>LHV</sub>).  City load: 1.5 × the 24-h profile
of `exp_bat_4b_24`.  All data are illustrative round numbers.

| File | Variant |
|------|---------|
| `invest_multi_lp.json` | continuous builds |
| `invest_multi_mip.json` | larger modules, `integer_expmod` on every candidate |
| `invest_multi_lp_nodesal.json` | the LP without the desalination candidate |
| `invest_multi_lp_shortrun.json` | the fleet planned without desalination, frozen, plus the 80 MW plant |

Results (HiGHS 1.16, objective 542,786 \$/day, no unserved energy):
solar 916.6 MW, CCGT 76.0 MW, north–city +484.7 MW, battery
212.2 MWh / 60.7 MW and the electrolyser 214.3 MW<sub>e</sub> each
recover opex + annuity exactly at their own rate.  The desalination
plant is built to its 80 MW bound: water value 658,286 \$/day against
electricity 106,973 and the social-rate annuity 117,570 \$/day
(benefit–cost 2.93).  Adding the plant to the fleet planned without it
raises the mean coast price from 55.26 to 61.88 \$/MWh and generator
revenue by 20 %; the long-run plan builds +59 MW solar, +30 MW CCGT and
+49 MW north–city for it.  MIP uplift 27,328 \$/day at fixed-integer
prices, 649 \$/day at relaxed prices.  See
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§9.

```bash
for v in lp mip lp_nodesal lp_shortrun; do
  gtopt invest_multi_$v.json --solver highs \
    --output-directory out_$v --write-out all
done
gtopt invest_multi_mip.json --solver highs \
  --output-directory out_mip_relaxed --write-out all \
  --set model_options.pricing_mode=relaxed
gtopt_cost_recovery out_lp out_mip out_mip_relaxed
gtopt_cost_recovery out_lp_nodesal out_lp_shortrun out_lp --price-blocks
```

`golden/<variant>/` holds the outputs the tool reads (used by the
`gtopt_cost_recovery` tests).

## Hydrology / weather scenarios and the social CVaR

`invest_multi_scen_expected.json` and `invest_multi_scen_cvar.json`
add three weather scenarios (sunny / cloudy / overcast, p 0.5 / 0.3 /
0.2, solar profile scaled 1.0 / 0.6 / 0.3, `risk_index` = the scale).
The second file sets `model_options.risk_measure = cvar`
(α = 0.8, λ = 0.5).  HiGHS: risk neutral builds solar 661 MW and CCGT
115 MW (objective 613,554); the CVaR plan builds solar 365 MW and CCGT
176 MW (objective 657,614), with π̃ = 0.25 / 0.15 / 0.60.  See
`docs/methods/investment-economics.md` §7.4.
