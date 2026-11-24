// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_coverage_gaps_rights.cpp
 * @brief     VolumeRight / FlowRight update_lp and stage_uniform paths
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Coverage audit 2026-11-22 found these irrigation-primitive paths with
 * zero hits:
 *
 *  - `VolumeRightLP::update_lp` (volume_right_lp.cpp 550-602): no test
 *    ever re-evaluated a reservoir-volume `bound_rule` to a *different*
 *    value, so the extraction-column re-clamp and its repeat on an
 *    unchanged bound were untested (the FlowRight analogue only asserts
 *    `updated >= 0`).
 *  - `FlowRightLP::add_to_lp` stage_uniform + non-consumptive return flow
 *    (flow_right_lp.cpp 688-697): the qeh column was never credited at
 *    `junction_b`.
 */

#include <algorithm>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/flow_right.hpp>
#include <gtopt/flow_right_lp.hpp>
#include <gtopt/json/json_flow_right.hpp>
#include <gtopt/json/json_volume_right.hpp>
#include <gtopt/junction_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/right_bound_rule.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/volume_right_lp.hpp>

using namespace gtopt;

namespace test_coverage_gaps_rights_ns
{
namespace
{

constexpr double kEini = 1500.0;

/// bound = V (identity) so any change of the average reservoir volume
/// changes the evaluated bound.
[[nodiscard]] RightBoundRule identity_rule()
{
  return RightBoundRule {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 1.0,
                  .constant = 0.0,
              },
          },
  };
}

/// One reservoir drained through a waterway whose flow is FORCED
/// (fmin = fmax), so efin < eini deterministically; no turbine, the
/// electrical side is a plain thermal unit serving a demand.
[[nodiscard]] System make_drain_system(const Array<VolumeRight>& vrs)
{
  return System {
      .name = "CoverageGapsVolumeRight",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .capacity = 50.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 5.0,
                  .capacity = 200.0,
              },
          },
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_up",
              },
              {
                  .uid = Uid {2},
                  .name = "j_down",
                  .drain = true,
              },
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww_forced",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 100.0,
                  .fmax = 100.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv1",
                  .junction = Uid {1},
                  .capacity = 3000.0,
                  .emin = 0.0,
                  .emax = 3000.0,
                  .eini = kEini,
              },
          },
      .volume_right_array = vrs,
  };
}

[[nodiscard]] Simulation make_day_simulation()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 24.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

[[nodiscard]] const JunctionLP* find_junction(const SystemLP& system_lp,
                                              Uid uid)
{
  for (const auto& j : system_lp.elements<JunctionLP>()) {
    if (j.uid() == uid) {
      return &j;
    }
  }
  return nullptr;
}

}  // namespace
}  // namespace test_coverage_gaps_rights_ns

using test_coverage_gaps_rights_ns::find_junction;
using test_coverage_gaps_rights_ns::identity_rule;
using test_coverage_gaps_rights_ns::kEini;
using test_coverage_gaps_rights_ns::make_day_simulation;
using test_coverage_gaps_rights_ns::make_drain_system;

