// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_typical_period_state_storage.cpp
 * @brief     State storages on periodic stages: one-cycle balance,
 *            stage end s + n·Δ_cycle, SDDP state = stage end (V3)
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Under `model_options.periodic_storage = true` a state storage
 * (reservoir, LNG terminal, a battery declared `daily_cycle = false,
 * use_state_variable = true`) is NOT periodic: on a stage standing for
 * n repetitions of its typical period the levels inside the cycle are
 * those of ONE cycle (real Δt), the stage-end state is
 * efin = s + n·(x_{N−1} − s), and the bounds hold on the first and the
 * last (⌈n⌉-th) repetition.  No closure row ties its end to its start.
 */

#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>

#include "typical_period_storage_cases.hpp"

using namespace gtopt;

namespace typical_period_state_test
{
namespace
{

constexpr std::string_view kState =
    R"(, "daily_cycle": false, "use_state_variable": true)";

/// One stage: prices 1 / 100 inside the 2 h period, load 10 MW, 15 MWh
/// state battery, `n` repetitions.
[[nodiscard]] std::string one_stage_json(double n,
                                         bool point,
                                         std::string_view model_extra)
{
  return std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": {} {}}},
                    {{"uid": 2, "duration": {} {}}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 2,
                      "period_hours": 2 {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "tp_state_battery",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[1, 100]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "annual_standing_loss": 0,
                        "emin": 0, "emax": 15, "eini": 0,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 15 {}}}]}}
}})",
      typical_period_test::options(model_extra),
      n,
      point ? R"(, "time": 0.5)" : "",
      n,
      point ? R"(, "time": 1.5)" : "",
      point ? R"(, "sampling": "point")" : "",
      kState);
}

}  // namespace
}  // namespace typical_period_state_test

TEST_CASE(  // NOLINT
    "Typical period - state battery cycle scales linearly with n")
{
  using namespace typical_period_test;
  using namespace typical_period_state_test;
  // Per cycle: charge 10 MWh at 1 $, serve the dear hour: 20 $; n
  // cycles: 20·n.
  constexpr std::string_view on = R"(, "periodic_storage": true)";
  for (const bool point : {false, true}) {
    CAPTURE(point);
    CHECK(solve_obj(one_stage_json(1.0, point, on)) == doctest::Approx(20.0));
    CHECK(solve_obj(one_stage_json(10.0, point, on)) == doctest::Approx(200.0));
    // non-integer n
    CHECK(solve_obj(one_stage_json(4.35, point, on))
          == doctest::Approx(20.0 * 4.35));
  }
}

