# mini/battery_ramp — 1 bus, battery, linear load ramp

1 bus, 24 h.  Load `d(t) = 10 + t` MW (10 → 34, mean 22, 528 MWh).
g1: 10 $/MWh, ≤ 22 MW; g2: 100 $/MWh.  Lossless battery: `emax 50 MWh`,
`eini = efin = 0`, `pmax_charge = pmax_discharge = 100`,
`use_state_variable: true` (needed so `eini` holds across blocks /
stages), `daily_cycle: false`.

Block (24 × 1 h, load = hourly cell mean `10.5 + h`), by hand:
in hours 0–11 g1 has `Σ (22 − 10.5 − h) = 72 MWh` spare, in hours
12–23 the load exceeds 22 MW by 72 MWh.  The battery can shift only
`emax = 50 MWh`, so g2 serves 22 MWh:
`10 · (528 − 22) + 100 · 22 = 7260`.
Duals: bus 10 $/MWh in hours 0–11 (g1 below its limit), 100 $/MWh in
hours 12–23 (g2 marginal); battery balance dual likewise 10 / 100; the
level peaks at `emax = 50` at the end of hour 11 (the `emax` bound
carries the 90 $/MWh spread).

Point (N = 24, node loads `10 + t_k`, CC energy exact: 528 MWh):
**7241.46825689373** (regression, HiGHS).  The level is integrated
between the nodes (`trapezoid`) and bounded only at the nodes, so the
effective shift differs slightly from 50 MWh.  Bus duals: 10 $/MWh at
nodes t < 12, 100 $/MWh at t > 12, 55 at the node t = 12 (load = 22,
degenerate).  FLAG: binding `emax` on a point stage — the newer storage
envelope rows may change this value; re-baseline with a current build.

Tolerance: 1e-6 relative.
