# Changelog

All notable changes to GTOpt (Generation, Transmission and storage
Optimization) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `gtopt_state.json` (pre-solve snapshot) now records the solver the run
  resolved (`lp_matrix_options.solver_name`, new JSON field; `--solver` >
  JSON pin > `.gtopt.conf` `solver` > `$GTOPT_SOLVER` > plugin priority)
  and the resolved parameter file (`solver_options.param_file`, including
  an auto-discovered `solvers/<solver>.prm`), so a replay of the snapshot
  uses the same backend and tuning.  A warning now says when
  `param_file` is set but the active backend ignores it (only cplex and
  cuopt read the main parameter file).

- `plexos2gtopt --scale-theta FACTOR` (same opt-in flag as `plp2gtopt`,
  now shared as `gtopt_shared.cli_flags.add_scale_theta_argument`): the LP
  angle is FACTOR × θ (1 = radians, 1000 = milliradians).  Default unset:
  nothing is emitted and gtopt's C++ auto-scaling (1/(3·median(X/V²))) picks
  the scale; an explicit value is written to `model_options.scale_theta`
  and always wins over auto (`theta_max` is still auto-computed).  The
  same manual override works on any case with
  `--set model_options.scale_theta=1000`.
- `cases/reservoir_water_value/`: e2e cases (monolithic and SDDP) that
  pin the water value of a spilling reservoir: 0 with a free exit, −c/fcr
  when every exit is priced (`spillway_cost`, `Junction.drain_cost`,
  `Waterway.fcost`), −ecost × remaining blocks with `ecost`, and >0 when
  dry.  Checked against hand-derived values by
  `tools/check_expected_values.py` (`add_expected_values_case`).

- `plp2gtopt --falla-segments {first,all}`, default `first` (output
  unchanged).  `all` keeps every PLP failure tier: each demand on a bus
  with several `FALLA_<bus>_k` centrals is split into `<demand>_falla<k>`
  demands with `lmax` = tier share of the demand (from `plpmance.dat`)
  and `fcost` = tier cost, so rationing follows PLP's cost curve.
- `sddp2gtopt --deficit-segments {first,all}`, default `first` (output
  unchanged).  `all` keeps the whole `sddp.dat` "Costo Deficit" curve on
  SDDP block studies: each demand becomes `<demand>_deficit<k>` demands
  with `lmax` = segment % of the demand (last segment takes the rest) and
  `fcost` = segment cost.  Both converters now share the split helpers in
  `gtopt_shared/demand_tiers.py` and the flag registrar
  `gtopt_shared.cli_flags.add_demand_tier_segments_argument`.
- `sddp_options.aperture_chunk_mode` (CLI `--aperture-chunk-mode`, cascade
  per-level `sddp_options.aperture_chunk_mode`): names how backward-pass
  apertures are grouped into tasks — `per_scene` (default),
  `per_aperture`, or `chunked` (`aperture_chunk_size` = K per task).  The
  setup log shows the resolved mode, e.g.
  `SDDP Aperture: chunk_mode=per_scene chunk_size=16`.  A size that
  contradicts the mode (e.g. `per_scene` with `aperture_chunk_size: 4`)
  is ignored with a warning.  `plp2gtopt` and `run_gtopt` accept and check
  the new name.
- Native stepwise demand-failure (VoLL) curve: `Demand.fail_segments`
  (`[{fraction, cost}]`, per-stage schedulable cost, open last segment,
  firm remainder) and the global default
  `model_options.demand_fail_segments`.  The LP splits the served load
  into one column per segment with plain bounds (K − 1 extra columns per
  block, no extra rows; one segment is LP-identical to `fcost`).  New
  outputs `Demand/fail_seg<k>_sol`; `demand('x').fail` is now available
  to user constraints.  Validation in gtopt and `gtopt_check_json`
  (`demand_fail_segments` check); `gtopt_check_output` prices load
  shedding per segment.
- `scripts/tests/test_public_cli.py`: every console script in
  `[project.scripts]` must pass `--help` and an end-to-end run on a tiny
  case (converters also solve the result with gtopt when a binary is
  available); a new public tool without a case fails CI.
- `tools/affected_tests.py`: selects the C++ (doctest files, integration
  CTest entries), Python (pytest dirs / files, pytest-testmon for reverse
  dependencies), guiservice and tools tests plus the lint targets
  affected by a git change, explains every pick, escalates to the full
  suite for core headers, build files and packaging config, and runs the
  selection (`--run`, `--lint`).  CI uses it through the
  `.github/actions/affected-tests` action: `ubuntu.yml`, `tsan.yml`
  (pushes), `scripts.yml` and `guiservice.yml` run only the affected
  tests against the PR base / previous push, fall back to the full suite
  without a usable base, and keep a `full_run` `workflow_dispatch` input
  (default on).  In affected runs the Python coverage gate applies per
  package to the packages whose own tests ran.  `guiservice.yml` now also
  triggers on `scripts/**` and skips itself when the change cannot reach
  guiservice.
- Native BESS reserve deliverability: `Battery.reserve_sustain_hours`
  (and `reserve_discharge_credit`) add per-block power-headroom and
  SoC-sufficiency rows for the battery's reserve provisions, with no
  reserve term in any bus balance, so they cannot pin negative bus
  prices.  Off by default in gtopt.
- `plexos2gtopt --bess-reserve {native,uc}`, default `native`: CEN's
  "BESS SSCC Constraints" (`UP/DOWNStorageBound_*`, `*_CF_{GEN,LOAD}_COMP`
  and the fictitious `*_AUX` battery) map to the native rows.  `uc`
  keeps the previous UserConstraint mapping.  See
  `docs/studies/sscc_battery_constraint.md`.
- `gtopt --version`, the `--help` header and the `starting gtopt` log
  line show the git revision the binary was built from, e.g.
  `1.1.0 (v1.1.0-20-g5a4fd2ef8abc)`.  A clean build of exactly the
  release tag prints just `1.1.0`; a build without git prints the
  version alone.
- `solution.csv` gains two trailing columns, `gtopt_version` and
  `gtopt_revision`, and `solver_status.json` the same two keys, so every
  run records which gtopt produced it.  `run_gtopt` shows it in its
  post-run summary.  The golden comparator ignores both columns.
