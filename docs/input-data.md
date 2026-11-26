# GTOpt Input Data Structure Documentation

This document describes the input data structure required to define and run
optimization cases with **GTOpt** (Generation, Transmission and storage Optimization), the
open-source solver for the optimization and analysis of power-system
generation, transmission and storage.

> For a step-by-step tutorial with worked examples and time-series workflow,
> see **[Planning Guide](planning-guide.md)**.
> For auto-generated field tables from source code run:
> `python3 scripts/gtopt_field_extractor.py --format html --output field_reference.html`

---

## Overview

A GTOpt case is defined by one or more **JSON configuration files** that
contain three top-level sections:

| Section        | Description |
|----------------|-------------|
| `options`      | Global solver and I/O settings (includes `solver_options` and `variable_scales`) |
| `simulation`   | Time structure: blocks, stages, scenarios, phases, scenes, apertures |
| `system`       | Physical system: buses, generators, demands, lines, etc. |

The JSON file may reference **external data files** (CSV or Parquet) for
time-series or profile data.  These files are stored in a subdirectory
specified by `options.input_directory`.

### Directory Layout

```text
case_name/
├── case_name.json            # Main configuration file
└── <input_directory>/        # External data files
    ├── Demand/
    │   └── lmax.parquet      # Demand load profile
    ├── Generator/
    │   └── pmax.parquet      # Generator capacity profile
    └── ...
```

---

## 1. Options

Global settings that control solver behavior and I/O formats.  All fields are
optional -- when absent, the solver applies built-in defaults (shown below).

#### Input settings

| Field              | Type   | Default      | Description |
|--------------------|--------|--------------|-------------|
| `input_directory`  | string | `"input"`    | Root directory for external data files (resolved relative to JSON file) |
| `input_format`     | string | `"parquet"`  | Preferred input format: `"parquet"` or `"csv"` |

#### Model parameters (`options.model_options`)

