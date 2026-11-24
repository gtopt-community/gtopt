/**
 * @file      stage.hpp
 * @brief     Planning-period (stage) definition for power system optimization
 * @date      Wed Mar 26 12:11:10 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A Stage represents an investment period in the multi-stage planning
 * horizon.  Capacity decisions made in a stage persist into all subsequent
 * stages.  Stage-level discount factors allow modelling the time value of
 * money.
 *
 * ### JSON Example
 * ```json
 * {"uid": 1, "first_block": 0, "count_block": 24, "discount_factor": 0.9}
 * ```
 *
 * @see Block for the time blocks contained within a stage
 * @see Phase for higher-level groupings of stages
 * @see StageLP for the LP representation
 */

#pragma once

#include <span>  // For std::dynamic_extent

#include <gtopt/basic_types.hpp>
#include <gtopt/stage_enums.hpp>
#include <gtopt/uid.hpp>

namespace gtopt
{

/**
 * @struct Stage
 * @brief Investment period grouping consecutive time blocks
 *
 * Each stage:
 * - References `count_block` consecutive blocks starting at `first_block`
 * - Applies an optional `discount_factor` to all costs incurred in this
 *   period (present-value discounting)
 * - Can be individually deactivated for scenario analysis
 *
 * @see Block for the time blocks contained within a stage
 * @see StageLP for the linear programming representation
 */
struct Stage
{
  Uid uid {unknown_uid};  ///< Unique identifier
  OptName name {};  ///< Optional human-readable label
  OptBool active {};  ///< Activation status (default: active)

  Size first_block {
      0,
  };  ///< 0-based index of the first block in this stage [dimensionless]
  Size count_block {std::dynamic_extent};  ///< Number of consecutive blocks in
                                           ///< this stage [dimensionless]
  OptReal discount_factor {
      1};  ///< Present-value cost multiplier for this stage [p.u.]

  /// Calendar month for this stage.
  /// Used for seasonal parameter lookups (e.g., irrigation schedules).
  std::optional<MonthType> month {};

  /// Whether blocks in this stage are chronologically ordered.
  /// When true, unit commitment constraints (startup/shutdown transitions)
  /// are enforced.  When false or absent, commitment is silently skipped.
  OptBool chronological {};

  static constexpr std::string_view class_name = "stage";

  [[nodiscard]] constexpr auto is_active() const noexcept
  {
    return active.value_or(true);
  }
};

using StageUid = UidOf<Stage>;
using StageIndex = StrongPositionIndexType<Stage>;
using OptStageIndex = std::optional<StageIndex>;

/// @brief First stage index — the root of the planning horizon.
[[nodiscard]] constexpr auto first_stage_index() noexcept -> StageIndex
{
  return StageIndex {0};
}

/// @brief Next stage index (stage_index + 1), preserving strong type.
[[nodiscard]] constexpr auto next(StageIndex stage_index) noexcept -> StageIndex
{
  return ++stage_index;
}

/// @brief Previous stage index (stage_index - 1), preserving strong type.
[[nodiscard]] constexpr auto previous(StageIndex stage_index) noexcept
    -> StageIndex
{
  return --stage_index;
}

/**
 * @brief Position of a stage INSIDE its phase (0 = the phase's first
 *        active stage).
 *
 * Distinct from the global `StageIndex` (position in
 * `SimulationLP::stages()`): `PhaseLP::stages()` is addressed by
 * `PhaseStageIndex`, `SimulationLP::stages()` by `StageIndex`, and
 * neither container accepts the other kind.  Mixing them was the root of
 * the `SimulationLP::prev_stage`, capacity `prev_phase_publishes` and
 * per-stage schedule bugs: the two coincide only in the first phase.
 */
struct PhaseStageTag;
using PhaseStageIndex = StrongPositionIndexType<PhaseStageTag>;

/// @brief First stage of a phase.
[[nodiscard]] constexpr auto first_phase_stage_index() noexcept
    -> PhaseStageIndex
{
  return PhaseStageIndex {0};
}

/// @brief Next phase-local stage position.
[[nodiscard]] constexpr auto next(PhaseStageIndex index) noexcept
    -> PhaseStageIndex
{
  return ++index;
}

/// @brief Previous phase-local stage position.
[[nodiscard]] constexpr auto previous(PhaseStageIndex index) noexcept
    -> PhaseStageIndex
{
  return --index;
}

}  // namespace gtopt
