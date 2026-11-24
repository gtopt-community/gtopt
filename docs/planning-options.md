# Planning Options Reference

Comprehensive reference for the GTOpt option hierarchy, JSON interface,
merge semantics, and solver configuration.

## Option Class Hierarchy

```text
MainOptions                    (CLI-only, not in JSON)
  |
  +-- PlanningOptions          (JSON key: "options")
        |
        +-- ModelOptions           (JSON key: "model_options")
        +-- MonolithicOptions      (JSON key: "monolithic_options")
        |     +-- SolverOptions        (JSON key: "solver_options")
        +-- SddpOptions            (JSON key: "sddp_options")
        |     +-- SolverOptions        (JSON key: "forward_solver_options")
        |     +-- SolverOptions        (JSON key: "backward_solver_options")
        +-- CascadeOptions         (JSON key: "cascade_options")
        +-- SolverOptions          (JSON key: "solver_options")
        +-- LpMatrixOptions        (JSON key: "lp_matrix_options")
        +-- SanityOptions          (JSON key: "sanity_options")
        +-- VariableScale[]        (JSON key: "variable_scales")
```

**C++ headers:**

| Class | Header |
|-------|--------|
| `PlanningOptions` | `planning_options.hpp` |
| `PlanningOptionsLP` | `planning_options_lp.hpp` |
| `MainOptions` | `main_options.hpp` |
| `SolverOptions` | `solver_options.hpp` |
| `SddpOptions` | `sddp_options.hpp` |
| `MonolithicOptions` | `monolithic_options.hpp` |
| `CascadeOptions` | `cascade_options.hpp` |
| `ModelOptions` | `model_options.hpp` |
| `VariableScale` | `variable_scale.hpp` |

> **Backward-compatibility aliases**: `Options` = `PlanningOptions`,
> `OptionsLP` = `PlanningOptionsLP`.  The JSON key `"options"` is unchanged.

## PlanningOptions Fields

All fields are `std::optional` -- absent fields inherit built-in defaults
(see `PlanningOptionsLP` for resolved values).

### Input/Output

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `input_directory` | string | `"input"` | Root directory for external data files |
| `input_format` | string | `"parquet"` | Input format: `"parquet"` or `"csv"` |
| `output_directory` | string | `"output"` | Root directory for result files |
| `output_format` | string | `"parquet"` | Output format: `"parquet"` or `"csv"` |
| `output_compression` | string | `"zstd"` | Compression codec for output files |
| `use_uid_fname` | bool | `true` | Use UIDs instead of names in filenames |

### Model Parameters (deprecated flat fields)

