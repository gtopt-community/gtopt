# Changelog

All notable changes to GTOpt (Generation, Transmission and storage
Optimization) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

An overview of the new capabilities with links to their documentation is
in [docs/whats-new.md](docs/whats-new.md).

### Added

- **Radial lines: one-way and dead-end detection
  (`model_options.line_direction_mode`, `Line.flow_direction`).** The
  bridges of the transport graph (every line ever active, DC links and
  candidates included) are classified from the injection signs on each
  side: one-way `ab` / `ba` (toward a load-only side, away from a
  generation-only side), dead end (nothing, or the same sign, behind the
  line) or both. `structural` drops the impossible direction of one-way
  lines (reverse flow / loss / segment columns; one-sided
  `tangent_signed_flow`) and does not build dead-end lines; lines in
  user constraints or line commitments stay untouched. `report` only
  logs the classification. `Line.flow_direction = ab | ba | both | auto`
  pins or opts out per line. Default `off`: the LP is unchanged.
  `gtopt_check_topology --directions` previews the classification.
- **`dlr2gtopt` / `re2gtopt`: one `--weather-source {explorador,open-meteo}`
  option for `history`**, through a weather layer both tools share
  (`gtopt_shared/weather_source.py`). `explorador` is the Explorador
  Eólico WRF 1 km hindcast (2010 or 2015); `open-meteo` is the Open-Meteo
  archive (ERA5 from 1940, ECMWF IFS from 2017). Years outside a source
  fail unless `--climatology calendar` (replay the source year on the same
  local month/day/hour) or `month-hour` (per month × hour statistic) is
  set. Each output gets a `.provenance.json`. Without the option the
  previous default is unchanged. The free Open-Meteo API is
  non-commercial only (`--api-key` / `--base-url` for commercial use).
  A third source, `explorador-solar`, reads the Explorador Solar
  (2004–2016): the hourly series (needs the site's download-profile
  declaration, `--explorador-solar-profile`, no default) or the
  month × hour means (`--explorador-solar-mode aggregate`). The hourly
  file is parsed in its real layout (metadata and variable table to the
  provenance; `difh` as DHI); its stamps were fitted as fixed UTC−4,
  centred on the hour (`--explorador-solar-label`), and its wind height
  is 5.5 m as documented by the site's SOLEVAPP report
  (`--explorador-solar-wind-height`). With no wind direction,
  `dlr2gtopt history` needs an explicit attack angle (45° study value,
  0° conservative variant, `--perpendicular-wind` 90°), or
  `--wind-angle-source explorador-eolico`: the angle per span and local
  month × hour from the Explorador Eólico WRF dominant direction
  (speed-weighted modal 22.5° sector), a rating parameter only.

- **`re2gtopt`: wind and PV plant profiles on the DLR typical year.**
  A new script turns weather into hourly output per plant and writes
  gtopt `GeneratorProfile` schedules, using the same historical hours
  that `dlr2gtopt typical-year` picks for the line ratings.
  - Weather: Explorador Eólico recon (10–140 m, the DLR grid and
    stencil) and ERA5 (Open-Meteo).
  - Wind model: density normalisation, OEDB or generic power curves,
    farm smoothing, wake, a soft cut-out and an explicit max-output
    cap (default 0.95).
  - PV model (pvlib): DIRINT, Perez, single-axis tracking with
    backtracking, Faiman cell temperature, soiling and DC/AC clipping.
  - Each plant's equipment is sized to reproduce its reported Pmax,
    with a per-plant peak check.
  - `inventory`: plant location and equipment from the CEN Infotécnica
    API. `fetch-cen`: hourly generation, curtailment and forecasts from
    CEN public files, cached and never committed.
  - `calibrate`: fits on observed output by scalar, empirical curve or
    quantile map. Curtailment is treated as censoring, so the profile
    is *potential* generation. Includes leave-one-year-out validation.
  - `typical`: the default `--case mean` preserves the long-term
    monthly energy. P90 and other quantiles are energy-yield cases.
  - `to-blocks` / `repeat`: use dlr2gtopt's own hour->block mapping.
    `--block-stat dlr-hour --cf-history` takes each block's CF at the
    DLR block's exact historical hour.
  - It reads the DLR provenance columns (blend partner, knn members)
    and the same `--timezone` / `--dst` calendar as dlr2gtopt.
  - Generic defaults for single plants: no farm smoothing (`ninja` stays
    available for fleets), and a PV DC/AC ratio by commissioning year
    (1.05 to 2015, 1.2 for 2016–2019, 1.3 from 2020; overridable).
  - Literature, defaults and data licences:
    `scripts/re2gtopt/README.md`.
- **Point-sampled stages** (`Stage.sampling = "point"`, `Block.time`,
  `Stage.period_hours`, `Stage.periodic`): block values are samples at
  node times and block durations are quadrature weights; a stage can
  sample one typical day or week that repeats, with every intra-period
  coupling wrapped around the period.  A generic cell partition counts
  min up / down times, commitment periods and daily-cycle windows on the
  quadrature cells.  See
  [Typical-week sampling](docs/methods/typical-week-sampling.md).
- **Storage integration rules on point stages**
  (`model_options.storage_integration`,
  `reservoir_storage_integration`, `battery_storage_integration`,
  per-element `storage_integration`): `block` (alias `cc` /
  `clenshaw_curtis`: the Clenshaw–Curtis weights on a Chebyshev grid),
  `trapezoid`, `lg4` / `lg6`, `lg4_pos` / `lg6_pos`.  Built-in defaults:
  `lg4_pos` for reservoirs, `block` for other storages; `plexos2gtopt`
  and `tcheby2gtopt` write `cc` (`--point-storage-integration`).
- **Typical-week sampling in `plexos2gtopt`**: Chebyshev weeks and days,
  uniform and point weeks, a Wednesday-noon window and the
  `--ramp-refine` sunrise / sunset refinement.
- **`tcheby2gtopt`** (`python -m tcheby2gtopt`): Chebyshev case
  converter and the `cheby_small` case library, with per-unit
  commitment-period classes on point stages.
- **Power-based unit commitment** (`model_options.uc_formulation =
  "power_based"`, per unit `Commitment.uc_formulation`; Morales-España,
  Gentile & Ramos 2015): power at block boundaries, trapezoid block
  energy, exact ramps, start-up / shut-down trajectories
  (`startup_trajectory`, `shutdown_trajectory`, `trajectory_step`) and
  ramp-capability reserves.  `cucdata2gtopt` can write it.
- **Commitment grids**: `Commitment.commitment_grid`,
  `Block.commitment_cell` and `Commitment.ramp_allowance` (`boundary`
  by default: only ramp rows that cross a commitment-period boundary get
  the start-up / shut-down allowance; `period` restores the looser form)
  on block and point stages; backward interval-slope ramps on point
  stages.
- **`model_options.prev_stage_link`** (`stage` default, `none`): what a
  user-constraint `<attr>_prev` term reads at the first block of a stage.
- **Gas network and LNG terminals in PAMPL**: user constraints can
  reference gas nodes, pipes, compressors, supplies, demands and LNG
  terminals.
- **GUI service security**: access-token authentication on non-loopback
  binds, security headers and bounded uploads (see
  `guiservice/INSTALL.md`).
