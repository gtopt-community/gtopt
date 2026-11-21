// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_capacity_schedule_expansion.cpp
 * @brief     Exogenous `capacity` schedules on expandable elements
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The capacity balance is
 *   capainst_t = (1 − ξ)·capainst_{t−1} + expcap·expmod_t + (C_t − C_{t−1}),
 * with C the exogenous `capacity` schedule.  Regressions:
 *   1. First expandable stage: the existing capacity C_t enters whole —
 *      it used to be forced through `expmod` (capainst = builds ≥ C_t),
 *      i.e. existing capacity was charged as a build.
 *   2. Phase boundary: C_{t−1} is the previous stage's schedule value; it
 *      used to be replaced by C_t, so a dated capacity increase across
 *      phases had to be bought.
 */

#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_options.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace capacity_schedule_expansion_test
{
namespace
{

// clang-format off

/// g1 (10 $/MWh) with capacity schedule [100, 150] MW and expansion
/// modules of 10 MW at 8760 $/MW-yr (1 $/MW·h); demand 80 then 140 MW.
/// The schedule covers the load in both stages, so the optimum builds
/// nothing: 80 × 10 + 140 × 10 = 2200.
constexpr std::string_view kCase = R"({
  "options": {"annual_discount_rate": 0.0, "output_format": "csv",
              "model_options": {"use_single_bus": true,
                                "demand_fail_cost": 10000,
                                "scale_objective": 1}},
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1},
                    {"uid": 2, "first_block": 1, "count_block": 1}]PHASES,
    "scenario_array": [{"uid": 1, "probability_factor": 1}]},
  "system": {
    "name": "cap_schedule",
    "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": "b1",
                      "lmax": [[80], [140]]}],
    "generator_array": [{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10,
                         "capacity": [100, 150], "expcap": 10,
                         "expmod": [10, 10], "capmax": 300,
                         "annual_capcost": 8760}]}
})";

// clang-format on

[[nodiscard]] std::string make_case(std::string_view phases)
{
  std::string s {kCase};
  s.replace(s.find("PHASES"), 6, phases);
  return s;
}

}  // namespace
}  // namespace capacity_schedule_expansion_test

TEST_CASE(  // NOLINT
    "Capacity schedule on an expandable element - existing capacity is free")
{
  using namespace capacity_schedule_expansion_test;
  Planning base;
  base.merge(parse_planning_json(make_case("")));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  CHECK(plp.systems().front().front().linear_interface().get_obj_value()
        == doctest::Approx(2200.0));
}

TEST_CASE(  // NOLINT
    "Capacity schedule on an expandable element - phase boundary (SDDP)")
{
  using namespace capacity_schedule_expansion_test;
  Planning base;
  base.merge(parse_planning_json(make_case(
      R"(,
    "phase_array": [{"uid": 1, "first_stage": 0, "count_stage": 1},
                    {"uid": 2, "first_stage": 1, "count_stage": 1}])")));
  PlanningLP plp(std::move(base));

  SDDPOptions opts;
  opts.max_iterations = 10;
  opts.convergence_tol = 1.0e-9;
  opts.stationary_tol = 0.0;
  opts.apertures = std::vector<Uid> {};
  opts.enable_api = false;
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  const auto& last = results->back();
  INFO("LB=", last.lower_bound, " UB=", last.upper_bound);
  // the 50 MW dated increase in phase 2 is exogenous, not a build
  CHECK(last.upper_bound == doctest::Approx(2200.0).epsilon(1e-6));
  CHECK(last.lower_bound == doctest::Approx(2200.0).epsilon(1e-6));
}
