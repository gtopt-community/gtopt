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
| `output_round_decimals` | int | `7` | Round the written value columns to N decimals (better zstd ratio); `0` or a negative value writes full precision. CLI: `--set output_round_decimals=N` |
| `write_out` | string | all | Which result kinds to write: comma list of `solution` (`sol`), `dual`, `rc` (reduced cost), optionally restricted to element classes, e.g. `"sol,dual,rc:Generator,Line"`. CLI: `--write-out` |

### Model parameters (moved to `model_options`)

The flat model keys that used to live directly under `options`
(`demand_fail_cost`, `reserve_fail_cost`, `hydro_fail_cost`,
`hydro_use_value`, `use_line_losses`, `loss_segments`, `use_kirchhoff`,
`use_single_bus`, `kirchhoff_threshold`, `scale_objective`,
`scale_theta`) were **removed** from the JSON contract: a planning file
that still places them at the top level of `options` is rejected.  Put
them inside `options.model_options` (see
[ModelOptions Fields](#modeloptions-fields)).  Two of them were also
renamed: `reserve_fail_cost` → `reserve_shortage_cost` and
`hydro_fail_cost` → `hydro_spill_cost`.

On the command line the short keys keep working: `--set
demand_fail_cost=5000` is rewritten to
`--set model_options.demand_fail_cost=5000` (and `reserve_fail_cost` /
`hydro_fail_cost` to the new names).

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
| `gas_options` | `GasOptions` | Gas-network physical constants and the Weymouth SLP loop (see [Input Data — Gas Network](input-data.md#327-gas-network)) |
| `variable_scales` | `VariableScale[]` | Per-class/variable LP scale overrides |

> **Migration**: model parameter fields (`use_kirchhoff`, `use_single_bus`,
> etc.) belong in the `model_options` sub-object; the former flat copies at
> the top level of `options` are rejected (see
> [Model parameters](#model-parameters-moved-to-model_options)).

## ModelOptions Fields

The `model_options` sub-object is the **canonical location** for
LP-construction parameters.  It enables per-level overrides in cascade
solver configurations.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `use_single_bus` | bool | `false` | Collapse network to single bus (copper-plate) |
| `use_kirchhoff` | bool | `true` | Apply DC Kirchhoff voltage-law constraints |
| `use_line_losses` | bool | `true` | **Deprecated** — use `line_losses_mode` instead. Model resistive line losses (on/off) |
| `line_losses_mode` | string | `"adaptive"` | Line loss formulation: `"none"`, `"linear"`, `"piecewise"`, `"bidirectional"`, `"adaptive"` (piecewise for fixed lines, bidirectional for expandable ones), `"dynamic"` (falls back to piecewise), `"piecewise_direct"`, `"tangent_signed_flow"`. Per-line `Line.line_losses_mode` overrides. See [formulation §5.5](formulation/mathematical-formulation.md) |
| `kirchhoff_threshold` | float | `0.0` | Min bus voltage [kV] for Kirchhoff activation |
| `kirchhoff_mode` | string | `"cycle_basis"` | KVL formulation: `"cycle_basis"` (loop flow) or `"node_angle"` (B–θ) |
| `dc_line_reactance_threshold` | float | `1e-6` | Per-unit floor `\|X/V²\|` below which a line's KVL flow term is dropped (see `tiny_reactance_mode`); `0` disables |
| `tiny_reactance_mode` | string | `"tie"` | Lines with `reactance` 0 or below `dc_line_reactance_threshold`: `"tie"` = zero-impedance tie, KVL forces the two bus angles equal (`θ_a − θ_b = φ`), flow free within `tmax`; `"dc"` = legacy free-flow DC link. Lines without `reactance` are always DC links |
| `tiny_reactance_rel_threshold` | float | `0.01` | Relative tie floor: tie when `\|X/V²\|` < max(`dc_line_reactance_threshold`, this × median `\|X/V²\|`); `0` = absolute floor only |
| `tie_line_losses` | bool | `false` | `false`: zero-impedance ties are lossless; `true`: legacy, ties keep their loss model |
| `loss_segment_bound` | bool | `true` | Ordered-fill upper bound on PWL loss segment columns (valid tightening cut); `false` = width-only bounds |
| `loss_segments` | int | `1` | Piecewise-linear segments for quadratic losses |
| `loss_cost_eps` | float | `0.1` | $/MWh on line-loss columns (degeneracy breaker / negative-LMP arbitrage guard). Per-line `Line.loss_cost_eps` overrides; `0` disables. Inert for `none`/`linear`/`piecewise_direct` |
| `loss_flow_abs_cost` | float | `0.001` | $/MWh (real units, before `scale_objective`) on the `tangent_signed_flow` abs-flow proxy columns `v` (Σv ≥ \|f\|). Degeneracy pin forcing Σv = \|f\| at the optimum, which keeps the secant chord on the losses anchored. Shifts LMP differences by at most ≈ its value per lossy line (flow direction). Applied only when `loss_cost_eps > 0`. Per-line `Line.loss_flow_abs_cost` overrides; `0` disables; `1e-6` reproduces the pre-option internal pin |
| `loss_overflow_cost` | float | `demand_fail_cost` | $/MWh on the loss carried by the overflow tier past a decoupled `Line.loss_envelope` (flow between the envelope and `tmax`); stops the tier from being a free energy sink under negative bus prices. Genuine overloads pay it on their loss above `R·env²/V²`. Per-line `Line.loss_overflow_cost` overrides; `0` disables. Unset default: the case's `demand_fail_cost` (global `model_options` value; per-demand `fcost` is not consulted), itself 1000 when unset; 1000 with a warning when it is an explicit 0. Inert without headroom past the envelope |
| `prev_stage_link` | string | `stage` | What a user-constraint `<attr>_prev` term reads at the first block of a stage whose previous stage is in the same phase: `stage` = that stage's last block; `none` = the constant 0 (legacy). The horizon start always reads `initial_<attr>` (e.g. `Generator.initial_generation`); a later-phase start reads 0 (see user-constraints.md). Also `--set model_options.prev_stage_link=none` |
| `line_thermal_limit_mode` | string | `flow` | How the line thermal limit treats losses: `flow` (flow variable only, no extra row), `sending` (sending-end power `f + a·ℓ ≤ tmax`, `a` = sending-bus loss share), `full` (`f + ℓ ≤ tmax` per direction; PyPSA convention). Per-line `Line.line_thermal_limit_mode` overrides. Also `--set model_options.line_thermal_limit_mode=full` |
| `overload_slack_bound` | string | `thermal` | Upper bound of the line overload slack (`overload_penalty` band): `thermal` = `min(hard − normal, max_overload_ratio × normal)` (DLR: IEEE 738 emergency − normal rating), `ratio` = `max_overload_ratio × normal`, `unbounded` = no bound. Per-line `Line.overload_slack_bound` overrides |
| `max_overload_ratio` | number | `1.5` | Overload headroom as a fraction of the normal threshold (slack ≤ +150 %, flow ≤ 2.5 × normal). Per-line `Line.max_overload_ratio` overrides |
| `use_line_dlr` | bool | `true` | Global switch for the dynamic line rating: `false` ignores every `Line.dlr` block and the lines keep their static `tmax` (static-vs-DLR comparisons). DLR via critical spans (Matus et al. 2012, DOI [10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254); Open access: [ResearchGate](https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating), [PDF (Universidad de Chile)](https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)) and IEEE Std 738-2012 ampacity. Also `--set model_options.use_line_dlr=false` |
| `dlr_limit_mode` | string | unset | How a `Line.dlr` rating combines with the static limits: `replace` = the DLR rating replaces `conductor_rating_*` (without one, `max_rating`/`min_rating`) and `tmax_normal_*`; `min` = `min(static, DLR rating)` in every block. Unset: `replace` on lines with a `conductor_rating_*`, `min` otherwise (historical `min(tmax, DLR)`). Flow, outage, equipment and security limits always apply. Per-line `Line.dlr.limit_mode` overrides |
| `scale_objective` | float | `1000` | Objective coefficient divisor |
| `scale_theta` | float | auto | Voltage-angle enlargement: θ_LP = scale_theta · θ (rad); auto = 1/(3·median(X/V²)); 1 = radians; 1000 = milliradians. `auto_scale = false` and unset ⇒ 1. Values < 1 shrink θ and log a warning (the meaning was inverted before 2026-11). A manual value always wins over auto (`theta_max` is still auto-computed); set it with `--set model_options.scale_theta=1000` or the converters' opt-in `--scale-theta FACTOR` (`plp2gtopt`, `plexos2gtopt`), which emit nothing by default |
| `demand_fail_cost` | float | `1000` | Penalty $/MWh for unserved demand (value of lost load) for every demand without its own `fcost`. `0` = unpenalized (freely sheddable) load; for non-sheddable load set `Demand.forced = true`. See [Unserved-demand cost](input-data.md#unserved-demand-cost-and-hard-demand) |
| `demand_fail_segments` | array | -- | Default stepwise curtailment curve `[{fraction, cost}, …]` (scalar costs) for demands with neither `fcost` nor `fail_segments`; takes precedence over `demand_fail_cost`. See input-data §3.3 |
| `battery_annual_standing_loss` | float | `0.48` | Self-discharge [p.u./year] for every Battery whose `annual_standing_loss` is unset (4 %/month, Li-ion cell + BMS; linear hourly drain = value/8760). `0` restores the legacy lossless default. Explicit per-battery values win; Reservoir/other storage unaffected |
| `reserve_coupling` | string | `"warn"` | How `validate_planning` treats a ReserveZone requirement coupled to dispatch (endogenous load term, `flow_coef`, endogenous inertia, commitment inertia with a relaxed Commitment, line contingencies, `contingency_includes_reserve`): `"warn"`, `"error"` (reject the case) or `"off"`.  Such a coupling puts a $\beta\mu$ credit into the LMPs and can price buses negative (formulation §5.9) |
| `battery_power_envelope` | bool | `true` | Emit `p_charge/Pc_max + p_discharge/Pd_max ≤ 1` per block on every battery converter (`Converter/power_envelope`), cutting simultaneous full-power charge + discharge |
| `reserve_shortage_cost` | float | -- | Penalty $/MWh for unserved reserve (formerly `reserve_fail_cost`) |
| `hydro_spill_cost` | float | -- | Default penalty $/m³ for unmet hydro rights (falls back to `0.0` when unset; formerly `hydro_fail_cost`). Overridden by per-element `fail_cost` |
| `hydro_use_value` | float | -- | Default benefit $/m³ for exercising hydro rights (falls back to `0.0` when unset). Overridden by per-element `use_value` |
| `state_violation_cost` | float | -- | Penalty $/MWh for state-variable violations in the SDDP elastic filter (formerly `state_fail_cost`). Fallback when an element (reservoir, etc.) does not define its own `scost`. Converted to physical units via the element's `mean_production_factor` |
| `demand_fail_rhs_shift` | bool | `false` | Write unserved demand as `load − lmax ∈ [−lmax, 0]` and move the `fail_cost·lmax` baseline into the bus-balance / capacity right-hand sides instead of the objective constant (same optimum, no large constant). Legacy alias `demand_option_c` |
| `allow_oversupply` | bool | `false` | Relax the bus balance to `≥` (free disposal of surplus energy): over-supplied buses price at 0 instead of negative. Changes the reported LMPs |
| `strict_storage_emin` | bool | `true` | Enforce the per-stage reservoir `emin` as a hard bound on the stage-start and stage-end volumes; `false` = PLP style (only the energy balance, SDDP keeps the trajectory above `emin`) |
| `lp_reduction` | bool | `false` | Skip provably-zero columns / rows at assembly (e.g. a generator with `pmax = pmin = 0` and its commitment). Helps solvers with a weak presolve; can shift the optimum slightly because such units also lose their commitment. CLI: `--lp-reduction` |
| `auto_scale` | bool | `true` | Automatic per-element scaling (reservoir energy / flow, LNG energy, bus angle). `false` (CLI `--no-scale`) keeps raw physical units (debugging; much higher kappa) |
| `theta_max` | float | auto | Bound of the voltage-angle columns, `θ ∈ [−theta_max, theta_max]`. Auto: `Σ tmax·x_τ` over the lines (never caps a flow below `tmax`); `2π` when `auto_scale = false` |
| `dc_line_resistance_threshold` | float | `1e-7` | Per-unit floor `\|R/V²\|` below which a line is lossless (its loss model is dropped). `0` disables |
| `scale_loss_link` | float | auto | Row factor of the loss link row `loss − Σ loss_k·seg_k = 0`; auto = a power of 10 from `median(R/V²)`; `1` disables. Not used by `linear`, `piecewise_direct`, `none` |
| `loss_secant_segments` | int | `1` | `tangent_signed_flow` only: secant segments per line and block (with `loss_use_sos2`). Per-line `Line.loss_secant_segments` overrides |
| `loss_use_sos2` | bool | `false` | `tangent_signed_flow` only: SOS2 ordering of the secant segments (needs a solver with native SOS2). Per-line `Line.loss_use_sos2` overrides |
| `naming_dialect` | string | -- | Naming dialect to enforce (`gtopt`, `plp`, `sddp`, `plexos`, `pypsa`, `pandapower`; see `share/gtopt/naming_dialects.json`): warns on keys from another dialect and writes `planning.json` keys in that dialect. CLI: `--naming-dialect` |
| `objective_mode` | string | `"cost"` | `"emissions"` minimises total emissions: generator dispatch cost coefficients (`gcost`, fuel) are zeroed and each emission source is charged at its zone `price` (1 $/t when unset). Emission prices and caps are not model options; set `price`, `cap` and `cap_cost` on an `EmissionZone` (see [Emissions](emissions.md)). The former `emission_cost` / `emission_cap` options were removed |
| `continuous_phases` | string | `"none"` | Phase-range expression controlling which phases run LP-relaxed (integers become continuous). Syntax: `"all"`, `"none"`, `"0"`, `"1,3:5,8:"`, `":3"`. Settable per cascade level |
| `pricing_mode` | string | `"fixed"` | MIP only: which LP supplies the duals (nodal prices) after the MIP; the dispatch is the same in every mode. `"fixed"` = all integers fixed at the incumbent (the polish LP); `"relaxed"` = LP relaxation of the full MIP; `"fast_start"` (alias `"fast-start"`) = integers fixed except fast-start commitments, whose status is relaxed to [0, 1]. A non-optimal pricing LP falls back to `fixed` with a warning; every (scene, phase) is recorded in `pricing.csv`. CLI: `--pricing-mode`. See formulation §5.15 "Pricing" |
| `transition_integrality` | string | `"auto"` | Integrality of the commitment startup / shutdown columns (v / w) on a MIP build: `"auto"` = binary (exact); `"continuous"` = always continuous in [0, 1] (not exact with start / stop ramps); `"binary"` = always binary. The status u is always integer; `relax`, `continuous_phases` and `--no-mip` relax u, v, w together. `Commitment.transition_integrality` overrides it per unit |
| `startup_tier_integrality` | string | `"continuous"` | Integrality of the commitment hot / warm / cold start selectors: `"continuous"` (the tier rows are totally unimodular once v / w are integral) or `"integer"`. Integer selectors are relaxed with u in `pricing_mode = "fast_start"`. `Commitment.startup_tier_integrality` overrides it per unit |
| `fast_start_max_hours` | float | `1` | Auto rule for `pricing_mode = "fast_start"`: a commitment without an explicit `fast_start` is fast-start when `min_up_time` and `min_down_time` are both ≤ this value [h] (unset = 0). Must be ≥ 0 |
| `uc_formulation` | string | `"energy_block"` | Unit-commitment formulation: `"energy_block"` (block-average power) or `"power_based"` (power at block boundaries, trapezoid energy, start-up / shut-down trajectories; Morales-España et al.). `Commitment.uc_formulation` overrides it per unit. See [Unit Commitment §4.6](unit-commitment.md#46-power-based-formulation-uc_formulation--power_based) |
| `ramp_formula` | string | `"backward"` | Ramp discretisation: `"backward"` (interval slope; on point stages the step is the node spacing `t_k − t_{k−1}`) or `"central2"` (second-order node derivative; opt-in, not recommended, warns) |
| `ramp_wrap` | bool | `false` | `central2` only: wrap the first / last node around the period |
| `long_transition` | string | -- | Planning default of `Commitment.long_transition` (`"on"`, `"off"`, `"no_uc"`) for cyclic units whose min up / down time or start window exceeds the typical period. Unset: such undeclared units are a validation error |
| `transition_status_rows` | bool | -- | **Removed**: the rows `v ≤ u` / `w ≤ 1 − u` no longer exist (binary v / w make them redundant). Still parsed so old files load; a warning is logged and the value ignored |
| `storage_integration` | string | per type | Point-sampled stages only (`Stage.sampling = "point"`; block stages always use `block`): storage-balance time integration. `block` (alias `cc` / `clenshaw_curtis`: block weights, which are the Clenshaw–Curtis weights on a Chebyshev grid), `trapezoid`, `lg4` / `lg6` (banded Lagrange rules), `lg4_pos` / `lg6_pos` (same with non-negative weights). Resolution: element `storage_integration` → `reservoir_storage_integration` / `battery_storage_integration` → this value → `lg4_pos` (reservoirs) / `block` (other storages). See [Typical-week sampling](methods/typical-week-sampling.md) |
| `reservoir_storage_integration` | string | `"lg4_pos"` | Point-stage rule of reservoirs (see `storage_integration`) |
| `battery_storage_integration` | string | `"block"` | Point-stage rule of batteries and the hydrogen / thermal / ammonia storages (see `storage_integration`) |

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
| `mip_gap` | float | -- | MIP only: target relative optimality gap (unset = solver default; HiGHS `mip_rel_gap`, CPLEX `EPGAP`, CBC `ratioGap`, …) |
| `mip_gap_abs` | float | -- | MIP only: target absolute gap in objective units (unset = solver default). Unlike `mip_gap`, insensitive to a large constant in the objective (e.g. a future-cost term) |
| `memory_emphasis` | bool | -- | Ask the backend for its memory-saving mode (CPLEX `MEMORYEMPHASIS`; other solvers ignore it). Unset leaves the solver default. Independent of `sddp_options.low_memory_mode` |
| `time_limit` | float | -- | Wall-clock budget in seconds for the whole solve of one LP/MIP (0 = no limit): MIP-start stages and algorithm-fallback retries share it — each retry gets the remaining time and retries stop once < 1 s is left.  For a MIP with `mip_polish` on, min(5 % of the budget, 300 s) is reserved for the integer-fixed LP re-solve (the polish gets the remaining time, at least 10 s) |
| `mip_polish` | bool | `true` | MIP only.  After the MIP stops — optimal, or non-optimal at a time / node / solution / memory limit or abort while holding a feasible incumbent — fix every integer column at its incumbent value and re-solve the remaining LP (CPLEX: `CPXPROB_FIXEDMILP`).  The reported objective, primal and duals are those of the fixed LP, which is never worse than the incumbent.  Runs whether or not duals are written.  A limit stop stays non-optimal: the run still exits 1 and `solution.csv` reports status `5` / `limit_polished`; the log prints the incumbent and polished objectives.  If the fixed LP fails, the incumbent is kept and a warning is logged.  `false` reports the raw incumbent (and, on a limit stop, no duals) |
| `scaling` | string | -- | Internal solver scaling strategy (nullopt = solver default): `"none"`, `"automatic"`, `"aggressive"` (or the codes `0` / `1` / `2`). See `SolverScaling` enum |
| `crossover` | string | `"auto"` | Barrier only. `"auto"`, `"primal"`, `"dual"`: cross over to a vertex (basis, vertex duals); `"none"`: keep the interior point (interior duals, no basis, the next solve starts cold). HiGHS has one crossover, so `primal` / `dual` act like `auto` there. The SDDP forward pass picks `none` itself when nothing needs its basis (see `docs/solvers.md`); an explicit value always wins. Applied per solve: a `none` solve does not leave later solves of the same LP without crossover; set `none` in every pass's options to keep it off everywhere |
| `barrier_solver` | string | `"auto"` | Which interior-point code runs `algorithm=barrier` (HiGHS only; other solvers ignore it). `"auto"`: HiPO (parallel) when HiGHS was built with HiPO, else IPX; `"hipo"`: HiPO (falls back to IPX like `auto`); `"ipx"`: IPX (serial), the barrier before HiPO builds. Also selects the IPM a HiGHS MIP falls back to (`mip_ipm_solver`) |
| `mip_lp_algorithm` | string | -- | MIP only: algorithm of the root LP relaxation, `"default"`, `"primal"`, `"dual"` or `"barrier"` (the `barrier_solver` IPM on HiGHS). Unset: the solver default (HiGHS `mip_lp_solver=choose`; CPLEX `StartAlgorithm` untouched). HiGHS `mip_lp_solver`, CPLEX `CPX_PARAM_STARTALG`; other solvers ignore it |
| `kappa_mode` | string | compiled default | Basis condition number read after each solve: `"estimate"` (HiGHS Hager estimate, CPLEX `CPX_KAPPA`), `"exact"` (HiGHS exact, one FTRAN per row, very slow on large LPs; CPLEX `CPX_EXACT_KAPPA`; for verification and studies), `"off"` (kappa is neither queried nor printed: no solver call, no `kappa` / `max_kappa` columns in `solution.csv`, no kappa in logs, stats or high-kappa warnings). Gurobi: `Kappa` / `KappaExact`; CLP: its dual-error proxy for both; MindOpt, SCIP, cuOpt have no kappa. Read once per solve. When unset: `$GTOPT_KAPPA_MODE`, else the compiled default (CMake `GTOPT_KAPPA_MODE_DEFAULT`; `auto` = `off` in release builds, `estimate` in Debug / CIFast; shown by `gtopt --version`). `run_gtopt --kappa-mode MODE` forwards it |
| `cold_solve_without_basis` | bool | `true` | HiGHS: when `advanced_basis` is requested but the LP holds no basis yet (first SDDP forward solve of a cell), solve cold with the normal settings (`algorithm`, `presolve`). `false` applies the warm settings anyway (simplex, presolve off), the behaviour before 2026-11, 2-3x slower on CEN cells |
| `deterministic_parallel` | bool | `false` | Reproducible multi-threaded solves. CPLEX: `true` sets `CPX_PARAM_PARALLELMODE` to deterministic, so the same LP, thread count and machine always give the same solution whatever the machine load; unset / `false` keeps opportunistic mode (faster, but on a degenerate LP the optimal basis, hence duals and reduced costs, can change from run to run under load). The e2e golden tests set it (`integration_test/cmake/run_gtopt.cmake`). Other solvers ignore it |
| `accept_optimal_infeas` | bool | `true` | CPLEX LP status `CPX_STAT_OPTIMAL_INFEAS` (5, "optimal with unscaled infeasibilities"). Unset / `true`: accepted as optimal with one warning per solve giving the max unscaled primal / dual infeasibility, no fallback re-solve. `false`: not optimal, so the `max_fallbacks` cycle re-solves and the cell fails when no attempt reaches OPTIMAL (the behaviour before this option). Other solvers ignore it |
| `mip_heuristic_effort` | float | -- | MIP only, HiGHS only: share of the MIP effort spent in primal heuristics, 0–1 (HiGHS default 0.05; 0.5 under `mip_start_effort: repair` unless this is set). Other solvers ignore it (use `param_file`) |
| `max_fallbacks` | int | `2` | On non-optimal exit, cycle through barrier → dual → primal up to this many times. `0` disables fallback. Each cycle logs one WARN line with every attempt's algorithm and native solver status (code and name) and the outcome |
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
| `mip_start` | `MipStartOptions` | off | Feasible MIP starting point for unit-commitment MIPs (LP relaxation, rounding, propagation dive, repair, injection; `enabled`, `relax`, `round`, `domain_rules`, `dive`, `scip_repair`, `inject`, `from_file`, `dump_file`, `seed_solution_file`, `skip_relaxation`, `root_basis_cache_file`). CLI: `--mip-start`. See [Monolithic §4.5](methods/monolithic.md#45-mip-warm-start-pipeline-mip_start) |

## CascadeOptions Fields

`cascade_options` holds `model_options`, `sddp_options` (global defaults
of every level; `max_iterations` is the global budget), `level_array`
(one `CascadeLevel` per level: `uid`, `name`, `active`, `model_options`,
`system_file`, `aperture_system_file`, `sddp_options`, `transition`) and
`write_level_outputs` (default `false`).  Each level's `sddp_options`
accepts the iteration, aperture, convergence and elastic fields plus its
own `forward_solver_options` / `backward_solver_options`.  Full
reference: [Cascade Method §4](methods/cascade.md#42-cascadeoptions-fields).

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
| `pin_logic_rows` | bool | `false` | Keep pure commitment-logic rows (u / v / w transition, exclusion, start-up tiers, start limits, converter charge / discharge exclusivity, plant unit counts with unit coefficients: ±1 rows on binary columns; not min up / down, whose window-length coefficient still needs balancing) out of row equilibration: their row factor stays 1 under `row_max`, `ruiz` and incremental `add_row`. Column scales still apply. Off by default: it left kappa unchanged on the benchmark cases and slowed some MIPs down considerably |
| `water_dual_target` | float | unset (off) | Target `\|y_LP\|` for reservoir energy-balance rows; see below |
| `water_dual_clamp` | float | `1e3` | Symmetric clamp `c in [1/k, k]` for the water-row factor |
| `kvl_row_scale` | bool | off | EXPERIMENTAL: post-equilibration factor on node-angle KVL rows; see below |
| `kvl_row_clamp` | float | `100` | Clamp `c in [1/k, k]` of the KVL row factor |
| `kvl_x_ref` | float | median(X/V²) | Reference `x_tau` of the KVL factor `c = x_ref / x_tau` (setting it also enables the factor) |
| `compute_stats` | bool | `false` | Compute and log coefficient min/max/ratio during `flatten()` |
| `label_meta_without_names` | bool | `false` | Keep each cell's label metadata even when LP names are off. Default drops it (diagnostics fall back to row / column indices); `true` restores the previous behaviour |
| `share_label_meta` | bool | `true` | Share the label metadata between a cell's LP and its flat-LP snapshot; `false` restores the previous per-LP copy |
| `drop_element_collections` | bool | `true` | SDDP / cascade under `low_memory_mode = off`: drop the disposable element wrappers after the build and rebuild them for output, as `compress` does; `false` keeps them resident (previous behaviour). Monolithic always keeps them |
| `lp_coeff_ratio_threshold` | float | `1e7` | When global max/min coefficient ratio exceeds this, print a per-scene/phase breakdown |
| `solver_name` | string | -- | Solver backend pin (`--solver` wins; beats `.gtopt.conf` `solver`, `$GTOPT_SOLVER` and plugin priority).  gtopt writes the solver it resolved here in `gtopt_state.json`, so replaying the snapshot uses the same backend |
| `validation` | `LpValidationOptions` | on | Incremental checks while the LP is assembled; one warning per offending entry, at most `max_warnings_per_kind` per kind. Fields: `enable` (`true`), `coeff_warn_max` (`1e10`), `coeff_warn_min` (`1e-10`, non-zero coefficients below it), `bound_warn_max` (`1e12`, finite bounds), `rhs_warn_max` (`1e12`), `obj_warn_max` (`1e10`), `max_warnings_per_kind` (`50`) |

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
| dead commitment | generators with `pmax` (or `capacity`, no `expcap`) `<= 0` over the whole horizon that still have a `Commitment` or `pmin > 0`; batteries with zero `pmax_charge` and `pmax_discharge` that keep `commitment` or a `pmin_*` |
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
| `min_iterations` | int | `1` | Minimum iterations before convergence is checked |
| `convergence_tol` | float | `0.01` | Relative gap tolerance; the stop criterion only when ΔUB stationarity is disabled (`stationary_tol = 0`) |

### Convergence Criteria

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `convergence_mode` | string | `"gap_stationary"` | Criterion mode: `"gap_only"`, `"gap_stationary"`, `"statistical"` (opt-in; needs `convergence_confidence > 0`) |
| `stationary_tol` | float | `0.005` | ΔUB stationarity tolerance (0 = disabled) |
| `stationary_window` | int | `4` | Look-back window (iterations) for the stationarity check |
| `stationary_gap_ceiling` | float | `0.02` | A stationary stop needs signed gap below this (negative passes) and `stationary_window` own iterations with cuts; `1.0` disables |
| `convergence_confidence` | float | `0.0` | Confidence level for the PLP-style statistical criterion (0 = disabled) |

### Cuts and Recovery

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cut_directory` | string | `"cuts"` | Directory for Benders cut files |
| `cut_sharing_mode` | string | `"none"` | Cut sharing between scenes: `"none"`, `"multicut"` (one α per source scene) or `"markov"` (experimental; see `markov_states` / `markov_transition` and [SDDP Markov](formulation/sddp-markov.md)). The removed `"expected"`, `"broadcast_mean"`, `"accumulate"`, `"max"` names are rejected |
| `markov_states` | int array | -- | `markov` only: Markov state of each scene, in `[0, M)` |
| `markov_transition` | float array | -- | `markov` only: row-major M×M row-stochastic transition matrix |
| `forward_sampling_mode` | string | `"persistent"` | `"persistent"` (each scene follows its own path) or `"resampled"` (probability-weighted re-draw at each phase boundary, matching the `multicut` bound) |
| `integer_cuts_mode` | string | `"none"` | Cuts on cells with integer columns: `"none"` or `"strengthened"` (LP-relaxation cut with a Lagrangian intercept from one MIP; needs a MIP-capable solver) |
| `cut_drain_mode` | string | `"iteration"` | Asynchronous runs: which in-flight cuts are kept after the stop signal: `"iteration"` (cuts of certified iterations; deterministic), `"count"`, `"all"` |
| `cut_coeff_eps` | float | `1e-8` | Drop cut coefficients below this absolute value (and their RHS term) |
| `scale_alpha` | float | `0` (auto) | Divisor of the future-cost column α (auto = the largest state-variable scale) |
| `alpha_min` | float | -- | Lower bound on α [$]; set `0` when every stage cost is non-negative. Not valid with negative-cost elements or `boundary_cuts_mean_shift` |
| `max_cuts_per_phase` | int | `0` | Maximum stored cuts per (scene, phase) (0 = unlimited) |
| `cut_prune_interval` | int | `10` | Iterations between cut pruning passes |
| `prune_dual_threshold` | float | `1e-8` | Dual threshold for inactive cut detection |
| `single_cut_storage` | bool | `false` | Use single-cut storage mode |
| `max_stored_cuts` | int | `0` | Maximum total stored cuts per scene (0 = unlimited) |
| `save_per_iteration` | bool | `true` | Save cuts after every iteration (not just at end) |
| `cut_recovery_mode` | string | `"none"` | Cut persistence: `"none"`, `"keep"`, `"append"`, `"replace"` |
| `recovery_mode` | string | `"full"` | Recovery from a previous run (with `cut_recovery_mode`): `"none"`, `"cuts"`, `"full"` |
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
| `elastic_penalty` | float | `1.0` | Penalty for elastic slack variables in feasibility |
| `elastic_mode` | string | `"farkas_recursive"` | Elastic filter mode: `"chinneck"` (alias `"iis"`), `"single_cut"` (alias `"cut"`), `"multi_cut"`, `"state_repair"` (alias `"plp"`), `"farkas_recursive"` (see `docs/methods/sddp.md` §5.4) |
| `fact_eps` | float | `1e-8` | PLP FactEPS tolerance for `state_repair` / `farkas_recursive` |
| `fact_max_cycles` | int | `500` | PLP FactMXC per-(scene, phase) solve-cycle cap for `state_repair` / `farkas_recursive` |
| `fcut_log` | bool | `false` | Write the PLP-style feasibility-cut debug log `gtopt_fcut.log` (`plpfact.log` analogue) to `log_directory`; CLI shorthand `--fcut-log` (see `docs/methods/sddp.md` §5.4) |
| `multi_cut_threshold` | int | `100` | Persistent cumulative infeasibility count at a (scene, phase) before switching to multi_cut (0 = always; <0 = disabled) |
| `terminal_failure_threshold` | int | `2` | Consecutive iterations in which the elastic filter produced no feasibility cut for a scene before that scene's forward pass is skipped until new cuts arrive (0 = off) |
| `forward_fail_stop` | bool | `false` | `true`: an infeasible phase installs its feasibility cut and ends the scene's forward pass for this iteration; `false`: PLP-style backtracking (re-solve the previous phase under the new cut) |
| `forward_infeas_rollback` | bool | `true` | Delete the cuts of a scene declared infeasible in the forward pass; the run aborts if every failed scene stalls without new cuts |
| `forward_max_fallbacks` | int | `solver_options.max_fallbacks` | Algorithm-fallback attempts for forward-pass solves |
| `backward_max_fallbacks` | int | `0` | Algorithm-fallback attempts for backward-pass and aperture solves (0 = fail at once) |
| `backward_resolve_target` | bool | `true` | Re-solve phase t at the trial state before building the cut for phase t−1, so cuts added earlier in the same backward pass are reflected |
| `max_async_spread` | int | `0` | Asynchronous scenes (with `cut_sharing_mode = none`): maximum iteration spread between the fastest and slowest scene; `0` = synchronous |

### Apertures (Backward-Pass Sampling)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `apertures` | array of UIDs | -- | Aperture UIDs (absent = from Phase, `[]` = pure Benders) |
| `aperture_directory` | string | -- | Alternate data directory for aperture scenarios |
| `num_apertures` | int | -- | First-N selector on each phase's `Phase.apertures` (unset = all, `0` = pure Benders). With the wettest-first order written by `plp2gtopt`, picks the N wettest |
| `aperture_selection_mode` | string | `"head"` | How `num_apertures` picks: `"head"` (first N), `"stride"` (alias `interleave` / `spread`: evenly spaced), `"tail"` (alias `last`: last N) |
| `aperture_system_file` | string | -- | Planning JSON whose `system` replaces the forward system in the backward aperture solves (a cheaper model with the same state elements). Overridden by `CascadeLevel` / `Phase.aperture_system_file` |
| `aperture_solve_mode` | string | `"warm"` | Per-aperture solve and cut recovery: `"cold"`, `"warm"` (re-optimise the chunk-resident basis), `"reduced_cost"` (barrier without crossover), `"dual_shared"` (one representative solve, the other cuts synthesised), `"screened"` (`dual_shared` plus exact re-solves of the loosest cuts). See [SDDP §4.4](methods/sddp.md) |
| `aperture_screen_count` | int | `2` | `screened` only: synthesised cuts re-solved exactly |
| `aperture_seed_basis` | bool | `false` | Seed each iteration's first aperture from the previous iteration's basis (vertex modes only) |
| `aperture_use_manual_clone` | bool | `true` | Build aperture clones from the flat-LP snapshot (`load_flat`) instead of the backend's native clone, so clones are built in parallel; falls back to the native clone when no snapshot is held |
| `aperture_drop_fcuts` | bool | -- | Deprecated and ignored (a warning is logged) |
| `aperture_timeout` | float | `30.0` | Per-aperture LP solve timeout in seconds (0 = no limit) |
| `save_aperture_lp` | bool | `false` | Save LP files for infeasible apertures to log directory |
| `aperture_chunk_mode` | string | `"per_scene"` | Backward aperture grouping into pool tasks: `"per_scene"` (all apertures of a (scene, phase) in series on one LP clone, warm-start reuse; parallelism capped at the scene count), `"per_aperture"` (one task per aperture, fresh clone, cold solve), `"chunked"` (`aperture_chunk_size` = K per task). CLI: `--aperture-chunk-mode` (see `docs/methods/sddp.md`, "Aperture task grouping") |
| `aperture_chunk_size` | int | -- | K apertures per task for `aperture_chunk_mode = "chunked"` (unset = auto formula). Deprecated as a mode selector when no mode is set: `-1` → `per_scene`, `1` → `per_aperture`, `K > 1` → `chunked` (warning logged); `0` → default `per_scene`. CLI: `--aperture-chunk-size` |
| `aperture_cut_resolve` | bool | `false` | Re-solve the source phase LP after installing each aperture-built cut (an optimality check only; nothing reads that solution). Off by default since 1.2.0 |

**Infeasible apertures.**  An infeasible (or timed-out) aperture is
dropped and the expected cut renormalised over the remaining ones.  With
a hard terminal target (`efin` without `efin_cost`) the model lacks
relatively complete recourse and the same aperture is dropped every
iteration: the LB is then over a rescaled distribution.  gtopt warns once
per (scene, phase, aperture) after 3 consecutive drops, reports
`apertures_dropped` per iteration, and never reports `converged` for an
iteration that dropped apertures.  For aperture runs prefer a soft `efin`
(`efin_cost`) — but a meaningful penalty may not be available, and with a
soft target the solution may never reach `efin` (it just pays the
penalty, so the target is silently unmet).  Details:
[SDDP §4.4, *Infeasible apertures*](methods/sddp.md#infeasible-apertures-drop-and-rescale).

### LP Updates and State Variables

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `update_lp_skip` | int | `0` | Iterations to skip between LP coefficient updates (0 = every iteration) |
| `state_variable_lookup_mode` | string | `"warm_start"` | Volume lookup for LP updates: `"warm_start"` or `"cross_phase"` |
| `low_memory_mode` | string | `"compress"` (SDDP/cascade) / `"off"` (monolithic) | One of `off` or `compress` (`snapshot` and `rebuild` are back-compat aliases for `compress`; see SDDP docs §7.6) |
| `memory_codec` | string | `"auto"` | Compression codec for `compress` mode (`auto` = zstd level 1, `lz4`, `snappy`, `zstd`, `gzip`, `none`) |
| `release_backends` | bool | `true` | Under `low_memory_mode = off`, release each solver backend after its solve and rebuild it from an uncompressed flat-LP snapshot on the next visit, warm-starting from the saved basis (same as `compress` with `memory_codec = uncompressed`). `false` keeps every LP live (the behaviour before this became the default) |
| `release_after_alpha` | bool | `true` | Release each low-memory backend right after α registration; `false` keeps them live until the post-initialisation sweep (previous behaviour) |
| `free_solver_scratch` | bool | `true` | Under `off`, free the CPLEX / Gurobi / MindOpt copy-out getter buffers after each forward solve and each iteration; `false` keeps them (previous behaviour) |
| `basis_cross_mode` | string | `"full_cross"` | Simplex-basis reuse between the forward and backward solves of a (scene, phase): `off`, `warm`, `forward_to_backward`, `backward_to_forward`, `full_cross`. Correctness-neutral |
| `lp_debug_passes` | string | `"forward,aperture"` | With `lp_debug = true`: SDDP passes whose LPs are dumped (`forward`, `backward`, `aperture`, `all`) |

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

#### Forward-pass crossover

With a barrier (or solver-default) forward algorithm, the forward pass
runs crossover (`auto`) only where its vertex solution is consumed:

- **Benders cuts from the forward duals**: no aperture set resolves
  (`apertures: []`, or no `aperture_array` / per-phase apertures), so
  the backward pass builds the cut from the forward reduced costs;
- **forward basis capture**: `basis_cross_mode` is `warm`,
  `forward_to_backward` or `full_cross` (the default).

Otherwise (aperture cuts with `basis_cross_mode` `off` or
`backward_to_forward`) the forward pass is barrier with `crossover:
none`.  An explicit non-`auto` crossover (`primal` / `dual` / `none`)
in `forward_solver_options` — or in the global `solver_options`, which
the forward options inherit — always wins; simplex algorithms are left
untouched.  With `basis_cross_mode` `warm` / `full_cross` the forward
pass is seeded from a captured basis from iteration 2 on and solved by
warm dual simplex (a vertex anyway); an interior forward solve leaves
no basis to capture, so `crossover: none` there only affects cold
solves — pair it with `basis_cross_mode: off` to run the forward pass
by barrier throughout.  The final simulation pass always crosses over (output duals).
The rule applies per cascade level.

Cuts built from interior forward data — the aperture pass's fallback
cut, or Benders cuts under an explicit `crossover: none` — have their
intercept lowered by `1e-8 · max(1, |z|)` (the barrier relative gap), so
primal–dual gap noise cannot lift the lower bound above the upper bound.

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
| *(start-up only)* | `--plugin-check` | What a failed solver-plugin integrity check does (`error` / `warning`) |
| *(start-up only)* | `--config FILE` | Option-defaults file (instead of `$GTOPT_CONFIG` / `~/.gtopt.conf`) |
| *(any option)* | `--set key=value` | Set any planning option (see below) |

The full flag list is in the [Usage Guide](usage.md#command-line-reference).
There are no `--method`, `--demand-fail-cost` or `--scale-objective`
flags: use `--set method=sddp`, `--set model_options.demand_fail_cost=500`
or `--set model_options.scale_objective=1000`.

> **`--set` key=value**: all other options are now set via `--set`.
> Examples: `--set use_single_bus=true`, `--set output_directory=results/`,
> `--set solver_options.algorithm=barrier`,
> `--set sddp_options.max_iterations=300`.
>
> **Deprecated aliases** (still work, emit a warning): `-b`, `-k`,
> `-D`, `-F`, `-d`, `-f`, `-C`, `-a` / `--algorithm`, `-t` / `--threads`,
> `--sddp-max-iterations`, `--sddp-min-iterations`,
> `--sddp-convergence-tol`, `--sddp-elastic-penalty`,
> `--sddp-elastic-mode`, `--lp-debug`, `--lp-error`, `--log-directory`,
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

**Experimental, off by default.**  Measured on a 5-year hydrothermal case
(3 SDDP iterations): exact (same LB at iteration 1, unit tests over
none/row_max/ruiz) but no gain in kappa or solver effort with one factor
per reservoir, and a clamp of 1e3 raises the coefficient ratio from ~5e5
to ~5e8.  Keep `k` small.

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
**Measured on the same case, the first-order lever is not this option but
`scale_theta`: leave it UNSET (auto = 1 / (3 median(X/V²))).**  A case that
pins `scale_theta: 1` (older plp2gtopt conversions) runs with ~10x higher
kappa.