- **`lp_matrix_options.pin_logic_rows`** (bool, default `false`) and
  `SparseRow::pin_scale`, the row-side counterpart of
  `SparseCol::pin_scale`.  With the option on, pure commitment-logic
  rows (Commitment and LineCommitment transition, exclusion, start-up
  tier and window, max / min starts; Converter charge / discharge
  exclusivity; Plant commit with unit coefficients / unique: all ±1
  rows) keep row factor 1 under `row_max`, `ruiz` and the incremental
  `add_row` / `add_rows` normalisation; column scales still apply and
  the rows still enter Ruiz's column norms.  Physical duals are
  unchanged.  Min up / down rows are never pinned: their aggregated
  form carries the window length (UT · startup), and pinning it widened
  uc_small's matrix range from 80 to 160.  Off by default: it left
  kappa unchanged on every benchmark case and made a cuc39 MIP much
  slower.
- **`solver_options.accept_optimal_infeas`** (bool, default `true`).
  A CPLEX LP that ends `CPX_STAT_OPTIMAL_INFEAS` (5, optimal with
  unscaled infeasibilities) is accepted as optimal, with one warning per
  solve giving the max unscaled primal and dual infeasibility, instead of
  running the barrier -> dual -> primal fallback cycle (whose
  presolve-off attempts stalled or ended in a numerical "infeasible" on
  the CEN PCP corridor LPs). `false` restores the former rejection.
  Also under `--set`, per SDDP pass and in `monolithic_options`. Other
  solvers ignore it.
- **Fallback cycle logging.** Every algorithm-fallback cycle
  (`max_fallbacks`) now logs one WARN line listing each attempt's
  algorithm and native solver status (code and name, new
  `SolverBackend::raw_status()`, plugin ABI 18) and the outcome; the
  per-attempt `resolve` lines were DEBUG only, so a monolithic run's
  failing attempts were invisible.
- **Relative zero-impedance floor** (`model_options.tiny_reactance_rel_threshold`,
  default `0.01`). A line is a zero-impedance tie when `|X/V²|` is below
  `max(dc_line_reactance_threshold, 0.01 × median |X/V²|)`. The absolute
  `1e-6` floor missed the PLEXOS `X = 1e-4` p.u. generator-connection
  placeholders, which sit exactly at `x = 1e-6`. On CEN PCP 2026-11-09 it
  ties 44 lines instead of 1, and the x_τ spread drops from 1.6e4 to 3.3e3.
  `0` restores the absolute floor. The effective floor is written back
  into `dc_line_reactance_threshold`, so every consumer uses it.
- **Lossless ties** (`model_options.tie_line_losses`, default `false`). A
  zero-impedance tie gets no loss columns or rows. `true` keeps the
  legacy tie loss model.
- **Ordered-fill bound on PWL loss segments**
  (`model_options.loss_segment_bound`, default `true`). Each segment column
  is capped at `min(w_k, max(0, tmax − b_{k−1}))`. This is a valid
  tightening cut that only bites when the segments reach past the flow
  cap: a `loss_envelope` above `tmax` used to leave the last segment
  unbounded. It limits non-physical loss inflation; it does not fix
  arbitrage. `false` restores the width-only bounds.

- **`solver_options.deterministic_parallel`** (bool, default unset =
  opportunistic, the former behaviour). `true` runs CPLEX in
  deterministic parallel mode (`CPX_PARAM_PARALLELMODE = 1`): the same
  LP gives the same basis, duals and reduced costs whatever the machine
  load. Also under `--set`, per SDDP pass and in `monolithic_options`.
  The e2e golden tests (`run_gtopt.cmake`) now set it: under a loaded
  `ctest -j20` CPLEX run, the opportunistic barrier + crossover ended
  `exp_bat_4b_24` (a dual-degenerate LP) on another optimal basis in ~1
  run of 15, failing its `Demand/load_cost` and
  `Generator/generation_cost` reduced-cost goldens.
- **Numerical-stability monitor.** Tests that fail when a change worsens
  the scaling or conditioning of the LP (docs/numerical-stability.md):
  - `test_numerical_stability.cpp`: six canonical cases (Kirchhoff
    network, battery, hydro, piecewise losses, UC with ramps, capacity
    expansion). Matrix / objective / RHS / bound ranges per case, no
    tiny or huge matrix entries, default scaling never wider than
    `--no-scale`, and per-solver kappa baselines (HiGHS, CPLEX).
  - `gtopt_check_output --numerics`: runs a case, dumps its LPs and
    checks kappa, SDDP kappa growth and cut-coefficient range, the LP
    ranges, cell status and solver numerical warnings against
    `integration_test/numerics/numerical_stability_baselines.json`;
    `--update-baselines` rewrites it, `--top N` lists the entries that
    drive the matrix range.  CTest label `numstab` (three small cases,
    default set); the CEN 2-year case (first phase) runs with
    `GTOPT_NUMERICS_TESTS=ON` and `GTOPT_CASES_DIR`.
- **Dynamic line rating (DLR) through critical spans.** A line can now
  carry a `dlr` object with one or more critical spans (one
  representative span is enough). Each span has a per-block weather
  forecast: ambient temperature, wind speed and direction, and global
  horizontal solar irradiance. For every block gtopt computes the span's
  ampacity with the IEEE Std 738 steady-state heat balance, converts it
  to MW and limits the line flow by it. The weather is input data, so
  the model stays linear.
  - **PLEXOS line limits.** New optional per-block line fields
    `max_flow` / `min_flow` (aliases of `tmax_ab` / `-tmax_ba`),
    `max_rating` / `min_rating`, `overload_max_rating` /
    `overload_min_rating` (the hard cap when the band is priced),
    `outage_max_rating` / `outage_min_rating` with `units_out`
    (maintenance derate), and `limit_penalty` (alias of
    `overload_penalty`). `min_*` are signed (≤ 0), as in PLEXOS.
  - **Limit causes.** `conductor_rating_ab/ba` (replaced by DLR),
    `equipment_rating_ab/ba` (TTCC, CCSS, wave trap, breaker) and
    `security_limit_ab/ba` (stability, N-1). These follow the CEN ERST
    causes.
  - **Combining the limits.** Every field is optional: unset means no
    limit, NaN cells are unset, and an unset B→A field never copies A→B.
    The limit is the min over the set fields, carried by one bound or row
    per direction. The binding part's dual goes to
    `Line/<part>{p,n}_dual`. Every field is also a PAMPL parameter.
  - `dlr.limit_mode` / `model_options.dlr_limit_mode`: `replace` or
    `min`. `replace` makes the DLR rating replace `conductor_rating_*`
    (without one, `max_rating`) and `tmax_normal`. `min` uses
    `min(static, rating)`. When unset, the mode is `replace` with a
    conductor rating and `min` otherwise, so existing cases are
    unchanged. Flow, outage, equipment and security limits always apply.
  - The DLR rating follows `line_thermal_limit_mode` like `tmax`:
    `|f| ≤ T` (`flow`), `|f| + a·ℓ ≤ T` (`sending`), `|f| + ℓ ≤ T`
    (`full`), in both directions, both span modes and the emergency
    tier. New tests pin this. When you audit a run, `Line/loss_sol`
    already holds the total loss (`lossp + lossn`). Do not add
    `Line/lossn_sol` to it.
  - `span_mode = "min"` (default) applies the minimum over the spans
    wherever `tmax` applies today. `"rows"` adds one limit row per span
    and block instead, and drops the rows of spans that cannot bind.
    Both give the same optimum.
  - `tmax_conductor_emergency` adds an emergency rating: above the
    normal rating the flow pays `overload_penalty`, up to the emergency
    rating.
  - A precomputed per-span MW schedule (`rating`) can replace the
    weather.
  - Every behaviour is an option: `model_options.use_line_dlr` (default
    `true`; `false` ignores every `dlr` block for static-vs-DLR runs),
    `dlr.use_emergency`, `dlr.safety_factor`, `dlr.v_pu`,
    `dlr.power_factor`, the low-wind rule (`low_wind_speed`, default
    0.5 m/s, and `low_wind_angle`, default 45°) and `dlr.solar_mode`
    (`ghi`, the default, uses the irradiance as absorbed flux `α·S·D`;
    `none` ignores the sun). The conductor resistance can be given as
    `r25`/`r75` or as `r20` + `alpha_r` + `ac_factor`.
  - The overload slack of every line (`overload_penalty` band) is now
    bounded: `overload_slack_bound = "thermal"` (default) caps it at
    `min(hard − normal, max_overload_ratio × normal)` — on DLR lines the
    IEEE 738 emergency minus normal rating, the conductor's thermal
    headroom. `"ratio"` uses `max_overload_ratio × normal`, and
    `"unbounded"` keeps the old open slack. `max_overload_ratio`
    defaults to 1.5 (flow ≤ 2.5 × normal), well above reported DLR gains
    (DOE SGDP 2014: +10–25 %; up to ~40 % at windy sites, 50–72 %
    outliers; EPRI 2025: DLR below static 30–40 % of the time). Both
    options can be set per line and in `model_options`. The span duals
    stay well defined when the flow sits exactly at the emergency
    rating.
  - New outputs: `Line/dlr_rating_sol`, `Line/critical_span_sol` and
    `Line/span_thermal_dual` (the value of one more MW of rating on each
    span).
  - The span selection method is Matus, Saez, Favley, Suazo-Martinez,
    Moya, Jimenez-Estevez, Palma-Behnke, Olguin and Jorquera,
    "Identification of Critical Spans for Monitoring Systems in Dynamic
    Thermal Rating", IEEE Trans. Power Delivery 27(2):1002–1009, 2012,
    DOI 10.1109/TPWRD.2012.2185254 (open access: [ResearchGate](https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating),
    [PDF (Universidad de
    Chile)](https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)).
    Selecting the spans is done outside
    gtopt. The ampacity follows IEEE Std 738-2012.
  - The input reference now also documents the existing Line fields
    `tmax_normal_ab` / `tmax_normal_ba`, `overload_penalty`,
    `in_service` and `enforce_level`.
