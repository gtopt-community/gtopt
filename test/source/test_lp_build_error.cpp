// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// add_to_lp failure policy (lp_build_error.hpp): every LP-assembly failure
// is a hard `LpBuildError` carrying class / uid / name / scenario / stage
// (and field / value / expected when the element states them).  Also the
// validate_planning rule that a state-carrying storage has one contiguous
// active period: commissioned once, retired once, never switched back on.

#include <algorithm>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

Simulation one_stage_simulation()
{
  return {
      .block_array = {{.uid = Uid {1}, .duration = 1}},
      .stage_array = {{.uid = Uid {1}, .first_block = 0, .count_block = 1}},
      .scenario_array = {{.uid = Uid {0}}},
  };
}

System base_system()
{
  return System {
      .name = "lp_build_error",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {
              {.uid = Uid {1}, .name = "d1", .bus = Uid {1}, .capacity = 50.0},
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 10.0,
                  .capacity = 200.0,
              },
          },
  };
}

/// Build the LP and return the LpBuildError it must raise.
LpBuildErrorInfo build_error(const System& system)
{
  const auto simulation = one_stage_simulation();
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(simulation, options);
  try {
    const SystemLP system_lp(system, simulation_lp);
  } catch (const LpBuildError& err) {
    return err.info();
  }
  FAIL("expected an LpBuildError");
  return {};
}

bool contains(std::string_view haystack, std::string_view needle)
{
  return haystack.contains(needle);
}

bool has_error(const ValidationResult& r, std::string_view needle)
{
  return std::ranges::any_of(
      r.errors, [&](const auto& e) { return contains(e, needle); });
}

}  // namespace

TEST_CASE("format_lp_build_error renders the full context")  // NOLINT
{
  const LpBuildErrorInfo info {
      .class_name = "GasPipe",
      .uid = 7,
      .name = "p7",
      .scenario = 1,
      .stage = 2,
      .block = 3,
      .field = "fmax",
      .value = "-1",
      .expected = "> 0",
      .detail = "pipe capacity",
  };
  CHECK(format_lp_build_error(info)
        == "LP build error in GasPipe uid=7 'p7' (scenario=1, stage=2, "
           "block=3): field 'fmax' = -1, expected > 0: pipe capacity");

  const LpBuildError err {info};
  CHECK(std::string_view {err.what()} == format_lp_build_error(info));
  CHECK(err.info().uid.value_or(0) == 7);

  // Without the optional parts.
  CHECK(format_lp_build_error({.class_name = "Bus", .detail = "boom"})
        == "LP build error in Bus: boom");
}

TEST_CASE("GeneratorProfile without generator capacity fails hard")  // NOLINT
{
  auto system = base_system();
  system.generator_array.push_back({
      .uid = Uid {2},
      .name = "solar",
      .bus = Uid {1},
      .pmax = 100.0,
      .gcost = 0.0,
  });
  system.generator_profile_array = {
      {
          .uid = Uid {5},
          .name = "solar_profile",
          .generator = Uid {2},
          .profile = 0.5,
      },
  };

  const auto info = build_error(system);
  CHECK(info.class_name == "GeneratorProfile");
  CHECK(info.uid.value_or(0) == 5);
  CHECK(info.name == "solar_profile");
  CHECK(info.scenario.value_or(-1) == 0);
  CHECK(info.stage.value_or(-1) == 1);
  CHECK(info.field == "generator.capacity");
  CHECK(info.value == "unset");
  CHECK(contains(info.detail, "generator uid=2"));
}

TEST_CASE("Turbine without a water source fails hard")  // NOLINT
{
  auto system = base_system();
  system.turbine_array = {
      {
          .uid = Uid {3},
          .name = "tur3",
          .generator = Uid {1},
      },
  };

  const auto info = build_error(system);
  CHECK(info.class_name == "Turbine");
  CHECK(info.uid.value_or(0) == 3);
  CHECK(info.name == "tur3");
  CHECK(info.field == "waterway/flow/junction_a");
}

