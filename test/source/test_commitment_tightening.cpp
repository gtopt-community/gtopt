// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_tightening.cpp
 * @brief     CommitmentLP formulation details that keep v / w exact:
 *            initial_status rounding, start / stop ramp terms only at the
 *            first block of a commitment period, first-block ramp-up on v
 * @date      2026-11-25
 *
 * Fixture: one bus; `g_uc` (10 $/MWh, 200 MW) under a Commitment and an
 * uncommitted peaker `g_peak` (100 $/MWh, 200 MW).
 */

#include <format>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

struct Case
{
  std::vector<double> load {};
  std::string commitment {};  ///< extra Commitment members (trailing ,)
};

/// Objective of the case.
[[nodiscard]] double solve_obj(const Case& c)
{
  std::string blocks;
  std::string lmax;
  for (std::size_t i = 0; i < c.load.size(); ++i) {
    blocks += std::format(
        R"({}{{"uid": {}, "duration": 1}})", i == 0 ? "" : ", ", i + 1);
    lmax += std::format("{}{}", i == 0 ? "" : ", ", c.load[i]);
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
  "system": {{"name": "commitment_tightening",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1,
                      "lmax": [[{}]], "capacity": 200}}],
    "generator_array": [
      {{"uid": 1, "name": "g_uc", "bus": 1, "gcost": 10, "capacity": 200}},
      {{"uid": 2, "name": "g_peak", "bus": 1, "gcost": 100,
        "capacity": 200}}],
    "commitment_array": [{{"uid": 1, "name": "uc", "generator": "g_uc",
      {} "initial_hours": 100}}]
  }}
}})",
      blocks,
      c.load.size(),
      lmax,
      c.commitment);
  PlanningLP plp(solver_test::with_mip_solver(parse_planning_json(json)));
  REQUIRE(plp.resolve().has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

[[nodiscard]] bool no_mip_solver()
{
  if (solver_test::first_mip_solver().empty()) {
    MESSAGE("Skipping — no MIP solver available");
    return true;
  }
  return false;
}

}  // namespace

TEST_CASE("Commitment initial_status is rounded at 0.5")  // NOLINT
{
  if (no_mip_solver()) {
    return;
  }
  // LP relaxation, one hour, load 200 = pmax (so u0 = 1), startup 500.
  // On from the start: 200·10 = 2000.  Off: start (500) + 2000 = 2500.
  // Unrounded, 0.7 would give v0 = 1 − 0.7 = 0.3 (2150) and 0.3 would
  // give v0 = 0.7 (2350).
  const auto obj = [](double initial_status)
  {
    return solve_obj({
        .load = {200.0},
        .commitment = std::format(
            R"("relax": true, "startup_cost": 500, "initial_status": {},)",
            initial_status),
    });
  };
  CHECK(obj(1.0) == doctest::Approx(2000.0));
  CHECK(obj(0.7) == doctest::Approx(2000.0));
  CHECK(obj(0.5) == doctest::Approx(2000.0));
  CHECK(obj(0.3) == doctest::Approx(2500.0));
  CHECK(obj(0.0) == doctest::Approx(2500.0));
}

TEST_CASE(  // NOLINT
    "Commitment ramp: SU applies only at the first block of a "
    "commitment_period")
{
  if (no_mip_solver()) {
    return;
  }
  // Load 0, 0, 200, 200; g_uc off initially, SU 100, RU 10 MW/h.  It
  // starts at hour 3: p3 <= SU = 100, then p4 <= p3 + RU = 110, the rest
  // from the peaker: (100·10 + 100·100) + (110·10 + 90·100) = 21 100.
  // With commitment_period = 2 h (u per 2 hours) hour 4 is interior to
  // the start period: it must also ramp with RU·u alone — the same
  // 21 100.  Applying SU·v there too (the former form) let p4 jump to
  // 200: 13 000.
  constexpr std::string_view base =
      R"("ramp_up": 10, "startup_ramp": 100, "initial_status": 0,
         "initial_power": 0,)";
  const auto per_block = solve_obj({
      .load = {0.0, 0.0, 200.0, 200.0},
      .commitment = std::string {base},
  });
  const auto aggregated = solve_obj({
      .load = {0.0, 0.0, 200.0, 200.0},
      .commitment = std::string {base} + R"("commitment_period": 2,)",
  });
  CHECK(per_block == doctest::Approx(21100.0));
  CHECK(aggregated == doctest::Approx(21100.0));
}

TEST_CASE(  // NOLINT
    "Commitment ramp: SD applies only at the first block of a "
    "commitment_period")
{
  if (no_mip_solver()) {
    return;
  }
  // Mirror of the ramp-up case: load 200, 200, 0, 0 with g_uc online at
  // P0 = 200 MW, SD 100, RD 50 MW/h.  It must be off at hour 3 (no load,
  // no oversupply), which needs p2 <= SD = 100, hence p1 <= 150, and
  // p1 >= P0 − RD = 150: p1 = 150 (+50 peaker), p2 = 100 (+100 peaker):
  // 1500 + 5000 + 1000 + 10 000 = 17 500.  The aggregated period (the
  // stop at hour 3 is the first block of period 2) gives the same.
  constexpr std::string_view base =
      R"("ramp_down": 50, "shutdown_ramp": 100, "initial_status": 1,
         "initial_power": 200,)";
  const auto per_block = solve_obj({
      .load = {200.0, 200.0, 0.0, 0.0},
      .commitment = std::string {base},
  });
  const auto aggregated = solve_obj({
      .load = {200.0, 200.0, 0.0, 0.0},
      .commitment = std::string {base} + R"("commitment_period": 2,)",
  });
  CHECK(per_block == doctest::Approx(17500.0));
  CHECK(aggregated == doctest::Approx(17500.0));
}

TEST_CASE("Commitment first-block ramp-up uses v[0]")  // NOLINT
{
  if (no_mip_solver()) {
    return;
  }
  // One hour, load 100, g_uc off initially, startup 500, SU 100.  The MIP
  // starts it: 500 + 100·10 = 1500.  The LP relaxation with the former
  // constant RHS (p0 <= SU) could run p0 = 100 at u0 = v0 = 1/2 (1250);
  // with p0 − SU·v0 <= 0 it needs v0 = 1 — the MIP value.
  const auto obj = [](bool relax)
  {
    return solve_obj({
        .load = {100.0},
        .commitment = std::format(
            R"("relax": {}, "startup_cost": 500, "ramp_up": 50,
               "startup_ramp": 100, "initial_status": 0,
               "initial_power": 0,)",
            relax ? "true" : "false"),
    });
  };
  CHECK(obj(false) == doctest::Approx(1500.0));
  CHECK(obj(true) == doctest::Approx(1500.0));
}
