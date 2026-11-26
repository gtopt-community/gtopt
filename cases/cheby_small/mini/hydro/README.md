# mini/hydro — 1 reservoir, 1 turbine

1 bus, 24 h.  Load `d(t) = 20 + t` MW (768 MWh).  Reservoir `rsv` at
junction `j_up`: `eini 0.36 hm³` (= 100 (m³/s)·h at 0.0036 hm³ per
(m³/s)·h), `emax 10`, no spill; inflow 5 m³/s; waterway `j_up → j_down`
(`j_down` drains) ≤ 100 m³/s with a turbine of 1 MW per m³/s feeding
`hydro` (0 $/MWh, 100 MW).  Thermal: 50 $/MWh.

By hand (block and point): the water is free and worth 50 $/MWh, so all
of it is used: `100 + 5 · 24 = 220 MWh`; the turbine never needs more
than 100 m³/s.  Thermal: `768 − 220 = 548 MWh` → **27400**.  The point
grid integrates the linear load exactly (CC), so the point objective is
also 27400.
Duals: bus 50 $/MWh at every block / node; reservoir water value
`50 / 0.0036 = 13888.9 $/hm³` (gtopt `water_value_dual`).

Tolerance: 1e-6 relative.
