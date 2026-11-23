/**
 * @file      test_user_constraint_daily_sum_soft.cpp
 * @brief     Soft (penalised) `daily_sum` user constraints
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_user_constraint_daily_sum.cpp` covers hard daily rows.  A
 * `penalty` turns each daily row soft with ONE slack per day (two for
 * `=`), keyed at the day-ending block.  Same 2-day fixture: flat 100 MW
 * load, g1 at 5 $/MWh, g2 at 50 $/MWh, so complying with a 1200 MWh/day
 * split costs 45 $/MWh more than violating it.
 *
 *   penalty 100 (> 45): rows hold         → 132000
 *   penalty  10 (< 45): rows fully relaxed → 24000 + 2400 MWh × 10 = 48000
 */

#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace ucdss_test
{
namespace
{

// clang-format off

auto make_json(std::string_view uc) -> std::string
{
  return std::format(
      R"(
  {{
    "options": {{
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {{
        "use_single_bus": true,
        "demand_fail_cost": 100000,
        "scale_objective": 1
      }}
    }},
    "simulation": {{
      "block_array": [
        {{ "uid": 1, "duration": 12 }},
        {{ "uid": 2, "duration": 12 }},
        {{ "uid": 3, "duration": 12 }},
        {{ "uid": 4, "duration": 12 }}
      ],
      "stage_array": [
        {{ "uid": 1, "first_block": 0, "count_block": 4, "active": 1,
           "chronological": true }}
      ],
      "scenario_array": [ {{ "uid": 1, "probability_factor": 1 }} ]
    }},
    "system": {{
      "name": "daily_sum_uc_test",
      "bus_array": [ {{ "uid": 1, "name": "b1" }} ],
      "generator_array": [
        {{ "uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 100,
           "gcost": 5,  "capacity": 100 }},
        {{ "uid": 2, "name": "g2", "bus": "b1", "pmin": 0, "pmax": 100,
           "gcost": 50, "capacity": 100 }}
      ],
      "demand_array": [
        {{ "uid": 1, "name": "d1", "bus": "b1",
           "lmax": [[100.0, 100.0, 100.0, 100.0]] }}
      ],
      "user_constraint_array": [{}]
    }}
  }}
)",
      uc);
}

// clang-format on

[[nodiscard]] auto solve(std::string_view uc) -> double
{
  Planning base;
  base.merge(parse_planning_json(make_json(uc)));
  PlanningLP planning_lp(std::move(base));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  return systems.front().front().linear_interface().get_obj_value_raw();
}

}  // namespace
}  // namespace ucdss_test

TEST_CASE("UserConstraint daily_sum soft <= relaxes per day")  // NOLINT
{
  using namespace ucdss_test;
  const auto uc = [](int penalty)
  {
    return std::format(
        R"({{ "uid": 1, "name": "g1_cap", "daily_sum": true,
        "constraint_type": "energy", "penalty": {},
        "expression": "generator('g1').generation <= 1200" }})",
        penalty);
  };
  CHECK(solve(uc(100)) == doctest::Approx(132000.0));
  CHECK(solve(uc(10)) == doctest::Approx(48000.0));
}

TEST_CASE("UserConstraint daily_sum soft >= relaxes per day")  // NOLINT
{
  using namespace ucdss_test;
  const auto uc = [](int penalty)
  {
    return std::format(
        R"({{ "uid": 1, "name": "g2_floor", "daily_sum": true,
        "constraint_type": "energy", "penalty": {},
        "expression": "generator('g2').generation >= 1200" }})",
        penalty);
  };
  CHECK(solve(uc(100)) == doctest::Approx(132000.0));
  CHECK(solve(uc(10)) == doctest::Approx(48000.0));
}

TEST_CASE("UserConstraint daily_sum soft = uses two slacks")  // NOLINT
{
  using namespace ucdss_test;
  const auto uc = [](int penalty)
  {
    return std::format(
        R"({{ "uid": 1, "name": "g1_pin", "daily_sum": true,
        "constraint_type": "energy", "penalty": {},
        "expression": "generator('g1').generation = 1200" }})",
        penalty);
  };
  CHECK(solve(uc(100)) == doctest::Approx(132000.0));
  CHECK(solve(uc(10)) == doctest::Approx(48000.0));
}
