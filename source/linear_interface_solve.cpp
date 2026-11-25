/**
 * @file      linear_interface_solve.cpp
 * @brief     LinearInterface solve dispatch — initial_solve / resolve.
 * @date      2026-04-26
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Extracted from `linear_interface.cpp`.  Holds the algorithm-fallback
 * helper (`next_fallback_algo`), the `initial_solve` and `resolve`
 * entry points, and the lazy-crossover / status helpers
 * (`ensure_duals`, `get_status`, `get_kappa`, `diagnose_row`).  All of
 * these share the timed-solve / fallback-cycle / log-guard scaffolding,
 * so they live together in their own translation unit.
 */

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <expected>
#include <format>
#include <fstream>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <gtopt/enum_option.hpp>
#include <gtopt/error.hpp>
#include <gtopt/kappa_mode.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/pricing_mode.hpp>
#include <gtopt/solver_options.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{
/// Accumulate one solve's effort into the process-global SolveEffortTotals
/// and return this solve's deterministic tick count so the caller can also
/// fold it into the per-LP `SolverStats::total_solve_ticks`.  Generic across
/// all backends: prefer the backend's native solver time + ticks
/// (`last_solve_effort`); fall back to the measured @p wall seconds when the
/// backend reports none, and to seconds when it reports no ticks.
[[nodiscard]] double accumulate_solve_effort(const SolverBackend& backend,
                                             double wall)
{
  const auto eff = backend.last_solve_effort();
  const double seconds = eff.seconds > 0.0 ? eff.seconds : wall;
  const double ticks = eff.ticks > 0.0 ? eff.ticks : seconds;
  SolveEffortTotals::instance().add(seconds, ticks, eff.iterations);
  return ticks;
}