TEST_CASE(
    "VolumeRightLP::update_lp re-clamps extraction to the re-evaluated "
    "reservoir-volume bound")
{
  const Array<VolumeRight> vrs = {
      {
          .uid = Uid {1},
          .name = "vr_rule",
          .emax = 5000.0,
          .eini = 0.0,
          .demand = 10.0,
          .fail_cost = 100.0,
          .use_state_variable = false,
          .bound_rule = identity_rule(),
      },
  };
  const auto system = make_drain_system(vrs);
  const auto simulation = make_day_simulation();
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(system, simulation_lp);

  auto& li = system_lp.linear_interface();
  const auto& s = system_lp.scene().scenarios()[0];
  const auto& t = system_lp.phase().stages()[0];
  const auto& vr_lp = system_lp.elements<VolumeRightLP>().front();
  const auto& rsv_lp = system_lp.elements<ReservoirLP>().front();

  const auto& ext_cols = vr_lp.extraction_cols_at(s, t);
  REQUIRE(ext_cols.size() == 1);
  const auto ext_col = ext_cols.begin()->second;

  // add_to_lp evaluates the rule at the reservoir's eini; the rule
  // yields a volume [hm3] that caps the extraction RATE spread over the
  // whole stage (`rule_rate_cap`).
  CHECK(li.get_col_upp()[ext_col]
        == doctest::Approx(vr_lp.rule_rate_cap(kEini, t)));

  auto r1 = li.resolve();
  REQUIRE(r1.has_value());
  REQUIRE(li.is_optimal());

  const auto sol = li.get_col_sol();
  const double vini = sol[rsv_lp.eini_col_at(s, t)];
  const double vfin = sol[rsv_lp.efin_col_at(s, t)];
  CAPTURE(vini);
  CAPTURE(vfin);
  REQUIRE(vfin < vini - 1.0);  // the forced release really drained it
  // The rule reads the STAGE-START volume (PLP VarMaulePrev / VolIni),
  // not the (vini + vfin) / 2 average: the drained vfin must not move
  // the bound.
  const double expected =
      vr_lp.rule_rate_cap(evaluate_bound_rule(identity_rule(), vini), t);

  const auto updated = system_lp.update_lp();
  CHECK(updated >= 1);
  CHECK(li.get_col_upp()[ext_col] == doctest::Approx(expected));

  SUBCASE("a second update with an unchanged bound re-applies it")
  {
    auto r2 = li.resolve();
    REQUIRE(r2.has_value());
    // The stage-start volume and therefore the bound are identical.  update_lp
    // deliberately does not short-circuit (under low_memory the LP is
    // rebuilt from the snapshot with build-time bounds), so the column
    // is rewritten with the same value.
    CHECK(system_lp.elements<VolumeRightLP>().front().update_lp(system_lp, s, t)
          == 1);
    CHECK(li.get_col_upp()[ext_col] == doctest::Approx(expected));
  }
}

// Regression for a defect found by the 2026-11-22 coverage audit:
// `VolumeRightLP::add_to_lp` clamps the extraction column to
// `min(fmax, rule_rate_cap)`; `update_lp` must re-apply the same `fmax`
// cap instead of writing the raw rule bound after the first SDDP update.
TEST_CASE("VolumeRightLP::update_lp keeps the fmax cap on extraction")
{
  constexpr double fmax_cap = 10.0;
  const Array<VolumeRight> vrs = {
      {
          .uid = Uid {1},
          .name = "vr_rule_fmax",
          .emax = 5000.0,
          .eini = 0.0,
          .demand = 10.0,
          .fmax = fmax_cap,
          .fail_cost = 100.0,
          .use_state_variable = false,
          .bound_rule = identity_rule(),
      },
  };
  const auto system = make_drain_system(vrs);
  const auto simulation = make_day_simulation();
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(system, simulation_lp);

  auto& li = system_lp.linear_interface();
  const auto& s = system_lp.scene().scenarios()[0];
  const auto& t = system_lp.phase().stages()[0];
  const auto& vr_lp = system_lp.elements<VolumeRightLP>().front();
  const auto ext_col = vr_lp.extraction_cols_at(s, t).begin()->second;

  REQUIRE(li.get_col_upp()[ext_col] == doctest::Approx(fmax_cap));
  auto r1 = li.resolve();
  REQUIRE(r1.has_value());
  [[maybe_unused]] const auto updated = system_lp.update_lp();
  CHECK(li.get_col_upp()[ext_col] <= fmax_cap + 1e-9);
}

// FlowRight analogue of the VolumeRight fmax regression above:
// `FlowRightLP::update_lp` re-resolves `[fmin, fmax]` through the same
// `resolve_bounds` as `add_to_lp`, so the re-clamped flow column is
// `min(fmax, rule_bound)` both when fmax binds and when the rule binds.
TEST_CASE("FlowRightLP::update_lp keeps the fmax cap on the flow column")
{
  constexpr double fmax_cap = 10.0;

  const auto run = [&](double slope)
  {
    CAPTURE(slope);
    const RightBoundRule rule {
        .reservoir = Uid {1},
        .segments =
            {
                {
                    .volume = 0.0,
                    .slope = slope,
                    .constant = 0.0,
                },
            },
    };
    auto system = make_drain_system({});
    system.flow_right_array = {
        {
            .uid = Uid {1},
            .name = "fr_rule_fmax",
            .junction_a = Uid {1},
            .fmin = 0.0,
            .fmax = fmax_cap,
            .bound_rule = rule,
        },
    };
    const auto simulation = make_day_simulation();
    const PlanningOptionsLP options;
    SimulationLP simulation_lp(simulation, options);
    SystemLP system_lp(system, simulation_lp);

    auto& li = system_lp.linear_interface();
    const auto& s = system_lp.scene().scenarios()[0];
    const auto& t = system_lp.phase().stages()[0];
    const auto& fr_lp = system_lp.elements<FlowRightLP>().front();
    const auto& rsv_lp = system_lp.elements<ReservoirLP>().front();
    const auto& fcols = fr_lp.flow_cols_at(s, t);
    REQUIRE(fcols.size() == 1);
    const auto fcol = fcols.begin()->second;

    CHECK(li.get_col_upp()[fcol]
          == doctest::Approx(
              std::min(fmax_cap, evaluate_bound_rule(rule, kEini))));

    auto r1 = li.resolve();
    REQUIRE(r1.has_value());
    REQUIRE(li.is_optimal());
    const auto sol = li.get_col_sol();
    const double vini = sol[rsv_lp.eini_col_at(s, t)];
    // The stage's own vfin moved, but the rule reads the STAGE-START
    // volume (PLP VarMaulePrev / VolIni), not (vini + vfin) / 2.
    REQUIRE(sol[rsv_lp.efin_col_at(s, t)] < kEini - 1.0);

    CHECK(system_lp.update_lp() >= 1);
    CHECK(li.get_col_upp()[fcol]
          == doctest::Approx(
              std::min(fmax_cap, evaluate_bound_rule(rule, vini))));
  };

  SUBCASE("fmax binds (rule bound far above fmax)")
  {
    run(1.0);
  }
  SUBCASE("rule binds (rule bound below fmax)")
  {
    run(0.005);
  }
}

