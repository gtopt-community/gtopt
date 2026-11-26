# losses — three buses, three lines, one commitment, 24 h

## Origin

The Chebyshev scheduling research test databases (Matus et al.), `system1_losses.db`
(no simulation DB: a 24 h window from 2016-01-01 is used).  Converted with
`scripts/tcheby2gtopt` (`ConvertOptions(line_losses=True)`).

## System

- Buses bar1, bar2, bar3; demands 200, 100, 100 MW (constant).
- Gen1 300 MW, 60 $/MWh (bar1, with commitment `c1`); Gen2 10–100 MW,
  80 $/MWh (bar2); Gen3 5–50 MW (capacity 250), 100 $/MWh (bar3).
- Lines (220 kV): Line12 500 MW, X 0.1 Ω, R 0.03 Ω; Line23 300 MW,
  X 0.3, R 0.02; Line31 500 MW, X 0.2, R 0.02.  `tmin`/`tmax` are empty
  in the DB, so the capacity is used both ways.  The resistances are
  written, so gtopt models losses (default `line_losses_mode`,
  `adaptive`).
- Commitment `c1` on Gen1: `min_up_time 4.5`, `min_down_time 4`.
- `fail_cost` 500 $/MWh.

Dropped (`conversion_notes.txt`): `Commitment.time_resolution` (2.5 h),
`Bus.active_sched` / `Generator.active_sched` (2015–2020 activation
dates that do not match the window).

## Time representations

`losses_block.json` (24 × 1 h) and `losses_point.json` (one point stage,
N = 24).  All data are constant, so both give the same LP values.

## Expected objective: 626454.116041310 (both)

Lossless, by hand: load 400 MW; Gen1 at 300 MW (cheapest), Gen3 at its
5 MW floor, Gen2 the rest (95 MW): `(300·60 + 95·80 + 5·100) · 24 =
626400`.  Gen2 is marginal (bus prices ≈ 80 $/MWh).  The losses add
about 0.028 MW at about 80 $/MWh over 24 h: +54.116, i.e. 626454.116.
The loss increment depends on gtopt's loss model, so use a tolerance of
1e-6 relative only with the same `line_losses_mode` default; with
`--set model_options.line_losses_mode=none` the objective is 626400.
