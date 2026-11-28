// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_typical_period_line_commitment.cpp
 * @brief     Cyclic LineCommitment (`cycle = "stage"`) on typical
 *            periods (V4)
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Two buses, g1 (10 $/MWh) on b1, load on b2 behind one switchable line
 * (relaxed status), two blocks.  `cycle = "stage"` wraps the breaker
 * state around the stage and ignores `initial_status`; unset keeps the
 * chronological rules on every stage (periodic or not).
 */

#include <algorithm>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/validate_planning.hpp>

#include "typical_period_helpers.hpp"

using namespace gtopt;

namespace typical_period_line_uc_test
{
namespace
{

struct LineCase
{
  std::string_view stage_extra {};
  std::string_view lc_extra {};
  double dur {1.0};  ///< block duration (weight)
  std::string_view lmax {"100"};
};

[[nodiscard]] std::string line_case(const LineCase& c)
{
  return std::format(
      R"({{
    "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
      "model_options": {{"use_single_bus": false, "use_kirchhoff": false,
                         "scale_objective": 1, "demand_fail_cost": 10000}}}},
    "simulation": {{
      "block_array": [{{"uid": 1, "duration": {}}},
                      {{"uid": 2, "duration": {}}}],
      "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 2,
                        "chronological": true {}}}],
      "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
    "system": {{
      "name": "tp_line_uc",
      "bus_array": [{{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}}],
      "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                            "gcost": 10, "capacity": 500}}],
      "demand_array": [{{"uid": 1, "name": "d2", "bus": "b2",
                         "lmax": {}}}],
      "line_array": [{{"uid": 1, "name": "l1_2", "bus_a": "b1",
                       "bus_b": "b2", "tmax_ab": 200, "tmax_ba": 200}}],
      "line_commitment_array": [{{"uid": 1, "name": "l1_2_ots",
                                  "line": "l1_2", "relax": true,
                                  "initial_status": 0 {}}}]}}
  }})",
      c.dur,
      c.dur,
      c.stage_extra,
      c.lmax,
      c.lc_extra);
}

constexpr std::string_view kPeriodic = R"(, "period_hours": 2)";
constexpr std::string_view kCycle = R"(, "cycle": "stage")";
constexpr std::string_view kCycleStart =
    R"(, "cycle": "stage", "startup_cost": 50)";

}  // namespace
}  // namespace typical_period_line_uc_test

TEST_CASE(  // NOLINT
    "Typical period - cyclic line commitment ignores initial_status")
{
  using namespace typical_period_line_uc_test;
  using typical_period_test::solve_obj;
  // cycle = "stage": no first-block pin, the line stays closed: 2·100·10.
  CHECK(solve_obj(line_case({.stage_extra = kPeriodic, .lc_extra = kCycle}))
        == doctest::Approx(2000.0));
  // unset (default): u_l[0] = initial_status = 0 → block 0 unserved:
  // 100·10000 + 100·10 — also on a periodic stage (opt-in only).
  CHECK(solve_obj(line_case({.stage_extra = kPeriodic}))
        == doctest::Approx(1'001'000.0));
  CHECK(solve_obj(line_case({})) == doctest::Approx(1'001'000.0));
  CHECK(solve_obj(line_case({.lc_extra = R"(, "cycle": "none")"}))
        == doctest::Approx(1'001'000.0));

  // With a start cost (u/v/w on): the wrapped C1 needs no start inside
  // the period (closed all along); chronological from initial_status = 0
  // pays a start (relaxed: u_0 = 0.5 carries the 100 MW, 0.5·50 = 25).
  constexpr std::string_view start = R"(, "startup_cost": 50)";
  CHECK(
      solve_obj(line_case({.stage_extra = kPeriodic, .lc_extra = kCycleStart}))
      == doctest::Approx(2000.0));
  CHECK(solve_obj(line_case({.stage_extra = kPeriodic, .lc_extra = start}))
        == doctest::Approx(2025.0));
}

