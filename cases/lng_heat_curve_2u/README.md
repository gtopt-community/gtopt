# lng_heat_curve_2u — reserve on a heat-rate curve

Two gas units burn from one LNG tank along their own fuel curves
(`LngTerminal.use_generator_fuel_curve`, link heat rate 1 = curves in
m³/MWh); a committed unit also burns its `Commitment.noload_fuel`:

| unit | no-load [m³/h] | curve [m³/MWh] |
|------|---------------:|----------------|
| `efficient` | 40 | 1.0 flat (`heat_rate`) |
| `inefficient` | 10 | 1.5 up to 60 MW, then 1.8 (`pmax_segments` / `heat_rate_segments`) |

Demand 150 MW and a 50-MW up-reserve requirement in one 1-h block: the
200 MW of the two units hold exactly 200 − 150 = 50 MW of headroom, so
both run and the question is who is part-loaded.  The tank gas is worth
10 \$/m³ (`efin = eini`, `efin_cost` 10).

* efficient at 100, inefficient at 50 (holds the reserve):
  140 + 10 + 75 = **225 m³**;
* efficient at 50 (holds the reserve), inefficient at 100:
  90 + 10 + 90 + 72 = 262 m³.

The plan part-loads the less efficient unit: objective
225 × 10 = **2,250 \$**.  With the scalar link heat rate only
(`use_generator_fuel_curve` unset) the burn is 150 m³ for every split
and the no-load fuel is not drawn from the tank.  The goldens
(`output/`) compare the dispatch and the objective.  A MIP (commitment):
HiGHS / CPLEX, never CLP.
