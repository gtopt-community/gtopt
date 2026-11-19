# LP Metadata Sidecar

When gtopt writes an LP file (`--lp-file`), it can also write a
machine-readable JSON **sidecar** describing every column and row of that LP.
External solvers, auditors and post-processing tools (for example a solver
that reads the exported model as MPS) then do not have to reverse-engineer
the `<class>_<variable>_<uid>_<context...>` names or guess the scaling.

The feature is **opt-in** and **zero cost when off**: nothing is generated
and the `.lp` file itself is unchanged.

## Enabling

```bash
gtopt system.json --lp-file model --lp-only --lp-metadata
# or, equivalently
gtopt system.json --lp-file model --lp-only --set lp_metadata=true
```

or in the planning JSON `options`: `"lp_metadata": true`.

Output file naming follows the LP file:

| LP file                              | Sidecar                                  |
|--------------------------------------|------------------------------------------|
| `model.lp` (single scene/phase)      | `model.meta.json`                        |
| `model_scene_S_phase_P.lp` (several) | `model_scene_S_phase_P.meta.json`        |

The sidecar is only written for LP files produced by `--lp-file`
(`PlanningLP::write_lp`); `--lp-debug` / error-LP dumps are not affected.

## Schema `gtopt-lp-metadata/1`

```json
{
"schema":"gtopt-lp-metadata/1",
"lp_file":"model.lp",
"scene_uid":1,
"phase_uid":1,
"objective":{"scale_objective":1000,"constant":0,"sense":"min"},
"blocks":[
{"scenario":1,"stage":1,"block":1,"duration":1,"probability":1,"discount":1,"cost_factor":1}
],
"columns":[
{"name":"generator_generation_1_1_1_1","class":"Generator","variable":"generation","uid":1,"context":{"type":"block","scenario":1,"stage":1,"block":1},"integer":false,"scale":1}
],
"rows":[
{"name":"bus_balance_1_1_1_1","class":"Bus","constraint":"balance","uid":1,"context":{"type":"block","scenario":1,"stage":1,"block":1},"scale":1}
]
}
```

`columns` and `rows` are in **LP order** (index *i* in the array is column /
row *i* of the LP).  `name` is exactly the name written in the `.lp` file.

| Field | Meaning |
|-------|---------|
| `objective.scale_objective` | Global objective divisor. Every objective coefficient written in the LP is the physical cost divided by this value: `obj_physical = obj_LP * scale_objective`. |
| `objective.constant` | Objective constant, in **physical** units. |
| `columns[].scale` | Column scale: `x_physical = scale * x_LP` (bounds and costs in the LP are divided accordingly). `1` means unscaled. |
| `columns[].integer` | `true` for integer/binary columns (e.g. commitment status). |
| `rows[].scale` | Row scale: physical row bounds = `scale * LP row bounds`. Row duals convert as `dual_physical = dual_LP * scale_objective / scale`. |
| `context` | Structured context (see below), or `null` when the entity has none. |
| `blocks[]` | One entry per (scenario, stage, block) of the cell. |
| `blocks[].duration` | Block duration in hours. |
| `blocks[].probability` | Scenario probability factor. |
| `blocks[].discount` | Stage discount factor. |
| `blocks[].cost_factor` | `probability * discount * duration`: the multiplier folded into every per-MWh cost to build its objective coefficient (before dividing by `scale_objective`). The dual `dual_LP * scale_objective / scale` of a block row still contains this factor; divide it by `cost_factor` to get the physical price in $/MWh (this is what gtopt's own outputs do). |

`context.type` is one of `stage` (`scenario`, `stage`), `block` (`scenario`,
`stage`, `block`), `block_ex` (+ `extra`), `scene_phase` (`scene`, `phase`),
`phase` (`scene`, `phase`, `element`), `iteration` (`scene`, `phase`,
`iteration`, `extra`) or `aperture` (`scene`, `phase`, `aperture`, `extra`).
All values are UIDs.

The JSON is streamed to disk (no in-memory document), one entry per line, so it
scales to large models.
