# `reserve_cpf_contingency_inertia` — CPF with contingency and inertia

Two buses, six chronological hours (load 300-520 MW at `b2`).  A 300 MW
coal unit (30 $/MWh) and 150 MW of solar sit at `b1`; two 200 MW gas
units (70 and 75 $/MWh) at `b2`.  All thermal units have a relaxed
Commitment (LP), so the case is solver-independent.

- **InertiaZone `sen`**: 3 000 MWs, met by InertiaProvisions on the
  thermal units (10 / 6 / 6 MWs per MW).  A `requirement_credits` entry
  lowers it by 1 500 MWs when both gas units are committed (continuous
  selector, the PLEXOS `InertiaGuacolda` form, formulation §5.17).
- **ReserveZone `cpf`**: `R = max(40 + 0.5 C - 0.005 I, 30 + 0.3 C)`
  [MW], with `C` the largest dispatched unit among `coal_big`, `gas_a`,
  `gas_b` (contingency candidates) and `I` the zone inertia.  Up reserve
  comes from the three thermal units (8-12 $/MW).  The zone pins
  `inertia_term: "endogenous"` (I = Σ pf·r, the PLEXOS dispatch-based
  form) so its outputs stay the PLEXOS-compatible reference; the default
  `"commitment"` would count I = Σ I_c·u_c from the relaxed Commitments
  (and `validate_planning` warns about the relaxed-UC coupling), see
  formulation §5.9.

Look at `ReserveZone/urequirement_sol.csv` (R),
`ReserveZone/urcontingency_sol.csv` (C), `InertiaZone/` and
`Commitment/status_sol.csv`.

## Run

```bash
gtopt cases/reserve_cpf_contingency_inertia/reserve_cpf_contingency_inertia.json
```

Registered as the `e2e_reserve_cpf_contingency_inertia_*` integration
tests.