/// Optimality-tolerance value a solver parameter file sets, if any.
///
/// Recognises the CPLEX names (`CPXPARAM_Simplex_Tolerances_Optimality`,
/// `CPX_PARAM_EPOPT`, `EpOpt`) and the generic `optimality_tolerance` /
/// `dual_feasibility_tolerance`.  Cached per path (the solve path calls
/// this for every LP solve).  An unreadable file counts as "not set".
[[nodiscard]] std::optional<double> param_file_optimality_tol(
    const std::string& path)
{
  static std::mutex mutex;
  static std::unordered_map<std::string, std::optional<double>> cache;
  const std::scoped_lock lock {mutex};
  if (const auto it = cache.find(path); it != cache.end()) {
    return it->second;
  }
  std::optional<double> found;
  std::ifstream in {path};
  std::string line;
  while (in && std::getline(in, line)) {
    std::istringstream ls {line};
    std::string key;
    std::string value;
    if (!(ls >> key >> value)) {
      continue;
    }
    std::string lower;
    lower.reserve(key.size());
    for (const char c : key) {
      lower.push_back(
          static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    if (lower == "cpxparam_simplex_tolerances_optimality"
        || lower == "cpx_param_epopt" || lower == "epopt"
        || lower == "optimality_tolerance"
        || lower == "dual_feasibility_tolerance")
    {
      try {
        found = std::stod(value);
      } catch (const std::exception&) {
        continue;
      }
    }
  }
  cache.emplace(path, found);
  return found;
}

/// Resolve the reduced-cost optimality tolerance (CPLEX ``EPOPT`` / HiGHS
/// dual-feasibility tolerance) the backend gets, in this order:
///
/// 1. ``solver_options.optimal_eps`` set by the user (JSON, ``--set``,
///    ``.gtopt.conf [solver.<name>]``): used as given;
/// 2. the solver parameter file (``param_file``, e.g. the case's
///    ``solvers/cplex.prm``) sets it: ``optimal_eps`` is left unset so the
///    file's value stays in force (a typed ``optimal_eps`` would override
///    it, see ``apply_options_to_env``);
/// 3. otherwise, when ``scale_objective != 1``, the scale-derived value
///    ``1e-6 / scale_objective`` clamped to ``[1e-9, 1e-1]``.
///
/// Why 3: the backend applies the tolerance in **raw** LP-objective space,
/// but ``flatten()`` divides the objective by ``scale_objective``
/// (linear_problem.cpp), so a raw reduced cost is
/// ``rc_raw = rc_phys / scale_objective`` and a fixed raw tolerance gets
/// coarser in physical terms as ``scale_objective`` grows.  ``1e-6`` is the
/// CPLEX default, i.e. the value that is correct at ``scale_objective == 1``.
/// A user / parameter-file value is never silently replaced by it.
///
/// Logs one info line per distinct outcome (value and why) per process.
void scale_optimality_tol_for_objective(SolverOptions& opts,
                                        const SolverOptions& user,
                                        double scale_objective)
{
  // One info line per distinct decision (value + reason) per process: the
  // solve path runs this for every LP solve.
  const auto log_once = [](std::string msg)
  {
    static std::mutex mutex;
    static std::unordered_set<std::string> seen;
    const std::scoped_lock lock {mutex};
    if (seen.insert(msg).second) {
      spdlog::info("{}", msg);
    }
  };

  if (user.optimal_eps.has_value()) {
    opts.optimal_eps = user.optimal_eps;
    log_once(std::format("optimality tolerance {:g}: from solver_options",
                         *user.optimal_eps));
    return;
  }
  if (opts.param_file.has_value() && !opts.param_file->empty()) {
    if (const auto prm = param_file_optimality_tol(*opts.param_file)) {
      opts.optimal_eps.reset();
      log_once(std::format("optimality tolerance {:g}: from parameter file {}",
                           *prm,
                           *opts.param_file));
      return;
    }
  }
  if (scale_objective == 1.0) {
    log_once("optimality tolerance: solver default (scale_objective = 1)");
    return;
  }
  constexpr double base_phys_eps = 1e-6;  // physical floor at scale_obj == 1
  constexpr double epopt_min = 1e-9;  // CPLEX EPOPT lower clamp
  constexpr double epopt_max = 1e-1;  // CPLEX EPOPT upper clamp
  opts.optimal_eps =
      std::clamp(base_phys_eps / scale_objective, epopt_min, epopt_max);
  log_once(std::format(
      "optimality tolerance {:g}: derived from scale_objective {:g} "
      "(1e-6 / scale_objective; no solver_options.optimal_eps or parameter "
      "file value)",
      *opts.optimal_eps,
      scale_objective));
}

/// Clamp the next fallback attempt to what is left of the solve's
/// wall-clock `time_limit` budget (started at @p solve_t0).
///
/// `time_limit` is a budget for the WHOLE solve of the LP / MIP, not per
/// attempt: without this, a MIP that hit its limit on the first attempt
/// was re-run up to `max_fallbacks` more times with the full limit each
/// (3 × 3600 s for a time_limit=3600 PCP case).  Returns false — skip the
/// attempt, keeping the last status / incumbent — once less than
/// `min_retry_time_slice_s` is left.  No-op (true) when no limit is set.
[[nodiscard]] bool clamp_retry_to_budget(
    SolverOptions& retry_opts,
    const std::optional<double>& time_limit,
    std::chrono::steady_clock::time_point solve_t0,
    std::string_view tag)
{
  const double elapsed =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - solve_t0)
          .count();
  const auto remaining = remaining_time_limit(time_limit, elapsed);
  if (!remaining.has_value()) {
    return true;
  }
  if (*remaining < min_retry_time_slice_s) {
    spdlog::info(
        "{}: time_limit budget of {:.1f}s exhausted after {:.1f}s — "
        "skipping the remaining algorithm fallbacks (keeping the last "
        "solver status / incumbent)",
        tag,
        time_limit.value_or(0.0),
        elapsed);
    return false;
  }
  retry_opts.time_limit = remaining;
  return true;
}
}  // namespace

