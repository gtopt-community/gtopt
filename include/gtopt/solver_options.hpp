// SPDX-License-Identifier: Apache-2.0
/**
 * @file      solver_options.hpp
 * @brief     Linear programming solver configuration options
 * @date      Mon Mar 24 10:24:13 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Licensed under the
 *            Apache License 2.0 (GTOpt Extension SDK), see LICENSING.md
 *
 * This module defines options specific to controlling linear programming
 * solvers, including algorithm selection, solver parameters, and tolerances.
 */

#pragma once

#include <optional>

#include <gtopt/solver_enums.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

/**
 * @brief Configuration options for linear programming solvers
 *
 * The SolverOptions structure contains parameters that control how linear
 * programming problems are solved, including algorithm selection, parallel
 * processing settings, numerical tolerances, and logging preferences.
 *
 * The three tolerance fields (@c optimal_eps, @c feasible_eps,
 * @c barrier_eps) are **optional**: when they are @c std::nullopt the solver
 * keeps its built-in default values instead of overriding them.
 */
struct SolverOptions
{
  /** @brief The solution algorithm to use (default_algo = use backend optimal)
   */
  LPAlgo algorithm {LPAlgo::default_algo};

  /** @brief Number of parallel threads to use (0 = use backend optimal) */
  int threads {0};

  /** @brief Whether to apply presolve optimizations (default: true).
   *
   * Pass `--set solver_options.presolve=false` on the CLI to disable
   * for SDDP cell-replay runs (the cuts being re-installed were
   * already vetted on first insertion, so presolve has no real
   * reductions to find — only fixed-cost setup overhead per call).
   * Default kept at `true` because:
   *   - HiGHS benchmark regresses 2.5x with presolve off
   *     (`docs/analysis/highs-benchmark-results.md:18, 27`).
   *   - Several SDDP unit tests depend on the presolve-induced LP
   *     basis for their analytical-dual / KKT-prediction checks; these
   *     would need fixture-level opt-in if the default flipped.
   * For PLP-converted-case style hot-start runs, set
   * `--set solver_options.presolve=false` explicitly.
   */
  bool presolve {true};

  /** @brief Optimality tolerance for solution (nullopt = use solver default)
   */
  std::optional<double> optimal_eps {};

  /** @brief Feasibility tolerance for constraints (nullopt = use solver
   * default) */
  std::optional<double> feasible_eps {};

  /** @brief Convergence tolerance for barrier algorithm (nullopt = use solver
   * default) */
  std::optional<double> barrier_eps {};

  /** @brief Target relative MIP optimality gap (nullopt = use solver default).
   *
   *  Backends translate this to their native "stop when
   *  `|best_obj − bound| / |best_obj| ≤ gap`" parameter:
   *  - CPLEX:   `CPX_PARAM_EPGAP`
   *  - HiGHS:   `mip_rel_gap`
   *  - Gurobi:  `MIPGap`
   *  - MindOpt: `MIP/RelGap`
   *  - CBC:     `ratioGap` (Cbc_setAllowableFractionGap)
   *
   *  Has no effect on continuous LPs; safely ignored when no integer or
   *  binary variables are present.  Pair with `time_limit` to bound MIP
   *  wall-clock when gap targets are loose. */
  std::optional<double> mip_gap {};

  /** @brief Target ABSOLUTE MIP optimality gap (nullopt = use solver default).
   *
   *  Backends translate this to their native "stop when
   *  `|best_obj − bound| ≤ gap_abs`" parameter:
   *  - CPLEX:   `CPX_PARAM_EPAGAP`
   *  - HiGHS:   `mip_abs_gap`
   *  - Gurobi:  `MIPGapAbs`
   *
   *  Unlike the relative `mip_gap`, the absolute gap is INVARIANT to a
   *  constant objective offset — essential when the objective carries a
   *  large baseline (e.g. an FCF cost-to-go) that would otherwise make the
   *  relative gap meaningless (a tiny relative gap masks a large
   *  decision-level absolute gap, or a near-zero objective inflates it).
   *  Expressed in physical objective units.  Has no effect on continuous
   *  LPs. */
  std::optional<double> mip_gap_abs {};

