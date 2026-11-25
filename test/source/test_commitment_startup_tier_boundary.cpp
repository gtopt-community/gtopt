// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_startup_tier_boundary.cpp
 * @brief     Hot / warm / cold startup tiers use the same strict offline
 *            thresholds for an in-horizon shutdown and an initial outage
 * @date      2026-11-25
 *
 * One unit (pmin = pmax = load = 10 MW, gcost 0) follows the load
 * pattern; the objective is exactly the cost of its starts.  The unit is
 * integer (relax = false): in the LP relaxation the tier rows are weak
 * (a fractional mix of shutdown windows prices a 4 h restart at 26.7),
 * so only the MIP shows the tier a start actually gets.  Tiers:
 * hot 10 for offline < 2 h, warm 20 for 2 h ≤ offline < 4 h, cold 40
 * for offline ≥ 4 h (docs/unit-commitment.md §4.5).
 *
 * The offline time of a start is counted the same way whether the unit
 * went off inside the horizon (shutdown period q: offline Σ dur[q..p-1])
 * or before it (initial_hours + in-horizon time before p).  The
 * in-horizon windows used to include the shutdown whose offline time
 * equals the threshold, pricing exactly 2 h hot and exactly 4 h warm,
 * while the same outage carried in through initial_hours was priced
 * warm / cold.
 */

#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

constexpr int kBlocks = 8;

/// Objective (= total start cost) of the unit following `load` (one
/// value per hourly block, 0 or 10), with the given initial condition.
[[nodiscard]] double tier_obj(const std::vector<int>& load,
                              int initial_status,
                              double initial_hours)
{
  REQUIRE(load.size() == static_cast<size_t>(kBlocks));
  std::string lmax;
  std::string blocks;
  for (int b = 0; b < kBlocks; ++b) {
    lmax += std::format("{}{}", b > 0 ? ", " : "", load[b]);
    blocks += std::format(
        R"({}{{"uid": {}, "duration": 1}})", b > 0 ? ", " : "", b + 1);
  }
  const auto json = std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1000,
      "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                     "chronological": true}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "tier_boundary",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1,
                      "lmax": [[{}]], "capacity": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": 1, "gcost": 0,
                         "capacity": 10}}],
    "commitment_array": [{{"uid": 1, "name": "uc_g1", "generator": "g1",
      "pmin": 10, "relax": false,
      "initial_status": {}, "initial_hours": {},
      "hot_start_cost": 10, "warm_start_cost": 20, "cold_start_cost": 40,
      "hot_start_time": 2, "cold_start_time": 4}}]
  }}
}})",
      blocks,
      kBlocks,
      lmax,
      initial_status,
      initial_hours);
  PlanningLP plp(solver_test::with_mip_solver(parse_planning_json(json)));
  REQUIRE(plp.resolve().has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

/// Load pattern: on in block 1, off for `offline` blocks, then on to the
/// end — one in-horizon shutdown (block 2) and one start after
/// `offline` hours.
[[nodiscard]] std::vector<int> restart_after(int offline)
{
  std::vector<int> load(kBlocks, 10);
  for (int b = 1; b <= offline; ++b) {
    load[b] = 0;
  }
  return load;
}

/// Load pattern for an initially-off unit: on from block 1, so the only
/// start is at t = 0 after `initial_hours` offline.
[[nodiscard]] std::vector<int> always_on()
{
  return std::vector<int>(kBlocks, 10);
}

}  // namespace

TEST_CASE("Startup tiers: in-horizon shutdown uses strict thresholds")
// NOLINT
{
  if (solver_test::first_mip_solver().empty()) {
    MESSAGE("Skipping — no MIP solver available");
    return;
  }
  // Initially on (no start at t = 0); shutdown at block 2.
  CHECK(tier_obj(restart_after(1), 1, 100) == doctest::Approx(10.0));
  // Exactly hot_start_time (2 h) offline: warm, not hot.
  CHECK(tier_obj(restart_after(2), 1, 100) == doctest::Approx(20.0));
  CHECK(tier_obj(restart_after(3), 1, 100) == doctest::Approx(20.0));
  // Exactly cold_start_time (4 h) offline: cold, not warm.
  CHECK(tier_obj(restart_after(4), 1, 100) == doctest::Approx(40.0));
  CHECK(tier_obj(restart_after(5), 1, 100) == doctest::Approx(40.0));
}

TEST_CASE("Startup tiers: initial outage prices like an in-horizon one")
// NOLINT
{
  if (solver_test::first_mip_solver().empty()) {
    MESSAGE("Skipping — no MIP solver available");
    return;
  }
  // Initially off for `h` hours, start at t = 0: the same tier as a
  // start `h` hours after an in-horizon shutdown (test above).
  for (const int h : {1, 2, 3, 4, 5}) {
    CAPTURE(h);
    CHECK(tier_obj(always_on(), 0, h)
          == doctest::Approx(tier_obj(restart_after(h), 1, 100)));
  }
  CHECK(tier_obj(always_on(), 0, 1) == doctest::Approx(10.0));
  CHECK(tier_obj(always_on(), 0, 2) == doctest::Approx(20.0));
  CHECK(tier_obj(always_on(), 0, 4) == doctest::Approx(40.0));
}