TEST_CASE(
    "FlowRight stage_uniform non-consumptive credits qeh at junction_b "
    "in every block")
{
  const Simulation sim {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
              },
              {
                  .uid = Uid {2},
                  .duration = 3.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 2,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
  PlanningOptions popts;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options(popts);

  auto make_sys = [](OptBool consumptive)
  {
    return System {
        .name = "CoverageGapsFlowRightStageUniform",
        .bus_array =
            {
                {
                    .uid = Uid {1},
                    .name = "b1",
                },
            },
        .junction_array =
            {
                {
                    .uid = Uid {1},
                    .name = "j_src",
                },
                {
                    .uid = Uid {2},
                    .name = "j_ret",
                },
                {
                    .uid = Uid {3},
                    .name = "j_drain",
                    .drain = true,
                },
            },
        .waterway_array =
            {
                {
                    .uid = Uid {1},
                    .name = "ww_src",
                    .junction_a = Uid {1},
                    .junction_b = Uid {3},
                    .fmin = 0.0,
                    .fmax = 1000.0,
                },
                {
                    .uid = Uid {2},
                    .name = "ww_ret",
                    .junction_a = Uid {2},
                    .junction_b = Uid {3},
                    .fmin = 0.0,
                    .fmax = 1000.0,
                },
            },
        .flow_right_array =
            {
                {
                    .uid = Uid {1},
                    .name = "fr_su",
                    .junction_a = Uid {1},
                    .direction = -1,
                    .fmin = 5.0,
                    .fmax = 10.0,
                    .flow_mode = "stage_uniform",
                    .junction_b = Uid {2},
                    .consumptive = consumptive,
                },
            },
    };
  };

  SUBCASE("non-consumptive: −1 at j_src and +1 at j_ret per block")
  {
    const auto sys = make_sys(OptBool {false});
    SimulationLP simulation_lp(sim, options);
    SystemLP system_lp(sys, simulation_lp);
    const auto& lp = system_lp.linear_interface();
    const auto& fr_lp = system_lp.elements<FlowRightLP>().front();
    const auto& s = system_lp.scene().scenarios()[0];
    const auto& t = system_lp.phase().stages()[0];

    REQUIRE(fr_lp.has_qeh(s, t));
    CHECK(fr_lp.flow_cols_at(s, t).empty());
    const auto qeh = fr_lp.qeh_col_at(s, t);

    const auto* j_src = find_junction(system_lp, Uid {1});
    const auto* j_ret = find_junction(system_lp, Uid {2});
    REQUIRE(j_src != nullptr);
    REQUIRE(j_ret != nullptr);
    const auto& src_rows = j_src->balance_rows_at(s, t);
    const auto& ret_rows = j_ret->balance_rows_at(s, t);
    REQUIRE(src_rows.size() == 2);
    REQUIRE(ret_rows.size() == 2);
    for (const auto& [buid, row] : src_rows) {
      CAPTURE(buid);
      CHECK(lp.get_coeff(row, qeh) == doctest::Approx(-1.0));
    }
    for (const auto& [buid, row] : ret_rows) {
      CAPTURE(buid);
      CHECK(lp.get_coeff(row, qeh) == doctest::Approx(+1.0));
    }
  }

  SUBCASE("consumptive (default): qeh is not credited at j_ret")
  {
    const auto sys = make_sys(OptBool {});
    SimulationLP simulation_lp(sim, options);
    SystemLP system_lp(sys, simulation_lp);
    const auto& lp = system_lp.linear_interface();
    const auto& fr_lp = system_lp.elements<FlowRightLP>().front();
    const auto& s = system_lp.scene().scenarios()[0];
    const auto& t = system_lp.phase().stages()[0];
    const auto qeh = fr_lp.qeh_col_at(s, t);

    const auto* j_ret = find_junction(system_lp, Uid {2});
    REQUIRE(j_ret != nullptr);
    for (const auto& [buid, row] : j_ret->balance_rows_at(s, t)) {
      CAPTURE(buid);
      CHECK(lp.get_coeff(row, qeh) == doctest::Approx(0.0));
    }
  }
}

// Coverage audit 2026-11-22: the JSON *serialisers* of VolumeRight /
// FlowRight (json_volume_right.hpp 139-169, json_flow_right.hpp
// 130-154) and the `reset_month` parse branch had zero hits.  The
// planning echo written next to the results goes through them, so a
// member-order slip between `json_member_list` and `to_json_data`
// would corrupt every re-run from that echo.
TEST_CASE(
    "VolumeRight / FlowRight JSON round-trip preserves rule and reset fields")
{
  SUBCASE("VolumeRight")
  {
    constexpr std::string_view src = R"({
      "uid": 7, "name": "vr_rt", "reservoir": 1, "emax": 500.0,
      "eini": 12.5, "efin": 3.0, "demand": 10.0, "fail_cost": 100.0,
      "use_state_variable": true, "reset_month": "april",
      "reset_value": 400.0,
      "bound_rule": {"reservoir": 1, "cap": 900.0,
                     "segments": [{"volume": 0.0, "slope": 0.0,
                                   "constant": 570.0},
                                  {"volume": 1200.0, "slope": 0.4,
                                   "constant": 90.0}]}
    })";
    const auto vr = daw::json::from_json<VolumeRight>(src);
    REQUIRE(vr.reset_month.has_value());
    CHECK(vr.reset_month.value_or(MonthType::january) == MonthType::april);

    const auto js = daw::json::to_json(vr);
    const auto back = daw::json::from_json<VolumeRight>(js);
    CHECK(back.uid == vr.uid);
    CHECK(back.name == vr.name);
    CHECK(back.eini.value_or(-1.0) == doctest::Approx(12.5));
    CHECK(back.efin.value_or(-1.0) == doctest::Approx(3.0));
    CHECK(back.use_state_variable.value_or(false));
    CHECK(back.reset_month.value_or(MonthType::january) == MonthType::april);
    CHECK(back.reset_value.value_or(-1.0) == doctest::Approx(400.0));
    REQUIRE(back.bound_rule.has_value());
    const auto rule = back.bound_rule.value_or(RightBoundRule {});
    REQUIRE(rule.segments.size() == 2);
    CHECK(rule.segments[1].slope == doctest::Approx(0.4));
    CHECK(rule.cap.value_or(-1.0) == doctest::Approx(900.0));
    CHECK(evaluate_bound_rule(rule, 1500.0) == doctest::Approx(690.0));
  }

  SUBCASE("FlowRight")
  {
    constexpr std::string_view src = R"({
      "uid": 3, "name": "fr_rt", "junction_a": 1, "direction": -1,
      "fmin": 1.0, "fmax": 10.0, "flow_mode": "stage_uniform",
      "fcost": 50.0, "junction_b": 2, "consumptive": false,
      "bound_rule": {"reservoir": 1,
                     "segments": [{"volume": 0.0, "slope": 0.1,
                                   "constant": 0.0}]}
    })";
    const auto fr = daw::json::from_json<FlowRight>(src);
    const auto js = daw::json::to_json(fr);
    const auto back = daw::json::from_json<FlowRight>(js);
    CHECK(back.uid == fr.uid);
    CHECK(back.flow_mode.value_or("") == "stage_uniform");
    CHECK(back.direction.value_or(0) == -1);
    CHECK_FALSE(back.consumptive.value_or(true));
    CHECK(back.junction_b.has_value());
    REQUIRE(back.bound_rule.has_value());
    CHECK(back.bound_rule.value_or(RightBoundRule {}).segments.size() == 1);
  }
}
