/**
 * @file      scene_merge.cpp
 * @brief     Merge the scenes spanned by a nonanticipative DecisionVariable
 * @date      Tue Nov 24 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <cstddef>
#include <format>
#include <iterator>
#include <span>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <gtopt/enum_option.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/scene_merge.hpp>
#include <gtopt/user_constraint_enums.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// The nonanticipative variables of the planning (system + UserModels),
/// validated: each one must be `scope: "global"`.
[[nodiscard]] std::vector<const DecisionVariable*> nonanticipative_variables(
    const System& system)
{
  std::vector<const DecisionVariable*> out;
  const auto collect = [&out](const auto& variables)
  {
    for (const auto& dv : variables) {
      if (dv.nonanticipative.value_or(false)) {
        out.push_back(&dv);
      }
    }
  };
  collect(system.decision_variable_array);
  for (const auto& um : system.user_model_array) {
    collect(um.variable_array);
  }

  for (const auto* dv : out) {
    const auto scope = dv->scope.has_value() && !dv->scope->empty()
        ? enum_from_name<ConstraintScope>(*dv->scope)
        : std::optional<ConstraintScope> {ConstraintScope::Block};
    if (scope != ConstraintScope::Global) {
      throw std::runtime_error(std::format(
          "decision_variable '{}': `nonanticipative: true` requires "
          "`scope: \"global\"` (got scope '{}') — only a single "
          "whole-horizon value can be shared across scenes",
          dv->name,
          dv->scope.value_or("block")));
    }
  }
  return out;
}

/// Scenario range [first, last) of a scene over `num_scenarios`.
[[nodiscard]] std::pair<std::size_t, std::size_t> scenario_range(
    const Scene& scene, std::size_t num_scenarios)
{
  const auto first = std::min(scene.first_scenario, num_scenarios);
  const auto count = scene.count_scenario == std::dynamic_extent
      ? num_scenarios - first
      : std::min(scene.count_scenario, num_scenarios - first);
  return {first, first + count};
}

}  // namespace

std::optional<SceneMerge> plan_nonanticipative_scene_merge(
    const Planning& planning)
{
  const auto variables = nonanticipative_variables(planning.system);
  if (variables.empty()) {
    return std::nullopt;
  }

  // A shared α would be one cost-to-go for every scene — wrong.
  for (const auto& fc : planning.system.future_cost_array) {
    if (!fc.use_user_alpha.value_or(false)) {
      continue;
    }
    const auto alpha_uid = fc.user_alpha_uid.value_or(unknown_uid);
    for (const auto* dv : variables) {
      if (dv->uid == alpha_uid) {
        throw std::runtime_error(std::format(
            "decision_variable '{}': `nonanticipative: true` on the user "
            "alpha of FutureCost '{}' — alpha is a per-scene cost-to-go and "
            "cannot be shared across scenes",
            dv->name,
            fc.name));
      }
    }
  }

  const auto& sim = planning.simulation;
  std::vector<Scene> scenes;
  std::ranges::copy_if(sim.scene_array,
                       std::back_inserter(scenes),
                       [](const Scene& s) { return s.is_active(); });
  if (scenes.size() <= 1) {
    // No scene_array (one implicit scene) or a single active scene: every
    // scenario already shares the scene-level column.
    return std::nullopt;
  }

  const auto& first_name = variables.front()->name;

  // Boundary cuts attach ONE α per scene LP to the FIRST scenario's state
  // columns; in a merged scene the other scenarios' future cost would be
  // dropped silently.
  const bool has_boundary_cuts = std::ranges::any_of(
      planning.system.future_cost_array,
      [](const auto& fc)
      { return fc.cuts_file.has_value() && !fc.cuts_file->empty(); });
  if (has_boundary_cuts
      || (sim.boundary_cuts_file.has_value()
          && !sim.boundary_cuts_file->empty()))
  {
    throw std::runtime_error(std::format(
        "decision_variable '{}': `nonanticipative: true` merges {} scenes "
        "into one LP, but the run has FutureCost boundary cuts — the cut "
        "alpha of a scene LP is tied to its first scenario's state, so the "
        "future cost of the other merged scenarios would be lost "
        "(not supported; drop the boundary cuts or the flag)",
        first_name,
        scenes.size()));
  }

  // The merged scene is one scenario range: the active scenes must tile a
  // contiguous block of `scenario_array` (scenarios are addressed by
  // position, so they cannot be reordered).
  const auto num_scenarios = sim.scenario_array.size();
  std::vector<std::pair<std::size_t, std::size_t>> ranges;
  ranges.reserve(scenes.size());
  for (const auto& s : scenes) {
    ranges.push_back(scenario_range(s, num_scenarios));
  }
  auto sorted = ranges;
  std::ranges::sort(sorted);
  for (std::size_t i = 1; i < sorted.size(); ++i) {
    if (sorted[i].first != sorted[i - 1].second) {
      throw std::runtime_error(std::format(
          "decision_variable '{}': `nonanticipative: true` merges the active "
          "scenes into one LP, but their scenarios are not one contiguous "
          "range of scenario_array (scenarios [{}, {}) and [{}, {})) — "
          "reorder the scenes/scenarios or deactivate the gap",
          first_name,
          sorted[i - 1].first,
          sorted[i - 1].second,
          sorted[i].first,
          sorted[i].second));
    }
  }

  SceneMerge merge;
  merge.merged_scene = Scene {
      .uid = scenes.front().uid,
      .name = scenes.front().name,
      .active = true,
      .first_scenario = sorted.front().first,
      .count_scenario = sorted.back().second - sorted.front().first,
  };
  map_reserve(merge.scene_of_scenario, merge.merged_scene.count_scenario);
  for (std::size_t si = 0; si < scenes.size(); ++si) {
    for (auto i = ranges[si].first; i < ranges[si].second; ++i) {
      merge.scene_of_scenario.insert_or_assign(sim.scenario_array[i].uid,
                                               scenes[si].uid);
    }
  }
  merge.original_scenes = std::move(scenes);
  for (const auto* dv : variables) {
    merge.variables.push_back(dv->name);
  }

  std::string vars;
  for (const auto& v : merge.variables) {
    vars += std::format("{}'{}'", vars.empty() ? "" : ", ", v);
  }
  std::string scene_list;
  for (const auto& s : merge.original_scenes) {
    scene_list += std::format(
        "{}{}{}",
        scene_list.empty() ? "" : ", ",
        s.uid,
        s.name.has_value() ? std::format(" '{}'", *s.name) : std::string {});
  }
  spdlog::info(
      "nonanticipative decision variable(s) {}: scenes [{}] merged into one "
      "LP scene (uid {}, {} scenarios) — one shared value across them",
      vars,
      scene_list,
      merge.merged_scene.uid,
      merge.merged_scene.count_scenario);

  return merge;
}

}  // namespace gtopt
