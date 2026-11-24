# `dv_nonanticipative` — an LNG cargo decided before the hydrology

A `scope: "global"` DecisionVariable with `"nonanticipative": true`:
one value shared by two hydrology **scenes**, which gtopt solves as one
extensive-form LP (see docs/user-constraints.md, *Nonanticipative across
scenes*).

## Source

Written for gtopt. All data are synthetic.

## System

| Element | Data |
|---------|------|
| Time | 3 blocks of 1 h, one stage, one phase |
| Scenes | `wet` (scenario 1) and `dry` (scenario 2), probability 0.5 each |
| Demand | `d1`: 100 MW in every block |
| Hydro | reservoir `rsv` (0–200, starts at 50, `flow_conversion_rate: 3.6` pinned — the unset default is 0.0036 hm³/(m³/s·h), which would make the stored water unlimited) feeds `hydro` (0 $/MWh, 100 MW); inflow 60 per block (wet) or 20 (dry) |
| LNG | `lng`: 10 $/MWh, generation capped in every block by `cargo` |
| Peaker | `peaker`: 100 $/MWh |
| Decision | `cargo` ∈ [0, 100], 150 $/unit, `cost_type: energy`, `scope: global`, `nonanticipative: true` |

Each scenario keeps its own reservoir trajectory. The stored 50 units
give 50 / 3.6 = 13.9 MWh, so hydro covers 180 + 13.9 = 193.9 MWh (wet)
or 60 + 13.9 = 73.9 MWh (dry) of the 300 MWh load: a residual of 106.1
or 226.1 MWh, i.e. 35.37 or 75.37 MW per block.

## Expected result

One unit of `cargo` up to 35.37 MW saves 90 $/MWh × 3 h in both
scenarios (270 $ > 150 $). Above that it only helps the dry scenario:
0.5 × 90 × 3 = 135 $ < 150 $. So `cargo` = 35.37 MW and

    150 · 35.37 + 0.5 · (10 · 106.1) + 0.5 · (10 · 106.1 + 100 · 120)
      = 12366.67

Without the flag each scene picks its own value (35.37 wet, 75.37 dry;
3183.33 + 6783.33 = 9966.67). The output stays split per original
scene: the `_s1_p0` / `_s2_p0` shards hold the wet / dry rows (the dry
scene buys 120 MWh from the peaker, so its bus price is 100 $/MWh), and
`cargo` (one column) is reported once, under scene 1. `solution.csv`
has one row for the merged LP (scene uid 1).

## Running

```bash
cd cases/dv_nonanticipative
gtopt dv_nonanticipative.json --output-directory results --write-out all
```
