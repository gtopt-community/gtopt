# `reserve_cpf_planes` — CEN CPF max-of-planes requirement (exogenous load term)

Three buses in a line (north - center - south), 24 hours.  A 700 MW
solar plant in the north produces more than the 285-500 MW center load
around noon, so solar is curtailed.  A 60 $/MWh CCGT and a 120 $/MWh
OCGT in the south cover the evening and offer CPF up reserve at 20 and
35 $/MW.

The `cpf10s` ReserveZone has a dynamic requirement
`R = max(140 - 0.0366 D, 120 - 0.02064 D)` [MW], the CEN 2026 CPF+@10s
slopes with the intercepts scaled to this small system
(formulation §5.9, "Dynamic Requirement: Max of Planes").

`load_term` is `exogenous` (the default): `D = 1.05 x load` (default
`demand_load_factor`) is data on the right-hand side.  At the curtailed
solar hours (10-16 h) the north price is 0 $/MWh, and the other prices
are the energy costs (60 / 75 $/MWh): the reserve price (20 $/MW) never
reaches an energy price.  `Bus/balance_dual.csv` omits zero rows.

Compare `Bus/balance_dual.csv` of `reserve_cpf_planes` and
`reserve_cpf_planes_endogenous`: same requirement, but only the
endogenous variant puts the reserve price into the energy prices.

## Run

```bash
gtopt cases/reserve_cpf_planes/reserve_cpf_planes.json
```

Registered as the `e2e_reserve_cpf_planes_*` integration tests (objective, bus
prices, requirement).
