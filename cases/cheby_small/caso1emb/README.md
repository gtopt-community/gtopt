# caso1emb — one reservoir, two buses, 3 × 100 h

## Origin

The Chebyshev scheduling research test databases (Matus et al.):
`system1_caso1emb_fact.db` (system) and `simu1_caso1emb_fact.db`
(simulation: 3 stages of 100 h from 2016-01-01, scenario `s1` maps
stage k to scene k).  Converted with `scripts/tcheby2gtopt`
(`conversion_notes.txt`: nothing dropped).

## System

- Buses Antuco220 (1), Valdivia220 (2); line 600 MW both ways,
  X = 40 Ω at 220 kV (`r = 10 Ω` not written: losses off).
- CT 60 MW, 10 $/MWh at bus 1; CF 100 MW, 100 $/MWh at bus 2; hydro CH
  450 MW with `pmin 20 MW` at bus 1, gcost 0.
- Hydro network: inflow `CH_Inf` into J1 (reservoir `CH_Emb`: vini
  3.6 hm³, vmax 100 hm³), waterway `CH_WW` J1→J2 with the turbine
  (1 MW per m³/s), waterway `OCEAN` J2→SEA (SEA → `drain: true`).
  `spillway_capacity 0` (tcheby has no free spill).
- Inflow per stage (scenes s1, s2, s3): 30, 10, 15 m³/s.
- Demand at bus 1: zero-order-hold schedule 100 MW from 0 h, 50 MW from
  2016-01-04 04:00 (76 h), 80 MW from 2016-01-08 08:00 (176 h).
  `fail_cost` 500 $/MWh.

## Time representations

- `caso1emb_block.json`: 300 hourly blocks, 3 stages.
- `caso1emb_point.json`: 3 point stages, N = 24.  The demand node values
  are the means of the hourly step series over the node's quadrature
  cell (`ChebyshevSampler(values="wcell")`): Σ w·v = hourly energy of
  each stage, values within [50, 100]; the 76 h step gives one node
  with 84.13 MW.

## Expected objective

Block: **252000**, by hand.  Water: `3.6 / 0.0036 + (30+10+15)·100 =
6500 MWh`.  The hydro floor of 20 MW takes `20 · 300 = 6000 MWh`, and
since generation may not exceed the load, CT runs at
`min(60, load − 20)`: 60·76 + 30·100 + 60·124 = 15000 MWh (150000 $).
The 100 MW hours still need 20 MW more: 76·20 = 1520 MWh, of which the
spare 500 MWh of water cover 500 and CF the other 1020 MWh (102000 $).
Total 252000.  The water value is that of CF: 100 / 0.0036 = 27777.8
$/hm³ (gtopt dual, all stages); bus prices are 100 $/MWh in the
100 MW hours of stage 1 and 10 $/MWh elsewhere.

Point: **247143.292205529** (regression, HiGHS).  The cell-mean demand
smooths the 76 h step (node 84.13 MW), which changes how much of the
100 MW load CF must serve; no closed form.  Storage levels do not bind
(vmax 100 hm³), so the envelope rows should not move it.

Tolerance: 1e-6 relative.
