# periodic_typical_days

Two typical days (one per stage) of four 6-hour blocks, two buses and one
switchable line. It exercises every coupling that a periodic stage
(`Stage.period_hours = 24`) can wrap once the matching option is set:

| Element | Option | Behaviour on the periodic stage |
|---------|--------|---------------------------------|
| `bess_2h` battery (cyclic, `daily_cycle` default) | `model_options.periodic_storage = true` | free start, ends where it started, every day |
| `phes_long` battery (`daily_cycle: false`, `use_state_variable: true`) | `model_options.periodic_storage = true` | one-day balance scaled to the stage: `efin = s + n (x_last - s)` |
| `lake` reservoir (state) | `model_options.periodic_storage = true` | same state scaling as `phes_long` |
| `l1_2_ots` line commitment | `cycle: "stage"` | the last block's status precedes the first block |
| `coal_ramp_up` / `coal_ramp_dn` user constraints | `model_options.prev_stage_link = "period"` | `generation_prev` of block 1 is the stage's own last block |

Stage 1 is 30 days (720 h, `n = 30`), stage 2 is 31 days (744 h, `n = 31`).
Each block weighs `duration` hours in the cost and `duration / n` real
hours in the storage balance.

Reading the solution:

- `lake`: stage 1 starts at 10000, ends the typical day at 10224.667, so
  `efin = 10000 + 30 * 224.667 = 16740`. Stage 2 starts at 16740 and with
  the dry inflow drains to `16740 + 31 * (16200 - 16740) = 0`.
- `l1_2_ots` stays on in every block; no startup is paid at the day wrap.
- Objective: 7113701.156 (HiGHS 1.16).

Remove the two options and the `cycle` field to get the legacy chained
model (one 30-day block per row, no wrap); the tier-3 unit test
`periodic_typical_days - options off restore the legacy LP` checks that
both LPs differ and both solve.

Run:

```bash
gtopt periodic_typical_days.json --output-directory out
```
