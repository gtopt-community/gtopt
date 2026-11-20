/**
 * @file      future_cost_lp.cpp
 * @brief     LP build / output path for FutureCost (FCF / cost-to-go) elements
 * @date      Sun Jun 21 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `add_to_global_lp` is still inert plumbing (the α-column registration,
 * boundary-cut load and mean_shift rebase live in the SDDP method).
 *
 * `add_to_output` SELF-FINDS its α columns + per-scene rebase constant at
 * write time from the persistent `SimulationLP` registries reached through the
 * `OutputContext`'s `SystemContext`.  Those registries survive the per-cell LP
 * rebuild that `write_out` performs under `low_memory = compress`, so the
 * FutureCost/{alpha|alpha_<s>, rebase} streams are emitted under ALL
 * `low_memory` modes — no per-cell resident stash is needed.
 */

#include <cstddef>
#include <deque>
#include <filesystem>
#include <mutex>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <variant>
#include <vector>

#include <gtopt/decision_variable_lp.hpp>
#include <gtopt/future_cost_lp.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/phase_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/scene_lp.hpp>
#include <gtopt/sddp_types.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>

namespace gtopt
{

namespace
{

/// Interned storage for the multicut stream names ("alpha_<s>").
///
/// `OutputContext`'s `ClassFieldName` map key stores `string_view`s and
/// documents a static-storage contract (names must outlive the deferred
/// flush).  The single-α name (`AlphaName`) already satisfies it; the
/// per-source-scene multicut names are dynamic, so intern them once with
/// stable addresses (deque never relocates).  `write_out` runs one task
/// per scene in parallel, hence the mutex.
auto alpha_stream_name(std::size_t source_scene) -> std::string_view
{
  static std::mutex mutex;
  static std::deque<std::string> pool;
  const std::scoped_lock lock {mutex};
  while (pool.size() <= source_scene) {
    pool.emplace_back("alpha_" + std::to_string(pool.size()));
  }
  return pool[source_scene];
}

}  // namespace

bool FutureCostLP::add_to_global_lp(const SystemContext& /*sc*/,
                                    const SceneLP& /*scene*/,
                                    const PhaseLP& /*phase*/,
                                    LinearProblem& /*lp*/)
{
  // Inert: α-column registration + boundary-cut load + mean_shift rebase
  // live in the SDDP method (register_alpha_variables + load_boundary_cuts).
  return true;
}

bool FutureCostLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();

  // SELF-FIND: reach the persistent registries through the output context.
  // Both `sim` (state-variable map) and the scene/phase coordinates outlive
  // the per-cell LP rebuild that `write_out` performs under compress.
  const auto& sc = out.system_context();
  const auto& sim = sc.simulation();
  const auto& scene = sc.system().scene();
  const auto& phase = sc.system().phase();

  // α columns registered on this (scene, phase) cell, in source-scene order:
  // single layout → 1 entry; multicut → N varphi_s.  Empty when α was never
  // registered (e.g. last phase pinned, or no SDDP α at all) → nothing to do.
  std::vector<std::pair<ColIndex, Uid>> acols;
  if (future_cost().use_user_alpha.value_or(false)) {
    // User-overridable FCF (piece 5 step 2a): the built-in α is inert (not a
    // state var), so locate the user-authored α DecisionVariable (a `global`
    // `state`/`link` column under the dedicated `UserStateVar` class) by uid on
    // this cell and emit IT as `FutureCost/alpha`.  One column → single layout.
    if (const auto ua_uid = future_cost().user_alpha_uid; ua_uid.has_value()) {
      for (const auto& [key, svar] :
           sim.state_variables(scene.index(), phase.index()))
      {
        if (key.uid == *ua_uid
            && key.class_name == DecisionVariableLP::StateClassName)
        {
          acols.emplace_back(svar.col(), *ua_uid);
          break;
        }
      }
    }
  } else {
    acols = alpha_cols_on_cell(sim, scene.index(), phase.index());
  }
  if (acols.empty()) {
    return true;
  }

  const auto& stages = phase.stages();
  if (stages.empty()) {
    return true;
  }
  const auto& last_stage = stages.back();
  const auto& last_blocks = last_stage.blocks();
  if (last_blocks.empty()) {
    return true;
  }
  const auto last_block = last_blocks.back().uid();
  const bool multi = acols.size() > 1;

  // One α stream per registered column.  `add_col_sol` reads
  // `col_sol[α_col]` at the holder's terminal coords, so the α column's own
  // scene-phase context is irrelevant on the read side — we just place the
  // single terminal-block entry under every scenario of this scene.
  for (std::size_t s = 0; s < acols.size(); ++s) {
    // Static-storage names only: the OutputContext field map keys hold
    // string_views until the deferred flush (see alpha_stream_name).
    const std::string_view name = multi ? alpha_stream_name(s) : AlphaName;
    STBIndexHolder<ColIndex> cols;
    for (const auto& scenario : scene.scenarios()) {
      cols[std::tuple {scenario.uid(), last_stage.uid()}][last_block] =
          acols[s].first;
    }
    out.add_col_sol(cname, name, id(), cols);
  }