  /** @brief Verbosity level for solver output (0 = none) */
  int log_level {0};

  /** @brief Controls solver log file generation.
   *
   * - `nolog`:     No log files written (default).
   * - `detailed`:  Separate log file per scene/phase/aperture, named
   *                `<solver>_sc<N>_ph<N>[_ap<N>].log`.
   */
  std::optional<SolverLogMode> log_mode {};

  /** @brief Wall-clock time budget, in seconds, for the whole solve of one
   *  LP / MIP.  0 / unset = no limit (solver default).
   *
   *  The budget covers every attempt gtopt makes for that problem, not
   *  each attempt separately: the algorithm fallback cycle of
   *  `LinearInterface::initial_solve` / `resolve` (see `max_fallbacks`)
   *  hands each retry only the time that is LEFT, and skips the remaining
   *  retries once less than `min_retry_time_slice_s` is left (the last
   *  solver status / incumbent is kept).  For a monolithic solve the
   *  MIP-start pipeline (LP relaxation, repair) also draws from the same
   *  budget: the MIP itself gets what the warm-start stages left over.
   *  The post-MIP polish LP (`mip_polish`) is charged too: the MIP runs
   *  under the budget minus the polish reserve and the polish gets the
   *  rest (at least `min_mip_polish_time_slice_s`).  The caller should
   *  check `is_optimal()` after solve to detect timeouts.
   */
  std::optional<double> time_limit {};

  /** @brief Solver-internal scaling strategy.
   *
   *  Controls how the LP solver scales the constraint matrix before solving.
   *  When nullopt, the solver keeps its built-in default.
   *
   *  @see SolverScaling for the available strategies and backend mapping.
   */
  std::optional<SolverScaling> scaling {};

  /** @brief Controls barrier crossover — and, with it, which duals the
   *  cut path consumes.
   *
   *  Exposed via JSON (`solver_options.crossover`) since dbeb01cb —
   *  the previous "internal, not user-visible" caveat is obsolete.  Was a
   *  bool (true/false); now a `CrossoverMode` enum
   *  (`auto`/`primal`/`dual`/`none`) so the crossover ALGORITHM is selectable,
   *  not just on/off.  `--set solver_options.crossover=none|primal|dual|auto`.
   *
   *  Crossover converts the interior-point solution into a basic feasible
   *  solution, producing **vertex (basic)** dual values (row prices /
   *  reduced costs).
   *
   *  - `automatic` (default) — cross over to a vertex; let the solver pick the
   *    cheaper of primal/dual crossover.  `get_row_dual` / `get_col_dual`
   *    return basic duals.
   *  - `primal` / `dual` — cross over via the named algorithm.  `primal` is
   *    currently the best for the GTEP barrier solves when a basis is needed.
   *  - `none` — the solve stops at the interior point.  CPLEX/HiGHS still
   *    expose the **interior** reduced costs / row prices, and
   *    `LinearInterface::ensure_duals()` uses them DIRECTLY (no lazy
   *    crossover re-solve).  The interior dual is the unique analytic-center
   *    dual — a valid optimal dual (valid value-function subgradient) — so
   *    SDDP optimality cuts built from it are valid supporting hyperplanes,
   *    AND deterministic: unlike a vertex dual it is NOT basis-dependent, so
   *    `low_memory` off vs compress (which present the LP to the solver
   *    differently) cannot diverge onto different equal-cost vertices.  This
   *    is the off≡compress invariance lever (slower per solve — barrier has
   *    no warm-start — so it is opt-in via `plp2gtopt --solver-invariant`).
   *
   *  The SDDP forward pass and the elastic-filter clone set crossover=none
   *  for speed; the aperture pass sets barrier+crossover=none for the same
   *  reason.  Only meaningful when algorithm == barrier — simplex methods
   *  always produce vertex duals by construction.
   *
   *  Backend mapping (see CrossoverMode):
   *  - CPLEX: none → `SolutionType=NONBASIC`; else `SolutionType=AUTO` +
   *    `BarCrossAlg` = auto(0)/primal(1)/dual(2).  (`BarCrossAlg=-1` is
   *    deprecated/rejected — `none` uses SolutionType, not BarCrossAlg.)
   *  - HiGHS: none → `run_crossover="off"`, else `"on"`
   *  - MindOpt: none → `SolutionTarget=2`, else `SolutionTarget=0`
   *  - CLP: ignored (CLP barrier always does crossover)
   *
   *  Applied per solve: every solve (re-)sets the backend's crossover
   *  switch from the options it is given, so a forward solve with `none`
   *  does not leave later solves of the same backend without crossover
   *  (HiGHS used to keep `none` until reloaded).  To keep crossover off
   *  everywhere, set `none` in every pass's options (`solver_options`,
   *  `forward_solver_options`, `backward_solver_options`).
   */
  CrossoverMode crossover {CrossoverMode::automatic};