std::expected<int, Error> LinearInterface::initial_solve(
    const SolverOptions& solver_options)
{
  using Clock = std::chrono::steady_clock;

  // Reconstruct the model from the compressed snapshot before solving.  Under
  // LowMemoryMode::compress the LP is built straight into the snapshot and the
  // backend is released WITHOUT ever loading the matrix, so the live backend is
  // empty (0 rows / 0 cols).  resolve() and fix_integers_and_resolve() already
  // do this; initial_solve() must too — otherwise the empty model reaches the
  // backend (CLP/CBC null-deref in ClpPresolve → SIGSEGV; HiGHS/MindOpt error;
  // CPLEX silently "solves" nothing).  No-op when not released (compress off).
  ensure_backend();

  // A new solve supersedes any bailed dual-recovery state (the latch
  // and the incumbent republished through the LI cache).
  clear_dual_recovery_bail_state();
  m_last_mip_polish_ = {};
  m_last_pricing_ = {};
  m_solve_kappa_read_ = false;

  ++m_solver_stats_.initial_solve_calls;
  m_solver_stats_.total_ncols += get_numcols();
  m_solver_stats_.total_nrows += get_numrows();

  try {
    // Start from backend-optimal defaults, overlay user settings on top.
    auto effective = m_backend_->optimal_options();
    effective.overlay(solver_options);
    // Resolve the optimality tolerance: user / parameter-file value, else
    // the scale_objective-derived one (EPOPT scale-leak, see helper).
    scale_optimality_tol_for_objective(
        effective, solver_options, m_scale_objective_);
    effective.kappa_mode = resolve_kappa_mode(effective.kappa_mode);
    m_last_solver_options_ = effective;

    m_backend_->apply_options(effective);

    const auto log_mode = effective.log_mode.value_or(SolverLogMode::nolog);
    const auto log_level = log_mode != SolverLogMode::nolog
        ? std::max(effective.log_level, 1)
        : effective.log_level;

    auto timed_solve = [&]
    {
      const auto t0 = Clock::now();
      if (log_mode != SolverLogMode::nolog && !m_log_file_.empty()) {
        const LogFileGuard log_guard(*this, m_log_file_, log_level);
        m_backend_->initial_solve();
      } else {
        const HandlerGuard guard(*this, log_level);
        m_backend_->initial_solve();
      }
      // See `resolve()` for the rationale.
      m_cache_.mark_solution_fresh(/*v=*/true);
      // Eagerly snapshot the just-solved primal/dual/reduced-cost
      // vectors into the LI cache so all downstream readers go through
      // the LI cache (single source of truth).  Plugin backends no
      // longer maintain a parallel solution cache.
      populate_solution_cache_post_solve();
      const double wall =
          std::chrono::duration<double>(Clock::now() - t0).count();
      m_solver_stats_.total_solve_time_s += wall;
      m_solver_stats_.total_solve_ticks +=
          accumulate_solve_effort(*m_backend_, wall);
    };

    // Wall-clock origin of the time_limit budget shared by the first
    // attempt and every fallback below.
    const auto solve_t0 = Clock::now();
    timed_solve();

    // Options of the last attempt that PROVED infeasibility (status 2).
    // A later fallback attempt that merely gives up must not overwrite
    // that verdict: e.g. HiGHS 1.15's IPX without presolve fails
    // ("IPM failed", kError → abandoned) on an LP the dual simplex has
    // already proven infeasible, which would turn a definitive
    // "infeasible" into a solver failure.
    std::optional<SolverOptions> proven_infeasible_opts;
    if (get_status() == 2) {
      proven_infeasible_opts = effective;
    }

    if (!is_optimal() && effective.max_fallbacks > 0) {
      // Algorithm fallback cycle: try alternative algorithms
      auto fallback_opts = effective;
      auto current_algo = effective.algorithm;

      for (int attempt = 0; attempt < effective.max_fallbacks && !is_optimal();
           ++attempt)
      {
        if (!clamp_retry_to_budget(
                fallback_opts,
                effective.time_limit,
                solve_t0,
                m_log_tag_.empty() ? get_prob_name() : m_log_tag_))
        {
          break;
        }
        const auto next_algo = next_fallback_algo(current_algo);
        spdlog::warn("{}: initial_solve non-optimal with {}, fallback to {}",
                     m_log_tag_.empty() ? get_prob_name() : m_log_tag_,
                     current_algo,
                     next_algo);

        fallback_opts.algorithm = next_algo;
        fallback_opts.presolve = false;
        // Reset aggressive scaling on fallback — aggressive scaling can
        // cause numerical difficulties (high kappa) that prevent the
        // primary algorithm from converging; the fallback algorithm may
        // succeed with the solver's default scaling strategy.
        if (fallback_opts.scaling == SolverScaling::aggressive) {
          fallback_opts.scaling = SolverScaling::automatic;
        }
        m_backend_->apply_options(fallback_opts);

        ++m_solver_stats_.fallback_solves;
        timed_solve();
        if (get_status() == 2) {
          proven_infeasible_opts = fallback_opts;
        }

        current_algo = next_algo;
      }

      // The cycle ended on a weaker verdict (abandoned / unknown) than an
      // earlier attempt's proof of infeasibility: re-run that attempt so
      // the backend state, `get_status()` and the stats report the proof.
      if (!is_optimal() && get_status() != 2
          && proven_infeasible_opts.has_value())
      {
        m_backend_->apply_options(*proven_infeasible_opts);
        ++m_solver_stats_.fallback_solves;
        timed_solve();
      }
    }

    if (!is_optimal()) {
      // Classify infeasibility for the end-of-run stats report.  Query
      // the backend directly (not get_status()) so primal/dual are
      // distinguishable — get_status() collapses them into status 2.
      ++m_solver_stats_.infeasible_count;
      if (m_backend_->is_proven_primal_infeasible()) {
        ++m_solver_stats_.primal_infeasible;
      } else if (m_backend_->is_proven_dual_infeasible()) {
        ++m_solver_stats_.dual_infeasible;
      }
      return std::unexpected(Error {
          .code = ErrorCode::SolverError,
          .message = std::format(
              "Solver returned non-optimal for problem: {} status: {}{}",
              get_prob_name(),
              get_status(),
              effective.max_fallbacks > 0 ? " (after algorithm fallback cycle)"
                                          : ""),
          .status = get_status(),
      });
    }

    query_solve_kappa();

    const auto status = get_status();
    cache_and_release();
    return status;

  } catch (const std::exception& e) {
    return std::unexpected(Error {
        .code = ErrorCode::InternalError,
        .message =
            std::format("Unexpected error in initial_solve: {}", e.what()),
    });
  }
}