- **`dlr2gtopt`: dynamic line rating inputs.** A new script builds the
  `dlr` object of a gtopt Line. It goes from tower coordinates to spans,
  per-span hourly weather, the IEEE 738 ampacity and the critical spans
  of Matus et al. (IEEE TPWRD 2012), then writes the per-block weather
  or rating schedules.
  - Tower sources: the CEN Infotécnica structure lists, OpenStreetMap,
    or a CSV/KML file.
  - Weather sources: the Explorador Eólico 1 km WRF hindcast,
    interpolated to each span (bilinear in space, log or power law in
    height, direction through u/v), Open-Meteo forecasts, or ECMWF
    open data.
  - `critical-spans` always writes the full ρ(n) curve, with the
    equidistant benchmark.
  - Every choice is an option, and downloads are cached and polite. See
    `scripts/dlr2gtopt/README.md` for the data licences.
- **`gtopt_check_cases` flags unserved energy.** After `--solve`, the
  demand failure (`Demand/fail_sol`) is summed per case (MWh, blocks
  with shedding, peak MW) and reported as a warning; `--fail-on-unserved`
  or a catalog entry with `"serves_all_load": true` makes it an error.

- **`dlr2gtopt`: typical meteorological years for long-term DLR.**
  Long-term studies have no weather forecast, and gtopt computes each
  critical span's rating itself, so the new subcommands build typical
  **weather** for `Line.dlr`.
  - `history`: a multi-year per-span table. Wind comes from the Explorador
    Eólico 1980-2017 reconstruction (1 km); temperature and GHI from ERA5
    via the Open-Meteo archive, calibrated per span to the WRF 1 km
    month × hour climatology.
  - `typical-year`: an 8760 h typical year of all critical spans, with the
    mean case (TMY month selection by the Finkelstein-Schafer statistic on
    the line rating) and the P90 case (the rating exceeded 90 % of the
    time = the 10th percentile, by analogue hours per month × hour, or a
    real low-rating month). Statistics are taken after the min over spans;
    every hour keeps all spans from one historical hour. It writes the
    month × hour rating statistics, a verification report with
    out-of-sample over/under-estimation, and plots.
  - `to-blocks` maps the typical year to PLP load blocks through
    plp2gtopt's hour→block map (or month × hour bands, or chronological
    blocks), and `repeat` tiles a month template over a 20-year horizon.
    Both write the `Line.dlr` weather schedules (or the `rating` /
    per-line `tmax` profiles).
  - Provenance (`--source-columns on`, the default): typical files
    record the blend partner of month seams (`source_time_2`) and the
    hours averaged by `knn-mean` (`knn_sources`). The blocks table
    records each block's UTC `source_time` and `source_year`, also in
    `--history` mode.
  - Calendar: `--timezone auto` (from the span coordinates) and
    `--dst off` (fixed standard time, UTC−4 for Chile, as PLP `indhor`)
    replace the UTC default. `--utc-offset` stays as an explicit
    override.
- **Line thermal limit with losses.** The new option
  `line_thermal_limit_mode` sets how a line's limit (`tmax_ab` /
  `tmax_ba`, and the installed capacity of expandable lines) treats its
  losses. It can be set per line or in `model_options`; a per-line value
  wins.
  - `flow` (default): only the flow is limited. Results do not change.
  - `full`: adds `flow + loss ≤ tmax` in each direction.
  - `sending`: limits the power leaving the sending bus, which is the
    flow plus the part of the loss charged there (see
    `loss_allocation_mode`).
  - With `sending` or `full`, the normal-rating band (`tmax_normal_ab` /
    `tmax_normal_ba` with `overload_penalty`) also counts the loss: the
    penalty is paid on `flow + loss` (or the sending-end share) above the
    normal rating, for every loss model. Under `flow` the band is
    unchanged.
- **Line limit shadow prices in the output.** `Line/thermalp_dual` and
  `Line/thermaln_dual` hold the duals of the `sending` / `full` limit
  rows, and `Line/capacityp_dual` / `Line/capacityn_dual` the duals of
  the installed-capacity rows of expandable lines, in $/MWh. Under
  `sending` / `full` the congestion rent of a full line moves from
  `flow_cost` to `thermalp_dual` / `thermaln_dual`.
- gtopt now checks that each solver plugin it loads is the one built
  with it and that it runs with the solver library it was compiled
  against.  It compares the plugin's build-id with the
  `<plugin>.so.build-id` manifest written next to it, the plugin's
  gtopt build with its own, and the solver version the plugin was
  compiled against with the version of the library loaded at run time
  (for example an old `libhighs.so.1` found through `LD_LIBRARY_PATH`).
  The new option `--plugin-check warning|error` (environment variable
  `GTOPT_PLUGIN_CHECK`) decides what a failed check does: `warning`
  logs it and loads the plugin; `error` refuses it.  See
  `docs/solvers.md`, "Plugin integrity".
- Commitment option `transition_integrality` (`auto` / `continuous` /
  `binary`), in `model_options` and per `Commitment` (the per-unit value
  wins): integrality of startup / shutdown.  `auto` (default) = binary.
  See `docs/unit-commitment.md` §4.1.
- Commitment option `startup_tier_integrality` (`continuous` /
  `integer`, `model_options` or per `Commitment`): integrality of the
  hot / warm / cold start selectors.  Default `continuous` (the tier
  rows are totally unimodular once startup is integral).
