// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_storage_rep_day_conservation.cpp
 * @brief     Storage energy conservation on representative-day stages
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Representative-day stages (as written by the AEMO ISP expansion
 * converter): 24 hour-of-day blocks whose duration is the number of days
 * the stage represents.  A `daily_cycle` battery scales its balance by
 * 24 / stage_duration and closes the cycle in the stage, so each real
 * day stores at most `emax` and the grid sees days × that energy; a
 * coupled (state-variable) battery carries MWh across stages with the
 * full block durations.  The optima below are computed by hand: any
 * created or lost energy changes them.
 */

#include <format>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace rep_day_conservation_test
{
namespace
{

[[nodiscard]] double solve_obj(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  return plp.systems().front().front().linear_interface().get_obj_value();
}

constexpr std::string_view kOptions =
    R"("options": {"annual_discount_rate": 0.0, "output_format": "csv",
                 "model_options": {"use_single_bus": true,
                                   "demand_fail_cost": 10000,
                                   "scale_objective": 1}})";

}  // namespace
}  // namespace rep_day_conservation_test

TEST_CASE(  // NOLINT
    "Representative day - daily_cycle battery conserves energy per day")
{
  using namespace rep_day_conservation_test;
  // One stage = 10 days × 24 hour-of-day blocks of 10 h.  Energy costs
  // 1 $/MWh in hours 1–12 and 100 $/MWh in hours 13–24; load 10 MW.
  // Battery 50 MWh, η_in = η_out = 0.9, starts / ends each day at 0.
  // Per day: charge 50/0.9 MWh, deliver 50·0.9 = 45 MWh.
  //   cheap 10 d × (120 + 55.56) · 1 + dear 10 d × (120 − 45) · 100
  //   = 1755.56 + 75000 = 76755.56
  std::string blocks;
  std::string gcost;
  for (int b = 0; b < 24; ++b) {
    blocks += std::format(
        R"({}{{"uid": {}, "duration": 10}})", b == 0 ? "" : ", ", b + 1);
    gcost += std::format("{}{}", b == 0 ? "" : ", ", b < 12 ? 1 : 100);
  }
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 24}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "rep_day_battery",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[{}]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 0.9, "output_efficiency": 0.9,
                        "annual_standing_loss": 0, "emin": 0, "emax": 50, "eini": 0,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 50, "daily_cycle": true}}]}}
}})",
      kOptions,
      blocks,
      gcost);
  CHECK(solve_obj(json) == doctest::Approx(1200.0 + (500.0 / 0.9) + 75000.0));
}

TEST_CASE(  // NOLINT
    "Representative day - coupled battery carries MWh across stages")
{
  using namespace rep_day_conservation_test;
  // Two rep-day stages of one block: 10 h at 1 $/MWh, then 5 h at
  // 100 $/MWh; load 10 MW; coupled 50 MWh battery (η 0.9 / 0.9).
  //   stage 1: 100 + 50/0.9 = 155.56;  stage 2: (50 − 45) · 100 = 500
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 10}}, {{"uid": 2, "duration": 5}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}},
                    {{"uid": 2, "first_block": 1, "count_block": 1}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "rep_day_coupled",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[1], [100]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 0.9, "output_efficiency": 0.9,
                        "annual_standing_loss": 0, "emin": 0, "emax": 50, "eini": 0,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 50, "daily_cycle": false,
                        "use_state_variable": true}}]}}
}})",
      kOptions);
  CHECK(solve_obj(json) == doctest::Approx(100.0 + (50.0 / 0.9) + 500.0));
}
