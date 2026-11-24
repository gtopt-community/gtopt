# generation_prev_initial

`Generator.initial_generation` (MW at `t = 0⁻`, PLEXOS *Initial Generation*)
is the value of `generation_prev` at the first block of the horizon.

This case reduces the CEN PCP `PANGUEramp` infeasibility to one bus and four
chronological hourly blocks:

- `PANGUE_U1`: pmin 80, pmax 200, 5 $/MWh, `initial_generation: 80`.
  Ramp user constraints `gen − gen_prev ≤ 60` and `gen_prev − gen ≤ 60`.
- `THERMAL`: 300 MW at 50 $/MWh.
- Demand: 150 / 200 / 250 / 150 MW.

| Hour | Ramp window for PANGUE_U1 | PANGUE_U1 | THERMAL |
|------|---------------------------|-----------|---------|
| 1    | [80, 140]  (prev = 80)    | 140       | 10      |
| 2    | [80, 200]                 | 200       | 0       |
| 3    | [140, 200] (pmax)         | 200       | 50      |
| 4    | [140, 200]                | 150       | 0       |

Objective: `5 · 690 + 50 · 60 = 6450`.

If `initial_generation` is removed, `generation_prev` at hour 1 is 0, so
the hour-1 ramp row allows at most 60 MW against pmin 80, and the LP is
infeasible. This is the legacy behaviour that broke the PLEXOS-converted
PCP cases.