- `gtopt --solvers` shows, for each plugin, its path, build-id and
  build tag, and for each solver the compiled and run time library
  versions and the path of the solver library actually loaded.
- **`run_gtopt` dashboard and plain mode.**
  - `--ui {auto,tui,plain}` (`--no-tui` / `--plain` = `plain`): the
    live dashboard on a terminal, line-oriented output for CI, pipes
    and editors.  `--no-color` (or `NO_COLOR`) now works, and `--ascii`
    (or `GTOPT_TUI_ASCII=1`, automatic on non-UTF-8 locales) draws the
    dashboard with ASCII characters only.
  - SDDP progress shows the gap against `convergence_tol`, the phase
    being solved and an ETA; objective values use SI suffixes; the log
    panel colours lines by level and folds repeats; a compact layout is
    used below 100 columns or 40 rows; `o` shows where the run's files
    are.
  - When gtopt ends, the last frame stays on screen with a summary and
    a banner that reflects the real outcome (converged, not converged,
    stopped, failed or killed), the output, log and status paths, and,
    after an infeasibility, the error LP files with the
    `gtopt_check_lp` command to analyse them.
  - Plain mode prints one line per SDDP iteration and a heartbeat every
    30 s.
  - `--fail-on-not-converged` exits with 4 when gtopt succeeded but SDDP
    did not converge.
  - `--attach` without an argument attaches to the only live run.
  - `--list-checks` prints each check with its stage and default.
  - `--solver NAME` is forwarded to gtopt; the conversion output flag
    is now `--convert-dir` (`-o` / `--output-dir` still work).
  - What gtopt prints on its own stdout / stderr is saved under
    `<output>/logs/` and shown when a run fails.
  - Ctrl-C asks for a graceful stop first, then terminates, then kills.

  See `docs/scripts-guide.md`, "run_gtopt".
- **gtopt refuses a HiGHS other than the pinned release at run time.**
  The HiGHS plugin carries `GTOPT_HIGHS_PIN_VERSION` (1.16.0, the version
  the goldens are generated with); when the `libhighs` loaded at run time
  is another release (e.g. the distro 1.12), the plugin is refused with
  the found and expected versions, the library path and how to fix it,
  and `--solver highs` fails instead of using another solver.  It only
  warns when the plugin was built with `-DGTOPT_HIGHS_ALLOW_UNPINNED=ON`
  (as `install.sh` does), with `GTOPT_HIGHS_ALLOW_UNPINNED=1`, or under
  `--plugin-check=warning`.  `gtopt --solvers` shows the pin.  An
  unavailable solver requested by name now also lists why its plugin was
  refused.  See `docs/solvers.md`, "Solver version pin (HiGHS)".
- **gtopt refuses a foreign solver library even when it reports the right
  version.** Each plugin records, at build time, the GNU build-id (else
  the SHA-256) of every shared library it links; at load gtopt compares
  it with the solver library actually mapped in the process.  A fake
  `libhighs.so.1` found through `LD_LIBRARY_PATH`, an `LD_PRELOAD` that
  interposes the solver's functions, or a library replaced in place is
  refused with the loaded and expected path and build-id (overrides:
  `GTOPT_<SOLVER>_ALLOW_UNPINNED=1` or `--plugin-check=warning`; a
  plugin built with `-DGTOPT_HIGHS_ALLOW_UNPINNED=ON` still refuses a
  foreign library).  Applies to every plugin with a shared solver
  library; statically linked solvers (CPLEX) report `static`.  See
  `docs/solvers.md`, "Solver library identity".
- **New CMake option `GTOPT_SOLVER_LIB_FULL_HASH` (default `OFF`).** When
  `ON`, each plugin also records the SHA-256 of every linked solver
  library and gtopt hashes the loaded library file at start: a modified
  copy that kept the original build-id is refused (`SHA256 MISMATCH`).
  `gtopt --solvers` shows whether it is active and `sha256 verified`.
