# caso2emb — two reservoirs, two buses, 3 × 100 h (PLP reference)

## Origin

The Chebyshev scheduling research test databases (Matus et al.), `caso2emb_ver19/`:
PLP input files (`plpcnfce.dat`, `plpcnfli.dat`, `plpdem.dat`,
`plpaflce.dat`, `plpeta.dat`, `plpbar.dat`) and the PLP results
(`plpcen.csv`, `plpemb.csv`, `plpbar.csv`, `plpcostop.csv`).
There is no system SQLite DB for this case; the system is transcribed in
`scripts/tcheby2gtopt/library.py::caso2emb_system` (the matching
simulation DB `simu1_caso2emb_ver19.db` gives the same 3 stages and
inflows).

## System

- Buses Antuco220 (1), Valdivia220 (2); line 600 MW both ways,
  X = 40 Ω at 220 kV.  PLP models no losses (`Modela Perdidas f`), so no
  resistance is written.
- Reservoir plants CH and CH2 at Antuco220: 450 MW, Rendi 1 MW/(m³/s),
  `Vini` 3.6 / 1.8 hm³, `Vmax` 10 hm³, `Vmin` 0; each turbines into the
  sea.
- Thermal CT at Valdivia220: 60 MW, 10 $/MWh.  Failure unit CF at
  Antuco220: 100 MW, 100 $/MWh (a generator, as in PLP).
- Demand at Antuco220: 100, 50, 80 MW in stages 1–3 (100 h each).
- Inflows: CH 30 / 10 / 15 m³/s, CH2 15 / 5 / 5 m³/s.

Dropped: PLP spill limits (`VertMax 10`, gtopt `spillway_capacity 0`;
not binding), the discount factor (`annual_discount_rate 0`), PLP's
small LP penalty terms.

## Time representations

- `caso2emb_block.json`: 300 blocks of 1 h, 3 stages.
- `caso2emb_point.json`: 3 point stages, N = 24 (25 Lobatto nodes per
  100 h stage, CC weights).  All data are constant per stage, so the node
  values equal the stage values.

## Expected objective: 135000 (both)

All water is free and must be used.  Hydro energy (Rendi 1):
`(3.6 + 1.8) hm³ / 0.0036 + (30+10+15 + 15+5+5) m³/s · 100 h = 1500 +
8000 = 9500 MWh`.  Demand: `(100 + 50 + 80) · 100 = 23000 MWh`.  CT
supplies the rest, 13500 MWh, without reaching 60 MW on average in any
stage and without losses: `10 · 13500 = 135000`.

PLP reference: `plpcen.csv` gives CT = 60, 42.78, 32.22 MW in stages
1–3 (13500 MWh, CF = 0); the PLP log shows the three stage LPs of an
earlier run with objectives 40001.44, 35001.26 and 60002.16, i.e.
CT = 40, 35, 60 MW: the same 135000 plus 4.86 of PLP's small
volume/angle penalty terms, which gtopt does not have.  The stage split
differs (degenerate: any dispatch that uses all the water is optimal);
the total matches.  `plpcostop.csv` (216000 / 154000 / 116000) reports
PLP's per-stage cost-to-go, not this total.

Duals (gtopt, block and point): bus prices 10 $/MWh everywhere (PLP
`CMgBar` 10); water value 2777.78 $/hm³ = 10 / 0.0036.

Tolerance: 1e-6 relative.
