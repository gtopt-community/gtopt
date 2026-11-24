/**
 * @file      stage_lp.hpp
 * @brief     Header file defining the StageLP class for linear programming
 * stages
 * @date      Wed Mar 26 12:10:25 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module provides functionality for managing stages in linear programming
 * planning problems
 */

#pragma once

#include <span>

#include <gtopt/basic_types.hpp>
#include <gtopt/block.hpp>
#include <gtopt/block_lp.hpp>
#include <gtopt/phase.hpp>
#include <gtopt/stage.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

namespace detail
{
[[nodiscard]] constexpr auto create_block_array(
    std::span<const Block> block_array, const Stage& stage)
{
  return std::ranges::to<std::vector>(
      block_array.subspan(stage.first_block, stage.count_block)
      | std::ranges::views::transform([](const Block& b)
                                      { return BlockLP {b}; }));
}
}  // namespace detail

/**
 * @brief A class representing a stage in a linear programming planning
 * problem
 *
 * Encapsulates a collection of blocks with time duration, discounting, and
 * indexing
 */
class StageLP
{
public:
  /// Class name used in user-constraint expressions (`stage.month`, ...).
  /// Stage is not a registered LP element collection, but it is exposed as
  /// a singleton class in the AMPL parameter resolver so PAMPL constraints
  /// can read calendar metadata for the active stage.
  static constexpr std::string_view ClassName {"stage"};
  /// Calendar month attribute name (1=Jan..12=Dec when set).
  static constexpr std::string_view MonthName {"month"};
  /// Stage uid attribute name (numeric uid of the active stage).
  static constexpr std::string_view UidName {"uid"};
  /// Total stage duration in hours.
  static constexpr std::string_view DurationName {"duration"};

  StageLP() = default;

  /**
   * @brief Constructs a StageLP from a Stage and a collection of blocks
   *
   * @param stage               The stage definition
   * @param blocks              Collection of blocks
   * @param annual_discount_rate Annual discount rate for time value
   * calculations
   * @param stage_index         GLOBAL position (in `SimulationLP::stages()`)
   * @param phase_index         Index of the parent phase
   * @param phase_stage_index   Position inside the parent phase (unknown
   *                            for the flat global copies)
   */
  explicit StageLP(
      Stage stage,
      std::span<const Block> blocks = {},
      double annual_discount_rate = 0.0,
      StageIndex stage_index = StageIndex {unknown_index},
      PhaseIndex phase_index = PhaseIndex {unknown_index},
      PhaseStageIndex phase_stage_index = PhaseStageIndex {unknown_index})
      : m_stage_(std::move(stage))
      , m_blocks_(detail::create_block_array(blocks, m_stage_))
      , m_timeinit_(std::ranges::fold_left(
            blocks | std::ranges::views::take(m_stage_.first_block)
                | std::ranges::views::transform([](const Block& b)
                                                { return b.duration; }),
            0.0,
            std::plus()))
      , m_duration_(std::ranges::fold_left(
            m_blocks_ | std::ranges::views::transform(&BlockLP::duration),
            0.0,
            std::plus<>()))
      , m_discount_factor_(
            annual_discount_factor(annual_discount_rate, m_timeinit_))
      , m_index_(stage_index)
      , m_phase_index_(phase_index)
      , m_phase_stage_index_(phase_stage_index)
  {
  }

  [[nodiscard]] constexpr const auto& stage() const noexcept
  {
    return m_stage_;
  }

  [[nodiscard]] constexpr auto timeinit() const noexcept { return m_timeinit_; }

  /// @return Total duration of the stage in hours
  [[nodiscard]] constexpr auto duration() const noexcept { return m_duration_; }

  [[nodiscard]] constexpr auto total_duration() const noexcept
  {
    return std::ranges::fold_left(
        m_blocks_ | std::ranges::views::transform(&BlockLP::duration),
        0.0,
        std::plus<>());
  }

  /// @return Combined discount factor (annual and stage-specific)
  [[nodiscard]] static constexpr auto calculate_discount_factor(
      double annual_rate, double timeinit) noexcept
  {
    return annual_discount_factor(annual_rate, timeinit);
  }

  [[nodiscard]] constexpr auto discount_factor() const noexcept
  {
    return m_discount_factor_ * stage().discount_factor.value_or(1.0);
  }

  [[nodiscard]] constexpr auto annual_discount_rate() const noexcept
  {
    return m_discount_factor_;
  }

  /// @return Whether this stage is active in the planning
  [[nodiscard]] constexpr auto is_active() const noexcept
  {
    return stage().active.value_or(true);
  }

  /// @return Unique identifier for this stage
  [[nodiscard]] constexpr auto uid() const noexcept
  {
    return make_uid<Stage>(stage().uid);
  }

  /// @return GLOBAL position of this stage (in `SimulationLP::stages()`,
  /// active stages only).  There is deliberately no plain `index()`: a
  /// stage has two positions and the caller must say which one.
  [[nodiscard]] constexpr auto stage_index() const noexcept -> StageIndex
  {
    return m_index_;
  }

  /// @return Position of this stage inside its phase
  /// (`PhaseLP::stages()`); unknown for the flat global copies held by
  /// `SimulationLP::stages()`.
  [[nodiscard]] constexpr auto phase_stage_index() const noexcept
      -> PhaseStageIndex
  {
    return m_phase_stage_index_;
  }

  /// @return true for the first stage of the first phase (the horizon
  /// start, where the case `eini` IS the incoming state).  Only phase
  /// copies (`PhaseLP::stages()`) know this; the flat global copies carry
  /// no phase position and answer false, as the pre-typed check did.
  [[nodiscard]] constexpr bool is_horizon_first() const noexcept
  {
    return m_phase_stage_index_ == first_phase_stage_index()
        && m_phase_index_ == PhaseIndex {0};
  }

  /// @return true for the first stage of its phase.
  [[nodiscard]] constexpr bool is_phase_first() const noexcept
  {
    return m_phase_stage_index_ == first_phase_stage_index();
  }

  [[nodiscard]] constexpr auto phase_index() const noexcept
  {
    return m_phase_index_;
  }

  /// @return Calendar month for this stage (if set)
  [[nodiscard]] constexpr auto month() const noexcept { return stage().month; }

  /// @return Whether this stage has chronologically ordered blocks
  [[nodiscard]] constexpr bool is_chronological() const noexcept
  {
    return stage().chronological.value_or(false);
  }

  /// @return Span of blocks in this stage
  [[nodiscard]] constexpr const auto& blocks() const noexcept
  {
    return m_blocks_;
  }

private:
  Stage m_stage_;  ///< Stage definition
  std::vector<BlockLP> m_blocks_;  ///< View of blocks in this m_stage_
  double m_timeinit_ {0.0};
  double m_duration_ {0.0};  ///< Total duration of the m_stage_
  double m_discount_factor_ {1.0};

  StageIndex m_index_ {unknown_index};
  PhaseIndex m_phase_index_ {unknown_index};
  PhaseStageIndex m_phase_stage_index_ {unknown_index};
};

}  // namespace gtopt