- Development versions: between releases `master` carries the next
  version with a marker (`GTOPT_VERSION_SUFFIX "-dev"` in
  `CMakeLists.txt`, `X.Y.Z.dev0` in `scripts/pyproject.toml`), so a
  master build reports e.g. `1.2.0-dev (v1.1.0-23-g...)`.  After a tag
  produces its draft release, the release workflow opens the PR that
  starts the next development version; the tag check now also requires
  an empty suffix.  See `docs/releasing.md`.
- `gtopt_check_topology` checks the network per stage.  It honours
  `Bus.active`, `Line.active` and `tmax_ab = tmax_ba = 0`, and reports
  topology periods (stage ranges with the same islands).  Each island
  shows its zone (bus `zone` field or name prefix such as `CL_`) and
  the elements on its buses with their MW.  Inter-zone lines are
  listed with the stages they are in service.  New
  `--expect-islands N` / `--expect-zones CL,PE` exit with code 3 when
  a stage has more islands with elements than expected.  See
  `docs/scripts/gtopt_check_topology.md`.

### Changed

- **SDDP α floor (behaviour change)**: the future-cost variable α is no
  longer floored at 0 when a cell installs its first cut — the floor is
  derived from the cuts actually installed (see *Fixed*).  **Models with
  only non-negative costs that relied on the implicit first-cut `α ≥ 0`
  should set `sddp_options.alpha_min = 0`**; without it the trial
  trajectory of the first iterations (and where a fixed-iteration run
  stops) can change.  Models with negative costs or rebased FCFs need no
  action — they were the ones reporting an invalid lower bound.
- A relative `solver_options.param_file` (e.g. the `"solvers/cplex.prm"`
  the converters pin) resolves against `input_directory` first — the case
  directory wins — and against the working directory only when the case
  has no such file; the log names the location used.  An auto-discovered
  `<input_directory>/solvers/<solver>.prm` is recorded as an absolute
  path.
- `solver_options.time_limit` is a wall-clock budget for the whole solve
  of one LP/MIP: MIP-start stages and the algorithm-fallback retries share
  it (each retry gets what is left; retries stop once < 1 s remains).  A
  MIP that hit its limit used to be re-run up to `max_fallbacks` more
  times with the full limit each.

- Option precedence is now *the more explicit, the closer to the run,
  wins*: CLI flag > `--set` > planning JSON (last `-s` wins) >
  `.gtopt.conf` > built-in default.  `.gtopt.conf` `[gtopt]` values used
  to be merged as if they were CLI flags, so they silently beat both the
  planning JSON and an explicit `--set` of the same option (e.g.
  `sddp-max-iterations` in `~/.gtopt.conf` overrode
  `--set sddp_options.max_iterations=…`).  They now fill only options the
  JSON, `--set` and the CLI left unset; relative directories from the
  config file stay CWD-relative.  Same rule as the Python tools' own
  config sections.  Documented in `docs/usage.md`, `--help` (`--set`) and
  `docs/templates/gtopt.conf.sample`.  **Behaviour change** for runs whose
  config file and planning JSON set the same option.

- Auto `scale_theta` (θ_LP = scale_theta · θ, when unset) is now
  `1 / (3 · median(X/V²))` instead of `1 / median(X/V²)`.  θ is a node
  potential accumulating ~D line drops to the reference bus (D ≈ 8-10 on
  the Chilean system), so the old rule left θ_LP ~8× the line flows;
  dividing by ≈√D = 3 balances the KVL-row θ coefficient against the θ_LP
  magnitude.  On CEN cl5y it gives ≈1039, next to the empirically best
  1000 (y24_S2_perf, CPLEX, 2 SDDP iterations: radians κ 1.56e8 / 1.137M
  ticks; old auto ≈3116 κ 1.12e7 / 1.086M; 1000 κ 1.12e7 / 1.071M).
  Explicit values (e.g. `--set model_options.scale_theta=1000`) are
  honoured unchanged.
- `sddp2gtopt` no longer pins `model_options.tiny_reactance_mode = "dc"`:
  PSR coupler circuits below `dc_line_reactance_threshold` (1e-3 p.u.)
  now use gtopt's default `tie` (zero-impedance tie, equal bus angles)
  like every other converter.  `--set model_options.tiny_reactance_mode=dc`
  restores the KVL-free DC-link treatment.  (The ~9 GWh GUA curtailment
  originally blamed on the couplers was the `dcirc.dat` units bug listed
  under *Fixed*, not a tie effect.)
- SDDP: the backward-pass apertures are now grouped **per scene** by
  default — every aperture of a (scene, phase) is solved in series on one
  LP clone, each warm-started off the previous basis.  Before, the
  default ("auto", which resolved to 1) gave one task per aperture with a
  fresh clone and a cold solve.  On a 16-scene × 16-aperture
  hydrothermal case the backward pass is several times faster, with the
  same bounds and peak memory.  Results
  are numerically the same up to solver tolerances.  Backward
  parallelism is now capped at the number of scenes; set
  `sddp_options.aperture_chunk_mode = "per_aperture"` (or
  `--aperture-chunk-mode per_aperture`) to get the previous behaviour.
  `plp2gtopt` now writes `aperture_chunk_mode: "per_scene"` instead of
  `aperture_chunk_size: 0`.
- **Result-changing for JSON cases that omit it**: an unset
  `Reservoir.flow_conversion_rate` now resolves to
  `Reservoir::default_flow_conversion_rate` = 0.0036 hm³/(m³/s·h), the
  value the C++ struct default and the docs already stated.  The JSON
  path fell back to 3.6 (the dam³ convention, 1000× larger).  The
  converters always set it explicitly; the SDDP.jl fixtures
  `fast_hydro_thermal`, `hydro_thermal_sddpjl` and
  `hydro_valley_sddpjl` now pin `flow_conversion_rate: 3.6` so their
  goldens are unchanged.  Cases that relied on the implicit 3.6 must
  set it.