std::expected<int, Error> LinearInterface::resolve(
    const SolverOptions& solver_options)
{
  using Clock = std::chrono::steady_clock;

  ensure_backend();

  // A new solve supersedes any bailed dual-recovery state (the latch
  // and the incumbent republished through the LI cache).
  clear_dual_recovery_bail_state();
  m_last_mip_polish_ = {};
  m_last_pricing_ = {};
  m_solve_kappa_read_ = false;

  ++m_solver_stats_.resolve_calls;
  m_solver_stats_.total_ncols += get_numcols();
  m_solver_stats_.total_nrows += get_numrows();

  try {
    // Start from backend-optimal defaults, overlay user settings on top.
    auto effective = m_backend_->optimal_options();
    effective.overlay(solver_options);
    // Resolve the optimality tolerance: user / parameter-file value, else
    // the scale_objective-derived one (EPOPT scale-leak, see helper).
    scale_optimality_tol_for_objective(
        effective, solver_options, m_scale_objective_);
    effective.kappa_mode = resolve_kappa_mode(effective.kappa_mode);
    m_last_solver_options_ = effective;

    m_backend_->apply_options(effective);

    const auto log_mode = effective.log_mode.value_or(SolverLogMode::nolog);
    const auto log_level = log_mode != SolverLogMode::nolog
        ? std::max(effective.log_level, 1)
        : effective.log_level;

    auto timed_solve = [&]
    {
      const auto t0 = Clock::now();
      if (log_mode != SolverLogMode::nolog && !m_log_file_.empty()) {
        const LogFileGuard log_guard(*this, m_log_file_, log_level);
        m_backend_->resolve();
      } else {
        const HandlerGuard guard(*this, log_level);
        m_backend_->resolve();
      }
      // Live backend's `is_proven_optimal()` / `col_solution()` now
      // reflect the just-completed solve.  Set BEFORE the
      // `is_optimal()` check below drives the fallback cycle.
      m_cache_.mark_solution_fresh(/*v=*/true);
      // Eagerly snapshot the just-solved primal/dual/reduced-cost
      // vectors into the LI cache so all downstream readers go through
      // the LI cache (single source of truth).  Plugin backends no
      // longer maintain a parallel solution cache.
      populate_solution_cache_post_solve();
      const double wall =
          std::chrono::duration<double>(Clock::now() - t0).count();
      m_solver_stats_.total_solve_time_s += wall;
      m_solver_stats_.total_solve_ticks +=
          accumulate_solve_effort(*m_backend_, wall);
    };

    // Wall-clock origin of the time_limit budget shared by the first
    // attempt and every fallback below.
    const auto solve_t0 = Clock::now();
    timed_solve();

    // Options of the last attempt that PROVED infeasibility (status 2).
    // A later fallback attempt that merely gives up must not overwrite
    // that verdict: e.g. HiGHS 1.15's IPX without presolve fails
    // ("IPM failed", kError → abandoned) on an LP the dual simplex has
    // already proven infeasible, which would turn a definitive
    // "infeasible" into a solver failure.
    std::optional<SolverOptions> proven_infeasible_opts;
    if (get_status() == 2) {
      proven_infeasible_opts = effective;
    }

    if (!is_optimal() && effective.max_fallbacks > 0) {
      // Algorithm fallback cycle: try alternative algorithms
      auto fallback_opts = effective;
      auto current_algo = effective.algorithm;

      for (int attempt = 0; attempt < effective.max_fallbacks && !is_optimal();
           ++attempt)
      {
        if (!clamp_retry_to_budget(
                fallback_opts,
                effective.time_limit,
                solve_t0,
                m_log_tag_.empty() ? get_prob_name() : m_log_tag_))
        {
          break;
        }
        const auto next_algo = next_fallback_algo(current_algo);
        // Demoted to DEBUG: these fallback-cycle lines were printing 2–3
        // consecutive warn lines per infeasible LP (barrier→dual→primal).
        // Final outcome is covered by the single "elastic ok" / "installed
        // fcut" INFO line in sddp_forward_pass.cpp, so the fallback-step
        // chatter is noise in the main log.  Still available at debug
        // level for a deep post-mortem.
        SPDLOG_DEBUG("{}: resolve non-optimal with {}, fallback to {}",
                     m_log_tag_.empty() ? get_prob_name() : m_log_tag_,
                     current_algo,
                     next_algo);

        fallback_opts.algorithm = next_algo;
        fallback_opts.presolve = false;
        // Reset aggressive scaling on fallback — aggressive scaling can
        // cause numerical difficulties (high kappa) that prevent the
        // primary algorithm from converging; the fallback algorithm may
        // succeed with the solver's default scaling strategy.
        if (fallback_opts.scaling == SolverScaling::aggressive) {
          fallback_opts.scaling = SolverScaling::automatic;
        }
        m_backend_->apply_options(fallback_opts);

        ++m_solver_stats_.fallback_solves;
        timed_solve();
        if (get_status() == 2) {
          proven_infeasible_opts = fallback_opts;
        }

        current_algo = next_algo;
      }

      // The cycle ended on a weaker verdict (abandoned / unknown) than an
      // earlier attempt's proof of infeasibility: re-run that attempt so
      // the backend state, `get_status()` and the stats report the proof.
      if (!is_optimal() && get_status() != 2
          && proven_infeasible_opts.has_value())
      {
        m_backend_->apply_options(*proven_infeasible_opts);
        ++m_solver_stats_.fallback_solves;
        timed_solve();
      }
    }

    if (!is_optimal()) {
      // Classify infeasibility for the end-of-run stats report.
      ++m_solver_stats_.infeasible_count;
      if (m_backend_->is_proven_primal_infeasible()) {
        ++m_solver_stats_.primal_infeasible;
      } else if (m_backend_->is_proven_dual_infeasible()) {
        ++m_solver_stats_.dual_infeasible;
      }
      return std::unexpected(Error {
          .code = ErrorCode::SolverError,
          .message = std::format(
              "Solver returned non-optimal for problem: {} status: {}{}",
              get_prob_name(),
              get_status(),
              effective.max_fallbacks > 0 ? " (after algorithm fallback cycle)"
                                          : ""),
          .status = get_status(),
      });
    }

    query_solve_kappa();

    const auto status = get_status();
    cache_and_release();
    return status;

  } catch (const std::exception& e) {
    return std::unexpected(Error {
        .code = ErrorCode::InternalError,
        .message = std::format("Unexpected error in resolve: {}", e.what()),
    });
  }
}