  /** @brief Warm-start this solve from the resident (advanced) basis.
   *
   *  Cross-solver primitive for re-optimizing a problem object IN PLACE
   *  after a small modification (typically column-bound changes) without
   *  rebuilding or re-solving from scratch.  Changing variable bounds
   *  does NOT invalidate a simplex basis — it stays dual-feasible — so a
   *  warm simplex re-solve off the previous optimal basis is usually a
   *  handful of pivots.  No basis is saved/restored: the backend reuses
   *  whatever basis is currently resident on its problem object.
   *
   *  Only the SIMPLEX methods can warm-start; barrier (interior point)
   *  cannot.  So when this is true the backend forces a simplex method,
   *  taking `algorithm` as the warm method (primal/dual); when
   *  `algorithm` is `default_algo`/`barrier`, the backend picks primal.
   *
   *  Pre-condition for any speedup: the problem object must already hold
   *  an optimal basis from a prior solve (e.g. a barrier solve WITH
   *  crossover, or any simplex solve).  On a cold object it degrades to a
   *  normal (cold) simplex solve.
   *
   *  Backend mapping:
   *  - CPLEX: `CPX_PARAM_ADVIND = 1` + `CPX_PARAM_LPMETHOD = primal|dual`,
   *    applied AFTER any `.prm` file so it wins over a pinned `LPMethod`.
   *  - Others (HiGHS/CLP/CBC/OSI/MindOpt/Gurobi): not yet wired — the
   *    field is ignored and the solve proceeds normally (still correct,
   *    just not warm).  This is the documented extension point.
   *
   *  Default: false (no warm start; preserves legacy behaviour).
   */
  bool advanced_basis {false};

  /** @brief Force barrier + primal-crossover for this solve, overriding any
   *  `.prm`-pinned `LPMethod`.  Internal (not JSON-exposed): set in-code on a
   *  local options copy by the SDDP aperture pass for the cold-canonical
   *  first-aperture solve (no resident basis) under the coordinated
   *  `aperture_seed_basis` scheme — a fast cold solve that lands on a
   *  deterministic vertex basis to anchor the subsequent dual warm starts.
   *
   *  Backend mapping (applied AFTER the `.prm`, like `advanced_basis`):
   *  - CPLEX: `CPX_PARAM_LPMETHOD = barrier` + `CPX_PARAM_BARCROSSALG =
   * primal`.
   *  - HiGHS: `solver = ipm` + `run_crossover = on`.
   *  - Others: ignored (the prm/default method stands — still correct).
   *
   *  Mutually exclusive with `advanced_basis` (a cold solve has no basis to
   *  advance from); if both are set, `advanced_basis` wins (warm beats cold).
   *  Default: false.
   */
  bool force_barrier_crossover {false};

