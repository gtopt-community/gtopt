/**
 * @file      scenario.hpp
 * @brief     Stochastic scenario definition for power system planning
 * @date      Wed Mar 26 12:12:32 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A Scenario represents one possible realization of uncertain inputs (e.g.,
 * hydrology, demand level, wind output).  Scenarios are weighted by
 * `probability_factor` when computing the expected-cost objective.
 *
 * ### JSON Example
 * ```json
 * {"uid": 1, "name": "dry_year", "probability_factor": 0.4}
 * ```
 *
 * @see ScenarioLP for the LP representation
 */

#pragma once

#include <gtopt/basic_types.hpp>
#include <gtopt/uid.hpp>

namespace gtopt
{

/**
 * @struct Scenario
 * @brief One possible future realization in stochastic planning
 *
 * When `probability_factor` values across all scenarios do not sum to 1, the
 * solver normalises them internally.  A scenario can be deactivated (e.g. to
 * perform deterministic runs with a single scenario).
 */
struct Scenario
{
  Uid uid {unknown_uid};  ///< Unique identifier
  OptName name {};  ///< Optional human-readable label
  OptBool active {};  ///< Activation status (default: active)

  OptReal probability_factor {1};  ///< Probability weight of this scenario
                                   ///< [p.u.]; values are normalised to sum 1

  /// @brief Optional source-hydrology index (metadata for PLP provenance).
  ///
  /// Populated by plp2gtopt as the 0-based index into PLP's hydrology
  /// classes.  Not consumed by the LP solver — kept so post-processing
  /// tools can trace each scenario back to its original PLP hydrology.
  OptInt hydrology {};

  /// @brief Optional ranking index of the scenario for an exogenous risk
  /// tail (`model_options.risk_rank_by = "index"`), e.g. a hydrology
  /// index (inflow energy, exceedance probability) or an expected
  /// unserved energy.  `risk_tail` picks the side (`upper` = highest
  /// values).  Not used by the default expected-cost LP.
  OptReal risk_index {};

  static constexpr std::string_view class_name = "scenario";

  [[nodiscard]] constexpr auto is_active() const noexcept
  {
    return active.value_or(true);
  }
};

using ScenarioUid = UidOf<Scenario>;
using ScenarioIndex = StrongPositionIndexType<struct Scenario>;

/// @brief First scenario index.
[[nodiscard]] constexpr auto first_scenario_index() noexcept -> ScenarioIndex
{
  return ScenarioIndex {0};
}

/**
 * @brief Position of a scenario INSIDE its scene (0 = the scene's first
 *        active scenario).  Distinct from the global `ScenarioIndex`
 *        (position in `SimulationLP::scenarios()`); `SceneLP::scenarios()`
 *        is addressed by this type.
 */
struct SceneScenarioTag;
using SceneScenarioIndex = StrongPositionIndexType<SceneScenarioTag>;

/// @brief First scenario of a scene.
[[nodiscard]] constexpr auto first_scene_scenario_index() noexcept
    -> SceneScenarioIndex
{
  return SceneScenarioIndex {0};
}

}  // namespace gtopt
