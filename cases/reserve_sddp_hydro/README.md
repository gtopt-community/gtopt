# `reserve_sddp_hydro` — the extended reserve model under SDDP

One bus, 3 phases × 1 stage × 4 one-hour blocks.  The same system as the
doctest fixture `test/source/xres_sddp_case.hpp`:

- reservoir `rsv` (eini 200, inflow 15/h) feeding a free 120 MW hydro unit,
  two thermals with a relaxed `Commitment` (pmin 20; per-block
  formulation, the stages are not chronological) and a peaker;
- BESS (40/40 MW, 100 MWh) offering capped two-sided reserve (split
  columns) with 0.1 MWh/MW deployment energy;
- InertiaZone `sen` (6 000 MWs, 2 000 MWs credit with both thermals on);
- ReserveZone `cpf`: R ≥ max(40 + 0.1·D, 80 + 0.05·D − 0.002·I, C ≥ p_thb);
  `cpf_dn`: 20 MW down.

The reservoir volume and the BESS state of charge are the SDDP states.
`golden.json` pins the converged objective (equal to the one-phase LP
optimum, see `test_sddp_reserve_extended.cpp`) and the reserve prices.
Registered as `e2e_reserve_sddp_hydro_{off,compress}_sddp_*`.