  /** @brief Path to a backend-native parameter file applied before the
   *  fields above (so the typed gtopt fields keep priority on conflict).
   *
   *  Backend mapping:
   *  - CPLEX: the standard CPLEX ``.prm`` format (one
   *    ``CPXPARAM_<Name> <value>`` pair per line, ``#`` for comments),
   *    parsed once per run and applied like ``CPXreadcopyparam`` (every
   *    parameter reset to its CPLEX default, then the file's values).
   *    Lets users pin the full ~150 CPLEX parameter surface without
   *    growing this struct further.
   *  - HiGHS / MindOpt / CLP: ignored (each has its own native parser
   *    that we may wire later via the same field).
   *
   *  Warm-start siblings (``cplex_warmstart.prm``, ``highs_warmstart.opts``,
   *  ``gurobi_warmstart.prm``, ``mindopt_warmstart.prm``) are applied LAST
   *  on advanced-basis solves and override only the keys they list.  Every
   *  file is read once per run (later edits are not picked up).
   *
   *  Unset (``nullopt``, default) ⇒ no file is read, backend defaults
   *  apply as before.
   *
   *  A relative path is resolved like the case's other input files before
   *  any plugin sees it (`resolve_solver_param_files`): first relative to
   *  `input_directory` (the case directory wins, so the conventional
   *  ``"solvers/cplex.prm"`` works when gtopt runs from an output
   *  directory), and only when no such file exists there relative to the
   *  working directory.  The resolved path, and which of the two locations
   *  supplied it, is logged; a file found nowhere is
   *  reported at WARN and the solver runs with its defaults.  Sibling files
   *  derived from its directory (``cplex_warmstart.prm``, ``cuopt.cfg``,
   *  ``highs_warmstart.opts``, …) follow the resolved location.
   */
  std::optional<std::string> param_file {};

  /** @brief Maximum algorithm fallback attempts on non-optimal solve.
   *
   *  When a solve returns non-optimal, the solver cycles through
   *  alternative algorithms (barrier → dual → primal → barrier) up to
   *  this many times.  0 = no fallback (fail immediately).
   *  Default: 2 (try all three algorithms).
   */
  int max_fallbacks {2};

  /** @brief Request a solver-native memory-saving mode.
   *
   *  Named after CPLEX's `CPX_PARAM_MEMORYEMPHASIS`.  Independent of gtopt's
   *  own `sddp_options.low_memory_mode` (which controls backend release +
   *  flat-LP snapshot between phases).
   *
   *  - `nullopt` (default): do not touch the backend's memory parameter —
   *    each solver keeps its built-in default.
   *  - `true`: ask the backend to compact internal data structures at the
   *    cost of solve time.
   *  - `false`: explicitly disable the backend's memory-emphasis mode.
   *
   *  Unsupported backends silently ignore this hint regardless of value.
   *
   *  Backend mapping:
   *  - CPLEX:   CPX_PARAM_MEMORYEMPHASIS = 1 / 0 (only set when specified).
   *  - HiGHS:   no direct equivalent (ignored).
   *  - MindOpt: no direct equivalent (ignored).
   *  - OSI/CLP: no direct equivalent (ignored).
   *  - Gurobi:  no direct equivalent (ignored).
   */
  std::optional<bool> memory_emphasis {};

  /** @brief Polish a MIP incumbent: fix its integers and re-solve the LP.
   *
   *  After every MIP solve that holds an integer-feasible incumbent —
   *  proven optimal OR stopped early by `time_limit`, a node / memory /
   *  solution limit or an abort — gtopt pins every integer column (and
   *  SOS2 member) to the incumbent, relaxes integrality and re-solves the
   *  resulting LP.  The reported objective, primal and duals are those of
   *  that fixed-integer LP: the best dispatch for the incumbent's
   *  commitment decisions.  A MIP stopped at its limit typically returns
   *  an incumbent whose CONTINUOUS part is far from optimal for its own
   *  integers (heuristic incumbents are not re-optimised), so without the
   *  polish the published dispatch and cost can be much worse than the
   *  commitment deserves.
   *
   *  The polish shares the `time_limit` budget: when a limit is set the
   *  MIP gets the budget minus a polish reserve
   *  (`mip_polish_reserve_fraction` of the limit, at most
   *  `max_mip_polish_reserve_s`), and the polish LP gets what is left —
   *  never less than `min_mip_polish_time_slice_s`.  A polish that fails
   *  (fixed LP not solved) falls back to the incumbent with a warning.
   *  A non-optimal MIP stays reported as non-optimal (solution.csv status
   *  `limit_polished`).
   *
   *  - `nullopt` / `true` (default): polish.
   *  - `false`: publish the solver's incumbent untouched; the fixed-LP
   *    pass then only runs to recover duals of a proven-optimal MIP when
   *    dual / reduced-cost output is requested (the pre-polish
   *    behaviour).
   *
   *  No effect on pure LPs. */
  std::optional<bool> mip_polish {};