- **Result-changing for cases with line losses and no explicit
  `loss_cost_eps`**: `model_options.loss_cost_eps` now defaults to
  `0.1` $/MWh in gtopt (was `0`; constant
  `PlanningOptionsLP::default_loss_cost_eps`).  It prices the loss
  columns of the `piecewise` / `bidirectional` / `adaptive` /
  `tangent_signed_flow` modes (inert for `none`, `linear`,
  `piecewise_direct`), breaking bidirectional-flow degeneracy and the
  small negative-LMP loss arbitrage; on a CEN 5-year SDDP it shrank
  small negative LMPs ~8x vs `1.0` with identical total losses.
  Per-line `Line.loss_cost_eps` still overrides; set `0` explicitly to
  restore the old formulation.  `plp2gtopt` and `plexos2gtopt` no longer
  impose their own default (`1.0`): `--loss-cost-eps` is unset by
  default (key not emitted, gtopt's `0.1` applies) and any explicit
  value, including `0`, is emitted.
- **GeneratorProfile formulation: no spillover column, availability capped
  at pmax.**  The profile row is now `gen ≤ A` with
  `A = min(capacity·profile, pmax_block)` (expansion:
  `gen − min(profile, 1)·C ≤ 0`); spillover `A − gen` is reconstructed at
  output time into the same `GeneratorProfile/spillover_sol` file,
  `spillover_dual` is the dual of the `≤` row (same sign as before) and
  `spillover_cost` is `−dual`.  One LP column fewer per profiled
  generator × block.  A profile with a non-zero `scost` keeps the slack
  column, now bounded `0 ≤ spill ≤ A`.  DemandProfile keeps its
  `unserved` column (bounded by `profile·capacity`, not clamped by
  `lmax`).  gtopt warns once per element when a profile value is > 1
  (profiles are per-unit of capacity, not MW).

- Public Python tools: `pip install ./scripts` no longer installs
  developer tools as commands.  `gtopt_field_extractor`,
  `cen2gtopt.marginal_units` and `cen2gtopt.pcp_validate` remain
  available as `python -m <module>` from `scripts/`.  The aliases
  `sddp_monitor` (use `gtopt_monitor`) and `gtopt_irrigation` (use
  `gtopt_expand`) were removed.  `docs/scripts-guide.md` lists every
  public tool in one table.

- `FlowRight` pass-through `bypass` arc (`junction_a` → `junction_b`) is
  now opt-in: it is built only with `use_bypass: true` (or, for older
  JSON, when `bypass_cost` / the new `bypass_fmax` is set).  It is
  bounded by `bypass_fmax` and written to the output as
  `FlowRight/bypass_sol` (PAMPL: `flow_right('x').bypass`).  Before, any
  right with `junction_b` got a free, unbounded arc that the output did
  not show, so junction balances could not be closed from the results
  (up to 828 m³/s on a Laja carry-mode diversion).  `plexos2gtopt` and
  `gtopt_expand` pmin-as-FlowRight now emit `use_bypass` explicitly;
  validation warns when a consumptive right sets `junction_b` without a
  bypass.

- `plp2gtopt --soft-emin-cost` now defaults to 0 instead of 0.1.  The
  `plpminembh.dat` soft minimum volume uses only the file's per-stage
  cost; stages with cost 0 (a free slack in PLP, i.e. no effect) are no
  longer emitted with an invented 0.1 price.  Results change for cases
  with zero-cost `plpminembh.dat` rows: those floors disappear, as in
  PLP.  Pass `--soft-emin-cost 0.1` for the old behaviour.  A zero
  fallback no longer suppresses the file's positive costs; use a
  negative value (e.g. `--soft-emin-cost -1`) to ignore the file.
- `plexos2gtopt` no longer turns a Waterway `Max Flow Penalty` into a
  per-unit flow cost when `Max Flow` is unbounded.  PLEXOS charges the
  penalty only on flow above `Max Flow`, so it never applies there.
  CEN PCP: `Post_Isla` loses its 3.6 $/(m³/s·h) `fcost`; the `Vert_*`
  spillways (`Max Flow = 0`) keep theirs.  Results change slightly
  around Isla / Curillinque.
- SDDP `low_memory = compress` uses less memory:
  - The in-memory flat-LP snapshots of the scenes of a phase are
    deduplicated. Each cell stores a compressed XOR delta against one
    shared base per phase, and the restore is bit-exact. On the CEN
    2-year case the delta is about 3% of a full snapshot.
  - The default in-memory codec (`sddp_options.memory_codec: auto`) is
    now zstd level 1 instead of lz4. Snapshots are about 30% smaller,
    and decompression stays about 0.1% of SDDP CPU time. Set
    `memory_codec: lz4` to get the previous codec back.
  - When the solution already lies inside its bounds, the cached
    column bounds are dropped as the solver is released.
- The storage loss field `annual_loss` is renamed `annual_standing_loss`
  on every storage element (Battery, Reservoir, LngTerminal,
  HydrogenStorage, AmmoniaStorage, ThermalStorage, AllowancePool,
  VolumeRight), and `model_options.battery_annual_loss` is renamed
  `model_options.battery_annual_standing_loss`.  The name follows PyPSA's
  `standing_loss` and makes clear it is the loss while stored, not the
  round-trip efficiency.  The old keys stay accepted as deprecated
  aliases (JSON input and `--set`), with a one-time warning; setting both
  spellings on one object is an error.  All converters now write the new
  key.

### Deprecated

- `sddp_options.aperture_drop_fcuts` is deprecated and ignored.  It was
  re-added to the JSON contract and to the `plp2gtopt` output (default
  `true`) on 2026-05-12, but no solver code ever read it, and the backward
  pass never adds feasibility cuts, so it had no effect.  It is still
  parsed (old cases load); setting it logs a validation warning.  The
  default is now unset and `plp2gtopt` no longer writes it.

- `sddp_options.aperture_chunk_size` as a mode selector.  When
  `aperture_chunk_mode` is not set, the old integers still work and log a
  deprecation warning: `-1` → `per_scene`, `1` → `per_aperture`, `K > 1` →
  `chunked` with K.  `0` / unset (formerly "auto", which resolved to 1)
  now means the new default `per_scene`, without a warning.  Keep
  `aperture_chunk_size` only as K for `aperture_chunk_mode: "chunked"`.

### Fixed

- A relative `solver_options.param_file` was handed verbatim to the
  backend, so a run started outside the case directory
  (`--set input_directory=<case>`) failed to open `solvers/cplex.prm`
  (CPLEX error 1423) and silently solved with solver defaults; sibling
  files derived from its directory (`cplex_warmstart.prm`, `cuopt.cfg`,
  …) were lost the same way.  Every `SolverOptions` bundle (global,
  monolithic, MIP-start relax, SDDP forward/backward, cascade) is now
  resolved before any plugin sees it; a file found nowhere is a WARN.
- `plexos2gtopt`: a battery whose `BAT_<name>` `Gen_Rating` series is
  0 MW over the whole horizon is out of service — `pmax_charge =
  pmax_discharge = 0`, written explicitly — instead of a working battery
  sized by the 2 MW static `Max Power` placeholder.  Partially de-rated
  units keep their rating series.
- `Junction.drain_cost` is now priced like `Waterway.fcost` and
  `Reservoir.spillway_cost` ($/(m³/s)·h through `CostHelper::block_ecost`,
  i.e. × probability × discount × block duration).  It entered the
  objective raw, so with 2 h blocks a costed junction drain cost half of
  the equivalent `_ver` waterway and the water value of a spilling
  reservoir came out at −c/(fcr·duration) instead of −c/fcr.
- `sddp2gtopt`: a negative PSR water value is kept as a boundary-cut
  slope (it was dropped together with the `efin`/`efin_cost` target,
  which stays positive-only).
- **plp2gtopt: GeneratorProfile written in MW instead of per-unit.**
  Profile-mode pasada centrals (solar / wind / CSP under
  `--pasada-mode auto|profile`) wrote the raw `plpaflce.dat` value
  (MW-equivalent) as `GeneratorProfile/profile`, so
  `capacity × profile` reached ~capacity² (e.g. FINIS_TERRAE_FV
  281.8 × 63.6 MW ≈ 17 900 MW of "available" energy and spillover up to
  2.5e4).  The profile is now `aflce × Rendi / PotMax` clipped to [0, 1]
  (PLP bounds the pasada flow by `min(aflce, PMax/Rendi)`,
  `plp-fijamues.f`); the scalar `afluent` path is fixed likewise.
  Dispatch was unaffected (pmax still capped generation) but
  spillover outputs and LP RHS magnitudes were wrong.  **PLP-derived
  cases converted with profile-mode pasadas must be regenerated**; in
  gtopt-cases the affected ones (profile max > 1) are
  `chile/medium_long_term/cen_long_term_1y`,
  `chile/medium_long_term/plp_23y/case` and
  `chile/medium_long_term/archive/scripts_cases_2y/gtopt_case_2y`
  (Peru / Guatemala profiles are already per-unit), plus derived cases
  such as the cl-pe interconnection merges.

- Parquet solution output: the `scenario`, `stage`, `block` and `uid` key
  columns are now `int32` (the `Uid` type) instead of `uint16`.  Element
  uids >= 65536 were silently wrapped mod 65536 (reservoir uid 101333 was
  written as 35797, colliding with other elements), and block uids of long
  hourly horizons overflowed after ~7 years.  Readers that cast the key
  columns to `uint16` must accept `int32`.

- Naming / unit dialect dictionaries: a stale installed
  `naming_dialects.json` / `unit_dialects.json` (e.g. an old
  `~/.local/share/gtopt/` copy) no longer shadows the running binary's
  dictionary.  The copy compiled into the binary is now the default;
  an external file is read only through `$GTOPT_NAMING_DIALECTS_FILE` /
  `$GTOPT_UNIT_DIALECTS_FILE` (logged, with a warning when the override
  lacks entries of the built-in dictionary).  Install and user share
  dirs are never probed.  Fixes the `annual_loss` deprecated-alias tests
  failing on machines with an older install.
- Multi-stage phases after the first one keep their stage chain.
  `SimulationLP::prev_stage` indexed the GLOBAL stage array with the
  stage's phase-LOCAL index, so in phase 2+ the second and later stages
  got a stage of an earlier phase as predecessor: storage restarted from
  `eini` (free water; a 3-stage hydro case with phases {1, 2} reported
  LB = UB = 1.4 M instead of 6.5 M) and every other `prev_stage` user
  (capacity, flow, volume-right and decision-variable chains) saw the
  wrong stage.  Single-stage phases and the first phase were not
  affected.

- SDDP: cuts loaded onto a multi-stage phase now bind to the phase's
  LAST-stage state column.  A multi-stage phase registers one `efin` per
  stage under the same `(class, uid, col_name)`; the boundary-cut loader
  (`load_boundary_cuts_csv`, incl. the mean-shift target) and the Parquet
  hot-start loader took the first registry match — the end volume of the
  phase's FIRST stage.  Terminal FCFs on the monolithic single-phase LP and
  on multi-stage last phases, and reloaded cuts on multi-stage phases,
  therefore priced the wrong column (a 3-phase hydro case: monolithic
  800 k instead of 6.5 M; reloaded LB 1.7 M instead of 6.5 M).  New helper
  `find_phase_state_col` (`sddp_cut_io.hpp`).

- SDDP: the α floor is derived after the cut row is installed.  Every
  install site (`add_cut_row`, cut sharing, Parquet reload) floored α
  before adding the row, so a cell's first cut was floored from an empty
  cut set — the `α ≥ 0` fallback.  With a negative cost-to-go
  (negative-cost units, rebased FCFs) this reported an invalid LB above
  the optimum for one iteration (3.75 M vs the 0.825 M optimum).  The floor
  is now the documented cut-derived box floor from the first cut on.
  Non-negative-cost models that relied on the implicit first-cut `α ≥ 0`
  should set `sddp_options.alpha_min = 0` (the trial trajectory, and
  where a fixed-iteration run stalls, can change without it).

- `.gtopt.conf`: an unknown key or an unparsable value (number, boolean,
  algorithm) in `[gtopt]` or `[solver.<name>]` now logs one warning naming
  the file, the section and the key; it used to be dropped silently.
  Numbers must be whole tokens (`threads = 4x` was read as 4).  The Python
  tools' sections are not checked.

- JSON `solver_options.log_mode` and `solver_options.scaling` accept the
  names documented for them (`"detailed"`, `"aggressive"`, …); the parser
  only took the numeric codes, which still work.  An unknown name or code
  is an error naming the option and the valid values.  Output JSON keeps
  writing the numeric codes.

- A JSON / `--set` `lp_debug` or `lp_error` now enables LP row/column
  names like the `--lp-debug` / `--lp-error` flags do; only the CLI flags
  did, so a JSON-only `"lp_debug": true` dumped LPs with generic names.

- Cascade levels that rebuild their LP (model-option overrides or a
  `system_file` swap) now inherit the caller's full `LpMatrixOptions`
  (solver, `matrix_eps`, LP names for `--lp-file` / `lp_debug` /
  `lp_error`, `compute_stats`, equilibration, coefficient-ratio
  threshold).  They were rebuilt with `LpMatrixOptions{}`, so e.g.
  `lp_debug` dumps and error LPs of those levels had no names.
  `PlanningLP::flat_options()` exposes the options; each level's are in
  `CascadeLevelStats::lp_options`.

- The solver parameter file's optimality tolerance is no longer silently
  replaced: with `scale_objective != 1` every solve set a typed
  `optimal_eps = 1e-6 / scale_objective`, which CPLEX applies after the
  `.prm`, so a case's `CPXPARAM_Simplex_Tolerances_Optimality` never took
  effect.  The order is now: `solver_options.optimal_eps` (used as given;
  it used to be divided by `scale_objective` too) > the parameter file's
  value > the `scale_objective`-derived default.  One info line reports
  the value used and why.  **Result-changing** for runs that set
  `optimal_eps` or a `.prm` tolerance with `scale_objective != 1`.

- Unknown per-element mode strings are now a validation error naming the
  element, the field, the bad value and the valid values:
  `Line.line_losses_mode`, `Line.loss_pwl_layout`,
  `Line.loss_allocation_mode`, `Generator.cogen_mode`,
  `FlowRight.flow_mode`, `DecisionVariable.cost_type` and
  `UserConstraint.constraint_type`.  They used to fall back silently to the
  global / default setting (`FlowRight.flow_mode` with a log line only).

- Unknown names in the string-typed `model_options` enums
  (`line_losses_mode`, `kirchhoff_mode`, `tiny_reactance_mode`,
  `objective_mode`), globally or in a cascade level, are now a validation
  error.  They used to fall back to the default formulation silently
  (e.g. `"line_losses_mode": "linaer"` ran the adaptive loss model).

- `$GTOPT_CONFIG` pointing at a missing file now logs a warning instead of
  silently falling back to `./.gtopt.conf` / `~/.gtopt.conf`.

- `--solver` is kept by cascade levels that rebuild their LP (model-option
  overrides): those levels used to fall back to the default solver
  (`GTOPT_SOLVER` / plugin priority).

- The `.gtopt.conf` `[solver.<name>]` section and the auto-discovered
  `<input_directory>/solvers/<name>.prm` now follow the solver the LP is
  actually built with (`--solver`, else `GTOPT_SOLVER` / plugin priority).
  Both were applied only with an explicit `--solver`, so a default-solver
  run (e.g. CPLEX picked by priority) silently ignored the case's
  `solvers/cplex.prm`.

- `--lp-error` is applied on its own: it was only honoured together with
  `--lp-debug`, and `--lp-debug` alone reset a JSON `lp_error: true`.

- `options.lp_matrix_options` is now resolved in one place
  (`PlanningOptionsLP::resolve_lp_matrix_options`, applied by
  `SystemLP::create_lp`) instead of only through the `LpMatrixOptions`
  argument the `gtopt` runner builds.  Before, a `PlanningLP` constructed
  directly (unit tests, embedders) and every **cascade level rebuilt with a
  new LP** ignored the JSON `equilibration_method` and the topology
  default (ruiz for multi-bus Kirchhoff) and ran with `row_max`; and the
  JSON fields `ruiz_max_iterations`, `ruiz_tolerance`, `fast_sqrt_method`
  and `validation` were ignored on every path, including the binary.
  An explicit `LpMatrixOptions` field still wins when it is set.  The
  equilibration default is unchanged (ruiz for multi-bus Kirchhoff, else
  `row_max`) and now also follows a cascade level's own `model_options`
  (e.g. a single-bus level 0 uses `row_max`, not the base case's ruiz).
  **Result-changing** for cascade levels ≥ 1 on multi-bus Kirchhoff cases
  (now ruiz) and for runs that set the Ruiz knobs / validation in JSON.
  `LpMatrixOptions::ruiz_max_iterations` / `ruiz_tolerance` are now
  optional (unset = 5 / 1e-3).  The pre-solve "Key options" log now
  prints the effective `use_kirchhoff` (was `false` when unset, though the
  default is `true`) and `scale_objective` (was 1000 for SDDP/cascade,
  whose default is 1).
- SDDP optimality cuts under `equilibration_method: ruiz` (the default
  for multi-bus Kirchhoff cases) had wrong slopes: the next-phase
  `sini` reduced cost was divided by the scale of the current-phase
  `efin` column instead of its own.  Ruiz scales the two columns
  differently, so every state-variable slope was off by
  `col_scale(sini) / col_scale(efin)` and the lower bound could exceed
  the upper bound and the true optimum.  `StateVariable` now stores the
  dependent column's scale with its reduced cost.  **Result-changing**
  for every SDDP / cascade run with ruiz (bounds, cuts, saved cut files,
  dispatch); `row_max` and `none` runs are unaffected.  Cut files saved
  by ruiz runs before this fix carry the wrong slopes and should be
  regenerated rather than hot-started from.
- `sddp2gtopt` (NCP/`.dat` front-end): `dcirc.dat` `RESIS`/`REACT` are
  now read as **% on the 100 MVA base** (`x_pu = X/100`, `r_pu = R/100`),
  as the JSON/CSV front-end already did, instead of ohms converted with
  the `Volt.dat` bus voltage (`X·100/kV²`).  The ohm reading shrank 69 kV
  reactances ~50x (and ~5000x on buses whose `Volt.dat` kV copies the
  name suffix, e.g. 692/691/342/232), so on the Guatemala NCP case 62
  real circuits (47 at 69 kV, 10 at 230 kV, 5 at 34.5 kV) fell below
  `dc_line_reactance_threshold` (105 "ties" instead of the 52 real
  couplers) and were modelled as zero-impedance ties; that, not the
  couplers, caused the 9160 MWh curtailment previously attributed to
  `tiny_reactance_mode`.
  Mistyped `Volt.dat` voltages (non-standard kV equal to the bus-name
  suffix) are corrected with a warning (692 → 69, 342 → 34.5, 232 → 230,
  132 → 13.8); they now only affect the `Bus.voltage` metadata.

- `VolumeRight.right_reservoir`: the `direction` sign was inverted —
  a child "withdrawal" (`direction = -1`, the default) credited the
  parent ledger.  It now debits it, as documented; `+1` credits it.
- `plp2gtopt`: a seepage (`plpfilemb.dat`) whose receiving junction's
  outlets cannot carry the largest flow of the seepage line on
  `[emin, emax]` gets a free drain at that junction, bounded by that
  excess.  Before, the outlet
  capacity capped the source reservoir volume and priced the junction
  negatively (CIPRESES → FILT_CIPRESES, outlet 20.1 m³/s).  PLP has the
  same cap; `--plp-legacy` keeps it and only warns.

- SDDP / cascade with `lp_reduction`: fixed-discharge inflows are no
  longer folded into the junction-balance RHS.  The fold removed the
  inflow column, so the aperture backward pass and sampled forward
  realizations (`update_aperture`) silently kept the base scenario's
  inflow at every folded junction — on converted PLP cases all
  reservoir/junction inflows.  Monolithic runs still fold.

- `gtopt_check_topology` no longer crashes on cases whose line fields
  name input files (e.g. `"reactance": "cl_reactance"`).  Scheduled
  fields (scalars, inline per-stage / per-block arrays and
  `<input_directory>/<Class>/<name>` tables) are resolved per stage
  with the new shared `gtopt_shared.field_sched.StageSchedResolver`.
  Static checks run on the union of in-service lines with min |X|.
  An isolated bus without demand no longer causes exit code 2.

- `gtopt_results_summary` and `gtopt_timeseries_export` read the current
  gtopt output layout (`<field>_sol_s<k>_p<k>.csv` shards and
  hive-partitioned Parquet); they reported all-zero KPIs and a single
  `phase=0_part` sheet before.
- `gtopt_monitor --no-gui` exits when a monolithic run reports `done`.
- `gtopt_check_pampl` no longer warns on `param`/`var` declarations.
- `sienna_to_gtopt` and `gtopt_check_fingerprint` exit with a clear
  message instead of a traceback on an unavailable bundle / missing file.

- plexos2gtopt AEMO ISP (weekly and expansion): pumped hydro (PHES)
  written as a Battery now carries `annual_standing_loss: 0`, so it no
  longer inherits the Li-ion self-discharge default
  (`model_options.battery_annual_standing_loss`).
- In-memory decompression (`low_memory = compress` LP snapshots) now
  checks that every codec (zstd, gzip, lz4, snappy, uncompressed)
  produces exactly the recorded original size.  A corrupt, truncated or
  mismatched buffer used to be zero-padded and loaded as a garbage LP;
  a snappy stream whose embedded length exceeded the recorded size
  overran the output buffer.  The cross-scene XOR-delta restore checks
  the base size too, and the snapshot restore validates the flat-LP
  metadata against the buffer size and leaves the snapshot empty on
  failure.
- `sddp2gtopt`: interconnection imports are unavailable in hours with no
  offer.  With `PRECIOSMEX.csv`, an hour priced blank or 0 for an import
  unit (or the regional `INTER-SAL` series it inherits) closes that unit
  for the hour; the Guatemala daily programs price only the billed days,
  so the look-ahead days had free, uncapped imports.  Without
  `PRECIOSMEX.csv`, an import unit whose fuel cost is 0 has no offer and
  is left out.  RESTMEX fixed or floor schedules keep a unit available,
  and their unscheduled hours now fall back to the hourly cap instead of
  the installed capacity.  `--import-limit` stays an opt-in cap.

## [1.1.0] - 2026-11-20

GTOpt 1.1.0 is the first release with packaged binaries, and it widens
the set of real systems GTOpt can read.  Highlights:

- **Ready-to-run binaries**: a Linux x86_64 build (Ubuntu 24.04+,
  Debian 13+) with the HiGHS and COIN-OR CBC/CLP solver plugins, its
  debug symbols, and the Python tools as a wheel (`gtopt_scripts`), all
  with SHA-256 checksums.
- **More PLEXOS models**: `plexos2gtopt --plexos-model` converts general
  multi-model PLEXOS databases for a chosen window (used for the Irish
  SEM model), and `plexos2gtopt.aemo_isp` builds dispatch weeks from the
  AEMO 2026 ISP model of the Australian NEM (prototype).
- **Transmission planning inputs**: `plp2gtopt` reads the CEN works plan
  (`mantlin.csv`), so the PET 2027 transmission planning bases convert
  with lines entering service and changing ratings as planned.
- **Other converters**: `psse2gtopt` cost and line-limit overlays and
  voltage-dependent loads; `pp2gtopt` time series and representative
  blocks; `sddp2gtopt` multi-file PSR SDDP cases.
- **Real cases**: the companion repository
  [gtopt-cases](https://github.com/gtopt-community/gtopt-cases) with
  cases from Australia, Chile, Guatemala, Ireland, Peru and Poland.
- **One home for end-of-horizon cuts**: `FutureCost` is now the single
  source of the boundary cuts and every converter emits it; the old
  boundary-cut option keys still work as deprecated aliases
  (`tools/migrate_future_cost.py` rewrites them).
- **Inspection**: LP files can also be written as MPS, with an optional
  scaling sidecar; `gtopt --version` reports the exact build revision.
- **Fixes**: code-scanning security findings in the GUI service, the
  water-value unit of PLP boundary cuts, and Python data files missing
  from wheel installs.

Upgrading from 1.0.0: cases keep working; run
`tools/migrate_future_cost.py` to move deprecated boundary-cut keys to
`FutureCost`, and see *Deprecated* below.

The detailed list of changes follows.

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

- **`plp2gtopt` reads `mantlin.csv`**: the transmission works plan of CEN
  PLP cases (e.g. the PET 2027 transmission-planning bases) is merged after
  `plpmanli.dat` block by block; lines now enter/leave service and change
  rating, R and X as planned (per-stage voltage/resistance/reactance
  schedules).

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

### Testing

- **`test_water_fail_cost_magnitude.py` runs on the published `plp_2y`
  case** (previously on a private case).  The `lost_pf` reference table
  was recomputed from plp_2y data with its derivation documented, and is
  checked end to end (`efin_cost = anchor × lost_pf × 1e6/3600`); the
  boundary-cut overwrite is checked separately against the emitted
  `boundary_cuts.csv`.  The old tests asserted obsolete defaults:
  `efin_cost` is only emitted with `--soft-storage-bounds` (hard bounds by
  default) and `--no-cuts-govern-terminal`, and the default
  `phi_expectation` CSV is no longer pre-divided by `NVarPhi`.

- **Monolithic keeps the boundary cuts of an SDDP-converted case**: new
  doctest pins that a FutureCost element (what the converters emit)
  loads under `method = monolithic`.

## [1.0.0] - 2026-11-18

First public release of GTOpt, the open-source optimizer for planning
and operating energy systems, under AGPL-3.0-or-later with the GTOpt
Solver Plugin Exception.  It includes:

- **One model from minutes to decades**: expansion planning of
  generation, transmission, storage and demand; hydrothermal scheduling
  with SDDP and multi-level cascade SDDP; unit commitment (three-bin
  MIP) and economic dispatch with DC power flow and line losses.
- **Storage and hydro**: reservoirs, cascades, pumped hydro and
  batteries with water values and storage opportunity costs, and
  irrigation agreements written as PAMPL user constraints.
- **Emissions, reserves and other energy carriers**: emission prices and
  caps by zone, reserves and inertia, hydrogen, ammonia, thermal storage
  and LNG in the same LP.
- **Solvers as plugins**: HiGHS, CBC/CLP, CPLEX, Gurobi, MindOpt, SCIP
  and cuOpt, loaded at run time.
- **Converters** from PLP, PLEXOS, PSR SDDP, PSS/E, pandapower and other
  formats, with open JSON/Parquet inputs and outputs.
- **Interfaces**: command line, Python tools, a browser GUI and a REST
  web service.
- **Documentation**: the full LP/MIP formulation and the white paper,
  with validation on IEEE networks, pandapower, published SDDP
  benchmarks and the Chilean daily schedule.

1.0.0 is a source release: build from source, or use 1.1.0 or later
for packaged binaries.  The changes made just before the release are
listed below.


### Added

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


- **Batteries now self-discharge 4 %/month by default** (behaviour
  change).  A `Battery` whose `annual_loss` is absent uses
  `0.48` p.u./year (`0.04 × 12`, linear convention: hourly drain
  `annual_loss/8760`), the combined lithium-ion cell + BMS figure from
  Battery University BU-802b (cell 1–2 %/month + ≈ 3 %/month protection
  circuit) and the Wikipedia "Self-discharge" table (Li-ion 2–3 %/month).
  Besides realism it removes the cost-free charge/discharge zig-zag across
  blocks whenever the marginal energy has a positive price (it does NOT
  break the tie when the window price is exactly 0, nor same-block
  simultaneous operation).  New `model_options.battery_annual_loss`
  overrides the default case-wide; **restore the old lossless behaviour
  with `--set model_options.battery_annual_loss=0`** (or `annual_loss: 0`
  per battery).  Explicit per-battery values, including `0`, win;
  Reservoir and other storages are unaffected.  gtopt logs once (INFO) how
  many batteries took the default.  Results of every case with batteries
  that omit `annual_loss` change slightly (CEN PCP weeks, AEMO ISP weeks,
  PLP-converted cases, PET27).
- **Battery power-envelope valid inequality on by default** (behaviour
  change): each battery converter emits, per block,
  `p_charge/Pc_max + p_discharge/Pd_max ≤ 1` (`Converter/power_envelope`
  rows, row-scaled by `max(Pc, Pd)`), cutting simultaneous full-power
  charge + discharge — the vertex a lossy battery uses to dump surplus
  energy at zero/negative prices.  Partial simultaneous operation inside
  the envelope is still LP-feasible (only binaries remove it).  Skipped
  when one direction has no rating or the hard `pmin_*` floors force a
  point outside the envelope.  Disable with
  `--set model_options.battery_power_envelope=false`.
- **Converters write explicit `annual_loss` for non-lithium "batteries"**:
  `plexos2gtopt --plexos-model` pumped storage rewritten as a Battery (SEM
  Turlough Hill) and `sienna_to_gtopt` PHES get `annual_loss: 0`
  (`type: pumped_hydro` / `pumped`); `plp2gtopt` emits
  `annual_loss = −12·ln(1 − mloss/100)` for a nonzero `plpess.dat` `mloss`
  [%/month] (exact PLP `genpdess.f` per-block drain) and omits it for
  `mloss = 0` / `plpcenbat.dat` so the gtopt default applies.  This also
  fixes the DCMod=2 regulation-tank Reservoir `annual_loss`, previously
  `mloss × 12` (100× too large: `mloss` is a percentage).

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

- **File-backed battery schedules on the synthesized elements**: a unified
  battery's file-backed `active`, `pmax_discharge`, `discharge_cost`,
  `pmax_charge`, `pmin_charge`, `charge_cost` or `pmin_discharge` (e.g.
  `"active": "act"`) was copied verbatim onto `<bat>_gen` / `<bat>_dem` /
  `<bat>_conv` / `uc_<bat>_gen`, which then looked up their own class
  directory and uid (`Generator/act` for the generator's uid) and failed
  ("Can't read table for class 'Generator'…" / "Can't find element …").
  `expand_batteries` now rewrites such references to
  `"Battery@<field>#<battery uid>"` — a new `#<uid>` suffix of the
  `<Class>@<field>` file-reference grammar — and the synthesized elements
  also inherit the battery's `active` schedule.
- **Battery charge/discharge decoupled when one direction had no rating**:
  when `pmax_charge` was 0 in a block (or `pmax_discharge` under
  `--lp-reduction`), the synthetic charge demand (discharge generator)
  column is elided and `ConverterLP`
  skipped BOTH coupling rows, leaving the battery's `finp` column free in
  `[0, +inf)` — free charging, energy from nowhere — and the discharge
  generator as free generation at `discharge_cost`.  The uncoupled
  battery-side flow is now pinned to 0 and the surviving direction is
  still coupled.  A battery with `pmax_charge = 0` in EVERY block of a
  stage also no longer throws (`flat_map::at`) — tolerant
  `lookup_load_cols`.  Affects cases with maintenance windows that zero a
  battery's charge or discharge rating (plp2gtopt `plpmanbat`/`plpmaness`,
  PLEXOS per-block ratings).
- **Work-pool swap gate no longer latches closed on cold swap**: the
  process-`VmSwap` dispatch gate blocked new tasks whenever `VmSwap` was
  at/above a hard-coded 2048 MB.  `VmSwap` counts pages that *are* on
  swap, so once the kernel had parked cold pages there (other load on the
  host, with zero swap I/O) the gate never reopened and the pool ran only
  the one always-admitted task (`Active: 1/31`, "process VmSwap 3343 MB
  >= 2048 MB", "stall recovery nudge fired" for over an hour on a host
  with ~120 GB available).  The gate now engages only while `VmSwap` is
  over the limit **and still growing** by more than a delta per 5 s
  window (`SwapGrowthGate`), releases after one quiet window, and logs
  one info line on engage and on release with the reason.  Both knobs
  are now exposed: `sddp_options.pool_max_process_swap_mb` (default
  2048, `0` = off; CLI `--swap-limit`) and
  `sddp_options.pool_swap_growth_mb` (default 64; CLI `--swap-growth`).
- **Irrigation agreements — PLP-fidelity revision** (deep review vs
  `genpdmaule.f` / `genpdlajam.f`).  Maule: La Invernada IQRNIN/IQNINV
  rule only when `RelaxInvern = F` and no phantom `econ_invernada`
  (CIPRESES no longer taxed); ordinary-reserve ledgers provisioned per
  `FijaMaule`; annual/seasonal counters emitted as remaining-rights
  ledgers (non-negative state value); Res. 105 `qa105 ≤ min(qarmr,
  QRiego105)` with district `retiro + deficit = pct · qa105` (Melado an
  equality + deficit); manual-year tables and Cota 425 honoured.  Laja:
  `laja_retiro_maximo` no longer carries the seepage term (it priced the
  lake's water NEGATIVE), PLP costs pinned by default
  (`--no-pin-agreement-costs` to opt out), economies only under the 1958
  agreement, manual withdrawals applied, WARN for unmodelled forced flows
  and `ini_econ`.  `validate_planning` rejects a rewarded FlowRight
  (`uvalue > 0`) with no junction and no UserConstraint reference.
- **`VolumeRight.bound_rule` units**: the rule volume [hm³] is converted
  to a rate `V / (0.0036 · stage_hours)` before capping the extraction;
  `initial_rule_bound` (VolumeRight and FlowRight) reads the predecessor
  phase's reservoir `efin` instead of the case `eini`; `update_lp` no
  longer skips an unchanged bound (stale under `low_memory`).
- **SDDP stop during the forward pass**: a `request_stop` / sentinel /
  API stop hit mid-forward (`ErrorCode::Cancelled`) is no longer counted
  as an infeasible scene — no cut rollback, no "all scenes infeasible"
  abort; the partial iteration is discarded and the run proceeds to the
  simulation pass and output.
- **AMPL registry race**: the user-constraint silent-zero lookups
  (`find_ampl_variable_for_element`, `find_ampl_class_attribute`) scanned
  every (scene, phase) cell while other scene threads were writing theirs
  — a data race that segfaulted the plp_2y irrigation build.  They now
  read a mutex-guarded registration index.
- **SDDP hot start**: a requested hot start that ends cold (cuts file
  ignored because `recovery_mode = none` — i.e. no `--recover` —, load
  error, empty file or missing cut directory) now logs
  `SDDP HotStart: cuts=cold … NOT applied: <reason>` at WARN.
- **Irrigation — end-to-end findings** (new PLP → plp2gtopt → gtopt
  e2e suite): an irrigation-diversion FlowRight whose PotMax is the PLP
  9999 sentinel was emitted without `fmax`, which the C++ resolves to 0
  — the offtake was pinned shut (plp_2y: RieSur123SCDZ, hence every
  Res. 105 district, and RieSaltos); it now carries `PotMax / Rendi`.
  The season re-provision reads the stage-start reservoir volume
  (`VolIni`, genpdlajam.f:575-576) instead of `(vini + vfin)/2`.  The
  Laja anticipado up-counter has no outflow (`fmax: 0`; a free
  extraction let the LP drain it and dodge the December debit).  A
  fixed-discharge Flow folded by `lp_reduction` stays resolvable in
  user constraints (constant moved to the RHS).  `validate_planning`
  warns on a `bound_rule` with no segments.
- **Rights ledgers — free disposal** (`VolumeRight.forfeit`): a
  zero-cost `forfeit` column lets unused rights be dropped, like a free
  spillway.  On plp_2y the Maule ledgers carried negative balance duals
  (degenerate cap vertex, or `emax < eini` forcing use) that SDDP cuts
  propagated upstream (all `maule_vol_gasto_riego_temp` cut slopes were
  negative).  The Laja and Maule templates now set `forfeit: true` on
  every depletion ledger (not on up-counters or economies).

- **`plexos2gtopt.aemo_isp` hydro**: reservoirs carry
  `flow_conversion_rate = 0.0036` and `Max Spill`; pumped-storage units
  (`Pump Load` / `Pump Efficiency`: Kidston, Shoalhaven, Wivenhoe,
  Tumut 3) pump through a tail → head `Waterway` + `Pump` + flexible
  `Demand`; week-start levels follow the source data (horizon-start
  `Initial Volume`, else `Target Week` / `Target`, else `Initial Volume`
  above `Min Volume`, else mid level).  The end of the week is
  constrained only by the source data (`Target Week` → `efin`, `End
  Effects Method` Recycle → `efin = eini`); the other (Free) storages
  get a hard, unpriced `efin = eini` (`--hydro-end hold`, the default;
  `--hydro-end free` drops it).  A single week has no future value of
  water (it should come from SDDP cuts).  `--hydro-water-value V` is an
  opt-in end value (soft `efin = eini`, shortfall at V × the cascade's
  MWh per hm³).  `--legacy` is unchanged.

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

- **+160 unit tests**: coverage increased from 71.5% to 77.3%, with new
  tests for `sddp_cut_io`, `sddp_monitor`, battery expansion, reservoir
  efficiency, variable scaling, and enum options.

[Unreleased]: https://github.com/gtopt-community/gtopt/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/gtopt-community/gtopt/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/gtopt-community/gtopt/releases/tag/v1.0.0
