# `reserve_sscc_wear` — operation wear adders on a two-day SSCC case

`reserve_sscc_multiday` (three buses, two chronological days, relaxed
commitment of coal, CCGT and gas turbine, CPF / CSF / CTF and inertia)
with the `Commitment` operation wear adders switched on:

| Commitment | `startup_wear_cost` [$/start] | `wear_hour_cost` a [$/h] | `wear_load_cost` b [$/MWh] |
|------------|------------------------------|--------------------------|----------------------------|
| `uc_coal`  | 3000 | 60 | −0.2 (low-load penalty; a + b·(300 − 120) = 24 ≥ 0) |
| `uc_ccgt`  | 1500 | 20 | 0.05 |
| `uc_gt`    | 400  | —  | —    |

The golden `output/` pins the objective (`solution.csv`) and the two wear
cost streams (`Commitment/startup_wear_cost_sol`,
`Commitment/operation_wear_cost_sol`).  Registered as the
`e2e_reserve_sscc_wear_*` integration tests.
