// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      sddp_forward_crossover.hpp
 * @brief     Forward-pass crossover policy and the interior-dual cut guard
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The SDDP forward pass only needs a VERTEX solution (barrier crossover)
 * when something consumes it:
 *
 *  - its reduced costs become the Benders cut — the plain backward pass
 *    (no aperture set resolves) builds the cut directly from the forward
 *    duals; or
 *  - its basis is captured to warm-start later solves
 *    (`basis_cross_mode` = warm / forward_to_backward / full_cross).
 *
 * Otherwise (aperture cuts, basis_cross_mode = off / backward_to_forward)
 * the forward solve is barrier WITHOUT crossover.  An explicit non-`auto`
 * `forward_solver_options.crossover` always wins; simplex algorithms are
 * left untouched (they already end on a vertex).
 *
 * Cuts built from interior (non-crossed-over) forward data — the aperture
 * bcut fallback, or the plain path under an explicit `crossover = none` —
 * have their intercept lowered by the barrier's relative optimality
 * tolerance, so primal–dual gap noise can never lift the lower bound
 * above the upper bound.
 */

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <span>

#include <gtopt/sddp_enums.hpp>
#include <gtopt/solver_options.hpp>

namespace gtopt
{

/// Relative intercept shift applied to cuts built from interior
/// (barrier, no crossover) forward data: the default barrier relative
/// optimality gap of the supported backends (CPLEX / HiGHS: 1e-8).
inline constexpr double interior_cut_rel_gap = 1e-8;

/// True when @p opts solves an LP by barrier (or the solver default,
/// which picks barrier on these LPs) without crossover — the solution
/// and its duals are an interior point, not a vertex.
[[nodiscard]] constexpr bool is_interior_solve(
    const SolverOptions& opts) noexcept
{
  return opts.crossover == CrossoverMode::none
      && (opts.algorithm == LPAlgo::barrier
          || opts.algorithm == LPAlgo::default_algo);
}

/// True when @p mode captures the forward solve's basis for later reuse.
[[nodiscard]] constexpr bool forward_basis_needed(BasisCrossMode mode) noexcept
{
  return mode == BasisCrossMode::warm
      || mode == BasisCrossMode::forward_to_backward
      || mode == BasisCrossMode::full_cross;
}

/// Resolve the forward-pass solver options (see the file comment).
///
/// @param fwd               forward options (explicit or the base LP ones)
/// @param explicit_options  `forward_solver_options` was given
/// @param duals_feed_cuts   the backward pass builds cuts from the
///                          forward duals (no aperture set resolves)
/// @param basis_mode        effective `basis_cross_mode`
[[nodiscard]] constexpr SolverOptions resolve_forward_solver_options(
    SolverOptions fwd,
    bool explicit_options,
    bool duals_feed_cuts,
    BasisCrossMode basis_mode) noexcept
{
  if (fwd.algorithm != LPAlgo::barrier && fwd.algorithm != LPAlgo::default_algo)
  {
    return fwd;  // simplex ends on a vertex anyway
  }
  if (explicit_options && fwd.crossover != CrossoverMode::automatic) {
    return fwd;  // explicit primal / dual / none
  }
  const bool vertex = duals_feed_cuts || forward_basis_needed(basis_mode);
  fwd.crossover = vertex ? CrossoverMode::automatic : CrossoverMode::none;
  return fwd;
}

/// Dual infeasibility of an LP solution (minimisation, solver space):
/// reduced-cost sign violations on one-sided / free columns and dual
/// sign violations on one-sided rows (|bound| ≥ @p inf is infinite).
/// `weighted` = Σ_j violation_j · max(1, |x_j|) over the columns — the
/// first-order error a cut inherits at the trial point.
struct DualInfeasibility
{
  double max_col {0.0};
  double max_row {0.0};
  double weighted {0.0};
};

[[nodiscard]] inline DualInfeasibility dual_infeasibility(
    std::span<const double> col_low,
    std::span<const double> col_upp,
    std::span<const double> reduced_cost,
    std::span<const double> col_sol,
    std::span<const double> row_low,
    std::span<const double> row_upp,
    std::span<const double> row_dual,
    double inf) noexcept
{
  DualInfeasibility out;
  const auto finite = [inf](double v) { return std::abs(v) < inf; };
  const auto nc = std::min(
      {col_low.size(), col_upp.size(), reduced_cost.size(), col_sol.size()});
  for (std::size_t j = 0; j < nc; ++j) {
    const bool lo = finite(col_low[j]);
    const bool up = finite(col_upp[j]);
    const double d = reduced_cost[j];
    double viol = 0.0;
    if (lo && !up) {
      viol = std::max(0.0, -d);
    } else if (!lo && up) {
      viol = std::max(0.0, d);
    } else if (!lo && !up) {
      viol = std::abs(d);
    }
    out.max_col = std::max(out.max_col, viol);
    out.weighted += viol * std::max(1.0, std::abs(col_sol[j]));
  }
  const auto nr = std::min({row_low.size(), row_upp.size(), row_dual.size()});
  for (std::size_t i = 0; i < nr; ++i) {
    const bool lo = finite(row_low[i]);
    const bool up = finite(row_upp[i]);
    const double y = row_dual[i];
    double viol = 0.0;
    if (lo && !up) {
      viol = std::max(0.0, -y);
    } else if (!lo && up) {
      viol = std::max(0.0, y);
    }
    out.max_row = std::max(out.max_row, viol);
  }
  return out;
}

/// Cut intercept from forward data: lowered by `interior_cut_rel_gap ·
/// max(1, |z|)` when the data come from an interior solve.
[[nodiscard]] inline double guarded_cut_intercept(double z,
                                                  bool interior) noexcept
{
  if (!interior) {
    return z;
  }
  return z - (interior_cut_rel_gap * std::max(1.0, std::abs(z)));
}

}  // namespace gtopt
