# mini/periodic_week — periodic week on N nodes

1 bus, 168 h.  Load `d(t) = 100 + 50 sin(2πt/24)` (periodic:
`d(0) = d(168)`).  g1: 10 $/MWh, ≤ 110 MW; g2: 100 $/MWh.  Lossless
battery closing the cycle: `eini = efin = 500 MWh`, `emax 1000`,
`pmax 100`, `use_state_variable: true`.

Block (168 × 1 h): the hourly cell means of the sine sum to 0 over whole
days, so the energy is 16800 MWh.  g1 can cover it (110 MW > mean
100 MW) with the battery shifting the daily peak above 110 MW (≈ 382 MWh
swing at most, within [0, 1000] from 500), so g2 never runs and g1 is
marginal: **168000**; bus duals 10 $/MWh everywhere.

Point (N = 48, one periodic stage): node N would be the same instant as
node 0 of the next cycle, so it is dropped and node 0 carries
`w_0 + w_N` — 48 blocks, no duplicated end instant.  The optimum is
`10 · Σ w_k d(t_k)` (the CC weights of the 49-node set with the two end
weights merged; `d(0) = d(168)`) = **168000** to 1e-12.  Bus duals 10
$/MWh at every node; the node-0 price `λ_0 / (w_0 + w_N)` is a regular
node price (no end merge).  The levels do not bind, so the storage
envelope rows should not move these values.

Tolerance: 1e-6 relative.
