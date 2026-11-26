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

  /// Storage cycle group (weighted representative-day cycle).
  ///
  /// Consecutive stages of the same phase that share a `cycle_group` id
  /// form ONE storage cycle for every `daily_cycle` storage: instead of
  /// closing each stage with `efin == eini`, the stages are chained as
  /// `cycle_weight` repetitions each (in stage order) and the group is
  /// closed with `Σ_d w_d · (efin_d − eini_d) = 0`.  Typical use: a
  /// weekday and a weekend representative day of one month, with weights
  /// 5 and 2 (days per typical week), so a battery may charge on the
  /// weekend and discharge on weekdays.  Unset (default) = no group, the
  /// per-stage daily closure is unchanged.  A group must not cross a
  /// phase boundary.
  OptInt cycle_group {};

  /// Repetitions of this stage inside its `cycle_group` (e.g. days per
  /// typical week).  Must be > 0.  Unset = `duration / 24` (the number of
  /// days the stage represents; `duration / period_hours`, the periods
  /// represented, when `period_hours` is set).  Ignored without
  /// `cycle_group`.
  OptReal cycle_weight {};

  /// Real length [h] of the chronological period sampled by the blocks of
  /// this stage (e.g. 168 for a typical week, 24 for a representative
  /// day).  Block durations are then WEIGHTED durations (energy and cost
  /// weights; they sum to the stage duration = period × repetitions) and
  /// the real time step of block b is
  /// `Δt_b = duration_b · period_hours / stage_duration`.  Real time steps
  /// drive the `daily_cycle` storage balance (cycle closed over the
  /// period), ramp limits, minimum up / down times and the
  /// `max_cycles_day` cap (period_hours / 24 days per period).
  /// Unset (default) = real time steps are the block durations, except
  /// for the legacy `daily_cycle` 24 h rescaling.
  OptReal period_hours {};

  /// Meaning of the block values of this stage:
  ///  - `"block"` (default): a block value holds CONSTANT over the block
  ///    (piecewise constant, length = real time step).
  ///  - `"point"`: block values are POINT samples at the node times
  ///    `Block.time` (required on every block), e.g. Chebyshev–Lobatto
  ///    nodes with `t_0` = period start and `t_N` = period end; durations
  ///    are quadrature weights, not intervals.  Storage balances then
  ///    integrate between nodes (`storage_integration`, default
  ///    `lg4_pos` for reservoirs and `block` for other storages), and
  ///    ramps, minimum up / down times, commitment
  ///    periods and daily cycle windows use the node spacing
  ///    `t_{k+1} − t_k`.
  OptName sampling {};

  /// Circular typical period: the blocks of this stage sample ONE period
  /// (typical day / week) that repeats `duration / period_hours` times.
  /// Every intra-period coupling wraps around the period (block N−1 →
  /// block 0: commitment transitions and windows, ramps, line
  /// commitment, `<attr>_prev` user-constraint terms, the start level of
  /// `daily_cycle` storages); only the inter-stage state variables
  /// (storages carried to the next stage / phase) are not periodic —
  /// they move by `n · Δ_cycle` across the stage.
  /// Unset (default): periodic when `period_hours` is set or
  /// `sampling = "point"`.
  OptBool periodic {};

  static constexpr std::string_view class_name = "stage";

  [[nodiscard]] constexpr auto is_active() const noexcept
  {
    return active.value_or(true);
  }

  /// True when the stage is a circular typical period (see `periodic`).
  [[nodiscard]] bool is_periodic() const noexcept
  {
    return periodic.value_or(period_hours.has_value()
                             || sampling.value_or("block") == "point");
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
