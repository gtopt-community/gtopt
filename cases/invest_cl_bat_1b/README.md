# invest_cl_bat_1b — battery case under Chilean rules

The [`invest_bat_1b`](../invest_bat_1b/) case (one bus, one
representative day, solar 400 MW, coal 120 MW at 35 \$/MWh, gas 200 MW
at 90 \$/MWh, candidate battery 200 k\$/MWh + 100 k\$/MW, 15 y, 8 %) with
`demand_fail_cost` = **795.11 \$/MWh**, the CNE *costo de falla de larga
duración* (Res. Ex. 432/2026,
<https://www.diariooficial.interior.gob.cl/publicaciones/2026/08/21/44530/01/2857667.pdf>).

The plan has no unserved energy and its prices never exceed the gas
unit's cost (90 \$/MWh), so the battery's revenue is pure arbitrage —
the same plan and prices as `invest_bat_1b`.  LP: 288.88 MWh / 84 MW;
MIP (20 MWh modules): 300 MWh / 100 MW.

```bash
gtopt invest_cl_bat_lp.json --solver highs \
  --output-directory out_lp --write-out all
gtopt invest_cl_bat_mip.json --solver highs \
  --output-directory out_mip --write-out all
gtopt_private_eval out_lp --market cl \
  --config ../../scripts/gtopt_private_eval/configs/invest_bat.json
```
