// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_first_block_shutdown.cpp
 * @brief     A unit that starts online may shut down in the first block
 *            when its initial output is within the shutdown ramp
 * @date      2026-11-25
 *
 * C5 at the first block is ``P0 − p[0] ≤ RD·u[0] + SD·w[0]``: only the
 * lagged output is a constant.  It used to be built with the constants
 * ``RD·initial_status + SD·(1 − initial_status)``, which drops the
 * shutdown term for a unit that starts online: with ``P0 > RD`` it could
 * not shut down in block 1 and was forced to ramp down first (RTS-GMLC:
 * five units kept on in hour 1, +3.5 % on the objective vs UC.jl).
 *
 * Fixture: 3 hourly blocks, load 200 MW.  ``g_exp`` (50 $/MWh,
 * commitment pmin 50, RD 50 MW/h) starts online at P0 = 150 MW;
 * ``g_cheap`` (10 $/MWh, 200 MW, no commitment) can carry the load.
 */

#include <format>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

/// Objective with g_exp's shutdown ramp `sd` (MW).
[[nodiscard]] double first_block_obj(double sd)
{
  const auto json = std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1000,
      "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{{"uid": 1, "duration": 1}},
                                 {{"uid": 2, "duration": 1}},
                                 {{"uid": 3, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 3,
                     "chronological": true}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "first_block_shutdown",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1,
                      "lmax": [[200, 200, 200]], "capacity": 200}}],
    "generator_array": [
      {{"uid": 1, "name": "g_exp", "bus": 1, "gcost": 50, "capacity": 200}},
      {{"uid": 2, "name": "g_cheap", "bus": 1, "gcost": 10,
        "capacity": 200}}],
    "commitment_array": [{{"uid": 1, "name": "uc_exp", "generator": "g_exp",
      "pmin": 50, "relax": false, "ramp_up": 50, "ramp_down": 50,
      "startup_ramp": 200, "shutdown_ramp": {},
      "initial_status": 1, "initial_hours": 100, "initial_power": 150}}]
  }}
}})",
      sd);
  PlanningLP plp(solver_test::with_mip_solver(parse_planning_json(json)));
  REQUIRE(plp.resolve().has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

}  // namespace

TEST_CASE("Commitment C5: online unit may shut down in the first block")
// NOLINT
{
  if (solver_test::first_mip_solver().empty()) {
    MESSAGE("Skipping — no MIP solver available");
    return;
  }
  // P0 = 150 ≤ SD = 150: off from block 1, g_cheap carries 3 × 200 MW.
  // Before the fix C5 read P0 − p[0] ≤ RD (no shutdown term), forcing
  // p[0] ≥ 100: 100·50 + 100·10 + 2·2000 = 10 000.
  CHECK(first_block_obj(150.0) == doctest::Approx(6000.0));
}

TEST_CASE("Commitment C5: first-block shutdown still needs P0 <= SD")
// NOLINT
{
  if (solver_test::first_mip_solver().empty()) {
    MESSAGE("Skipping — no MIP solver available");
    return;
  }
  // P0 = 150 > SD = 100: block 1 stays on and ramps down to
  // P0 − RD = 100 (= SD), then shuts down in block 2:
  // 100·50 + 100·10 + 2·2000 = 10 000.
  CHECK(first_block_obj(100.0) == doctest::Approx(10000.0));
}
