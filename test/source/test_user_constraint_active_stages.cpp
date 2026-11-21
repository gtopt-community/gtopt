// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_user_constraint_active_stages.cpp
 * @brief     Per-stage activation of user constraints (`active_stages`)
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace uc_active_stages_test
{
namespace
{

/// Two 1 h stages, load 80 MW; g1 10 $/MWh, g2 100 $/MWh.  The user
/// constraint `g1.generation <= 50` (fields in `uc_extra`) shifts 30 MWh
/// to g2 in every stage where it is active.
[[nodiscard]] double solve_obj(std::string_view uc_extra)
{
  const auto json = std::format(
      R"({{
    "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
                "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
    "simulation": {{
      "block_array": [{{"uid": 1, "duration": 1}}, {{"uid": 2, "duration": 1}}],
      "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}},
                      {{"uid": 2, "first_block": 1, "count_block": 1}}],
      "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
    "system": {{
      "name": "uc_active_stages",
      "bus_array": [{{"uid": 1, "name": "b1"}}],
      "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 80}}],
      "generator_array": [
        {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10, "capacity": 100}},
        {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100, "capacity": 100}}],
      "user_constraint_array": [{{"uid": 1, "name": "cap_g1",
        "expression": "generator('g1').generation <= 50" {}}}]}}
  }})",
      uc_extra);
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  return plp.systems().front().front().linear_interface().get_obj_value();
}

}  // namespace
}  // namespace uc_active_stages_test

TEST_CASE("UserConstraint active_stages - row only in active stages")  // NOLINT
{
  using namespace uc_active_stages_test;
  // active in both stages: 2 × (50 × 10 + 30 × 100)
  CHECK(solve_obj("") == doctest::Approx(7000.0));
  // stage 1 off: 80 × 10 + (50 × 10 + 30 × 100)
  CHECK(solve_obj(R"(, "active_stages": [0, 1])") == doctest::Approx(4300.0));
  // never active: 2 × 80 × 10
  CHECK(solve_obj(R"(, "active_stages": [0, 0])") == doctest::Approx(1600.0));
  // scalar `active: false` still wins over the schedule
  CHECK(solve_obj(R"(, "active": false, "active_stages": [1, 1])")
        == doctest::Approx(1600.0));
}
