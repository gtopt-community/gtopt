# `uc_power_based_philipsen2019` — power-based schedule from the literature

Scenario 2 of the day-ahead market example in Philipsen, Morales-España,
de Weerdt and De Vries [1], solved with the power-based unit-commitment
formulation (`model_options.uc_formulation = "power_based"`, see
[Unit Commitment §4.6](../../docs/unit-commitment.md#46-power-based-formulation-uc_formulation--power_based)).
The published optimum is the reference: power-based day-ahead cost
**12 570 €** ([1], Table 9), with the schedule of [1], Table 8.  It is
a linear problem (both units are must-run with a zero minimum output).

## Source

All data are rebuilt from the tables of [1] (open access, CC BY 4.0):

| Unit | Range | Ramp | Offer price | [1] |
|------|-------|------|-------------|-----|
| `G1` | 0–200 MW | 65 MW/h | 25 €/MWh | Table 3 |
| `G2` | 0–100 MW | 120 MW/h | 37 €/MWh | Table 3 |

Demand, energy per hour 1–7 ([1], Table 4, "Energy SC2"):
0, 60, 120, 135, 90, 30 and 15 MWh.  Both units start the day at 0 MW
(the power at hour 0 in [1], Table 8).

The fast-start unit `G3` of [1] only provides real-time reserve and is
dispatched at zero in the day-ahead schedule ([1], Table 8), so it is
left out; adding it as a 60 MW, 180 MW/h, 90 €/MWh unit does not change
the optimum.

[1] clears the market on a power balance at the hour boundaries; gtopt
keeps an energy balance per block.  For this demand the two give the
same optimum and schedule (checked with an independent LP), so the case
tests the power-based unit rows, not the balance.

## Reference values

| Quantity | [1] | gtopt |
|----------|-----|-------|
| Power-based day-ahead cost (Table 9) | 12 570 € | 12 570 |
| `G1` boundary powers, end of hours 1–7 (Table 8) | 0, 65, 120, 95, 30, 30, 0 MW | same |
| `G2` boundary powers, end of hours 1–7 (Table 8) | 0, 55, 0, 55, 0, 0, 0 MW | same |
| Energy-based day-ahead cost (Table 9) | 11 250 € | 11 250 with `uc_formulation` removed |

The energy-based schedule is cheaper because it lets `G1` deliver
60 MWh in hour 2 from 0 MW at the start of the hour, which its 65 MW/h
ramp cannot do ([1], Sect. 4.3.2).

## Running

```bash
cd cases/uc_power_based_philipsen2019
gtopt uc_power_based_philipsen2019.json --output-directory results
```

`output/` holds the reference results of the end-to-end test
(`solution.csv`, the trapezoid energies `Generator/generation_sol.csv`
and the boundary powers `Commitment/power_sol.csv`).

## References

[1] R. Philipsen, G. Morales-España, M. de Weerdt and L. De Vries,
"Trading power instead of energy in day-ahead electricity markets,"
*Applied Energy*, vol. 233–234, pp. 802–815, 2019,
doi: [10.1016/j.apenergy.2018.09.205](https://doi.org/10.1016/j.apenergy.2018.09.205).
