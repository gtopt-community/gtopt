// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
// Per-candidate annuity from overnight cost (overnight_capcost /
// economic_life / wacc) and the lossless expandable-line capacity rows.
#include <cmath>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/capital_recovery.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "require_value.hpp"

using namespace gtopt;

namespace capital_recovery_test
{
namespace
{

constexpr double k_hours = 8760.0;

/// One bus, one 8760-h block, 100 MW load at 1000 $/MWh VOLL, an
/// existing 200 $/MWh unit and the candidate generators @p candidates
/// (a JSON array body).  `scale_objective = 1`, so the objective is in $.
std::string gen_case(const std::string& candidates, double social_rate = 0.0)
{
  return R"json({
  "options": {
    "model_options": {
      "use_single_bus": true, "scale_objective": 1,
      "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "annual_discount_rate": )json"
      + std::to_string(social_rate) + R"json(,
    "block_array": [ {"uid": 1, "duration": 8760} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 1} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "crf",
    "bus_array": [ {"uid": 1, "name": "b1"} ],
    "demand_array": [ {"uid": 1, "name": "d1", "bus": "b1", "lmax": 100} ],
    "generator_array": [
      {"uid": 1, "name": "old", "bus": "b1", "gcost": 200, "capacity": 100},
      )json"
      + candidates + R"json(
    ]
  }
})json";
}

struct Solved
{
  double obj {0.0};
  std::vector<double> capainst;  ///< per generator, 0 when not expandable
};

Solved solve(const std::string& json)
{
  auto planning = daw::json::from_json<Planning>(json);
  PlanningLP planning_lp(std::move(planning));
  const auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  auto& system_lp = planning_lp.systems().front().front();
  auto& li = system_lp.linear_interface();
  Solved out {.obj = li.get_obj_value(), .capainst = {}};
  const auto sol = li.get_col_sol();
  const auto& stage = planning_lp.simulation().stages().front();
  for (const auto& gen : system_lp.elements<GeneratorLP>()) {
    const auto col = gen.capacity_col_at(stage);
    out.capainst.push_back(col ? sol[*col] : 0.0);
  }
  return out;
}

}  // namespace
}  // namespace capital_recovery_test

using namespace capital_recovery_test;

TEST_CASE("capital_recovery_factor closed forms")  // NOLINT
{
  // CRF(8 %, 30 y) = 0.08 · 1.08^30 / (1.08^30 − 1).
  const double g = std::pow(1.08, 30.0);
  CHECK(capital_recovery_factor(0.08, 30.0)
        == doctest::Approx(0.08 * g / (g - 1.0)));
  CHECK(capital_recovery_factor(0.08, 30.0)
        == doctest::Approx(0.0888274).epsilon(1e-6));

  SUBCASE("zero rate is straight-line")
  {
    CHECK(capital_recovery_factor(0.0, 20.0) == doctest::Approx(0.05));
  }
  SUBCASE("no life is a perpetuity")
  {
    CHECK(capital_recovery_factor(0.06, std::nullopt) == doctest::Approx(0.06));
    CHECK(capital_recovery_factor(0.06, 0.0) == doctest::Approx(0.06));
    CHECK(capital_recovery_factor(0.0, std::nullopt) == doctest::Approx(0.0));
  }
  SUBCASE("a higher rate or a shorter life raises the annuity")
  {
    CHECK(capital_recovery_factor(0.10, 25.0)
          > capital_recovery_factor(0.06, 25.0));
    CHECK(capital_recovery_factor(0.08, 15.0)
          > capital_recovery_factor(0.08, 40.0));
  }
  SUBCASE("annuity_from_overnight scales linearly")
  {
    CHECK(annuity_from_overnight(1.0e6, 0.08, 30.0)
          == doctest::Approx(1.0e6 * capital_recovery_factor(0.08, 30.0)));
    CHECK(capital_recovery_factor(-1.5, 10.0) == doctest::Approx(0.0));
  }
}

