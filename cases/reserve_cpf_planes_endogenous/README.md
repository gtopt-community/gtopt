# `reserve_cpf_planes_endogenous` — CEN CPF max-of-planes requirement (endogenous load term)

Three buses in a line (north - center - south), 24 hours.  A 700 MW
solar plant in the north produces more than the 285-500 MW center load
around noon, so solar is curtailed.  A 60 $/MWh CCGT and a 120 $/MWh
OCGT in the south cover the evening and offer CPF up reserve at 20 and
35 $/MW.

The `cpf10s` ReserveZone has a dynamic requirement
`R = max(140 - 0.0366 D, 120 - 0.02064 D)` [MW], the CEN 2026 CPF+@10s
slopes with the intercepts scaled to this small system
(formulation §5.9, "Dynamic Requirement: Max of Planes").

`load_term` is `endogenous_generation` (PLEXOS `Generation_Calculation`
compatibility): `D = sum of generation` is an LP column.  Each MWh
generated lowers the requirement by 0.0366 MW, worth 0.0366 x 20 =
0.732 $/MWh, so every price drops by that credit (60 -> 59.268) and at
the curtailed solar hours (10-16 h) the north price is -0.732 $/MWh —
the negative-price mechanism the exogenous default removes.

Compare `Bus/balance_dual.csv` of `reserve_cpf_planes` and
`reserve_cpf_planes_endogenous`: same requirement, but only the
endogenous variant puts the reserve price into the energy prices.
`ReserveZone/reserve_credit_sol.csv` writes that credit natively: −0.732
$/MWh while the first plane binds (−0.0366 × 20) and −1.281 at the
evening hours priced by the OCGT (−0.0366 × 35); `credit_transfer_sol`
is the credit × generated energy [$].  In `reserve_cpf_planes`
(exogenous load) the credit is 0.

## Run

```bash
gtopt cases/reserve_cpf_planes_endogenous/reserve_cpf_planes_endogenous.json
```

Registered as the `e2e_reserve_cpf_planes_endogenous_*` integration tests (objective, bus
prices, requirement, reserve credit).