  /** @brief Stop a MIP after this many integer-feasible solutions.
   *  Internal (not JSON-exposed): a deterministic way to make a MIP stop
   *  at a limit while holding an incumbent — used by the `mip_polish`
   *  tests on every MIP backend.  Unset ⇒ the backend's default (no limit).
   *
   *  Backend mapping: CPLEX `CPX_PARAM_INTSOLLIM`, HiGHS
   *  `mip_max_improving_sols`, Gurobi `SolutionLimit`, MindOpt
   *  `MIP/MaxSols`, SCIP `limits/solutions`, CBC
   *  `CbcModel::setMaximumSolutions`; ignored by LP-only backends. */
  std::optional<int> mip_solution_limit {};

  /** @brief Interior-point implementation for `algorithm = barrier`.
   *
   *  Unset ⇒ `auto`.  See `BarrierSolver`.
   *
   *  Backend mapping:
   *  - HiGHS: `solver` = `ipm` (auto) / `ipx` / `hipo` on barrier LP
   *    solves, and `mip_ipm_solver` = `choose` / `ipx` / `hipo` for the
   *    IPM a MIP falls back to (and for `mip_lp_algorithm = barrier`).
   *  - Every other backend: ignored (one barrier implementation).
   */
  std::optional<BarrierSolver> barrier_solver {};

  /** @brief LP algorithm of a MIP's root relaxation.
   *
   *  Unset ⇒ the backend's default (no parameter is touched).
   *  `default` asks the backend to choose explicitly.
   *
   *  Backend mapping:
   *  - HiGHS: `mip_lp_solver` = `choose` (default) / `simplex` (primal,
   *    dual) / the `barrier_solver` IPM (`ipm`, `ipx`, `hipo`).  HiGHS
   *    uses it only for the first LP solved without a basis (the root).
   *  - CPLEX: `CPX_PARAM_STARTALG` = 0 / 1 / 2 / 4.
   *  - Every other backend: ignored.
   *  No effect on pure LPs.
   */
  std::optional<LPAlgo> mip_lp_algorithm {};

  /** @brief Share of MIP effort spent in primal heuristics, in [0, 1].
   *
   *  Unset ⇒ the backend's default (HiGHS 0.05), except under
   *  `mip_start_effort = repair`, where HiGHS gets 0.5 unless this is set.
   *
   *  Backend mapping:
   *  - HiGHS: `mip_heuristic_effort`.
   *  - Every other backend: ignored (their heuristic-effort parameters use
   *    other scales; set them through `param_file`).
   *  No effect on pure LPs.
   */
  std::optional<double> mip_heuristic_effort {};

  /** @brief How kappa (basis condition number) is obtained.
   *
   *  Unset ⇒ `estimate`.  See `KappaMode` for the per-backend mapping.
   */
  std::optional<KappaMode> kappa_mode {};

  /** @brief Advanced-basis request on a backend that holds no basis.
   *
   *  `advanced_basis` asks for a warm re-solve (HiGHS: simplex, presolve
   *  off, crossover off, off the resident basis).  When the backend has
   *  no basis yet (the first SDDP forward solve of a cell, a reloaded
   *  backend):
   *  - `true` / unset (default): solve cold with the normal settings
   *    (`algorithm`, `presolve`).  CEN 2y cell: 0.9 s instead of 2.1 s.
   *  - `false`: apply the warm settings anyway (presolve off, simplex) —
   *    the behaviour before 2026-11.
   *
   *  HiGHS only (CPLEX decides this itself from ADVIND); other backends
   *  ignore it.
   */
  std::optional<bool> cold_solve_without_basis {};

