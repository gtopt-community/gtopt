# invest_cl_screening_1b — screening case under Chilean rules

The [`invest_screening_1b`](../invest_screening_1b/) case (one bus, one
8760-h stage, six-block load-duration curve, candidates base 3.0 M\$/MW
30 y 10 \$/MWh, mid 1.1 M / 25 / 45, peak 0.5 M / 20 / 110, nuclear
6.0 M / 40 / 5, all at 8 %) with two changes so the optimal plan has **no
unserved energy**, as Chilean expansion studies assume:

* `demand_fail_cost` = **795.11 \$/MWh**, the CNE *costo de falla de
  larga duración* of the SEN for a 0–5 % deficit (Res. Ex. 432/2026,
  DO 21-Aug-2026,
  <https://www.diariooficial.interior.gob.cl/publicaciones/2026/08/21/44530/01/2857667.pdf>);
* the second load block (60 h) is at 1,000 MW instead of 950 MW, so the
  peak lasts 80 h/yr.

Why the load shape and not more candidates: the LP builds a peaker MW
only if it is cheaper than shedding, i.e. if the peak lasts longer than
annuity / (costo de falla − peaker cost) = 50,926 / (795.11 − 110) =
74 h/yr.  With the original 20-h peak the LP sheds 50 MW whatever the
candidate capacity offered; with an 80-h peak it builds the peak in
full.

| File | Variant |
|------|---------|
| `invest_cl_screening_lp.json` | continuous builds |
| `invest_cl_screening_mip.json` | integer modules base 300, mid 200, peak 100, nuclear 300 MW |

Results (HiGHS 1.16, `golden/`): LP builds base 550 / mid 150 / peak
300 MW, MIP 600 / 200 / 200 MW; no unserved energy in any block.  The
LP still prices the peak blocks above the peaker's cost (795.11 and
730.40 \$/MWh): that rent is the peaker's annuity (peak-load pricing).
The Chilean CMg does not pay it — see
[docs/methods/private-investor-evaluation-cl.md](../../docs/methods/private-investor-evaluation-cl.md).

```bash
gtopt invest_cl_screening_lp.json --solver highs \
  --output-directory out_lp --write-out all
gtopt invest_cl_screening_mip.json --solver highs \
  --output-directory out_mip --write-out all
gtopt invest_cl_screening_mip.json --solver highs \
  --output-directory out_mip_relaxed --write-out all \
  --set model_options.pricing_mode=relaxed
gtopt_private_eval out_lp --market cl \
  --config ../../scripts/gtopt_private_eval/configs/invest_screening.json
```
