# `matpower_case30_reserves` — MATPOWER fixed zonal reserves (DC)

Benchmark from MATPOWER (3-clause BSD; Copyright (c) 1996-2024, Power
Systems Engineering Research Center (PSERC) and individual contributors):

- case: <https://github.com/MATPOWER/matpower/blob/master/lib/t/t_case30_userfcns.m>
- reference: <https://github.com/MATPOWER/matpower/blob/master/lib/t/t_opf_userfcns.m>,
  section "reserves + interface flow lims (DC)"
- reserve model: `lib/toggle_reserves.m`

30 buses, 41 branches (x in p.u., `RATE_A` → `tmax`), 6 generators, one
period.  Two overlapping up-reserve zones (zone 1 = all units, 60 MW;
zone 2 = units 5 and 6, 20 MW), offers `[1.9 2 3 4 5 5.5]` $/MW, at most
25 MW per unit, `Pg + R ≤ Pmax`, and two interface-flow limits (area-1
imports in [-15, 25] MW, area-2 imports in [-10, 20] MW) written as
UserConstraints on signed branch flows.

The quadratic generator cost `c2·P² + c1·P` is a convex piecewise-linear
cost with 0.1 MW secant segments (exact at every breakpoint), through a
unit-price `Fuel` and `heat_rate_segments`.  Each unit has one
`ReserveProvision` listing every zone it belongs to.

| Quantity | MATPOWER | gtopt |
|----------|----------|-------|
| R (MW) | 25 15 0 0 16.9 3.1 | identical (error < 1e-14) |
| zone prices ($/MW) | 2, 3.5 (unit prices 2 / 5.5) | 2, 3.5 |
| reserve cost ($) | 179.05 | 179.05 |
| interface flows (MW) | -15, 20 | -15, 20 |

Regenerated from the tables in `test/source/test_reserve_bench_matpower_case30.cpp`
(the doctest builds the same case in-process).  Registered as the
`e2e_matpower_case30_reserves_*` integration tests.