TEST_CASE(  // NOLINT
    "Typical period - periodic_storage off keeps the stretched balance")
{
  using namespace typical_period_test;
  using namespace typical_period_state_test;
  // Legacy rule: the weighted durations (10 h + 10 h) are one stretched
  // sequence: the 15 MWh battery shifts 15 MWh once:
  // (100 + 15)·1 + (100 − 15)·100 = 8615.
  CHECK(solve_obj(one_stage_json(10.0, false, "")) == doctest::Approx(8'615.0));
  CHECK(solve_obj(one_stage_json(10.0, false, R"(, "periodic_storage": false)"))
        == doctest::Approx(8'615.0));
}

TEST_CASE(  // NOLINT
    "Typical period - large state battery carries energy across stages")
{
  using namespace typical_period_test;
  using namespace typical_period_state_test;
  // Stage 1 cheap, stage 2 dear, 10 repetitions each.  The battery fills
  // to 100 MWh over stage 1 (Δ = 10 per cycle) and empties over stage 2:
  //   stage 1: (200 + 100)·1 = 300;  stage 2: (200 − 100)·100 = 10000.
  for (const bool point : {false, true}) {
    CAPTURE(point);
    const TwoStageCase c {.n = 10.0, .point = point};
    const auto s = solve_two_stage<BatteryLP>(battery_json(c, kState));
    CHECK(s.obj == doctest::Approx(10'300.0));
    CHECK(s.efin_is_state);
    REQUIRE(s.stages.size() == 2);
    // not periodic: the stage end differs from its start
    CHECK(s.stages[0].eini == doctest::Approx(0.0));
    CHECK(s.stages[0].efin == doctest::Approx(100.0));
    CHECK(s.stages[1].eini == doctest::Approx(100.0));
    CHECK(s.stages[1].efin == doctest::Approx(0.0));
    for (const auto& lv : s.stages) {
      check_state_stage(lv, 10.0, 0.0, 100.0);
    }
  }
}

TEST_CASE(  // NOLINT
    "Typical period - reservoir is a state storage on periodic stages")
{
  using namespace typical_period_test;
  // Inflow 20 per cycle (200 per stage).  Stage 1 (load 200 MWh, 1 $)
  // stores 150 (Δ = 15 per cycle) and burns 150 thermal; stage 2 (load
  // 600, 100 $) turbines 200 + 150: thermal 250·100.  150 + 25000.
  for (const bool point : {false, true}) {
    CAPTURE(point);
    const TwoStageCase c {.n = 10.0, .point = point};
    const auto s = solve_two_stage<ReservoirLP>(reservoir_json(c, ""));
    CHECK(s.obj == doctest::Approx(25'150.0));
    CHECK(s.efin_is_state);
    REQUIRE(s.stages.size() == 2);
    CHECK(s.stages[0].efin == doctest::Approx(150.0));
    for (const auto& lv : s.stages) {
      check_state_stage(lv, 10.0, 0.0, 150.0);
    }
  }
}

TEST_CASE(  // NOLINT
    "Typical period - LNG terminal is a state storage on periodic stages")
{
  using namespace typical_period_test;
  // Delivery 200 m³ in stage 1 (20 per cycle at the stage rate), gas
  // unit 1 m³/MWh.  Stage 1 stores 150, burns 50 (thermal 150·1);
  // stage 2 burns 150 (thermal 450·100).  150 + 45000.
  for (const bool point : {false, true}) {
    CAPTURE(point);
    const TwoStageCase c {.n = 10.0, .point = point};
    const auto s = solve_two_stage<LngTerminalLP>(lng_json(c, ""));
    CHECK(s.obj == doctest::Approx(45'150.0));
    CHECK(s.efin_is_state);
    REQUIRE(s.stages.size() == 2);
    CHECK(s.stages[0].efin == doctest::Approx(150.0));
    for (const auto& lv : s.stages) {
      check_state_stage(lv, 10.0, 0.0, 150.0);
    }
  }
}

TEST_CASE(  // NOLINT
    "Typical period - state storage bounds hold on the last repetition")
{
  using namespace typical_period_test;
  using namespace typical_period_state_test;
  // Stage 1 with n = 4.35 (non-integer): the battery may end stage 1 at
  // most at emax, i.e. Δ ≤ 100 / 4.35, and every level of the last
  // (5th) repetition stays ≤ emax.
  const TwoStageCase c {.n = 4.35};
  const auto s = solve_two_stage<BatteryLP>(battery_json(c, kState));
  REQUIRE(s.stages.size() == 2);
  CHECK(s.stages[0].efin <= 100.0 + 1e-6);
  for (const auto& lv : s.stages) {
    check_state_stage(lv, 4.35, 0.0, 100.0);
  }
}

TEST_CASE(  // NOLINT
    "Typical period - 2-phase SDDP of state storages equals monolithic")
{
  using namespace typical_period_test;
  using namespace typical_period_state_test;
  for (const bool point : {false, true}) {
    CAPTURE(point);
    const TwoStageCase mono {.n = 10.0, .point = point};
    const TwoStageCase sddp {
        .n = 10.0,
        .point = point,
        .phases = true,
        .method = "sddp",
    };
    {
      const auto m = solve_two_stage<BatteryLP>(battery_json(mono, kState));
      check_sddp_matches<BatteryLP>(battery_json(sddp, kState), m, "Battery");
    }
    {
      const auto m = solve_two_stage<ReservoirLP>(reservoir_json(mono, ""));
      check_sddp_matches<ReservoirLP>(reservoir_json(sddp, ""), m, "Reservoir");
    }
    {
      const auto m = solve_two_stage<LngTerminalLP>(lng_json(mono, ""));
      check_sddp_matches<LngTerminalLP>(lng_json(sddp, ""), m, "LngTerminal");
    }
  }
}
