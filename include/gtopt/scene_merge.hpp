/**
 * @file      scene_merge.hpp
 * @brief     Merge the scenes spanned by a nonanticipative DecisionVariable
 * @date      Tue Nov 24 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * gtopt builds one LP per (scene, phase) cell, so a `scope: "global"`
 * DecisionVariable is one column per scene: shared by the scenarios of a
 * scene, but free to differ between scenes.  `nonanticipative: true` asks
 * for ONE value across scenes as well (a here-and-now decision taken
 * before the scene — e.g. the hydrology — is known).
 *
 * The implementation reuses the existing "one scene, several scenarios"
 * path: every active scene is folded into a single LP scene whose scenario
 * range is the union of the original ones.  Each scenario keeps its own
 * data, probability and state columns (reservoirs, …); only the scene-level
 * columns (coarse-scoped DecisionVariables, capacity expansion) become one
 * shared column — the extensive form of the two-stage problem.  The
 * planning's `scene_array` is NOT modified: `SimulationLP` applies the merge
 * when it builds its scenes, and the output writer splits the per-cell
 * shards back into the original scenes.
 */

#pragma once

#include <optional>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/fmap.hpp>
#include <gtopt/scene.hpp>

namespace gtopt
{

struct Planning;

/// The scenes folded into one LP scene for nonanticipative variables.
struct SceneMerge
{
  /// The single LP scene: first original scene's uid / name, scenario
  /// range = union of the merged scenes' ranges.
  Scene merged_scene {};

  /// The active scenes that were merged, in `scene_array` order.
  std::vector<Scene> original_scenes {};

  /// Scenario uid → uid of the original scene that owns it (used to split
  /// the output shards back into the original scenes).
  flat_map<Uid, Uid> scene_of_scenario {};

  /// Names of the nonanticipative variables that asked for the merge.
  std::vector<Name> variables {};
};

/**
 * @brief Decide whether (and how) the active scenes must be merged.
 *
 * Scans `system.decision_variable_array` and every
 * `user_model_array[*].variable_array` for `nonanticipative: true`.
 *
 * Throws `std::runtime_error` (named variable + reason) when:
 *  - a nonanticipative variable is not `scope: "global"`;
 *  - it is the user α of a FutureCost (`use_user_alpha`): α is a per-scene
 *    cost-to-go and cannot be shared;
 *  - the run has FutureCost boundary cuts: the FCF α of a scene LP is tied
 *    to the first scenario's state, so a merged scene would drop the
 *    future cost of every other scenario;
 *  - the active scenes do not cover one contiguous scenario range.
 *
 * @return `nullopt` when nothing must be merged (no nonanticipative
 *         variable, or at most one active scene); otherwise the merge.
 */
[[nodiscard]] std::optional<SceneMerge> plan_nonanticipative_scene_merge(
    const Planning& planning);

}  // namespace gtopt