std::expected<LinearInterface::FixIntegersOutcome, Error>
LinearInterface::fix_integers_and_resolve(const SolverOptions& opts)
{
  using Clock = std::chrono::steady_clock;

  ensure_backend();

  // A repeated fix pass supersedes any prior bailed state.
  clear_dual_recovery_bail_state();

  // Build the effective options the backend's internal re-solve runs
  // under: backend-optimal defaults, user settings overlaid on top, then
  // the scale-objective optimality-tolerance correction — exactly what
  // `resolve()` applies before a solve (this TU owns the helper).  The
  // common backend method re-applies these via `apply_options(opts)`
  // internally; the CPLEX override then overrides only the LP method to
  // warm dual simplex for the throwaway fixed-LP pass.
  auto effective = m_backend_->optimal_options();
  effective.overlay(opts);
  scale_optimality_tol_for_objective(effective, opts, m_scale_objective_);
  effective.kappa_mode = resolve_kappa_mode(effective.kappa_mode);
  m_last_solver_options_ = effective;

  // Snapshot the MIP incumbent primal BEFORE the backend mutates
  // anything.  If the pass BAILS the incumbent is republished through
  // the LI cache below — the uniform, backend-independent guarantee
  // that cache-first readers (`get_col_sol{,_raw}`, the OutputContext
  // solution span) publish the MIP primal after a bail, whatever state
  // the backend's own restore achieved.  One vector copy, paid only on
  // LPs with discrete structure.
  const auto pre_fix_sol = get_col_sol_raw();
  const std::vector<double> incumbent(pre_fix_sol.begin(), pre_fix_sol.end());

  // Delegate the fix + re-solve to the one common backend virtual.  Each
  // plugin implements it with its native mechanism (CPLEX: single-call
  // `CPXPROB_FIXEDMILP` warm dual pass; everyone else: the portable
  // pin-bounds -> relax -> resolve default).  The fix happens on the LIVE
  // backend right after the MIP solve — no compress/reconstruct between
  // fix and read — so this is intentionally NOT routed through the LI
  // replay log; the fixed LP is a throwaway whose sole product is the
  // committed-solution duals.
  //
  // `sos2_member_cols()` hands the backend the SOS2 member columns
  // recorded at `load_flat`: they are CONTINUOUS, so the integer pin
  // alone leaves them free, and an SOS set is inert in the pure LP
  // re-solve — the members must be pinned to the incumbent too or the
  // fixed LP re-optimizes them to SOS-infeasible vertices (λ-form loss
  // ladders re-inflate to the envelope under negative dual pair-sums).
  const auto t0 = Clock::now();
  const int fixed =
      m_backend_->fix_mip_and_resolve_duals(effective, sos2_member_cols());
  const double fix_wall =
      std::chrono::duration<double>(Clock::now() - t0).count();
  m_solver_stats_.total_solve_time_s += fix_wall;
  m_solver_stats_.total_solve_ticks +=
      accumulate_solve_effort(*m_backend_, fix_wall);

  if (fixed < 0) {
    // The backend BAILED (no incumbent, fixed-LP solve failure, or the
    // CPLEX SOS-support verification fired) and — per the
    // `fix_mip_and_resolve_duals` bail contract — restored its live
    // problem to the MIP incumbent.  Belt-and-braces: republish the
    // pre-fix incumbent through the LI cache so cache-first readers
    // publish the MIP primal even if a backend's restore is imperfect,
    // and latch `duals_unavailable` so the dual / reduced-cost getters
    // return empty views instead of MIP dual scratch.  The distinct
    // `bailed` outcome lets callers warn ("MIP primal preserved; duals
    // unavailable") instead of confusing this with the benign pure-LP
    // no-op below.
    if (!incumbent.empty()) {
      const auto buf = m_cache_.col_sol_buffer(incumbent.size());
      std::ranges::copy(incumbent, buf.begin());
    }
    m_duals_unavailable_ = true;
    return FixIntegersOutcome {
        .fixed_columns = 0,
        .status = std::nullopt,
        .bailed = true,
    };
  }
  if (fixed == 0) {
    // Pure LP — nothing was pinned and no re-solve ran; the caller's
    // existing duals are already valid.
    return FixIntegersOutcome {.fixed_columns = 0, .status = std::nullopt};
  }

  // The fixed LP was solved on the live backend; refresh the LI cache the
  // same way `resolve()` does post-solve so downstream dual / reduced-cost
  // readers see the committed-solution values, not stale MIP data.
  ++m_solver_stats_.resolve_calls;
  m_cache_.mark_solution_fresh(/*v=*/true);
  populate_solution_cache_post_solve();

  return FixIntegersOutcome {
      .fixed_columns = Index {fixed},
      .status = 0,
  };
}