  /** @brief Reproducible multi-threaded solves.
   *
   *  - unset / `false` (default): opportunistic parallel mode — fastest,
   *    but thread timing decides the solve path, so the same LP can end
   *    on a different optimal basis from run to run, and a degenerate LP
   *    then reports different duals / reduced costs (seen under machine
   *    load).  The behaviour before this option.
   *  - `true`: deterministic parallel mode — the same LP, thread count
   *    and machine always give the same solution, whatever the load.
   *    The e2e golden tests run with it.
   *
   *  Backend mapping:
   *  - CPLEX:   CPX_PARAM_PARALLELMODE = 1 (`true`) / -1 (unset, `false`).
   *  - HiGHS, Gurobi, MindOpt, OSI/CLP: ignored (no opportunistic mode
   *    enabled by gtopt).
   */
  std::optional<bool> deterministic_parallel {};

  [[nodiscard]] constexpr bool deterministic_parallel_enabled() const noexcept
  {
    return deterministic_parallel.value_or(false);
  }

  [[nodiscard]] constexpr KappaMode effective_kappa_mode() const noexcept
  {
    return kappa_mode.value_or(KappaMode::estimate);
  }

  /// Member-wise equality.  Lets a backend skip re-applying an identical
  /// option bundle (e.g. CPLEX re-reading the `.prm` file before every
  /// solve).
  [[nodiscard]] constexpr bool operator==(const SolverOptions&) const = default;

  /** @brief Effective `mip_polish` (unset ⇒ enabled). */
  [[nodiscard]] constexpr bool mip_polish_enabled() const noexcept
  {
    return mip_polish.value_or(true);
  }

  /**
   * @brief Merge another SolverOptions into this one (first-value-wins for
   * optional fields).
   *
   * Only the optional tolerance / limit fields are merged; the non-optional
   * fields (algorithm, threads, presolve, log_level) are not changed by
   * merge — they keep their already-set values.
   */
  void merge(const SolverOptions& other)
  {
    merge_opt(optimal_eps, other.optimal_eps);
    merge_opt(feasible_eps, other.feasible_eps);
    merge_opt(barrier_eps, other.barrier_eps);
    merge_opt(mip_gap, other.mip_gap);
    merge_opt(mip_gap_abs, other.mip_gap_abs);
    merge_opt(time_limit, other.time_limit);
    merge_opt(log_mode, other.log_mode);
    merge_opt(scaling, other.scaling);
    merge_opt(memory_emphasis, other.memory_emphasis);
    merge_opt(mip_polish, other.mip_polish);
    merge_opt(mip_solution_limit, other.mip_solution_limit);
    merge_opt(barrier_solver, other.barrier_solver);
    merge_opt(mip_lp_algorithm, other.mip_lp_algorithm);
    merge_opt(mip_heuristic_effort, other.mip_heuristic_effort);
    merge_opt(kappa_mode, other.kappa_mode);
    merge_opt(cold_solve_without_basis, other.cold_solve_without_basis);
    merge_opt(deterministic_parallel, other.deterministic_parallel);
    merge_opt(param_file, other.param_file);
  }

