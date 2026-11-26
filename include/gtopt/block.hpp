/**
 * @file      block.hpp
 * @brief     Time block definition for power system optimization
 * @date      Wed Mar 26 12:12:08 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A Block is the smallest indivisible time unit in the planning horizon.
 * Energy quantities are obtained by multiplying power by duration:
 * `energy [MWh] = power [MW] × duration [h]`.
 *
 * ### JSON Example
 * ```json
 * {"uid": 1, "duration": 1.0}
 * ```
 */

#pragma once

#include <iterator>

#include <gtopt/basic_types.hpp>
#include <gtopt/uid.hpp>

namespace gtopt
{

/**
 * @struct Block
 * @brief Smallest time unit in the optimization horizon
 *
 * Blocks are ordered within a @ref Stage.  A stage references its first
 * block by index (`first_block`) and contains `count_block` consecutive
 * blocks.  The `duration` field converts MW dispatch values into MWh energy
 * quantities in the objective function.
 *
 * @see Stage for the grouping of blocks into planning periods
 */
struct Block
{
  Uid uid {unknown_uid};  ///< Unique identifier
  OptName name {};  ///< Optional human-readable label

  Real duration {0.0};  ///< Duration of this time block [h]

  /// Node time of the block inside its stage's chronological period [h]
  /// (real hours from the period start, see `Stage::period_hours`).  Used
  /// by derivative-based constraints (`ramp_formula = central2`) on
  /// non-uniform grids, e.g. Chebyshev nodes whose block durations are
  /// quadrature weights.  Unset = midpoint of the block in real time.
  OptReal time {};

  /// Commitment cell of the block (any integer label).  When the blocks
  /// of a stage carry it, consecutive blocks with the same label share
  /// one commitment status u (and v / w only at cell boundaries): the
  /// binary grid is the base grid of a refined sampler (e.g. the hourly
  /// cells or the N Chebyshev nodes) while the refined blocks / nodes
  /// only add continuous variables.  Block stages only, for units
  /// without their own `commitment_period` (which wins); point stages
  /// always use the uniform per-unit grid (never the nodes).  Unset on
  /// every block = the usual per-block / period grid.
  OptInt commitment_cell {};

  static constexpr std::string_view class_name = "block";
};

using BlockUid = UidOf<Block>;
using BlockIndex = StrongPositionIndexType<Block>;

/// @brief First block index.
[[nodiscard]] constexpr auto first_block_index() noexcept -> BlockIndex
{
  return BlockIndex {0};
}

}  // namespace gtopt

namespace std
{

template<>
struct incrementable_traits<gtopt::BlockIndex>
{
  using difference_type = int;
};

}  // namespace std
