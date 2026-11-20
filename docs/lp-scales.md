# LP Scales Sidecar

A model file written with `--lp-file` (`.lp` or `.mps`) carries the whole
optimization problem: matrix, bounds, integer columns, names and the
objective constant.  Any solver can read it and reproduce gtopt's optimum.

What the file cannot carry is how gtopt **scaled** the problem.  The model
is written in gtopt's internal units, so a solution read back from an
external solver cannot be mapped to MW, hm³ or $/MWh without:

- the objective scale (`scale_objective`, 1000 by default);
- the per-block weights (`cost_factor`) folded into every cost;
- the scale factor of every column and row that gtopt scaled (reservoir
  volumes, voltage angles, equilibrated rows, ...).

`--lp-scales` writes those next to each model file: a small JSON sidecar
and, at the `full` level, two Parquet tables with the per-column and
per-row scale factors.  The option is **opt-in** and costs nothing when
off; the model file itself is unchanged.

## Enabling

```bash
gtopt system.json --lp-file model --lp-only --lp-scales           # full
gtopt system.json --lp-file model --lp-only --lp-scales summary
# or, equivalently
gtopt system.json --lp-file model --lp-only --set lp_scales=full
```

or in the planning JSON `options`: `"lp_scales": "full"`.

| Level | Writes |
|-------|--------|
| `none` (default; also `false`) | Nothing. |
| `summary` | The JSON only: build, objective scale and constant, block and stage weights, numerics.  A few kB per cell. |
| `full` (also `true`, and `--lp-scales` without a value) | The JSON plus the two Parquet tables with every column and row whose scale is not 1. |

The files follow the model file name; model (`.lp`, `.mps`) and
compression (`.gz`, `.zst`, ...) extensions are replaced:

| Model file                           | JSON sidecar                        | Parquet tables (`full`) |
|--------------------------------------|-------------------------------------|-------------------------|
| `model.lp` (single scene/phase)      | `model.scales.json`                 | `model.scales_cols.parquet`, `model.scales_rows.parquet` |
| `model.mps.gz` (`--lp-compression gzip`) | `model.scales.json.gz`          | `model.scales_cols.parquet`, `model.scales_rows.parquet` |
| `model_scene_S_phase_P.lp` (several) | `model_scene_S_phase_P.scales.json` | `model_scene_S_phase_P.scales_cols.parquet`, ... |

With `--lp-compression` the JSON is compressed with the same codec as the
model file.  The Parquet tables are always zstd-compressed internally and
keep their name.

A failure to write the sidecar is logged as a warning; it never aborts the
model dump.  Only `--lp-file` writes a sidecar; the `--lp-debug` and
error-LP dumps do not.

## Schema `gtopt-lp-scales/1`

```json
{
  "schema": "gtopt-lp-scales/1",
  "level": "full",
  "gtopt": {"version": "1.0", "git_revision": "v1.0-412-g4eec3995b573"},
  "lp_file": "model.lp",
  "scene_uid": 1,
  "phase_uid": 1,
  "num_cols": 78,
  "num_rows": 88,
  "objective": {"scale_objective": 1000, "constant": 25016, "sense": "min"},
  "blocks": [
    {"scenario": 1, "stage": 1, "block": 1, "duration": 1,
     "probability": 1, "discount": 1, "cost_factor": 1}
  ],
  "stages": [
    {"scenario": 1, "stage": 1, "duration": 24,
     "probability": 1, "discount": 1, "cost_factor": 24}
  ],
  "tables": {"columns": "model.scales_cols.parquet",
             "rows": "model.scales_rows.parquet",
             "num_scaled_columns": 12, "num_scaled_rows": 86},
  "numerics": {
    "equilibration_method": "row_max",
    "nnz": 240,
    "matrix": {"min_abs": 0.01, "max_abs": 1, "log10_ratio": 2},
    "matrix_min_col": "line_flowp_1_1_1_1",
    "matrix_max_col": "bus_theta_1_1_1_1",
    "objective": {"min_abs": 0.001, "max_abs": 1, "log10_ratio": 3},
    "bounds": {"min_abs": 10, "max_abs": 500, "log10_ratio": 1.7},
    "rhs": {"min_abs": 0.2, "max_abs": 1, "log10_ratio": 0.7},
    "col_scales": {"min_abs": 1, "max_abs": 1000, "log10_ratio": 3},
    "row_scales": {"min_abs": 0.25, "max_abs": 1, "log10_ratio": 0.6},
    "row_types": [
      {"type": "balance", "count": 24, "nnz": 96, "min_abs": 0.5, "max_abs": 1}
    ]
  }
}
```

