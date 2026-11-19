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

`--lp-scales` writes those as a small JSON sidecar next to each model file.
The option is **opt-in** and costs nothing when off; the model file itself
is unchanged.

## Enabling

```bash
gtopt system.json --lp-file model --lp-only --lp-scales
# or, equivalently
gtopt system.json --lp-file model --lp-only --set lp_scales=true
```

or in the planning JSON `options`: `"lp_scales": true`.

The sidecar follows the model file name; model (`.lp`, `.mps`) and
compression (`.gz`, `.zst`, ...) extensions are replaced:

| Model file                           | Sidecar                                 |
|--------------------------------------|-----------------------------------------|
| `model.lp` (single scene/phase)      | `model.scales.json`                     |
| `model.mps.gz` (`--lp-compression`)  | `model.scales.json`                     |
| `model_scene_S_phase_P.lp` (several) | `model_scene_S_phase_P.scales.json`     |

A failure to write the sidecar is logged as a warning; it never aborts the
model dump.  Only `--lp-file` writes a sidecar; the `--lp-debug` and
error-LP dumps do not.

## Schema `gtopt-lp-scales/1`

```json
{
  "schema": "gtopt-lp-scales/1",
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
  "scaled_columns": [
    {"index": 12, "name": "reservoir_energy_1_1_1_1", "class": "Reservoir",
     "variable": "energy", "uid": 1,
     "context": {"type": "block", "scenario": 1, "stage": 1, "block": 1},
     "scale": 1000, "unit": "Mm3"}
  ],
  "row_scales": [1, 0.5, 0.25],
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
| `scaled_columns[]` | Only the columns whose scale is not 1: `x_physical = scale * x_LP`, in `unit` when known.  `name` is the name in the model file; `class`, `variable`, `uid` and `context` identify the gtopt element (`context.type` is one of `stage`, `block`, `block_ex`, `scene_phase`, `phase`, `iteration`, `aperture`, with only the members that apply to it). |
| `row_scales` | Scale of every row, in LP order (empty when all are 1): `dual_physical = dual_LP * scale_objective / row_scales[i]`.  Divide a block row's physical dual by that block's `cost_factor` (a stage row's by the stage's) to get the price per unit, as gtopt's own outputs do. |

Columns not listed have scale 1: their LP values are already in physical
units.

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