bool LinearInterface::has_mip_incumbent() const
{
  if (m_backend_released_ || m_backend_ == nullptr) {
    return false;
  }
  try {
    return m_backend_->has_mip_incumbent();
  } catch (...) {
    return false;
  }
}

std::expected<LinearInterface::MipPolishOutcome, Error>
LinearInterface::polish_mip(const SolverOptions& opts)
{
  ensure_backend();

  MipPolishOutcome out {};
  if (sos2_set_count() == 0 && !has_integer_cols()) {
    return out;  // pure LP — nothing to polish
  }

  out.mip_optimal = is_optimal();
  if (!out.mip_optimal && !has_mip_incumbent()) {
    // Stopped (limit / failure) without an integer-feasible point: there
    // are no integer values to fix.
    out.no_incumbent = true;
    m_last_mip_polish_ = out;
    return out;
  }

  out.incumbent_obj = get_obj_value();
  auto fix = fix_integers_and_resolve(opts);
  if (!fix) {
    return std::unexpected(std::move(fix.error()));
  }
  out.attempted = true;
  out.fixed_columns = fix->fixed_columns;
  if (fix->bailed) {
    out.bailed = true;
  } else if (fix->fixed_columns > 0) {
    out.polished = true;
    out.polished_obj = get_obj_value();
  }
  m_last_mip_polish_ = out;
  return out;
}