  /**
   * @brief Overlay user-supplied options on top of backend defaults.
   *
   * Start from backend optimal defaults (this), then apply any user
   * option that is not at its sentinel/default value.  User settings
   * always win over backend defaults.
   *
   * Sentinels: algorithm=default_algo, threads=0, log_level=0,
   * max_fallbacks=2, presolve=true.
   * Optional fields: has_value() means user specified.
   */
  void overlay(const SolverOptions& user)
  {
    if (user.algorithm != LPAlgo::default_algo) {
      algorithm = user.algorithm;
    }
    if (user.threads != 0) {
      threads = user.threads;
    }
    if (!user.presolve) {
      presolve = user.presolve;
    }
    if (user.crossover != CrossoverMode::automatic) {
      // `crossover` defaults to `automatic` on both sides (struct default
      // for `user` and backend-optimal default).  Mirror the presolve
      // sentinel: only an explicit non-default choice (none/primal/dual)
      // overrides the base — e.g. the SDDP forward pass
      // (`sddp_iteration.cpp fwd_opts.crossover = CrossoverMode::none`) and
      // the elastic-filter clone solve.  Without this the user's
      // `crossover = none` was silently dropped during the overlay, leaving
      // crossover ON on every forward solve — observed on the large
      // hydrothermal case 2026-05-15 in every one of 4 K cplex_*.log files
      // including pure forward passes.
      crossover = user.crossover;
    }
    // Cold-canonical flag (default false): only the explicit "user wants ON"
    // case overrides the base, mirroring the bool sentinels above.  Without
    // this the aperture pass's force_barrier_crossover request was silently
    // dropped during overlay.  (advanced_basis is intentionally NOT overlaid
    // here — the warm path already works via the .prm's ADVIND=1 + the resident
    // basis, and overlaying it would reroute through apply_cplex_warmstart and
    // change the tuned warm method.)
    if (user.force_barrier_crossover) {
      force_barrier_crossover = user.force_barrier_crossover;
    }
    if (user.log_level != 0) {
      log_level = user.log_level;
    }
    if (user.max_fallbacks != 2) {
      max_fallbacks = user.max_fallbacks;
    }
    // optional fields: user wins if specified
    merge_opt(optimal_eps, user.optimal_eps);
    merge_opt(feasible_eps, user.feasible_eps);
    merge_opt(barrier_eps, user.barrier_eps);
    merge_opt(mip_gap, user.mip_gap);
    merge_opt(mip_gap_abs, user.mip_gap_abs);
    merge_opt(time_limit, user.time_limit);
    merge_opt(log_mode, user.log_mode);
    merge_opt(scaling, user.scaling);
    merge_opt(memory_emphasis, user.memory_emphasis);
    merge_opt(mip_polish, user.mip_polish);
    merge_opt(mip_solution_limit, user.mip_solution_limit);
    merge_opt(barrier_solver, user.barrier_solver);
    merge_opt(mip_lp_algorithm, user.mip_lp_algorithm);
    merge_opt(mip_heuristic_effort, user.mip_heuristic_effort);
    merge_opt(kappa_mode, user.kappa_mode);
    merge_opt(cold_solve_without_basis, user.cold_solve_without_basis);
    merge_opt(deterministic_parallel, user.deterministic_parallel);
    merge_opt(param_file, user.param_file);
  }
};

/// A retry that would get less than this many seconds of the remaining
/// `SolverOptions::time_limit` budget is skipped (no solver makes useful
/// progress on a large LP / MIP in under a second, and a zero / negative
/// limit is "no limit" for several backends).
inline constexpr double min_retry_time_slice_s = 1.0;

/// Share of `SolverOptions::time_limit` held back from the MIP for the
/// post-MIP polish LP (see `SolverOptions::mip_polish`), capped at
/// `max_mip_polish_reserve_s`.
inline constexpr double mip_polish_reserve_fraction = 0.05;

/// Upper bound, in seconds, of the polish reserve taken from the MIP.
inline constexpr double max_mip_polish_reserve_s = 300.0;

/// The polish LP always gets at least this many seconds, even when the MIP
/// consumed the whole `time_limit` budget (a fixed-integer LP warm-started
/// from the incumbent basis is usually a few pivots; a cold barrier on a
/// large UC needs more — the reserve above is what normally covers it).
inline constexpr double min_mip_polish_time_slice_s = 10.0;

/// Polish reserve for a MIP solve under @p time_limit:
/// `min(mip_polish_reserve_fraction * limit, max_mip_polish_reserve_s)`,
/// or 0 when no limit is set.
[[nodiscard]] constexpr double mip_polish_reserve(
    std::optional<double> time_limit) noexcept
{
  if (!time_limit.has_value() || *time_limit <= 0.0) {
    return 0.0;
  }
  const double share = mip_polish_reserve_fraction * *time_limit;
  return share < max_mip_polish_reserve_s ? share : max_mip_polish_reserve_s;
}

/// Remaining wall-clock budget of @p time_limit after @p elapsed_s seconds.
///
/// `std::nullopt` when no limit is set (unset or <= 0 ⇒ unlimited);
/// otherwise `time_limit - elapsed_s`, which may be zero or negative once
/// the budget is exhausted.
[[nodiscard]] constexpr std::optional<double> remaining_time_limit(
    std::optional<double> time_limit, double elapsed_s) noexcept
{
  if (!time_limit.has_value() || *time_limit <= 0.0) {
    return std::nullopt;
  }
  return *time_limit - elapsed_s;
}

}  // namespace gtopt