- **Fixed: the LP fingerprint SHA-256 was not standard SHA-256** (the
  message schedule's sigma1 shifted by 2 instead of 10).  `sha256_hex()`
  is now FIPS 180-4 (checked against the NIST vectors) and shared with
  the solver-library identity check; LP fingerprint hashes (`col_hash`,
  `row_hash`, `structural_hash`) change value and now agree with
  `gtopt_check_fingerprint` (Python `hashlib`).  No fingerprint goldens
  are stored in the repository.
- **`GTOPT_SOLVER` naming a refused plugin is now an error.** When the
  solver named by `GTOPT_SOLVER` belongs to a plugin refused by an
  integrity check (build-id, build tag, version, shadowing, HiGHS pin),
  gtopt fails like an explicit `--solver` (same error, exit code 3)
  instead of silently falling back to another solver.  A solver with no
  plugin still warns and falls back.  `GTOPT_SOLVER_FALLBACK_ON_REFUSED=1`
  restores the fallback for refused plugins.

- **New solver options `barrier_solver`, `mip_lp_algorithm` and
  `mip_heuristic_effort`**, settable in `solver_options`,
  `sddp_options.forward_solver_options` / `backward_solver_options`,
  `monolithic_options.solver_options` and with `--set`.
  `barrier_solver` (`auto`, `ipx`, `hipo`; HiGHS) picks the
  interior-point code of `algorithm: barrier` and of a HiGHS MIP's IPM
  fallback; `auto` is the previous behaviour, and `ipx` restores the
  serial IPX on a HiGHS built with HiPO. `mip_lp_algorithm`
  (`default`, `primal`, `dual`, `barrier`) sets the MIP root LP
  algorithm (HiGHS `mip_lp_solver`, CPLEX `StartAlgorithm`);
  `mip_heuristic_effort` (0-1, HiGHS) the share of MIP effort spent in
  heuristics. All three are unset by default, which keeps each
  solver's own default.
- **Cascade levels take their own solver options**:
  `cascade_options.level_array.N.sddp_options.forward_solver_options`
  and `backward_solver_options` override the optional solver fields
  (tolerances, `mip_gap`, `time_limit`, `barrier_solver`, …) for that
  level.

### Removed

- **Commitment rows `startup ≤ status` / `shutdown ≤ 1 − status` and the
  option `transition_status_rows`** (added 2026-11-25, unit and line
  commitments).  Why: with binary startup / shutdown
  (`transition_integrality = auto`) they are redundant in a MIP; at
  scale they made the LP degenerate and near-singular (the exclusion row
  is their exact sum, and they were emitted for fixed-status periods
  too: 155 014 of 219 564 rows on the CEN PCP case), CPLEX's crossover
  ended at kappa 1e16 and more, and CPLEX declared feasible PCP LPs
  infeasible.  Without them the same LP solves with kappa ≈ 2e7.
  `transition_integrality = auto` is binary again (it was continuous
  only while the rows were on), which is exact and is UnitCommitment.jl's
  Gar1962 model; LP relaxations and MIP sizes of commitment cases move
  back to the pre-2026-11-25 values (Hua-Baldick Ex. 2 relaxed price
  820/13 ≈ 63.08 again).  Input files that still set
  `transition_status_rows` (bool or name, in `model_options` or a
  `Commitment`) load; the key is ignored with one warning.

### Changed

- **`sddp_options.release_backends` now defaults to `true`.** Under
  `low_memory_mode: off`, SDDP / cascade release each cell's solver
  backend after its solve and rebuild it from the uncompressed snapshot
  plus the saved basis (as `compress` with `memory_codec:
  uncompressed`). On CEN 2y with CPLEX the peak drops from 22.3 to
  3.7 GiB; wall time is unchanged within noise (dual simplex -3.6 %,
  barrier +4 %). The bounds match `compress` exactly and move ~0.025 %
  against live backends (degenerate ties). `--set
  sddp_options.release_backends=false` restores the previous behaviour
  (every LP live). Monolithic runs are unaffected.
- `run_gtopt` no longer forces `snappy` output compression: the case's
  `output_compression` (or gtopt's default, `zstd`) is used unless `-C`
  is given.  `-l` for `--log-level` is deprecated (in gtopt `-l` means
  `--lp-file`).
- `gtopt --help`: `--sddp-num-apertures` now describes the actual
  behaviour (unset = every aperture of the phase, `0` = none,
  `N` = the first `N`).
- **One rule for the output directory.** A directory given on the
  command line is relative to the current directory, whether it comes
  from `-d` or `--set output_directory=` (before, `--set` was anchored at
  the case JSON's directory, so `--set output_directory=.` wrote into the
  case directory while `-d .` used the CWD).  `--set input_directory=`
  follows the same rule.  A JSON `options.output_directory` stays
  relative to the JSON file, and the case-directory form `gtopt case/` now
  honours it instead of forcing `case/results` (`results` remains its
  default when the JSON sets none).  `run_gtopt` and `gtopt_check_cases`
  follow the same rule; `gtopt_check_cases` passes
  `--set output_directory=` instead of the deprecated `-d`.

- **New option `loss_flow_abs_cost` (default 0.001 $/MWh).** In
  `tangent_signed_flow` the abs-flow proxy columns (Σv ≥ |f|) carry a
  small cost that makes Σv = |f| at the optimum and keeps the secant
  chord on the losses anchored.  It was an internal constant of
  1e-6 $/MWh, which became 1e-9 in the LP after `scale_objective =
  1000`: below the solvers' tolerances, so it could fail to pin, and it
  stretched the objective range (9 decades on `line_loss_envelope`).
  It is now a user option in real $/MWh, in `model_options` and per
  line, default 0.001 (1e-6 in the LP).  It applies only when
  `loss_cost_eps > 0`, as before.  Effect on results: the price
  difference across a lossy line moves by at most about 0.001 $/MWh
  in the flow direction, and the objective rises by about
  0.001 · Σ|f| on those lines.  `loss_flow_abs_cost: 1e-6` reproduces
  the old LP exactly; `0` disables the pin.
- **Solver plugins that fail the integrity check are now refused by
  default.** The default of `--plugin-check` is `error` (it was
  `warning`): a plugin from another build tree, a stale install, a
  plugin without its `.so.build-id` manifest, or a solver library
  shadowed through `LD_LIBRARY_PATH` is not loaded.  The default is
  set at build time with the new CMake option
  `GTOPT_PLUGIN_CHECK_DEFAULT` (`error` or `warning`, checked at
  configure time); `$GTOPT_PLUGIN_CHECK` overrides it and
  `--plugin-check` overrides both.  `gtopt --help`, `gtopt --version`
  and `gtopt --solvers` show the effective mode and where it comes
  from.  Plugins built and installed with the same gtopt load as
  before.  To load a mismatched plugin anyway, pass
  `--plugin-check=warning` or set `GTOPT_PLUGIN_CHECK=warning`.  The CI
  artifact now ships each plugin's `.so.build-id` manifest.
- **Lower SDDP memory.** On the CEN 2y case (CPLEX, 18 scenes x 52
  phases, 3 iterations) peak RSS drops from 26.2 to 23.2 GiB under
  `low_memory_mode: off` and from 4.5 to 2.5 GiB under `compress`, with
  identical bounds. Each low-memory backend is released right after
  alpha registration; the copy-out scratch of the CPLEX, Gurobi and
  MindOpt plugins is freed after each forward solve; compressed buffers
  are exact-size; with LP names off no label metadata is kept at all
  (with names on it is shared with the snapshot instead of copied);
  and SDDP / cascade under `off` drop the disposable element
  collections, rebuilding them for `write_out`. LP files and names are
  unchanged when names or LP-file output are on. Every reduction has an
  option that restores the previous behaviour:
  `lp_matrix_options.label_meta_without_names` (default `false`),
  `lp_matrix_options.share_label_meta` (default `true`),
  `lp_matrix_options.drop_element_collections` (default `true`),
  `sddp_options.release_after_alpha` (default `true`) and
  `sddp_options.free_solver_scratch` (default `true`).
- New option `sddp_options.release_backends` (default `false`, now
  `true`, see above): under
  `low_memory_mode: off`, release each solver backend after its solve and
  rebuild it from an uncompressed snapshot, warm-starting from the saved
  basis, as `compress` does. `false` keeps every LP live.
- `plp2gtopt` no longer writes `low_memory_mode: off`, so gtopt's
  `compress` default applies unless the PLP source sets a mode.

- **Tighter cap on line losses.** A line's loss can no longer exceed
  what the loss model gives at the line's maximum flow. Before, the cap
  could be up to `(loss_envelope / tmax)²` times larger when
  `loss_envelope` was above `tmax`. The extra room only served fictitious
  losses under negative prices. Results change only for lines with
  `loss_envelope` above `tmax`.

- The run log's `solver=highs/<version>` and `solver=clp/<version>` now
  give the version of the solver library loaded at run time, not the
  version of the headers the plugin was compiled with.
- HiGHS: without an explicit `threads`, a MIP now uses every physical
  core (HiGHS 1.16 runs one tree-search worker per thread); an LP keeps
  4 threads.  Before, every solve was capped at 4.
- HiGHS 1.16.0 is the reference version, for its parallel MIP solver
  (on by default) and its MIP and presolve fixes: CI builds it, and
  `install.sh` builds it from source when the distribution package is
  older (Ubuntu 26.04 and Debian 13 ship 1.12.0).  The optional
  `highspy` dependency of `gtopt_check_lp` requires 1.16 or later.
- **HiGHS is built with HiPO, its parallel interior-point solver.**
  `install.sh` and CI build HiGHS 1.16 with `-DHIPO=ON
  -DBUILD_OPENBLAS=ON`, so `algorithm: barrier` runs HiPO (multithreaded
  ordering, normal equations and factorisation) instead of the serial
  IPX. A HiGHS without HiPO still works and runs IPX. See `BUILDING.md`
  and the HiGHS section of `docs/solvers.md`.

### Fixed

- **Kappa: a solver "infinity" is no kappa.** CPLEX answers
  `CPX_KAPPA` with status 0 and its huge-value sentinel 1e75 when it
  cannot bound the condition of the final basis; `solution.csv`,
  `max_kappa` and the high-kappa warning reported it as kappa = 1e+75.
  That sentinel (>= 1e75), +inf and NaN now read as unavailable (-1),
  with one CPLEX warning per process; huge finite estimates (CPLEX
  reports 2.3e16 on that LP's basis) are kept and still trip the
  high-kappa warning.
- **Cascade `solution.csv` summary.** A cascade never set the run
  summary, so `max_kappa` was -1 next to valid per-cell kappas and
  `gap` / `gap_change` were defaults; the last level's values are now
  written.
- `auto_scale_theta` logs and keeps the default `scale_theta` when every
  line is a DC link or a tie (no median). The model sanity check no longer
  reports X/R on ties or on lines below the resistance floor.
  `gtopt_shared.sanity_report` mirrors the relative tie floor.
- **HiGHS SDDP runs are several times faster.** The HiGHS plugin asked
  HiGHS for the exact basis condition number after every solve, one
  FTRAN per basis row: on the CEN 2y case that was about 75 % of the
  HiGHS CPU time, and a backward pass took 6-8x longer than with CPLEX.
  It now reads the Hager estimate (as CPLEX's `CPX_KAPPA`). New option
  `kappa_mode` for every solver: `estimate` (default), `exact` (the old
  HiGHS computation, for diagnostics; CPLEX `CPX_EXACT_KAPPA`, Gurobi
  `KappaExact`) or `off` (kappa is neither queried nor printed: no
  solver call, no `kappa` / `max_kappa` columns in `solution.csv`, no
  kappa in logs or high-kappa warnings). Kappa is now read once per
  solve instead of up to three times. When `kappa_mode` is unset,
  `$GTOPT_KAPPA_MODE` applies, else the compiled default: new CMake
  option `GTOPT_KAPPA_MODE_DEFAULT` (`auto`: `off` in release builds,
  `estimate` in Debug and CIFast), printed by `gtopt --version`. The
  integration tests set `GTOPT_KAPPA_MODE=exact`; `run_gtopt
  --kappa-mode MODE` passes the mode to gtopt. `solution.csv` readers
  (`gtopt_check_cases`, `gtopt_shared`) accept files without the kappa
  columns. With the
  presolve fix below, a HiGHS SDDP backward pass on a large hydrothermal
  case is about twice as fast as before, with the same bounds.
- A HiGHS warm-start request (`advanced_basis`) with no basis to start
  from, such as the first forward solve of each SDDP cell, now solves
  cold with presolve. It used to turn presolve off, which made the cold
  dual simplex 2-3x slower. `cold_solve_without_basis: false` restores
  the old behaviour.
- HiGHS: `crossover` is applied on every solve. Once a solve had used
  `crossover: none`, every later barrier solve of the same LP (an SDDP
  backward re-solve, for example) also ran without crossover, and
  returned interior duals and no basis where a vertex was requested.
  To keep crossover off on every solve, set `crossover: none` in every
  pass's solver options.

- `gtopt .` and `gtopt ..` now find the case JSON named after the
  directory (e.g. `gtopt .` inside `ieee_9b/` uses `ieee_9b.json`);
  they used to look for `..json` and fail.
- **The LP fingerprint check is real.** `--set lp_fingerprint=true`
  wrote `lp_fingerprint_*.json` files with an empty hash and template
  (the option never reached the LP builder), and the C++ SHA-256 had a
  wrong message-schedule shift, so its digests matched no standard
  SHA-256.  Both are fixed.  `gtopt_check_fingerprint` now reads every
  column of an LP file (not only the bounds section), compares on
  class/variable/context arity, recomputes the stored hash and exits
  with 2 on an empty, tampered or unlabelled input instead of passing.

- **Async SDDP reported a lower bound of 0.** With `max_async_spread > 0`
  the per-scene and aggregate LB were read after the backward pass, from
  the phase-0 LP that had just received a cut and (on the aperture path)
  was not re-solved, so they came out as 0.  The LB is now captured right
  after the forward pass, as the synchronous path does.

- **HiGHS kept its options across `load_problem`.** Loading a problem
  calls `Highs::clear()`, which resets every option, so the algorithm,
  crossover, thread count and log file set before the load were lost
  (the solve ran with HiGHS defaults).  They are now re-applied after
  every load.

- Expandable lines using `piecewise` losses with the `tangent` layout
  now respect their installed capacity. Before, their flow was limited
  only by `tmax`.

- An LP already proven infeasible is no longer reported as an abandoned
  solve because a later algorithm in the fallback cycle gave up on it.
  With HiGHS 1.15, whose interior point solver fails without presolve
  on some infeasible LPs, SDDP counted infeasible apertures as solver
  failures.
- HiGHS solver errors no longer abort the solve with an exception: they
  are reported as an abandoned solve, so the algorithm fallbacks still
  run.
- SDDP apertures whose solve is abandoned or ends non-optimal (not
  proven infeasible, not timed out) are retried once from scratch
  before being dropped.  The per-phase aperture log reports how many
  were retried and recovered.
- A turbine whose generator has zero capacity no longer turns its
  waterway into a free spillway: dropping the generator also dropped the
  conversion row that kept the turbine flow at zero, so water could pass
  through an offline turbine at no cost.  The flow is now fixed at zero
  (turbines with `drain` may still pass water).
- Destroying the LP-name spill store could deadlock: the stop request
  was not made under the worker's mutex, so the worker could miss the
  wake-up and wait forever.
- Commitment startup tiers now use the same offline-time thresholds
  for a unit that shut down inside the horizon as for one that was
  already off before it: hot when offline `< hot_start_time`, warm
  when `hot_start_time ≤` offline `< cold_start_time`, cold otherwise.
  Before, a start exactly `hot_start_time` (`cold_start_time`) hours
  after an in-horizon shutdown was priced hot (warm), one tier cheaper
  than documented.  Results can change for units restarting exactly at
  a tier boundary.
- A committed unit that starts the horizon online can now shut down in
  the first block.  The first-block ramp-down row used the initial
  status where it needed the block's own status and shutdown, so a unit
  with `initial_power` above `ramp_down` was kept on for block 1.  On
  the RTS-GMLC UC.jl benchmark this cost +3.5 % (five units forced on);
  gtopt now matches UnitCommitment.jl's optimum.
- Commitment `initial_status` is rounded at 0.5 (as LineCommitment
  already did), so the first-period startup / shutdown stay 0/1.
- `pricing_mode = fast_start` now relaxes integer start-up tier
  selectors with the status: pinned at the incumbent they kept the
  fast-start unit on in every start period.
- Commitment ramp rows with `commitment_period` apply the startup /
  shutdown ramp only at the first block of the period where the unit
  starts / stops; interior blocks ramp with `ramp_up` / `ramp_down`
  only.  Before, every block of the start period could ramp by
  `startup_ramp`.
- The first-block ramp-up row uses the startup column
  (`p − startup_ramp·startup ≤ initial_power + ramp_up·initial_status`)
  instead of a constant: same integer solutions, tighter LP relaxation.
- LineCommitment startup tiers now use the same thresholds as
  Commitment (hot when open `< hot_start_time`, cold when open
  `≥ cold_start_time`), and a line open since before the horizon
  counts `initial_hours` at every block, not only the first: a later
  first close was always priced cold.  Line startup / shutdown columns
  are binary (with the line status' domain): continuous ones admitted
  startup = shutdown = ½ on a line that stayed closed.

## [1.2.0] - 2026-11-24

GTOpt 1.2.0 adds natural-gas networks, a much richer reserve and
unit-commitment model, and an SDDP solver that is faster and stricter
about when it reports convergence.  A systematic review of how the
optimisation model is built also found and fixed many cases where an
element was cheaper, freer or more constrained than intended.  Several
defaults changed, so results of existing cases can move: read *Changes
that affect your results* before comparing with 1.1.0.

### Highlights

- **Gas and power in one model.** Gas nodes, pipelines with line pack,
  compressors, supplies, demands and gas-fired turbines can be optimised
  together with the electrical system, with nodal gas prices next to the
  electricity prices.
- **Realistic reserve requirements.** Reserve needs can follow load,
  system inertia, the largest contingency and line flows, block by block,
  and batteries can only offer reserve they can actually deliver.
- **Better prices from unit-commitment runs.** Choose how prices are
  derived from a commitment schedule (fixed, relaxed or fast-start
  pricing) and compute the matching uplift payments.
- **SDDP you can trust.** Faster backward passes, and the solver no
  longer reports convergence on a large gap, on bounds inherited from a
  previous cascade level, or while some hydrology samples are
  infeasible.
- **Losses without phantom flows.** Loss modelling now resists the
  artificial loss "burning" that negative nodal prices can induce, and
  very low-impedance branches are modelled as ideal ties.
- **Value of lost load as a curve.** Load shedding can be priced in
  steps, as in operator and regulatory practice.
- **More systems to read.** PLEXOS 8.1 to 10.0 models, the AEMO ISP as a
  monthly expansion model, the clustered unit-commitment benchmarks, and
  reserve test systems from Sienna and RTS-GMLC.

### Changes that affect your results

#### Costs and penalties

- **Unserved energy is priced at 1000 $/MWh by default.** A case that
  did not set `demand_fail_cost` used to shed load for free; it now
  serves it.  Set `demand_fail_cost: 0` for the old behaviour.
- **Penalties are weighted like every other cost.** Soft user-constraint
  penalties, water-right deficit costs and junction drain costs are now
  multiplied by scenario probability, discount factor and block duration.
  Multi-scenario, discounted or multi-hour-block cases see different
  trade-offs; single-scenario hourly cases do not change.
- **Met reserve requirements no longer lower the objective.** The
  reported total cost is the real operating cost; dispatch and prices do
  not change.
- **Start-up cost in the first hour** follows how long the unit has been
  off (hot, warm or cold start) instead of always being a cold start.

#### Network and losses

- **Losses carry a small cost** (`loss_cost_eps`, now 0.1 $/MWh).  This
  removes simultaneous flow in both directions and the loss "burning"
  that small negative prices can cause; total losses are practically
  unchanged.  Set it to 0 for the old formulation.
- **Flow beyond the loss envelope is priced** (`loss_overflow_cost`,
  default: the case's unserved-energy cost), so overloaded lines cannot
  absorb energy as fictitious losses.
- **Zero- or near-zero-reactance branches are ideal ties** (equal
  voltage angles at both ends) instead of uncontrolled DC links that
  could act as free phase shifters.  `tiny_reactance_mode: dc` restores
  the old treatment.
- **Angle scaling is back to its documented meaning** (`scale_theta`
  multiplies the angle; 1000 = milliradians).  Cases that carry a very
  small value such as `1e-4` get a warning and should invert it or drop
  it.  Angle limits are now derived from the network whenever they are
  not set, so they can no longer cap line flows by accident.
- `piecewise_direct` losses charge the full transmission toll per MWh.

#### Unit commitment and storage

- **Commitment on non-chronological stages.** Units with a commitment
  now get an on/off status and minimum stable level in every block of
  load-duration or representative-block stages (start-up, ramps and
  minimum up/down need chronology and are skipped there).  The former
  simple commitment element is merged into the regular one.
- **Reserve and inertia from committed units** respect the unit's
  minimum stable level and its on/off status, so an off unit cannot
  offer reserve and down reserve is no longer free at minimum load.
- **Battery minimum discharge** is a hard floor again for batteries
  without commitment.
- **Batteries with commitment cannot charge and discharge in the same
  block** (`exclusive_charge_discharge`, default on), so they cannot burn
  surplus energy through round-trip losses under negative prices.  Set it
  to `false` to allow simultaneous operation.
- **Hydrogen and thermal storage carry their level between stages** by
  default instead of closing a cycle in every stage (#546).
- **Fuel limits count the real fuel burn**, including piecewise and
  hourly heat rates and no-load fuel (#545).
- **Existing capacity of an expandable unit is no longer bought again**
  in its first expansion stage, which lowers the objective of such
  expansion cases.

#### Hydro and water rights

- A reservoir without an explicit flow-to-volume conversion now uses
  0.0036 hm³ per m³/s·h, as documented (the JSON default was 1000 times
  larger).  Set `flow_conversion_rate` if your case relied on it.
- Global spill and use values apply only to the water rights they are
  meant for; usage-cost rights are no longer capped at zero flow; rule
  curves read the stage-start volume; the pass-through arc of a flow
  right exists only when requested (`use_bypass`) and appears in the
  output.

#### SDDP and cascade

- **Stricter convergence.** A "stationary" stop now needs the run's own
  iterations and a gap below 2 % (was 5 %), and an iteration in which
  some hydrology samples were infeasible is never declared converged
  (#551).  Short runs may run a few more iterations; the end-of-run
  summary reports how many samples were dropped and where.
- **Hydrology samples are solved scene by scene** by default, reusing
  each solve's basis; bounds are the same, the backward pass is much
  faster (`aperture_chunk_mode: per_aperture` restores the old grouping).
- **The future-cost floor comes from the cuts.** Models with only
  non-negative costs that relied on an implicit floor of zero should set
  `alpha_min: 0`.
- **Cuts built with Ruiz scaling had wrong slopes**, so bounds could
  cross.  Cut files saved by such runs should be regenerated.
- Setting the number of hydrology samples to 0 now means none (a
  deterministic backward pass), and the per-sample time limit is 30 s.

#### Outputs

- Dual (price) tables list every block, zeros included.
- Element and block identifiers in Parquet outputs are 32-bit integers;
  large identifiers were previously wrapped.
- Renewable spillover is computed from availability minus dispatch;
  availability is capped at the unit's maximum output.

#### Configuration

- A `.gtopt.conf` in the working directory is no longer read; pass it
  with `--config`.  Options in the config file now only fill values the
  case and the command line leave unset.
- `annual_loss` is renamed `annual_standing_loss` (self-discharge while
  stored); the old name still works with a warning.

#### Converted cases

- **PLEXOS:** units without a real commitment decision are LP-relaxed
  (`--uc-relax none` for all-integer); reserves are modelled with the
  native reserve requirements (`--reserve-model uc` for the old
  emulation); constraints without a penalty price are hard, as in
  PLEXOS; lines PLEXOS does not enforce get non-binding limits; lossless
  transformers get a correct per-unit susceptance (flows and prices
  change); fuel transport charges scale with heat rate; battery
  self-discharge comes from the PLEXOS data.
- **PLP:** a battery charged by a co-located plant no longer curtails
  that plant (the plant stays on the grid and the battery charge is
  limited to its output); lines PLP treats as lossless stay lossless;
  solar and wind profiles are written per unit (re-convert affected
  cases); zero-cost soft minimum volumes are no longer emitted.
- **PSR SDDP:** branch impedances are read as % on 100 MVA (they were
  read as ohms, turning real circuits into ideal ties), and imports are
  unavailable in hours without an offer.

### New modelling capabilities

#### Network and losses

- Branches with zero or tiny impedance can be modelled as ideal ties or
  as DC links (`tiny_reactance_mode`).
- A priced overflow tier for losses beyond a line's loss envelope
  (`loss_overflow_cost`, per case or per line).

#### Unit commitment and reserves

- **Pricing modes** for commitment runs (`pricing_mode`: `fixed`,
  `relaxed`, `fast_start`), with automatic fast-start classification and
  a `gtopt_pricing_uplift` tool for make-whole and lost-opportunity
  payments.  Validated against the classic pricing literature examples.
- **Dynamic reserve requirements**: the requirement in each block is the
  largest of several planes combining load, inertia, the largest
  contingency and line flows; load enters from forecast data so reserves
  do not distort energy prices.  Equality requirements, surplus prices
  and per-plane prices are available.
- **Contingency candidates** (generators, loads or lines whose loss sets
  the requirement), **inertia credits** when enough units of a group are
  on, **cold (offline) reserve**, symmetric up/down offers, shared
  headroom and per-zone effectiveness factors.
- A check that lists every way a reserve requirement depends on dispatch
  and could create negative price credits (`reserve_coupling`).
- **Feasible commitment starting points** for large MIPs, and every MIP
  that stops at a time limit is re-solved with its commitment fixed, so
  costs, dispatch and prices are consistent.

#### Storage and batteries

- **Battery reserve deliverability**: reserve offers are backed by power
  headroom and enough state of charge to sustain them, and deployment
  energy is charged to the battery itself.
- **Battery power expansion**, with an optional fixed energy-to-power
  ratio for new storage.
- Storage can be **retired** during the horizon; a unit commissioned
  later starts from its stated initial level.

#### Demand

- **Stepwise value of lost load** per demand (`fail_segments`) or for the
  whole case (`demand_fail_segments`), with one shedding output per step.

#### Gas and emissions

- **Gas networks**: nodes with pressure limits and gas prices, pipelines
  (Weymouth flow with optional line pack and elevation), compressors
  (gas- or electric-driven, with fuel use), supplies with volume
  allotments, demands, and gas turbines linked to their generators with a
  minimum supply pressure.  Meshed networks are solved by successive
  linearisation.
- **No-load fuel** for committed units, fuel-based emission rates, and
  emission zones limited to a set of buses (#545).

#### Decision variables and user constraints

- **Here-and-now decisions** that take one value across all hydrology
  scenarios (`nonanticipative`), for example an LNG cargo plan fixed
  before inflows are known.
- Case-wide decisions that span all planning phases, integer decision
  variables, user constraints active only in some stages, and
  stage-average constraints in PAMPL files.

#### Converters

- **PLEXOS**: versions 8.1, 9.0 and 10.0; native reserve and battery
  reserve mapping; inflexible LNG tanks; horizon truncation with end
  conditions; emission tracking of imports; initial generation for ramp
  limits; AEMO ISP network augmentations by development path and a new
  monthly multi-year expansion model of the AEMO ISP (prototype).
- **PLP**: all failure (falla) tiers as a value-of-lost-load curve
  (`--falla-segments all`); loss tangent count selection
  (`--loss-tangents`, default `double` = one tangent per PLP loss
  segment in each flow direction).
- **PSR SDDP**: the full deficit cost curve (`--deficit-segments all`).
- **New converters and inputs**: `cucdata2gtopt` (the clustered
  unit-commitment benchmarks on the IEEE 39- and 118-bus systems),
  `gasnet2gtopt` (gas networks and published benchmarks), Sienna reserve
  test systems, and RTS-GMLC reserves and inertia.

#### Tools

- **Data-quality checks** after every conversion and before every solve:
  implausible line impedances and voltage levels, profiles outside 0-1,
  zero-capacity units with commitment, rewards above failure costs.
- **Topology check per stage**: electrical islands, zones and
  interconnectors as the network changes over time.
- **Network reduction** to zones (for example one bus per country) while
  keeping chosen interconnectors.
- `gtopt-fetch` downloads and verifies public operator data.
- SDDP and cascade runs can write full results for every cascade level.

### Improvements

- The SDDP log explains why each run stopped and how much solver effort
  each pass took, and reports the upper bound net of irrigation
  agreement rewards and the effect of end-of-horizon cuts on the gap.
- Unknown option or mode names are errors instead of silently falling
  back to a default formulation; invalid couplings between elements
  (for example a turbine active while its generator is not) are reported
  before the solve.
- `gtopt --version` and every result file record the exact program
  version that produced them.
- Settings priority is clear: command line, then `--set`, then the case
  file, then `.gtopt.conf`, then defaults.
- Solver parameter files are found next to the case, follow the solver
  actually used, and are recorded for reproducible re-runs; a solve time
  limit is one budget per solve.
- The installer can select and auto-detect solvers.

### Fixes

#### Network and losses

- Kirchhoff's voltage law is kept on all surviving loops when a line is
  out of service in some blocks.
- A line's loss envelope no longer caps its flow.

#### Unit commitment and reserves

- Reserve rows of units referenced by name and by number now match;
  units with both a minimum and a maximum number of starts build
  correctly; relaxed commitments report their actual status.
- Inertia contributions use the committed minimum output and are capped
  at the unit rating.

#### Storage and batteries

- Storage commissioned after the first stage no longer starts with free
  stored energy; out-of-service storage and converters no longer produce
  energy, heat or hydrogen; expandable converters pay for the capacity
  they use.
- Batteries with commitment have separate charge and discharge decisions
  again, so they can operate with both minimum charge and discharge
  levels.

#### Hydro and irrigation

- The water value of a spilling reservoir is correct with multi-hour
  blocks; water-right ledgers debit and credit in the documented
  direction.
- Irrigation agreements follow the PLP rules more closely (Laja
  withdrawal cap at the stage-start filtration, no reward for undelivered
  water).
- Converted PLP seepage no longer caps the upstream reservoir when the
  downstream outlets are too small.

#### SDDP and cascade

- Phases with several stages keep their storage chain after the first
  phase (storage used to restart from its initial level, giving free
  water); capacity built mid-horizon is carried to later phases.
- Loaded cuts apply to the end-of-phase volume; the first cut of each
  cell no longer gives an invalid lower bound with negative costs.
- With model reduction on, each hydrology sample sees its own inflows.
- The run status file always ends with a final status.

#### Gas and emissions

- Gas line pack cannot be refilled for free between stages; emission
  and fuel penalty costs are reported on the right basis.

#### Converters

- **PLEXOS:** batteries with zero rating are out of service, charge
  power uses the real rating, hourly bound and minimum-volume files are
  aligned to the right dates, and restarts after outages are allowed.
- **PLP:** the first boundary cut of the PLP cut file is kept; reduced
  cascade networks load correctly.
- **PSR SDDP:** negative water values are kept in the boundary cuts.
- **Sienna:** thermal minimum output applies only when committed (units
  were forced on all day).  The converter is now `sienna2gtopt`;
  `sienna_to_gtopt` still works as a deprecated alias.

#### Interfaces

- Result summary and time-series export tools read the current output
  layout; the topology checker handles lines defined in data files; the
  monitor exits when a run ends.

### Performance

- SDDP backward pass much faster by default (hydrology samples solved in
  sequence on one model with basis reuse), forward solves start from the
  latest backward basis, and a redundant re-solve after each cut is
  skipped.
- Lower memory use for large SDDP cases (shared, compressed model
  snapshots across scenarios).
- Solver warm starts keep the case's solver settings (threads,
  tolerances, time limits), and parameter files are read once per run.

### Security

- LP debug compression no longer goes through a shell; the external
  compressors are started directly with an argument list.
- Stages whose block range falls outside the block list, very deeply
  nested constraint expressions and very large index sets are rejected
  with an error instead of being processed.
- `.gtopt.conf` is no longer read from the current working directory
  (search order `--config`, `$GTOPT_CONFIG`, `~/.gtopt.conf`).
- Builds are hardened by default (stack protection, control-flow
  protection, fortified library calls, read-only relocations), and the
  binary and plugins no longer search the current directory or the home
  directory for libraries.
- Cascade cut files are written to a private temporary directory.
- The GUI service accepts only loopback or explicitly allowed host names
  (`GTOPT_GUI_ALLOWED_HOSTS`), requires its own header on write
  requests, and escapes case values it displays.
- Automatic LP diagnostics never send a model to the public NEOS server.
- The binary download helper accepts only verified artifacts of this
  repository.
- Operator API keys are read only from environment variables and are
  never written to logs.
- Python dependency minimums raised to versions without known
  advisories.

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

[Unreleased]: https://github.com/gtopt-community/gtopt/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/gtopt-community/gtopt/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/gtopt-community/gtopt/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/gtopt-community/gtopt/releases/tag/v1.0.0