namespace
{

/// TEST-ONLY: `GTOPT_TEST_FORCE_PRICING_FAIL=1` makes the pricing LP
/// report failure so tests can exercise the fallback-to-fixed path end to
/// end (relaxing integrality never makes a feasible MIP infeasible, so a
/// real failure needs numerical trouble or a limit).  Read fresh on every
/// call (cold path: once per MIP cell).
[[nodiscard]] bool test_force_pricing_fail() noexcept
{
  // NOLINTNEXTLINE(concurrency-mt-unsafe)
  const char* const v = std::getenv("GTOPT_TEST_FORCE_PRICING_FAIL");
  return v != nullptr && v[0] == '1';
}

}  // namespace

LinearInterface::PricingDuals LinearInterface::solve_pricing_lp(
    PricingMode mode, const SolverOptions& opts)
{
  PricingDuals out;
  out.outcome.requested = mode;
  out.outcome.applied = PricingMode::fixed;
  if (mode == PricingMode::fixed) {
    return out;  // the fixed-integer polish IS the fixed pricing LP
  }

  const auto fallback = [&](std::string reason, int status = -1)
  {
    out.outcome.applied = PricingMode::fixed;
    out.outcome.fallback = true;
    out.outcome.status = status;
    out.outcome.reason = std::move(reason);
    out.row_dual_raw.clear();
    out.col_cost_raw.clear();
    return std::move(out);
  };

  try {
    ensure_backend();
    const auto ncols = static_cast<int>(get_numcols());

    // Integer set of the MIP — read BEFORE the polish relaxes it.
    std::vector<int> int_cols;
    for (int c = 0; c < ncols; ++c) {
      if (is_integer(ColIndex {c})) {
        int_cols.push_back(c);
      }
    }
    if (int_cols.empty()) {
      // Pure LP (or SOS-only): no commitment to relax — the fixed pass
      // already yields the only meaningful prices.
      out.outcome.reason = "no integer columns";
      return out;
    }

    const auto is_relaxed = [&](int c)
    {
      return mode == PricingMode::relaxed
          || std::ranges::binary_search(m_fast_start_cols_, c);
    };
    const auto n_relaxed = std::ranges::count_if(int_cols, is_relaxed);
    if (n_relaxed == 0) {
      // fast_start without fast-start units: identical to fixed pricing.
      out.outcome.reason = "no fast-start commitment columns";
      return out;
    }

    if (!is_optimal() && !has_mip_incumbent()) {
      return fallback("no MIP incumbent");
    }

    // Snapshot the incumbent before cloning (cache-first read).
    const auto sol_span = get_col_sol_raw();
    const std::vector<double> sol(sol_span.begin(), sol_span.end());
    if (std::cmp_less(sol.size(), ncols)) {
      return fallback("MIP incumbent unavailable");
    }

    auto pricing_lp = clone();

    std::vector<ColIndex> idx;
    std::vector<char> lu;
    std::vector<double> val;
    const auto pin = [&](int c, double v)
    {
      idx.emplace_back(c);
      lu.push_back('B');
      val.push_back(v);
    };
    for (const int c : int_cols) {
      if (!is_relaxed(c)) {
        // Integers may report a hair off the lattice under tolerance.
        pin(c, std::round(sol[static_cast<std::size_t>(c)]));
      }
    }
    const auto n_int_pinned = idx.size();
    for (const int c : m_sos2_member_cols_) {
      if (c >= 0 && c < ncols && !is_integer(ColIndex {c})) {
        pin(c, sol[static_cast<std::size_t>(c)]);
      }
    }
    pricing_lp.set_col_bounds_raw(idx, lu, val);
    (void)pricing_lp.relax_integers();

    out.outcome.relaxed_columns = static_cast<Index>(n_relaxed);
    out.outcome.pinned_columns = static_cast<Index>(idx.size());
    spdlog::debug(
        "pricing LP [{}]: mode={} relaxed={} pinned_int={} pinned_sos={}",
        get_prob_name(),
        enum_name(mode),
        n_relaxed,
        n_int_pinned,
        idx.size() - n_int_pinned);

    auto solved = pricing_lp.resolve(opts);
    if (!solved) {
      return fallback(
          std::format("pricing LP solve failed: {}", solved.error().message),
          solved.error().status);
    }
    if (test_force_pricing_fail() || !pricing_lp.is_optimal()) {
      return fallback(
          std::format("pricing LP not optimal (status {}{})",
                      pricing_lp.get_status(),
                      test_force_pricing_fail() ? ", forced by test hook" : ""),
          pricing_lp.get_status());
    }

    const auto duals = pricing_lp.get_row_dual_raw();
    const auto rcs = pricing_lp.get_col_cost_raw();
    if (duals.size() != static_cast<std::size_t>(get_numrows())
        || std::cmp_not_equal(rcs.size(), ncols))
    {
      return fallback("pricing LP duals unavailable");
    }
    out.row_dual_raw.assign(duals.begin(), duals.end());
    out.col_cost_raw.assign(rcs.begin(), rcs.end());
    out.outcome.applied = mode;
    out.outcome.attempted = true;
    out.outcome.status = 0;
    return out;
  } catch (const std::exception& e) {
    return fallback(std::format("pricing LP error: {}", e.what()));
  }
}

