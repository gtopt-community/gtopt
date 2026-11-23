# `reservoir_water_value` — water value of a spilling reservoir

End-to-end cases that pin the sign and the size of a reservoir's water
value (`Reservoir/water_value_dual`, the dual of the storage balance row,
published in $/hm³ with "water is valuable" = positive) when the
reservoir is full and spills.  The rule they check:

* if water can leave the reservoir through a **free** exit, the water
  value of a spilling reservoir is **0** (never negative);
* if **every** exit is priced at `c` $/(m³/s)·h, one more hm³ costs
  `c / fcr` to get rid of, so the water value is **−c / fcr** (and never
  below it);
* a cost on the stored volume (`ecost`, $/hm³ per block) with no exit
  gives **−ecost × (blocks left)**;
* when water is scarce the water value is **thermal cost × production
  factor / fcr > 0**.

Negative water values are a legitimate result: the `*_sddp` variants
check that SDDP carries them through its Benders cuts and reproduces the
monolithic objective and duals.

## Common data

| | |
|---|---|
| network | 1 bus, demand 100 MW |
| generators | `g_hydro` 0 $/MWh, `g_thermal` 50 $/MWh (200 MW each) |
| hydro | `j_rsv` → `ww_turb` (turbine, pf 1 MW/(m³/s)) → `j_sea` (free drain) |
| reservoir | `rsv` on `j_rsv`, emax 0.5 hm³, eini 0.5 hm³, `flow_conversion_rate` 0.0036 hm³/(m³/s·h) |
| time | 3 stages × 1 block × 2 h (SDDP variants: 3 phases, 1 stage each) |
| inflow | 500, 500, 0 m³/s (wet, wet, dry); `ecost`: 500 in every stage |

`flow_conversion_rate` is set explicitly (0.0036 is also the default;
older builds defaulted a JSON reservoir without it to 3.6).

Derived quantities (fcr = 0.0036, duration 2 h):

* turbine flow = 100 m³/s → 0.72 hm³ per stage; inflow 3.6 hm³ per wet
  stage → the full reservoir spills 2.88 hm³ = 400 m³/s per wet stage,
  800 m³/s summed over stages 1–2 (the LP may shift spill between
  stages 1 and 2, so only the sum is checked);
* dry stage 3: the 0.5 hm³ give 0.5/0.0036 = 138.89 MWh, the thermal
  covers 61.11 MWh → **3055.56 $**; water value 50/0.0036 =
  **13888.89 $/hm³**;
* exit cost c = 3.6 $/(m³/s)·h: spill cost 3.6 × 800 × 2 = 5760 $,
  water value −3.6/0.0036 = **−1000 $/hm³**.

## Cases

| JSON | exit(s) of the full reservoir | water value st. 1, 2 | st. 3 | objective |
|------|------|------|------|------|
| `spill_free` | spillway, `spillway_cost` 0 | 0 | 13888.89 | 3055.56 |
| `spill_cost` | spillway, `spillway_cost` 3.6 (only exit) | −1000 | 13888.89 | 8815.56 |
| `spill_cost_free_exit` | spillway 3.6 + free `j_rsv` drain | 0 | 13888.89 | 3055.56 |
| `drain_cost` | `j_rsv` drain, `drain_cost` 3.6 (only exit) | −1000 | 13888.89 | 8815.56 |
| `waterway_fcost` | waterway `ww_spill`, `fcost` 3.6 (only exit) | −1000 | 13888.89 | 8815.56 |
| `ecost` | none (emax 100), `ecost` 100 $/hm³ | −300, −200 | −100 | 1878 |
| `spill_cost_sddp` | as `spill_cost`, SDDP | −1000 | 13888.89 | 8815.56 |
| `ecost_sddp` | as `ecost`, SDDP | −300, −200 | −100 | 1878 |

`ecost`: volumes 3.38, 6.26, 9.14 hm³ → objective 100 × 18.78 = 1878;
an extra hm³ stored in stage t stays to the end and pays 100 in each of
the 4 − t remaining blocks.  In `ecost_sddp` the −200 of stage 1 beyond
its own −100 comes only from the cut slope, so the case fails if a
negative state dual is clamped, dropped or sign-flipped in the cuts.

`drain_cost` gave −500 / 5935.56 before `Junction.drain_cost` was priced
with the block duration (`CostHelper::block_ecost`).

## How the cases are checked

`tools/check_expected_values.py` compares the output against the
hand-derived values in `expected.json` (objective, water value per
stage, spill sums, volumes, sign bounds) with `rtol = atol = 1e-6`, and
fails on any mismatch.  Registration (`integration_test/CMakeLists.txt`,
`add_expected_values_case`) creates `e2e_rwv_<case>_solve` and
`e2e_rwv_<case>_check`:

```bash
ctest -R e2e_rwv_ --output-on-failure
# by hand
gtopt spill_cost.json --output-directory /tmp/rwv --solver highs
python3 ../../tools/check_expected_values.py --output-dir /tmp/rwv \
  --expected expected.json --case spill_cost
```