TEST_CASE("A foreign exception is wrapped with the element context")  // NOLINT
{
  // DemandProfile on a demand with fail_segments raises a plain
  // std::runtime_error inside add_to_lp; the driver re-throws it as an
  // LpBuildError naming the element, scenario and stage.
  auto system = base_system();
  system.demand_array.front().fail_segments = {
      {.fraction = 0.5, .cost = 100.0},
      {.fraction = 0.5, .cost = 500.0},
  };
  system.demand_profile_array = {
      {
          .uid = Uid {9},
          .name = "dp9",
          .demand = Uid {1},
          .profile = 0.8,
      },
  };

  const auto info = build_error(system);
  CHECK(info.class_name == "DemandProfile");
  CHECK(info.uid.value_or(0) == 9);
  CHECK(info.name == "dp9");
  CHECK(info.scenario.value_or(-1) == 0);
  CHECK(info.stage.value_or(-1) == 1);
  CHECK(contains(info.detail, "stepwise failure curve"));
}

TEST_CASE(
    "validate_planning: state-carrying storage cannot switch back on")  // NOLINT
{
  Planning planning;
  planning.simulation = {
      .block_array =
          {
              {.uid = Uid {1}, .duration = 1},
              {.uid = Uid {2}, .duration = 1},
              {.uid = Uid {3}, .duration = 1},
          },
      .stage_array =
          {
              {.uid = Uid {10}, .first_block = 0, .count_block = 1},
              {.uid = Uid {20}, .first_block = 1, .count_block = 1},
              {.uid = Uid {30}, .first_block = 2, .count_block = 1},
          },
      .scenario_array = {{.uid = Uid {0}}},
  };
  planning.system.bus_array = {{.uid = Uid {1}, .name = "b1"}};
  planning.system.junction_array = {{.uid = Uid {1}, .name = "j1"}};

  const auto reservoir = [](Uid uid, std::vector<IntBool> active)
  {
    Reservoir r {
        .uid = uid,
        .name = std::format("r{}", uid),
        .junction = Uid {1},
        .emax = 100.0,
        .eini = 50.0,  // required when commissioned after stage 1 (P0-8)
    };
    r.active = std::move(active);
    return r;
  };

  SUBCASE("on -> off -> on is an error, at the re-activation stage")
  {
    planning.system.reservoir_array = {reservoir(1, {True, False, True})};
    const auto r = validate_planning(planning);
    CHECK(has_error(r, "Reservoir uid=1 'r1': state-carrying storage"));
    CHECK(has_error(r, "becomes active again at stage uid=30"));
    CHECK_FALSE(has_error(r, "stage uid=20"));
  }
  SUBCASE("retirement (on -> off) is allowed")
  {
    planning.system.reservoir_array = {reservoir(6, {True, True, False})};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "state-carrying storage"));
  }
  SUBCASE("commissioning then retirement (off -> on -> off) is allowed")
  {
    planning.system.reservoir_array = {reservoir(7, {False, True, False})};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "state-carrying storage"));
  }
  SUBCASE("commissioning (off -> on) is allowed")
  {
    planning.system.reservoir_array = {reservoir(2, {False, True, True})};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "state-carrying storage"));
  }
  SUBCASE("a daily-cycle reservoir may toggle")
  {
    auto res = reservoir(3, {True, False, True});
    res.daily_cycle = true;
    planning.system.reservoir_array = {res};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "state-carrying storage"));
  }
  SUBCASE("a default (daily-cycle) battery may toggle")
  {
    Battery bat {.uid = Uid {4}, .name = "bat4"};
    bat.active = std::vector<IntBool> {True, False, True};
    planning.system.battery_array = {bat};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "state-carrying storage"));
  }
  SUBCASE("a state-carrying battery may be retired")
  {
    Battery bat {.uid = Uid {5}, .name = "bat5"};
    bat.active = std::vector<IntBool> {True, True, False};
    bat.daily_cycle = false;
    bat.use_state_variable = true;
    planning.system.battery_array = {bat};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "state-carrying storage"));
  }
  SUBCASE("a state-carrying battery may not switch back on")
  {
    Battery bat {.uid = Uid {8}, .name = "bat8"};
    bat.active = std::vector<IntBool> {True, False, True};
    bat.daily_cycle = false;
    bat.use_state_variable = true;
    planning.system.battery_array = {bat};
    const auto r = validate_planning(planning);
    CHECK(has_error(r, "Battery uid=8 'bat8'"));
    CHECK(has_error(r, "stage uid=30"));
  }
}

