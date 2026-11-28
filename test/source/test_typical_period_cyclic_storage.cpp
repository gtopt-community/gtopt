// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_typical_period_cyclic_storage.cpp
 * @brief     Cyclic (daily_cycle) storages on periodic stages (V2)
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Under `model_options.periodic_storage = true` a `daily_cycle` storage
 * (battery default, or a reservoir declared cyclic) on a periodic stage
 * closes its level within the period and is not a state: its start level
 * is a fresh free column per stage (not the previous stage's end, not
 * `eini`).  With the option off (default) the legacy chain is kept.
 */

#include <algorithm>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/validate_planning.hpp>

#include "typical_period_storage_cases.hpp"

using namespace gtopt;

namespace typical_period_cyclic_test
{
namespace
{

constexpr std::string_view kOptOn = R"(, "periodic_storage": true)";

/// Two typical days (2 × 1 h), dear hour FIRST (100 / 1 $/MWh), load
/// 10 MW, battery emax 20 in day 1 and 5 in day 2.
[[nodiscard]] std::string two_day_json(std::string_view stage_extra,
                                       std::string_view bat_extra,
                                       std::string_view model_extra = kOptOn)
{
  return std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}, {{"uid": 2, "duration": 1}},
                    {{"uid": 3, "duration": 1}}, {{"uid": 4, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 2 {}}},
                    {{"uid": 2, "first_block": 2, "count_block": 2 {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "tp_chain",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[100, 1], [100, 1]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "annual_standing_loss": 0,
                        "emin": 0, "emax": [[20, 20], [5, 5]],
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 20, "daily_cycle": true {}}}]}}
}})",
      typical_period_test::options(model_extra),
      stage_extra,
      stage_extra,
      bat_extra);
}

constexpr std::string_view kPeriodic = R"(, "period_hours": 2)";

[[nodiscard]] bool has_warning(Planning p, std::string_view needle)
{
  const auto res = validate_planning(p);
  return std::ranges::any_of(res.warnings,
                             [needle](const std::string& w)
                             { return w.contains(needle); });
}

}  // namespace
}  // namespace typical_period_cyclic_test

TEST_CASE(  // NOLINT
    "Typical period - daily_cycle start level is free per periodic stage")
{
  using namespace typical_period_cyclic_test;
  using typical_period_test::solve_obj;
  // Periodic: day 1 starts at 10 and discharges into the dear hour
  // (20 $); day 2 starts at 5: 5·100 + 15·1 = 515.  Total 535.
  CHECK(solve_obj(two_day_json(kPeriodic, "")) == doctest::Approx(535.0));
  // Non-periodic (unchanged): eini_2 = efin_1 = eini_1 ≤ 5: 2·515.
  CHECK(solve_obj(two_day_json("", "")) == doctest::Approx(1030.0));

  // `eini` does not pin a periodic start level ...
  constexpr std::string_view eini0 = R"(, "eini": 0)";
  CHECK(solve_obj(two_day_json(kPeriodic, eini0)) == doctest::Approx(535.0));
  // ... but still pins the chronological one: no discharge in the dear
  // hour of day 1 nor (chained) day 2: 2·(10·100 + 10·1).
  CHECK(solve_obj(two_day_json("", eini0)) == doctest::Approx(2020.0));
}

TEST_CASE(  // NOLINT
    "Typical period - periodic_storage off keeps the legacy chain")
{
  using namespace typical_period_cyclic_test;
  using typical_period_test::solve_obj;
  // Option unset / false: the periodic stages chain like chronological
  // ones (eini_2 = efin_1 = eini_1 ≤ 5) and `eini` pins the start.
  for (const std::string_view off :
       {std::string_view {},
        std::string_view {R"(, "periodic_storage": false)"}})
  {
    CAPTURE(off);
    CHECK(solve_obj(two_day_json(kPeriodic, "", off))
          == doctest::Approx(1030.0));
    CHECK(solve_obj(two_day_json(kPeriodic, R"(, "eini": 0)", off))
          == doctest::Approx(2020.0));
  }
}

