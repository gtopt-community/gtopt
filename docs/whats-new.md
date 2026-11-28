# What's New in GTOpt

An overview of the capabilities added since October 2026 (releases
1.1.0 and 1.2.0 and the current development line), with links to the
pages that document them.  The authoritative list of changes, including
behaviour changes and fixes, is the [Changelog](../CHANGELOG.md).

## Time representation

- **Point-sampled stages.**  `Stage.sampling = "point"` turns block
  values into samples at node times `Block.time`; block durations become
  quadrature weights.  With `Stage.period_hours` and `Stage.periodic`
  a stage samples one typical day or week that repeats over the stage,
  and every intra-period coupling (ramps, commitment, `_prev` terms,
  daily-cycle storage) wraps around the period.  See
  [Input Data §2.2](input-data.md#22-stage).
- **Chebyshev nodes and Clenshaw–Curtis weights** for typical weeks and
  days, with a generic cell partition so that min up / down times,
  commitment periods and daily-cycle windows are counted on quadrature
  cells.  See [Typical-week sampling](methods/typical-week-sampling.md).
- **Storage integration rules on point stages**
  (`model_options.storage_integration`, `reservoir_storage_integration`,
  `battery_storage_integration`, per-element `storage_integration`):
  `block` / `cc` (Clenshaw–Curtis), `trapezoid`, `lg4` / `lg6` and the
  non-negative `lg4_pos` / `lg6_pos`.  Built-in defaults are `lg4_pos`
  for reservoirs and `block` for other storages; the converters write
  `cc` for both.  See
  [Planning Options](planning-options.md#modeloptions-fields).
- **Typical-week sampling in `plexos2gtopt`** (Chebyshev weeks and
  days, Wednesday-noon window, `--ramp-refine` sunrise / sunset
  refinement, `--point-storage-integration`) and the new
  **`tcheby2gtopt`** converter with the `cheby_small` case library.
  See [plexos2gtopt](scripts/plexos2gtopt.md) and
  [tcheby2gtopt](scripts/tcheby2gtopt.md).

## Unit commitment and pricing

- **Daily (non-uniform) commitment grid** (`commitment_breaks` on
  `Commitment`, `Stage` or `model_options`; `Stage.start_hour`):
  the status lives on cells given by daily boundaries, e.g. the
  evening peak 17:30–23:30 as one cell, on point and block stages,
  with a per-stage (seasonal) pattern.  Converters:
  `--commitment-breaks` / `--commitment-breaks-file`.  See
  [Typical-week sampling §4c](methods/typical-week-sampling.md).
- **Power-based unit commitment** (Morales-España et al.):
  `model_options.uc_formulation = "power_based"` (per unit
  `Commitment.uc_formulation`) models power at block boundaries,
  trapezoid block energy, exact ramps and start-up / shut-down
  trajectories (`startup_trajectory`, `shutdown_trajectory`,
  `trajectory_step`).  See
  [Unit Commitment §4.6](unit-commitment.md#46-power-based-formulation-uc_formulation--power_based).
- **Commitment periods and grids**: `Commitment.commitment_period`,
  `commitment_grid`, `Block.commitment_cell` and `ramp_allowance`
  (`boundary` by default) control the resolution of the binaries on
  block and point stages; cyclic commitment over a typical period with
  `Commitment.cycle` and `long_transition`.
- **Binary start-up / shut-down** columns (`transition_integrality =
  auto`); the short-lived `v ≤ u` / `w ≤ 1 − u` rows
  (`transition_status_rows`) were removed.
- **Pricing modes** after a MIP (`pricing_mode`: `fixed`, `relaxed`,
  `fast_start`), recorded in `pricing.csv`.
- **Risk-averse planner** (`risk_measure = "cvar"`, `cvar_alpha`,
  `cvar_weight`): mean-CVaR over the scenarios of a monolithic plan;
  risk-adjusted scenario probabilities in `risk_measure.csv`.  See
  [formulation §4.5](formulation/mathematical-formulation.md#45-risk-averse-objective-mean-cvar-opt-in).
- **Numerics switches**: `lp_matrix_options.pin_logic_rows` keeps ±1
  commitment-logic rows out of row equilibration;
  `solver_options.accept_optimal_infeas` accepts an "optimal with
  unscaled infeasibilities" status with a warning;
  `solver_options.deterministic_parallel` for reproducible parallel
  solves.  See [Planning Options](planning-options.md).
- **Feasible MIP starting points** (`monolithic_options.mip_start`,
  `--mip-start`).  See [Monolithic §4.5](methods/monolithic.md).

## Network

- **Zero-impedance ties**: a relative floor
  (`tiny_reactance_rel_threshold`, default 0.01 of the median `|X/V²|`)
  on top of `dc_line_reactance_threshold`; ties are lossless by default
  (`tie_line_losses = false`) and force equal bus angles.
- **Loss model safeguards**: ordered-fill bounds on piecewise loss
  segments (`loss_segment_bound`), small loss costs
  (`loss_cost_eps`, `loss_flow_abs_cost`) and the overflow price past a
  loss envelope (`loss_overflow_cost`).
- **Thermal limit with losses**: `line_thermal_limit_mode` (`flow`,
  `sending`, `full`) and line-limit duals in the output.
- **Dynamic line rating** through critical spans (`Line.dlr`,
  IEEE Std 738 ampacity, `use_line_dlr`, `dlr_limit_mode`,
  `overload_slack_bound`, `max_overload_ratio`) and the
  **`dlr2gtopt`** input builder.  See
  [Input Data §3.4](input-data.md#dynamic-line-rating-dlr) and
  [dlr2gtopt](scripts/dlr2gtopt.md).
- **Optimal transmission switching** with `LineCommitment`.
- **Radial-line flow direction**: `model_options.line_direction_mode`
  (`off`, `report`, `structural`) and `Line.flow_direction` (`both`,
  `ab`, `ba`, `auto`) drop the impossible direction of one-way radial
  lines and the dead-end lines, which also removes the phantom
  two-way loss sink on them.  Preview with
  `gtopt_check_topology --directions`.  See
  [Planning Options](planning-options.md#modeloptions-fields).

## Energy carriers and hydro

- **Gas networks**: nodes with pressure limits, Weymouth pipes with
  line pack, compressors, supplies and demands, gas-fired units, and
  **LNG terminals** with deliveries, boil-off and send-out limits; all
  reachable from PAMPL.  See [Gas Network](gas-network.md) and
  [gasnet2gtopt](scripts/gasnet2gtopt.md).
- **Hydrogen, ammonia and heat**: carrier nodes, storages,
  hydrogen offtakes and `CarrierConverter` (electrolysers, fuel cells,
  …).  See [Input Data §3.28](input-data.md#328-multi-carrier-nodes-and-storage-hydrogen-ammonia-thermal).
- **Irrigation agreements**: `FlowRight` (target / `fcost` / `uvalue`
  kink, `flow_mode`), `VolumeRight` ledgers with reset, debit and
  credit rules and free disposal, `RightBoundRule` on reservoir volume
  or calendar month, and PAMPL files with named sets and `state(var)`.
  See [Irrigation Agreements](irrigation-agreements.md) and
  [User Constraints](user-constraints.md).
- **Emissions** per pollutant and fuel, emission zones with caps and
  prices, allowance pools.  See [Emissions](emissions.md).

## SDDP and cascade

- **Cut sharing** `none` / `multicut` (one α per source scene) and the
  experimental `markov` mode; the invalid broadcast modes were removed.
- **Aperture grouping** (`aperture_chunk_mode`: `per_scene` default,
  `per_aperture`, `chunked`) and solve modes (`warm` default, `cold`,
  `reduced_cost`, `dual_shared`, `screened`).
- **Memory**: `low_memory_mode = compress` with a zstd-level-1 memory
  codec by default, `release_backends = true`, deduplicated snapshots.
- **Cascade levels** with their own model options, reduced systems
  (`system_file`, `aperture_system_file`) and per-level solver options.
  See [SDDP](methods/sddp.md) and [Cascade](methods/cascade.md).

## Solvers

- Plugins for HiGHS (pinned release, HiPO parallel interior point),
  CBC / CLP, SCIP, MindOpt, cuOpt (GPU, monolithic), CPLEX and Gurobi,
  loaded at run time; plugin ABI 18.  Plugins and the solver library
  they load are verified at start-up (`--plugin-check`).
- **Kappa reporting** on every backend that has it (`kappa_mode`:
  `estimate`, `exact`, `off`) with `simulation.kappa_warning` /
  `kappa_threshold`.
- New solver options: `barrier_solver`, `mip_lp_algorithm`,
  `mip_heuristic_effort`, `crossover`, `mip_polish`.
  See [Supported Solvers](solvers.md).

## Converters and tools

New or extended converters: `plexos2gtopt` (PLEXOS 8.1–10.0, AEMO ISP
expansion), `sddp2gtopt` (PSR SDDP), `psse2gtopt`, `sienna2gtopt`,
`cucdata2gtopt` (clustered UC benchmark, power-based option),
`tcheby2gtopt`, `dlr2gtopt`, `gasnet2gtopt`, `rts_gmlc_to_gtopt`, and
the `run_gtopt` dashboard with plain mode and `--attach`.  The
exporters `gtopt2plp`, `gtopt2sddp` and `gtopt2plexos` write a gtopt
case and its results in PLP, PSR SDDP and PLEXOS formats, reporting
whatever the target cannot hold.  See
the [Scripts Guide](scripts-guide.md).
