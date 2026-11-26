// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_storage_cycle_group.cpp
 * @brief     Weighted storage cycle over a group of representative days
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `Stage::cycle_group` / `Stage::cycle_weight`: a weekend and a weekday
 * representative day of one typical week (weights 2 and 5).  A
 * `daily_cycle` battery may then carry energy from the weekend into the
 * weekdays: the group is closed with Σ_d w_d·Δ_d = 0 instead of
 * Δ_d = 0 per day, and the last repetition of every day is bounded.
 * All optima are computed by hand.
 *
 * Common data: stage 1 = weekend (one block of 48 h = 2 days), stage 2 =
 * weekday (one block of 120 h = 5 days); daily_cycle scaling makes each
 * block one 24 h day of the battery balance.  Load 0 MW on the weekend,
 * 10 MW on weekdays (240 MWh/day).  Generator `cheap` (10 $/MWh) only on
 * the weekend; `dear` (100 $/MWh) always.  Battery starts the week at 0.
 */

#include <algorithm>
#include <format>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace cycle_group_test
{
namespace
{

[[nodiscard]] Planning make_planning(std::string_view extra1,
                                     std::string_view extra2,
                                     double emax,
                                     double eff)
{
  const auto json = std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 48}},
                    {{"uid": 2, "duration": 120}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1 {0}}},
                    {{"uid": 2, "first_block": 1, "count_block": 1 {1}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "cycle_group",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[0], [10]]}}],
    "generator_array": [
      {{"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10,
        "pmax": [[100], [0]], "capacity": 100}},
      {{"uid": 2, "name": "dear", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": {3}, "output_efficiency": {3},
                        "emin": 0, "emax": {2}, "eini": 0,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": {2}}}]}}
}})",
      extra1,
      extra2,
      emax,
      eff);
  Planning base;
  base.merge(parse_planning_json(json));
  return base;
}

[[nodiscard]] double solve_obj(Planning planning)
{
  PlanningLP plp(std::move(planning));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  return plp.systems().front().front().linear_interface().get_obj_value();
}

[[nodiscard]] bool has_error(const ValidationResult& r, std::string_view what)
{
  return std::ranges::any_of(r.errors,
                             [&](const auto& e)
                             { return e.find(what) != std::string::npos; });
}

constexpr std::string_view kGroup = R"(, "cycle_group": 1)";

}  // namespace
}  // namespace cycle_group_test

TEST_CASE(  // NOLINT
    "Storage cycle group - daily_cycle default closes every day")
{
  using namespace cycle_group_test;
  // No cycle_group: Δ_d = 0 per day, the battery cannot shift energy
  // from the weekend; weekdays are served by `dear`:
  //   10 MW × 120 h × 100 $/MWh = 120000
  CHECK(solve_obj(make_planning("", "", 2000, 1.0))
        == doctest::Approx(120000.0));
}

TEST_CASE(  // NOLINT
    "Storage cycle group - weekend charging serves the weekdays")
{
  using namespace cycle_group_test;
  // Weekend charges c MWh/day, weekdays discharge d MWh/day:
  //   closure 2c = 5d, weekday need d = 240  ⇒  c = 600,
  //   peak level 2c = 1200 ≤ emax 2000, weekday floor 1200 − 5·240 = 0.
  //   cost = 2 days × 600 MWh × 10 $/MWh = 12000
  const double expected = 2.0 * 600.0 * 10.0;
  SUBCASE("default weights = days represented")
  {
    CHECK(solve_obj(make_planning(kGroup, kGroup, 2000, 1.0))
          == doctest::Approx(expected));
  }
  SUBCASE("explicit weights")
  {
    const auto parsed =
        make_planning(R"(, "cycle_group": 7, "cycle_weight": 2)",
                      R"(, "cycle_group": 7, "cycle_weight": 5)",
                      2000,
                      1.0);
    REQUIRE(parsed.simulation.stage_array.size() == 2);
    CHECK(parsed.simulation.stage_array[0].cycle_weight.value_or(-1)
          == doctest::Approx(2.0));
    CHECK(parsed.simulation.stage_array[1].cycle_weight.value_or(-1)
          == doctest::Approx(5.0));
    CHECK(parsed.simulation.stage_array[1].cycle_group.value_or(-1) == 7);
    CHECK(solve_obj(make_planning(R"(, "cycle_group": 7, "cycle_weight": 2)",
                                  R"(, "cycle_group": 7, "cycle_weight": 5)",
                                  2000,
                                  1.0))
          == doctest::Approx(expected));
  }
}

TEST_CASE(  // NOLINT
    "Storage cycle group - last repetition bound limits the carry-over")
{
  using namespace cycle_group_test;
  // emax 1000: after the 2nd weekend day the level is 2c ≤ 1000, so
  // c = 500 (a bound on the first repetition only would allow 600).
  //   weekdays: 5d = 2c = 1000 ⇒ d = 200 MWh/day from the battery,
  //   40 MWh/day from `dear`.
  //   cost = 1000 × 10 + 5 × 40 × 100 = 10000 + 20000 = 30000
  CHECK(solve_obj(make_planning(kGroup, kGroup, 1000, 1.0))
        == doctest::Approx(30000.0));
}

TEST_CASE(  // NOLINT
    "Storage cycle group - no energy is created with losses")
{
  using namespace cycle_group_test;
  // η_in = η_out = 0.9.  Weekdays need 240 MWh/day at the grid ⇒ the
  // cell gives d = 240/0.9 per day; closure 2c = 5d = 1333.33 cell MWh,
  // bought on the weekend as 1333.33/0.9 = 1481.48 grid MWh at 10 $/MWh
  // (cheaper than `dear`: 10/0.81 = 12.35 < 100).
  const double weekly_cell = 5.0 * 240.0 / 0.9;
  CHECK(solve_obj(make_planning(kGroup, kGroup, 2000, 0.9))
        == doctest::Approx(weekly_cell / 0.9 * 10.0));
}

TEST_CASE(  // NOLINT
    "Storage cycle group - validation")
{
  using namespace cycle_group_test;
  SUBCASE("group may not cross a phase boundary")
  {
    auto p = make_planning(kGroup, kGroup, 2000, 1.0);
    p.simulation.phase_array = {
        {
            .uid = Uid {1},
            .first_stage = 0,
            .count_stage = 1,
        },
        {
            .uid = Uid {2},
            .first_stage = 1,
            .count_stage = 1,
        },
    };
    CHECK(has_error(validate_planning(p), "crosses a phase boundary"));
  }
  SUBCASE("weight must be positive")
  {
    auto p = make_planning(
        R"(, "cycle_group": 1, "cycle_weight": 0)", kGroup, 2000, 1.0);
    CHECK(has_error(validate_planning(p), "cycle_weight"));
  }
  SUBCASE("group must be a run of consecutive stages")
  {
    auto p = make_planning(kGroup, kGroup, 2000, 1.0);
    p.simulation.stage_array[0].cycle_group = 3;
    p.simulation.stage_array.push_back(p.simulation.stage_array[0]);
    p.simulation.stage_array.back().uid = Uid {3};
    CHECK(has_error(validate_planning(p), "not a run of consecutive"));
  }
  SUBCASE("a valid group passes")
  {
    auto p = make_planning(kGroup, kGroup, 2000, 1.0);
    CHECK(validate_planning(p).ok());
  }
}