TEST_CASE(  // NOLINT
    "Typical period - short daily_cycle battery stays cyclic, no state")
{
  using namespace typical_period_test;
  // Default battery (daily_cycle) on the two-stage case: it cannot carry
  // energy from the cheap to the dear stage: 200 + 200·100.
  const TwoStageCase c {.n = 10.0};
  const auto s = solve_two_stage<BatteryLP>(battery_json(c, ""));
  CHECK(s.obj == doctest::Approx(20'200.0));
  CHECK_FALSE(s.efin_is_state);
  for (const auto& lv : s.stages) {
    CHECK(lv.efin == doctest::Approx(lv.eini));  // closes per period
  }
}

TEST_CASE(  // NOLINT
    "Typical period - reservoir declared cyclic closes within the period")
{
  using namespace typical_period_test;
  // daily_cycle reservoir: stage 1 turbines its whole inflow (200, no
  // thermal), stage 2 turbines 200 and burns 400·100.
  for (const bool point : {false, true}) {
    CAPTURE(point);
    const TwoStageCase c {.n = 10.0, .point = point};
    const auto s = solve_two_stage<ReservoirLP>(
        reservoir_json(c, R"(, "daily_cycle": true)"));
    CHECK(s.obj == doctest::Approx(40'000.0));
    CHECK_FALSE(s.efin_is_state);
    for (const auto& lv : s.stages) {
      CHECK(lv.efin == doctest::Approx(lv.eini));
    }
  }
  // The same reservoir as a state storage moves water across stages.
  const TwoStageCase c {.n = 10.0};
  const auto st = solve_two_stage<ReservoirLP>(reservoir_json(c, ""));
  CHECK(st.obj == doctest::Approx(25'150.0));
  CHECK(st.efin_is_state);
}

TEST_CASE(  // NOLINT
    "Typical period - periodic_storage guard-rail warnings")
{
  using namespace typical_period_cyclic_test;
  {
    // daily_cycle battery with eini on periodic stages: eini ignored
    Planning p;
    p.merge(parse_planning_json(two_day_json(kPeriodic, R"(, "eini": 0)")));
    CHECK(has_warning(p, "eini / efin are ignored"));
  }
  {
    // the option is set but no stage is periodic
    Planning p;
    p.merge(parse_planning_json(two_day_json("", "")));
    CHECK(has_warning(p, "has no effect"));
  }
  {
    // option off: no periodic_storage warning
    Planning p;
    p.merge(parse_planning_json(
        two_day_json(kPeriodic, R"(, "eini": 0)", std::string_view {})));
    CHECK_FALSE(has_warning(p, "eini / efin are ignored"));
    CHECK_FALSE(has_warning(p, "has no effect"));
  }
}

TEST_CASE(  // NOLINT
    "Typical period - contradictory storage flags are warned")
{
  using namespace typical_period_test;
  const TwoStageCase c {.n = 10.0};
  const auto warned = [](const std::string& json)
  {
    Planning p;
    p.merge(parse_planning_json(json));
    const auto res = validate_planning(p);
    return std::ranges::any_of(res.warnings,
                               [](const std::string& w)
                               { return w.contains("contradict"); });
  };
  // Reservoir: explicit daily_cycle + use_state_variable
  CHECK(warned(reservoir_json(
      c, R"(, "daily_cycle": true, "use_state_variable": true)")));
  CHECK_FALSE(warned(reservoir_json(c, "")));
  // Battery: daily_cycle defaults to true, so use_state_variable alone
  // is void (the battery stays cyclic) — warned; explicit
  // daily_cycle = false makes it a state storage — not warned.
  CHECK(warned(battery_json(c, R"(, "use_state_variable": true)")));
  CHECK(warned(
      battery_json(c, R"(, "daily_cycle": true, "use_state_variable": true)")));
  CHECK_FALSE(warned(battery_json(
      c, R"(, "daily_cycle": false, "use_state_variable": true)")));
  CHECK_FALSE(warned(battery_json(c, "")));
}

TEST_CASE(  // NOLINT
    "Typical period - battery use_state_variable alone stays cyclic")
{
  using namespace typical_period_test;
  // The default is NOT flipped by use_state_variable: the battery keeps
  // daily_cycle (no state, closes per period) and solves like the
  // default battery (20 200), while daily_cycle = false makes it a
  // state storage (10 300).
  const TwoStageCase c {.n = 10.0};
  const auto s = solve_two_stage<BatteryLP>(
      battery_json(c, R"(, "use_state_variable": true)"));
  CHECK(s.obj == doctest::Approx(20'200.0));
  CHECK_FALSE(s.efin_is_state);
  const auto st = solve_two_stage<BatteryLP>(
      battery_json(c, R"(, "daily_cycle": false, "use_state_variable": true)"));
  CHECK(st.obj == doctest::Approx(10'300.0));
  CHECK(st.efin_is_state);
}