TEST_CASE(
    "overnight_capcost + wacc reproduces the annual_capcost LP")  // NOLINT
{
  // The candidate (10 $/MWh) replaces the 200 $/MWh unit: 100 MW built.
  const double annual = 1.0e6 * capital_recovery_factor(0.09, 25.0);
  const auto by_annual = solve(gen_case(
      R"({"uid": 2, "name": "new", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 10, "expmod": 50,
          "annual_capcost": )"
      + std::to_string(annual) + "}"));
  const auto by_overnight = solve(gen_case(
      R"({"uid": 2, "name": "new", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 10, "expmod": 50,
          "overnight_capcost": 1000000, "economic_life": 25,
          "wacc": 0.09})"));
  const double expected = (100.0 * 10.0 * k_hours) + (100.0 * annual);
  CHECK(by_annual.obj == doctest::Approx(expected).epsilon(1e-9));
  CHECK(by_overnight.obj == doctest::Approx(by_annual.obj).epsilon(1e-9));
  REQUIRE(by_overnight.capainst.size() == 2);
  CHECK(by_overnight.capainst[1] == doctest::Approx(100.0));
}

TEST_CASE("wacc unset falls back to the social annual_discount_rate")  // NOLINT
{
  // Single stage at t = 0: the discount factor is 1 for any rate, so the
  // rate only enters through the annuity.
  const std::string cand = R"({"uid": 2, "name": "new", "bus": "b1",
      "gcost": 10, "capacity": 0, "expcap": 10, "expmod": 50,
      "overnight_capcost": 1000000, "economic_life": 25})";
  const auto social = solve(gen_case(cand, /*social_rate=*/0.05));
  const double annual = 1.0e6 * capital_recovery_factor(0.05, 25.0);
  CHECK(social.obj
        == doctest::Approx((100.0 * 10.0 * k_hours) + (100.0 * annual))
               .epsilon(1e-9));
}

TEST_CASE("annual_capcost takes precedence over overnight_capcost")  // NOLINT
{
  const auto both = solve(gen_case(
      R"({"uid": 2, "name": "new", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 10, "expmod": 50,
          "annual_capcost": 50000, "overnight_capcost": 9000000,
          "economic_life": 25, "wacc": 0.30})"));
  CHECK(both.obj
        == doctest::Approx((100.0 * 10.0 * k_hours) + (100.0 * 50000.0))
               .epsilon(1e-9));
}

TEST_CASE(
    "two candidates differing only in wacc: the cheaper one wins")  // NOLINT
{
  const auto cands = [](double wacc_a, double wacc_b)
  {
    return R"({"uid": 2, "name": "a", "bus": "b1", "gcost": 10,
               "capacity": 0, "expcap": 10, "expmod": 50,
               "overnight_capcost": 1000000, "economic_life": 25,
               "wacc": )"
        + std::to_string(wacc_a) + R"(},
             {"uid": 3, "name": "b", "bus": "b1", "gcost": 10,
               "capacity": 0, "expcap": 10, "expmod": 50,
               "overnight_capcost": 1000000, "economic_life": 25,
               "wacc": )"
        + std::to_string(wacc_b) + "}";
  };

  SUBCASE("a at 6 %, b at 10 %")
  {
    const auto s = solve(gen_case(cands(0.06, 0.10)));
    REQUIRE(s.capainst.size() == 3);
    CHECK(s.capainst[1] == doctest::Approx(100.0));
    CHECK(s.capainst[2] == doctest::Approx(0.0));
  }
  SUBCASE("a at 10 %, b at 6 %")
  {
    const auto s = solve(gen_case(cands(0.10, 0.06)));
    REQUIRE(s.capainst.size() == 3);
    CHECK(s.capainst[1] == doctest::Approx(0.0));
    CHECK(s.capainst[2] == doctest::Approx(100.0));
  }
  SUBCASE("equal annuities: a tie, only the total is determined")
  {
    // Identical candidates are a degenerate LP: any split of the
    // 100 MW is optimal and the solver picks one.  The objective and
    // the total built are unique.
    const auto s = solve(gen_case(cands(0.08, 0.08)));
    REQUIRE(s.capainst.size() == 3);
    CHECK(s.capainst[1] + s.capainst[2] == doctest::Approx(100.0));
    const double annual = 1.0e6 * capital_recovery_factor(0.08, 25.0);
    CHECK(s.obj
          == doctest::Approx((100.0 * 10.0 * k_hours) + (100.0 * annual))
                 .epsilon(1e-9));
  }
}