These keys live in the `model_options` sub-object; the former flat copies
at the top level of `options` are rejected (see
[Planning Options](planning-options.md#model-parameters-moved-to-model_options)).
The table lists the commonly used ones; the full list is in
[Planning Options → ModelOptions](planning-options.md#modeloptions-fields).

| Field                  | Type    | Default   | Units         | Description |
|------------------------|---------|-----------|---------------|-------------|
| `demand_fail_cost`     | number  | `1000`    | $/MWh         | Penalty cost for unserved demand (value of lost load); see [Unserved-demand cost and hard demand](#unserved-demand-cost-and-hard-demand) |
| `battery_annual_standing_loss`  | number  | `0.48`    | p.u./year     | Self-discharge for batteries without `annual_standing_loss` (4 %/month; `0` = legacy lossless). See [Battery self-discharge default](#battery-self-discharge-default) |
| `battery_power_envelope` | boolean | `true`  | --            | Per-block valid inequality `p_charge/Pc_max + p_discharge/Pd_max ≤ 1` on every battery; `false` restores the pre-envelope LP |
| `reserve_shortage_cost` | number | *(none)*  | $/MWh         | Penalty cost for unserved spinning reserve (formerly `reserve_fail_cost`) |
| `hydro_spill_cost`     | number  | *(none)*  | $/m³          | Default penalty cost for unmet hydro (irrigation) rights (formerly `hydro_fail_cost`). Per-element `fail_cost` overrides this |
| `hydro_use_value`      | number  | *(none)*  | $/m³          | Default value (benefit) of exercising hydro rights. Per-element `use_value` overrides this |
| `state_violation_cost` | number  | *(none)*  | $/MWh         | Penalty cost for SDDP state-variable violations (formerly `state_fail_cost`). Fallback when a reservoir/storage element lacks its own `scost`; converted via element's `mean_production_factor` |
| `use_line_losses`      | boolean | `true`    | --            | **Deprecated** — use `line_losses_mode` instead. Enable resistive line-loss modeling |
| `line_losses_mode`     | string  | `"adaptive"` | --         | Line-loss model: `"none"`, `"linear"`, `"piecewise"`, `"bidirectional"`, `"adaptive"`, `"dynamic"` |
| `loss_segments`        | integer | `1`       | --            | Number of piecewise-linear segments for quadratic line losses (1 = linear) |
| `loss_cost_eps`        | number  | `0.1`     | $/MWh         | Cost on the line-loss columns: breaks bidirectional-flow degeneracy and blocks loss arbitrage under negative LMPs. Per-line `Line.loss_cost_eps` overrides; `0` disables. Inert for `none`/`linear`/`piecewise_direct` |
| `loss_flow_abs_cost`   | number  | `0.001`   | $/MWh         | Pin on the `tangent_signed_flow` abs-flow proxy columns (Σv ≥ \|f\|): forces Σv = \|f\| at the optimum. Shifts LMP differences by at most ≈ its value per lossy line. Only when `loss_cost_eps > 0`. Per-line `Line.loss_flow_abs_cost` overrides; `0` disables; `1e-6` = legacy internal pin |
| `loss_overflow_cost`   | number  | `demand_fail_cost` | $/MWh         | Price on the loss carried by the overflow tier past a decoupled `Line.loss_envelope` (flow between the envelope and `tmax`): closes the overflow energy sink under negative bus prices; genuine overloads pay it on their loss above `R·env²/V²`. Per-line `Line.loss_overflow_cost` overrides; `0` disables. Unset default: the case's `demand_fail_cost` (global `model_options` value; per-demand `fcost` is not consulted), itself 1000 when unset; 1000 with a warning when it is an explicit 0. |
| `prev_stage_link` | string | `stage` | —        | `<attr>_prev` at the first block of a stage whose previous stage is in the same phase: `stage` = that stage's last block, `none` = 0 (legacy) |
| `overload_slack_bound` | string | `thermal` | — | Default `Line.overload_slack_bound`: `thermal` = `min(hard − normal, max_overload_ratio × normal)`, `ratio`, `unbounded` |
| `max_overload_ratio` | number | 1.5 | — | Default `Line.max_overload_ratio` (slack ≤ ratio × normal). A guard rail above reported DLR gains (+10–25 % typical, ~40 % at windy sites, 50–72 % outliers; see the formulation refs [39]–[44]) |
| `use_line_dlr` | boolean | `true` | — | Global DLR switch: `false` ignores every `Line.dlr` block (static `tmax` applies), for static-vs-DLR comparisons. Also `--set model_options.use_line_dlr=false` |
| `line_thermal_limit_mode` | string | `flow` | —        | Default for `Line.line_thermal_limit_mode`: `flow` limits the flow variable only (no extra row); `sending` limits the sending-end power `f + a·ℓ`; `full` adds `f + ℓ ≤ tmax` per direction. Per-line value overrides |
| `use_kirchhoff`        | boolean | `true`    | --            | Enable DC Kirchhoff voltage-law constraints |
| `use_single_bus`       | boolean | `false`   | --            | Collapse network to a single bus (copper-plate model) |
| `kirchhoff_threshold`  | number  | `0`       | kV            | Minimum bus voltage below which Kirchhoff is not applied |
| `dc_line_reactance_threshold` | number | `1e-6` | p.u.   | `\|X/V²\|` below which a line's KVL flow term is dropped (see `tiny_reactance_mode`); `0` disables |
| `tiny_reactance_mode`  | string  | `"tie"`   | --            | Lines with `reactance` 0 or below `dc_line_reactance_threshold`: `"tie"` = zero-impedance tie (bus angles forced equal, `θ_a − θ_b = φ`); `"dc"` = legacy free-flow DC link. Lines without `reactance` are always DC links |
| `tiny_reactance_rel_threshold` | number | `0.01` | -- | Relative tie floor: a line is a zero-impedance tie when `\|X/V²\|` < max(`dc_line_reactance_threshold`, this × median `\|X/V²\|`); `0` = absolute floor only |
| `tie_line_losses` | boolean | `false` | -- | `false`: zero-impedance ties are lossless (no loss rows); `true`: legacy, ties keep their loss model |
| `loss_segment_bound` | boolean | `true` | -- | Ordered-fill upper bound `min(w_k, max(0, tmax − b_{k−1}))` on PWL loss segment columns (valid tightening cut); `false` = width-only bounds |
| `scale_objective`      | number  | `1000`    | dimensionless | Divisor applied to all objective coefficients for numerical stability |
| `scale_theta`          | number  | auto      | dimensionless | Voltage-angle enlargement: θ_LP = scale_theta · θ (rad); auto = 1/(3·median(X/V²)); 1 = radians; 1000 = milliradians |
| `objective_mode`       | string  | `"cost"`  | --            | `"emissions"` minimises emissions instead of cost (see [Emissions](#325-emissions)). Emission prices and caps are set on `EmissionZone`, not here |
| `continuous_phases`    | string  | `"none"`  | --            | Phase range where all integer/binary variables relax to continuous. Syntax: `"all"`, `"none"`, `"0"`, `"1,3:5,8:"`, `":3"` |
| `ramp_formula`         | string  | `"backward"` | --         | Ramp discretisation: `"backward"` (default, also on point stages: the interval slope `p_k − p_{k−1} ≤ RU·h_k`, `h_k = t_k − t_{k−1}`, cyclic wrap `h_0 = t_0 + H − t_{N−1}`) or `"central2"` (opt-in, NOT recommended: a convex combination of the adjacent slopes that lets zig-zag dispatch through; see formulation C4/C5) |
| `uc_formulation`       | string  | `"energy_block"` | --     | Unit-commitment formulation on chronological block stages: `"energy_block"` (default, block-average power) or `"power_based"` (power at the block boundaries, block energy = trapezoid, exact ramps, start-up / shut-down trajectories, ramp-capability reserves; see [Unit Commitment §4.6](unit-commitment.md#46-power-based-formulation-uc_formulation--power_based)). `Commitment.uc_formulation` overrides it per unit |
| `ramp_wrap`            | boolean | `false`   | --            | `central2` only: wrap the end nodes cyclically over the stage period |
| `storage_integration` | string | per type | -- | Point-sampled stages only (block stages always use `block`). Storage balance time integration: `block` (`e_b = e_{b−1} + f_b·Δt_b`), `trapezoid` / `lg4` / `lg6` (banded 2- / 4- / 6-point Lagrange rule on `Block.time`, level at the node; rows balanced so interval sums = interval lengths and column sums = block weights), `lg4_pos` / `lg6_pos` (nonnegative weights; `lg4_pos` is the recommended high-order rule — plain `lg4` / `lg6` have negative weights and are not recommended for LP); every banded rule adds charge-only / discharge-only envelope rows (`max(D_kj, 0)`). Resolution: element `storage_integration` → `reservoir_storage_integration` / `battery_storage_integration` → this value → `lg4_pos` (reservoirs) / `block` (others) |
| `reservoir_storage_integration` | string | `"lg4_pos"` | -- | Point-stage rule of reservoirs (see `storage_integration`) |
| `battery_storage_integration` | string | `"block"` | -- | Point-stage rule of batteries and the other energy storages (see `storage_integration`) |

> **Note**: `annual_discount_rate` has moved to the `simulation`
> section (see [Section 2](#2-simulation)).  For backward
> compatibility, it is still accepted here.

#### Output settings

| Field                | Type    | Default      | Description |
|----------------------|---------|--------------|-------------|
| `output_directory`   | string  | `"output"`   | Root directory for output result files |
| `output_format`      | string  | `"parquet"`  | Output format: `"parquet"` or `"csv"` |
| `output_compression` | string  | `"zstd"`     | Compression codec: `"uncompressed"`, `"gzip"`, `"zstd"`, `"lz4"`, `"bzip2"`, `"xz"` |
| `use_uid_fname`      | boolean | `true`       | Use element UIDs instead of names in output filenames |

> **LP variable/row names** are enabled automatically when the CLI flag
> `--lp-file` or `--lp-debug` is set; there is no separate JSON or CLI
> option to control the naming level.  See
> [Planning Options → LpMatrixOptions](planning-options.md#lpmatrixoptions-fields).

#### Solver selection

| Field         | Type   | Default        | Description |
|---------------|--------|----------------|-------------|
| `method` | string | `"monolithic"` | Planning solver: `"monolithic"` (default), `"sddp"`, or `"cascade"`. See [SDDP Solver](methods/sddp.md), [Cascade Solver](methods/cascade.md), and [Monolithic Solver](methods/monolithic.md) |
| `build_mode` | string | `"scene-parallel"` | How `PlanningLP::create_systems` assembles per-cell `SystemLP`s: `"serial"`, `"scene-parallel"` (default), `"full-parallel"`, or `"direct-parallel"`. Serial builds in the calling thread with no pool/build-buffer overhead |

#### Logging and debugging

| Field                       | Type    | Default  | Description |
|-----------------------------|---------|----------|-------------|
| `log_directory`             | string  | `"logs"` | Directory for log, trace, and error LP files |
| `lp_debug`                  | boolean | `false`  | Save LP debug files to `log_directory` before solving. Monolithic: one file per `(scene, phase)` named `gtopt_lp_<scene>_<phase>.lp`. SDDP: one file per `(iteration, scene, phase)` named `gtopt_iter_<iter>_<scene>_<phase>.lp` |
| `lp_compression`            | string  | `""`     | Compression codec for LP debug files: `""` (inherit from output), `"none"` (no compression), or a codec name (`"zstd"`, `"gzip"`, `"lz4"`, `"bzip2"`, `"xz"`) |
| `lp_only`                   | boolean | `false`  | Build all LP matrices but skip solving entirely (CLI: `--lp-only` / `-c`). Combine with `lp_debug: true` to export every scene/phase LP |
| `lp_scales`               | string  | `none`   | `none`, `summary` (JSON `<lp-file>.scales.json` with objective scale, block weights, numerics) or `full` (plus Parquet tables of the scaled columns/rows); `true`/`false` mean `full`/`none` (CLI: `--lp-scales`; see [LP scales](lp-scales.md)) |
| `lp_coeff_ratio_threshold`  | number  | `1e7`    | When the global max/min coefficient ratio exceeds this value, per-scene/phase breakdown is printed |
| `lp_debug_scene_min`        | integer | —        | Minimum scene UID (inclusive) for LP debug file saving |
| `lp_debug_scene_max`        | integer | —        | Maximum scene UID (inclusive) for LP debug file saving |
| `lp_debug_phase_min`        | integer | —        | Minimum phase UID (inclusive) for LP debug file saving |
| `lp_debug_phase_max`        | integer | —        | Maximum phase UID (inclusive) for LP debug file saving |
| `lp_fingerprint`            | boolean | `false`  | Compute LP structural fingerprint after solving. Output: `lp_fingerprint_scene_{S}_phase_{P}.json` per scene/phase. See [LP Fingerprint](lp-fingerprint.md) |

#### Constraint handling

| Field             | Type   | Default    | Description |
|-------------------|--------|------------|-------------|
| `constraint_mode` | string | `"strict"` | User-constraint runtime error policy: `"normal"` (warn + drop offending constraint), `"strict"` (default; abort with diagnostic), `"debug"` (strict + verbose per-row lowering trace). See [User Constraints → constraint_mode](user-constraints.md#constraint_mode--runtime-error-policy) |

#### Removed LP solver fields

The former top-level `lp_algorithm`, `lp_threads` and `lp_presolve` keys
are no longer part of the JSON contract; use the `solver_options`
sub-object (`algorithm`, `threads`, `presolve`; see Section 1.1).

### Example

```json
{
  "options": {
    "output_format": "csv",
    "model_options": {
      "use_single_bus": false,
      "demand_fail_cost": 1000,
      "scale_objective": 1000,
      "use_kirchhoff": true
    },
    "input_directory": "system_c0",
    "input_format": "parquet",
    "method": "monolithic",
    "solver_options": {
      "algorithm": 3,
      "presolve": true
    }
  }
}
```

### 1.1 SolverOptions

LP solver configuration exposed as a sub-object under `options`.
(The former top-level `lp_algorithm`, `lp_threads` and `lp_presolve`
fields were removed.)

| Field          | Type    | Default     | Description |
|----------------|---------|-------------|-------------|
| `algorithm`    | integer | `3` (barrier) | LP algorithm: 0 = auto, 1 = primal simplex, 2 = dual simplex, 3 = barrier |
| `threads`      | integer | `0`         | Number of solver threads (0 = automatic) |
| `presolve`     | boolean | `true`      | Enable LP presolve optimizations |
| `optimal_eps`  | number  | solver default | Optimality tolerance (omit to keep solver default) |
| `feasible_eps` | number  | solver default | Feasibility tolerance (omit to keep solver default) |
| `barrier_eps`  | number  | solver default | Barrier convergence tolerance (omit to keep solver default) |
| `log_level`    | integer | `0`         | Solver output verbosity (0 = silent) |
| `log_mode`     | string  | `"nolog"`   | Solver log-file policy: `"nolog"` (no files) or `"detailed"` (one file per scene/phase/aperture, `<solver>_sc<N>_ph<N>[_ap<N>].log`); numeric `0` / `1` also accepted |
| `time_limit`   | number  | solver default | Per-LP wall-clock time limit in seconds. Exceeding it aborts the solve; caller must check `is_optimal()` |
| `scaling`      | string  | solver default | Internal solver scaling strategy: `"none"`, `"automatic"`, `"aggressive"` (or `0` / `1` / `2`). See `SolverScaling` enum |
| `crossover`    | string  | `auto`      | `auto` / `primal` / `dual` / `none`. When `algorithm == barrier`, convert the interior-point solution to a simplex basis (vertex duals). The SDDP forward pass uses `none` only when nothing consumes its vertex solution (see [planning options](planning-options.md#forward-pass-crossover)) |
| `max_fallbacks`| integer | `2`         | On non-optimal exit, cycle through barrier → dual → primal up to this many times. `0` disables fallback |

**Example:**

```json
{
  "options": {
    "solver_options": {
      "algorithm": 3,
      "threads": 4,
      "presolve": true,
      "optimal_eps": 1e-8,
      "feasible_eps": 1e-8,
      "log_level": 1
    }
  }
}
```

### 1.2 VariableScale

Per-class or per-element LP variable scaling factors. Convention:
`physical_value = LP_value * scale`. Defined as an array under
`options.variable_scales`.

Resolution priority when the solver looks up a scale:

1. Per-element entry matching `(class_name, variable, uid)`
2. Per-class entry matching `(class_name, variable)` with `uid = -1`
3. Fallback: `1.0` (no scaling)

> **Note:** Global options (`scale_theta`) take precedence over entries
> in `variable_scales`.

| Field        | Type    | Default | Description |
|--------------|---------|---------|-------------|
| `class_name` | string  | —       | Element class (e.g. `"Bus"`, `"Reservoir"`, `"Battery"`) |
| `variable`   | string  | —       | Variable name (e.g. `"theta"`, `"volume"`, `"energy"`) |
| `uid`        | integer | `-1`    | Element UID (`-1` = apply to all elements of this class) |
| `scale`      | number  | `1.0`   | Scale factor: `physical = LP * scale` |

**Example:**

```json
{
  "options": {
    "variable_scales": [
      {"class_name": "Bus", "variable": "theta",
       "uid": -1, "scale": 0.001},
      {"class_name": "Reservoir", "variable": "volume",
       "uid": -1, "scale": 1000.0},
      {"class_name": "Battery", "variable": "energy",
       "uid": 1, "scale": 10.0}
    ]
  }
}
```

### 1.3 SDDPOptions

SDDP-specific solver configuration, specified as a sub-object under
`options.sddp_options`.  Field names omit the `sddp_` prefix since the
section name already provides the namespace.  All fields are optional.

For full algorithmic details, see [SDDP Solver](methods/sddp.md).

The tables below cover the common fields; the complete list with
defaults is in [Planning Options → SddpOptions](planning-options.md#sddpoptions-fields).

#### Iteration control

| Field              | Type    | Default  | Description |
|--------------------|---------|----------|-------------|
| `max_iterations`   | integer | `100`    | Maximum number of forward/backward iterations |
| `min_iterations`   | integer | `1`      | Minimum iterations before declaring convergence |
| `convergence_tol`  | number  | `0.01`   | Relative gap tolerance; the stop criterion only when stationarity is disabled (`stationary_tol = 0`) |
| `stationary_tol`   | number  | `0.005`  | ΔUB stationarity tolerance (default stop criterion under `convergence_mode = gap_stationary`); `0.0` disables it |
| `stationary_window`| integer | `4`      | Look-back window (iterations) for the stationarity check |

#### Advanced tuning

| Field                | Type    | Default        | Description |
|----------------------|---------|----------------|-------------|
| `elastic_penalty`    | number  | `1.0`          | Penalty for elastic slack variables in feasibility |
| `elastic_mode`       | string  | `"farkas_recursive"` | Elastic filter mode: `"farkas_recursive"`, `"state_repair"` (alias `"plp"`), `"chinneck"` (alias `"iis"`), `"single_cut"` (alias `"cut"`), or `"multi_cut"` |
| `multi_cut_threshold`| integer | `100`          | Cumulative forward-pass infeasibility count at a (scene, phase) before auto-switching to multi_cut (counter is persistent; 0 = always; <0 = disabled) |
| `cut_sharing_mode`   | string  | `"none"`       | Cut sharing across scenes: `"none"`, `"multicut"` or `"markov"` (experimental); the removed `"expected"` / `"accumulate"` / `"max"` names are rejected |
| `update_lp_skip` | integer | `0`        | Iterations to skip between LP coefficient updates, e.g. volume-dependent production factors (0 = every iteration) |

#### Cut file management

| Field                | Type    | Default  | Description |
|----------------------|---------|----------|-------------|
| `cut_directory`      | string  | `"cuts"` | Directory for Benders cut files |
| `save_per_iteration` | boolean | `true`   | Save cuts to CSV after each iteration (vs. only at end) |
| `cuts_input_file`    | string  | `""`     | File path for loading initial cuts (empty = cold start) |
| `sentinel_file`      | string  | `""`     | Path to a sentinel file; if it exists, the solver stops gracefully |

#### Boundary cuts (deprecated aliases)

The boundary (future-cost) cuts are configured on the FutureCost element
(`system.future_cost_array`, see [Section 3.26](#326-future-cost)).  The
keys below are **deprecated aliases**, honoured only when `method` is
`sddp` or `cascade` and translated into the FutureCost element at load
time with a warning.

| Field                       | Deprecated alias for |
|-----------------------------|----------------------|
| `boundary_cuts_file`        | `future_cost_array[].cuts_file` |
| `boundary_cuts_mode`        | `future_cost_array[].mode` |
| `boundary_cut_sharing_mode` | `future_cost_array[].sharing` |
| `boundary_cuts_mean_shift`  | `future_cost_array[].mean_shift` |
| `boundary_max_iterations`   | `future_cost_array[].iteration_limit` |
| `missing_cut_var_mode`      | `future_cost_array[].missing_cut_var_mode` |

`scale_alpha` and `cut_coeff_eps` stay genuine `sddp_options` fields
(they are SDDP algorithm settings, not aliases).

#### Apertures

| Field                 | Type      | Default   | Description |
|-----------------------|-----------|-----------|-------------|
| `apertures`           | int array | *(absent)*| Aperture UIDs for backward pass. Absent = use per-phase `apertures`. Empty `[]` = pure Benders. Non-empty = use exactly these UIDs |
| `num_apertures`       | integer   | *(absent)*| Apertures per phase taken from `Phase.apertures`: absent = all, `0` = none (pure Benders), `N` = first `N`. CLI `--sddp-num-apertures` |
| `aperture_selection_mode` | string | `"head"` | Which `num_apertures` entries are taken: `head` (first N, the wettest with `plp2gtopt` ordering), `stride` (evenly spaced), `tail` (last N) |
| `aperture_directory`  | string    | `""`      | Directory for aperture-specific scenario data (empty = use `input_directory`) |
| `aperture_timeout`    | number    | `30.0`    | Timeout in seconds for individual aperture LP solves (0 = no timeout) |
| `save_aperture_lp`    | boolean   | `false`   | Save LP files for infeasible apertures to `log_directory` |

#### Timeouts and warm-start

| Field            | Type    | Default | Description |
|------------------|---------|---------|-------------|
| `state_variable_lookup_mode` | string | `"warm_start"` | How `update_lp` elements (seepage, production factor, discharge limit) obtain reservoir volume between phases: `"warm_start"` (warm solution / vini, no cross-phase lookup) or `"cross_phase"` (previous phase's efin). Does **not** affect SDDP state-variable chaining or cut generation. |

#### Hot-start modes

| Field            | Type    | Default  | Description |
|------------------|---------|----------|-------------|
| `cut_recovery_mode` | string  | `"none"` | Hot-start mode: `"none"`, `"keep"`, `"append"`, or `"replace"`. Replaces the removed boolean `hot_start` (CLI: `--hot-start`) |

#### Cut pruning

| Field                  | Type    | Default  | Description |
|------------------------|---------|----------|-------------|
| `max_cuts_per_phase`   | integer | `0`      | Maximum retained cuts per (scene, phase) LP. 0 = unlimited |
| `cut_prune_interval`   | integer | `10`     | Iterations between cut pruning passes |
| `prune_dual_threshold` | number  | `1e-8`   | Dual threshold for inactive cut detection |
| `single_cut_storage`   | boolean | `false`  | Store cuts in per-scene vectors only |
| `max_stored_cuts`      | integer | `0`      | Maximum total stored cuts per scene (0 = unlimited) |
| `low_memory_mode`      | string  | `"compress"` (SDDP/cascade) / `"off"` (monolithic) | `off` or `compress` (`snapshot` and `rebuild` are back-compat aliases for `compress`; see SDDP method docs) |
| `memory_codec`         | string  | `"auto"` | Compression codec for `compress` mode |

#### Simulation mode

| Field             | Type    | Default | Description |
|-------------------|---------|---------|-------------|
| `simulation_mode` | boolean | `false` | Run in simulation mode: forward-only evaluation of the policy from loaded cuts. Sets `max_iterations=0` and disables cut saving. Feasibility cuts from the simulation pass are discarded by default for hot-start reproducibility |

#### Monitoring API

| Field         | Type    | Default | Description |
|---------------|---------|---------|-------------|
| `api_enabled` | boolean | `true`  | Enable the SDDP monitoring API (writes JSON status file each iteration) |

### 1.4 CascadeOptions (multi-level hybrid solver)

These options configure the cascade solver (`method = "cascade"`), which
runs a multi-level hybrid algorithm with progressive LP refinement.  They are
set in their own `cascade_options` sub-object (not inside `sddp_options`).
See [Cascade Method](methods/cascade.md) for full documentation, and
[SDDP Solver -- S10](methods/sddp.md#10-cascade-solver--multi-level-hybrid-solver)
for a summary.

The `cascade_options` object contains these top-level fields:

| Field           | Type   | Description |
|-----------------|--------|-------------|
| `model_options` | object | Global `ModelOptions` defaults for all levels |
| `sddp_options`  | object | Global `SddpOptions` defaults for all levels. `max_iterations` here is the global budget across all levels |
| `level_array`   | array  | Array of `CascadeLevel` objects. When empty or omitted, a single level with the global options is run (plain SDDP) |
| `write_level_outputs` | boolean | `false` (default): only the last active level writes results. `true`: every intermediate level also writes a full output set (results, `solution.csv`, cuts, `solver_status.json`, `level_summary.json`) to `<output_directory>/<level name>/`. See [Cascade Method](methods/cascade.md) |

Each element in the `level_array` is an object with the following optional
fields:

| Field           | Type    | Description |
|-----------------|---------|-------------|
| `uid`           | integer | Unique identifier for this level |
| `name`          | string  | Human-readable level name (for logging) |
| `model_options` | object  | LP formulation overrides; when present, a new LP is built. When absent, the previous level's LP is reused |
| `sddp_options`  | object  | Solver parameters for this level (per-level overrides) |
| `transition`    | object  | Transfer rules from the previous level |

**`model_options` fields** (same structure at both cascade and level scope):

| Field                  | Type    | Default | Description |
|------------------------|---------|---------|-------------|
| `use_single_bus`       | boolean | `false` | Aggregate all buses into one |
| `use_kirchhoff`        | boolean | `true`  | Enable DC power flow (voltage angles) |
| `line_losses_mode`     | string  | `"adaptive"` | Line-loss model (`use_line_losses` is the deprecated on/off form) |
| `kirchhoff_threshold`  | number  | `0.0`   | Threshold for Kirchhoff constraint activation |
| `loss_segments`        | integer | `1`     | Number of piecewise-linear loss segments |
| `scale_objective`      | number  | `1000`  | Divisor for objective coefficients |
| `scale_theta`          | number  | auto    | Voltage-angle enlargement: θ_LP = scale_theta · θ (rad); auto = 1/(3·median(X/V²)); 1 = radians; 1000 = milliradians |
| `demand_fail_cost`     | number  | `1000`  | Penalty cost for unserved demand [$/MWh] |
| `reserve_shortage_cost` | number | *(none)*| Penalty cost for unserved reserve [$/MWh] |

> **Note**: `annual_discount_rate` has moved to the `simulation`
> section.  For backward compatibility, it is still accepted in
> `model_options`.

**`sddp_options` fields** (per-level solver parameters):

| Field             | Type      | Default              | Description |
|-------------------|-----------|----------------------|-------------|
| `max_iterations`  | integer   | from global `sddp_options` | Maximum iterations for this level |
| `min_iterations`  | integer   | from global `sddp_options` | Minimum iterations before convergence |
| `apertures`       | int array | from global `sddp_options` | Aperture UIDs (absent = inherit, `[]` = Benders) |
| `convergence_tol` | number    | from global `sddp_options` | Convergence tolerance |
| `stationary_tol`  | number    | from global `sddp_options` | Stationary gap-change tolerance (0 = disabled) |
| `stationary_window`| integer  | from global `sddp_options` | Look-back window for stationary gap check |

**`transition` fields:**

See [CASCADE_SOLVER.md §4.5](methods/cascade.md) for detailed cut
forgetting semantics and the two-phase solve behavior.

| Field                        | Type    | Default | Description |
|------------------------------|---------|---------|-------------|
| `inherit_optimality_cuts`    | integer | `0`     | `0` = do not inherit; `-1` = inherit and keep forever; `N > 0` = inherit, then forget after N training iterations |

**SDDP example:**

```json
{
  "options": {
    "method": "sddp",
    "sddp_options": {
      "max_iterations": 200,
      "convergence_tol": 1e-5,
      "stationary_tol": 0.01,
      "stationary_window": 10,
      "cut_sharing_mode": "expected",
      "cut_directory": "cuts",
      "elastic_mode": "single_cut",
      "elastic_penalty": 1e7,
      "cut_recovery_mode": "keep",
      "apertures": []
    }
  }
}
```

**Cascade example:**

```json
{
  "options": {
    "method": "cascade",
    "sddp_options": {
      "max_iterations": 30,
      "convergence_tol": 0.01
    },
    "cascade_options": {
      "level_array": [
        {
          "name": "uninodal_benders",
          "model_options": {
            "use_single_bus": true,
            "use_kirchhoff": false,
            "use_line_losses": false
          },
          "sddp_options": {
            "max_iterations": 10,
            "apertures": [],
            "convergence_tol": 0.05
          }
        },
        {
          "name": "transport_sddp",
          "model_options": {
            "use_single_bus": false,
            "use_kirchhoff": false
          },
          "sddp_options": {
            "max_iterations": 15,
            "apertures": [1, 2, 3]
          },
          "transition": {
            "inherit_optimality_cuts": -1
          }
        },
        {
          "name": "full_sddp",
          "model_options": {
            "use_kirchhoff": true,
            "use_line_losses": true
          },
          "sddp_options": {
            "max_iterations": 30,
            "apertures": [1, 2, 3]
          },
          "transition": {
            "inherit_optimality_cuts": -1
          }
        }
      ]
    }
  }
}
```

**Simulation mode example:**

```json
{
  "options": {
    "method": "sddp",
    "sddp_options": {
      "simulation_mode": true,
      "cut_recovery_mode": "keep",
      "cut_directory": "cuts"
    }
  }
}
```

### 1.5 MonolithicOptions

Monolithic-solver-specific configuration, specified as a sub-object under
`options.monolithic_options`.  All fields are optional.

For full details, see [Monolithic Solver](methods/monolithic.md).

| Field                      | Type    | Default       | Description |
|----------------------------|---------|---------------|-------------|
| `solve_mode`               | string  | `"monolithic"`| Solve mode: `"monolithic"` (all phases in one LP) or `"sequential"` (phase-by-phase) |
| `boundary_cuts_file`       | string  | —             | Deprecated alias → `future_cost_array[].cuts_file` |
| `boundary_cuts_mode`       | string  | —             | Deprecated alias → `future_cost_array[].mode` |
| `boundary_cut_sharing_mode`| string  | —             | Deprecated alias → `future_cost_array[].sharing` |
| `boundary_max_iterations`  | integer | —             | Deprecated alias → `future_cost_array[].iteration_limit` |

The `boundary_*` aliases are honoured only when `method` is `monolithic`
(the default).  The monolithic solver reads the boundary cuts only from
the FutureCost element ([Section 3.26](#326-future-cost)) and never from
`sddp_options`.

**Example:**

```json
{
  "options": {
    "method": "monolithic",
    "monolithic_options": {
      "solve_mode": "monolithic"
    }
  },
  "system": {
    "future_cost_array": [
      {
        "uid": 1,
        "name": "fcf",
        "cuts_file": "boundary_cuts.csv",
        "mode": "separated"
      }
    ]
  }
}
```

---

## 2. Simulation

Defines the temporal structure of the optimization. The `simulation`
object contains arrays of time-structure elements organized in a
hierarchy:

```text
Scenario  (probability_factor)
  └─ Phase
       └─ Stage  (discount_factor, first_block, count_block)
            └─ Block  (duration [h])
```

Scenes group scenarios; Apertures define SDDP backward-pass openings.

### Simulation-level fields

In addition to the arrays below, the `simulation` object accepts these
scalar fields:

| Field                      | Type   | Default            | Description |
|----------------------------|--------|--------------------|-------------|
| `annual_discount_rate`     | number | `0.0`              | Annual discount rate for multi-stage CAPEX discounting [p.u./year] |
| `boundary_cuts_file`       | string | —                  | Deprecated alias → `future_cost_array[].cuts_file` (any method) |
| `boundary_cuts_valuation`  | string | —                  | Deprecated alias → `future_cost_array[].valuation` (any method) |
| `probability_rescale`      | string | `"runtime"`        | Rescale scenario / scene probabilities that do not sum to 1: `none` (warn only), `build` (at validation), `runtime` (at build and run time) |
| `kappa_warning`            | string | `"warn"`           | High basis condition number after a solve: `none`, `warn` (log a warning), `save_lp` (warn and write the LP). Needs a solver that reports kappa (`solver_options.kappa_mode`) |
| `kappa_threshold`          | number | `1e9`              | Kappa above which `kappa_warning` fires |
| `block_hour_map`           | string | —                  | Block → calendar-hour map file (written by `plp2gtopt`); metadata for post-processing, not read by the solver |
| `iteration_array`          | array  | —                  | Per-iteration SDDP control entries (`Iteration`) |

> **Backward compatibility**: `annual_discount_rate` is also accepted
> in `options` or `options.model_options`.  The boundary cuts are
> configured on the FutureCost element
> ([Section 3.26](#326-future-cost)).

| Array              | Element   | Required | Description |
|--------------------|-----------|----------|-------------|
| `block_array`      | Block     | Yes      | Indivisible time units |
| `stage_array`      | Stage     | Yes      | Planning/investment periods |
| `scenario_array`   | Scenario  | Yes      | Stochastic realizations |
| `phase_array`      | Phase     | No       | Groups of consecutive stages |
| `scene_array`      | Scene     | No       | Groups of scenarios |
| `aperture_array`   | Aperture  | No       | SDDP backward-pass openings |

When `phase_array` or `scene_array` are empty, a single default
Phase / Scene covering all stages / scenarios is created automatically.

### 2.1 Block

A block is the smallest indivisible time unit. `energy [MWh] = power [MW] × duration [h]`.

| Field      | Type   | Units | Required | Description |
|------------|--------|-------|----------|-------------|
| `uid`      | integer| —     | Yes      | Unique identifier |
| `name`     | string | —     | No       | Optional name |
| `duration` | number | h     | Yes      | Duration of the block |
| `time` | number | h | No | Node time inside the stage period (real hours), used by `ramp_formula = central2` and point-stage storage integration. Must lie in its cell [τ_{k−1}, τ_k] (cumulative durations). Default: block midpoint |

### 2.2 Stage

A stage groups consecutive blocks into a planning/investment period.

| Field            | Type    | Units | Required | Description |
|------------------|---------|-------|----------|-------------|
| `uid`            | integer | —     | Yes      | Unique identifier |
| `name`           | string  | —     | No       | Optional name |
| `first_block`    | integer | —     | Yes      | 0-based index of the first block in this stage |
| `count_block`    | integer | —     | Yes      | Number of consecutive blocks in this stage |
| `discount_factor`| number  | p.u.  | No       | Present-value cost multiplier for this stage |
| `active`         | boolean | —     | No       | Whether the stage is active |
| `chronological`  | boolean | —     | No       | Whether blocks are sequential time periods (enables unit commitment). Default: `false`. See [Unit Commitment](unit-commitment.md) |
| `cycle_group`    | integer | —     | No       | Storage cycle group: consecutive stages of one phase with the same id form one weighted cycle for `daily_cycle` storages (Σ w·ΔSoC = 0, see formulation §5.7). Default: unset (each stage closes its own day) |
| `cycle_weight`   | number  | —     | No       | Repetitions of the stage inside its `cycle_group` (e.g. days per typical week). Must be > 0. Default: duration / 24 |
| `period_hours`   | number  | —     | No       | Real length (h) of the chronological period sampled by the stage (168 typical week, 24 day). Block durations become weights; real step Δt = duration · period_hours / stage duration drives `daily_cycle` storage cycles, ramps and min up/down. Default: unset |
| `sampling`       | string  | —     | No       | `"block"` (default: a value holds constant over its block) or `"point"` (values are samples AT `Block.time`, required and increasing; durations are quadrature weights). Point stages integrate storage between nodes (`storage_integration`: `lg4_pos` for reservoirs, `block` for batteries by default), use interval-slope (`backward`) ramps scaled by `h_k = t_k − t_{k−1}`, floor commitment periods at 1 h, and count min up/down, commitment periods and `max_cycles_day` days on the quadrature cells; a periodic grid has N nodes (node N ≡ node 0 dropped, its weight added to node 0); duals ÷ duration are prices AT `t_k`; `block_time.csv` is written to the output directory |

### 2.3 Scenario

A scenario represents a possible future realization (hydrology, demand level, etc.).

| Field               | Type    | Units | Required | Description |
|---------------------|---------|-------|----------|-------------|
| `uid`               | integer | —     | Yes      | Unique identifier |
| `name`              | string  | —     | No       | Optional name |
| `probability_factor`| number  | p.u.  | No       | Probability weight (values are normalised to sum to 1) |
| `active`            | boolean | —     | No       | Whether the scenario is active |

`probability_factor` values are **global** probabilities: a scene's weight
is the sum of its scenarios' factors, and the total over all scenes should
be 1.  When it is not, a warning is always logged and, unless
`simulation.probability_rescale` is `none` (warn only), the values are
rescaled at build time (`build`, and `runtime`, the default):

1. total ≈ 1: left untouched;
2. every scene sums to ≈ 1 (two or more scenes; a single-scenario scene
   qualifies with factor 1): read as **conditional per-scene**
   probabilities — every factor is divided by the number of scenes, so
   the scenes get equal weight;
3. any other total: every factor is divided by the total, keeping the
   relative scene weights.  This changes the expected OPEX relative to
   CAPEX.  A total outside [0.5, 2] usually means the values are
   representative-period weights (e.g. day counts); in gtopt those belong
   in the block/stage `duration`, not in `probability_factor`.

### 2.4 Phase

A phase groups consecutive planning stages into a higher-level period.
This allows modelling distinct investment or operational windows
(e.g. a 5-year construction phase followed by a 20-year operational
phase). When only one phase is needed (the common case), it is created
automatically with defaults covering all stages.

| Field          | Type    | Units | Required | Description |
|----------------|---------|-------|----------|-------------|
| `uid`          | integer | —     | Yes      | Unique identifier |
| `name`         | string  | —     | No       | Optional name |
| `active`       | boolean | —     | No       | Whether the phase is active |
| `first_stage`  | integer | —     | Yes      | 0-based index of the first stage in this phase |
| `count_stage`  | integer | —     | No       | Number of stages (`-1` or omit = all remaining) |
| `apertures`    | array   | —     | No       | Array of aperture UIDs for this phase's SDDP backward pass (empty = use all) |

### 2.5 Scene

A scene groups consecutive scenarios into a logical set. Scenes are
used by the solver to partition scenarios when building LP sub-problems
(one LP per scene/phase combination). When no `scene_array` is
provided, a single default scene covering all scenarios is created.

| Field            | Type    | Units | Required | Description |
|------------------|---------|-------|----------|-------------|
| `uid`            | integer | —     | Yes      | Unique identifier |
| `name`           | string  | —     | No       | Optional name |
| `active`         | boolean | —     | No       | Whether the scene is active |
| `first_scenario` | integer | —     | Yes      | 0-based index of the first scenario in this scene |
| `count_scenario` | integer | —     | No       | Number of scenarios (`-1` or omit = all remaining) |

### 2.6 Aperture

An aperture represents one hydrological (or stochastic) realization
used in the SDDP backward pass. Each aperture references a **source
scenario** whose affluent data (flow bounds) are applied to the cloned
phase LP before solving. Apertures allow the backward pass to sample
a different set of scenarios than the forward pass.

When no `aperture_array` is provided, the backward pass builds a plain
Benders cut from the forward solution (no apertures), unless
`sddp_options.apertures` lists UIDs: then one aperture is synthesised
per listed UID from the first scenarios.  `sddp_options.num_apertures`
selects how many entries of each phase's `Phase.apertures` list are
used: unset = all, `0` = none, `N` = the first `N`.

| Field                | Type    | Units | Required | Description |
|----------------------|---------|-------|----------|-------------|
| `uid`                | integer | —     | Yes      | Unique identifier |
| `name`               | string  | —     | No       | Optional name |
| `active`             | boolean | —     | No       | Whether the aperture is active |
| `source_scenario`    | integer | —     | Yes      | UID of the scenario whose affluent data to use |
| `probability_factor` | number  | p.u.  | No       | Probability weight (normalised to sum 1 across active apertures; default: 1) |

> **Note:** When `aperture_directory` is set in `sddp_options`,
> the source scenario is first looked up in that directory; if not
> found there, it falls back to the regular `input_directory`.

### Example

```json
{
  "simulation": {
    "block_array": [
      {"uid": 1, "duration": 1},
      {"uid": 2, "duration": 2}
    ],
    "stage_array": [
      {"uid": 1, "first_block": 0, "count_block": 1, "active": 1},
      {"uid": 2, "first_block": 1, "count_block": 1, "active": 1}
    ],
    "scenario_array": [
      {"uid": 1, "probability_factor": 1}
    ],
    "phase_array": [
      {"uid": 1, "first_stage": 0, "count_stage": 2}
    ],
    "scene_array": [
      {"uid": 1, "first_scenario": 0, "count_scenario": 1}
    ],
    "aperture_array": [
      {"uid": 1, "source_scenario": 1, "probability_factor": 0.5},
      {"uid": 2, "source_scenario": 2, "probability_factor": 0.5}
    ]
  }
}
```

---

## 3. System

The system section defines all physical components of the power system.

### 3.1 Bus

An electrical bus (node) in the network.

| Field             | Type    | Units | Required | Description |
|-------------------|---------|-------|----------|-------------|
| `uid`             | integer | —     | Yes      | Unique identifier |
| `name`            | string  | —     | Yes      | Bus name |
| `active`          | boolean | —     | No       | Whether the bus is active |
| `voltage`         | number  | kV    | No       | Nominal voltage level |
| `reference_theta` | number  | rad   | No       | Fixed voltage angle (reference bus: set to 0) |
| `use_kirchhoff`   | boolean | —     | No       | Override global Kirchhoff setting for this bus |

### 3.2 Generator

A generation unit connected to a bus.

| Field              | Type                | Units        | Required | Description |
|--------------------|---------------------|--------------|----------|-------------|
| `uid`              | integer             | —            | Yes      | Unique identifier |
| `name`             | string              | —            | Yes      | Generator name |
| `bus`              | integer\|string     | —            | Yes      | Connected bus UID or name |
| `active`           | boolean             | —            | No       | Whether the generator is active |
| `pmin`             | number\|array\|string| MW          | No       | Minimum active power output |
| `pmin_fcost`       | number\|array\|string| $/MWh       | No       | Makes `pmin` soft: `generation + unserved ≥ pmin`, the slack priced at this cost (unset = hard `pmin`) |
| `pmax`             | number\|array\|string| MW          | No       | Maximum active power output |
| `gcost`            | number\|array\|string| $/MWh       | No       | Variable generation cost |
| `lossfactor`       | number\|array\|string| p.u.        | No       | Network loss factor |
| `capacity`         | number\|array\|string| MW          | No       | Installed capacity |
| `expcap`           | number\|array\|string| MW          | No       | Capacity added per expansion module |
| `expmod`           | number\|array\|string| —           | No       | Maximum number of expansion modules |
| `capmax`           | number\|array\|string| MW          | No       | Absolute maximum capacity |
| `annual_capcost`   | number\|array\|string| $/MW-year   | No       | Annualized investment cost |
| `annual_derating`  | number\|array\|string| p.u./year   | No       | Annual capacity derating factor |
| `emission_rate`  | number\|array\|string| tCO₂/MWh   | No       | **Legacy** CO₂ emission rate (scalar only), folded into a `co2` `EmissionSource`. Prefer `emissions` or `emission_source_array`; see [Emissions](#325-emissions) |
| `emissions`      | array                | —            | No       | Inline `EmissionSource` rows (`zone`, `emission`, `rate`, `upstream_rate`) for this generator |
| `emission_captures` | array             | —            | No       | CCS per pollutant: `emission`, capture fraction `rate` [p.u.], `cost` [$/t captured] |
| `initial_generation` | number           | MW           | No       | Dispatch at `t = 0⁻` (PLEXOS *Initial Generation*). Value of `generation_prev` at the first block of the horizon, so ramp user constraints `gen − gen_prev ≤ R` see the real starting point. Also the native `Commitment` ramp `p_prev` there when `initial_power` is unset. Never applied past the horizon start. Unset → 0 (cold start) |
| `cogen_mode`       | string              | —            | No       | Cogeneration tag: `"dispatched"` or `"must_run"` (unset = not a cogeneration unit) |
| `uini`             | number              | —            | No       | Units online at t = 0 (informational, written by `plexos2gtopt`; commitment state comes from `Commitment.initial_status`) |

> **Note:** Fields that accept `number|array|string` can be a numeric constant,
> an inline array (indexed by `[stage][block]`), or a filename referencing an
> external Parquet/CSV file in `input_directory/Generator/`.

### 3.3 Demand

An electrical demand (load) connected to a bus.

| Field              | Type                | Units        | Required | Description |
|--------------------|---------------------|--------------|----------|-------------|
| `uid`              | integer             | —            | Yes      | Unique identifier |
| `name`             | string              | —            | Yes      | Demand name |
| `bus`              | integer\|string     | —            | Yes      | Connected bus UID or name |
| `active`           | boolean             | —            | No       | Whether the demand is active |
| `lmax`             | number\|array\|string| MW          | No       | Maximum served load |
| `lmin`             | number\|array\|string| MW          | No       | Minimum served load per (stage, block) |
| `lossfactor`       | number\|array\|string| p.u.        | No       | Network loss factor |
| `fcost`            | number\|array\|string| $/MWh       | No       | Demand curtailment cost; falls back to `model_options.demand_fail_cost` (default 1000). `0` = freely dispatchable load |
| `fail_segments`    | array of objects    | —            | No       | Stepwise curtailment (VoLL) curve `[{fraction, cost}, …]`; overrides `fcost` (see below) |
| `forced`           | boolean             | —            | No       | When `true`, load is fixed at `lmax` (hard, non-sheddable demand; no curtailment) |
| `emin`             | number\|array\|string| MWh         | No       | Minimum energy that must be served per stage |
| `ecost`            | number\|array\|string| $/MWh       | No       | Energy-shortage cost |
| `capacity`         | number\|array\|string| MW          | No       | Installed capacity |
| `expcap`           | number\|array\|string| MW          | No       | Capacity added per expansion module |
| `expmod`           | number\|array\|string| —           | No       | Maximum number of expansion modules |
| `capmax`           | number\|array\|string| MW          | No       | Absolute maximum capacity |
| `annual_capcost`   | number\|array\|string| $/MW-year   | No       | Annualized investment cost |
| `annual_derating`  | number\|array\|string| p.u./year   | No       | Annual capacity derating factor |

#### Stepwise failure curve (`fail_segments`)

`fail_segments` is an ordered list of curtailment segments.  Each object
has `fraction` (share of the block demand `lmax`, `0 < fraction ≤ 1`) and
`cost` ($/MWh; scalar, per-stage array, or a filename under
`input_directory/Demand/`).  Only the last segment may omit `fraction`:
it is then open and covers the residual `1 − Σ fraction`.  When every
fraction is explicit and they sum to less than 1, the uncovered remainder
is firm load (it cannot be curtailed — PLP `FALLA_*` semantics).

```json
{"uid": 1, "name": "d1", "bus": "b1", "lmax": 120,
 "fail_segments": [
   {"fraction": 0.05, "cost": 492.4},
   {"fraction": 0.05, "cost": 541.7},
   {"fraction": 0.10, "cost": 648.6},
   {"cost": 742.1}]}
```

Costs should be non-decreasing (a warning is issued otherwise).  The LP
adds one served-load column per segment (no extra rows); a single
segment is identical to `fcost`.  Precedence: `forced` > `fail_segments`
> `fcost` > `model_options.demand_fail_segments` >
`model_options.demand_fail_cost`.  Outputs: `Demand/fail_sol` (total)
plus `Demand/fail_seg<k>_sol` per segment (K ≥ 2).  User constraints can
reference `demand('d1').fail` (total curtailment) and
`demand('d1').load`.  A demand with K ≥ 2 segments cannot be the charge
demand of a `Converter` nor carry a `DemandProfile` / demand
`CapacityProfile`.

### 3.4 Line

A transmission line connecting two buses.

| Field              | Type                | Units        | Required | Description |
|--------------------|---------------------|--------------|----------|-------------|
| `uid`              | integer             | —            | Yes      | Unique identifier |
| `name`             | string              | —            | Yes      | Line name |
| `bus_a`            | integer\|string     | —            | Yes      | Sending-end (from) bus |
| `bus_b`            | integer\|string     | —            | Yes      | Receiving-end (to) bus |
| `active`           | boolean             | —            | No       | Whether the line is active |
| `voltage`          | number\|array\|string| kV          | No       | Nominal voltage level. Omit or set to 1.0 for per-unit mode |
| `resistance`       | number\|array\|string| Ω (p.u.)    | No       | Series resistance. Use Ω when voltage is in kV; p.u. when voltage is omitted |
| `reactance`        | number\|array\|string| Ω (p.u.)    | No       | Series reactance (DC power flow). Use Ω when voltage is in kV; p.u. when voltage is omitted. Susceptance: $B = V^2 / X$. Omit for a DC/HVDC link (no KVL). `0` or `\|X/V²\|` below `dc_line_reactance_threshold` is a zero-impedance tie forcing equal bus angles (unless `tiny_reactance_mode = "dc"`) |
| `lossfactor`       | number\|array\|string| p.u.        | No       | Lumped loss factor |
| `line_losses_mode` | string              | —            | No       | Loss model of this line (`none`, `linear`, `piecewise`, `bidirectional`, `adaptive`, `dynamic`, `piecewise_direct`, `tangent_signed_flow`); unset inherits `model_options.line_losses_mode` |
| `loss_segments`    | integer             | —            | No       | Piecewise-linear segments of the quadratic loss curve; unset inherits `model_options.loss_segments` (1) |
| `loss_pwl_layout`  | string              | —            | No       | Segment layout of the loss approximation: `uniform` (default), `equal_error`, `tangent`, `midpoint` |
| `loss_allocation_mode` | string          | —            | No       | Bus that carries the loss: `receiver` (default), `sender`, or `split` (half at each end) |
| `loss_envelope`    | number\|array\|string| MW          | No       | Flow range over which the loss segments are spread (e.g. the original rating when `tmax` is lifted). Unset = `max(tmax_ab, tmax_ba)`. It never caps the flow: between `loss_envelope` and `tmax` losses stay quadratic and are priced by `loss_overflow_cost` |
| `loss_secant_segments` | integer         | —            | No       | `tangent_signed_flow` only: secant segments of the loss chord (default 1); unset inherits `model_options.loss_secant_segments` |
| `loss_use_sos2`    | boolean             | —            | No       | `tangent_signed_flow` only: SOS2 fill order on the secant segments (needs a MIP solver; default `false`) |
| `use_line_losses`  | boolean             | —            | No       | **Deprecated** — use `line_losses_mode` (`false` = `none`) |
| `loss_cost_eps`    | number              | $/MWh        | No       | Per-line price on the line-loss columns; overrides `model_options.loss_cost_eps` (default 0.1). Set ≥ ½·\|π_a + π_b\| on corridors where the end-bus prices can sum negative to block loss arbitrage; `0` disables. Inert for `none`/`linear`/`piecewise_direct` |
| `loss_flow_abs_cost` | number            | $/MWh        | No       | Per-line `tangent_signed_flow` abs-flow pin; overrides `model_options.loss_flow_abs_cost` (default 0.001). Only when the line's `loss_cost_eps > 0`; `0` disables |
| `loss_overflow_cost` | number            | $/MWh        | No       | Per-line overflow-tier price; overrides `model_options.loss_overflow_cost` (default: the case's `demand_fail_cost`). Charged per MWh of loss carried between `loss_envelope` and `tmax`; `0` disables. Inert when `tmax ≤ loss_envelope` |
| `line_thermal_limit_mode` | string       | —            | No       | How the thermal limit treats losses: `flow` (flow variable only), `sending` (sending-end power `f + a·ℓ`, `a` = sending-bus loss share of `loss_allocation_mode`) or `full` (`f + ℓ ≤ tmax`). Also folded into the capacity rows of expandable lines. Unset inherits `model_options.line_thermal_limit_mode` |
| `type`             | string              | —            | No       | Element type tag; use `"transformer"` for transformers |
| `tap_ratio`        | number\|array\|string| p.u.        | No       | Off-nominal tap ratio τ (default 1.0). A tap-changing transformer with τ ≠ 1 has effective susceptance $B/\tau$. Supports per-stage schedule. |
| `phase_shift_deg`  | number\|array\|string| degrees     | No       | Phase-shift angle φ in degrees (default 0). Models a Phase-Shifting Transformer (PST); shifts the Kirchhoff constraint RHS by $-\sigma_\theta \cdot \phi_{\text{rad}}$. |
| `tmax_ab`          | number\|array\|string| MW          | No       | Max flow in A→B direction |
| `tmax_ba`          | number\|array\|string| MW          | No       | Max flow in B→A direction |
| `max_flow`, `min_flow` | number\|array\|string | MW | No | PLEXOS `Max Flow` / `Min Flow`: aliases of `tmax_ab` and `-tmax_ba` (`min_flow` is **signed**, ≤ 0). Give either the alias or `tmax_*`, not both. Never replaced by a DLR rating. See [Line limits](#line-limits) |
| `max_rating`, `min_rating` | number\|array\|string | MW | No | PLEXOS `Max Rating` / `Min Rating` (normal rating; `min_rating` signed, ≤ 0). The limit is `min` with every other set field |
| `overload_max_rating`, `overload_min_rating` | number\|array\|string | MW | No | PLEXOS `Overload Max/Min Rating` (emergency rating; min signed). The hard cap when `overload_penalty` / `limit_penalty` > 0 in the block, with the normal rating as the priced threshold; unused otherwise |
| `outage_max_rating`, `outage_min_rating` | number\|array\|string | MW | No | PLEXOS `Outage Max/Min Rating` (min signed): the rating in blocks with `units_out > 0` (maintenance derate); never replaced by a DLR rating |
| `units_out` | number\|array\|string | — | No | PLEXOS `Units Out`: `> 0` applies `outage_*_rating` in that block |
| `limit_penalty` | number\|array\|string | $/MWh | No | PLEXOS `Limit Penalty`: alias of `overload_penalty` (give one) |
| `conductor_rating_ab`, `conductor_rating_ba` | number\|array\|string | MW | No | Conductor (thermal) rating per direction; a `dlr` rating replaces it by default |
| `equipment_rating_ab`, `equipment_rating_ba` | number\|array\|string | MW | No | Series-equipment rating per direction (current transformers/TTCC, series capacitors/CCSS, wave traps, breakers); never weather-scaled |
| `security_limit_ab`, `security_limit_ba` | number\|array\|string | MW | No | Security / operational limit per direction (voltage or angle stability, N-1 transfer limits); never weather-scaled |
| `tmax_normal_ab`   | number\|array\|string| MW          | No       | Soft (normal) A→B threshold. With `overload_penalty > 0`, flow above it (up to `tmax_ab`) pays the penalty; ignored when unset or `≥ tmax_ab` |
| `tmax_normal_ba`   | number\|array\|string| MW          | No       | Soft (normal) B→A threshold (same semantics as `tmax_normal_ab`) |
| `overload_penalty` | number\|array\|string| $/MWh       | No       | Price per MWh of flow above `tmax_normal_*` (per-(stage, block), weighted by block duration like any energy cost). `0` / unset: no overload columns |
| `overload_slack_bound` | string          | —            | No       | Upper bound of the overload slack: `thermal` (default: `min(hard − normal, max_overload_ratio × normal)` — on DLR lines the IEEE 738 emergency minus normal rating; ratio alone without a finite hard cap), `ratio` (`max_overload_ratio × normal`) or `unbounded`. Unset inherits `model_options.overload_slack_bound` |
| `max_overload_ratio` | number            | —            | No       | Overload headroom as a fraction of the normal threshold (default 1.5: slack ≤ +150 %, flow ≤ 2.5 × normal). Unset inherits `model_options.max_overload_ratio` |
| `in_service`       | integer\|array\|string| —          | No       | Per-(stage, block) in-service flag (PLEXOS `Units`): `0` opens the line for that block — no flow column, loss, balance or KVL contribution. Default: in service |
| `enforce_level`    | integer             | —            | No       | Legacy PLEXOS `Enforce Limits` (0/1/2). Kept for backward compatibility; a no-op — the flow cap is always enforced (express soft limits with `tmax_normal_*` + `overload_penalty`) |
| `dlr`              | object              | —            | No       | Dynamic line rating through critical spans — see [Dynamic line rating](#dynamic-line-rating-dlr) |
| `tcost`            | number\|array\|string| $/MWh       | No       | Variable transmission cost |
| `capacity`         | number\|array\|string| MW          | No       | Installed capacity |
| `expcap`           | number\|array\|string| MW          | No       | Capacity added per expansion module |
| `expmod`           | number\|array\|string| —           | No       | Maximum number of expansion modules |
| `capmax`           | number\|array\|string| MW          | No       | Absolute maximum capacity |
| `annual_capcost`   | number\|array\|string| $/MW-year   | No       | Annualized investment cost |
| `annual_derating`  | number\|array\|string| p.u./year   | No       | Annual capacity derating factor |
| `integer_expmod`   | boolean             | —            | No       | Build whole expansion modules only (integer `expmod`, MIP) |

#### Line limits

Every limit field is **optional**. An unset field gives no limit; it is
never read as 0. In each block and direction the line limit is the **min
over the fields that are set**. With none set, the line is bounded only by
`capacity` (unbounded without one), as before.

- **Directions.** An unset B→A field does **not** copy the A→B value; this
  is the same convention as `tmax_ba`. Set both directions when the limit
  is symmetric.
- **Signs (PLEXOS).** `min_flow`, `min_rating`, `overload_min_rating` and
  `outage_min_rating` are signed, as in PLEXOS: `min_rating = -490` limits
  the B→A flow to 490 MW. Validation rejects a positive `min_*` value and a
  negative `max_*` value. The `_ab` / `_ba` fields are magnitudes (≥ 0).
- **Unset cells.** In a schedule, a NaN cell (or a missing value in a
  file) means "unset in this block". An explicit `0` is a real limit of
  0 MW.

**PLEXOS ↔ gtopt.**

| PLEXOS (Line / Transformer) | gtopt field | Notes |
|-----------------------------|-------------|-------|
| Max Flow / Min Flow | `max_flow` / `min_flow` | Aliases of `tmax_ab` / `-tmax_ba` |
| Max Rating / Min Rating (Transformer: Rating) | `max_rating` / `min_rating` | Normal rating |
| Overload Max Rating / Overload Min Rating (Transformer: Overload Rating) | `overload_max_rating` / `overload_min_rating` | Emergency rating, used when the band is priced |
| Outage Max Rating / Outage Min Rating | `outage_max_rating` / `outage_min_rating` | Applies where `units_out > 0` |
| Units Out | `units_out` | Maintenance flag per block |
| Limit Penalty | `limit_penalty` | Alias of `overload_penalty` |
| Units | `in_service` | `0` opens the line |

**Limit causes.** A limit also has a **cause**, and the cause decides
whether a dynamic line rating may replace it. The CEN ERST study gives
each Chilean transmission limit a cause:

| Part | Fields | ERST causes (examples) | DLR |
|------|--------|------------------------|-----|
| Conductor | `conductor_rating_ab` / `_ba` | Conductor (conductor heating) | Replaced by the DLR rating (default when a conductor rating is set) |
| Equipment | `equipment_rating_ab` / `_ba` | TTCC (current transformers), CCSS (series capacitors), wave traps, breakers | Never weather-scaled |
| Security | `security_limit_ab` / `_ba` | Estabilidad de Tensión (voltage stability), angle stability, N-1 transfer limits | Never weather-scaled |

A converter that knows the cause writes the classified field. One that
does not writes `max_rating` (or `max_flow`). The fields combine freely,
because the limit is always the min over the set fields.

**One bound per direction, duals per part.** Every part bounds the same
loaded quantity, set by `line_thermal_limit_mode`: `|f|`, `|f| + a·ℓ` or
`|f| + ℓ`. The min is carried by one flow-column bound, one
`thermal{p,n}` row, or the DLR span row or bound. No row is added per
part. The binding part is found by comparing values; on a tie the order
is conductor (the DLR included), rating, outage, equipment, security,
flow. The dual of that bound or row is written as
`Line/<part>{p,n}_dual`: `conductor`, `rating`, `outage`, `equipment`,
`security` or `flowlimit` (for example `securityp_dual`). These outputs
are written only for lines that have a rating, outage or classified
field. `thermal{p,n}_dual` and `span_thermal_dual` are unchanged. On
these lines the loss segments are spread over the stage maximum of the
limit, and the block limit is then applied as a bound or row RHS (as for a
DLR rating). This keeps the segment widths from tying with the limit, a
tie that would split the dual arbitrarily.

- **Expansion.** On an expandable line, the installed capacity (the
  capacity rows) is the equipment that the investment adds. A DLR rating
  never replaces it.
- **PAMPL.** Every field is a `line.<field>` parameter (for example
  `line.max_rating`, `line.security_limit_ba`); the signed `min_*` fields
  are returned as given. `line.tmax_ba` also resolves `min_flow`.
- **Interfaces (follow-up).** PLEXOS `Interface` limits span several
  circuits, for example NPA–Polpaico at 2000 MW over two circuits. They
  are not modelled as line fields. Write them today as a user constraint
  on the sum of the circuit flows.

#### Dynamic line rating (DLR)

`dlr` limits the line flow, per block, by the thermal rating of its
**critical spans**: the spans whose conductor temperature binds first
(Matus et al. 2012, DOI [10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254); Open access: [ResearchGate](https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating), [PDF (Universidad de Chile)](https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)). One representative
span is valid and common. Choosing the spans is a pre-processing step
(Python tooling); gtopt only consumes the list.

For each span and block gtopt evaluates the IEEE Std 738 steady-state heat
balance at the maximum conductor temperature from that block's weather,
converts the ampacity to MW and limits the flow by the minimum over the
spans (and the static `tmax`). Weather is exogenous data, so the rating is a
per-block right-hand side: the LP stays linear. See formulation §5.5
*Dynamic Line Rating*.

```json
"dlr": {
  "conductor": {"diameter": 0.02814, "r25": 7.283e-5, "r75": 8.688e-5,
                "emissivity": 0.8, "absorptivity": 0.8, "subconductors": 1},
  "tmax_conductor": 75,
  "tmax_conductor_emergency": 100,
  "voltage": 220,
  "span_mode": "min",
  "critical_spans": [
    {"uid": 101, "name": "s1", "azimuth": 35, "elevation": 420,
     "ambient_temperature": "dlr_ambient_temperature",
     "wind_speed": "dlr_wind_speed", "wind_direction": "dlr_wind_direction",
     "solar_irradiance": "dlr_solar_irradiance"},
    {"uid": 102, "name": "s2", "rating": "dlr_rating"}
  ]
}
```

| Field (`dlr.*`)             | Type    | Units | Default | Description |
|-----------------------------|---------|-------|---------|-------------|
| `conductor.diameter`        | number  | m     | —       | Conductor outside diameter (required for IEEE 738) |
| `conductor.r25`, `conductor.r75` | number | Ω/m | —     | AC resistance at 25 °C and 75 °C; `R(T)` is linear between them and extrapolated beyond |
| `conductor.r20`, `conductor.alpha_r`, `conductor.ac_factor` | number | Ω/m, 1/K, — | —, —, 1 | Alternative to `r25`/`r75` (give one form, not both): `R(T) = ac_factor · r20 · (1 + alpha_r·(T − 20))` |
| `conductor.emissivity`      | number  | —     | 0.8     | Emissivity ε ∈ [0, 1] |
| `conductor.absorptivity`    | number  | —     | 0.8     | Solar absorptivity α ∈ [0, 1] |
| `conductor.subconductors`   | integer | —     | 1       | Conductors per phase bundle (multiplies the rating) |
| `tmax_conductor`            | number  | °C    | 75      | Normal maximum conductor temperature |
| `tmax_conductor_emergency`  | number  | °C    | —       | Emergency temperature. When set (and `overload_penalty > 0` in the block) the emergency rating is the hard cap and the normal rating the soft `overload_penalty` threshold; the overload slack is bounded by `min(emergency − normal, max_overload_ratio × normal)` under the default `overload_slack_bound = thermal`. Emergency tiers are owner-defined in practice (long-term emergency 4–12 h, short-term ~15 min; ISO-NE PP7, NYISO) |
| `voltage`                   | number  | kV    | `Line.voltage` | Voltage of the A→MW conversion (must be in kV) |
| `v_pu`                      | number  | p.u.  | 1       | Operating voltage |
| `power_factor`              | number  | —     | 1       | Power factor (0, 1] |
| `safety_factor`             | number  | —     | 1       | η, multiplies the IEEE 738 rating (1 = off) |
| `span_mode`                 | string  | —     | `min`   | `min`: effective rating = min over spans; `rows`: one row pair per non-dominated span |
| `use_emergency`             | boolean | —     | `true`  | `false` drops the emergency tier (`tmax_conductor_emergency`, `rating_emergency`): the normal rating is the hard cap |
| `low_wind_speed`            | number  | m/s   | 0.5     | Below this speed the wind direction is ignored and `low_wind_angle` is used (`0` disables the rule) |
| `low_wind_angle`            | number  | deg   | 45      | Attack angle of the low-wind rule, in [0, 90] |
| `solar_mode`                | string  | —     | `ghi`   | `ghi`: `solar_irradiance` is the global horizontal irradiance used directly as absorbed flux, `q_s = α·S·D` (conservative; same convention as the Python critical-span tooling); `none`: `q_s = 0` and `solar_irradiance` becomes optional |
| `limit_mode`                | string  | —     | `model_options.dlr_limit_mode`; unset: `replace` with `conductor_rating_*`, else `min` | `replace`: the DLR rating replaces `conductor_rating_*` (without one: `max_rating` / `min_rating`) and `tmax_normal_*`; `min`: `min(static, rating)` in every block. Flow, outage, equipment and security limits apply in both |
| `critical_spans`            | array   | —     | —       | One or more spans (non-empty) |

| Field (`critical_spans[k].*`) | Type | Units | Default | Description |
|-------------------------------|------|-------|---------|-------------|
| `uid`                         | integer | — | `1000·line.uid + k` (k 1-based) | Span uid; keys the weather files and the per-span outputs, so it must be unique across the case |
| `name`                        | string | — | `<line>_s<k>` | Span name |
| `azimuth`                     | number | deg | 0 | Bearing of the span axis |
| `elevation`                   | number | m | 0 | Altitude above sea level (air density) |
| `ambient_temperature`         | number\|array\|string | °C | — | Per-(scenario, stage, block) ambient temperature |
| `wind_speed`                  | number\|array\|string | m/s | — | Wind speed (below `low_wind_speed`, default 0.5 m/s, the attack angle is `low_wind_angle`, default 45°) |
| `wind_direction`              | number\|array\|string | deg | — | Wind bearing; attack angle `φ = \|((dir − azimuth + 90) mod 180) − 90\|` |
| `solar_irradiance`            | number\|array\|string | W/m² | — | Global horizontal irradiance (GHI), used as absorbed flux `q_s = α·S·D` (conservative; no sun-geometry model). Not needed under `solar_mode = "none"` |
| `tmax_conductor`, `tmax_conductor_emergency` | number | °C | `dlr.*` | Per-span overrides |
| `rating`                      | number\|array\|string | MW | — | Precomputed normal rating; bypasses IEEE 738 and the weather fields (used verbatim — no η, `v_pu` or power factor) |
| `rating_emergency`            | number\|array\|string | MW | — | Precomputed emergency rating (requires `rating`) |

The weather fields and `rating` are STB schedules: a scalar, a 3-D inline
array `[scenario][stage][block]`, or a file name resolved as
`input/Line/<name>.parquet` (or `.csv`) in **long** layout with columns
`scenario, stage, block, uid, value`, where `uid` is the **span** uid. One
file per field can hold every span of the case.

**References.** The critical-span approach follows Matus et al.,
"Identification of Critical Spans for Monitoring Systems in Dynamic
Thermal Rating", *IEEE Trans. Power Delivery* 27(2):1002–1009, 2012,
DOI [10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254);
Open access: [ResearchGate](https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating), [PDF (Universidad de Chile)](https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)
(gtopt consumes the span list; selecting the spans is done outside
gtopt).  The ampacity follows the steady-state heat balance of IEEE Std
738-2012.  See the formulation refs [9], [38].

**Precomputed ratings are verbatim.** A span `rating` / `rating_emergency`
is used exactly as given (MW): `safety_factor` (η), `v_pu`,
`power_factor`, `subconductors` and `voltage` apply only to ratings
computed from weather with IEEE 738.  Fold any margin into the schedule
itself.

**SDDP apertures.** The DLR rating belongs to the scene (scenario): every
backward-pass aperture of a phase reuses its scene's per-block ratings.
Apertures represent hydrology; DLR weather does not vary per aperture.

**Loss envelope.** The loss PWL is never driven by the per-block DLR rating:
it keeps the static `tmax` (or `capacity`), or `loss_envelope` when set.
With neither `tmax` nor `capacity` the stage envelope `1.05 · max_b T̄_b`
stands in as the static cap and loss range. On lines that cannot be expanded, the range under `limit_mode = "replace"`
is the larger of the static limit maximum and that envelope. The static
limit maximum covers every set field, with the overload rating where one is
given.

**DLR vs static limits (`limit_mode`).** The DLR rating *is* the
conductor's thermal capacity. It replaces the conductor rating, but never
another part (see [Line limits](#line-limits)):

- **Line with `conductor_rating_*`.** The default is `replace`. The DLR
  rating replaces the conductor rating, and the limit is the min of the
  DLR rating and the other set fields (equipment, security, rating,
  outage, flow). You no longer need to inflate `tmax` as a guard.
- **Line without a conductor rating.** The default is `min`:
  `min(static, DLR)`, which is the original `min(tmax, DLR)`. An explicit
  `replace` (`dlr.limit_mode` or `model_options.dlr_limit_mode`) replaces
  `max_rating` / `min_rating` instead. `max_flow` / `tmax` is never
  replaced.
- **Maintenance derate.** Use `outage_max_rating` with `units_out`. It
  always applies, so `min(DLR, outage)` holds in the maintenance blocks.
  `in_service = 0` opens the line.

Under `replace` the static `tmax_normal_*` (a thermal rating) is ignored.
When the emergency tier is active, the DLR normal rating sets the overload
band. On expandable lines the installed-capacity rows always stay in
place. When the DLR rating exceeds the static limit, the loss PWL is
stretched to the DLR envelope.

**Thermal-limit mode.** The DLR rating is a thermal limit like `tmax` and
follows the same `line_thermal_limit_mode` (per line, else
`model_options.line_thermal_limit_mode`), in both directions, both span modes,
the emergency tier and precomputed `rating`s: `flow` caps `|f| ≤ T` (no extra
row: `span_mode = min` only tightens the flow-column bound), `sending` caps
`|f| + a·ℓ ≤ T` (`a` = sending-bus loss share) and `full` caps
`|f| + ℓ ≤ T` (`min` lowers the RHS of the existing `thermalp` / `thermaln`
rows, no extra row). `line_thermal_limit_mode = flow` is the flow-only DLR.
To audit a run, take `ℓ` from `Line/loss_sol`, which is already the line's
total loss (`lossp + lossn`); `Line/lossn_sol` is only its B→A part, so
adding it to `loss_sol` counts a reverse-direction loss twice.

**Outputs** (`Line/…`): `dlr_rating_sol` (effective normal rating, A→B, the
remaining static cap included: a security or equipment limit, an outage
rating, or the static rating under `limit_mode = "min"`), `dlr_rating_emergency_sol` (emergency tier),
`critical_span_sol` (uid of the binding span; `-1` when a static cap is
lower) and `span_thermal_dual` (per-span rating dual `∂obj/∂T_k`, keyed by the
**span** uid; same unfolding as the other `*_dual` outputs).

**Validation** rejects negative or out-of-range parameters, an empty
`critical_spans`, a span with neither the four weather fields nor `rating`,
weather without `conductor` or a kV voltage, inline arrays whose shape is not
`[scenario][stage][block]`, duplicate span uids, and an unknown `span_mode`; it
warns when the line is out of service (`in_service = 0`) in every block.

### 3.5 Battery

A battery energy storage system (BESS).

#### Standalone battery (unified definition)

When the optional `bus` field is set (without `source_generator`), the system
auto-generates a discharge Generator, a charge Demand, and a linking Converter
during preprocessing. Both charge and discharge connect to the same external
bus. Only a single Battery element is needed — no separate Converter,
Generator, or Demand definitions required.

```json
{
  "uid": 1, "name": "bess1",
  "bus": 3,
  "input_efficiency": 0.95, "output_efficiency": 0.95,
  "emin": 0, "emax": 200,
  "pmax_charge": 60, "pmax_discharge": 60,
  "gcost": 0,
  "capacity": 200
}
```

#### Generation-coupled battery (hybrid / behind-the-meter)

When both `bus` and `source_generator` are set, the battery operates in
*generation-coupled* mode: the referenced generator directly charges the
battery through an auto-created internal bus. This models a solar or wind
plant that sits "behind" the battery (e.g. a DC-coupled or AC-coupled
hybrid system).

`System::expand_batteries()` will:
- Create an internal bus (name = `<battery.name>_int_bus`)
- Connect the **discharge Generator** to the external `bus`
- Connect the **charge Demand** to the internal bus
- Rewire the **source generator** to the internal bus

The `source_generator` value is the UID or name of a Generator element
already defined in `generator_array`. Its own `bus` field is overwritten
with the internal bus.

```json
{
  "uid": 1, "name": "bess1",
  "bus": 3,
  "source_generator": "solar1",
  "input_efficiency": 0.95, "output_efficiency": 0.95,
  "emin": 0, "emax": 200,
  "pmax_charge": 60, "pmax_discharge": 60,
  "gcost": 0,
  "capacity": 200
}
```

#### Traditional definition

Without the `bus` field, a separate Converter, Generator, and Demand must
be defined manually (see §3.6 Converter).

| Field               | Type                | Units        | Required | Description |
|---------------------|---------------------|--------------|----------|-------------|
| `uid`               | integer             | —            | Yes      | Unique identifier |
| `name`              | string              | —            | Yes      | Battery name |
| `active`            | boolean             | —            | No       | Whether the battery is active; a per-stage vector on a state-carrying battery must be one contiguous active period (see [State-carrying storage activation](#state-carrying-storage-activation)) |
| `bus`               | integer\|string     | —            | No       | Bus connection (enables unified / coupled definition) |
| `source_generator`  | integer\|string     | —            | No       | Source generator for generation-coupled mode (battery name or UID) |
| `input_efficiency`  | number\|array\|string| p.u.        | No       | Charging efficiency |
| `output_efficiency` | number\|array\|string| p.u.        | No       | Discharging efficiency |
| `annual_standing_loss`       | number\|array\|string| p.u./year   | No       | Annual self-discharge rate (linear: hourly drain = value/8760). **Default when absent: 0.48 (4 %/month)** — see [Battery self-discharge default](#battery-self-discharge-default) |
| `emin`              | number\|array\|string| MWh         | No       | Minimum state of charge |
| `emax`              | number\|array\|string| MWh         | No       | Maximum state of charge |
| `ecost`             | number\|array\|string| $/MWh       | No       | Storage usage cost (penalty) |
| `eini`              | number              | MWh          | No       | Initial state of charge |
| `efin`              | number              | MWh          | No       | Terminal state of charge |
| `efin_cost`         | number              | $/MWh        | No       | Penalty per MWh of `efin` shortfall; makes `efin` soft (see the reservoir note on [terminal targets and SDDP apertures](#terminal-targets-and-sddp-apertures)) |
| `pmax_charge`       | number\|array\|string| MW          | No       | Max charging power (unified definition) |
| `pmax_discharge`    | number\|array\|string| MW          | No       | Max discharging power (unified definition) |
| `pmin_charge`       | number\|array\|string| MW          | No       | Min charging power: hard floor every block, or the when-charging floor with `commitment` |
| `pmin_discharge`    | number\|array\|string| MW          | No       | Min discharging power: hard floor every block (`Generator.pmin`), or the when-discharging floor (`Commitment.pmin`) with `commitment` |
| `commitment`        | boolean             | —            | No       | Gate `pmin_charge` / `pmin_discharge` with per-block charge / discharge mode binaries (MIP). See formulation §5.8 *Charge / Discharge Rate Floors* |
| `exclusive_charge_discharge` | boolean    | —            | No       | With `commitment`: charge/discharge exclusivity constraint `u_charge + u_discharge ≤ 1` per block. **Default when absent: `true`**; `false` allows both modes in one block. Ignored without `commitment` (warning if `true`) |
| `gcost`             | number\|array\|string| $/MWh       | No       | Discharge generation cost (unified definition) |
| `capacity`          | number\|array\|string| MWh         | No       | Installed energy capacity |
| `expcap`            | number\|array\|string| MWh         | No       | Energy capacity per expansion module |
| `expmod`            | number\|array\|string| —           | No       | Maximum number of expansion modules |
| `capmax`            | number\|array\|string| MWh         | No       | Absolute maximum energy capacity |
| `annual_capcost`    | number\|array\|string| $/MWh-year  | No       | Annualized investment cost |
| `annual_derating`   | number\|array\|string| p.u./year   | No       | Annual capacity derating factor |
| `power_capacity`    | number\|array\|string| MW          | No       | Installed power (charge + discharge per block ≤ installed power) |
| `power_expcap`      | number\|array\|string| MW          | No       | Power per expansion module |
| `power_expmod`      | number\|array\|string| —           | No       | Maximum number of power modules |
| `power_capmax`      | number\|array\|string| MW          | No       | Absolute maximum power. Set `pmax_charge` / `pmax_discharge` at least this high on a candidate battery |
| `power_annual_capcost` | number\|array\|string| $/MW-year | No     | Annualized investment cost of power |
| `power_annual_derating` | number\|array\|string| p.u./year | No    | Annual power derating factor |
| `energy_power_ratio` | number             | h            | No       | Fixed storage duration: installed energy = ratio × installed power, so both axes are built together. Unset = energy and power expand independently |
| `integer_expmod`    | boolean             | —            | No       | Build whole expansion modules only (MIP) |
| `max_cycles_day`    | number              | cycles/day   | No       | Hard daily throughput limit `Σ discharge·Δt ≤ N · capacity` per day (PLEXOS *Max Cycles Day*); a constraint, not a cost |
| `type`              | string              | —            | No       | Optional battery type tag (metadata, e.g. `"lithium"`, `"flow"`) |
| `soft_emin`         | number\|array\|string| MWh         | No       | Soft minimum energy per stage (penalized slack below `emin`) |
| `soft_emin_cost`    | number\|array\|string| $/MWh       | No       | Penalty cost per unit of `soft_emin` slack violation |
| `use_state_variable`| boolean             | —            | No       | Enable stage/phase coupling of the battery state variable |
| `daily_cycle`       | boolean             | —            | No       | Enforce daily cycling (initial SoC equals final SoC each day) |
| `reserve_sustain_hours` | number          | h            | No       | Native BESS reserve rows: two-sided power headroom and SoC sufficiency for $T$ hours (formulation §5.9) |
| `reserve_discharge_credit` | boolean      | —            | No       | Credit discharge in the down-reserve headroom (default false) |
| `reserve_energy_usage_up` / `reserve_energy_usage_down` | number | MWh/MW/h | No | Expected reserve deployment energy charged to the SoC (replaces the CEN AUX `CF_*_COMP`; no bus term) |
| `storage_integration` | string            | —            | No       | Point-stage storage integration of this battery (overrides `model_options.battery_storage_integration`; see `storage_integration`) |

#### File-backed battery schedules

Any battery schedule may name a long-layout table instead of an inline
value, e.g. `"active": "act"` reads `<input_dir>/Battery/act.parquet`
(or `.csv`) rows `stage, uid, value` for the battery's own `uid`.  A unified
battery (one with `bus`) is expanded into `<name>_gen`, `<name>_dem`,
`<name>_conv` and `uc_<name>_gen`; the schedules they inherit (`active`,
`pmax_discharge`, `discharge_cost`, `pmax_charge`, `pmin_charge`,
`charge_cost`, `pmin_discharge`) are rewritten to the reference
`"Battery@<field>#<battery uid>"`, so every synthesized element reads the
**battery's** rows rather than its own class directory and uid.  The
general file-reference grammar is `"<field>"`, `"<Class>@<field>"` and
`"<Class>@<field>#<uid>"` (the last reads the rows of element `<uid>`).
A file-backed `charge_cost` must still hold the **negated** value (it
lands on the charge demand's `fcost`, as for an inline cost the expansion
negates it).

#### Unserved-demand cost and hard demand

Every demand is **soft** by default: served load may fall below `lmax`
and each unserved MWh costs the demand's `fcost` or, when `fcost` is
unset, the global `model_options.demand_fail_cost`, which defaults to
**1000 $/MWh** when it is itself unset.

| Want | Set |
|------|-----|
| Value of lost load for all demands | `model_options.demand_fail_cost` (default 1000) |
| Different cost for one demand | `Demand.fcost` (scalar, per-(stage, block) array, or file) |
| Freely dispatchable load (no penalty, e.g. a charging sink or the demand linked to a `Pump`) | `Demand.fcost = 0` (or the global `demand_fail_cost = 0`) |
| Hard, non-sheddable demand | `Demand.forced = true` (load fixed at `lmax`; the LP is infeasible if it cannot be served) |

An explicit `demand_fail_cost: 0` keeps its literal meaning (zero
penalty, so unserved load is free) and gtopt warns when no demand carries
its own `fcost`/`ecost` either.  Before the 1000 $/MWh default existed,
leaving `demand_fail_cost` unset behaved like `0`; cases that relied on
that must now set it to `0` explicitly.  Converters always write an
explicit value (e.g. `plp2gtopt` writes `0` because PLP prices
curtailment per demand through its falla units).

#### State-carrying storage activation

A storage element that carries a state variable across stages / phases
(`use_state_variable` true and `daily_cycle` false: reservoirs, LNG
terminals, H2 / thermal / ammonia storage, volume rights by default; a
battery only when set explicitly) may be **commissioned** once
(inactive -> active) and **retired** once (active -> inactive): its
per-stage `active` vector must read `off* on* off*`.  At retirement the
state is simply dropped (no link into the inactive stages or phases; an
`efin` target in an inactive last stage does not apply).  Switching it
back on after a gap (on -> off -> on) is rejected by validation at the
stage where it reactivates, because the dropped state would re-enter
unlinked.  For a cyclic store set `use_state_variable: false` or
`daily_cycle: true`, or model the two periods as two elements.

#### Battery self-discharge default

A battery whose `annual_standing_loss` is **absent** self-discharges at
**0.48 p.u./year = 4 %/month** (`0.04 × 12`).  gtopt uses the linear
convention
`SoC[b] = SoC[b−1] · (1 − annual_standing_loss/8760 · Δt_b) + …`, so the
hourly drain is `0.48/8760 ≈ 5.48e-5` and a single 730 h block loses
exactly 4 %.

The value is a conservative lithium-ion cell + BMS figure:

- Battery University, "BU-802b: What does Elevated Self-discharge Do?"
  (<https://batteryuniversity.com/article/elevating-self-discharge>):
  Li-ion ≈ 5 % in the first 24 h, then 1–2 %/month, plus ≈ 3 %/month for
  the protection circuit; roughly doubles per +10 °C.
- Wikipedia, "Self-discharge"
  (<https://en.wikipedia.org/wiki/Self-discharge>): Li-ion 2–3 %/month.

The default also penalises the LP degeneracy that lets a lossless battery
cycle (charge/discharge zig-zag across blocks) at zero cost — but only
when the replaced energy has a positive price.  If the charging window is
priced at exactly 0 $/MWh (free curtailed solar), zig-zag schedules still
tie; give the zero-cost unit its physical VOM (even 0.5–2 $/MWh) or set a
datasheet degradation cost in the battery's `discharge_cost` to break it.
Same-block simultaneous charge + discharge is limited by the
**power envelope** `p_charge/pmax_charge + p_discharge/pmax_discharge ≤ 1`
(on by default, `model_options.battery_power_envelope`; partial
simultaneous operation inside it is still LP-feasible).

Rules:

- An explicit `annual_standing_loss` (including `0`) is always respected.
- `model_options.battery_annual_standing_loss` changes the default for the whole
  case; `0` restores the legacy lossless behaviour
  (`--set model_options.battery_annual_standing_loss=0`).
- Only `Battery` is affected — `Reservoir`, `LngTerminal`, and the other
  storages keep `annual_standing_loss = 0` when unset.  Converters write an
  explicit value for non-lithium storage emitted as a Battery (pumped
  hydro → `0`), and plp2gtopt converts a nonzero PLP `mloss` [%/month] to
  `−12·ln(1 − mloss/100)`.
- gtopt logs once (INFO) how many batteries took the default.
- **Deprecated alias**: the previous key `annual_loss` (on every storage
  element) and `model_options.battery_annual_loss` are still accepted
  and rewritten to `annual_standing_loss` /
  `battery_annual_standing_loss`, with a one-time deprecation warning.
  Setting both spellings on the same object is an error.

### 3.6 Converter

Links a battery to a discharge generator and a charge demand.

| Field              | Type                | Units           | Required | Description |
|--------------------|---------------------|-----------------|----------|-------------|
| `uid`              | integer             | —               | Yes      | Unique identifier |
| `name`             | string              | —               | Yes      | Converter name |
| `active`           | boolean             | —               | No       | Whether the converter is active |
| `battery`          | integer\|string     | —               | Yes      | Associated battery UID or name |
| `generator`        | integer\|string     | —               | Yes      | Discharge generator UID or name |
| `demand`           | integer\|string     | —               | Yes      | Charge demand UID or name |
| `conversion_rate`  | number\|array\|string| MW/(MWh/h)     | No       | Electrical output per unit stored energy withdrawn |
| `capacity`         | number\|array\|string| MW              | No       | Installed power capacity |
| `expcap`           | number\|array\|string| MW              | No       | Power capacity per expansion module |
| `expmod`           | number\|array\|string| —               | No       | Maximum number of expansion modules |
| `capmax`           | number\|array\|string| MW              | No       | Absolute maximum power capacity |
| `annual_capcost`   | number\|array\|string| $/MW-year       | No       | Annualized investment cost |
| `annual_derating`  | number\|array\|string| p.u./year       | No       | Annual capacity derating factor |
| `commitment`       | boolean             | —               | No       | Gate the charge demand / discharge generator bounds with per-block mode binaries (set from `Battery.commitment`) |
| `exclusive_charge_discharge` | boolean   | —               | No       | With `commitment`: add `u_charge + u_discharge ≤ 1` per block (row `charge_discharge_exclusive`). Default `true` |
| `energy_power_ratio` | number            | h               | No       | Fixed storage duration (set from `Battery.energy_power_ratio`) |

### 3.7 Junction

A hydraulic node in the water network.

| Field    | Type    | Units | Required | Description |
|----------|---------|-------|----------|-------------|
| `uid`    | integer | —     | Yes      | Unique identifier |
| `name`   | string  | —     | Yes      | Junction name |
| `active` | boolean | —     | No       | Whether the junction is active |
| `drain`  | boolean | —     | No       | If true, excess water can leave the system through a drain column |
| `drain_capacity` | number | m³/s | No | Upper bound on the drain flow (default +∞ = unbounded; PLP `VertMax`). Ignored unless `drain` is true |
| `drain_cost` | number | $/(m³/s·h) | No | Cost of the drained flow, weighted per block like `Waterway.fcost` (default 0 = free; PLP `CVert`). Ignored unless `drain` is true |

### 3.8 Waterway

A water channel connecting two junctions.

| Field        | Type                | Units  | Required | Description |
|--------------|---------------------|--------|----------|-------------|
| `uid`        | integer             | —      | Yes      | Unique identifier |
| `name`       | string              | —      | Yes      | Waterway name |
| `active`     | boolean             | —      | No       | Whether the waterway is active |
| `junction_a` | integer\|string     | —      | Yes      | Upstream junction UID or name |
| `junction_b` | integer\|string     | —      | Yes      | Downstream junction UID or name |
| `capacity`   | number\|array\|string| m³/s  | No       | Maximum flow capacity |
| `lossfactor` | number\|array\|string| p.u.  | No       | Transit loss coefficient |
| `fmin`       | number\|array\|string| m³/s  | No       | Minimum required water flow |
| `fmin_fcost` | number\|array\|string| $/(m³/s·h) | No | Makes `fmin` soft (`flow + unserved ≥ fmin`, e.g. an ecological flow under drought); unset = hard |
| `fmax`       | number\|array\|string| m³/s  | No       | Maximum allowed water flow |

### 3.9 Reservoir

A water reservoir connected to a junction.  Volume units: **hm³** (1 hm³ = 10⁶ m³).

| Field                  | Type                | Units       | Required | Description |
|------------------------|---------------------|-------------|----------|-------------|
| `uid`                  | integer             | —           | Yes      | Unique identifier |
| `name`                 | string              | —           | Yes      | Reservoir name |
| `active`               | boolean             | —           | No       | Whether the reservoir is active; a per-stage vector on a state-carrying reservoir must be one contiguous active period (see [State-carrying storage activation](#state-carrying-storage-activation)) |
| `junction`             | integer\|string     | —           | Yes      | Associated junction UID or name |
| `spillway_capacity`    | number              | m³/s        | No       | Maximum uncontrolled spill capacity |
| `spillway_cost`        | number              | $/(m³/s·h) | No       | Cost per unit of spilled flow; setting it (0 = free) creates the spill column |
| `spill_junction`       | integer\|string     | —           | No       | Junction that receives the spilled water (unset = spill leaves the system) |
| `capacity`             | number\|array\|string| hm³       | No       | Total usable storage capacity |
| `annual_standing_loss`          | number\|array\|string| p.u./year  | No       | Annual evaporation/seepage loss rate |
| `emin`                 | number\|array\|string| hm³       | No       | Minimum allowed stored volume |
| `emax`                 | number\|array\|string| hm³       | No       | Maximum allowed stored volume |
| `ecost`                | number\|array\|string| $/hm³     | No       | Water value (shadow cost of stored water) |
| `eini`                 | number              | hm³        | No       | Initial stored volume |
| `efin`                 | number              | hm³        | No       | Target final stored volume |
| `efin_cost`            | number              | $/hm³      | No       | Penalty per hm³ of `efin` shortfall; makes `efin` soft (`vol_end + slack >= efin`, slack priced at `efin_cost`) |
| `fmin`                 | number              | m³/s        | No       | Minimum net inflow |
| `fmax`                 | number              | m³/s        | No       | Maximum net inflow |
| `flow_conversion_rate` | number              | hm³/(m³/s·h)| No     | Converts m³/s × hours to hm³ (default: 0.0036) |
| `scost`                | number\|array\|string| $/hm³     | No       | Short-run water shortage cost |
| `soft_emin`            | number\|array\|string| hm³       | No       | Soft minimum volume per stage (penalized slack below `emin`) |
| `soft_emin_cost`       | number\|array\|string| $/hm³     | No       | Penalty cost per unit of `soft_emin` slack violation |
| `use_state_variable`   | boolean             | —           | No       | Enable stage/phase coupling of the reservoir state variable |
| `daily_cycle`          | boolean             | —           | No       | Enforce daily cycling (initial volume equals final volume each day) |
| `storage_integration`  | string              | —           | No       | Point-stage storage integration of this reservoir (overrides `model_options.reservoir_storage_integration`; default `lg4_pos`) |

#### Terminal targets and SDDP apertures

A hard `efin` (no `efin_cost`) is a terminal constraint every SDDP
backward-pass aperture must be able to meet from the forward trial
state.  When a dry aperture cannot, it is dropped and the expected cut is
renormalised over the feasible apertures; if that repeats every iteration
the LB is over a rescaled (wetter) distribution and gtopt does not report
the run as converged (see
[SDDP §4.4, *Infeasible apertures*](methods/sddp.md#infeasible-apertures-drop-and-rescale)).
A soft target (`efin_cost`) restores feasibility, with two caveats: a
meaningful penalty may not be available (it must exceed the value of the
stored water to bind, yet it enters the objective and the cuts), and the
solution may then never reach `efin` — it pays the penalty instead, so
the target is silently unmet.  Check the `efin` slack in the output.

All reservoir costs are opt-in: gtopt adds none by default.  A positive
`spillway_cost` (or `ecost`, or a cost on every outflow path) makes the
water value negative, down to −cost, while the reservoir spills: one
more m³ in a full reservoir must be spilled at that cost.  Set them only
when the source model publishes the value; use `spillway_cost = 0` for a
free spillway.

### 3.10 Turbine

A hydro turbine linking a waterway to a generator.

| Field             | Type                | Units      | Required | Description |
|-------------------|---------------------|------------|----------|-------------|
| `uid`             | integer             | —          | Yes      | Unique identifier |
| `name`            | string              | —          | Yes      | Turbine name |
| `active`          | boolean             | —          | No       | Whether the turbine is active |
| `waterway`        | integer\|string     | —          | Yes      | Associated waterway UID or name |
| `generator`       | integer\|string     | —          | Yes      | Associated generator UID or name |
| `drain`           | boolean             | —          | No       | If true, turbine can spill water without generating |
| `conversion_rate` | number\|array\|string| MW·s/m³   | No       | Water-to-power conversion factor |
| `capacity`        | number\|array\|string| MW        | No       | Maximum turbine power output |
| `main_reservoir`  | integer\|string     | —          | No       | Reservoir whose volume drives the efficiency curve |

### 3.11 Flow (Inflow)

A water inflow or outflow at a junction.

| Field       | Type                | Units | Required | Description |
|-------------|---------------------|-------|----------|-------------|
| `uid`       | integer             | —     | Yes      | Unique identifier |
| `name`      | string              | —     | Yes      | Flow name |
| `active`    | boolean             | —     | No       | Whether the flow is active |
| `direction` | integer             | —     | No       | +1 = inflow, −1 = outflow |
| `junction`  | integer\|string     | —     | Yes      | Associated junction UID or name |
| `discharge` | number\|array\|string| m³/s | Yes      | Water discharge schedule |

### 3.12 Reservoir Seepage (formerly Filtration)

Piecewise-linear seepage model from a waterway to an adjacent reservoir,
representing water losses due to soil permeability (Darcy's law
approximation).  The seepage flow through the waterway is
constrained to:

```text
seepage [m³/s] = slope [m³/s/hm³] × avg_reservoir_volume [hm³] + constant [m³/s]
```

where `avg_reservoir_volume = (eini + efin) / 2`.  This captures the
hydrostatic-head dependence of seepage.  Corresponds to PLP **plpfilemb.dat**.

When piecewise-linear `segments` are provided, the active segment is
selected based on the reservoir's current volume (vini from the previous
phase).  The LP constraint coefficients (slope and RHS) are updated
dynamically — the same mechanism used by Reservoir Production Factor for
turbine conversion rates.  If `segments` is empty, the static `slope` and
`constant` fields are used as a simple linear model.

**JSON array name:** `reservoir_seepage_array`

| Field       | Type    | Units        | Required | Description |
|-------------|---------|--------------|----------|-------------|
| `uid`       | integer | —            | Yes      | Unique identifier |
| `name`      | string  | —            | Yes      | Seepage element name |
| `active`    | boolean | —            | No       | Whether the seepage element is active |
| `waterway`  | integer\|string | —    | Yes      | Source waterway UID or name |
| `reservoir` | integer\|string | —    | Yes      | Receiving reservoir UID or name |
| `slope`     | number  | m³/s / hm³   | No       | Default seepage slope (used when `segments` is empty) |
| `constant`  | number  | m³/s         | No       | Default constant seepage rate (used when `segments` is empty) |
| `segments`  | array   | —            | No       | Piecewise-linear concave segments (see below) |

Each segment in the `segments` array describes one piece of the concave
filtration envelope:

| Field      | Type   | Units          | Description |
|------------|--------|----------------|-------------|
| `volume`   | number | hm³            | Volume breakpoint |
| `slope`    | number | m³/s / hm³     | Seepage slope at this breakpoint |
| `constant` | number | m³/s           | Seepage rate at this breakpoint |

The seepage rate at volume *V* is computed as the minimum over all
segments: `seepage(V) = min_i { constant_i + slope_i × (V − volume_i) }`.
This is analogous to the piecewise-linear evaluation used in
[Reservoir Production Factor](#313-reservoir-production-factor).

**Example with segments:**

```json
{
  "uid": 1,
  "name": "filt1",
  "waterway": "w1_2",
  "reservoir": "r1",
  "slope": 0.001,
  "constant": 1.0,
  "segments": [
    { "volume": 0.0, "slope": 0.0003, "constant": 0.5 },
    { "volume": 5000.0, "slope": 0.0001, "constant": 2.0 }
  ]
}
```

### 3.13 Reservoir Production Factor

Piecewise-linear turbine efficiency as a function of reservoir volume
(hydraulic head).  Models the PLP "rendimiento" concept: the turbine
conversion rate varies with the current water level in the reservoir.
Corresponds to PLP **plpcenre.dat** (*Archivo de Rendimiento de Embalses*).

When the SDDP solver runs, it updates the turbine's conversion-rate LP
coefficient at each forward-pass iteration using the current reservoir
volume.  For the first iteration, the reservoir's `eini` value is used.
If the LP solver does not support in-place coefficient modification
(`supports_set_coeff()`), the static `conversion_rate` from the Turbine
element is used unchanged.

**JSON array name:** `reservoir_production_factor_array`

| Field                        | Type    | Units          | Required | Description |
|------------------------------|---------|----------------|----------|-------------|
| `uid`                        | integer | —              | Yes      | Unique identifier |
| `name`                       | string  | —              | Yes      | Production factor element name |
| `active`                     | boolean | —              | No       | Whether the element is active |
| `turbine`                    | integer\|string | —      | Yes      | Associated turbine UID or name |
| `reservoir`                  | integer\|string | —      | Yes      | Associated reservoir UID or name |
| `mean_production_factor`     | number  | MW·s/m³        | No       | Fallback production factor (default: 1.0) |
| `segments`                   | array   | —              | No       | Piecewise-linear concave segments |

Each segment in the `segments` array has the following fields:

| Field      | Type   | Units           | Description |
|------------|--------|-----------------|-------------|
| `volume`   | number | hm³             | Volume breakpoint |
| `slope`    | number | efficiency/hm³  | Slope at this breakpoint |
| `constant` | number | MW·s/m³         | Efficiency at this breakpoint (point-slope form) |

The efficiency is computed as the **minimum** over all segments:

```text
efficiency(V) = min_i { constant_i + slope_i × (V − volume_i) }
```

Segments should have slopes in **decreasing** order so the function forms
a concave envelope.  The result is clamped to zero (efficiency cannot be
negative).

**Example:**

```json
{
  "reservoir_production_factor_array": [
    {
      "uid": 1,
      "name": "eff_colbun",
      "turbine": "COLBUN",
      "reservoir": "COLBUN",
      "mean_production_factor": 1.53,
      "segments": [
        { "volume": 0.0, "slope": 0.0002294, "constant": 1.2558 },
        { "volume": 500.0, "slope": 0.0001, "constant": 1.53 }
      ]
    }
  ]
}
```

### 3.14 Reservoir Discharge Limit

Piecewise-linear volume-dependent discharge limit for reservoirs.  This is
a safety/environmental constraint that limits the hourly-average discharge
as a function of the reservoir volume (e.g. to prevent landslides or
excessive drawdown).

The constraint per stage is:

```text
qeh ≤ slope × V_avg + intercept
```

where `qeh` is the stage-average hourly discharge [m³/s], `V_avg` is the
average reservoir volume `(eini + efin) / 2` [hm³], and slope/constant are
from the active piecewise-linear segment.

Generalizes the PLP "Ralco" constraint (`plpralco.dat`).

**JSON array name:** `reservoir_discharge_limit_array`

| Field       | Type    | Units   | Required | Description |
|-------------|---------|---------|----------|-------------|
| `uid`       | integer | —       | Yes      | Unique identifier |
| `name`      | string  | —       | Yes      | Discharge limit element name |
| `active`    | boolean | —       | No       | Whether the element is active |
| `waterway`  | integer\|string | — | Yes   | Source waterway UID or name |
| `reservoir` | integer\|string | — | Yes   | Associated reservoir UID or name |
| `segments`  | array   | —       | No       | Piecewise-linear segments |

Each segment has:

| Field       | Type   | Units        | Description |
|-------------|--------|-------------|-------------|
| `volume`    | number | hm³         | Volume breakpoint |
| `slope`     | number | m³/s / hm³  | Discharge limit slope |
| `intercept` | number | m³/s        | Discharge limit intercept |

### 3.15 Generator Profile

A time-varying capacity-factor profile for a generator.

| Field       | Type                | Units | Required | Description |
|-------------|---------------------|-------|----------|-------------|
| `uid`       | integer             | —     | Yes      | Unique identifier |
| `name`      | string              | —     | Yes      | Profile name |
| `active`    | boolean             | —     | No       | Whether the profile is active |
| `generator` | integer\|string     | —     | Yes      | Associated generator UID or name |
| `profile`   | number\|array\|string| p.u. | Yes      | Capacity-factor profile (0–1) |
| `scost`     | number\|array\|string| $/MWh| No       | Short-run generation cost override |

### 3.16 Demand Profile

A time-varying load-shape profile for a demand element.

| Field    | Type                | Units | Required | Description |
|----------|---------------------|-------|----------|-------------|
| `uid`    | integer             | —     | Yes      | Unique identifier |
| `name`   | string              | —     | Yes      | Profile name |
| `active` | boolean             | —     | No       | Whether the profile is active |
| `demand` | integer\|string     | —     | Yes      | Associated demand UID or name |
| `profile`| number\|array\|string| p.u. | Yes      | Load-scaling profile (0–1) |
| `scost`  | number\|array\|string| $/MWh| No       | Short-run load-shedding cost override |

### 3.17 Reserve Zone

A spinning-reserve requirement zone.

| Field    | Type                | Units  | Required | Description |
|----------|---------------------|--------|----------|-------------|
| `uid`    | integer             | —      | Yes      | Unique identifier |
| `name`   | string              | —      | Yes      | Zone name |
| `active` | boolean             | —      | No       | Whether the zone is active |
| `urreq`  | number\|array\|string| MW    | No       | Up-reserve requirement |
| `drreq`  | number\|array\|string| MW    | No       | Down-reserve requirement |
| `urcost` | number\|array\|string| $/MW  | No       | Up-reserve shortage penalty |
| `drcost` | number\|array\|string| $/MW  | No       | Down-reserve shortage penalty |
| `urmin` / `drmin` | number\|array\|string| MW | No | Requirement floor (Min-Provision) |
| `urreq_planes` / `drreq_planes` | array of objects | — | No | Dynamic requirement $R \ge \max_k$ plane$_k$; each object has `alpha` [MW], `demand_coef` [MW/MW], `inertia_coef` [MW/MWs], `contingency_coef` [MW/MW], `flow_coef` [MW/MW], all scalar\|array\|filename schedules |
| `load_term` | string | — | No | `"exogenous"` (default: load is a parameter) or `"endogenous_generation"` (PLEXOS-compat, creates negative LMPs) |
| `system_load` | number\|array\|string | MW | No | Given plane load $D$ (overrides the demand sum) |
| `demand_load_factor` | number | p.u. | No | $D$ = factor × Σ demand lmax (default **1.05**: conservative, over-sizes rather than under-sizes the reserve; CEN PCP ratio 1.025–1.05; `endogenous_generation` default 1) |
| `demands` | array | — | No | Demands summed into $D$ (default: all but battery charge) |
| `inertia_zone` | integer\|string | — | No | InertiaZone whose provisions give the inertia term |
| `inertia_term` | string | — | No | Plane inertia $I$: `"commitment"` (default, CEN B.1: $\sum I_c u_c$ over the zone's provisions, $I_c = \mathrm{pf}\cdot\min$(`provision_max`, pmax); uncommitted units count only when forced on; with a relaxed Commitment it can price buses negative — `validate_planning` warns), `"exogenous"` (data: `system_inertia`, else the conservative must-run sum, with a warning; the remedy for that risk), `"endogenous"` (PLEXOS-compat $`\sum \mathrm{pf}\,r`$, $r \le p$; couples dispatch, can price buses negative).  Only the CPF planes — an InertiaZone's own minimum stays commitment-based |
| `system_inertia` | number\|array\|string | MWs | No | Given plane inertia $I$ for `inertia_term: "exogenous"` (missing blocks read 0, the most conservative value) |
| `contingency_generators` | array | — | No | Up contingency: $C \ge p_g$ for each listed generator |
| `contingency_demands` | array | — | No | Down contingency: $C \ge$ load of each listed demand (e.g. `bess_dem`) |
| `urcontingencies` / `drcontingencies` | array of objects | — | No | Contingency candidates (PLEXOS `*_Comparison`): `{name, generators, generator_coefs, demands, demand_coefs, lines, line_coefs, offset}`, $`C \ge \sum a\,x + \mathrm{offset}`$ (coefs default 1) |
| `requirement_sense` | string | — | No | `"ge"` (default) or `"eq"`: equality provision balance (PLEXOS `*MinProvision` Sense 0) |
| `ursurplus_cost` / `drsurplus_cost` | number\|array\|string | $/MW | No | Surplus penalty of an `"eq"` balance (no column when unset: provision pinned).  Ignored with planes: $R$ absorbs the excess (`validate_planning` warns) |
| `contingency_includes_reserve` | boolean | — | No | $C \ge a(p + r)$: the lost unit's provision into this zone counts (default false, as PLEXOS) |
| `flow_lines` | array | — | No | Lines whose signed flow sum is the `flow_coef` term.  **Couples dispatch into the requirement** (the term enters the LMP spread; `validate_planning` warns).  Decoupled alternative: an exogenous `alpha` schedule (expected flow or fixed MW contingency) |

Example (CEN CPF+@10s, two of the twelve planes; see
[formulation §5.9](formulation/mathematical-formulation.md)):

```json
{"uid": 1, "name": "cpf10s", "urcost": 5000,
 "urreq_planes": [
   {"alpha": 516.0, "demand_coef": -0.0366, "inertia_coef": -0.0123,
    "contingency_coef": 1.25},
   {"alpha": 219.16, "demand_coef": -0.02064, "inertia_coef": -0.003164,
    "contingency_coef": 0.928}],
 "demand_load_factor": 1.025, "inertia_zone": "sen",
 "contingency_generators": ["unit_a", "unit_b", "bess_gen"]}
```

Outputs (planes mode, `ReserveZone/`): `urequirement_sol`, the plane
duals `urplane<k>_dual`, `urplane_binding_sol`, `inertia_sol`, and the
native requirement credit `reserve_credit_sol` [$/MWh] (the LMP shift
$\ell \sum_k \delta^D_k \mu_k$ of the endogenous load term, the same at
every bus; 0 with the exogenous default), `credit_transfer_sol` [\$]
(credit × generated energy) and, with `flow_lines`, `flow_credit_sol`
(the extra LMP spread across the corridor).

A plane term whose support is missing (`inertia_coef` without an
inertia source, `contingency_coef` without candidates, `flow_coef`
without `flow_lines`) is an error under `constraint_mode` strict (both
in `validate_planning` and when the LP is built) and a warning in normal
mode, where the term is dropped; `contingency_coef` must be ≥ 0.  Every
dispatch-coupling channel is reported by `validate_planning` according to
`model_options.reserve_coupling` (`warn` default, `error`, `off`).

The load term is exogenous by default so that a marginal MWh of demand
never changes the requirement (no negative LMPs); see CEN Minuta
GM-DPRO Nº05-2026
(<https://www.coordinador.cl/wp-content/uploads/2026/06/Minuta_GM-DPRO_No05-2026_Modelacion_y_Asignacion_de_SSCC_CF-ISSCC2026.pdf>).

**Stepwise reserve demand curve (ORDC).**  There is no dedicated
demand-curve element: a priced zone (`urreq` + `urcost`) is one ORDC step
exactly — its requirement column $0 \le s \le$ `urreq` costs $-c$, i.e.
the step's value, and the zone adds $+c \cdot$ `urreq` as an objective
constant so the reported objective is the shortage penalty.  A concave
ORDC with steps (width $w_k$, price $c_k$, decreasing) is therefore one
zone per step, `urreq` $= w_k$, `urcost` $= c_k$, and one provision per
eligible unit and step, all with `shared_headroom`.  The objective equals
the welfare form ($\text{cost} - \sum_k c_k s_k$) plus the constant
$\sum_t \sum_k c_k w_k \Delta t$.  `sienna2gtopt reserves` maps
PowerSimulations' `StepwiseCostReserve` this way and is cross-checked
against it (`tools/test_sienna_vs_gtopt.py`).

### 3.18 Reserve Provision

A generator's contribution to reserve zones.

| Field                  | Type                | Units  | Required | Description |
|------------------------|---------------------|--------|----------|-------------|
| `uid`                  | integer             | —      | Yes      | Unique identifier |
| `name`                 | string              | —      | Yes      | Provision name |
| `active`               | boolean             | —      | No       | Whether the provision is active |
| `generator`            | integer\|string     | —      | Yes      | Associated generator UID or name |
| `reserve_zones`        | string              | —      | Yes      | Comma-separated reserve zone UIDs or names |
| `urmax`                | number\|array\|string| MW    | No       | Maximum up-reserve offer |
| `drmax`                | number\|array\|string| MW    | No       | Maximum down-reserve offer |
| `ur_capacity_factor`   | number\|array\|string| p.u.  | No       | Up-reserve capacity factor |
| `dr_capacity_factor`   | number\|array\|string| p.u.  | No       | Down-reserve capacity factor |
| `ur_provision_factor`  | number\|array\|string| p.u.  | No       | Up-reserve provision factor |
| `dr_provision_factor`  | number\|array\|string| p.u.  | No       | Down-reserve provision factor |
| `urcost`               | number\|array\|string| $/MW  | No       | Up-reserve bid cost |
| `drcost`               | number\|array\|string| $/MW  | No       | Down-reserve bid cost |
| `ur_zone_factors` / `dr_zone_factors` | array of numbers | p.u. | No | Per-zone effectiveness factor, parallel to `reserve_zones` (default 1) |
| `shared_headroom`      | boolean             | —      | No       | One headroom row shared by all flagged provisions of the generator |
| `offline`              | boolean             | —      | No       | Cold up reserve: no headroom row, $r \le$ urmax·(1−u) |
| `symmetric`            | boolean             | —      | No       | $r^{up} = r^{dn}$ |
| `urmax_response` / `drmax_response` | number\|array\|string | MW | No | Native BESS only: cap on the discharge-side part (PLEXOS Max Response) |
| `urmax_charge_response` / `drmax_charge_response` | number\|array\|string | MW | No | Native BESS only: cap on the charge-side part (PLEXOS Max Charge Response) |
| `ur_energy_usage` / `dr_energy_usage` | number\|array\|string | MWh/MW/h | No | Native BESS only: deployment energy of this provision charged to the SoC; overrides the Battery `reserve_energy_usage_up/down` |

---

### 3.18 bis Inertia Zone (commitment-dependent requirement)

Besides `uid`, `name`, `active`, `requirement` [MWs] and `cost`
[$/MWs], an `inertia_zone_array` entry accepts:

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `requirement_credits` | array of objects | — | No | Relief steps `{name, generators, units, credit, integer}`: the requirement drops by `credit` [MWs]·y with `units·y ≤ Σ u_g` over `generators`; `integer: true` = exact step (binary y), default continuous as PLEXOS |

Example (Norte Grande, 13 400 / 9 500 / 7 500 MWs for 0–1 / 2–3 / ≥ 4
Guacolda units):

```json
{"uid": 2, "name": "norte_grande", "requirement": 13400,
 "requirement_credits": [
   {"generators": ["GUACOLDA_1", "GUACOLDA_2", "GUACOLDA_3",
                   "GUACOLDA_4", "GUACOLDA_5"],
    "units": 2, "credit": 3900, "integer": true},
   {"generators": ["GUACOLDA_1", "GUACOLDA_2", "GUACOLDA_3",
                   "GUACOLDA_4", "GUACOLDA_5"],
    "units": 4, "credit": 2000, "integer": true}]}
```

---

### 3.19 User Constraint

User-defined linear constraints applied to the LP formulation.
See **[User Constraints](user-constraints.md)** for the full syntax
reference and examples.

| Field         | Type            | Unit  | Required | Description |
|---------------|-----------------|-------|----------|-------------|
| `uid`         | integer         | —     | Yes      | Unique identifier |
| `name`        | string          | —     | Yes      | Human-readable constraint name |
| `active`      | boolean         | —     | No       | Activation flag (default: true) |
| `expression`  | string          | —     | Yes      | Constraint expression in AMPL-inspired syntax |
| `description` | string          | —     | No       | Free text |
| `constraint_type` | string      | —     | No       | Scaling hint: `power` (default), `energy`, `raw`, `unitless` |
| `penalty`     | number          | $/unit | No      | When > 0 the constraint is soft: violations are paid at this price (weighted by probability, discount and block duration like every cost) |
| `penalty_class` | string        | —     | No       | Penalty unit: `raw` (default) or `hydro_flow` |
| `rhs`         | number\|array\|string | — | No    | Right-hand side per stage and block; replaces the number at the end of `expression` where set |
| `scope`       | string          | —     | No       | Row granularity: `block` (default), `stage`, `phase`, `global` |
| `active_stages` | array\|string | 0/1  | No       | Per-stage on/off (`[0, 1, 1]` or a file field); a stage with 0 gets no row. Combined with `active` |
| `daily_sum`   | boolean         | —     | No       | One row per 24 h day instead of one per block (with `constraint_type: energy` the terms are weighted by block duration) |
| `directive`   | object          | —     | No       | Typed constraint-family directive (`kind`, …); see [User Constraints](user-constraints.md) |
| `slack_name`  | string          | —     | No       | Name of the slack column of a soft constraint (LP labels only) |

**System-level fields:**

| Field                     | Type            | Description |
|---------------------------|-----------------|-------------|
| `user_constraint_array`   | array           | Inline array of UserConstraint objects |
| `user_constraint_file`    | string          | Path to external JSON file with constraint array |
| `user_constraint_files`   | array of string | Paths to multiple external JSON files with constraint arrays |

### 3.20 Flow Right

Water-right constraints on waterway flow (m³/s).  See
**[Irrigation Agreements](irrigation-agreements.md)** for the full reference.

**JSON array name:** `flow_right_array`

| Field        | Type            | Units | Required | Description |
|--------------|-----------------|-------|----------|-------------|
| `uid`        | integer         | —     | Yes      | Unique identifier |
| `name`       | string          | —     | Yes      | Flow right name |
| `active`     | boolean         | —     | No       | Activation flag (default: true) |
| `purpose`    | string          | —     | No       | Metadata tag (`irrigation`, `generation`, `environmental`, …) |
| `junction_a` | integer\|string | —     | No       | Source junction (the served flow is withdrawn there) |
| `direction`  | integer         | —     | No       | Sign in user-constraint flow balances: `+1` supply, `-1` withdrawal |
| `fmin`       | number\|array\|string | m³/s | No | Hard lower bound on the served flow (default 0) |
| `fmax`       | number\|array\|string | m³/s | No | Hard upper bound; unset = `target` when set, else `fmin` |
| `target`     | number\|array\|string | m³/s | No | Soft kink: below it `fcost` applies, above it `uvalue` (legacy alias `discharge`) |
| `fcost`      | number\|array\|string | $/m³/s·h | No | Penalty on the shortfall below `target`; unset = `model_options.hydro_spill_cost` on blocks with `target > 0` |
| `uvalue`     | number\|array\|string | $/m³/s·h | No | Value of flow above `target` (positive = benefit, negative = cost); formerly `use_value` |
| `flow_mode`  | string          | —     | No       | `per_block` (default), `stage_average` (kink and costs on the stage-average flow), `stage_uniform` (one flow for the whole stage). Legacy `use_average: true` = `stage_average` |
| `priority`   | number          | —     | No       | Allocation priority (metadata for ordering) |
| `bound_rule` | object          | —     | No       | Volume- or month-dependent bound, see [RightBoundRule](#right-bound-rule) |
| `junction_b` | integer\|string | —     | No       | Return junction (carry mode and/or the opt-in bypass) |
| `consumptive`| boolean         | —     | No       | `false` returns the served flow to `junction_b` (default: true) |
| `use_bypass` | boolean         | —     | No       | Opt-in pass-through arc `junction_a → junction_b` (default: off; implied by `bypass_cost`/`bypass_fmax`) |
| `bypass_cost`| number          | $/m³/s·h | No    | Cost on the bypass arc (default: 0) |
| `bypass_fmax`| number          | m³/s  | No       | Capacity of the bypass arc (default: unbounded) |

**Outputs** (`FlowRight/`): `flow_sol` / `flow_cost`, `fail_sol`,
`excess_sol`, `flow_kink_dual`, and — only when the bypass is built —
`bypass_sol` / `bypass_cost` (the pass-through flow, needed to close the
`junction_a` / `junction_b` balances from the output).

See [irrigation-agreements.md](irrigation-agreements.md) for details and examples.

### 3.21 Volume Right

Water-right ledgers in volume (hm³): a "dummy reservoir" that tracks the
accumulated entitlement, optionally carried between phases as an SDDP
state.  See **[Irrigation Agreements](irrigation-agreements.md)** for the
full reference.

**JSON array name:** `volume_right_array`

| Field        | Type            | Units | Required | Description |
|--------------|-----------------|-------|----------|-------------|
| `uid`        | integer         | —     | Yes      | Unique identifier |
| `name`       | string          | —     | Yes      | Volume right name |
| `active`     | boolean         | —     | No       | Activation flag (default: true) |
| `purpose`    | string          | —     | No       | Metadata tag (`irrigation`, `economy`, …) |
| `reservoir`  | integer\|string | —     | No       | Physical source reservoir: the right's extraction leaves its balance |
| `right_reservoir` | integer\|string | — | No     | Parent VolumeRight whose balance receives this right's flow (with `direction`); must appear earlier in the array |
| `direction`  | integer         | —     | No       | `+1` supply / `-1` withdrawal in the parent balance |
| `emin` / `emax` | number\|array\|string | hm³ | No | Bounds on the accumulated volume |
| `ecost`      | number\|array\|string | $/hm³ | No | Holding cost of the accumulated volume |
| `eini` / `efin` | number       | hm³   | No       | Initial volume / end-of-horizon minimum (`efin_cost` makes it soft) |
| `soft_emin` / `soft_emin_cost` | number\|array\|string | hm³, $/hm³ | No | Soft floor and its penalty |
| `demand`     | number\|array\|string | hm³/stage | No | Required delivery per stage; shortfall pays `fail_cost` |
| `fmax`       | number\|array\|string | m³/s | No | Maximum extraction rate |
| `fail_cost`  | number          | $/hm³ | No       | Penalty on unmet `demand` |
| `saving_rate`| number\|array\|string | m³/s | No | Economy rights: maximum rate at which unused rights become savings |
| `forfeit`    | boolean         | —     | No       | Free disposal of unused rights (keeps the state value ≥ 0); default `false` |
| `flow_conversion_rate` | number | hm³/(m³/s·h) | No | Default `0.0036` |
| `use_state_variable` | boolean | —   | No       | Carry the ledger between phases (SDDP state) |
| `annual_standing_loss` | number\|array\|string | p.u./year | No | Ledger decay |
| `reset_month` | string         | —     | No       | Calendar month at which the right is re-provisioned (to `reset_value`, else the `bound_rule` value, else `emax`) |
| `reset_monthly` | boolean      | —     | No       | Re-provision at every new calendar month (not with a `bound_rule` provision) |
| `reset_value` | number         | hm³   | No       | Explicit provision at reset (e.g. `0` for an up-counter) |
| `reset_debit_right` | integer\|string | — | No    | Right whose incoming volume is subtracted from this right's provision at reset |
| `reset_credit_right` | integer\|string | — | No   | Right whose remaining volume is credited at reset: provision = min(`emax`, own + credit) |
| `priority`   | number          | —     | No       | Allocation priority (metadata) |
| `bound_rule` | object          | —     | No       | See [RightBoundRule](#right-bound-rule) |

#### Right bound rule

`bound_rule` (on FlowRight and VolumeRight) is a piecewise-linear
function `bound = min(cap, max(floor, constant_k + slope_k · x))` over
segments `{volume, slope, constant}` sorted by their breakpoint
`volume`.  The input `x` depends on `axis`:

| Field      | Default            | Description |
|------------|--------------------|-------------|
| `axis`     | `reservoir_volume` | `reservoir_volume` (stage-start volume of `reservoir`) or `stage_month` (calendar month of the stage; needs stage months) |
| `reservoir`| —                  | Reservoir whose volume drives the rule (required for `reservoir_volume`) |
| `segments` | `[]`               | Breakpoints `{volume, slope, constant}`; empty = no effect |
| `cap`      | unset              | Upper cap of the computed bound |
| `floor`    | `0`                | Lower floor of the computed bound |

Rules are re-evaluated in `update_lp` each SDDP iteration from the
stage-start volume.  The agreements' coupling rows are user constraints,
usually loaded from PAMPL files that use named sets and `state(var)`
(see [User Constraints](user-constraints.md)).

See [irrigation-agreements.md](irrigation-agreements.md) for details and examples.

### 3.22 User Parameter

Named scalar parameters usable in user constraint expressions.

**JSON array name:** `user_param_array`

| Field   | Type    | Units | Required | Description |
|---------|---------|-------|----------|-------------|
| `uid`   | integer | —     | Yes      | Unique identifier |
| `name`  | string  | —     | Yes      | Parameter name (used in constraint expressions) |
| `value` | number\|array\|string | — | Yes | Parameter value (per block/stage/scenario schedule) |

### 3.23 Commitment

Unit commitment parameters for a generator.  Each entry links to exactly one
generator and enables binary on/off scheduling with startup/shutdown costs,
ramp constraints, and minimum up/down times on stages with
`chronological: true`.  On a non-chronological stage the same element
uses the per-block formulation (status `u` per block, `p ≤ Pmax·u`,
`p ≥ pmin·u`, no-load cost per block; startup / shutdown / ramp / min
up-down data ignored).  The
deprecated `simple_commitment_array` (`dispatch_pmin` → `pmin`) is read as
Commitments.

> **Full documentation**: See [Unit Commitment Guide](unit-commitment.md)
> for the mathematical formulation, worked examples, and advanced features.

**JSON array name:** `commitment_array`

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `uid` | integer | — | Yes | Unique identifier |
| `name` | string | — | No | Human-readable name |
| `active` | boolean | — | No | Whether this commitment is active (default: `true`) |
| `generator` | integer\|string | — | **Yes** | Foreign key to Generator (uid or name) |
| `startup_cost` | number\|array\|string | $/start | No | Cost per startup event |
| `shutdown_cost` | number\|array\|string | $/stop | No | Cost per shutdown event |
| `noload_cost` | number | $/hr | No | Fixed hourly cost when committed |
| `pmin` | number\|array\|string | MW | No | Minimum stable level while committed (`p ≥ pmin·u`), per stage and block. Unset: the generator's own lower bound. `Generator.pmin` stays an unconditional floor |
| `noload_fuel` | number | fuel unit/h | No | No-load fuel burn while committed (PLEXOS `Heat Rate Base`); counted in `Fuel` offtake and fuel-basis emissions (`noload_fuel · u · Δt`), not re-priced (cost stays in `noload_cost`) |
| `min_up_time` | number | hours | No | Minimum online duration after startup |
| `min_down_time` | number | hours | No | Minimum offline duration after shutdown |
| `ramp_up` | number | MW/hr | No | Normal ramp-up rate |
| `ramp_down` | number | MW/hr | No | Normal ramp-down rate |
| `startup_ramp` | number | MW | No | Max output in startup block (default: Pmax) |
| `shutdown_ramp` | number | MW | No | Max output in shutdown block (default: Pmax) |
| `initial_status` | number | — | No | Initial state: 1.0 = online, 0.0 = offline |
| `initial_hours` | number | hours | No | Hours in current state at t=0 |
| `relax` | boolean | — | No | LP relaxation: u,v,w ∈ [0,1] instead of {0,1} |
| `must_run` | boolean | — | No | Force u = 1 at all times |
| `transition_integrality` | string | — | No | Startup / shutdown integrality for this unit: `auto` (binary), `continuous`, `binary`; unset = `model_options.transition_integrality` (default `auto`) |
| `startup_tier_integrality` | string | — | No | Hot / warm / cold selector integrality: `continuous` or `integer`; unset = `model_options.startup_tier_integrality` (default `continuous`) |
| `fast_start` | boolean | — | No | Fast-start unit for `pricing_mode = "fast_start"` (unset = auto: min up and min down ≤ `fast_start_max_hours`) |
| `commitment_period` | number \| `"auto"` | hours | No | Coarser binary variable resolution. Periods are unions of whole cells (blocks; quadrature cells on `Stage.sampling = "point"` stages): cell k joins period ⌊mid_k/P⌋, so a boundary snaps to the nearer cell end. `"auto"`: P = max(block resolution, ½·min(min_up_time, min_down_time)) |
| `commitment_grid` | string | — | No | `"block"` (one status per block/cell) or `"overlay"` (per `commitment_period`). Default: overlay iff `commitment_period` is set. Point stages: default = uniform grid of max(`commitment_period`, 1 h) by node time; `"block"` / `"cells"` keep the previous per-node / quadrature-cell grids |
| `ramp_allowance` | string | — | No | `"boundary"` (default: only ramp rows crossing a commitment-period boundary get the SU/SD ramp allowance) or `"period"` (every block of a commitment period gets the allowance of its period; older, looser form) |
| `startup_trajectory` | number array | MW | No | `power_based` only: power at successive `trajectory_step` instants of the start-up, before the unit reaches `pmin` (empty = quick start) |
| `shutdown_trajectory` | number array | MW | No | `power_based` only: power at successive instants after leaving `pmin` (last entry normally 0) |
| `trajectory_step` | number | h | No | Spacing of the trajectory samples (default 1) |
| `cycle` | string | — | No | `"none"` (default) or `"stage"`: cyclic UC over the stage (typical day/week): transitions, windows and ramps wrap, no initial state, start costs × `duration / period_hours`. See [unit-commitment](unit-commitment.md) |
| `long_transition` | string | — | Cond. | `"on"`, `"off"` or `"no_uc"`; required for cyclic units whose min up/down time or start window exceeds the typical period (validation error otherwise) |
| `long_transition_schedule` | string[] | — | No | Per-stage `long_transition` (by stage position) |
| `hot_start_cost` | number | $/start | No | Startup cost for hot start |
| `warm_start_cost` | number | $/start | No | Startup cost for warm start |
| `cold_start_cost` | number | $/start | No | Startup cost for cold start |
| `hot_start_time` | number | hours | No | A start is hot when the unit has been offline less than this |
| `cold_start_time` | number | hours | No | A start is cold when the unit has been offline at least this (warm in between) |
| `initial_power` | number | MW | No | Output at `t = 0⁻` used by the first-block ramp rows; unset: `Generator.initial_generation` at the horizon start, else 0 |
| `fixed_status` | number\|array\|string | 0/1 | No | Pin the status `u` per stage and block (`1` on, `0` off); blocks without a value stay free. Wins over `must_run` where set |
| `max_starts` / `min_starts` | integer | starts | No | Hard bounds on the number of starts within each `starts_scope` window |
| `starts_scope` | string\|integer | — | No | Window of `max_starts` / `min_starts`: `hour`, `day`, `week`, `horizon` (default; `month` / `year` are treated as `horizon`), or a window length in hours |
| `ini_hours_up` / `ini_hours_down` | number | hours | No | Informational copy of the source's initial up / down hours (the LP uses `initial_status` and `initial_hours`) |

> **Removed fields**: `pmax_segments`, `heat_rate_segments`, `fuel_cost`
> and `fuel_emission_factor` are no longer `Commitment` fields.  The
> piecewise heat rate is set on the `Generator` (`fuel`, `pmax_segments`,
> `heat_rate_segments`) with the fuel price on `Fuel.price`; emissions are
> set with `Fuel.emission_factors[]` or an `EmissionSource` (see
> [Unit Commitment §5](unit-commitment.md#5-piecewise-heat-rate-curves)).

> **Startup tiers** require all five fields (`hot_start_cost`, `warm_start_cost`,
> `cold_start_cost`, `hot_start_time`, `cold_start_time`) to be present.
> If one is missing, or `cold_start_time < hot_start_time` (physically
> invalid), tiers are skipped with a warning and every start is priced at
> the flat `startup_cost` — or, when that is unset, at the largest given
> tier cost (a start is never free).  The tier of a start is chosen from
> the unit's offline time: for a unit off since before the horizon that is
> the in-horizon time before the start plus `initial_hours`, so a
> first-block start after a short initial outage is a hot start.  A unit
> that shut down inside the horizon is offline for the blocks from its
> shutdown to the start.  In both cases the start is hot when offline
> `< hot_start_time`, warm when `hot_start_time ≤` offline
> `< cold_start_time`, and cold otherwise.

### 3.24 Model Options

System-wide modeling options that control the objective and LP relaxation.
The full list is in [Section 1](#1-options).

**JSON path:** `options.model_options`

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `objective_mode` | string | — | No | `"cost"` (default) or `"emissions"`: minimise emissions, charging each emission source at its zone `price` (1 $/t when unset). See [Emissions](#325-emissions) |
| `continuous_phases` | string | — | No | Phase range expression for LP relaxation of UC binaries: `"all"`, `"none"`, `"1,3:5"`, etc. Default: `"none"`. See [Relaxation Control](unit-commitment.md#9-relaxation-control) |

### 3.25 Emissions

Emissions are modelled with three arrays in `system` plus generator and
fuel fields.  See [Emissions](emissions.md) for the full guide (global
and local pollutants, marginal emissions) and
[Unit Commitment §6](unit-commitment.md#6-emission-cost-and-cap) for the
LP rows.

**`emission_array`** (`Emission`) — pollutant registry.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `uid` | integer | — | Yes | Unique identifier |
| `name` | string | — | Yes | Pollutant name (`co2`, `ch4`, `n2o`, `nox`, `so2`, `pm10`, …) |

**`emission_zone_array`** (`EmissionZone`) — system-wide or regional scope
of a price and/or cap.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `uid` | integer | — | Yes | Unique identifier |
| `name` | string | — | Yes | Zone name |
| `emissions` | array | — | Yes¹ | Covered pollutants: `{emission, weight}`; `weight` is the CO₂-eq factor (GWP-100), default 1.0. |
| `price` | number\|array\|string | $/t | No | Per-tonne price (tax) on every source in the zone |
| `cap` | number\|array\|string | t/stage | No | Cap on total weighted emissions per (scenario, stage). Hard unless `cap_cost` is set |
| `cap_cost` | number\|array\|string | $/t | No | Penalty on emissions above `cap` (soft cap) |
| `allowance_pool` | integer\|string | — | No | FK to an `AllowancePool` (cap-and-trade with banking); replaces the per-stage `cap` row |
| `buses` | array | — | No | Bus scope (uids or names) for the fuel-derived sources synthesised from `Fuel.emission_factors`: only generators on these buses get a source in this zone. Empty = all buses. Explicit `emission_source_array` rows are not filtered |

¹ Or the single-pollutant shortcut `"emission": "co2"` (weight 1.0).

**`emission_source_array`** (`EmissionSource`) — generator → zone →
pollutant link.  Can also be written inline as `Generator.emissions[]`.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `uid` | integer | — | Yes | Unique identifier (optional inline) |
| `name` | string | — | Yes | Source name (optional inline) |
| `generator` | integer\|string | — | Yes | FK to Generator (omitted inline) |
| `zone` | integer\|string | — | Yes | FK to EmissionZone |
| `emission` | integer\|string | — | Yes | FK to Emission; must match an entry of the zone's `emissions` |
| `rate` | number\|array\|string | t/MWh | No | Combustion (stack) rate |
| `upstream_rate` | number\|array\|string | t/MWh | No | Upstream (well-to-tank) rate |
| `fuel_rate` | number\|array\|string | t/fuel unit | No | Combustion factor applied to the generator's fuel burn (per-block or piecewise heat rate); additive with `rate` |
| `upstream_fuel_rate` | number\|array\|string | t/fuel unit | No | Upstream factor applied to the fuel burn |

`Fuel.emission_factors[]` (`{emission, combustion, upstream}` in t per
fuel unit) builds one source per covering zone for every generator with
that `fuel`: a t/MWh `rate` (heat rate × heat content × factor) when
`heat_rate` is a scalar, otherwise (per-block `heat_rate`,
`heat_rate_segments`) a `fuel_rate` (heat content × factor) that the LP
applies to the exact fuel burn, including the piecewise heat-rate
slacks and the committed no-load burn `Commitment.noload_fuel · u`.  Generators with `fuel_per_block` are skipped with a warning.  Pollutants can be
referenced by name or by uid in zones, sources and fuels; both resolve
against `emission_array`.

Example: a 30 $/t CO₂ price on the whole system and a soft cap of
1500 t per stage:

```json
"emission_array": [{"uid": 1, "name": "co2"}],
"emission_zone_array": [
  {"uid": 1, "name": "system_co2",
   "emissions": [{"emission": "co2", "weight": 1.0}],
   "price": 30.0, "cap": 1500.0, "cap_cost": 200.0}
],
"emission_source_array": [
  {"uid": 1, "name": "g1_co2", "generator": "g1",
   "zone": "system_co2", "emission": "co2", "rate": 0.9}
]
```

Outputs: `EmissionSource/emissions_sol`, `combustion_sol`, `rate_sol`
(and `upstream_sol`, `captured_sol` when used); `EmissionZone/cap_dual`
(the endogenous price of a cap) and `cap_slack_sol` for a soft cap.

**Migrating from `emission_cost` / `emission_cap`.**  The options
`model_options.emission_cost` and `model_options.emission_cap` were
removed and are rejected as unknown.  Use a system-wide zone instead:
`emission_cost: X` becomes `"price": X`, and `emission_cap: Y` becomes
`"cap": Y` (hard) or `"cap": Y, "cap_cost": Z` (soft).
`Generator.emission_rate` still works but is folded into a `co2` source
that goes to the first zone listing `co2` by **uid**, or else to an
automatic unpriced `default_co2` zone; write an explicit
`EmissionSource` to attach it to a priced or capped zone.

### 3.26 Future Cost

The FutureCost element owns the end-of-horizon cost-to-go (the future-cost
function, FCF: α / `varphi_s`) and the boundary cuts that linearise it.  It
is the **single place** where the FCF boundary cuts are configured, for
every planning method: `monolithic`, `sddp`, `cascade` (and the monolithic
fallback of a single-phase SDDP run) all read only the resolved FutureCost
element.  The first active element is used.

**JSON array name:** `future_cost_array`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `uid` | integer | — | Unique identifier |
| `name` | string | — | Element name (e.g. `"fcf"`) |
| `active` | bool/int | `true` | Inactive elements are skipped |
| `description` | string | — | Free-text provenance |
| `cuts_file` | string | — | Boundary-cut CSV (`iteration,scene,rhs,<state...>`); a relative path resolves against `options.input_directory` for every method |
| `scale_alpha` | number | auto | Scale of the α / `varphi_s` columns; auto-derived from the cut coefficients when unset |
| `mean_shift` | bool | `true` | Rebase α at the reservoirs' `efin` targets; the removed constant `c̄` is added back to the objective |
| `single_cut_equality` | bool | `true` | With a single cut per scene, install it as an equality with α freed (PLEXOS-style terminal value); `false` keeps the `≥` form |
| `sharing` | string | derived | `"per_scene"`, `"shared"` or `"multicut"`; unset → `shared` for `mode = combined`, otherwise `per_scene` |
| `mode` | string | `"separated"` | `"noload"`, `"separated"` (per scene), `"combined"` (broadcast), or `"phi_expectation"` (PLP-literal NVarPhi plane-hydrology φ columns) |
| `iteration_limit` | integer | `0` | Keep only the cuts of the last N iterations of the source run (distinct CSV `iteration` values); `0` = all |
| `missing_cut_var_mode` | string | `"skip_coeff"` | Cut naming an unknown state variable: `"skip_coeff"` (drop the coefficient) or `"skip_cut"` (drop the cut) |
| `valuation` | string | `"end_of_horizon"` | `"end_of_horizon"` or `"present_value"` (apply the last stage's discount factor on load) |
| `use_user_alpha` | bool | `false` | Replace the built-in α by a user-authored `DecisionVariable`; mutually exclusive with `cuts_file` |
| `user_alpha_uid` | integer | — | UID of the user α `DecisionVariable` (required with `use_user_alpha`) |

**Example:**

```json
{
  "system": {
    "future_cost_array": [
      {
        "uid": 1,
        "name": "fcf",
        "cuts_file": "boundary_cuts.csv",
        "mode": "separated",
        "iteration_limit": 0
      }
    ]
  }
}
```

A boundary cut always involves α.  Any other generic linear constraint on
the terminal state belongs in a
[User Constraint](#319-user-constraint), not in FutureCost.

#### Fields that stay in `sddp_options`

- `sddp_options.scale_alpha` — the SDDP per-phase α scale.  When
  `future_cost_array[].scale_alpha` is set it overrides it for the
  boundary α; the monolithic solver reads only the FutureCost value.
- `sddp_options.cut_coeff_eps` — SDDP only; the monolithic solver uses
  the default `1e-8` when loading boundary cuts.

#### Deprecated aliases

The keys that preceded FutureCost are still accepted.  They are
translated into the first active FutureCost element (an element
`{"uid": 1, "name": "fcf"}` is created when there is none) once, after
every JSON file and every `--set` / CLI override has been merged, so
`--set sddp_options.boundary_cuts_file=...` keeps working.  Each honoured
alias logs a warning naming the old and the new key.

| Legacy key | FutureCost field | Honoured for method |
|------------|------------------|---------------------|
| `options.sddp_options.boundary_cuts_file` | `cuts_file` | `sddp`, `cascade` |
| `options.sddp_options.boundary_cuts_mode` | `mode` | `sddp`, `cascade` |
| `options.sddp_options.boundary_cut_sharing_mode` | `sharing` | `sddp`, `cascade` |
| `options.sddp_options.boundary_cuts_mean_shift` | `mean_shift` | `sddp`, `cascade` |
| `options.sddp_options.boundary_max_iterations` | `iteration_limit` | `sddp`, `cascade` |
| `options.sddp_options.missing_cut_var_mode` | `missing_cut_var_mode` | `sddp`, `cascade` |
| `options.monolithic_options.boundary_cuts_file` | `cuts_file` | `monolithic` |
| `options.monolithic_options.boundary_cuts_mode` | `mode` | `monolithic` |
| `options.monolithic_options.boundary_cut_sharing_mode` | `sharing` | `monolithic` |
| `options.monolithic_options.boundary_max_iterations` | `iteration_limit` | `monolithic` |
| `simulation.boundary_cuts_file` | `cuts_file` | any |
| `simulation.boundary_cuts_valuation` | `valuation` | any |

Rules:

- A key of the **other** method is ignored with a warning: the monolithic
  solver never reads `sddp_options`, and SDDP never reads
  `monolithic_options`.
- The same field given with **different** values (element vs. alias, or
  two aliases) is a hard error; equal values are accepted.
- `cascade_options.level_array[].sddp_options` boundary keys are not read.

To rewrite existing cases, use the migration helper (formatting is
preserved; `--method-rules` applies the per-method rules above instead of
moving every legacy key):

```bash
python tools/migrate_future_cost.py [--method-rules] [--dry-run] FILE...
```

`gtopt_check_json` reports each alias (check `future_cost_aliases`) as a
warning and conflicting values as a critical finding.

### 3.27 Gas Network

A steady-state natural-gas network (radial tangents, or linearised pipes
with a sequential LP for meshed networks) coupled to the dispatch
(pure LP; formulation in
[mathematical-formulation.md §5.19](formulation/mathematical-formulation.md);
user guide and benchmark cases in [Gas Network](gas-network.md)).
Units: flow km³/h (standard), pressure bar abs, gas cost $/km³, heat
rate km³/MWh.  The converter `gasnet2gtopt` writes these arrays from a
`*.gasnet.json` description (`--legacy` keeps the older
DecisionVariable / UserConstraint form).

Network gas properties live in `options.gas_options`:
`gas_specific_gravity` (0.6), `gas_temperature` [K] (288.15) and
`compressibility` (0.9).  They feed the Weymouth constant from geometry
and the elevation exponent; a pipe's `gravity`, `temperature` and `z`
override them.  The sequential LP of `weymouth_mode: linearized` pipes
is controlled by `slp_iterations` (0 = single pass), `slp_tolerance`
(1e-4, relative flow change), `slp_damping` (ω = 0.5), `slp_min_flow`
(1e-3 · fmax), `slp_initial_flow` (0.1 · fmax) and `slp_proximal_cost`
(ρ = 1e-6 \$ per km³/h per hour on |f̄ − f₀|).  The proximal term makes
the SLP settle when the LP is degenerate: equal-cost vertices otherwise
alternate between passes.  `linepack_cyclic` (default `true`) closes
each stage with at least its initial line pack.  Set it to `false` for
outage studies, so that the pack can be drawn down.

| `gas_options` field | Default | Description |
|---------------------|---------|-------------|
| `gas_specific_gravity` | 0.6 | Gas specific gravity G (air = 1) |
| `gas_temperature` | 288.15 | Flowing temperature T [K] |
| `compressibility` | 0.9 | Compressibility factor Z |
| `slp_iterations` | 0 | SLP passes after the first monolithic solve |
| `slp_tolerance` | 1e-4 | Stop when the largest relative flow change is below it |
| `slp_damping` | 0.5 | ω in f₀ ← f₀ + ω (f − f₀) |
| `slp_min_flow` | 1e-3 | Floor on \|f₀\| as a fraction of `fmax` |
| `slp_initial_flow` | 0.1 | First-pass \|f₀\| as a fraction of `fmax` (no `reference_flow`) |
| `slp_proximal_cost` | 1e-6 | ρ on \|f̄ − f₀\| (0 = off) |
| `linepack_cyclic` | true | Each stage closes with at least its initial line pack |

Every gas element accepts the common fields `uid` (integer, required),
`name` (string), `active` (boolean or per-stage array, default active)
and `type` (free string tag, e.g. `lng_regas`, `fuel_gas_booster`,
`ccgt`).  References to other elements (`gas_node`, `gas_node_a`,
`generator`, `bus`, …) accept a uid or a name.

**`gas_node_array`** — squared-pressure node with a gas balance per block.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `uid`, `name` | integer, string | — | Yes | Identity |
| `active` | boolean\|array | — | No | Activation status (default active) |
| `type` | string | — | No | Free tag (e.g. `city_gate`) |
| `pmin`, `pmax` | number\|array\|string | bar | No | Pressure band (π = p² ∈ [pmin², pmax²]) |
| `pressure_cost` | number\|array\|string | $/(bar²·h) | No | Cost on π; a tiny negative value (−1e-4) selects the highest-pressure optimum |
| `p_init` | number | bar | No | Initial pressure of adjacent line-pack pipes |
| `elevation` | number | m | No | Elevation above datum (default 0); see `elevation_exponent` |

**`gas_supply_array`** — source (field, import, LNG regasification).

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `gas_node` | uid\|name | — | Yes | Injection node |
| `fmin`, `fmax` | number\|array\|string | km³/h | No | Injection limits |
| `cost` | number\|array\|string | $/km³ | No | Gas cost |
| `type` | string | — | No | Tag, e.g. `lng_regas` |
| `volume` | number | km³ | No | Volume available over each stage: Σ Δt·flow ≤ volume (e.g. an LNG allotment); `fmax` stays the rate cap |

**`gas_demand_array`** — non-power gas load.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `gas_node` | uid\|name | — | Yes | Delivery node |
| `load` | number\|array\|string | km³/h | No | Load per block |
| `fail_cost` | number\|array\|string | $/km³ | No | Shedding cost; unset = hard load |

**`gas_pipe_array`** — Weymouth pipe, flow `gas_node_a → gas_node_b`.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `gas_node_a`, `gas_node_b` | uid\|name | — | Yes | Inlet / outlet |
| `fmax` | number\|array\|string | km³/h | Yes | Capacity (tangents span [0, fmax]) |
| `weymouth_k` | number | bar²/(km³/h)² | No | Weymouth constant; default from the geometry below (Menon SI Weymouth) |
| `length` | number | km | No* | Pipe length (*needed without `weymouth_k`, or for line pack without `linepack_coeff`) |
| `diameter` | number | mm | No* | Inner diameter (*as `length`) |
| `efficiency` | number | p.u. | No | Pipeline efficiency E (0.92) |
| `gravity`, `temperature`, `z` | number | —, K, — | No | Per-pipe overrides of `gas_options` |
| `elevation_exponent` | number | — | No | Exponent `s` of the elevation term (π_a − e^s π_b = K_e f\|f\|); default from the end nodes' `elevation` |
| `segments` | integer | — | No | Number of tangents (8) |
| `tangent_layout` | string | — | No | `uniform` (default) or `relative_pressure` |
| `weymouth_mode` | string | — | No | `radial` (default: fixed direction, tangents), `linearized` (meshed: signed flow, one equality per block, SLP) or `none` (capacity-only arc a → b, 0 ≤ f ≤ fmax, no pressure coupling: a transfer through a parallel system) |
| `linearization` | string | — | No | `linearized` mode: `secant` (default) or `tangent` |
| `reference_flow` | number | km³/h | No | `linearized` mode: signed first-pass reference flow f₀ (default `slp_initial_flow` · fmax) |
| `linepack` | boolean | — | No | Model line pack (intra-stage cycle) |
| `linepack_coeff` | number | km³/bar | No | Default: geometric volume / (Z·Pb) |
| `linepack_pressure_ref` | number | bar | No | Linearisation pressure |
| `linepack_ini` | number | km³ | No | Initial pack (default from the nodes' `p_init`) |

**`gas_compressor_array`** — station / booster `gas_node_in → gas_node_out`.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `gas_node_in`, `gas_node_out` | uid\|name | — | Yes | Suction / discharge node |
| `fmax` | number\|array\|string | km³/h | Yes | Throughput capacity |
| `max_ratio` | number | — | No | p_out ≤ max_ratio · p_in (default 1: no boost) |
| `allow_bypass` | boolean | — | No | Allow p_out < p_in (regulator); default true except `saldarriaga` |
| `bidirectional` | boolean | — | No | Open valve: signed flow, p_out = p_in; needs `max_ratio` 1 and `fuel_model` `none` (default false) |
| `fuel_model` | string | — | No | `none`, `fixed_fraction` (default), `saldarriaga` |
| `fuel_fraction` | number | — | No | Fixed fuel share of throughput |
| `gamma`, `alpha` | number | — | No | Saldarriaga γ (adiabatic default) and (κ−1)/κ |
| `efficiency`, `driver_efficiency` | number | — | No | 0.80 / 0.33 |
| `op_flow`, `op_ratio`, `op_suction_pressure` | number, array, number | km³/h, —, bar | No | Taylor operating point(s); one fuel plane per `op_ratio` |
| `drive` | string | — | No | `gas` (fuel from the suction node) or `electric` |
| `bus`, `motor_efficiency`, `power_max` | uid\|name, number, number | —, —, MW | No | Electric drive: motor load on `bus` (motor efficiency 0.95; `power_max` unset = unbounded) |

**`gas_turbine_array`** — link from a `Generator` to its fuel-gas node.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `generator`, `gas_node` | uid\|name | — | Yes | Plant and delivery node |
| `heat_rate` | number\|array\|string | km³/MWh | No | Fuel use in the node balance |
| `min_pressure` | number | bar | No | Plant minimum fuel-gas pressure (gate) |
| `pressure_band` | number | bar | No | Linear ramp below `min_pressure` (0.5) |
| `nominal_pressure` | number | bar | No | Full output pressure (derate above `min_pressure`) |
| `derate_min_output` | number | p.u. | No | Output share at `min_pressure` |

```json
"gas_node_array": [{"uid": 1, "name": "S", "pmin": 30, "pmax": 70},
                   {"uid": 2, "name": "T", "pmin": 35, "pmax": 70}],
"gas_supply_array": [{"uid": 1, "name": "LNG", "gas_node": "S",
                      "fmax": 1000, "cost": 300, "type": "lng_regas"}],
"gas_demand_array": [{"uid": 1, "name": "CITY", "gas_node": "T",
                      "load": 60, "fail_cost": 3000}],
"gas_pipe_array": [{"uid": 1, "name": "S_T", "gas_node_a": "S",
                    "gas_node_b": "T", "length": 80, "diameter": 406,
                    "fmax": 300}],
"gas_turbine_array": [{"uid": 1, "name": "CCGT_gas", "generator": "CCGT",
                       "gas_node": "T", "heat_rate": 0.2,
                       "min_pressure": 30}]
```

Outputs: `GasNode/{pressure_sol, pi_sol, balance_dual}` (gas price
$/km³), `GasSupply/flow_sol`, `GasDemand/{load_sol, fail_sol}`,
`GasPipe/{flow_sol | flow_in_sol, flow_out_sol, linepack_sol}` and the
exactness report `GasPipe/{weymouth_gap_sol, pressure_error_sol}`,
`GasCompressor/{flow_sol, fuel_sol, ratio_sol, power_sol}`,
`GasTurbine/{fuel_sol, pressure_gate_dual, derate_dual}`.

---

### 3.28 Multi-carrier nodes and storage (hydrogen, ammonia, thermal)

Besides the electric `Bus`, gtopt has carrier-tagged balance nodes.
Carriers never mix: an element on a node of one carrier cannot serve
another carrier except through a `CarrierConverter`.  Energy units are
MWh (MWh_LHV for hydrogen and ammonia, MWh_th for heat).  The LP is
given in [formulation](formulation/mathematical-formulation.md).

| Array | Element | Fields |
|-------|---------|--------|
| `hydrogen_node_array` / `ammonia_node_array` / `thermal_node_array` | Node | `uid`, `name`, `active` |
| `hydrogen_storage_array` | HydrogenStorage | see below, node key `hydrogen_node` |
| `ammonia_storage_array` | AmmoniaStorage | see below, node key `ammonia_node` |
| `thermal_storage_array` | ThermalStorage | see below, node key `thermal_node` |
| `hydrogen_demand_array` | HydrogenDemand | see below |
| `carrier_converter_array` | CarrierConverter | see below |

**Storage fields** (all three storage types; same semantics as
`Battery` / `Reservoir`): `type` (tag, e.g. `salt_cavern`, `lh2`),
the node key, `input_efficiency`, `output_efficiency`,
`annual_standing_loss` [p.u./year], `emin`, `emax`, `ecost`, `eini`,
`efin`, `efin_cost`, `soft_emin`, `soft_emin_cost`, the expansion
fields `capacity`, `expcap`, `expmod`, `capmax`, `annual_capcost`,
`annual_derating`, `integer_expmod`, and `use_state_variable` /
`daily_cycle`.  The storage level is carried between stages like a
battery's.  On point-sampled stages these storages follow
`model_options.battery_storage_integration`.

**HydrogenDemand** (offtake from a `hydrogen_node`):

| Field | Units | Description |
|-------|-------|-------------|
| `hydrogen_node` | — | Node the offtake withdraws from |
| `lmax` / `lmin` | MW_LHV | Offtake rate bounds per (stage, block); `lmax` unset = unbounded, `lmin` default 0 |
| `min_energy` / `max_energy` | MWh_LHV/stage | Energy delivered per stage, `Σ dur·load` |
| `min_energy_cost` | $/MWh_LHV | Penalty below `min_energy` (set = soft, unset = hard) |
| `price` | $/MWh_LHV | Value of the offtake (price-responsive buyer); default 0 |

**CarrierConverter** (electrolyser, fuel cell, Haber–Bosch, cracker,
power block, …) moves energy from one carrier's node to another's:

| Field | Units | Description |
|-------|-------|-------------|
| `from_carrier` / `to_carrier` | — | `electric`, `hydrogen`, `thermal`, `ammonia` (required) |
| `from_node` / `to_node` | — | Node (Bus, HydrogenNode, …) of the matching carrier |
| `efficiency` | p.u. | Output per unit input, per (stage, block); default 1 |
| `ocost` | $/MWh input | Operating cost per unit of input flow; default 0 |
| `capacity`, `expcap`, `expmod`, `capmax`, `annual_capcost`, `annual_derating`, `integer_expmod` | MW input | Installed input capacity and capacity expansion |

### 3.29 Fuel

**JSON array name:** `fuel_array`.  Generators reference a fuel
(`Generator.fuel`, with `heat_rate`); see
[Unit Commitment](unit-commitment.md) and [Emissions](emissions.md).

| Field | Units | Description |
|-------|-------|-------------|
| `type` / `subtype` / `description` | — | Tags (e.g. `gas` / `lng`); metadata only |
| `price` | $/fuel unit | Per-(stage, block) schedule; the fuel unit is the user's choice and must match `heat_rate` |
| `heat_content` | GJ/fuel unit | Optional, for energy reporting |
| `emission_factors` | — | Array of `{emission, combustion, upstream}` [t/fuel unit] per pollutant |
| `combustion_emission_factor` / `upstream_emission_factor` | tCO₂/fuel unit | Legacy single-pollutant form, folded into `emission_factors` |
| `max_offtake` / `min_offtake` | fuel unit/stage | Cap / floor on the fuel burnt by all generators on this fuel per stage |
| `max_offtake_cost` / `min_offtake_cost` | $/fuel unit | Penalty that makes the cap / floor soft (unset = hard) |
| `max_offtake_per_block` / `min_offtake_per_block` | bool | Enforce the stage limit per block, pro-rated by block duration |

### 3.30 Plant

**JSON array name:** `plant_array`.  Groups generators that are
configuration variants of one physical plant (e.g. combined-cycle
modes).

| Field | Description |
|-------|-------------|
| `generator_names` | Member generators (at least two for any row to be emitted) |
| `pmax` | Plant capacity: `Σ generation ≤ pmax` per block |
| `n_units` | Units that can be committed: `Σ commit_coeffs·status ≤ n_units`, one row per stage on the members' first-block status |
| `commit_coeffs` | Per-member coefficients for `n_units` (default 1 each) |
| `uniq_mutex` | `true`: at most one configuration committed, `Σ status ≤ 1` per stage (first-block status; default off) |

### 3.31 Line Commitment

**JSON array name:** `line_commitment_array`.  Optimal transmission
switching: a binary in-service status `u` for a `line`, with the KVL row
relaxed by a big-M when the line is open.

| Field | Description |
|-------|-------------|
| `line` | Line being switched |
| `initial_status` | Status at t = 0 (1 closed, 0 open); unset = free |
| `must_run` | Pin the line closed |
| `fixed_status` | Per-(stage, block) pin (1 closed, 0 open; other values free). A line outage always pins it open |
| `relax` | Relax `u` to [0, 1] (LP lower bound) |
| `kvl_big_m` | Per-line big-M of the KVL disjunction (> 0); default `\|b\|·(θmax − θmin)` |
| `startup_cost`, `shutdown_cost`, `hot_start_cost`, `warm_start_cost`, `cold_start_cost`, `hot_start_time`, `cold_start_time`, `min_up_time`, `min_down_time`, `max_starts`, `min_starts`, `starts_scope`, `initial_hours` | Switching costs and limits with the same meaning as on `Commitment` (switching events become v / w columns) |

### 3.32 Inertia Provision

**JSON array name:** `inertia_provision_array`.  Inertia offered by a
`generator` to one or more inertia zones (see Section 3.18 bis).

| Field | Units | Description |
|-------|-------|-------------|
| `generator` | — | Providing generator |
| `inertia_zones` | — | Zones credited |
| `inertia_constant` | s | Machine inertia constant H |
| `rated_power` | MVA | Rated apparent power S |
| `provision_max` | MW | Maximum provision per (stage, block) |
| `provision_factor` | MWs/MW | Effectiveness factor per (stage, block) |
| `cost` | $/MW | Provision cost per (stage, block) |

### 3.33 Pump

**JSON array name:** `pump_array`.  A pump moves water through a
`waterway` using the electricity of a linked `demand`:
`pump_power = pump_factor · flow`.

| Field | Units | Description |
|-------|-------|-------------|
| `waterway` | — | Pumping waterway |
| `demand` | — | Electrical demand that carries the pump load (usually with `fcost = 0`) |
| `pump_factor` | MW/(m³/s) | Power per unit flow |
| `efficiency` | p.u. | Pump efficiency (default 1) |
| `capacity` | m³/s | Maximum pump flow |
| `main_reservoir` | — | Reservoir whose volume may drive the conversion rate |

### 3.34 Decision variables and user models

`decision_variable_array` declares free LP columns (bounds, cost,
scope, state carried between stages, non-anticipativity) that user
constraints can reference; `user_model_array` bundles variables and
constraints under one name and tag; `user_constraint_file(s)` load
PAMPL files.  All three are described in
[User Constraints](user-constraints.md).

### 3.35 LNG Terminal

**JSON array name:** `lng_terminal_array`.  An LNG tank with deliveries,
regasification and boil-off that fuels linked generators (formulation
§5.14; with a gas network, see [Gas Network](gas-network.md)).

| Field | Units | Description |
|-------|-------|-------------|
| `emin` / `emax` | m³ | Tank level bounds per (stage, block) |
| `ecost` | $/m³ | Holding cost per (stage, block) |
| `eini` / `efin` / `efin_cost` | m³, $/m³ | Initial level, end-of-horizon minimum and its shortfall penalty |
| `soft_emin` / `soft_emin_cost` | m³, $/m³ | Soft floor and its penalty |
| `annual_standing_loss` | p.u./year | Boil-off |
| `delivery` | m³/stage | Scheduled cargo arrivals |
| `sendout_max` / `sendout_min` | m³/h | Regasification rate bounds |
| `spillway_capacity` / `spillway_cost` | m³/h, $/m³ | Venting limit and penalty |
| `use_state_variable` | — | Carry the tank level between phases |
| `mean_production_factor` | MWh/m³ | Used to convert the state penalty `scost` |
| `scost` | $/m³ | State-violation penalty |
| `flow_conversion_rate` | — | m³/h × h → m³ (default 1) |
| `generators` | — | Linked generators: `[{generator, heat_rate}]`, `heat_rate` in m³ LNG/MWh (default 1) |

## 4. External Data Files

When a field value is a **string** instead of a number, it refers to an
external data file in the `input_directory`.  Files are organized by
component type:

```text
<input_directory>/
├── Bus/
├── Generator/
│   └── pmax.parquet
├── Demand/
│   └── lmax.parquet
├── Line/
├── Battery/
└── ...
```

### File Format

External data files (CSV or Parquet) follow a tabular format with columns:

| Column     | Description |
|------------|-------------|
| `scenario` | Scenario UID |
| `stage`    | Stage UID |
| `block`    | Block UID |
| `uid:<N>`  | Value for element with UID N |

### Example CSV

```csv
"scenario","stage","block","uid:1"
1,1,1,10
1,2,2,15
1,3,3,8
```

---

## 5. Output Structure

After running GTOpt, results are written to the `output_directory`:

```text
output/
├── solution.csv              # Objective value, status
├── Bus/
│   └── balance_dual.csv      # Marginal costs per bus
├── Generator/
│   ├── generation_sol.csv    # Generation dispatch
│   └── generation_cost.csv   # Generation costs
└── Demand/
    ├── load_sol.csv           # Served load
    ├── fail_sol.csv           # Unserved demand
    ├── capacity_dual.csv      # Capacity dual values
    └── ...
```

Output files use the same tabular format as input files, with
`scenario`, `stage`, `block`, and `uid:<N>` columns.

---

## 6. Planning (Root Container)

The `Planning` object is the root of the GTOpt input data model.
A JSON configuration file represents a single `Planning` instance
with three top-level sections:

| Field        | Type       | Required | Description |
|--------------|------------|----------|-------------|
| `options`    | Options    | No       | Global solver and I/O settings (see [Section 1](#1-options)) |
| `simulation` | Simulation | Yes      | Time structure: blocks, stages, scenarios, phases, scenes, apertures (see [Section 2](#2-simulation)) |
| `system`     | System     | Yes      | Physical network components (see [Section 3](#3-system)) |

Multiple JSON files can be merged with `Planning::merge()`, allowing
the configuration to be split across files (e.g. time structure in
one file, network elements in another). The merge uses first-value
semantics for scalar options and concatenation for arrays.

**Example (minimal single-file):**

```json
{
  "options": {
    "model_options": {
      "demand_fail_cost": 1000,
      "use_single_bus": true
    },
    "input_directory": "input",
    "output_directory": "output"
  },
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [
      {"uid": 1, "first_block": 0, "count_block": 1}
    ],
    "scenario_array": [{"uid": 1, "probability_factor": 1}]
  },
  "system": {
    "bus_array": [{"uid": 1, "name": "bus1"}],
    "generator_array": [
      {"uid": 1, "name": "gen1", "bus": 1, "pmax": 100,
       "gcost": 20}
    ],
    "demand_array": [
      {"uid": 1, "name": "dem1", "bus": 1, "lmax": 50}
    ]
  }
}
```

---

## 7. Complete Example

See `cases/c0/system_c0.json` for a minimal working example with:
- 1 bus, 1 generator, 1 demand
- 5 time blocks, 5 stages, 1 scenario
- Demand expansion planning

---

## See also

- **[Unit Commitment Guide](unit-commitment.md)** — Dedicated guide for
  the three-bin UC formulation, emission framework, and worked examples
- **[Mathematical Formulation](formulation/mathematical-formulation.md)**
  — Full LP/MIP optimization formulation with academic references
- **[Planning Guide](planning-guide.md)** — Step-by-step planning guide
  with worked examples
- **[Usage Guide](usage.md)** — Command-line options and advanced usage
- **[Scripts Guide](scripts-guide.md)** — Python conversion utilities
