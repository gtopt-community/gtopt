/**
 * @file      cell_partition.hpp
 * @brief     Cell partition of a stage: periods / days as unions of cells
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Every stage is a partition of its period into cells [τ_{k−1}, τ_k] with
 * τ_k = Σ_{j≤k} w_j and w_k the block's real duration.  On block stages
 * the cells are the blocks; on point stages (`Stage.sampling = "point"`)
 * w_k is the quadrature weight of node t_k (Clenshaw–Curtis,
 * trapezoid/Voronoi, Gauss, user supplied) and validation requires
 * t_k ∈ [τ_{k−1}, τ_k].
 *
 * Duration logic (commitment periods, min up/down windows,
 * `max_cycles_day` days) groups whole cells: cell k belongs to group
 * ⌊mid_k / P⌋, mid_k = (τ_{k−1} + τ_k)/2.  Equivalently a nominal
 * boundary m·P falling inside a cell snaps to the nearer cell end (a
 * boundary exactly at the cell midpoint puts the cell in the later
 * group).  Group durations are sums of cell lengths, consistent with the
 * energy / cost quadrature; the snapping error |τ_boundary − m·P| is at
 * most half the cell that contained m·P.
 *
 * Backward compatibility: block stages with uniform durations keep the
 * legacy tumbling accumulator (a group closes once Σw ≥ P), which equals
 * the midpoint rule whenever the duration divides P.
 */

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <span>
#include <vector>

namespace gtopt
{

/// Group index (consecutive, from 0) of every cell for a nominal group
/// length @p period [h]: cell k → ⌊mid_k / P⌋, renumbered without gaps
/// (a long cell spanning several nominal groups leaves no empty group).
/// All zeros when P ≤ 0.
[[nodiscard]] inline std::vector<std::size_t> cell_groups(
    std::span<const double> cells, double period)
{
  std::vector<std::size_t> grp(cells.size(), 0);
  if (period <= 0.0) {
    return grp;
  }
  double tau = 0.0;
  long prev = 0;
  std::size_t g = 0;
  for (std::size_t k = 0; k < cells.size(); ++k) {
    const double mid = tau + (0.5 * cells[k]);
    tau += cells[k];
    const auto raw = static_cast<long>(std::floor((mid / period) + 1e-12));
    if (k > 0 && raw != prev) {
      ++g;
    }
    prev = raw;
    grp[k] = g;
  }
  return grp;
}

/// Legacy tumbling grouping: a group closes once its accumulated
/// duration reaches P (non-overlapping, chronological).
[[nodiscard]] inline std::vector<std::size_t> accumulated_groups(
    std::span<const double> cells, double period)
{
  std::vector<std::size_t> grp(cells.size(), 0);
  if (period <= 0.0) {
    return grp;
  }
  double accum = 0.0;
  std::size_t g = 0;
  for (std::size_t k = 0; k < cells.size(); ++k) {
    grp[k] = g;
    accum += cells[k];
    if (accum >= period - 1e-9) {
      ++g;
      accum = 0.0;
    }
  }
  return grp;
}

/// Grouping used by the duration logic of a stage.  Block stages with
/// uniform durations keep the legacy accumulated grouping (identical
/// LPs for existing cases; equal to `cell_groups` whenever the duration
/// divides P); point stages and variable-duration block stages use the
/// midpoint-snapped cell groups.
[[nodiscard]] inline std::vector<std::size_t> duration_groups(
    std::span<const double> cells, double period, bool point)
{
  bool uniform = true;
  for (const double c : cells) {
    if (std::abs(c - cells.front()) > 1e-9 * std::max(1.0, cells.front())) {
      uniform = false;
      break;
    }
  }
  return (!point && uniform) ? accumulated_groups(cells, period)
                             : cell_groups(cells, period);
}

/// Cell indices j after which a new group starts (the group ends at τ_j).
[[nodiscard]] inline std::vector<std::size_t> snap_period_boundaries(
    std::span<const double> cells, double period, bool point = true)
{
  const auto grp = duration_groups(cells, period, point);
  std::vector<std::size_t> out;
  for (std::size_t k = 1; k < grp.size(); ++k) {
    if (grp[k] != grp[k - 1]) {
      out.push_back(k - 1);
    }
  }
  return out;
}

/// @name Daily commitment grid (`Commitment.commitment_breaks`)
/// @{

/// Clock hour of a time of day @p hours, reduced to [0, 24).
[[nodiscard]] inline double clock_of_day(double hours) noexcept
{
  const double r = std::fmod(hours, 24.0);
  return r < 0.0 ? r + 24.0 : r;
}

/// Cell boundaries in STAGE time of the daily grid @p breaks (hours of
/// the day, [0, 24), strictly increasing) over a stage of length
/// @p period that starts at clock time @p start_hour: every stage time
/// τ in (0, period) with clock_of_day(start_hour + τ) ∈ breaks, sorted.
/// A boundary within 1e-9 h of the stage start or end is dropped (the
/// stage ends there anyway).  The cells of the stage are
/// [0, τ_1), [τ_1, τ_2), ..., [τ_K, period].
[[nodiscard]] inline std::vector<double> daily_cell_bounds(
    std::span<const double> breaks, double start_hour, double period)
{
  constexpr double tol = 1e-9;
  std::vector<double> out;
  if (breaks.empty() || !(period > 0.0)) {
    return out;
  }
  const double s = clock_of_day(start_hour);
  for (double day = 0.0; day - 24.0 < period; day += 24.0) {
    for (const double b : breaks) {
      const double t = day + b - s;
      if (t <= tol) {
        continue;
      }
      if (t >= period - tol) {
        return out;
      }
      out.push_back(t);
    }
  }
  return out;
}

/// Cell of stage time @p t on the boundaries @p bounds (see
/// `daily_cell_bounds`): half-open cells [τ_c, τ_{c+1}), so a time
/// exactly on a boundary (within @p tol) belongs to the LATER cell;
/// times past the last boundary (incl. the period end) join the last
/// cell.  Cell 0 starts at the stage start.
[[nodiscard]] inline std::size_t cell_of_time(std::span<const double> bounds,
                                              double t,
                                              double tol = 1e-9) noexcept
{
  return static_cast<std::size_t>(std::ranges::upper_bound(bounds, t + tol)
                                  - bounds.begin());
}

/// True when the first and the last cell of a stage are the two pieces
/// of ONE daily cell: the stage is a whole number of days long and does
/// not start on a break, so on a cyclic (periodic) stage the cell
/// straddling the period start wraps around it.  False when the stage
/// holds a single cell (no boundary).
[[nodiscard]] inline bool daily_cells_wrap(std::span<const double> breaks,
                                           double start_hour,
                                           double period) noexcept
{
  constexpr double tol = 1e-9;
  if (breaks.empty() || !(period > 0.0)) {
    return false;
  }
  const double days = period / 24.0;
  if (days < 1.0 - tol || std::abs(days - std::round(days)) > tol) {
    return false;
  }
  const double s = clock_of_day(start_hour);
  const bool on_break =
      std::ranges::any_of(breaks,
                          [s](double b)
                          {
                            const double d = std::abs(b - s);
                            return d <= tol || std::abs(d - 24.0) <= tol;
                          });
  return !on_break && !daily_cell_bounds(breaks, start_hour, period).empty();
}

/// @}

}  // namespace gtopt
