# `reserve_sscc_multiday` — CPF / CSF / CTF, inertia and BESS over two days

Three buses (north — center — south, 300 / 400 MW lines, DC Kirchhoff,
lossless), two chronological days of 24 one-hour blocks (one stage per
day), solar at the north bus, wind at the south bus and three committed
thermal units (relaxed `Commitment`: pmin, min up/down, startup cost).

| Zone | Requirement | Notes |
|------|-------------|-------|
| `cpf` | up: max(30 + 0.02·D − 0.002·I, 0.3·C); down 20 MW | exogenous D (factor 1), I = `sen` inertia, C ≥ coal / CCGT output |
| `csf` | up 40, down 30 MW | `requirement_sense: "eq"`, surplus 1 $/MW |
| `ctf` | up 60 MW | the gas turbine offers **cold** reserve (`offline`) |
| `sen` (inertia) | 4 000 MWs | 1 000 MWs credit once coal and CCGT are both committed |

- **coal_c** offers CPF + CSF with `shared_headroom`; **ccgt_c** offers
  CPF, CSF and CTF; **gt_s** offers cold CTF only.
- **bess** (north, 100 MW / 400 MWh, η 0.95, sustain 0.25 h) offers CPF
  with per-side caps (`urmax_response` 40, `urmax_charge_response` 30 →
  split columns) and CSF with deployment energy 0.3 MWh/MW
  (`ur/dr_energy_usage`).

Registered as the `e2e_reserve_sscc_multiday_*` integration tests (the
golden `output/` pins only solver-invariant files).
