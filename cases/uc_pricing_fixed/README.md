# `uc_pricing_fixed` — UC pricing toy, `pricing_mode = "fixed"`

Two buses, two hours, two committed units.  The same system is solved in
three cases that differ only in `model_options.pricing_mode`:
`uc_pricing_fixed` (this one), [`uc_pricing_relaxed`](../uc_pricing_relaxed/)
and [`uc_pricing_fast_start`](../uc_pricing_fast_start/).  The dispatch
is identical in all three; only the nodal prices change.  It is a
mixed-integer problem.

## Source

Written for GTOpt.  The unit test
[`test/source/test_pricing_mode.cpp`](../../test/source/test_pricing_mode.cpp)
builds the same system; the uplift tool test
`scripts/gtopt_pricing_uplift/tests/test_integration.py` reads these
goldens.

## System

| Element | Data |
|---|---|
| `b1` – `b2` | line `l12`, reactance 0.1, 300 MW (never binding) |
| `g_slow` @ `b1` | 10 $/MWh, 0–120 MW; commitment: pmin 50 MW, no-load 300 $/h, min up/down 4 h, on for 10 h |
| `g_fast` @ `b2` | 50 $/MWh, 0–100 MW; commitment: pmin 40 MW, start-up 1000 $, min up/down 1 h, off |
| demand @ `b2` | 140 MW, 80 MW (1-hour blocks) |

`g_fast` is fast-start by the auto rule (min up/down 1 h ≤
`fast_start_max_hours` = 1); `g_slow` is not.

## Results (all modes)

MIP dispatch: `g_slow` 100 / 80 MW, `g_fast` 40 / 0 MW (on in hour 1 only,
`g_slow` is capped at 120 MW and `g_fast` cannot run below 40 MW).
Objective 5400 $.

| Mode | λ hour 1 | λ hour 2 | Make-whole | Lost opportunity | Total uplift |
|---|---|---|---|---|---|
| `fixed` | 10 | 10 | 3200 (`g_slow` 600, `g_fast` 2600) | 0 | 3200 |
| `fast_start` | 60 | 10 | 600 (`g_fast`) | 1300 (`g_slow`) | 1900 |
| `relaxed` | 60 | 12.5 | 600 (`g_fast`) | 1100 (`g_slow`) | 1700 |

Hand check: under `fixed` the marginal unit is `g_slow` (10 $/MWh) in
both hours, so `g_fast` loses (50 − 10)·40 + 1000 = 2600 and `g_slow`
loses its no-load 2·300 = 600.  Under `fast_start` the relaxed `u` of
`g_fast` prices hour 1 at its average cost 50 + 1000/100 = 60
(the start-up cost spread over 100 MW of relaxed capacity); `g_fast`
then loses 1000 − (60 − 50)·40 = 600, while `g_slow` earns 4400 but could
earn 5700 at 120 MW in hour 1 and off in hour 2 (LOC 1300).  Under
`relaxed` the `g_slow` no-load also enters hour 2 (λ = 10 + 300/120 =
12.5), lowering its LOC to 1100.

Each output directory also has `pricing.csv` (applied mode, relaxed /
pinned integer columns, fallback flag).

## Run

```bash
cd cases/uc_pricing_fixed
gtopt uc_pricing_fixed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_fixed.json
```
