# Changelog

All notable changes to GTOpt (Generation, Transmission and storage
Optimization) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.1.0] - 2026-11-20

### Added

- **`plexos2gtopt` general PLEXOS databases**: `--plexos-model`,
  `--plexos-start-date`, `--plexos-sample` and `--plexos-data-dir` pick one
  Model of a multi-model PLEXOS XML (scenarios, dated rows, time-of-day
  patterns, wind/demand Data Files) for a dispatch window; pumped storage
  becomes a battery, demand-side bids become price-bid generators, banded
  heat-rate curves are made convex, and constraints without a penalty stay
  hard.  Missing Data Files are fatal only when a converted property needs
  them.  A bare `*.xml` / `*.xml.xz` is accepted as input.
- **`plexos2gtopt.aemo_isp`** (prototype): builds an hourly dispatch case for
  a short window of one scenario of AEMO's 2026 ISP PLEXOS model (buses,
  demand traces, generators, batteries, flow paths, hydro storages with
  inflows, daily energy limits); expansion and the PLEXOS constraint set are
  not converted yet.
- **`psse2gtopt` overlays**: `--gen-costs` (`bus,id[,pmax],mw1,cost1,…`) and
  `--line-limits` (`from,to,ckt,tmax`) CSV files.

- **Real cases in gtopt-cases**: the public case repository
  [gtopt-community/gtopt-cases](https://github.com/gtopt-community/gtopt-cases)
  (Australia, Chile, Guatemala, Ireland, Peru, Poland); `tools/get_cases.sh` and
  `tools/cases.lock` pin it for the tests that need large external cases
  (`GTOPT_CASES_DIR`).

- **`pp2gtopt` monthly stages with representative blocks**:
  `--stage-period month|week|day` (calendar dates from a date column,
  `--start-date`, or a non-leap 2023 default) and `--block-method
  ldc|hour-of-day` with `--blocks-per-stage X` build non-chronological
  blocks (net-load-duration-curve groups, `--ldc-key`, `--ldc-split
  hours|energy`; or typical-day hour groups) with every element averaged
  over the same hours, preserving each element's energy per stage.
  `BlockHourMap/block_hour_map.parquet` (+ `simulation.block_hour_map`)
  records which hours form each block.  `net.storage` now converts to
  gtopt batteries in time-series mode (`--storage`, `--storage-efficiency`).
  `--method sddp` (alias of `--solver`) emits one phase per stage with
  `sddp_options` = 100 iterations / 0.01 tolerance when the case has
  storage and `max_iterations = 0` (forward pass only, same result as
  `monolithic`) when it has none (`--sddp-max-iterations`,
  `--sddp-convergence-tol`).  `gtopt_shared.time_grid` gains `BlockGrid`,
  `calendar_stage_of_hour`, `ldc_groups`, `hour_of_day_groups`,
  `chronological_groups` and `TimeGrid.block_durations` /
  `to_block_grid` (existing signatures unchanged).

- **`pp2gtopt` time-series mode**: `--profiles FILE` (CSV/Parquet, wide or
  long) turns a pandapower / MATPOWER network plus hourly load and
  renewable series into ONE multi-block, multi-stage case covering the
  whole profile horizon (or the `--hours A:B` / `A+N` sub-window):
  `--block-hours N` averages N hours per block, `--stage-hours H`
  (`--stage-length`) sets stage length with one phase per stage
  (`--single-phase` to disable).  Writes `<out>.json` + `<out>/Demand/lmax.parquet`
  and `<out>/GeneratorProfile/profile.parquet` (`capacity_profile_array`),
  plus `<out>/pp2gtopt_state.json` (paths relative to it) replayable with
  `--from-state`.  Columns map to elements via `load:<ref>`,
  `sgen:<ref>`, `gen:<ref>`, `load@bus:<b>`, `load@zone:<z>`, `load@all`,
  bare names, or a JSON/YAML `--profiles-spec` (selectors, MW / p.u. /
  auto units, scale).  `--profiles-from-net` reads pandapower
  `ConstControl` profiles.  Also: `--matpower` / `.mat` input
  (`--mpc-var`), `net.sgen` converted as generators, `--solver
  monolithic` (default with profiles), `--output-format`.  Shared
  `gtopt_shared.time_grid` (`TimeGrid`, `parse_hour_range`,
  `write_block_schedule`) for other hourly converters.

- **`sddp2gtopt` multi-file PSR SDDP block cases**: converts classic
  `.dat` SDDP studies (COES Peru, AMM Guatemala; SDDP 17.0–18.0 layouts).
  The converted case covers demand by block and bus, thermal and hydro
  plants with maintenance and limits, renewables with block profiles, the
  hydro cascade with reservoirs and historical inflows (one scenario per
  series), and the DC network with losses and dated circuit changes.  It
  is written as a gtopt SDDP planning with one phase per monthly stage
  (Parquet schedules).  New CLI options: `--num-scenarios` and
  `--method`.

- **`FutureCost.iteration_limit`**: keeps only the boundary cuts of the
  last N iterations of the source run (`0`/unset = all), honoured by every
  method; replaces `{sddp,monolithic}_options.boundary_max_iterations`.
  `FutureCost.missing_cut_var_mode` is also new, and `FutureCost.valuation`
  is now typed `BoundaryCutsValuation` (`end_of_horizon` /
  `present_value`), the type the cut loader actually uses.
- **`tools/migrate_future_cost.py`**: rewrites the deprecated boundary-cut
  keys of planning JSON files onto `system.future_cost_array`, preserving
  formatting (`--method-rules`, `--dry-run`).
- **`gtopt_check_json` check `future_cost_aliases`**: warns on each
  deprecated boundary-cut key and reports conflicting values as critical.

- **MPS output for `--lp-file`**: new `--lp-format lp|mps` option (default
  `lp`, unchanged); a `--lp-file` ending in `.mps` also selects MPS.  Names and
  integer columns are preserved (`MARKER` sections; `BV` bounds for binaries
  on OSI/CBC) by every backend (cuOpt writes only MPS).  An explicit request
  the backend cannot honour is an error; the diagnostic dumps fall back to
  the other format.  `--lp-compression` also compresses the `--lp-file`
  dump.  `SolverBackend::write_lp(filename, LpFileFormat)` now takes the
  format and returns `bool`, and `k_solver_abi_version` is bumped to 16, so
  out-of-tree solver plugins must implement it for both formats and be
  rebuilt.

- **LP scales sidecar** (`--lp-scales`, option `lp_scales`): when an LP
  file is written with `--lp-file`, also write `<stem>.scales.json`
  (`gtopt-lp-scales/1`, serialised with daw::json) with what the
  `.lp`/`.mps` cannot carry: the objective scale, the per-block and
  per-stage `cost_factor` weights and, at the `full` level, the columns
  and rows whose scale is not 1 (columns with their gtopt element and
  physical unit) in two zstd Parquet tables, `<stem>.scales_cols.parquet`
  and `<stem>.scales_rows.parquet`.  A solution read back from an external
  solver can then be mapped to physical units.  The JSON also records the
  gtopt build (version and git revision) and the model's numerical ranges
  (matrix, objective, bounds, RHS, scale factors, per constraint family).
  Levels `none` (default), `summary` (JSON only) and `full` (`true`); the
  JSON follows `--lp-compression`.  The model file is unchanged.  See
  `docs/lp-scales.md`.
- **`gtopt::build_info()`**: version and `git describe` revision of the
  running build, refreshed at build time, for audit trails in generated
  files.

- **Unit commitment (three-bin formulation)**: full implementation of the
  Morales-España tight-and-compact UC model with status ($u$), startup ($v$),
  and shutdown ($w$) binary variables per committed generator.
  - Core constraints: logical transition (C1), generation bounds (C2),
    exclusion (C3).
  - Ramp constraints (C4–C5): tight state-dependent ramp-up/down limits
    with separate startup/shutdown ramp parameters.
  - Minimum up/down time (C6–C7): aggregated formulation at period level
    with correct handling of non-uniform block durations.
  - Hot/warm/cold startup cost tiers (C8–C10): time-dependent startup costs
    based on offline duration with validation and graceful fallback.
  - Piecewise linear heat rate curves: multi-segment fuel cost modeling with
    per-segment emission accounting and segment-bound constraints.
  - Commitment periods: optional coarser binary variable resolution to reduce
    integer variable count.
  - LP relaxation control: per-element (`relax` flag) and per-phase
    (`model_options.relaxed_phases`) with `PhaseRangeSet` expression parser
    supporting `"all"`, `"none"`, ranges, and comma-separated indices.
  - Reserve-UC integration: reserve headroom constraints become conditional
    on commitment status ($P_\max \cdot u$ instead of fixed $P_\max$).
  - Must-run mode: forces $u = 1$ for baseload or contractually committed units.
  - Chronological gating: UC only applies on stages with `chronological: true`;
    non-chronological stages dispatch normally (backward compatible).
  - New `Commitment` struct (`commitment.hpp`) with JSON serialization.
  - New `CommitmentLP` class (`commitment_lp.{hpp,cpp}`) with full output.
  - Comprehensive test suite: 40+ test cases in `test_commitment.cpp` (2600+
    lines) covering all constraint types, edge cases, and regression tests.
  - Documentation: `docs/unit-commitment.md` — dedicated guide with formulation,
    JSON reference, worked examples, and academic references.
- **Emission cost and cap framework**:
  - `generator.emission_factor` (tCO₂/MWh): per-generator CO₂ intensity.
  - `model_options.emission_cost` ($/tCO₂): system-wide carbon price, adds
    cost adder to dispatch and per-segment fuel costs.
  - `model_options.emission_cap` (tCO₂/year): per-stage CO₂ cap with
    endogenous carbon pricing via constraint dual variable.
- **Stage chronological flag**: `stage.chronological` boolean field enables
  block-sequential UC constraints on specific stages while leaving
  non-chronological stages for expansion planning.
- **SDDP cut types**: optimality and feasibility cuts are now distinguished
  in the SDDP solver, with named cut coefficients for better diagnostics.
- **Variable scaling**: `VariableScaleMap` support and decoupled `flow_scale`
  option for improved numerical conditioning of large hydro models.
- **Enum options framework**: solver options now use typed enums instead of
  raw strings, with compile-time validation.
- **Solver timeouts**: configurable time limits for LP solves; the solver
  abandons and reports partial results when the limit is exceeded.
- **Single-phase SDDP-to-monolithic fallback**: when the problem has only
  one phase, the SDDP solver automatically falls back to the monolithic
  solver instead of failing.
- **SDDP hot-start**: `sddp_hot_start` option to load cuts from a previous
  run and resume SDDP iterations.
- **plp2gtopt scale options**: `--auto-rsv-energy-scale` and `--auto-energy-scale`
  flags for automatic variable scaling during PLP conversion.
- **Hydro worked example**: Example 5 (simple hydro cascade) added to
  `PLANNING_GUIDE.md`.
- **CHANGELOG.md**: this file.

### Changed

- **FutureCost is the single source of the FCF boundary cuts**: the
  `system.future_cost_array` element configures the boundary cuts for every
  method (monolithic, SDDP, cascade); a method never reads another
  method's options.  The monolithic solver no longer honours
  `sddp_options.boundary_cuts_mean_shift`, `missing_cut_var_mode`,
  `scale_alpha` or `cut_coeff_eps` (it uses `FutureCost.scale_alpha` and
  the default `cut_coeff_eps`).  A relative `cuts_file` now resolves
  against `options.input_directory` under monolithic too, and a
  single-phase SDDP run falling back to monolithic now loads the cuts.
  `use_user_alpha` together with `cuts_file` is an error under monolithic
  as under SDDP.  Generic extra linear constraints belong in
  `UserConstraint`.
- **Converters emit FutureCost**: `plp2gtopt`, `plexos2gtopt`,
  `sddp2gtopt` and `igtopt` write `system.future_cost_array` and never the
  legacy keys; `plp2gtopt`'s emissions ray no longer overwrites a user
  `cuts_file`.

### Deprecated

- **Boundary-cut option keys** are now aliases of FutureCost fields,
  translated once at planning load (after `--set`) with a warning:
  `sddp_options.boundary_cuts_file`, `boundary_cuts_mode`,
  `boundary_cut_sharing_mode`, `boundary_cuts_mean_shift`,
  `boundary_max_iterations`, `missing_cut_var_mode` (honoured only for
  `sddp` / `cascade`); `monolithic_options.boundary_cuts_file`,
  `boundary_cuts_mode`, `boundary_cut_sharing_mode`,
  `boundary_max_iterations` (only for `monolithic`);
  `simulation.boundary_cuts_file`, `boundary_cuts_valuation` (any method).
  A key of the other method is ignored with a warning; different values
  for the same field are a hard error.

### Removed

- **`BoundaryCutSoftCost` enum**: the former type of `FutureCost.valuation`
  was never read by the cut loader.

- **Emission options replaced by `EmissionZone`**: `model_options.emission_cost`
  and `model_options.emission_cap` were removed (and are now rejected as
  unknown options).  A carbon price is now a zone `price` and a cap a zone
  `cap` (hard) or `cap` + `cap_cost` (soft); generators are linked through
  `emission_source_array` (or `Generator.emissions[]` /
  `Fuel.emission_factors[]`).  `Commitment.fuel_emission_factor` (with
  `pmax_segments`, `heat_rate_segments`, `fuel_cost`) was removed from
  `Commitment`; the piecewise curve lives on `Generator`.  Migration:

  ```json
  "emission_array": [{"uid": 1, "name": "co2"}],
  "emission_zone_array": [
    {"uid": 1, "name": "system_co2",
     "emissions": [{"emission": "co2", "weight": 1.0}],
     "price": 30.0, "cap": 500000.0}
  ],
  "emission_source_array": [
    {"uid": 1, "name": "coal_co2", "generator": "coal",
     "zone": "system_co2", "emission": "co2", "rate": 0.95}
  ]
  ```

  replaces `"model_options": {"emission_cost": 30, "emission_cap": 500000}`
  with `"emission_rate": 0.95` on `coal`.  See `docs/emissions.md`.

- **Shared arrow input-index cache**: the long-format input reader's
  `(Stage, Block)` row index is built once and shared — across every
  per-(scene, phase) LP cell within a run, and across cascade levels
  (each level previously rebuilt it cold). LP-only build on a 5-year
  8-scene case: 256 s → 30.7 s and 20 GiB → 7 GiB; the cascade
  `transport → full_network` level transition: 213 s → 28 s (7.6×).
- **`LinearInterface` decomposition (state)**: the matrix conditioning
  statistics, the name/label subsystem, and the Ruiz/equilibration
  scaling state were extracted into behaviour-free value types
  (`MatrixStats`, `LpLabelStore`, `ScalingState` + relocated
  `ScaledView` in `matrix_stats.hpp` / `lp_label_store.hpp` /
  `lp_scaling.hpp`), each with isolation unit tests. Behaviour and COW
  clone semantics are unchanged.
- **clang-tidy hygiene**: every broad file-wide `NOLINTBEGIN/END`
  blanket in production code was replaced by a real fix or a narrow,
  documented per-site suppression.
- **Options refactor**: SDDP options no longer require the `sddp_` prefix
  (e.g., `max_iterations` instead of `sddp_max_iterations`). Old names are
  still accepted for backward compatibility.
- **MonolithicOptions**: monolithic solver options are now grouped under a
  dedicated `MonolithicOptions` struct for clarity.
- **`as_label` optimization**: label generation uses `std::to_chars` for
  faster integer-to-string conversion, measurably improving LP construction
  time on large cases.
- **SDDP cut I/O refactor**: extracted `sddp_cut_io` and `sddp_monitor`
  modules from `sddp_solver` for better separation of concerns.
- **SDDP aperture module**: extracted `sddp_aperture` for cleaner
  decomposition of the SDDP algorithm.

### Fixed

- **Security (code scanning)**: the guiservice-plus AI chat route no longer
  fetches a browser-supplied URL (Ollama/custom endpoints must be
  allow-listed in `GTOPT_AI_ALLOWED_BASE_URLS`); guiservice error responses
  return an error id instead of exception text, and `/api/logs` omits
  tracebacks.
- **`psse2gtopt` loads**: constant-current and constant-impedance load
  components are evaluated at the solved bus voltage
  (`PL + IP·VM + YP·VM²`); negative loads become fixed injections instead
  of being dropped.

- **`plp2gtopt`: boundary-cut `efin_cost` used the wrong volume unit.**
  `PlanosParser.lower_bound_water_value_by_reservoir` (the cut water value
  that overwrites `Reservoir.efin_cost` under `--soft-storage-bounds`)
  still scaled plpplem2 gradients by the per-reservoir `10^(FEscala-6)`,
  the FEscala mis-reading the `boundary_cuts.csv` writer dropped on
  2026-07-12.  `efin_cost` was off from the cuts gtopt loads by
  `10^(9-FEscala)` (×10 low for FEscala=8, ×10 high for FEscala=10).
  Both now use the shared uniform `PLANOS_GRAD_SCALE` (`$/dam³ → $/hm³`,
  ×1000); the `apply_fescala` keyword became `grad_scale`.

- **Python tools: runtime data files missing from non-editable installs.**
  `pip install ./scripts` (wheel) did not ship
  `plexos2gtopt/data/cen_pcp_hard_ucs.txt`, the `plexos2gtopt`/`plp2gtopt`
  `solvers/*.prm|*.opts` files, the `gtopt_expand` whitelist CSVs, nor the
  repo-root `share/gtopt/{emissions,cogen}` tables, and every loader
  silently fell back: `plexos2gtopt` demoted ~90–110 CEN user constraints
  per case from HARD to SOFT, used the bare IPCC emission factors instead
  of `cen_chile.json`, tagged no cogen units and wrote no `solvers/`
  directory.  All files are now package data; `cen_chile.json` and
  `cen_chile_cogen.csv` moved from `share/gtopt/` to
  `scripts/gtopt_shared/data/{emissions,cogen}/` (single copy; the C++
  side only reads `share/gtopt/{naming,unit}_dialects.json`), and the
  duplicate `plp2gtopt/templates/ror_equivalence.csv` was dropped in
  favour of `gtopt_expand/templates/`.  A missing required file now raises
  `gtopt_shared.resources.MissingDataFileError` with a reinstall hint.
  The three copies of the cogen loader were merged into
  `gtopt_shared.cen_cogen` (the `gtopt_marginal_units` copy looked one
  directory above the repo root and never found the CSV).  A new
  `pytest -m wheel` test builds and installs the wheel and checks every
  file; the Scripts CI workflow runs it.  Cases converted with an
  affected install must be reconverted.

- **`future_cost_array` dropped on planning merge**: `System::merge` did
  not merge `future_cost_array`, so a FutureCost element read from a
  planning file was silently ignored.
- **Solver registry crash on failed plugin validation**: requesting a
  solver whose plugin fails subprocess validation (e.g. a transient
  license error) crashed with SIGSEGV instead of falling back to the
  next backend — an erased-iterator increment in
  `SolverRegistry::ensure_solver_loaded`.
- **Planning-level solver pin honored**: the programmatic
  `options.lp_matrix_options.solver_name` pin was silently ignored —
  LP construction always re-resolved the default solver (GTOPT_SOLVER /
  plugin priority), including across cascade levels.
- **MindOpt backend**: `MDOstartenv` license validation is retried with
  backoff (transient `MDO_INVALID_LICENSE` under heavy parallel load);
  `LIBMILP_PATH` is self-derived from the loaded `libmindopt.so` so MIP
  solves work without user environment setup.  SOS2 stays unsupported
  on purpose: MindOpt 2.3.0 mis-handles SOS2 on real models (silent
  non-enforcement or spurious infeasibility — verified with minimal
  probes), so the fail-fast "switch solver" error is kept.
- **Exit codes**: the solver now returns correct exit codes (0=optimal,
  1=non-optimal, 2=input error, 3=internal error) instead of always
  returning 0.
- **Logging**: log messages no longer interleave in multi-scene SDDP solves;
  per-scene log context is now properly scoped.
- **`flat_map` portability**: replaced `std::flat_map` with `gtopt::flat_map`
  in `sddp_cut_io` to ensure compatibility across compilers.

### Testing

- **`test_water_fail_cost_magnitude.py` runs on the published `plp_2y`
  case** (was the private `iplp_juan`, now retired).  The `lost_pf`
  reference table was recomputed from plp_2y data with its derivation
  documented, and is now checked end to end on a planos-free copy of the
  case (`efin_cost = anchor × lost_pf × 1e6/3600`); the boundary-cut
  overwrite is checked separately against the emitted `boundary_cuts.csv`.
  The juan-based tests (and `test_plp_gtopt_comparison.py::
  test_efin_cost_from_boundary_cuts`) had been silently skipped since
  2026-05 and asserted obsolete defaults: `efin_cost` is only emitted with
  `--soft-storage-bounds` (hard bounds by default since 2026-07) and
  `--no-cuts-govern-terminal`, and the default `phi_expectation` CSV is
  no longer pre-divided by `NVarPhi`.
- **Monolithic keeps the boundary cuts of an SDDP-converted case**: new
  doctest pins that a FutureCost element (what the converters emit)
  loads under `method = monolithic`.

- **+160 unit tests**: coverage increased from 71.5% to 77.3%, with new
  tests for `sddp_cut_io`, `sddp_monitor`, battery expansion, reservoir
  efficiency, variable scaling, and enum options.

[Unreleased]: https://github.com/gtopt-community/gtopt/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/gtopt-community/gtopt/releases/tag/v1.1.0