void LinearInterface::install_pricing_duals(PricingDuals pricing)
{
  auto& outcome = pricing.outcome;
  if (outcome.applied != PricingMode::fixed) {
    const auto ncols = static_cast<std::size_t>(get_numcols());
    const auto nrows = static_cast<std::size_t>(get_numrows());
    if (m_duals_unavailable_) {
      // The polish bailed: the primal is the raw incumbent and the
      // pricing duals were computed for it, but keep the uniform
      // "duals unavailable" contract of a bailed cell.
      outcome.applied = PricingMode::fixed;
      outcome.fallback = true;
      outcome.reason = "fixed-integer polish bailed; duals unavailable";
    } else if (pricing.row_dual_raw.size() != nrows
               || pricing.col_cost_raw.size() != ncols)
    {
      outcome.applied = PricingMode::fixed;
      outcome.fallback = true;
      outcome.reason = "pricing dual size mismatch";
    } else {
      // Keep the current (polished MIP) primal: copy it out first — the
      // span may alias the cache buffer resized below.
      const auto sol_span = get_col_sol_raw();
      const std::vector<double> sol(sol_span.begin(), sol_span.end());
      if (m_cache_.col_low().size() != ncols
          || m_cache_.col_upp().size() != ncols)
      {
        ensure_backend();
        m_backend_->fill_col_lower(m_cache_.col_low_buffer(ncols));
        m_backend_->fill_col_upper(m_cache_.col_upp_buffer(ncols));
      }
      std::ranges::copy(sol, m_cache_.col_sol_buffer(ncols).begin());
      std::ranges::copy(pricing.col_cost_raw,
                        m_cache_.col_cost_buffer(ncols).begin());
      std::ranges::copy(pricing.row_dual_raw,
                        m_cache_.row_dual_buffer(nrows).begin());
      m_cache_.set_is_optimal(/*v=*/true);
      m_pricing_installed_ = true;
    }
  }
  m_last_pricing_ = std::move(outcome);
}

}  // namespace gtopt
