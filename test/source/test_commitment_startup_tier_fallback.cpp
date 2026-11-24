// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_startup_tier_fallback.cpp
 * @brief     Startup-tier pricing of the first start and the fallback for
 *            an unusable tier set
 * @date      2026-11-23
 *
 * One unit (pmin = pmax = load = 10 MW, so even the relaxed u must be 1
 * when it runs), off at t = 0 for `initial_hours`, starts in the first
 * loaded block: the LP objective is exactly its start cost.
 */

#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] std::string stf_json(std::string_view uc_extra,
                                   bool chronological = true,
                                   int first_on_block = 1)
{
  // Demand only from `first_on_block` on, so the start happens there.
  std::string lmax;
  for (int b = 1; b <= 3; ++b) {
    lmax +=
        std::format("{}{}", b > 1 ? ", " : "", b >= first_on_block ? 10 : 0);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1000,
      "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{{"uid": 1, "duration": 1}},
                                 {{"uid": 2, "duration": 1}},
                                 {{"uid": 3, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 3,
                     "chronological": {}}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "stf",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1,
                      "lmax": [[{}]], "capacity": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": 1, "gcost": 0,
                         "capacity": 10}}],
    "commitment_array": [{{"uid": 1, "name": "uc_g1", "generator": "g1",
      "pmin": 10, "initial_status": 0, "relax": true{}}}]
  }}
}})",
      chronological,
      lmax,
      uc_extra);
}

[[nodiscard]] double stf_obj(std::string_view uc_extra, int first_on = 1)
{
  PlanningLP plp(parse_planning_json(
      stf_json(uc_extra, /*chronological=*/true, first_on)));
  REQUIRE(plp.resolve().has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

constexpr std::string_view kTiers =
    R"(, "hot_start_cost": 10, "warm_start_cost": 20,
         "cold_start_cost": 40, "hot_start_time": 2, "cold_start_time": 6)";

}  // namespace

TEST_CASE("Startup tiers: the first start is priced from the offline time")
// NOLINT
{
  // Off 1 h before t = 0: hot (< 2 h).
  CHECK(stf_obj(std::format(R"({}, "initial_hours": 1)", kTiers))
        == doctest::Approx(10.0));
  // Off 4 h: warm (2 ≤ 4 < 6).
  CHECK(stf_obj(std::format(R"({}, "initial_hours": 4)", kTiers))
        == doctest::Approx(20.0));
  // Off 8 h: cold.
  CHECK(stf_obj(std::format(R"({}, "initial_hours": 8)", kTiers))
        == doctest::Approx(40.0));
  // Off 1 h, start at block 3 (t = 2): 3 h offline → warm, not hot.
  CHECK(stf_obj(std::format(R"({}, "initial_hours": 1)", kTiers), 3)
        == doctest::Approx(20.0));
}

TEST_CASE("Startup tiers: an unusable tier set never makes starts free")
// NOLINT
{
  SUBCASE("missing warm_start_cost: falls back to startup_cost")
  {
    CHECK(stf_obj(R"(, "startup_cost": 25, "hot_start_cost": 10,
          "cold_start_cost": 40, "hot_start_time": 2,
          "cold_start_time": 6, "initial_hours": 8)")
          == doctest::Approx(25.0));
  }
  SUBCASE("missing warm_start_cost, no startup_cost: largest tier cost")
  {
    CHECK(stf_obj(R"(, "hot_start_cost": 10, "cold_start_cost": 40,
          "hot_start_time": 2, "cold_start_time": 6, "initial_hours": 1)")
          == doctest::Approx(40.0));
  }
  SUBCASE("cold_start_time < hot_start_time: fallback, not free")
  {
    CHECK(stf_obj(R"(, "hot_start_cost": 10, "warm_start_cost": 20,
          "cold_start_cost": 40, "hot_start_time": 6,
          "cold_start_time": 2, "initial_hours": 1)")
          == doctest::Approx(40.0));
  }
  SUBCASE("no tiers: plain startup_cost (unchanged)")
  {
    CHECK(stf_obj(R"(, "startup_cost": 25, "initial_hours": 8)")
          == doctest::Approx(25.0));
  }
}
