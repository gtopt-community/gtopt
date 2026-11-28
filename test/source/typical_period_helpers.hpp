// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      typical_period_helpers.hpp
 * @brief     Shared helpers of the typical-period (periodic stage) tests
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Invariant under test: on a periodic stage (`Stage.periodic`, default
 * `period_hours` set or point sampling) the intra-period couplings that
 * opt in (`periodic_storage`, `prev_stage_link = "period"`,
 * `LineCommitment.cycle`) wrap (block N−1 → block 0) and only the
 * inter-stage state variables are not periodic; a state storage moves by
 * n·Δ_cycle across a stage standing for n = duration / period_hours
 * repetitions of its typical period.  Without the options the LP is the
 * legacy one.
 */

#pragma once

#include <format>
#include <string>
#include <string_view>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

namespace typical_period_test
{

/// Solve a single-phase planning and return the objective.
[[nodiscard]] inline double solve_obj(const std::string& json)
{
  gtopt::Planning base;
  base.merge(gtopt::parse_planning_json(json));
  gtopt::PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

/// `"options"` member: single bus, no discount, unit objective scale.
[[nodiscard]] inline std::string options(std::string_view extra = "",
                                         std::string_view method = "monolithic")
{
  return std::format(
      R"("options": {{"annual_discount_rate": 0.0, "output_format": "csv",
                 "method": "{}",
                 "model_options": {{"use_single_bus": true,
                                    "demand_fail_cost": 10000,
                                    "scale_objective": 1 {}}}}})",
      method,
      extra);
}

/// Solve @p planning with SDDP and return {lower bound, upper bound}.
[[nodiscard]] inline auto solve_sddp_bounds(gtopt::PlanningLP& planning_lp)
    -> std::pair<double, double>
{
  gtopt::SDDPOptions opts;
  opts.max_iterations = 20;
  opts.convergence_tol = 1.0e-6;
  opts.cut_sharing = gtopt::CutSharingMode::none;
  opts.enable_api = false;
  gtopt::SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  return {results->back().lower_bound, results->back().upper_bound};
}

}  // namespace typical_period_test
