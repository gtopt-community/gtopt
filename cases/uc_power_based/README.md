# `uc_power_based` — power-based unit commitment

Three thermal units on one bus over eight chronological hours, scheduled
with the power-based unit-commitment formulation
(`model_options.uc_formulation = "power_based"`, see
[Unit Commitment §4.6](../../docs/unit-commitment.md#46-power-based-formulation-uc_formulation--power_based)).
The decision variables are the powers at the hour boundaries; each hour's
energy is the trapezoid of its two boundary powers, the ramp limits bound
the boundary powers, and the start-up / shut-down trajectories add their
energy to the hours around the event.  It is a mixed-integer problem.

## Source

Written for GTOpt (derived from [`uc_small`](../uc_small/README.md)).
The unit tests in
[`test/source/test_commitment_power_based.cpp`](../../test/source/test_commitment_power_based.cpp)
check the individual rows.

## System

| Unit | Range | Cost | Commitment |
|------|-------|------|------------|
| `g_base` | 30–100 MW | 20 $/MWh | no-load 30 $/h, start-up 200 $, ramp 25 MW/h, on at 50 MW at the start |
| `g_mid` | 30–80 MW | 45 $/MWh | no-load 40 $/h, start-up 400 $, ramp 30 MW/h, min. up/down 3 h, start-up trajectory 10, 20 MW, shut-down trajectory 20, 10 MW, off at the start |
| `g_peak` | 15–60 MW | 90 $/MWh | no-load 20 $/h, start-up 150 $, off at the start |

Demand: 50, 70, 110, 150, 160, 120, 70 and 50 MW.

`g_base` cannot follow the load ramp alone (25 MW/h on the boundary
powers), so `g_mid` starts with its two-hour trajectory and `g_peak`
covers the rest.  `Commitment/power_sol.csv` holds the boundary powers
(end of each hour); `Generator/generation_sol.csv` is their trapezoid
average.

## Running

```bash
cd cases/uc_power_based
gtopt uc_power_based.json --output-directory results --write-out all
```

`output/` holds the reference results of the end-to-end test.