TEST_CASE("small modules at a high rate vs one large module")  // NOLINT
{
  // Many small modules (10 MW each, 9 %) against one large 100 MW block
  // with economies of scale (lower overnight cost, 7 %).  Same energy
  // cost, so the cheaper annuity per MW wins in the LP.
  const auto s = solve(gen_case(
      R"({"uid": 2, "name": "small", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 10, "expmod": 20,
          "overnight_capcost": 1200000, "economic_life": 25, "wacc": 0.09},
        {"uid": 3, "name": "large", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 100, "expmod": 1,
          "overnight_capcost": 1000000, "economic_life": 25,
          "wacc": 0.07})"));
  REQUIRE(s.capainst.size() == 3);
  CHECK(s.capainst[1] == doctest::Approx(0.0));
  CHECK(s.capainst[2] == doctest::Approx(100.0));
}

TEST_CASE("Line overnight_capcost and lossless expansion capacity")  // NOLINT
{
  // b1 has a 10 $/MWh unit, b2 a 100 MW load and a 200 $/MWh unit.  The
  // lossless line (no resistance → no loss model) starts at 0 MW and can
  // be expanded: the capacity rows must cap the flow at capainst.  The
  // line annuity per MW-h is below the 190 $/MWh spread, so 100 MW are
  // built and carried.
  const std::string json = R"json({
  "options": {
    "model_options": {
      "use_single_bus": false, "use_kirchhoff": false,
      "scale_objective": 1, "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "block_array": [ {"uid": 1, "duration": 8760} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 1} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "line_crf",
    "bus_array": [ {"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"} ],
    "demand_array": [ {"uid": 1, "name": "d2", "bus": "b2", "lmax": 100} ],
    "generator_array": [
      {"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10, "capacity": 300},
      {"uid": 2, "name": "dear", "bus": "b2", "gcost": 200, "capacity": 300}
    ],
    "line_array": [
      {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
       "tmax_ab": 500, "tmax_ba": 500,
       "capacity": 0, "expcap": 25, "expmod": 8,
       "overnight_capcost": 500000, "economic_life": 40, "wacc": 0.08}
    ]
  }
})json";
  auto planning = daw::json::from_json<Planning>(json);
  PlanningLP planning_lp(std::move(planning));
  REQUIRE(planning_lp.resolve().has_value());
  auto& system_lp = planning_lp.systems().front().front();
  auto& li = system_lp.linear_interface();
  const auto& stage = planning_lp.simulation().stages().front();
  const auto& line = system_lp.elements<LineLP>().front();
  const auto cap_col = line.capacity_col_at(stage);
  REQUIRE(cap_col.has_value());
  const auto sol = li.get_col_sol();
  CHECK(sol[test_support::require_value(cap_col)] == doctest::Approx(100.0));
  const double annual = 500000.0 * capital_recovery_factor(0.08, 40.0);
  CHECK(li.get_obj_value()
        == doctest::Approx((100.0 * 10.0 * k_hours) + (100.0 * annual))
               .epsilon(1e-9));

  SUBCASE("fixed O&M is charged on the built corridor")
  {
    auto p3 = daw::json::from_json<Planning>(json);
    p3.system.line_array.front().fixed_om_cost = OptTRealFieldSched {20000.0};
    PlanningLP lp3(std::move(p3));
    REQUIRE(lp3.resolve().has_value());
    CHECK(lp3.systems().front().front().linear_interface().get_obj_value()
          == doctest::Approx((100.0 * 10.0 * k_hours)
                             + (100.0 * (annual + 20000.0)))
                 .epsilon(1e-9));
  }

  SUBCASE("too expensive a line is not built: the load pays 200 $/MWh")
  {
    auto p2 = daw::json::from_json<Planning>(json);
    p2.system.line_array.front().overnight_capcost = OptTRealFieldSched {1.0e8};
    PlanningLP lp2(std::move(p2));
    REQUIRE(lp2.resolve().has_value());
    auto& s2 = lp2.systems().front().front();
    const auto& st2 = lp2.simulation().stages().front();
    const auto col2 = s2.elements<LineLP>().front().capacity_col_at(st2);
    REQUIRE(col2.has_value());
    CHECK(s2.linear_interface().get_col_sol()[test_support::require_value(col2)]
          == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(s2.linear_interface().get_obj_value()
          == doctest::Approx(100.0 * 200.0 * k_hours).epsilon(1e-9));
  }
}

TEST_CASE("Battery power_overnight_capcost reaches the converter")  // NOLINT
{
  // Two 1-h blocks: cheap 10 $/MWh then 100 $/MWh.  A lossless battery
  // with fixed 100 MWh energy and an expandable power axis; the power
  // annuity comes from power_overnight_capcost at the battery's wacc
  // and economic_life, and must equal the power_annual_capcost run.
  const auto bat_json = [](const std::string& power_cost)
  {
    return R"json({
  "options": {
    "model_options": {
      "use_single_bus": true, "scale_objective": 1,
      "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "block_array": [ {"uid": 1, "duration": 1}, {"uid": 2, "duration": 1} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 2} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "bat_crf",
    "bus_array": [ {"uid": 1, "name": "b1"} ],
    "demand_array": [ {"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[0, 50]]} ],
    "generator_array": [
      {"uid": 1, "name": "cheap", "bus": "b1", "gcost": [[10, 1000]],
       "capacity": 200},
      {"uid": 2, "name": "dear", "bus": "b1", "gcost": 100, "capacity": 200}
    ],
    "battery_array": [
      {"uid": 1, "name": "bess", "bus": "b1",
       "input_efficiency": 1, "output_efficiency": 1,
       "annual_standing_loss": 0, "emin": 0, "emax": 100, "eini": 0,
       "capacity": 100, "pmax_charge": 500, "pmax_discharge": 500,
       "power_capacity": 0, "power_expcap": 10, "power_expmod": 20,
       "economic_life": 15, "wacc": 0.08, )json"
        + power_cost + R"json(}
    ]
  }
})json";
  };
  const double annual = 100000.0 * capital_recovery_factor(0.08, 15.0);
  auto p1 = daw::json::from_json<Planning>(
      bat_json("\"power_annual_capcost\": " + std::to_string(annual)));
  auto p2 = daw::json::from_json<Planning>(
      bat_json("\"power_overnight_capcost\": 1e5"));
  PlanningLP lp1(std::move(p1));
  PlanningLP lp2(std::move(p2));
  REQUIRE(lp1.resolve().has_value());
  REQUIRE(lp2.resolve().has_value());
  const double obj1 =
      lp1.systems().front().front().linear_interface().get_obj_value();
  const double obj2 =
      lp2.systems().front().front().linear_interface().get_obj_value();
  CHECK(obj2 == doctest::Approx(obj1).epsilon(1e-9));
  // power_fixed_om_cost is forwarded to the converter and charged with
  // the power annuity.
  auto p3 = daw::json::from_json<Planning>(bat_json(
      R"("power_overnight_capcost": 1e5, "power_fixed_om_cost": 5000)"));
  auto p4 = daw::json::from_json<Planning>(
      bat_json("\"power_annual_capcost\": " + std::to_string(annual + 5000.0)));
  PlanningLP lp3(std::move(p3));
  PlanningLP lp4(std::move(p4));
  REQUIRE(lp3.resolve().has_value());
  REQUIRE(lp4.resolve().has_value());
  CHECK(lp3.systems().front().front().linear_interface().get_obj_value()
        == doctest::Approx(
               lp4.systems().front().front().linear_interface().get_obj_value())
               .epsilon(1e-9));
  // The power capex is paid over the 2-h stage: 50 MW · annual · 2/8760,
  // far below the 90 $/MWh spread on 50 MWh, so 50 MW are built.
  CHECK(obj1
        == doctest::Approx((50.0 * 10.0) + (50.0 * annual * 2.0 / k_hours))
               .epsilon(1e-6));
}

TEST_CASE("overnight_capcost fields round-trip through JSON")  // NOLINT
{
  const auto planning = daw::json::from_json<Planning>(gen_case(
      R"({"uid": 2, "name": "new", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 10, "expmod": 50,
          "overnight_capcost": 1000000, "economic_life": 25,
          "wacc": 0.09})"));
  const auto text = daw::json::to_json(planning);
  CHECK(text.find("\"overnight_capcost\"") != std::string::npos);
  CHECK(text.find("\"economic_life\"") != std::string::npos);
  CHECK(text.find("\"wacc\"") != std::string::npos);
  const auto back = daw::json::from_json<Planning>(text);
  const auto& gen = back.system.generator_array.back();
  CHECK(gen.wacc.has_value());
  CHECK(gen.economic_life.has_value());
  CHECK(gen.overnight_capcost.has_value());
}

namespace
{
/// Objective of a one-bus, one 8760-h block case: 100 MW of supply at
/// 20 $/MWh and an expandable `desal` demand worth 300 $/MWh served
/// (`fcost`), up to 50 MW in 10-MW modules; @p cost is its capex JSON.
double desal_obj(const std::string& cost, double social_rate)
{
  auto planning = daw::json::from_json<Planning>(std::string {R"json({
  "options": {"model_options": {"use_single_bus": true, "scale_objective": 1,
                                "demand_fail_cost": 1000}},
  "simulation": {
    "annual_discount_rate": )json"} + std::to_string(social_rate)
                                                 + R"json(,
    "block_array": [ {"uid": 1, "duration": 8760} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 1} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "desal",
    "bus_array": [ {"uid": 1, "name": "b1"} ],
    "generator_array": [
      {"uid": 1, "name": "g", "bus": "b1", "gcost": 20, "capacity": 100}
    ],
    "demand_array": [
      {"uid": 1, "name": "desal", "bus": "b1", "lmax": 50, "fcost": 300,
       "capacity": 0, "expcap": 10, "expmod": 5, )json"
                                                 + cost + R"json(}
    ]
  }
})json");
  PlanningLP lp(std::move(planning));
  REQUIRE(lp.resolve().has_value());
  return lp.systems().front().front().linear_interface().get_obj_value();
}
}  // namespace

TEST_CASE("Demand overnight_capcost without wacc uses the social rate")
{
  // Public project: no wacc, so the annuity is at the 6 % social rate.
  // 1.5 M$/MW over 25 y -> 117,341 $/MW-yr < (300 - 20)·8760 margin, so
  // the full 50 MW are built.
  const double annual = 1.5e6 * capital_recovery_factor(0.06, 25.0);
  const double by_annual =
      desal_obj("\"annual_capcost\": " + std::to_string(annual), 0.06);
  const double by_overnight =
      desal_obj(R"("overnight_capcost": 1.5e6, "economic_life": 25)", 0.06);
  CHECK(by_overnight == doctest::Approx(by_annual).epsilon(1e-9));
  // served 50 MW: energy 50·20·8760 + capex 50·annual (no curtailment)
  CHECK(by_annual
        == doctest::Approx((50.0 * 20.0 * k_hours) + (50.0 * annual))
               .epsilon(1e-6));
  // An explicit wacc overrides the social rate.
  const double at_10 = desal_obj(
      R"("overnight_capcost": 1.5e6, "economic_life": 25, "wacc": 0.10)", 0.06);
  CHECK(at_10 > by_overnight);
}

TEST_CASE("CarrierConverter overnight_capcost equals annual_capcost")
{
  // Electrolyser: 1 MWh_e -> 0.7 MWh_LHV, hydrogen worth 100 $/MWh_LHV
  // (net 70 - 20 = 50 $/MWh_e margin), up to 40 MW in 10-MW modules.
  const auto run = [](const std::string& cost)
  {
    auto planning = daw::json::from_json<Planning>(std::string {R"json({
  "options": {"model_options": {"use_single_bus": true, "scale_objective": 1}},
  "simulation": {
    "annual_discount_rate": 0.05,
    "block_array": [ {"uid": 1, "duration": 8760} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 1} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "ely",
    "bus_array": [ {"uid": 1, "name": "b1"} ],
    "generator_array": [
      {"uid": 1, "name": "g", "bus": "b1", "gcost": 20, "capacity": 100}
    ],
    "hydrogen_node_array": [ {"uid": 1, "name": "h2"} ],
    "hydrogen_demand_array": [
      {"uid": 1, "name": "off", "hydrogen_node": "h2", "lmax": 100,
       "price": 100}
    ],
    "carrier_converter_array": [
      {"uid": 1, "name": "ely", "from_carrier": "electric",
       "to_carrier": "hydrogen", "from_node": "b1", "to_node": "h2",
       "efficiency": 0.7, "capacity": 0, "expcap": 10, "expmod": 4, )json"}
                                                   + cost + R"json(}
    ]
  }
})json");
    PlanningLP lp(std::move(planning));
    REQUIRE(lp.resolve().has_value());
    return lp.systems().front().front().linear_interface().get_obj_value();
  };
  const double annual = 1.0e6 * capital_recovery_factor(0.09, 20.0);
  const double by_annual = run("\"annual_capcost\": " + std::to_string(annual));
  const double by_overnight =
      run(R"("overnight_capcost": 1e6, "economic_life": 20, "wacc": 0.09)");
  CHECK(by_overnight == doctest::Approx(by_annual).epsilon(1e-9));
  // 109,546 $/MW-yr annuity < 50·8760 margin: all 40 MW built and run.
  CHECK(by_annual
        == doctest::Approx((40.0 * 20.0 * k_hours) - (28.0 * 100.0 * k_hours)
                           + (40.0 * annual))
               .epsilon(1e-6));
  // Fixed O&M is charged with the annuity.
  const double with_fom = run(R"("overnight_capcost": 1e6, "economic_life": 20,
                                 "wacc": 0.09, "fixed_om_cost": 15000)");
  CHECK(with_fom
        == doctest::Approx(
               run("\"annual_capcost\": " + std::to_string(annual + 15000.0)))
               .epsilon(1e-9));
}

// ── fixed O&M of expansion capacity ─────────────────────────────────────

TEST_CASE("fixed_om_cost is charged with the annuity")  // NOLINT
{
  // Candidate as in the overnight test: 100 MW built.  Fixed O&M F is a
  // yearly cost of each MW built, so annuity K + F must give the same LP
  // as an annual_capcost of K + F.
  const double k = 1.0e6 * capital_recovery_factor(0.09, 25.0);
  const double f = 20000.0;
  const auto cand = [](const std::string& cost)
  {
    return R"({"uid": 2, "name": "new", "bus": "b1", "gcost": 10,
               "capacity": 0, "expcap": 10, "expmod": 50, )"
        + cost + "}";
  };
  const auto with_fom = solve(gen_case(cand(
      R"("overnight_capcost": 1000000, "economic_life": 25, "wacc": 0.09,
         "fixed_om_cost": 20000)")));
  const auto summed =
      solve(gen_case(cand("\"annual_capcost\": " + std::to_string(k + f))));
  const auto no_fom = solve(gen_case(cand(
      R"("overnight_capcost": 1000000, "economic_life": 25, "wacc": 0.09)")));

  CHECK(with_fom.obj == doctest::Approx(summed.obj).epsilon(1e-9));
  CHECK(with_fom.obj
        == doctest::Approx((100.0 * 10.0 * k_hours) + (100.0 * (k + f)))
               .epsilon(1e-9));
  CHECK(with_fom.obj - no_fom.obj == doctest::Approx(100.0 * f).epsilon(1e-6));

  SUBCASE("alongside annual_capcost too")
  {
    const auto both =
        solve(gen_case(cand("\"annual_capcost\": " + std::to_string(k)
                            + R"(, "fixed_om_cost": 20000)")));
    CHECK(both.obj == doctest::Approx(summed.obj).epsilon(1e-9));
  }
  SUBCASE("unset or zero fixed O&M leaves the objective unchanged")
  {
    const auto zero = solve(gen_case(cand(
        R"("overnight_capcost": 1000000, "economic_life": 25, "wacc": 0.09,
           "fixed_om_cost": 0)")));
    CHECK(zero.obj == doctest::Approx(no_fom.obj).epsilon(1e-12));
  }
  SUBCASE("stage schedule: only the stage value applies")
  {
    const auto sched = solve(gen_case(cand(
        R"("overnight_capcost": 1000000, "economic_life": 25, "wacc": 0.09,
           "fixed_om_cost": [20000])")));
    CHECK(sched.obj == doctest::Approx(with_fom.obj).epsilon(1e-9));
  }
}

TEST_CASE("zero profit with fixed O&M: margin = annuity + fixed O&M")  // NOLINT
{
  // One 8760-h block: the price is the marginal cost of one more MW of
  // load, ΔObj / 8760.  The built unit's margin (λ − c)·q·8760 must pay
  // its annuity plus its fixed O&M exactly.
  const double k = 1.0e6 * capital_recovery_factor(0.09, 25.0);
  const double f = 20000.0;
  const std::string cand =
      R"({"uid": 2, "name": "new", "bus": "b1", "gcost": 10,
          "capacity": 0, "expcap": 10, "expmod": 50,
          "overnight_capcost": 1000000, "economic_life": 25,
          "wacc": 0.09, "fixed_om_cost": 20000})";
  auto json100 = gen_case(cand);
  auto json101 = json100;
  const std::string from = "\"lmax\": 100";
  json101.replace(json101.find(from), from.size(), "\"lmax\": 101");
  const auto s100 = solve(json100);
  const auto s101 = solve(json101);
  const double lambda = (s101.obj - s100.obj) / k_hours;
  REQUIRE(s100.capainst.size() == 2);
  const double q = s100.capainst[1];
  CHECK(q == doctest::Approx(100.0));
  const double margin = (lambda - 10.0) * q * k_hours;
  CHECK(margin == doctest::Approx(q * (k + f)).epsilon(1e-6));
}

TEST_CASE("fixed_om_cost on Line, Demand and CarrierConverter")  // NOLINT
{
  SUBCASE("Demand (public desalination plant)")
  {
    const double annual = 1.5e6 * capital_recovery_factor(0.06, 25.0);
    const double with_fom = desal_obj(
        R"("overnight_capcost": 1.5e6, "economic_life": 25,
           "fixed_om_cost": 30000)",
        0.06);
    const double summed = desal_obj(
        "\"annual_capcost\": " + std::to_string(annual + 30000.0), 0.06);
    CHECK(with_fom == doctest::Approx(summed).epsilon(1e-9));
  }
}
