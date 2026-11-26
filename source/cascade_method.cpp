/**
 * @file      cascade_method.cpp
 * @brief     Multi-level cascade method implementation
 * @date      2026-03-22
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>  // mkdtemp
#include <filesystem>
#include <format>
#include <fstream>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <daw/daw_read_file.h>
#include <gtopt/arrow_index_cache.hpp>
#include <gtopt/as_label.hpp>
#include <gtopt/cascade_method.hpp>
#include <gtopt/cascade_progress.hpp>
#include <gtopt/constraint_names.hpp>
#include <gtopt/enum_option.hpp>
#include <gtopt/fmap.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/label_maker.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/process_util.hpp>
#include <gtopt/sddp_common.hpp>  // gtopt::format_si
#include <gtopt/sddp_cut_io.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_file_loader.hpp>
#include <gtopt/utils.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

// ─── Constructor ────────────────────────────────────────────────────────────

CascadePlanningMethod::CascadePlanningMethod(
    SDDPOptions base_opts, CascadeOptions cascade_opts) noexcept
    : m_base_opts_(std::move(base_opts))
    , m_cascade_opts_(std::move(cascade_opts))
{
  if (m_cascade_opts_.level_array.empty()) {
    // Empty cascade = single level with default model/solver options.
    // Equivalent to running the base SDDP solver directly.
    m_cascade_opts_.level_array = {
        CascadeLevel {
            .name = OptName {"default"},
        },
    };
  }
}

// ─── Build SDDPOptions for one level ────────────────────────────────────────

auto CascadePlanningMethod::build_level_sddp_opts(
    const std::optional<CascadeLevelMethod>& level_solver,
    int remaining_budget) const -> SDDPOptions
{
  auto opts = m_base_opts_;

  // Apply cascade-global SDDP options as per-level defaults.
  // max_iterations is special: it's a global budget applied via
  // remaining_budget, not a per-level default.  `value_or(current)` is
  // the `double ← optional<double>` equivalent of `merge_opt`
  // (which only handles optional → optional).
  const auto& cs = m_cascade_opts_.sddp_options;
  opts.convergence_tol = cs.convergence_tol.value_or(opts.convergence_tol);
  opts.min_iterations = cs.min_iterations.value_or(opts.min_iterations);
  opts.elastic_penalty = cs.elastic_penalty.value_or(opts.elastic_penalty);
  opts.scale_alpha = cs.scale_alpha.value_or(opts.scale_alpha);
  opts.stationary_tol = cs.stationary_tol.value_or(opts.stationary_tol);
  opts.stationary_gap_ceiling =
      cs.stationary_gap_ceiling.value_or(opts.stationary_gap_ceiling);

  // Apply per-level overrides
  if (level_solver) {
    opts.max_iterations =
        level_solver->max_iterations.value_or(opts.max_iterations);
    if (level_solver->apertures.has_value()) {
      opts.apertures = level_solver->apertures;
    }
    if (level_solver->num_apertures.has_value()) {
      opts.num_apertures = level_solver->num_apertures;
    }
    if (level_solver->aperture_selection_mode.has_value()) {
      opts.aperture_selection_mode = gtopt::require_enum<ApertureSelectionMode>(
          "aperture_selection_mode", *level_solver->aperture_selection_mode);
    }
    if (level_solver->aperture_solve_mode.has_value()) {
      opts.aperture_solve_mode = gtopt::require_enum<ApertureSolveMode>(
          "aperture_solve_mode", *level_solver->aperture_solve_mode);
    }
    apply_aperture_chunk_override(
        opts,
        level_solver->aperture_chunk_mode.transform(
            [](const std::string& name)
            {
              return gtopt::require_enum<ApertureChunkMode>(
                  "aperture_chunk_mode", name);
            }),
        level_solver->aperture_chunk_size);
    opts.aperture_screen_count = level_solver->aperture_screen_count.value_or(
        opts.aperture_screen_count);
    opts.aperture_seed_basis =
        level_solver->aperture_seed_basis.value_or(opts.aperture_seed_basis);
    opts.min_iterations =
        level_solver->min_iterations.value_or(opts.min_iterations);
    opts.convergence_tol =
        level_solver->convergence_tol.value_or(opts.convergence_tol);
    opts.stationary_tol =
        level_solver->stationary_tol.value_or(opts.stationary_tol);
    opts.stationary_gap_ceiling = level_solver->stationary_gap_ceiling.value_or(
        opts.stationary_gap_ceiling);
    opts.stationary_window =
        level_solver->stationary_window.value_or(opts.stationary_window);
    if (level_solver->elastic_mode.has_value()) {
      opts.elastic_filter_mode = gtopt::require_enum<ElasticFilterMode>(
          "elastic_mode", *level_solver->elastic_mode);
    }
    opts.elastic_penalty =
        level_solver->elastic_penalty.value_or(opts.elastic_penalty);
    // Per-level solver options: the level's optional fields win over the
    // base (already pre-merged with the global solver_options).
    CascadeLevelMethod::merge_solver_opts(opts.forward_solver_options,
                                          level_solver->forward_solver_options);
    CascadeLevelMethod::merge_solver_opts(
        opts.backward_solver_options, level_solver->backward_solver_options);
  }

  // Cap max_iterations to the remaining global budget
  if (remaining_budget >= 0) {
    opts.max_iterations = std::min(opts.max_iterations, remaining_budget);
  }

  return opts;
}

// ─── Clone Planning with LP overrides ───────────────────────────────────────
//
// `resolve_system_file` / `load_system_from_file` now live in
// `gtopt/system_file_loader.hpp` (shared with the SDDP aperture-system
// feature).  The cascade `system_file` path keeps using the system-only
// loader; its own `model_options` overlay remains authoritative.

auto CascadePlanningMethod::clone_planning_with_overrides(
    const Planning& source, const ModelOptions& model_opts) -> Planning
{
  Planning copy = source;

  copy.options.model_options.merge(model_opts);
  return copy;
}

// ─── Clear all cuts ─────────────────────────────────────────────────────────

void CascadePlanningMethod::clear_all_cuts(PlanningLP& planning_lp,
                                           const SDDPMethod& solver)
{
  int total_removed = 0;

  for (auto&& [scene, _sc] :
       enumerate<SceneIndex>(planning_lp.simulation().scenes()))
  {
    const auto& states = solver.phase_states(scene);

    for (auto&& [phase, _ph] :
         enumerate<PhaseIndex>(planning_lp.simulation().phases()))
    {
      auto& li = planning_lp.system(scene, phase).linear_interface();
      const auto base = static_cast<int>(states[phase].base_nrows);
      const auto current = li.get_numrows();

      if (current > base) {
        auto indices = std::ranges::to<std::vector>(iota_range(base, current));
        li.delete_rows(indices);
        total_removed += current - base;
      }
    }
  }

  SPDLOG_INFO("Cascade: cleared {} cut rows from LPs", total_removed);
}

// ─── Nonanticipative merge vs cascade levels ────────────────────────────────

auto CascadePlanningMethod::validate_nonanticipative_levels(
    const SimulationLP& sim, int remaining_budget) const -> std::optional<Error>
{
  const auto& merge = sim.scene_merge();
  if (!merge.has_value()) {
    return std::nullopt;
  }
  for (std::size_t i = 0; i < m_cascade_opts_.level_array.size(); ++i) {
    const auto& level = m_cascade_opts_.level_array[i];
    if (!level.active.value_or(true)) {
      continue;
    }
    const auto level_name = level.name.value_or(as_label("level", i));
    const auto opts =
        build_level_sddp_opts(level.sddp_options, remaining_budget);
    if (auto err = validate_nonanticipative_sddp(sim, opts)) {
      err->message = std::format("cascade level '{}' (level_array[{}]): {}",
                                 level_name,
                                 i,
                                 err->message);
      return err;
    }
    if (i > 0 && level.transition
        && level.transition->inherit_optimality_cuts.value_or(0) != 0)
    {
      return Error {
          .code = ErrorCode::InvalidInput,
          .message = std::format(
              "cascade level '{}' (level_array[{}]): decision_variable "
              "'{}': `nonanticipative: true` merges {} scenes into one LP "
              "scene, which is incompatible with "
              "transition.inherit_optimality_cuts = {} — inherited cuts are "
              "stored per (class, variable, uid) without the scenario and "
              "cannot address the per-scenario state columns of the merged "
              "scene (not supported; set inherit_optimality_cuts = 0 or "
              "drop the flag)",
              level_name,
              i,
              merge->variables.front(),
              merge->original_scenes.size(),
              level.transition->inherit_optimality_cuts.value_or(0)),
      };
    }
  }
  return std::nullopt;
}

// ─── Main solve orchestration ───────────────────────────────────────────────

namespace
{

/// Write `<dir>/level_summary.json` for one finished cascade level.
void write_level_summary_json(const std::filesystem::path& dir,
                              const CascadeLevelStats& ls,
                              std::size_t level_idx,
                              std::string_view system_file)
{
  std::ofstream ofs(dir / "level_summary.json");
  if (!ofs) {
    SPDLOG_WARN("Cascade: cannot write {}",
                (dir / "level_summary.json").string());
    return;
  }
  ofs << std::format(
      "{{\n  \"level\": {},\n  \"name\": \"{}\",\n"
      "  \"system_file\": \"{}\",\n  \"iterations\": {},\n"
      "  \"lower_bound\": {:.17g},\n  \"upper_bound\": {:.17g},\n"
      "  \"gap\": {:.17g},\n  \"converged\": {},\n"
      "  \"cuts_added\": {},\n  \"elapsed_s\": {:.3f}\n}}\n",
      level_idx,
      ls.name,
      system_file,
      ls.iterations,
      ls.lower_bound,
      ls.upper_bound,
      ls.gap,
      ls.converged,
      ls.cuts_added,
      ls.elapsed_s);
}

/// (Re)write `<dir>/cascade_levels.csv` with one row per finished level.
void write_cascade_levels_csv(const std::filesystem::path& dir,
                              const std::vector<CascadeLevelStats>& stats)
{
  std::error_code ec;
  std::filesystem::create_directories(dir, ec);
  std::ofstream ofs(dir / "cascade_levels.csv");
  if (!ofs) {
    SPDLOG_WARN("Cascade: cannot write {}",
                (dir / "cascade_levels.csv").string());
    return;
  }
  ofs << "level,name,iterations,lower_bound,upper_bound,gap,converged,"
         "cuts_added,elapsed_s,aperture_solves,aperture_infeasible,"
         "aperture_failed,aperture_dropped_last_iteration\n";
  for (const auto& [i, ls] : std::views::enumerate(stats)) {
    ofs << std::format(
        "{},{},{},{:.17g},{:.17g},{:.17g},{},{},{:.3f},{},{},{},{}\n",
        i,
        ls.name,
        ls.iterations,
        ls.lower_bound,
        ls.upper_bound,
        ls.gap,
        ls.converged ? 1 : 0,
        ls.cuts_added,
        ls.elapsed_s,
        ls.apertures.solves,
        ls.apertures.infeasible,
        ls.apertures.failed,
        ls.apertures.dropped_in_last_iteration);
  }
}

/// Create a fresh private (0700) directory `<parent>/gtopt_cascade_XXXXXX`
/// with mkdtemp(3).  The name is unpredictable and creation is atomic, so a
/// pre-planted file or symlink cannot be reused.  Returns an empty path on
/// failure.
[[nodiscard]] std::filesystem::path make_private_temp_dir(
    const std::filesystem::path& parent)
{
  std::error_code ec;
  std::filesystem::create_directories(parent, ec);
  auto templ = (parent / "gtopt_cascade_XXXXXX").string();
  if (::mkdtemp(templ.data()) == nullptr) {
    return {};
  }
  return std::filesystem::path {templ};
}

/// Removes the cascade's private temp directory when solve() returns.
class CascadeTempDirCleanup
{
public:
  explicit CascadeTempDirCleanup(std::filesystem::path& dir) noexcept
      : m_dir_(dir)
  {
  }
  ~CascadeTempDirCleanup() noexcept
  {
    if (!m_dir_.empty()) {
      std::error_code ec;
      std::filesystem::remove_all(m_dir_, ec);
      m_dir_.clear();
    }
  }
  CascadeTempDirCleanup(const CascadeTempDirCleanup&) = delete;
  CascadeTempDirCleanup(CascadeTempDirCleanup&&) = delete;
  auto operator=(const CascadeTempDirCleanup&)
      -> CascadeTempDirCleanup& = delete;
  auto operator=(CascadeTempDirCleanup&&) -> CascadeTempDirCleanup& = delete;

private:
  std::filesystem::path& m_dir_;
};

}  // namespace

bool CascadePlanningMethod::is_owned_temp_file(const std::string& path) const
{
  if (m_tmp_dir_.empty() || path.empty()) {
    return false;
  }
  return std::filesystem::path {path}.parent_path() == m_tmp_dir_;
}

auto CascadePlanningMethod::solve(PlanningLP& planning_lp,
                                  const SolverOptions& opts)
    -> std::expected<int, Error>
{
  // Per-level output routing (`cascade_options.write_level_outputs`):
  // capture the caller's output directory once — the caller LP's own
  // directory is temporarily redirected while it serves as an
  // intermediate level and restored afterwards.
  const bool write_levels = m_cascade_opts_.write_level_outputs.value_or(false);
  const std::filesystem::path base_output_dir {
      std::string(planning_lp.options().output_directory()),
  };
  // Private temp dir for level-to-level cut files: created on first use
  // (see below), removed when solve() returns.
  const CascadeTempDirCleanup tmp_dir_cleanup {m_tmp_dir_};
  PlanningLP* current_lp = nullptr;
  // One arrow-index cache shared across every per-level PlanningLP so the
  // (cname, fname, uid) -> (Stage, Block) input index is built once for the
  // whole cascade and reused, instead of rebuilt cold each level (each level
  // owns a fresh SimulationLP by value, so the per-run index-cache fix would
  // otherwise be scoped to a single level).  Seeded by the first level that
  // builds, then handed to every subsequent level's constructor.  Levels
  // build sequentially, so there is no cross-level concurrency on the cache.
  std::shared_ptr<ArrowIndexCache> shared_index_cache;
  std::unique_ptr<SDDPMethod> current_solver;
  ModelOptions prev_effective_model = m_cascade_opts_.model_options;

  // Global iteration budget: cascade sddp_options.max_iterations applies
  // to the sum of all level iterations.  -1 means no global cap.
  const auto& cascade_max_iter = m_cascade_opts_.sddp_options.max_iterations;
  int remaining_budget = cascade_max_iter.has_value() ? *cascade_max_iter : -1;

  // Running iteration index at which the NEXT level should start.
  // Fed into each level's SDDPOptions::iteration_offset_hint so that level
  // N's iteration indices start strictly past level N-1's — giving every
  // level a disjoint range in m_cut_store_ (no collisions on
  // save_cuts_for_iteration) and a globally monotonic `[i{}]` in logs.
  IterationIndex global_iter_index {};

  SPDLOG_INFO("Cascade: starting with {} levels (global budget={})",
              m_cascade_opts_.level_array.size(),
              remaining_budget);

  // Locate the LAST ACTIVE level — its SDDP sim pass keeps writing
  // per-cell outputs; every earlier active level skips its sim-pass
  // write_out (the cells would be overwritten anyway).  Returns
  // `size()` when every level is inactive (loop body below never
  // executes, so the value is irrelevant).
  const std::size_t last_active_level_idx = [&]
  {
    for (std::size_t i = m_cascade_opts_.level_array.size(); i-- > 0;) {
      if (m_cascade_opts_.level_array[i].active.value_or(true)) {
        return i;
      }
    }
    return m_cascade_opts_.level_array.size();
  }();

  // ── Cascade checkpoint / resume bookkeeping ──
  //
  // Sidecar `cascade_progress.json` next to the per-level cut directories
  // tracks which levels have finished.  When a previous run died mid-
  // cascade, `--recover` (= `recovery_mode != none`) loads this file,
  // identifies the first non-done level, and skips earlier ones by
  // reusing their persisted cuts — no LP rebuild for the completed prefix.
  const std::filesystem::path progress_path = [&]() -> std::filesystem::path
  {
    if (m_base_opts_.cuts_output_file.empty()) {
      return {};  // no output dir → checkpointing disabled
    }
    return std::filesystem::path(m_base_opts_.cuts_output_file).parent_path()
        / "cascade_progress.json";
  }();
  const bool progress_enabled = !progress_path.empty();

  CascadeProgress progress;
  progress.run_id = std::format(
      "{}", std::chrono::system_clock::now().time_since_epoch().count());
  progress.levels.resize(m_cascade_opts_.level_array.size());
  for (std::size_t i = 0; i < m_cascade_opts_.level_array.size(); ++i) {
    auto& slot = progress.levels[i];
    slot.index = i;
    slot.name = std::string(
        m_cascade_opts_.level_array[i].name.value_or(as_label("level", i)));
  }

  std::size_t resume_idx = 0;
  std::string resume_inherited_cuts_file;  // → m_prev_cuts_file_
  const bool recovery_enabled =
      m_base_opts_.recovery_mode != RecoveryMode::none;
  if (recovery_enabled && progress_enabled
      && std::filesystem::exists(progress_path))
  {
    auto loaded = load_cascade_progress(progress_path);
    if (loaded.has_value()) {
      // Hydrate our in-memory progress with the persisted state, keyed by
      // index.  Levels added/removed since the previous run are simply
      // ignored: name+index drift is recovery's only safety net for now.
      for (const auto& lp : loaded->levels) {
        if (lp.index < progress.levels.size()
            && progress.levels[lp.index].name == lp.name)
        {
          auto& slot = progress.levels[lp.index];
          slot.status = lp.status;
          slot.converged = lp.converged;
          slot.iters = lp.iters;
          slot.global_iter_after = lp.global_iter_after;
          slot.cuts_file = lp.cuts_file;
        }
      }
      // First ACTIVE level that isn't `done`.  Inactive levels are
      // configured-out, never counted as a resume target — otherwise an
      // `[L0 done, L1 inactive, L2 done]` checkpoint would resume at L1,
      // fall through to the inactive-skip, and then re-solve L2 from
      // scratch.  If every active level is done, resume_idx == size() and
      // the loop below exits without doing any work.
      //
      // `last_done_idx` walks alongside: it's the most recent active+done
      // level whose cuts_file we should feed into the resume level's
      // transition.
      resume_idx = progress.levels.size();
      std::size_t last_done_idx = progress.levels.size();
      for (std::size_t i = 0; i < progress.levels.size(); ++i) {
        const auto& cfg = m_cascade_opts_.level_array[i];
        if (!cfg.active.value_or(true)) {
          continue;
        }
        if (progress.levels[i].status != CascadeLevelStatus::done) {
          resume_idx = i;
          break;
        }
        last_done_idx = i;
      }
      // Restore global_iter_index from the latest done level's checkpoint so
      // future cuts/iterations land past every iter the previous run
      // already consumed.
      if (last_done_idx < progress.levels.size()) {
        const auto& last_done = progress.levels[last_done_idx];
        if (last_done.global_iter_after >= 0) {
          global_iter_index = IterationIndex {last_done.global_iter_after};
        }
        if (!last_done.cuts_file.empty()
            && std::filesystem::exists(last_done.cuts_file))
        {
          resume_inherited_cuts_file = last_done.cuts_file;
        }
      }
      if (resume_idx >= progress.levels.size()) {
        SPDLOG_INFO(
            "Cascade --recover: all {} level(s) already complete — "
            "nothing to do",
            progress.levels.size());
      } else {
        SPDLOG_INFO(
            "Cascade --recover: resuming from level [{}] (index {}/{}), "
            "global_iter_index={}",
            progress.levels[resume_idx].name,
            resume_idx,
            progress.levels.size(),
            value_of(global_iter_index));
      }
    } else {
      SPDLOG_WARN(
          "Cascade --recover: progress file '{}' present but "
          "unreadable: {} — cold start",
          progress_path.string(),
          loaded.error().message);
    }
  } else if (recovery_enabled && progress_enabled) {
    SPDLOG_INFO("Cascade --recover: no checkpoint at '{}' — cold start",
                progress_path.string());
  }

  // Loop-scoped kappa carry-forward.  Captured BEFORE the per-level
  // ``current_solver.reset()`` at the end of each iteration, so the
  // next level can seed its solver's ``m_seed_max_kappa_`` with the
  // previous level's value — keeps the ``kappa=…`` clause in the iter
  // log alive across cascade levels even when CPLEX barrier without
  // crossover leaves the new level's LPs without a queryable basis.
  // Sentinel ``-1.0`` = "no observation yet" (first level).
  double carry_kappa = -1.0;

  // Carried last-N (UB, LB) pairs from the previous cascade level,
  // installed on each new ``SDDPMethod`` via :func:`seed_prior_bounds`.
  // Lets iter 1 of L1+ exercise a real ``stationary_window``-iter
  // lookback against the previous level's tail instead of the
  // no-lookback 1.0 sentinel (or a 1-iter degraded lookback when the
  // seed was a single point).  Oldest-first ordering: the last
  // element is the previous level's most recent iter.  Empty for L0
  // (no prior level).  See the setter docstring on ``SDDPMethod`` for
  // the convergence-safety pairing with ``min_iterations >= 2``.
  std::vector<SDDPMethod::PriorIterBounds> carry_bounds {};

  // Seed the per-level transition state from the recovered checkpoint so
  // the first resumed level (resume_idx) sees the same `m_prev_cuts_file_`
  // it would have seen in the original run.  No-op when resume_idx == 0
  // (cold start).
  if (!resume_inherited_cuts_file.empty()) {
    m_prev_cuts_file_ = std::move(resume_inherited_cuts_file);
  }

  // ── Nonanticipative scene merge: fail fast, before level 0 solves ──
  // Every level's effective SDDP options (incl. per-level aperture
  // overrides) and every cut transition are audited against the merged
  // scene of the caller's LP.  Levels that swap the system are re-checked
  // at their own start (their merge may differ).
  if (auto err = validate_nonanticipative_levels(planning_lp.simulation(),
                                                 remaining_budget))
  {
    return std::unexpected(std::move(*err));
  }
  bool prev_level_merged = false;

  for (std::size_t level_idx = 0;
       level_idx < m_cascade_opts_.level_array.size();
       ++level_idx)
  {
    const auto& level = m_cascade_opts_.level_array[level_idx];
    const auto level_name = level.name.value_or(as_label("level", level_idx));

    // ── 0a. Skip already-completed level on --recover ──
    // Cuts were persisted by the previous run.  We rely on the disk
    // artifacts for the transition into the resume level: cut inheritance
    // points at the most recent done level's cuts file via
    // `m_prev_cuts_file_`.
    if (level_idx < resume_idx) {
      SPDLOG_INFO(
          "Cascade [{}]: skipping (already complete in checkpoint, "
          "iters={}, converged={})",
          level_name,
          progress.levels[level_idx].iters,
          progress.levels[level_idx].converged ? "yes" : "no");
      continue;
    }

    // ── 0. Skip inactive level ──
    // `active = false` disables the level entirely: no LP build, no solve,
    // no state/cut production.  State and cut files left over from prior
    // active levels are preserved untouched so a later active level still
    // sees the latest upstream data.
    if (!level.active.value_or(true)) {
      SPDLOG_INFO("Cascade [{}]: inactive (active=false), skipping",
                  level_name);
      continue;
    }

    // ── 1. Build LP for each level ──
    // Intermediate/final levels always build a fresh LP to ensure clean
    // state (no leftover alpha variables from previous levels).  The
    // first level reuses the caller-supplied
    // PlanningLP when the caller's options already cover the level-0
    // effective model — the caller pre-merges cascade level-0 overrides in
    // build_solve_and_output(), so the initial LP is normally compatible.
    auto effective_model = prev_effective_model;
    if (level.model_options.has_value()) {
      effective_model.merge(*level.model_options);
    }
    prev_effective_model = effective_model;

    // A non-empty system_file always forces a fresh LP build for this
    // level — the network topology is being swapped wholesale, so the
    // caller's pre-built PlanningLP cannot be reused.
    const bool has_system_swap =
        level.system_file.has_value() && !level.system_file->empty();

    if (level_idx == 0 && !has_system_swap
        && planning_lp.planning().options.model_options.covers(effective_model))
    {
      current_lp = &planning_lp;
      current_solver.reset();
      // Adopt the caller LP's (already-warm) index cache so downstream
      // fresh-built levels reuse it instead of rebuilding it cold.
      if (!shared_index_cache) {
        shared_index_cache = planning_lp.simulation().arrow_index_cache_ptr();
      }
      SPDLOG_INFO("Cascade [{}]: reusing caller PlanningLP", level_name);
    } else {
      auto modified_planning = clone_planning_with_overrides(
          planning_lp.planning(), effective_model);

      if (has_system_swap) {
        const auto& input_dir =
            planning_lp.planning().options.input_directory.value_or(
                std::string {});
        modified_planning.system =
            load_system_from_file(*level.system_file, input_dir);
        SPDLOG_INFO(
            "Cascade [{}]: swapped system from '{}' "
            "({} buses, {} lines, {} generators, {} demands)",
            level_name,
            *level.system_file,
            modified_planning.system.bus_array.size(),
            modified_planning.system.line_array.size(),
            modified_planning.system.generator_array.size(),
            modified_planning.system.demand_array.size());
      }

      // State variable transfer uses structured keys — no LP names needed.
      // Pass the shared arrow-index cache so this level's per-cell builds
      // reuse the warm (Stage, Block) index instead of rebuilding it cold;
      // seed the cache from this level when it is the first to build.
      // Inherit the caller's full LP build options (solver, eps, LP names
      // for lp_file / lp_debug / lp_error, compute_stats, equilibration,
      // …).  An empty LpMatrixOptions{} here silently dropped them all on
      // every rebuilt level; equilibration left unset still resolves from
      // the level's own model_options (SystemLP::create_lp).
      const auto& level_flat_opts = planning_lp.flat_options();
      auto new_lp = std::make_unique<PlanningLP>(
          std::move(modified_planning), level_flat_opts, shared_index_cache);
      if (!shared_index_cache) {
        shared_index_cache = new_lp->simulation().arrow_index_cache_ptr();
      }
      current_lp = new_lp.get();
      m_owned_lps_.push_back(std::move(new_lp));
      current_solver.reset();

      SPDLOG_INFO("Cascade [{}]: built new PlanningLP", level_name);
    }

    // Intermediate level with its own output set → route every write
    // (sim-pass cells, write_out, cuts) into `<output>/<level name>/`.
    // The last active level always writes to the base directory.
    const bool write_this_level =
        write_levels && level_idx != last_active_level_idx;
    const auto level_output_dir = base_output_dir / level_name;
    if (write_levels) {
      current_lp->set_output_directory(write_this_level
                                           ? level_output_dir.string()
                                           : base_output_dir.string());
      if (write_this_level) {
        std::filesystem::create_directories(level_output_dir);
        SPDLOG_INFO("Cascade [{}]: level output -> {}",
                    level_name,
                    level_output_dir.string());
      }
    }

    // ── 3. Build SDDPOptions and create solver ──
    auto level_opts =
        build_level_sddp_opts(level.sddp_options, remaining_budget);

    // Backstop for levels whose (swapped) system merges differently from
    // the caller's: inherited cuts cannot cross a merged scene layout.
    {
      const auto& merge = current_lp->simulation().scene_merge();
      const bool inherits = level_idx > 0 && level.transition
          && level.transition->inherit_optimality_cuts.value_or(0) != 0;
      if (inherits && (merge.has_value() || prev_level_merged)) {
        return std::unexpected(Error {
            .code = ErrorCode::InvalidInput,
            .message = std::format(
                "cascade level '{}': transition.inherit_optimality_cuts "
                "crosses a nonanticipative scene merge (this level {}, "
                "previous level {}) — inherited cuts are stored per "
                "(class, variable, uid) without the scenario and cannot "
                "address a merged scene (not supported; set "
                "inherit_optimality_cuts = 0 or drop the flag)",
                level_name,
                merge.has_value() ? "merged" : "not merged",
                prev_level_merged ? "merged" : "not merged"),
        });
      }
      prev_level_merged = merge.has_value();
    }

    // Route each level's output to a level-specific subdirectory so
    // per-scene cut files and state files don't collide between levels.
    // Final level keeps the original path for backward compatibility.
    const bool is_last_level =
        level_idx == m_cascade_opts_.level_array.size() - 1;
    if (!m_base_opts_.cuts_output_file.empty()) {
      const auto base_dir =
          std::filesystem::path(m_base_opts_.cuts_output_file).parent_path();
      const auto base_name =
          std::filesystem::path(m_base_opts_.cuts_output_file).filename();
      if (is_last_level) {
        level_opts.cuts_output_file = m_base_opts_.cuts_output_file;
      } else if (write_this_level) {
        // Keep the level's cuts inside its own output directory.
        const auto level_cut_dir = level_output_dir / "cuts";
        std::filesystem::create_directories(level_cut_dir);
        level_opts.cuts_output_file = (level_cut_dir / base_name).string();
      } else {
        const auto level_dir = base_dir / level_name;
        std::filesystem::create_directories(level_dir);
        level_opts.cuts_output_file = (level_dir / base_name).string();
      }
    }

    // Intermediate levels save per-iteration so that their cuts and
    // state are available for cascade hot-start.  Final level uses
    // the base option.
    level_opts.save_per_iteration =
        is_last_level ? m_base_opts_.save_per_iteration : true;

    // Skip the post-training simulation pass at every non-final-active
    // level: the sim pass at an intermediate level produces NOTHING
    // the cascade subsequently consumes — optimality cuts come
    // from the training backward passes, and the per-cell write_out
    // output (the sim pass's only persistent side-effect) would be
    // overwritten by the next level's sim pass anyway (per-element
    // output paths are shared across levels).  The last active level
    // keeps `skip_simulation_pass = false` so its simulation runs end-
    // to-end and its outputs land on disk under the configured
    // `output_directory`.  Filed as issue #479 for the
    // accompanying feature flag that routes per-level output to
    // separate subdirectories — opting back in to the previous
    // behaviour will then require a config flip rather than a code
    // change here.
    level_opts.skip_simulation_pass =
        level_idx != last_active_level_idx && !write_this_level;

    // Seed the level's iteration counter past all iterations that prior
    // levels consumed.  Hot-start cuts loaded below (via load_cuts) may
    // raise this further through std::max in initialize_solver.
    level_opts.iteration_offset_hint = global_iter_index;

    // ── Resume: feed this level its own intra-level cuts checkpoint ──
    // When `--recover` lands on level_idx == resume_idx, the previous
    // run already persisted per-iteration cuts at
    // `level_opts.cuts_output_file`. Point the SDDP solver's `cuts_input_file`
    // at that path so its `initialize_solver` auto-loads the cuts and advances
    // the iteration counter past every iter the previous run consumed — the
    // saving continues seamlessly because `save_cuts_parquet` appends.
    //
    // We only override `cuts_input_file` when the user hasn't already
    // supplied one (rare for cascade) and the level's own checkpoint
    // exists on disk.
    if (recovery_enabled && level_idx == resume_idx
        && level_opts.cuts_input_file.empty()
        && !level_opts.cuts_output_file.empty()
        && std::filesystem::exists(level_opts.cuts_output_file))
    {
      level_opts.cuts_input_file = level_opts.cuts_output_file;
      SPDLOG_INFO("Cascade [{}]: hot-starting from intra-level checkpoint '{}'",
                  level_name,
                  level_opts.cuts_input_file);
    }

    // Record the level's cuts file path in the progress object now (so a
    // mid-solve crash leaves the next --recover with a working pointer
    // even before the level marks itself done).
    if (progress_enabled) {
      progress.levels[level_idx].cuts_file = level_opts.cuts_output_file;
      progress.levels[level_idx].status = CascadeLevelStatus::in_progress;
      if (auto save = save_cascade_progress(progress, progress_path);
          !save.has_value())
      {
        SPDLOG_WARN("Cascade: could not flush progress file '{}': {}",
                    progress_path.string(),
                    save.error().message);
      }
    }
    // Always create a fresh solver for each level, ensuring clean state.
    current_solver = std::make_unique<SDDPMethod>(*current_lp, level_opts);
    // ``carry_kappa`` deliberately does NOT seed the new solver's kappa
    // baseline: under CPLEX barrier without crossover the per-iter LP
    // solve never refreshes it, so every later level would display the
    // warm-up LP's stale value.  An empty kappa column is more honest.
    (void)carry_kappa;

    // Seed the new solver's Δgap lookback with the prior level's
    // last-N (UB, LB) so its iter 1 exercises a real windowed
    // lookback instead of the 1.0 sentinel.  See the
    // :func:`SDDPMethod::seed_prior_bounds` docstring for the
    // seed-array semantics and convergence-safety pairing with
    // min_iterations.
    if (!carry_bounds.empty()) {
      current_solver->seed_prior_bounds(carry_bounds);
    }

    // Aperture count to display:
    //   * explicit aperture list set     → its size
    //   * `num_apertures = N` set        → N (the per-phase cap)
    //   * neither set                    → -1 (use full per-phase list)
    const int aperture_log_count = level_opts.apertures.has_value()
        ? static_cast<int>(level_opts.apertures->size())
        : level_opts.num_apertures.value_or(-1);

    SPDLOG_INFO(
        "Cascade [{}]: new solver (max_iters={}, "
        "apertures={}, tol={})",
        level_name,
        level_opts.max_iterations,
        aperture_log_count,
        level_opts.convergence_tol);

    // ── 4. Load inherited cuts (after solver init, so alpha cols exist) ──
    // Cuts were pre-filtered and serialized at the end of the previous
    // level (step 5) so that the previous level's LP/solver could be
    // released before this level built its own LP.  Here we just load
    // the resulting file into the new solver.
    int inherited_cut_count = 0;
    if (level.transition && level_idx > 0 && !m_prev_cuts_file_.empty()
        && std::filesystem::exists(m_prev_cuts_file_))
    {
      // Ensure alpha variables are added before resolving cut column names
      if (auto init_err = current_solver->ensure_initialized();
          !init_err.has_value())
      {
        return std::unexpected(init_err.error());
      }
      // Route through the new solver so that state-variable resolution
      // (including the alpha column, which is registered as a state
      // variable by initialize_alpha_variables) runs against the new
      // level's sv_map.
      auto load_result = current_solver->load_cuts(m_prev_cuts_file_);
      if (load_result.has_value()) {
        inherited_cut_count = load_result->count;
        SPDLOG_INFO("Cascade [{}]: transferred {} cuts from {}",
                    level_name,
                    load_result->count,
                    m_prev_cuts_file_);
      } else {
        SPDLOG_WARN("Cascade [{}]: cut load failed: {}",
                    level_name,
                    load_result.error().message);
      }
      // Only delete the hand-off file when gtopt created it in its own
      // private temp directory.  On --recover the inherited file is the
      // previous level's persisted cuts output (a user-visible artifact
      // that may also be named explicitly in the options): keep it.
      if (is_owned_temp_file(m_prev_cuts_file_)) {
        std::error_code ec;
        std::filesystem::remove(m_prev_cuts_file_, ec);
      }
      m_prev_cuts_file_.clear();
    }

    // ── Cut forgetting: iteration-aware inherited cut lifecycle ──
    // inherit_optimality_cuts semantics:
    //   0 or absent: do not inherit
    //   -1:          inherit and keep forever
    //   N > 0:       inherit but forget after N training iterations,
    //                then re-solve with only self-generated cuts
    //
    // Only optimality cuts are inheritable across levels.  Feasibility
    // cuts are installed by the forward pass and tied to a level's own
    // trial values, so carrying them across levels is not meaningful.
    int forget_threshold = 0;
    if (level.transition && inherited_cut_count > 0) {
      const int opt_mode =
          level.transition->inherit_optimality_cuts.value_or(0);
      if (opt_mode > 0) {
        forget_threshold = opt_mode;
      }
    }

    // If forget_threshold > 0, cap phase-1 to that many iterations.
    // Only update max_iterations — do NOT reassign the full options,
    // as that would overwrite auto-computed fields (e.g. scale_alpha
    // auto-set during initialize_solver()).
    if (forget_threshold > 0) {
      current_solver->mutable_options().max_iterations =
          std::min(level_opts.max_iterations, forget_threshold);
    }

    const auto t_level = std::chrono::steady_clock::now();
    auto result = current_solver->solve(opts);

    if (!result.has_value()) {
      return std::unexpected(result.error());
    }
    // Backward-pass aperture statistics of this level (the tracker is
    // reset by every solve(), so a phase-2 re-solve is accumulated below).
    auto level_apertures = current_solver->aperture_drop_tracker().summary();

    // Phase-1 accumulators — folded into level_iterations / total_cuts
    // below so the per-level summary log reports the FULL level work
    // (phase-1 + phase-2), not just phase-2's tail.  Without this the
    // ``Cascade [X]: N iters, new_cuts=M`` line under-reports at every
    // level that ran a forget pass.
    int phase1_cuts_added = 0;
    int phase1_iter_count_for_stats = 0;

    // Forget inherited cuts if any inherit mode was positive (N > 0).
    // Phase-1 was already capped to N iterations; now we delete the
    // inherited cuts from the LP and re-solve with remaining ones.
    //
    // `result->size()` = N_train + (sim_pass ? 1 : 0).  Intermediate
    // cascade levels set `skip_simulation_pass = true` so the trailing
    // simulation entry is absent — without this guard the count was
    // off by one and the "forgetting X cuts after N iters" log read
    // ``after 0 iters`` whenever a level skipped its sim pass.
    const auto phase1_iters = level_opts.skip_simulation_pass
        ? static_cast<int>(result->size())
        : static_cast<int>(result->size()) - 1;
    const bool should_forget = inherited_cut_count > 0 && forget_threshold > 0;

    if (should_forget) {
      SPDLOG_INFO("Cascade [{}]: forgetting {} inherited cuts after {} iters",
                  level_name,
                  inherited_cut_count,
                  phase1_iters);

      // Delete inherited cuts from the LP rows and update row indices
      current_solver->forget_first_cuts(inherited_cut_count);

      // Deduct phase-1 iterations from budget
      if (remaining_budget >= 0) {
        remaining_budget = std::max(0, remaining_budget - phase1_iters);
      }

      // Keep phase-1 results.  Track its cut count + iter count so the
      // per-level summary log below reports the FULL level (phase-1 +
      // phase-2), not just phase-2 — otherwise ``new_cuts=N`` and
      // ``N iters`` under-report at every level that ran a forget pass.
      phase1_cuts_added =
          std::ranges::fold_left(*result,
                                 0,
                                 [](int sum, const SDDPIterationResult& r)
                                 { return sum + r.cuts_added; });
      phase1_iter_count_for_stats = phase1_iters;
      m_all_results_.insert(
          m_all_results_.end(), result->begin(), result->end());

      // Update solver options with remaining budget for phase-2.
      // Preserve auto-computed fields (scale_alpha, etc.) by updating
      // only the fields that need to change, not the full struct.
      auto phase2_opts =
          build_level_sddp_opts(level.sddp_options, remaining_budget);
      auto& solver_opts = current_solver->mutable_options();
      solver_opts.max_iterations = phase2_opts.max_iterations;
      solver_opts.save_per_iteration = level_opts.save_per_iteration;
      current_solver->clear_stop();

      // Bump the solver's iteration counter past phase-1's last
      // training iter so phase-2 doesn't restart at the phase-1 offset
      // and produce duplicate iter indices in the per-iter log.  The
      // SDDPMethod sets ``m_iteration_offset_`` once at construction
      // from ``iteration_offset_hint``; a plain re-entry into
      // ``solve()`` would otherwise reuse the same starting index.
      if (!result->empty()) {
        current_solver->bump_iteration_offset(
            next(result->back().iteration_index));
      }

      SPDLOG_INFO(
          "Cascade [{}]: re-solving without inherited cuts "
          "(max_iters={})",
          level_name,
          phase2_opts.max_iterations);

      result = current_solver->solve(opts);
      if (!result.has_value()) {
        return std::unexpected(result.error());
      }
      level_apertures.accumulate(
          current_solver->aperture_drop_tracker().summary());
    }

    const double level_elapsed = std::chrono::duration<double>(
                                     std::chrono::steady_clock::now() - t_level)
                                     .count();

    m_all_results_.insert(m_all_results_.end(), result->begin(), result->end());

    // The SDDP solver returns N training iterations + (optionally) 1
    // simulation pass entry — but ONLY the SYNCHRONOUS solve path
    // appends a sim-pass `SDDPIterationResult` (see
    // `sddp_iteration.cpp::iterate`'s `results.push_back(ir)` after
    // the sim pass).  The ASYNC path
    // (`SDDPMethod::solve_async`, dispatched when
    // `cut_sharing == none && max_async_spread > 0 && num_scenes > 1`)
    // runs the sim pass per-scene without pushing an aggregate
    // `SDDPIterationResult`.  Intermediate cascade levels skip the sim
    // pass entirely (`level_opts.skip_simulation_pass=true`), so
    // `result->size()` equals exactly N there.
    //
    // Decision matrix for whether to subtract 1 (= "sim entry
    // included in result vector"):
    //   skip_simulation_pass=true          → no sim entry → no -1
    //   skip_simulation_pass=false, sync   → sim entry    → -1
    //   skip_simulation_pass=false, async  → no sim entry → no -1
    //
    // Mirror the async-dispatch condition from
    // `sddp_iteration.cpp:290-292` exactly so the two stay in sync
    // (num_scenes>1 is dropped here: if num_scenes==1 the level
    // produced at most one training iter so the off-by-one matters
    // little either way, and querying scene_count from the
    // owned/released level LP would be brittle).  Without this
    // adjustment the async final level on the large hydrothermal case reports
    // `0 iters` even when training had been running for ~5 min
    // before convergence fired.
    //
    // `level_iterations` counts ONLY the iters that have not yet been
    // deducted from the global budget — phase-1's iters were already
    // subtracted inside the forget branch at line ~660, so we keep
    // this value at phase-2-only for the budget arithmetic below.
    // `iterations_reported` is the FULL level (phase-1 + phase-2) for
    // the per-level log + CascadeLevelStats so operators see the real
    // work done.
    const bool sim_entry_in_result = !level_opts.skip_simulation_pass
        && (level_opts.cut_sharing != CutSharingMode::none
            || level_opts.max_async_spread <= 0);
    const auto level_iterations = sim_entry_in_result
        ? static_cast<int>(result->size()) - 1  // exclude final fwd
        : static_cast<int>(result->size());
    const auto iterations_reported =
        phase1_iter_count_for_stats + level_iterations;

    // ── Collect per-level stats ──
    // When this level runs with max_iterations=0 *and*
    // skip_simulation_pass=true (i.e. every cascade level except the
    // last one, in a no-training cascade run) the SDDP solver returns
    // an empty results vector — no training iters, no sim pass slot.
    // Fall back to a default-constructed sentinel so the per-level
    // stats and log line stay well-defined and the global iteration
    // index does not advance (no iterations consumed any budget).
    const SDDPIterationResult empty_sentinel {};
    const auto& last = result->empty() ? empty_sentinel : result->back();
    const int phase2_cuts =
        std::ranges::fold_left(*result,
                               0,
                               [](int sum, const SDDPIterationResult& r)
                               { return sum + r.cuts_added; });
    const int total_cuts = phase1_cuts_added + phase2_cuts;

    // `gap0` = first training iteration's gap (initial level gap).
    // `Δgap` = last - first (signed; negative = gap closed).  When the
    // level produced no training iterations (size <= 1), gap0 and Δ are
    // suppressed to avoid log noise on degenerate cases.
    // `new_cuts` is the total Benders cuts *added this level* (summed
    // `bwd.total_cuts` across iterations), not the cumulative store.
    const bool have_gap0 = result->size() >= 2;
    const double gap0 = have_gap0 ? result->front().gap : last.gap;
    const double dgap = last.gap - gap0;

    m_level_stats_.push_back(CascadeLevelStats {
        .name = level_name,
        .iterations = iterations_reported,
        .lower_bound = last.lower_bound,
        .upper_bound = last.upper_bound,
        .gap = last.gap,
        .gap_initial = gap0,
        .gap_delta = dgap,
        .have_gap_delta = have_gap0,
        .converged = last.converged,
        .elapsed_s = level_elapsed,
        .cuts_added = total_cuts,
        .lp_options = current_lp->flat_options(),
        .apertures = level_apertures,
    });
    if (write_levels) {
      write_cascade_levels_csv(base_output_dir, m_level_stats_);
    }

    // Mark this level as `done` in the checkpoint.  The actual flush to
    // disk happens further below — AFTER cuts are persisted — so a
    // SIGKILL between the two writes leaves the checkpoint behind
    // the disk state (recovery redoes a few iters), never ahead of it.
    if (progress_enabled) {
      auto& pslot = progress.levels[level_idx];
      pslot.iters = iterations_reported;
      pslot.converged = last.converged;
      pslot.status = CascadeLevelStatus::done;
    }
    // Why the level stopped — scan for the iteration that converged (the
    // tail may be the simulation pass).
    const auto conv_it = std::ranges::find_if(
        *result, [](const SDDPIterationResult& r) { return r.converged; });
    const std::string stop_why = [&]() -> std::string
    {
      if (conv_it != result->end()) {
        if (conv_it->statistical_converged) {
          return "converged: statistical CI";
        }
        if (conv_it->stationary_converged) {
          return "converged: UB stationary, gap below "
                 "stationary_gap_ceiling";
        }
        return "converged: gap below convergence_tol";
      }
      // Issue #551: a criterion may have been met while the last
      // backward pass dropped infeasible apertures — say so, the level's
      // LB is then over a rescaled distribution.
      const int last_dropped = level_iterations > 0
          ? (*result)[static_cast<std::size_t>(level_iterations - 1)]
                .apertures_dropped
          : 0;
      const auto drop_note = last_dropped > 0
          ? std::format(
                "; {} aperture(s) infeasible in the last backward pass, "
                "the LB is over a rescaled distribution",
                last_dropped)
          : std::string {};
      if (remaining_budget >= 0 && level_iterations >= remaining_budget) {
        return std::format(
            "global iteration budget exhausted (not converged){}", drop_note);
      }
      return std::format("max_iterations={} reached (not converged){}",
                         level_opts.max_iterations,
                         drop_note);
    }();
    SPDLOG_INFO("Cascade [{}]: stopped — {}", level_name, stop_why);
    SPDLOG_INFO(
        "Cascade [{}]: {} iters, lb={}, ub={}, "
        "gap={:.4g}{}, new_cuts={}, {:.3f}s{}",
        level_name,
        iterations_reported,
        format_si(last.lower_bound),
        format_si(last.upper_bound),
        last.gap,
        have_gap0 ? std::format(" (gap0={:.4g}, Δ={:+.2e})", gap0, dgap)
                  : std::string {},
        total_cuts,
        level_elapsed,
        last.converged ? " (converged)" : "");

    // Update remaining global budget
    if (remaining_budget >= 0) {
      remaining_budget = std::max(0, remaining_budget - level_iterations);
      SPDLOG_INFO("Cascade: remaining global budget = {}", remaining_budget);
    }

    // Advance the global iteration index past every index this level
    // produced — `last.iteration_index` is the simulation-pass slot at
    // `next(last training iteration)`, so `next()` of that is strictly
    // past even the discarded sim index.  When the level produced no
    // results (max_iters=0 + skip_simulation_pass) keep the index where
    // it was — no iterations have been emitted on this level.
    if (!result->empty()) {
      global_iter_index = next(last.iteration_index);
    }
    if (progress_enabled) {
      progress.levels[level_idx].global_iter_after =
          static_cast<Index>(value_of(global_iter_index));
    }

    // ── 4. Check convergence ──
    if (last.converged) {
      if (level_idx == m_cascade_opts_.level_array.size() - 1) {
        // Flush progress to disk before breaking — the post-loop flush at
        // line ~1451 is skipped on this path, so without this an
        // `--recover` after a happy-path converged cascade would see the
        // final level still marked `in_progress` and re-solve it (the
        // upstream save happened at level *entry* — `:852` — but the
        // post-convergence `status = done` assignment at `:1165` only
        // lives in memory).  Captured by the
        // `test_cascade_recover.cpp` "final-level converges" assertion
        // — synthesized sidecar there documents the historical
        // pre-fix shape too.
        if (progress_enabled) {
          if (auto save = save_cascade_progress(progress, progress_path);
              !save.has_value())
          {
            SPDLOG_WARN(
                "Cascade: could not flush progress file '{}' "
                "after final-level convergence: {}",
                progress_path.string(),
                save.error().message);
          }
        }
        break;  // converged at final level
      }
      // Converged at intermediate level — continue to next level
    }

    // Check if global budget is exhausted
    if (remaining_budget == 0) {
      SPDLOG_INFO("Cascade: global iteration budget exhausted at level [{}]",
                  level_name);
      break;
    }

    // ── 5. Extract state for next level + explicit cleanup ──
    // Only prepare state/cut transfer and release LP cells when an
    // ACTIVE subsequent level exists.  If every remaining level is
    // inactive, skip this block entirely so the caller's PlanningLP
    // retains its systems for write_out() to emit solution parquets.
    // Without this guard, level 0 would release its cells in
    // anticipation of level 1, only to find levels 1..N all inactive —
    // leaving write_out with an empty system grid.
    const bool has_active_successor = std::ranges::any_of(
        std::span(m_cascade_opts_.level_array).subspan(level_idx + 1),
        [](const CascadeLevel& l) { return l.active.value_or(true); });
    if (level_idx + 1 < m_cascade_opts_.level_array.size()
        && has_active_successor)
    {
      // Pre-filter and serialize cuts to disk while the current LP is
      // still alive.  Doing this here (not at next-level start) lets us
      // drop the solver + owned LP before level N+1 allocates a new LP,
      // halving peak memory on the level boundary.
      m_prev_cuts_file_.clear();
      const auto& next_level = m_cascade_opts_.level_array[level_idx + 1];
      // [DIAG cut-transfer] Surface every decision point on the
      // intermediate→next cut serialization path.  Added 2026-05-13
      // after the plain-variant hydrothermal case showed `new_cuts=4000` at
      // level 0 but ZERO `serialized N cuts` log lines AND α=0 throughout level
      // 1 (forward primal-only objective) — i.e. cuts were stored and counted
      // but never made it into the parquet handed to transport.  Remove (or
      // fold to TRACE) after the root cause is fixed.
      SPDLOG_INFO(
          "[DIAG] Cascade [{}]: transition decision: "
          "next_level.transition.has_value={}",
          level_name,
          next_level.transition.has_value());
      if (next_level.transition) {
        // Cut activity at the cascade boundary: every Optimality cut is
        // inherited, with no dual-threshold filter.  The main cell's
        // `row_dual[cut.row]` is not a valid activity measure — cuts are
        // added after the aperture clones solve and the cell is not
        // re-solved, so that dual is stale or empty.  The correct measure
        // (probability-weighted aperture duals accumulated across
        // iterations) is not implemented.  Feasibility cuts are not
        // inherited; the next level's forward pass regenerates them.
        auto stored_cuts = current_solver->stored_cuts();
        const auto& trans = *next_level.transition;
        const int opt_cut_mode = trans.inherit_optimality_cuts.value_or(0);
        const bool want_opt_cuts = opt_cut_mode != 0;
        SPDLOG_INFO(
            "[DIAG] Cascade [{}]: opt_cut_mode={} want_opt_cuts={} "
            "stored_cuts.size()={} num_stored_cuts(direct)={}",
            level_name,
            opt_cut_mode,
            want_opt_cuts,
            stored_cuts.size(),
            current_solver->num_stored_cuts());

        // Only optimality cuts are inheritable across levels.  Feasibility
        // cuts are regenerated by the forward pass as needed.
        if (want_opt_cuts && !stored_cuts.empty()) {
          std::vector<StoredCut> filtered;
          filtered.reserve(stored_cuts.size());
          int n_opt = 0;
          int n_feas = 0;
          int n_other = 0;
          for (const auto& cut : stored_cuts) {
            if (cut.type == CutType::Optimality) {
              ++n_opt;
              filtered.push_back(cut);
            } else if (cut.type == CutType::Feasibility) {
              ++n_feas;
            } else {
              ++n_other;
            }
          }
          SPDLOG_INFO(
              "[DIAG] Cascade [{}]: stored cuts breakdown: "
              "n_opt={} n_feas={} n_other={} -> filtered={}",
              level_name,
              n_opt,
              n_feas,
              n_other,
              filtered.size());
          if (!filtered.empty()) {
            // Private (mkdtemp, 0700) directory under the output
            // directory (or $TMPDIR), and an exclusively created file in
            // it: never a predictable, shared /tmp name.
            if (m_tmp_dir_.empty()) {
              m_tmp_dir_ = make_private_temp_dir(
                  base_output_dir.empty()
                      ? std::filesystem::temp_directory_path()
                      : base_output_dir);
            }
            const auto cuts_tmp = m_tmp_dir_.empty() ? std::filesystem::path {}
                                                     : m_tmp_dir_
                    / std::format("cascade_cuts_{}.parquet", level_idx + 1);
            std::expected<void, Error> save_result = std::unexpected(Error {
                .code = ErrorCode::FileIOError,
                .message = std::format(
                    "cannot create a private temp file for the cuts "
                    "of level {}",
                    level_idx + 1),
            });
            if (!cuts_tmp.empty() && create_exclusive_file(cuts_tmp)) {
              save_result =
                  save_cuts_parquet(filtered, *current_lp, cuts_tmp.string());
            }
            if (save_result.has_value()) {
              m_prev_cuts_file_ = cuts_tmp.string();
              SPDLOG_INFO("Cascade [{}]: serialized {} cuts for next level",
                          level_name,
                          filtered.size());
            } else {
              SPDLOG_WARN("Cascade [{}]: cut save failed: {}",
                          level_name,
                          save_result.error().message);
            }
          }
        }
      }

      // ── Per-level output set (write_level_outputs) ──
      // After the cut serialization above (which still reads the level's
      // cells) and before the teardown below: flush the level's
      // solution (cells not already emitted by the sim pass,
      // solution.csv) into its own directory, then drop a copy of the
      // solver status and a level summary next to it.
      if (write_this_level) {
        const auto t_wo = std::chrono::steady_clock::now();
        try {
          current_lp->write_out();
        } catch (const std::exception& ex) {
          SPDLOG_WARN("Cascade [{}]: level write_out failed: {}",
                      level_name,
                      ex.what());
        }
        std::error_code ec;
        if (!m_base_opts_.api_status_file.empty()
            && std::filesystem::exists(m_base_opts_.api_status_file, ec))
        {
          std::filesystem::copy_file(
              m_base_opts_.api_status_file,
              level_output_dir / "solver_status.json",
              std::filesystem::copy_options::overwrite_existing,
              ec);
        }
        if (!m_level_stats_.empty()) {
          write_level_summary_json(level_output_dir,
                                   m_level_stats_.back(),
                                   level_idx,
                                   level.system_file.value_or(""));
        }
        if (current_lp == &planning_lp) {
          planning_lp.set_output_directory(base_output_dir.string());
        }
        SPDLOG_INFO("Cascade [{}]: level output written to {} ({:.1f}s)",
                    level_name,
                    level_output_dir.string(),
                    std::chrono::duration<double>(
                        std::chrono::steady_clock::now() - t_wo)
                        .count());
      }

      // ── EXPLICIT CLEANUP before next level allocates its LP ──
      // Drop the solver (frees aperture subproblems + scene_phase_states_)
      // and the owned LP (frees the per-cell LP matrices).  When the
      // caller's PlanningLP was reused at this level, also release its
      // per-(scene, phase) cells so the shell stays alive but the heavy
      // solver backends are returned to the allocator — otherwise the
      // caller's LP sits resident while the next level doubles memory.
      //
      // Snapshot the solver's max kappa BEFORE the reset so the next
      // level can seed its own kappa baseline.  Without this snapshot
      // the kappa-carry would read -1 (current_solver already null)
      // and the iter log would silently drop the ``kappa=…`` clause.

      // Force-flush the async logger BEFORE the level teardown.  The
      // overrun_oldest async policy (gtopt_main.cpp) drops oldest
      // messages when the bounded queue saturates — under sustained
      // backpressure this can silently swallow critical post-
      // convergence diagnostics (per-scene cancellation warnings,
      // final iter UB/LB lines) that we need on disk before the next
      // level's parallel LP build starts a fresh burst of log lines.
      // `flush()` blocks until every queued message has been written
      // through the sinks, so any line emitted up to this point in
      // the L_N solve is guaranteed to land in the log file before
      // L_{N+1} begins producing.
      // Time the whole teardown so the per-level boundary cost stays
      // visible in the iter log.  Historically the dominant component
      // was the serial destruction of the (scene × phase) LP grid
      // (~4-5s/level on plp/2_years); ``release_cells()`` now fans that
      // per-cell teardown across the solver work pool.
      const auto t_teardown_start = std::chrono::steady_clock::now();
      try {
        if (auto logger = spdlog::default_logger()) {
          logger->flush();
        }
      } catch (...) {
        // Best-effort: a flush failure must not prevent the cascade
        // transition.  The next level will still run; we just lose
        // the disk-flush guarantee for the prior level's tail logs.
      }

      const auto owned_before = m_owned_lps_.size();
      const bool released_caller = (current_lp == &planning_lp);
      if (current_solver) {
        const double k = current_solver->global_max_kappa();
        if (k >= 0.0) {
          carry_kappa = std::max(carry_kappa, k);
        }
      }
      // Snapshot the level's last-N (UB, LB) BEFORE resetting the
      // solver so the next level can seed its Δgap lookback against
      // a real window of prior iters (a single point is not enough
      // for ``stationary_window > 1``).  ``m_all_results_``
      // accumulates every iter result across the cascade; its tail
      // at this point is this level's last iter.  We take up to
      // ``SDDPMethod::STATIONARY_SEED_DEPTH`` of the most recent
      // entries, preserving cascade order (oldest-first).  When the
      // level produced no results (cancelled early before iter 1
      // ever finalised), leave carry_bounds unchanged so the next
      // level keeps whatever was carried through from earlier (or
      // stays empty at L0).
      if (!m_all_results_.empty()) {
        const std::size_t depth = SDDPMethod::STATIONARY_SEED_DEPTH;
        const std::size_t take = std::min(depth, m_all_results_.size());
        carry_bounds.clear();
        carry_bounds.reserve(take);
        for (std::size_t i = m_all_results_.size() - take;
             i < m_all_results_.size();
             ++i)
        {
          carry_bounds.push_back(SDDPMethod::PriorIterBounds {
              .upper_bound = m_all_results_[i].upper_bound,
              .lower_bound = m_all_results_[i].lower_bound,
          });
        }
      }
      current_solver.reset();
      current_lp = nullptr;
      // Release each owned LP's (scene × phase) cells via the parallel
      // ``release_cells()`` path BEFORE destroying the owning
      // unique_ptrs.  The default ``~PlanningLP`` destroys the grid
      // serially, which costs ~4.6s/level on plp/2_years; calling
      // ``release_cells()`` first fans the per-cell teardown across the
      // solver work pool, leaving only empty shells for ``clear()`` to
      // drop.
      for (auto& owned_lp : m_owned_lps_) {
        if (owned_lp) {
          owned_lp->release_cells();
        }
      }
      m_owned_lps_.clear();
      m_owned_lps_.shrink_to_fit();
      if (released_caller) {
        planning_lp.release_cells();
      }
      const auto teardown_ms =
          std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::steady_clock::now() - t_teardown_start)
              .count();
      SPDLOG_INFO(
          "Cascade [{}]: released solver, {} owned LP(s){} before level "
          "[{}] (teardown {}ms)",
          level_name,
          owned_before,
          released_caller ? " and caller LP cells" : "",
          m_cascade_opts_.level_array[level_idx + 1].name.value_or(
              as_label("level", level_idx + 1)),
          teardown_ms);

      // Second flush: ensure the "released solver" line + any other
      // post-teardown diagnostics make it to disk before the next
      // level's PlanningLP build starts emitting parallel-scene LP-
      // element log lines.  Cheap (queue is usually near-empty
      // immediately after the previous flush).
      try {
        if (auto logger = spdlog::default_logger()) {
          logger->flush();
        }
      } catch (...) {
        // Best-effort — same rationale as above.
      }
    }

    // Flush progress AFTER cuts have been written.  Order matters: a
    // SIGKILL between the cuts write and this rename leaves the
    // checkpoint trailing the disk state — recovery redoes a few iters,
    // never resumes past missing data.
    if (progress_enabled) {
      if (auto save = save_cascade_progress(progress, progress_path);
          !save.has_value())
      {
        SPDLOG_WARN("Cascade: could not flush progress file '{}': {}",
                    progress_path.string(),
                    save.error().message);
      }
    }
  }

  // The caller's final write_out (and planning.json) always lands in the
  // base output directory, even when the cascade stopped early at a
  // level that had been redirected to its own subdirectory.
  if (write_levels) {
    planning_lp.set_output_directory(base_output_dir.string());
    if (current_lp != nullptr) {
      current_lp->set_output_directory(base_output_dir.string());
    }
  }

  // ── Transfer the final level's LP to the caller for write_out ──
  // If the final active level built its own PlanningLP (i.e. the
  // caller's cells were released at a prior level→level cleanup, so
  // `planning_lp.systems()` is now empty), that LP lives in
  // `m_owned_lps_` and would be destroyed together with this
  // CascadePlanningMethod instance.  Without this transfer,
  // `gtopt_lp_runner` would call `planning_lp.write_out()` on an
  // empty system grid and emit only `planning.json` — solution.csv
  // stays header-only and element parquets are never written.
  //
  // Hand the owned LP over to the caller via the new output-delegate
  // channel so `write_out()` forwards to it, producing the full
  // per-(scene, phase) output.
  if (current_lp != nullptr && current_lp != &planning_lp) {
    auto it = std::ranges::find_if(m_owned_lps_,
                                   [current_lp](const auto& p)
                                   { return p.get() == current_lp; });
    if (it != m_owned_lps_.end()) {
      auto owned = std::move(*it);
      m_owned_lps_.erase(it);
      planning_lp.set_output_delegate(std::move(owned));
      SPDLOG_INFO(
          "Cascade: transferred final-level LP to caller as write_out "
          "delegate");
    }
  }

  const bool converged =
      !m_all_results_.empty() && m_all_results_.back().converged;

  // ── Print per-level summary ──
  SPDLOG_INFO("Cascade: ═══ Summary ═══");
  for (const auto& ls : m_level_stats_) {
    SPDLOG_INFO(
        "  [{}]: {} iters, lb={}, ub={}, gap={:.4g}{}, "
        "cuts={}, {:.3f}s{}",
        ls.name,
        ls.iterations,
        format_si(ls.lower_bound),
        format_si(ls.upper_bound),
        ls.gap,
        ls.have_gap_delta
            ? std::format(
                  " (gap0={:.4g}, Δ={:+.2e})", ls.gap_initial, ls.gap_delta)
            : std::string {},
        ls.cuts_added,
        ls.elapsed_s,
        ls.converged ? " (converged)" : "");
  }
  SPDLOG_INFO("Cascade: {} levels, {} total results (converged={})",
              m_level_stats_.size(),
              m_all_results_.size(),
              converged);

  // ── Backward-pass aperture statistics: per level + cascade total ──
  ApertureSummary total_apertures;
  for (const auto& ls : m_level_stats_) {
    if (ls.apertures.solves > 0) {
      SPDLOG_INFO(
          "  [{}] apertures: {} LP(s), {} infeasible, {} failed ({:.2f}% "
          "dropped), {} still dropped in the last iteration",
          ls.name,
          ls.apertures.solves,
          ls.apertures.infeasible,
          ls.apertures.failed,
          100.0 * ls.apertures.dropped_fraction(),
          ls.apertures.dropped_in_last_iteration);
    }
    total_apertures.accumulate(ls.apertures);
  }
  log_aperture_summary(total_apertures, "Cascade [total]");

  return converged ? 1 : 0;
}

}  // namespace gtopt