TEST_CASE(  // NOLINT
    "Typical period - cyclic line start cost is paid once per repetition")
{
  using namespace typical_period_line_uc_test;
  using typical_period_test::solve_obj;
  // Typical 2 h period repeated n = 10 times (durations 10, 10); the
  // line is forced closed in block 1 and open in block 2 (no load
  // there).  Cyclic: one close per cycle (block 2 → block 1 wraps),
  // 10 cycles: 100·10·10 + 10·50 = 10500.
  constexpr std::string_view fixed =
      R"(, "cycle": "stage", "startup_cost": 50,
           "fixed_status": [[1, 0]])";
  const LineCase c {
      .stage_extra = kPeriodic,
      .lc_extra = fixed,
      .dur = 10.0,
      .lmax = "[[100, 0]]",
  };
  CHECK(solve_obj(line_case(c)) == doctest::Approx(10'500.0));
  // Chronological (initial_status 0): one close at block 1 only.
  constexpr std::string_view fixed_chrono =
      R"(, "startup_cost": 50, "fixed_status": [[1, 0]])";
  const LineCase cc {
      .stage_extra = kPeriodic,
      .lc_extra = fixed_chrono,
      .dur = 10.0,
      .lmax = "[[100, 0]]",
  };
  CHECK(solve_obj(line_case(cc)) == doctest::Approx(10'050.0));
}

TEST_CASE(  // NOLINT
    "Typical period - cyclic min up time wraps around the period")
{
  using namespace typical_period_line_uc_test;
  using typical_period_test::solve_obj;
  // Load only in block 2, the line forced open in block 1 (fixed 0) and
  // free in block 2 (2 = no pin); min up time 2 h on 2 × 1 h blocks.
  // Cyclic: a close at block 2 must hold through block 1 of the next
  // cycle (window wraps), which is pinned open: the line cannot close,
  // block 2 is unserved: 100·10000.  Chronological: the window of the
  // last block is truncated (no row), the line closes (relaxed: u = 0.5
  // carries the 100 MW on tmax 200): 100·10 + 0.5·1.
  constexpr std::string_view up_cyc =
      R"(, "cycle": "stage", "min_up_time": 2, "startup_cost": 1,
           "fixed_status": [[0, 2]])";
  constexpr std::string_view up_chrono =
      R"(, "min_up_time": 2, "startup_cost": 1, "fixed_status": [[0, 2]])";
  CHECK(solve_obj(line_case({
            .stage_extra = kPeriodic,
            .lc_extra = up_cyc,
            .lmax = "[[0, 100]]",
        }))
        == doctest::Approx(1'000'000.0));
  CHECK(solve_obj(line_case({
            .stage_extra = kPeriodic,
            .lc_extra = up_chrono,
            .lmax = "[[0, 100]]",
        }))
        == doctest::Approx(1'000.5));
}

TEST_CASE(  // NOLINT
    "Typical period - LineCommitment.cycle validation")
{
  using namespace typical_period_line_uc_test;
  const auto check = [](std::string_view lc_extra)
  {
    Planning p;
    p.merge(parse_planning_json(
        line_case({.stage_extra = kPeriodic, .lc_extra = lc_extra})));
    return validate_planning(p);
  };
  const auto bad = check(R"(, "cycle": "week")");
  CHECK(
      std::ranges::any_of(bad.errors,
                          [](const std::string& e)
                          { return e.contains("must be 'none' or 'stage'"); }));
  const auto cyc = check(kCycle);
  CHECK(std::ranges::any_of(cyc.warnings,
                            [](const std::string& w)
                            { return w.contains("initial_status"); }));
  const auto plain = check("");
  CHECK_FALSE(std::ranges::any_of(plain.warnings,
                                  [](const std::string& w)
                                  { return w.contains("cycle = 'stage'"); }));
}
