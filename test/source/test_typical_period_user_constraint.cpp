// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_typical_period_user_constraint.cpp
 * @brief     `<attr>_prev` wraps at block 0 of periodic stages under
 *            `model_options.prev_stage_link = "period"` (V5)
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/validate_planning.hpp>

#include "typical_period_helpers.hpp"

using namespace gtopt;

namespace typical_period_prev_test
{
namespace
{

constexpr std::string_view kPeriod = R"(, "prev_stage_link": "period")";
constexpr std::string_view kPeriodic = R"(, "period_hours": 2)";

/// g1 (1 $) ramp-limited by g1 − g1_prev ≤ 10, g2 (100 $), flat load
/// 50 MW over 2 × 1 h per stage; `nstages` stages of 2 blocks.
[[nodiscard]] std::string prev_case(std::string_view stage_extra,
                                    std::string_view model_extra,
                                    int nstages = 1)
{
  std::string blocks;
  std::string stages;
  for (int s = 0; s < nstages; ++s) {
    blocks += std::format(R"({}{{"uid": {}, "duration": 1}},
                          {{"uid": {}, "duration": 1}})",
                          s == 0 ? "" : ", ",
                          (2 * s) + 1,
                          (2 * s) + 2);
    stages += std::format(
        R"({}{{"uid": {}, "first_block": {}, "count_block": 2,
             "chronological": true {}}})",
        s == 0 ? "" : ", ",
        s + 1,
        2 * s,
        stage_extra);
  }
  return std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "tp_uc_prev",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 50}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "user_constraint_array": [{{"uid": 1, "name": "ramp_g1",
      "expression": "generator('g1').generation - generator('g1').generation_prev <= 10"}}]}}
}})",
      typical_period_test::options(model_extra),
      blocks,
      stages);
}

}  // namespace
}  // namespace typical_period_prev_test

TEST_CASE(  // NOLINT
    "Typical period - user constraint _prev wraps to the last block")
{
  using namespace typical_period_prev_test;
  using typical_period_test::solve_obj;
  // period + periodic stage: g1[0] − g1[1] ≤ 10 → g1 = 50 in both
  // blocks: 100.
  CHECK(solve_obj(prev_case(kPeriodic, kPeriod)) == doctest::Approx(100.0));
  // default (`stage`) on the same periodic stage: block 0 at the horizon
  // start reads 0 (g1[0] ≤ 10), g1[1] ≤ 20:
  // (10 + 40·100) + (20 + 30·100) = 7030 — the LP is unchanged.
  CHECK(solve_obj(prev_case(kPeriodic, "")) == doctest::Approx(7030.0));
  // period on a non-periodic chronological stage: same as `stage`.
  CHECK(solve_obj(prev_case("", kPeriod)) == doctest::Approx(7030.0));
  // explicit periodic = false opts the stage out of the wrap.
  CHECK(
      solve_obj(prev_case(R"(, "period_hours": 2, "periodic": false)", kPeriod))
      == doctest::Approx(7030.0));
}

TEST_CASE(  // NOLINT
    "Typical period - _prev wraps inside each periodic stage")
{
  using namespace typical_period_prev_test;
  using typical_period_test::solve_obj;
  // Two periodic stages: each wraps onto itself (2 × 100).  Under
  // `stage` the second stage chains to the first stage's last block:
  // stage 1 = 7030 as above, stage 2 continues the ramp from g1 = 20:
  // g1 = 30, 40 → (30 + 20·100) + (40 + 10·100) = 3070.  Total 10100.
  CHECK(solve_obj(prev_case(kPeriodic, kPeriod, 2)) == doctest::Approx(200.0));
  CHECK(solve_obj(prev_case(kPeriodic, "", 2)) == doctest::Approx(10'100.0));
}

TEST_CASE(  // NOLINT
    "Typical period - prev_stage_link = period validation")
{
  using namespace typical_period_prev_test;
  const auto errors = [](std::string_view model_extra)
  {
    Planning p;
    p.merge(parse_planning_json(prev_case(kPeriodic, model_extra)));
    const auto res = validate_planning(p);
    return std::ranges::count_if(res.errors,
                                 [](const std::string& e)
                                 { return e.contains("prev_stage_link"); });
  };
  CHECK(errors(kPeriod) == 0);
  CHECK(errors(R"(, "prev_stage_link": "wrap")") == 1);
}