### Header

| Field | Meaning |
|-------|---------|
| `level` | `summary` or `full`. |
| `gtopt.version`, `gtopt.git_revision` | The gtopt build that wrote the file (`git describe --always --dirty` of its source tree, or `unknown`). |
| `lp_file` | Name of the model file this sidecar describes, as written (`.lp`/`.mps`, possibly compressed). |
| `num_cols`, `num_rows` | Size of the LP, to check the sidecar against the model file. |

### Converting to physical units

| Field | Meaning |
|-------|---------|
| `objective.scale_objective` | Global objective divisor σ: `obj_physical = obj_LP * scale_objective`. |
| `objective.constant` | Objective constant in **physical** units.  Redundant with the model file (where it appears divided by σ), kept for convenience. |
| `blocks[]` | One entry per (scenario, stage, block): `cost_factor = probability * discount * duration` is folded into every per-MWh cost of that block. |
| `stages[]` | One entry per (scenario, stage), for stage-level entries (investment, capacity, stage constraints): `cost_factor = probability * discount * duration` with the stage duration. |
| `tables` | `full` only: file names (next to the JSON) of the two Parquet tables below, and how many entries they hold. |

### Parquet tables (`full`)

On a real system a cell has hundreds of thousands of columns and rows, and
with row equilibration nearly every row is scaled, so the per-entry scale
factors go to Parquet instead of the JSON.  Both tables carry `scene_uid`
and `phase_uid`, so the tables of every cell of a run (one per phase in
SDDP) can be read as one dataset, e.g.
`pandas.read_parquet(glob("model_*.scales_cols.parquet"))`.

`<stem>.scales_cols.parquet`, one row per column whose scale is not 1,
with `x_physical = scale * x_LP`:

| Column | Type | Meaning |
|--------|------|---------|
| `scene_uid`, `phase_uid` | int32 | The cell. |
| `index` | int32 | Column index in the LP. |
| `name` | string | Column name in the model file. |
| `class`, `variable`, `uid` | string, string, int32 | The gtopt element and LP variable. |
| `context` | string | `stage`, `block`, `block_ex`, `scene_phase`, `phase`, `iteration` or `aperture` (null without context). |
| `scenario`, `stage`, `block`, `scene`, `phase`, `element`, `iteration`, `aperture`, `extra` | int32 | The context members that apply to `context`; null otherwise. |
| `scale` | double | Column scale. |
| `unit` | string | Physical unit of `x_physical`, when known. |

`<stem>.scales_rows.parquet`, one row per row whose scale is not 1:

| Column | Type | Meaning |
|--------|------|---------|
| `scene_uid`, `phase_uid` | int32 | The cell. |
| `index` | int32 | Row index in the LP. |
| `name` | string | Row name in the model file. |
| `scale` | double | Row scale: `dual_physical = dual_LP * scale_objective / scale`. |

Divide a block row's physical dual by that block's `cost_factor` (a stage
row's by the stage's) to get the price per unit, as gtopt's own outputs do.
Columns and rows not listed have scale 1: their LP values are already in
physical units.

### Numerics

Figures of merit of the model **as handed to the solver**, after gtopt's
scaling and row equilibration.  Each range is `min_abs` / `max_abs` over
the finite non-zero magnitudes, and `log10_ratio` the decades they span:
above roughly 6–8 decades in the matrix, numerical trouble is likely.

| Field | Meaning |
|-------|---------|
| `equilibration_method` | Row equilibration applied (`none`, `row_max`, `ruiz`). |
| `nnz` | Non-zeros of the constraint matrix. |
| `matrix` | Coefficient range of the constraint matrix, with the columns holding the extremes (`matrix_min_col`, `matrix_max_col`).  Present when LP statistics are collected (the default for gtopt runs). |
| `objective`, `bounds`, `rhs` | Ranges of the objective coefficients, finite column bounds and finite row bounds. |
| `col_scales`, `row_scales` | Range of the scale factors gtopt applied: how aggressive the scaling had to be. |
| `row_types[]` | Coefficient range and size per constraint family (`balance`, `kvl`, `capacity`, ...), to locate the badly scaled part of a model. |

### Units

`unit` comes from the `lp` dialect of the unit registry
(`share/gtopt/unit_dialects.json`), keyed by the snake_case LP class and the
LP variable name; it is omitted for variables without an entry.
