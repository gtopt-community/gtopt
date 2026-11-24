#pragma once

#include <gtopt/basic_types.hpp>
#include <gtopt/scenario.hpp>
#include <gtopt/scene.hpp>
#include <gtopt/stage_lp.hpp>

namespace gtopt
{

class ScenarioLP
{
public:
  ScenarioLP() = default;

  /// @param scenario_index        GLOBAL position (`SimulationLP::scenarios()`)
  /// @param scene_index           Parent scene
  /// @param scene_scenario_index  Position inside the parent scene (unknown
  ///                              for the flat global copies)
  explicit ScenarioLP(
      Scenario scenario,
      ScenarioIndex scenario_index = ScenarioIndex {unknown_index},
      SceneIndex scene_index = SceneIndex {unknown_index},
      SceneScenarioIndex scene_scenario_index =
          SceneScenarioIndex {unknown_index})
      : m_scenario_(std::move(scenario))
      , m_index_(scenario_index)
      , m_scene_index_(scene_index)
      , m_scene_scenario_index_(scene_scenario_index)
  {
  }

  [[nodiscard]] constexpr auto is_active() const noexcept
  {
    return m_scenario_.is_active();
  }

  /// First scenario of its scene (scene copies) or of the simulation
  /// (flat global copies, which carry no scene position).
  [[nodiscard]] constexpr auto is_first() const noexcept
  {
    if (m_scene_scenario_index_ != SceneScenarioIndex {unknown_index}) {
      return !m_scene_scenario_index_;
    }
    return !m_index_;
  }

  [[nodiscard]] constexpr auto uid() const noexcept
  {
    return make_uid<Scenario>(m_scenario_.uid);
  }

  [[nodiscard]] constexpr auto probability_factor() const noexcept
  {
    return m_scenario_.probability_factor.value_or(1.0);
  }

  /// GLOBAL position (`SimulationLP::scenarios()`).  No plain `index()`:
  /// a scenario has a global and a scene-local position.
  [[nodiscard]] constexpr auto scenario_index() const noexcept -> ScenarioIndex
  {
    return m_index_;
  }
  /// Position inside the parent scene (`SceneLP::scenarios()`).
  [[nodiscard]] constexpr auto scene_scenario_index() const noexcept
      -> SceneScenarioIndex
  {
    return m_scene_scenario_index_;
  }
  [[nodiscard]] constexpr auto scene_index() const noexcept
  {
    return m_scene_index_;
  }

private:
  Scenario m_scenario_;
  ScenarioIndex m_index_ {unknown_index};
  SceneIndex m_scene_index_ {unknown_index};
  SceneScenarioIndex m_scene_scenario_index_ {unknown_index};
};

}  // namespace gtopt
