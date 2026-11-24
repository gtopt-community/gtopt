/**
 * @file      scene_lp.hpp
 * @brief     Header for SceneLP class that represents a logical scene with LP
 * scenario elements
 * @date      Wed Mar 26 12:10:25 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module provides a logical view of a Scene combined with its associated
 * ScenarioLP elements, enabling efficient access and management of scene
 * components for linear programming planning.
 */

#pragma once

#include <span>

#include <gtopt/basic_types.hpp>
#include <gtopt/position_vector.hpp>
#include <gtopt/scenario.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/scene.hpp>
#include <gtopt/simulation.hpp>

namespace gtopt
{

namespace detail
{
[[nodiscard]] constexpr auto create_scenario_array(
    std::span<const Scenario> scenario_array,
    const Scene& scene,
    SceneIndex scene_index)
{
  auto scenarios =
      scenario_array.subspan(scene.first_scenario, scene.count_scenario);
  // Global position = active scenarios before this scene + local position
  // (`SimulationLP::scenarios()` holds the active scenarios only).
  const auto global_offset = static_cast<Index>(std::ranges::count_if(
      scenario_array.first(scene.first_scenario), &Scenario::is_active));

  return std::ranges::to<PositionVector<SceneScenarioIndex, ScenarioLP>>(
      enumerate_active<SceneScenarioIndex>(scenarios)
      | std::ranges::views::transform(
          [scene_index, global_offset](const auto& is)
          {
            auto&& [scene_scenario_index, scenario] = is;
            return ScenarioLP {
                scenario,
                ScenarioIndex {global_offset + Index {scene_scenario_index}},
                scene_index,
                scene_scenario_index,
            };
          }));
}

}  // namespace detail

/**
 * @class SceneLP
 * @brief Represents a logical scene with linear programming scenario elements
 *
 * This class combines a Scene with its associated ScenarioLP elements,
 * providing a unified interface for scene management in linear programming
 * contexts.
 */
class SceneLP
{
public:
  SceneLP() = default;

  /**
   * @brief Construct a SceneLP from a Scene and a collection of Scenario
   * elements
   *
   * @param scene       The Scene object
   * @param scenarios   Collection of Scenario elements
   * @param scene_index Index of this scene in the parent container
   *
   * Initializes the SceneLP with the given Scene and extracts the relevant
   * ScenarioLP elements based on the Scene's first_scenario and count_scenario.
   */
  explicit SceneLP(Scene scene,
                   std::span<const Scenario> scenarios,
                   SceneIndex scene_index = SceneIndex {unknown_index})
      : m_scene_(std::move(scene))
      , m_scenarios_(
            detail::create_scenario_array(scenarios, m_scene_, scene_index))
      , m_index_(scene_index)
  {
  }

  explicit SceneLP(Scene scene,
                   const Simulation& simulation,
                   SceneIndex scene_index = SceneIndex {unknown_index})
      : SceneLP(std::move(scene), simulation.scenario_array, scene_index)
  {
  }

  /**
   * @brief Check if the scene is active
   * @return true if the scene is active, false otherwise
   */
  [[nodiscard]] constexpr auto is_active() const noexcept
  {
    return m_scene_.active.value_or(true);
  }

  /**
   * @brief Get the unique identifier of the scene
   * @return The scene's unique identifier
   */
  [[nodiscard]] constexpr auto uid() const noexcept
  {
    return make_uid<Scene>(m_scene_.uid);
  }

  /**
   * @brief Get all scenario elements associated with this scene
   * @return Span of ScenarioLP elements
   */
  [[nodiscard]] constexpr auto scenarios() const
      -> const PositionVector<SceneScenarioIndex, ScenarioLP>&
  {
    return m_scenarios_;
  }

  [[nodiscard]] constexpr auto first_scenario() const noexcept
  {
    return ScenarioIndex {static_cast<Index>(m_scene_.first_scenario)};
  }

  [[nodiscard]] constexpr auto count_scenario() const noexcept
  {
    return static_cast<Index>(m_scene_.count_scenario);
  }

  /// @return Index of this scene in parent container
  [[nodiscard]] constexpr auto index() const noexcept { return m_index_; }

  /// @return Accumulated probability factor across all scenarios in this scene.
  ///         Each scenario contributes its own probability_factor
  ///         (default 1.0).
  [[nodiscard]] constexpr auto probability_factor() const noexcept
  {
    double total = 0.0;
    for (const auto& sc : m_scenarios_) {
      total += sc.probability_factor();
    }
    return total;
  }

private:
  Scene m_scene_;  ///< The underlying scene
  /// Scenarios of this scene, addressed by scene-LOCAL position.
  PositionVector<SceneScenarioIndex, ScenarioLP> m_scenarios_;
  SceneIndex m_index_ {unknown_index};
};

}  // namespace gtopt
