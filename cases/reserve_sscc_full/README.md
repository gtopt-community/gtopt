# `reserve_sscc_full` — CPF, CSF and CTF with shared headroom, cold CTF and a BESS

One bus, eight chronological hours (load 230-450 MW, 250 MW of solar).

| Zone | Requirement | Notes |
|------|-------------|-------|
| `cpf` | up 30, down 20 MW | |
| `csf` | up 60, down 40 MW | `requirement_sense: "eq"`, surplus 1 $/MW (PLEXOS `*MinProvision`) |
| `ctf` | up 80 MW | |

- **CCGT** (450 MW, 55 $/MWh) offers CPF, CSF and CTF with
  `shared_headroom`: one row `p + r_cpf + r_csf + r_ctf <= 450` (and
  `p - r_dn >= 0`) instead of one per service.
- **Peaker** (120 MW, 140 $/MWh, relaxed Commitment) offers **cold** CTF
  (`offline`): `r <= 120 (1 - u)`, available only while it is off.
- **BESS** (200 MWh, 50/50 MW, eta 0.95) offers CSF up and down with the
  native rows (`reserve_sustain_hours: 1`): headroom on both the
  discharge and the charge side, per-side caps (`urmax_response`,
  `urmax_charge_response`, `drmax_charge_response`, PLEXOS Max Response
  / Max Charge Response), and expected deployment energy 0.3 MWh/MW
  charged to its own state of charge (`reserve_energy_usage_up/down`,
  replacing the CEN AUX battery; no bus injection).

Look at `ReserveProvision/` (incl. `reserve_*_sol`) and `ReserveZone/`
for the BESS split.  The peaker commitment is relaxed (LP), so its
status is fractional; `Commitment/status_sol.csv` rounds it up for
display, while the LP value obeys `r_cold <= 120 (1 - u)`.

## Run

```bash
gtopt cases/reserve_sscc_full/reserve_sscc_full.json
```

Registered as the `e2e_reserve_sscc_full_*` integration tests.