TEST_CASE("Water rights on an inactive source: build backstop")  // NOLINT
{
  // validate_planning rejects these up front; built directly (no
  // validation) the LP raises an LpBuildError naming the right.
  auto system = base_system();
  system.junction_array = {
      {.uid = Uid {1}, .name = "j1", .active = False},
      {.uid = Uid {2}, .name = "j2", .drain = true},
  };

  SUBCASE("FlowRight with inactive junction_a")
  {
    system.flow_right_array = {
        {
            .uid = Uid {4},
            .name = "fr4",
            .junction_a = Uid {1},
            .fmax = 5.0,
        },
    };
    const auto info = build_error(system);
    CHECK(info.class_name == "FlowRight");
    CHECK(info.uid.value_or(0) == 4);
    CHECK(info.field == "junction_a");
  }
  SUBCASE("VolumeRight with inactive reservoir")
  {
    system.junction_array.front().active = True;
    Reservoir rsv {
        .uid = Uid {1},
        .name = "r1",
        .junction = Uid {1},
        .emax = 100.0,
        .eini = 50.0,
    };
    rsv.active = False;
    system.reservoir_array = {rsv};
    system.volume_right_array = {
        {
            .uid = Uid {6},
            .name = "vr6",
            .reservoir = Uid {1},
            .emax = 100.0,
            .eini = 50.0,
            .fmax = 5.0,
        },
    };
    const auto info = build_error(system);
    CHECK(info.class_name == "VolumeRight");
    CHECK(info.uid.value_or(0) == 6);
    CHECK(info.field == "reservoir");
  }
}

TEST_CASE("validate_planning: water rights need an active source")  // NOLINT
{
  Planning planning;
  planning.simulation = {
      .block_array =
          {
              {.uid = Uid {1}, .duration = 1},
              {.uid = Uid {2}, .duration = 1},
          },
      .stage_array =
          {
              {.uid = Uid {10}, .first_block = 0, .count_block = 1},
              {.uid = Uid {20}, .first_block = 1, .count_block = 1},
          },
      .scenario_array = {{.uid = Uid {0}}},
  };
  planning.system.bus_array = {{.uid = Uid {1}, .name = "b1"}};
  Junction ja {.uid = Uid {1}, .name = "ja"};
  ja.active = std::vector<IntBool> {True, False};
  planning.system.junction_array = {ja, {.uid = Uid {2}, .name = "jb"}};
  Reservoir rsv {
      .uid = Uid {1},
      .name = "r1",
      .junction = Uid {2},
      .emax = 100.0,
  };
  rsv.active = std::vector<IntBool> {False, True};  // commissioned
  planning.system.reservoir_array = {rsv};

  SUBCASE("consumptive FlowRight on an inactive junction_a")
  {
    planning.system.flow_right_array = {
        {.uid = Uid {3}, .name = "fr3", .junction_a = Uid {1}},
    };
    const auto r = validate_planning(planning);
    CHECK(has_error(r, "FlowRight uid=3 'fr3': its source junction_a uid=1"));
    CHECK(has_error(r, "stage uid=20"));
  }
  SUBCASE("non-consumptive FlowRight on an inactive junction_a")
  {
    planning.system.flow_right_array = {
        {
            .uid = Uid {4},
            .name = "fr4",
            .junction_a = Uid {1},
            .junction_b = Uid {2},
            .consumptive = false,
        },
    };
    const auto r = validate_planning(planning);
    CHECK(has_error(r, "return flow would create water"));
  }
  SUBCASE("FlowRight inactive together with its junction is valid")
  {
    FlowRight fr {.uid = Uid {5}, .name = "fr5", .junction_a = Uid {1}};
    fr.active = std::vector<IntBool> {True, False};
    planning.system.flow_right_array = {fr};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "FlowRight uid=5"));
  }
  SUBCASE("VolumeRight on a not-yet-commissioned reservoir")
  {
    planning.system.volume_right_array = {
        {.uid = Uid {6}, .name = "vr6", .reservoir = Uid {1}},
    };
    const auto r = validate_planning(planning);
    CHECK(has_error(r, "VolumeRight uid=6 'vr6': its reservoir uid=1"));
    CHECK(has_error(r, "stage uid=10"));
  }
  SUBCASE("VolumeRight commissioned with its reservoir is valid")
  {
    VolumeRight vr {.uid = Uid {7}, .name = "vr7", .reservoir = Uid {1}};
    vr.active = std::vector<IntBool> {False, True};
    vr.eini = 0.0;  // required when commissioned after stage 1 (P0-8)
    planning.system.volume_right_array = {vr};
    const auto r = validate_planning(planning);
    CHECK_FALSE(has_error(r, "VolumeRight uid=7"));
  }
}