> **Deprecated**: The flat model fields below are kept for backward
> compatibility but emit a deprecation warning when parsed from JSON.
> Use the `model_options` sub-object instead (see [ModelOptions
> Fields](#modeloptions-fields)).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `demand_fail_cost` | float | -- | **Deprecated** — use `model_options.demand_fail_cost` |
| `reserve_fail_cost` | float | -- | **Deprecated** — use `model_options.reserve_fail_cost` |
| `hydro_fail_cost` | float | -- | **Deprecated** — use `model_options.hydro_fail_cost` |
| `hydro_use_value` | float | -- | **Deprecated** — use `model_options.hydro_use_value` |
| `use_line_losses` | bool | `true` | **Deprecated** — use `model_options.use_line_losses` |
| `loss_segments` | int | `1` | **Deprecated** — use `model_options.loss_segments` |
| `use_kirchhoff` | bool | `true` | **Deprecated** — use `model_options.use_kirchhoff` |
| `use_single_bus` | bool | `false` | **Deprecated** — use `model_options.use_single_bus` |
| `kirchhoff_threshold` | float | `0.0` | **Deprecated** — use `model_options.kirchhoff_threshold` |
| `scale_objective` | float | `1000` | **Deprecated** — use `model_options.scale_objective` |
| `scale_theta` | float | auto | **Deprecated** — use `model_options.scale_theta` |

> **Note**: `annual_discount_rate` belongs in the `simulation` section.
> For backward compatibility it is still accepted as a flat field in
> `options`, but emits a deprecation warning.

### Solver Selection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `method` | string | `"monolithic"` | Planning method: `monolithic`, `sddp`, `cascade` |
| `build_mode` | string | `"scene-parallel"` | How per-cell `SystemLP` objects are assembled: `"serial"`, `"scene-parallel"`, `"full-parallel"`, `"direct-parallel"`. `serial` skips the `AdaptiveWorkPool` / build-buffer for apples-to-apples comparison against the pre-parallel code path |

### Logging and Debug

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `log_directory` | string | `"logs"` | Directory for log and trace files |
| `lp_debug` | bool | `false` | Save LP debug files before solving |
| `lp_compression` | string | -- | Compression codec for LP files |
| `lp_only` | bool | `false` | Build LP matrices without solving (CLI: `--lp-only` / `-c`) |
| `lp_scales` | string | `none` | Write the [LP scales](lp-scales.md) next to each LP file written by `--lp-file`: `summary` (JSON) or `full` (JSON plus Parquet tables); `true`/`false` mean `full`/`none` (CLI: `--lp-scales`) |
| `lp_fingerprint` | bool | `false` | Write [LP fingerprint](lp-fingerprint.md) JSON for formulation audit |
| `lp_debug_scene_min` | int | -- | Minimum scene UID (inclusive) for LP debug file saving |
| `lp_debug_scene_max` | int | -- | Maximum scene UID (inclusive) for LP debug file saving |
| `lp_debug_phase_min` | int | -- | Minimum phase UID (inclusive) for LP debug file saving |
| `lp_debug_phase_max` | int | -- | Maximum phase UID (inclusive) for LP debug file saving |

### Constraint Handling

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `constraint_mode` | string | `"strict"` | User-constraint runtime error policy: `"normal"` (warn + drop offending constraint), `"strict"` (default; abort with diagnostic), `"debug"` (strict + verbose per-row lowering trace). See [User Constraints → constraint_mode](user-constraints.md#constraint_mode--runtime-error-policy) |

### Grouped Sub-objects

| Field | Type | Description |
|-------|------|-------------|
| `model_options` | `ModelOptions` | Power system model configuration (**canonical location** for model fields) |
| `monolithic_options` | `MonolithicOptions` | Monolithic solver settings |
| `sddp_options` | `SddpOptions` | SDDP solver settings |
| `cascade_options` | `CascadeOptions` | Cascade solver settings |
| `solver_options` | `SolverOptions` | Global LP solver configuration |
| `lp_matrix_options` | `LpMatrixOptions` | LP assembly / equilibration / stats configuration |
| `sanity_options` | `SanityOptions` | Thresholds of the data-quality warnings emitted at load time |
| `variable_scales` | `VariableScale[]` | Per-class/variable LP scale overrides |

> **Migration**: Model parameter fields (`use_kirchhoff`, `use_single_bus`,
> etc.) can appear either at the top level of `options` (deprecated, backward-
> compatible) or inside the `model_options` sub-object (preferred).  When
> both are present, the top-level flat value takes precedence.  Flat fields
> emit a deprecation warning when parsed from JSON.

## ModelOptions Fields

The `model_options` sub-object is the **canonical location** for
LP-construction parameters.  It enables per-level overrides in cascade
solver configurations.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `use_single_bus` | bool | `false` | Collapse network to single bus (copper-plate) |
| `use_kirchhoff` | bool | `true` | Apply DC Kirchhoff voltage-law constraints |
| `use_line_losses` | bool | `true` | **Deprecated** — use `line_losses_mode` instead. Model resistive line losses (on/off) |
| `line_losses_mode` | string | `"adaptive"` | Line loss formulation: `"none"`, `"linear"`, `"piecewise"`, `"bidirectional"`, `"adaptive"`, `"dynamic"` |
| `kirchhoff_threshold` | float | `0.0` | Min bus voltage [kV] for Kirchhoff activation |
| `kirchhoff_mode` | string | `"cycle_basis"` | KVL formulation: `"cycle_basis"` (loop flow) or `"node_angle"` (B–θ) |
| `dc_line_reactance_threshold` | float | `1e-6` | Per-unit floor `\|X/V²\|` below which a line's KVL flow term is dropped (see `tiny_reactance_mode`); `0` disables |
| `tiny_reactance_mode` | string | `"tie"` | Lines with `reactance` 0 or below `dc_line_reactance_threshold`: `"tie"` = zero-impedance tie, KVL forces the two bus angles equal (`θ_a − θ_b = φ`), flow free within `tmax`; `"dc"` = legacy free-flow DC link. Lines without `reactance` are always DC links |
| `loss_segments` | int | `1` | Piecewise-linear segments for quadratic losses |
| `loss_cost_eps` | float | `0.1` | $/MWh on line-loss columns (degeneracy breaker / negative-LMP arbitrage guard). Per-line `Line.loss_cost_eps` overrides; `0` disables. Inert for `none`/`linear`/`piecewise_direct` |
| `scale_objective` | float | `1000` | Objective coefficient divisor |
| `scale_theta` | float | auto | Voltage-angle enlargement: θ_LP = scale_theta · θ (rad); auto = 1/(3·median(X/V²)); 1 = radians; 1000 = milliradians. `auto_scale = false` and unset ⇒ 1. Values < 1 shrink θ and log a warning (the meaning was inverted before 2026-11). A manual value always wins over auto (`theta_max` is still auto-computed); set it with `--set model_options.scale_theta=1000` or the converters' opt-in `--scale-theta FACTOR` (`plp2gtopt`, `plexos2gtopt`), which emit nothing by default |
| `demand_fail_cost` | float | `1000` | Penalty $/MWh for unserved demand (value of lost load) for every demand without its own `fcost`. `0` = unpenalized (freely sheddable) load; for non-sheddable load set `Demand.forced = true`. See [Unserved-demand cost](input-data.md#unserved-demand-cost-and-hard-demand) |
| `demand_fail_segments` | array | -- | Default stepwise curtailment curve `[{fraction, cost}, …]` (scalar costs) for demands with neither `fcost` nor `fail_segments`; takes precedence over `demand_fail_cost`. See input-data §3.3 |
| `battery_annual_standing_loss` | float | `0.48` | Self-discharge [p.u./year] for every Battery whose `annual_standing_loss` is unset (4 %/month, Li-ion cell + BMS; linear hourly drain = value/8760). `0` restores the legacy lossless default. Explicit per-battery values win; Reservoir/other storage unaffected |
| `reserve_coupling` | string | `"warn"` | How `validate_planning` treats a ReserveZone requirement coupled to dispatch (endogenous load term, `flow_coef`, endogenous inertia, commitment inertia with a relaxed Commitment, line contingencies, `contingency_includes_reserve`): `"warn"`, `"error"` (reject the case) or `"off"`.  Such a coupling puts a $\beta\mu$ credit into the LMPs and can price buses negative (formulation §5.9) |
| `battery_power_envelope` | bool | `true` | Emit `p_charge/Pc_max + p_discharge/Pd_max ≤ 1` per block on every battery converter (`Converter/power_envelope`), cutting simultaneous full-power charge + discharge |
| `reserve_fail_cost` | float | -- | Penalty $/MWh for unserved reserve |
| `hydro_fail_cost` | float | -- | Default penalty $/m³ for unmet hydro rights (falls back to `0.0` when unset). Overridden by per-element `fail_cost` |
| `hydro_use_value` | float | -- | Default benefit $/m³ for exercising hydro rights (falls back to `0.0` when unset). Overridden by per-element `use_value` |
| `state_fail_cost` | float | -- | Penalty $/MWh for state-variable violations in SDDP elastic filter. Fallback when an element (reservoir, etc.) does not define its own `scost`. Converted to physical units via the element's `mean_production_factor` |
| `objective_mode` | string | `"cost"` | `"emissions"` minimises total emissions: generator dispatch cost coefficients (`gcost`, fuel) are zeroed and each emission source is charged at its zone `price` (1 $/t when unset). Emission prices and caps are not model options; set `price`, `cap` and `cap_cost` on an `EmissionZone` (see [Emissions](emissions.md)). The former `emission_cost` / `emission_cap` options were removed |
| `continuous_phases` | string | `"none"` | Phase-range expression controlling which phases run LP-relaxed (integers become continuous). Syntax: `"all"`, `"none"`, `"0"`, `"1,3:5,8:"`, `":3"`. Settable per cascade level |

## SolverOptions Fields

The `solver_options` sub-object (also embedded in `monolithic_options`,
`sddp_options.forward_solver_options`, and `sddp_options.backward_solver_options`)
configures the LP backend.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `algorithm` | string/int | `"barrier"` | LP algorithm: `"default"` (0), `"primal"` (1), `"dual"` (2), `"barrier"` (3) |
| `threads` | int | `0` | Number of parallel solver threads (0 = solver default) |
| `presolve` | bool | `true` | Apply LP presolve before solving |
| `log_level` | int | `0` | Solver output verbosity (0 = none) |
| `log_mode` | string | `"nolog"` | Solver log-file policy: `"nolog"` or `"detailed"` (per-scene/phase/aperture file); the legacy numeric codes `0` / `1` are also accepted |
| `optimal_eps` | float | -- | Optimality (reduced-cost) tolerance, passed to the solver as given.  Unset: the solver parameter file's value if it sets one (e.g. `CPXPARAM_Simplex_Tolerances_Optimality` in `solvers/cplex.prm`), else `1e-6 / scale_objective` (clamped to [1e-9, 1e-1]) when `scale_objective != 1`, else the solver default.  The value used and why is logged once |
| `feasible_eps` | float | -- | Feasibility tolerance (nullopt = solver default) |
| `barrier_eps` | float | -- | Barrier convergence tolerance (nullopt = solver default) |
| `time_limit` | float | -- | Wall-clock budget in seconds for the whole solve of one LP/MIP (0 = no limit): MIP-start stages and algorithm-fallback retries share it — each retry gets the remaining time and retries stop once < 1 s is left.  For a MIP with `mip_polish` on, min(5 % of the budget, 300 s) is reserved for the integer-fixed LP re-solve (the polish gets the remaining time, at least 10 s) |
| `mip_polish` | bool | `true` | MIP only.  After the MIP stops — optimal, or non-optimal at a time / node / solution / memory limit or abort while holding a feasible incumbent — fix every integer column at its incumbent value and re-solve the remaining LP (CPLEX: `CPXPROB_FIXEDMILP`).  The reported objective, primal and duals are those of the fixed LP, which is never worse than the incumbent (on CEN PCP 2026-04-22 an incumbent of 3.06e9 polishes to 1.58e9).  Runs whether or not duals are written.  A limit stop stays non-optimal: the run still exits 1 and `solution.csv` reports status `5` / `limit_polished`; the log prints the incumbent and polished objectives.  If the fixed LP fails, the incumbent is kept and a warning is logged.  `false` reports the raw incumbent (and, on a limit stop, no duals) |
| `scaling` | string | -- | Internal solver scaling strategy (nullopt = solver default): `"none"`, `"automatic"`, `"aggressive"` (or the codes `0` / `1` / `2`). See `SolverScaling` enum |
| `crossover` | bool | `true` | Convert interior-point solution to a simplex basis for duals (only meaningful with `algorithm=barrier`) |
| `max_fallbacks` | int | `2` | On non-optimal exit, cycle through barrier → dual → primal up to this many times. `0` disables fallback |
| `param_file` | string | -- | Backend-native parameter file (CPLEX `.prm`, …), read before the typed fields above. A relative path is resolved against `input_directory` first (the case directory wins, so `"solvers/cplex.prm"` works when gtopt runs from an output directory); the working directory is only a fallback when the case has no such file.  The resolved path and which location supplied it are logged; a missing file is reported at WARN.  Unset: `<input_directory>/solvers/<solver>.prm` is used when it exists (auto-discovery) |

## MonolithicOptions Fields

> **Boundary cuts**: configure them on the FutureCost element
> (`system.future_cost_array`, see
> [Input Data — Future Cost](input-data.md#326-future-cost)).  The
> `boundary_*` keys below are deprecated aliases, honoured only when
> `method` is `monolithic`.  The monolithic solver never reads
> `sddp_options`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `solve_mode` | string | `"monolithic"` | Solve mode: `"monolithic"` or `"sequential"` |
| `solver_options` | `SolverOptions` | -- | Per-method LP solver configuration |
| `boundary_cuts_file` | string | -- | Deprecated alias → `future_cost_array[].cuts_file` |
| `boundary_cuts_mode` | string | -- | Deprecated alias → `future_cost_array[].mode` |
| `boundary_cut_sharing_mode` | string | -- | Deprecated alias → `future_cost_array[].sharing` |
| `boundary_max_iterations` | int | -- | Deprecated alias → `future_cost_array[].iteration_limit` |

## LpMatrixOptions Fields

The `lp_matrix_options` sub-object configures how the flat (column-major)
LP representation is built and conditioned before being handed to the
backend.  Only the fields listed below are exposed in JSON; the four name
flags (`col_with_names`, `row_with_names`, `col_with_name_map`,
`row_with_name_map`) are set internally and are enabled together when
`--lp-file` is given or `lp_debug` / `lp_error` is on (CLI flag, `--set`
or JSON).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `equilibration_method` | string | topology | Matrix scaling: `"none"`, `"row_max"`, `"ruiz"`.  Unset ⇒ `"ruiz"` for multi-bus Kirchhoff models (`use_kirchhoff` and not `use_single_bus`), else `"row_max"` |
| `fast_sqrt_method` | string | `"ieee_halve"` | Approximate sqrt used by Ruiz scaling; see `FastSqrtMethod` enum |
| `ruiz_max_iterations` | int | `5` | Ruiz iteration cap |
| `ruiz_tolerance` | float | `1e-3` | Ruiz convergence tolerance |
| `water_dual_target` | float | unset (off) | Target `\|y_LP\|` for reservoir energy-balance rows; see below |
| `water_dual_clamp` | float | `1e3` | Symmetric clamp `c in [1/k, k]` for the water-row factor |
| `kvl_row_scale` | bool | off | EXPERIMENTAL: post-equilibration factor on node-angle KVL rows; see below |
| `kvl_row_clamp` | float | `100` | Clamp `c in [1/k, k]` of the KVL row factor |
| `kvl_x_ref` | float | median(X/V²) | Reference `x_tau` of the KVL factor `c = x_ref / x_tau` (setting it also enables the factor) |
| `compute_stats` | bool | `false` | Compute and log coefficient min/max/ratio during `flatten()` |
| `lp_coeff_ratio_threshold` | float | `1e7` | When global max/min coefficient ratio exceeds this, print a per-scene/phase breakdown |
| `solver_name` | string | -- | Solver backend pin (`--solver` wins; beats `.gtopt.conf` `solver`, `$GTOPT_SOLVER` and plugin priority).  gtopt writes the solver it resolved here in `gtopt_state.json`, so replaying the snapshot uses the same backend |

These fields are resolved for every LP build
(`PlanningOptionsLP::resolve_lp_matrix_options`): the `gtopt` binary,
each cascade level, SDDP aperture systems and a `PlanningLP` built
directly from C++ all use the same options.  The topology default of
`equilibration_method` follows the `model_options` of the LP being
built, so a cascade level that rebuilds its LP with
`use_single_bus = true` defaults to `"row_max"` and a multi-bus
Kirchhoff level to `"ruiz"`; an explicit `equilibration_method` applies
to every level.

> **LP naming**: There is **no** `names_level` field or `--lp-names-level`
> flag.  Pass `--lp-file <path>` or `--lp-debug` to have GTOpt enable all
> four naming fields on `LpMatrixOptions` so the generated `.lp` dump is
> human-readable.

## SanityOptions Fields

`validate_planning` runs data-quality checks on every case at load time.
They **never** reject a case: each category emits ONE `Validation:
Sanity: ...` warning with the count and the top offenders; the full list
is logged at debug level (`--log-level debug`).  Only inline values are
inspected — file-backed schedules resolve at LP-build time (the converters'
post-conversion report, `gtopt_shared.sanity_report`, also reads the
files).

| Category | Flags |
|----------|-------|
| phantom reward | `FlowRight.uvalue > 0`, `FlowRight.fcost < 0` or `model_options.hydro_use_value` above `reward_fail_cost_ratio` × demand fail cost (global `demand_fail_cost`, else the cheapest per-demand `fcost`) — the LP sheds load to collect it (Maule Res. 105: reward 1100 > deficit 1000 gave −2.1 G\$ of phantom reward) |
| coupled phantom reward | a `FlowRight` with `uvalue > 0` that shares a user constraint (inline or loaded from PAMPL) with a right whose shortfall penalty (`fcost > 0`, or `uvalue < 0` on a target-0 deficit right) is below `reward_fail_cost_ratio` × the reward — the LP books shortfall to collect the reward (Maule Res. 105, review R3: reward 1100 vs `deficit_Melado` 1000) |
| profile range | `GeneratorProfile` / `DemandProfile` / `CapacityProfile` values outside `[0, 1]` (per-unit factors) |
| dead commitment | generators with `pmax` (or `capacity`, no `expcap`) `<= 0` over the whole horizon that still have a `Commitment` / `SimpleCommitment` or `pmin > 0`; batteries with zero `pmax_charge` and `pmax_discharge` that keep `commitment` or a `pmin_*` |
| line X/R | `|X| / R > line_xr_max` |
| line x p.u. | `100·|X|/V² > line_x_pu_max` (100 MVA base; lines with `voltage` set) |
| line x outlier | `|X|/V²` more than `line_x_outlier_factor` times off the median of its kV class (bus voltage, else line voltage; both bus voltages set and different = `transformer` class) — mistyped kV, impedance read in the wrong unit |
| reserve level | a reservoir without `emin` (unset or 0) whose volume drives a `FlowRight` / `VolumeRight` `bound_rule` with a breakpoint above 0 (an agreement reserve level the LP may cross) |

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable` | bool | `true` | Master switch |
| `reward_fail_cost_ratio` | float | `1.0` | Reward / demand-fail-cost ratio above which a reward is flagged |
| `profile_tolerance` | float | `1e-6` | Slack on `[0, 1]` |
| `line_x_outlier_factor` | float | `50` | Allowed ratio to the kV-class median of `|X|/V²` |
| `line_x_min_class_size` | int | `5` | Minimum lines in a kV class for the outlier test |
| `line_x_pu_max` | float | `5.0` | Reactance ceiling [p.u., 100 MVA base] |
| `line_xr_max` | float | `100` | X/R ceiling |
| `max_offenders` | int | `5` | Offenders named per summary line |

```json
{ "options": { "sanity_options": { "line_xr_max": 200, "max_offenders": 10 } } }
```

## SddpOptions Fields

See [SDDP Method](methods/sddp.md) for full documentation with examples.

### Iteration Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_iterations` | int | `100` | Maximum forward/backward iterations |
| `min_iterations` | int | `2` | Minimum iterations before convergence is checked |
| `convergence_tol` | float | `1e-4` | Relative gap tolerance for convergence |

### Convergence Criteria

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `convergence_mode` | string | `"statistical"` | Criterion mode: `"gap_only"`, `"gap_stationary"`, `"statistical"` |
| `stationary_tol` | float | `0.01` | Tolerance for stationary-gap convergence (0 = disabled) |
| `stationary_window` | int | `10` | Look-back window for stationary-gap check |
| `convergence_confidence` | float | `0.95` | Confidence level for PLP-style statistical convergence (0 = disabled) |

### Cuts and Recovery

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cut_directory` | string | `"cuts"` | Directory for Benders cut files |
| `cut_sharing_mode` | string | `"none"` | Cut sharing: `"none"`, `"expected"`, `"accumulate"`, `"max"` |
| `max_cuts_per_phase` | int | `0` | Maximum stored cuts per (scene, phase) (0 = unlimited) |
| `cut_prune_interval` | int | `10` | Iterations between cut pruning passes |
| `prune_dual_threshold` | float | `1e-8` | Dual threshold for inactive cut detection |
| `single_cut_storage` | bool | `false` | Use single-cut storage mode |
| `max_stored_cuts` | int | `0` | Maximum total stored cuts per scene (0 = unlimited) |
| `save_per_iteration` | bool | `true` | Save cuts after every iteration (not just at end) |
| `cut_recovery_mode` | string | `"none"` | Cut persistence: `"none"`, `"keep"`, `"append"`, `"replace"` |
| `recovery_mode` | string | `"none"` | Recovery from previous run: `"none"`, `"cuts"`, `"full"` |
| `cuts_input_file` | string | -- | CSV file for hot-start cuts |
| `boundary_cuts_file` | string | -- | Deprecated alias → `future_cost_array[].cuts_file` (sddp/cascade only) |
| `boundary_cuts_mode` | string | -- | Deprecated alias → `future_cost_array[].mode` (sddp/cascade only) |
| `boundary_cut_sharing_mode` | string | -- | Deprecated alias → `future_cost_array[].sharing` (sddp/cascade only) |
| `boundary_cuts_mean_shift` | bool | -- | Deprecated alias → `future_cost_array[].mean_shift` (sddp/cascade only) |
| `boundary_max_iterations` | int | -- | Deprecated alias → `future_cost_array[].iteration_limit` (sddp/cascade only) |
| `missing_cut_var_mode` | string | -- | Deprecated alias → `future_cost_array[].missing_cut_var_mode` (sddp/cascade only) |

The FCF boundary cuts are configured on the FutureCost element
(`system.future_cost_array`, see
[Input Data — Future Cost](input-data.md#326-future-cost)).

### Feasibility and Elastic Filter

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `elastic_penalty` | float | `1e2` | Penalty for elastic slack variables in feasibility |
| `elastic_mode` | string | `"chinneck"` | Elastic filter mode: `"chinneck"` (alias `"iis"`), `"single_cut"` (alias `"cut"`), `"multi_cut"`, `"state_repair"` (alias `"plp"`), `"farkas_recursive"` (see `docs/methods/sddp.md` §5.4) |
| `fact_eps` | float | `1e-8` | PLP FactEPS tolerance for `state_repair` / `farkas_recursive` |
| `fact_max_cycles` | int | `500` | PLP FactMXC per-(scene, phase) solve-cycle cap for `state_repair` / `farkas_recursive` |
| `fcut_log` | bool | `false` | Write the PLP-style feasibility-cut debug log `gtopt_fcut.log` (`plpfact.log` analogue) to `log_directory`; CLI shorthand `--fcut-log` (see `docs/methods/sddp.md` §5.4) |
| `multi_cut_threshold` | int | `3` | Persistent cumulative infeasibility count at a (scene, phase) before switching to multi_cut (0 = always; <0 = disabled) |

### Apertures (Backward-Pass Sampling)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `apertures` | array of UIDs | -- | Aperture UIDs (absent = from Phase, `[]` = pure Benders) |
| `aperture_directory` | string | -- | Alternate data directory for aperture scenarios |
| `aperture_timeout` | float | `30.0` | Per-aperture LP solve timeout in seconds (0 = no limit) |
| `save_aperture_lp` | bool | `false` | Save LP files for infeasible apertures to log directory |
| `aperture_chunk_mode` | string | `"per_scene"` | Backward aperture grouping into pool tasks: `"per_scene"` (all apertures of a (scene, phase) in series on one LP clone, warm-start reuse; parallelism capped at the scene count), `"per_aperture"` (one task per aperture, fresh clone, cold solve), `"chunked"` (`aperture_chunk_size` = K per task). CLI: `--aperture-chunk-mode` (see `docs/methods/sddp.md`, "Aperture task grouping") |
| `aperture_chunk_size` | int | -- | K apertures per task for `aperture_chunk_mode = "chunked"` (unset = auto formula). Deprecated as a mode selector when no mode is set: `-1` → `per_scene`, `1` → `per_aperture`, `K > 1` → `chunked` (warning logged); `0` → default `per_scene`. CLI: `--aperture-chunk-size` |

### LP Updates and State Variables

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `update_lp_skip` | int | `0` | Iterations to skip between LP coefficient updates (0 = every iteration) |
| `state_variable_lookup_mode` | string | `"warm_start"` | Volume lookup for LP updates: `"warm_start"` or `"cross_phase"` |
| `low_memory_mode` | string | `"compress"` (SDDP/cascade) / `"off"` (monolithic) | One of `off` or `compress` (`snapshot` and `rebuild` are back-compat aliases for `compress`; see SDDP docs §7.6) |
| `memory_codec` | string | `"auto"` | Compression codec for `compress` mode (`auto` = zstd level 1, `lz4`, `snappy`, `zstd`, `gzip`, `none`) |

### Work Pool Swap Gate

These two fields live in `sddp_options` but apply to **every** work pool
of the run (LP build, SDDP forward/backward, monolithic, output writing).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pool_max_process_swap_mb` | float | `2048` | Process swap usage (`VmSwap`, MB) at/above which the pool throttles task dispatch — but **only while `VmSwap` is still growing** (see next field). `0` = gate off. CLI: `--swap-limit` |
| `pool_swap_growth_mb` | float | `64` | `VmSwap` growth (MB per 5 s window) above which the process counts as actively swapping. `0` = any net growth counts. CLI: `--swap-growth` |

`VmSwap` counts pages that *are* on swap, not pages being moved.  Cold
pages the kernel evicted because of other load on the host stay there
with no swap I/O at all, so the gate looks at the growth of `VmSwap`
rather than its level: a process with a large but static `VmSwap` is
never throttled.  The pool logs one `swap gate ENGAGED` line (info) when
the gate closes and one `swap gate RELEASED` line, with the reason, when
it opens again (one quiet 5 s window).  Growth is a per-process signal,
so paging caused by other tenants of a shared host does not throttle
this run; the host-wide swap I/O rate is not used.

### Monitoring and Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_enabled` | bool | `true` | Write JSON status file each iteration (for monitoring tools) |
| `sentinel_file` | string | -- | File path; if it exists, solver stops gracefully after current iteration |
| `simulation_mode` | bool | `false` | Skip training (max_iterations=0), run forward-only policy evaluation |

### Per-Pass Solver Options

| Field | Type | Description |
|-------|------|-------------|
| `forward_solver_options` | `SolverOptions` | LP solver overrides for SDDP forward pass |
| `backward_solver_options` | `SolverOptions` | LP solver overrides for SDDP backward pass |

## Solver Options Precedence

Each planning method can specify per-pass solver options that override the
global `solver_options`.  The merge chain (highest priority first):

| Method | Merge chain |
|--------|-------------|
| Monolithic | `monolithic_options.solver_options` -> `solver_options` |
| SDDP forward | `sddp_options.forward_solver_options` -> `solver_options` |
| SDDP backward | `sddp_options.backward_solver_options` -> `solver_options` |

For each field in `SolverOptions`, the per-pass value is used if set;
otherwise the global `solver_options` value applies.

### JSON Example

```json
{
  "options": {
    "solver_options": {
      "algorithm": 3,
      "threads": 4,
      "optimal_eps": 1e-8
    },
    "sddp_options": {
      "forward_solver_options": {
        "algorithm": 1
      },
      "backward_solver_options": {
        "threads": 1
      }
    }
  }
}
```

In this example, the forward pass uses algorithm 1 (primal) with 4 threads
and 1e-8 tolerance (from global), while the backward pass uses algorithm 3
(barrier) with 1 thread and 1e-8 tolerance.

## Variable Scales Precedence

Variable scaling factors are resolved in this order (highest priority first):

1. Global options (`scale_theta` for bus voltage angles; it ENLARGES θ,
   θ_LP = scale_theta · θ, so its `Bus.theta` entry is `1 / scale_theta`)
2. `variable_scales` entries matching by `(class_name, variable, uid)`
3. `variable_scales` entries matching by `(class_name, variable, uid=-1)` (wildcard)
4. Default scale = 1.0

### JSON Example

```json
{
  "options": {
    "model_options": {
      "scale_theta": 1000
    },
    "variable_scales": [
      {"class_name": "Reservoir", "variable": "energy", "uid": -1, "scale": 1000.0},
      {"class_name": "Battery", "variable": "energy", "uid": 1, "scale": 10.0}
    ]
  }
}
```

## Merge Semantics

When multiple JSON files are passed to GTOpt, their `"options"` sections are
merged left to right.  For each field:

- **Optional scalars**: the last file that sets the field wins
  (unset fields in later files leave earlier values alone)
- **Sub-objects**: merged recursively (same rule per field)
- **Non-optional `solver_options` fields** (`algorithm`, `threads`,
  `presolve`, `log_level`): the first file wins
- **`variable_scales`**: appended (later files add entries, earlier entries
  take precedence for the same key)

### Precedence layers

The more explicit, the closer to the run, wins:

1. **Dedicated CLI flags** (`--solver highs`, `--output-format csv`)
2. **`--set`** (`--set use_single_bus=true`; the last `--set` of a key wins)
3. **JSON files** (last file that sets a field wins, see above)
4. **Config file** (`.gtopt.conf` `[gtopt]` section; only fills fields the
   layers above left unset)
5. **Built-in defaults** (`PlanningOptionsLP` compile-time defaults)

## MainOptions (CLI-only)

`MainOptions` is used only by the standalone binary and is not part of the
JSON interface.  It carries all CLI-parsed values plus the list of planning
files.

| Field | CLI flag | Description |
|-------|----------|-------------|
| `planning_files` | positional / `-s` | System JSON file paths |
| `solver` | `--solver` | LP solver backend |
| `method` | `--method` | Planning method |
| `demand_fail_cost` | `--demand-fail-cost` | Unserved demand penalty |
| `scale_objective` | `--scale-objective` | Objective scaling factor |
| *(any option)* | `--set key=value` | Set any planning option (see below) |

> **`--set` key=value**: all other options are now set via `--set`.
> Examples: `--set use_single_bus=true`, `--set output_directory=results/`,
> `--set solver_options.algorithm=barrier`,
> `--set sddp_options.max_iterations=300`.
>
> **Deprecated aliases** (still work, emit a warning): `-b`, `-k`,
> `-D`, `-F`, `-d`, `-f`, `-C`, `--algorithm`, `--threads`,
> `--sddp-max-iterations`, `--sddp-min-iterations`,
> `--sddp-convergence-tol`, `--lp-debug`, `--log-directory`,
> `--cut-directory`, `--lp-compression`, `--lp-coeff-ratio`.

## Full JSON Example

```json
{
  "options": {
    "input_directory": "system_data",
    "input_format": "parquet",
    "output_directory": "output",
    "output_format": "parquet",
    "output_compression": "zstd",
    "method": "sddp",
    "lp_debug": false,
    "model_options": {
      "demand_fail_cost": 1000,
      "use_kirchhoff": true,
      "use_single_bus": false,
      "use_line_losses": true,
      "scale_objective": 1000
    },
    "solver_options": {
      "algorithm": 3,
      "threads": 4,
      "optimal_eps": 1e-8,
      "feasible_eps": 1e-8
    },
    "sddp_options": {
      "max_iterations": 200,
      "convergence_tol": 1e-4,
      "forward_solver_options": {
        "algorithm": 1
      }
    },
    "lp_matrix_options": {
      "equilibration_method": "ruiz",
      "compute_stats": true
    },
    "variable_scales": [
      {"class_name": "Reservoir", "variable": "energy", "uid": -1, "scale": 1000.0}
    ]
  },
  "simulation": {
    "annual_discount_rate": 0.1
  },
  "system": {
    "future_cost_array": [
      {
        "uid": 1,
        "name": "fcf",
        "cuts_file": "boundary_cuts.csv",
        "valuation": "end_of_horizon"
      }
    ]
  }
}
```

## See Also

- [Usage Guide](usage.md) -- CLI reference and examples
- [Planning Guide](planning-guide.md) -- Step-by-step planning guide
- [SDDP Method](methods/sddp.md) -- SDDP solver configuration
- [Cascade Method](methods/cascade.md) -- Cascade solver configuration
- [Monolithic Method](methods/monolithic.md) -- Monolithic solver configuration


### Water-row dual conditioning (`water_dual_target`) — EXPERIMENTAL

**Experimental, off by default.**  Measured on CEN cl5y (3 SDDP iterations,
CPLEX): exact (same LB at iteration 1, unit tests over none/row_max/ruiz) but
no gain in kappa or solver effort with one factor per reservoir, and a
clamp of 1e3 raises the coefficient ratio from ~5e5 to ~5e8.  Keep `k` small.

Reservoir energy-balance rows carry the water value, typically 1e3-1e5
`$/hm3` once boundary / Benders cuts are present, i.e. LP duals 2-3 decades
above the `[1, 100]` band solvers handle best.  Row-max / Ruiz scale rows
by their *coefficients*, not by dual size, and undo any pre-scaling, so the
correction is applied **after equilibration**: the row is multiplied by `c`
(coefficients and bounds) and its stored row scale divided by `c`.

- LP dual becomes `y_LP / c`; the physical dual
  `y_LP * scale_objective / row_scale`, SDDP cut coefficients, outputs and
  saved cuts are unchanged (exactness tests in
  `test_sddp_cut_exactness.cpp`).
- `c = clamp(expected|y_LP| / water_dual_target, 1/k, k)` with
  `expected|y_LP| = water_value_hint * prob*disc * row_scale / scale_objective`.
- Per reservoir: `water_value_hint` (`$/hm3`, default = the state-violation
  cost) and `dual_scale` (explicit `c`, overrides the auto rule).
- Trade-off: the row's coefficients now have max `c` instead of 1 after
  equilibration (spread vs. the junction rows grows by `c`); keep `k`
  small (default 1e3 is an upper bound, 1e1-1e2 is safer).


### KVL row dual conditioning (`kvl_row_scale`) — EXPERIMENTAL

Node-angle KVL rows (`Line.theta`) have duals `dLMP / x_tau`
(1e5-1e7 on congested 220/500 kV corridors).  With `kvl_row_scale` each row
is multiplied after equilibration by `c = clamp(x_ref / x_tau, 1/k, k)`
(same exact mechanism as `water_dual_target`: stored row scale divided by
`c`, physical duals / cuts / outputs unchanged).  Because `y * x_tau` is
invariant per flow column, shrinking the dual by `1/x_tau` inflates the
row's coefficients by the same factor, so only a mild `x_ref` is useful.
**Measured on cl5y the first-order lever is not this option but
`scale_theta`: leave it UNSET (auto = 1 / (3 median(X/V²))).**  A case that
pins `scale_theta: 1` (older plp2gtopt conversions) runs with ~10x higher
kappa.
