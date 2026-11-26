# system2_week — 9-bus Chilean system, one week of hourly data

## Origin

The Chebyshev scheduling research test databases (Matus et al.), `system2.db`: a
reduced SING + SIC system (9 buses, 8 lines, 262 units, 9 demands) with
hourly demand schedules (9 × 168) and hourly `pmax` schedules for 67
variable units (67 × 168), 2029-01-01 00:00 to 2029-01-07 23:00.  The
window is that week (168 h).  Converted with `scripts/tcheby2gtopt`.

## Mapping

- Generators: capacity, `pmax` (hourly schedule where present), `cvar`
  -> `gcost`.  The `pmin` of 161 thermal units is **dropped**
  (`ConvertOptions(pmin_mode="drop")`): as a hard floor without
  commitment it would force every unit on for the whole week.  The case
  is an economic dispatch.
- Lines: radial network, `tmin`/`tmax` (±capacity), X 0.1 Ω at 220 kV
  (no resistance: losses off).
- Demand `lmax` = hourly schedule (the `load` column, 10000 MW, is only
  the default before the first key).  `fail_cost` 500 $/MWh.

## Time representations

- `system2_week_block.json`: 168 hourly blocks.
- `system2_week_point.json`: one point stage, N = 48 (49 nodes over
  168 h).  Node values: cell means over the quadrature cells
  (`ChebyshevSampler(values="wcell")`): energy of every series is exact
  (demand 2448939.1 MWh in both), node values within the hourly range.

## Expected objective (regression, HiGHS)

- Block: **48407753.8832999**.
- Point (N = 48): **46675189.8127262** (−3.58 %).

The demand energy is the same; the gap comes from the cell means
flattening the daily peaks (cells up to ≈ 5.5 h wide near mid-week), so
fewer expensive peakers run.  It closes as N grows (same converter,
`--nodes`): N = 96 → 47819035.53 (−1.22 %), N = 168 → 48167982.40
(−0.50 %).  `--point-values interp` at N = 48 gives 47762211.93
(−1.33 %) but does not conserve energy.

Tolerance: 1e-6 relative.