  // Per-scene α-rebase constant c̄ ($) — the mean_shift offset folded into the
  // objective; surfaced so `alpha + rebase` reconstructs the un-rebased FCF.
  const double c_bar = sim.alpha_offset(scene.index());
  if (c_bar != 0.0) {
    STBIndexHolder<double> rebase;
    for (const auto& scenario : scene.scenarios()) {
      rebase[std::tuple {scenario.uid(), last_stage.uid()}][last_block] = c_bar;
    }
    out.add_col_sol_values(cname, RebaseName, id(), rebase);
  }

  return true;
}

void apply_future_cost_config(const FutureCost& fc, SDDPOptions& opts)
{
  if (fc.cuts_file.has_value()) {
    opts.boundary_cuts_file = *fc.cuts_file;
  }
  if (fc.scale_alpha.has_value()) {
    opts.scale_alpha = *fc.scale_alpha;
  }
  if (fc.mean_shift.has_value()) {
    opts.boundary_cuts_mean_shift = *fc.mean_shift;
  }
  if (fc.mode.has_value()) {
    opts.boundary_cuts_mode = *fc.mode;
  }
  if (fc.sharing.has_value()) {
    opts.boundary_cut_sharing = *fc.sharing;
  } else if (fc.mode.has_value()) {
    // Legacy scope rule: an explicit sharing wins; otherwise `combined`
    // broadcasts (shared) and every other mode keeps per-scene α.
    opts.boundary_cut_sharing = (*fc.mode == BoundaryCutsMode::combined)
        ? BoundaryCutSharingMode::shared
        : BoundaryCutSharingMode::per_scene;
  }
  if (fc.iteration_limit.has_value()) {
    opts.boundary_max_iterations = *fc.iteration_limit;
  }
  if (fc.missing_cut_var_mode.has_value()) {
    opts.missing_cut_var_mode = *fc.missing_cut_var_mode;
  }
  if (fc.valuation.has_value()) {
    opts.boundary_cuts_valuation = *fc.valuation;
  }
}

std::string resolve_future_cost_path(const PlanningLP& planning_lp,
                                     const std::string& path)
{
  if (path.empty()) {
    return path;
  }
  const std::filesystem::path p {path};
  if (p.is_absolute()) {
    return path;
  }
  return (std::filesystem::path {planning_lp.options().input_directory()} / p)
      .string();
}

const FutureCost* active_future_cost(const PlanningLP& planning_lp)
{
  const auto& sim = planning_lp.simulation();
  if (sim.scene_count() <= 0 || sim.phases().empty()) {
    return nullptr;
  }
  // Read the FutureCost element straight from the System input data
  // (`future_cost_array`), NOT the LP collection: under the SDDP default
  // `low_memory = compress`, `clear_disposable_collections` drops the
  // planning-only FutureCostLP collection (it has no `update_lp`), leaving
  // `elements<FutureCostLP>()` empty even though the input array is intact.
  // The System is replicated across every cell, so (scene 0, phase 0) is
  // authoritative.
  const auto& sys = planning_lp.system(SceneIndex {0}, PhaseIndex {0}).system();
  for (const auto& fc : sys.future_cost_array) {
    // Active unless the `active` field is an explicit scalar `False`.  The
    // FutureCost element is global (no per-stage schedule in practice), so a
    // scalar is the only meaningful form; any vector/file schedule is treated
    // as active (presence == on).
    if (fc.active.has_value()) {
      if (const auto* scalar = std::get_if<IntBool>(&*fc.active);
          scalar != nullptr && *scalar == False)
      {
        continue;  // explicitly deactivated
      }
    }
    return &fc;
  }
  return nullptr;
}

bool has_active_use_user_alpha(const PlanningLP& planning_lp)
{
  const auto* fc = active_future_cost(planning_lp);
  return fc != nullptr && fc->use_user_alpha.value_or(false);
}

std::optional<Uid> active_user_alpha_uid(const PlanningLP& planning_lp) noexcept
{
  const auto* fc = active_future_cost(planning_lp);
  if (fc != nullptr && fc->use_user_alpha.value_or(false)
      && fc->user_alpha_uid.has_value())
  {
    // Return the optional directly (already engaged here) rather than
    // dereferencing + re-wrapping — same value, no redundant round-trip.
    return fc->user_alpha_uid;
  }
  return std::nullopt;
}

}  // namespace gtopt
