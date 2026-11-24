/**
 * @file      coupler_fail_closed.hpp
 * @brief     "Fail-closed" helpers for coupler elements (rule R2)
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A coupler (Turbine, GasTurbine, Converter, Pump, electric
 * GasCompressor) turns a resource of a partner element into another
 * resource.  When the coupler is not *effective* in a stage — it is
 * inactive, or a reference it needs is inactive or has no column — its
 * conversion row cannot be written.  Leaving the partner's producing
 * column free would then create energy / water / compression from
 * nothing, so instead the producing direction of that column is closed:
 *
 *   uppb ← min(uppb, 0),   lowb ← min(lowb, 0)
 *
 * A negative lower bound (reverse / consuming direction) is preserved:
 * only production is forbidden.  See
 * docs/formulation/mathematical-formulation.md ("Coupler fail-closed
 * rule").
 */

#pragma once

#include <algorithm>

#include <gtopt/basic_types.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/linear_problem.hpp>

namespace gtopt
{

/// Close the producing (positive) direction of column @p col.
inline void close_producing_col(LinearProblem& lp, ColIndex col)
{
  auto& c = lp.col_at(col);
  c.uppb = std::min(c.uppb, 0.0);
  c.lowb = std::min(c.lowb, 0.0);
}

/// Close the producing direction of every column in @p cols.
inline void close_producing_cols(LinearProblem& lp,
                                 const BIndexHolder<ColIndex>& cols)
{
  for (const auto& [buid, col] : cols) {
    close_producing_col(lp, col);
  }
}

}  // namespace gtopt
