// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_penalty_sink_battery.cpp — non-line energy sinks under a penalty
// incentive: battery round-trip losses and zero-value demand.
//
// Companion of test_line_losses_penalty_sink.cpp (line-loss sinks).
// Single bus (no network losses), one 1 h block:
//
//   g1 (10 $/MWh), res (5 MW), d1 = 80 MW,
//   bess: 100 MW charge / 100 MW discharge, η = 0.9 each way
//         (round trip η² = 0.81), SoC closed over the block (efin = eini).
//
// Two penalty incentives:
//   * "down": DOWN-reserve requirement above the load, short priced at
//     10 000 $/MW.  The provision row is `p − dr ≥ 0`, so the zone forces
//     g1 ≥ drreq: every MW destroyed cures 1 MW of shortage
//     (incentive 9 990 $/MWh, bus price −9 990).
//   * "cpf": UP-reserve requirement falling with generation,
//     R ≥ alpha − β·D, D = Σ generation − Σ battery charge
//     (`load_term: endogenous_generation`), incentive 134 $/MWh.
//
// Measured (burned MW = g1 + res − served d1):
//
//   sink                                   down           cpf
//   none                                     0   λ=−9990    0  λ=−134
//   battery, power envelope ON (default)  10.50  λ=−9990    0  λ=−134
//   battery, power envelope OFF           19.00  λ=−9990    0  λ=−134
//   battery, cycle cost 1 $/MWh           10.50  λ=−9990    0
//   battery, cycle cost 6000 $/MWh           0   λ=−9990    0
//   battery, 5 MW surplus, cost 0             5  λ=0
//   battery, 5 MW surplus, cost 1             5  λ=−9.53
//   demand with fcost = 0, lmax 50           20  λ=0       50  λ=−134
//
// Reading:
//   1. A lossy battery charging AND discharging in the same block burns
//      (1 − η²)·c.  The `power_envelope` row (converter_lp.cpp,
//      p_c/Pc + p_d/Pd ≤ 1) only halves the cap, from (1 − η²)·P = 19 MW
//      to (1 − η²)/(1 + η²)·P = 10.5 MW; nothing forbids partial
//      simultaneity in pure LP.
//   2. charge/discharge costs behave like `loss_cost_eps`: a price FLOOR
//      at −cost·(1 + η²)/(1 − η²) while the sink is interior, a blocker
//      only above incentive·(1 − η²)/(1 + η²).
//   3. Under the CPF incentive the battery is NOT a sink: the charge is
//      subtracted from D, so cycling leaves D = served load.  A plain
//      demand is not subtracted: any load with zero curtailment value is
//      a sink for both incentives.
//
// DEFECT tests assert the defect IS present; when one starts failing
// the sink was closed and the test should be inverted into a guard.

#include <cmath>
#include <cstddef>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{
namespace penalty_sink_battery_test
{

constexpr double kBatLoad = 80.0;
constexpr double kBatGcost = 10.0;
constexpr double kBatPenalty = 10000.0;
constexpr double kBatBeta = 0.0144;
constexpr double kBatPbat = 100.0;
constexpr double kBatEta = 0.9;
constexpr double kBatRoundTrip = kBatEta * kBatEta;  ///< 0.81
constexpr Uid kBatDumpUid {77};

struct BatCase
{
  /// "cpf": up-reserve requirement falling with Σ generation − charge.
  /// "down": down-reserve requirement `surplus` MW above the load.
  std::string_view incentive {"down"};
  double surplus {20.0};
  bool envelope {true};
  double cycle_cost {0.0};  ///< charge_cost = discharge_cost [$/MWh]
  bool with_battery {true};
  double dump_lmax {0.0};  ///< extra demand with fcost = 0
};

[[nodiscard]] std::string bat_json(const BatCase& c)
{
  const std::string zone = c.incentive == "cpf"
      ? std::format(
            R"("reserve_zone_array": [{{"uid": 1, "name": "cpf",
      "urcost": {}, "urreq_planes": [{{"alpha": 20, "demand_coef": {}}}],
      "demand_load_factor": 1.0, "load_term": "endogenous_generation"}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp", "generator": "res",
      "reserve_zones": [1], "urmax": 5, "ur_provision_factor": 1}}])",
            kBatPenalty,
            -kBatBeta)
      : std::format(
            R"("reserve_zone_array": [{{"uid": 1, "name": "down",
      "drcost": {}, "drreq": {}}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp", "generator": "g1",
      "reserve_zones": [1], "drmax": 1000, "dr_provision_factor": 1}}])",
            kBatPenalty,
            kBatLoad + c.surplus);
  const std::string battery = c.with_battery
      ? std::format(
            R"("battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
      "input_efficiency": {}, "output_efficiency": {}, "emin": 0,
      "emax": 400, "eini": 200, "pmax_charge": {}, "pmax_discharge": {},
      "charge_cost": {}, "discharge_cost": {}, "capacity": 400,
      "annual_standing_loss": 0, "daily_cycle": false,
      "use_state_variable": false}}],
    )",
            kBatEta,
            kBatEta,
            kBatPbat,
            kBatPbat,
            c.cycle_cost,
            c.cycle_cost)
      : std::string {};
  const std::string dump = c.dump_lmax > 0.0 ? std::format(
                                                   R"(,
      {{"uid": {}, "name": "dump", "bus": 1, "lmax": {}, "fcost": 0}})",
                                                   kBatDumpUid,
                                                   c.dump_lmax)
                                             : std::string {};
  return std::format(
      R"({{
  "options": {{
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1.0e5,
      "scale_objective": 1.0, "battery_power_envelope": {}}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "penalty_sink_battery",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "lmax": {}}}{}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": {}, "capacity": 1000}},
      {{"uid": 2, "name": "res", "bus": 1, "gcost": 500, "capacity": 5}}],
    {}{}
  }}
}})",
      c.envelope,
      kBatLoad,
      dump,
      kBatGcost,
      battery,
      zone);
}

struct BatResult
{
  double burned {0.0};  ///< g1 + res − served d1 [MW]
  double charge {0.0};  ///< battery charge demand [MW]
  double discharge {0.0};  ///< battery discharge generator [MW]
  double dump {0.0};  ///< served zero-value demand [MW]
  double lambda {0.0};  ///< bus price [$/MWh]
};

[[nodiscard]] BatResult bat_solve(const BatCase& c)
{
  PlanningLP plp(parse_planning_json(bat_json(c)));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto duals = li.get_row_dual();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();

  BatResult r;
  for (const auto& g : sys.elements<GeneratorLP>()) {
    const double p = sol[g.generation_cols_at(scenario, stage).at(buid)];
    if (g.uid() == Uid {1} || g.uid() == Uid {2}) {
      r.burned += p;
    } else {
      r.discharge += p;
    }
  }
  for (const auto& d : sys.elements<DemandLP>()) {
    const double l = sol[d.load_cols_at(scenario, stage).at(buid)];
    if (d.uid() == kBatDumpUid) {
      r.dump += l;
    } else if (d.uid() == Uid {1}) {
      r.burned -= l;
    } else {
      r.charge += l;
    }
  }
  r.lambda = duals
      [sys.elements<BusLP>().front().balance_rows_at(scenario, stage).at(buid)];
  return r;
}

}  // namespace penalty_sink_battery_test
}  // namespace

TEST_CASE(
    "penalty sink battery: a lossy battery charges and discharges in the "
    "same block to burn a penalised surplus (DEFECT)")  // NOLINT
{
  using namespace penalty_sink_battery_test;
  // DEFECT-DOCUMENTING TEST — "down" incentive, 20 MW of surplus wanted.
  const auto none = bat_solve({.with_battery = false});
  CHECK(none.burned == doctest::Approx(0.0).epsilon(1e-6));
  CHECK(none.lambda == doctest::Approx(kBatGcost - kBatPenalty));

  // Default formulation (power envelope ON): c + d ≤ P and d = η²·c
  // give c = P/(1 + η²), burned = (1 − η²)/(1 + η²)·P = 10.5 MW.
  const auto env = bat_solve({});
  CHECK(env.burned
        == doctest::Approx(kBatPbat * (1.0 - kBatRoundTrip)
                           / (1.0 + kBatRoundTrip))
               .epsilon(1e-4));
  CHECK(env.charge > 50.0);
  CHECK(env.discharge > 40.0);
  CHECK(env.discharge == doctest::Approx(kBatRoundTrip * env.charge));
  CHECK(env.lambda == doctest::Approx(kBatGcost - kBatPenalty));

  // Envelope OFF: both directions at full power, (1 − η²)·P = 19 MW.
  const auto noenv = bat_solve({.envelope = false});
  CHECK(noenv.burned
        == doctest::Approx(kBatPbat * (1.0 - kBatRoundTrip)).epsilon(1e-4));
  CHECK(noenv.charge == doctest::Approx(kBatPbat));

  // A cycle cost is a palliative, like loss_cost_eps: burning 1 MW costs
  // cost·(1 + η²)/(1 − η²) = 9.53·cost, so it blocks only above
  // 9 990 / 9.53 = 1 049 $/MWh.
  const auto cost1 = bat_solve({.cycle_cost = 1.0});
  CHECK(cost1.burned == doctest::Approx(env.burned).epsilon(1e-4));
  const auto cost6000 = bat_solve({.cycle_cost = 6000.0});
  CHECK(std::abs(cost6000.burned) < 1e-6);
  CHECK(cost6000.charge < 1e-6);
}

TEST_CASE(
    "penalty sink battery: an interior battery sink floors the bus price "
    "at its cycle cost (DEFECT)")  // NOLINT
{
  using namespace penalty_sink_battery_test;
  // DEFECT-DOCUMENTING TEST — 5 MW of surplus wanted, within the 10.5 MW
  // battery cap: the shortage is cured, the true price (−9 990) is
  // replaced by the marginal cost of the sink.
  const double per_mw = (1.0 + kBatRoundTrip) / (1.0 - kBatRoundTrip);  // 9.53
  for (const double cost : {0.0, 1.0, 10.0}) {
    CAPTURE(cost);
    const auto r = bat_solve({.surplus = 5.0, .cycle_cost = cost});
    CHECK(r.burned == doctest::Approx(5.0).epsilon(1e-4));
    CHECK(r.lambda == doctest::Approx(-cost * per_mw).epsilon(1e-3));
  }
}

TEST_CASE(
    "penalty sink battery: cycling earns nothing from the endogenous CPF "
    "load term (IMMUNE)")  // NOLINT
{
  using namespace penalty_sink_battery_test;
  // REGRESSION GUARD — D = Σ generation − Σ battery charge: the charge
  // demand of a battery converter is subtracted (reserve_zone_lp.cpp,
  // `charge_demands`), so g1 + discharge − charge = served load whatever
  // the battery does.  Cycling only costs fuel.
  for (const bool envelope : {true, false}) {
    CAPTURE(envelope);
    const auto r = bat_solve({.incentive = "cpf", .envelope = envelope});
    CHECK(std::abs(r.burned) < 1e-6);
    CHECK(r.charge < 1e-6);
    CHECK(r.discharge < 1e-6);
    CHECK(r.lambda == doctest::Approx(kBatGcost - (kBatBeta * kBatPenalty)));
  }
}

TEST_CASE(
    "penalty sink demand: a load with zero curtailment value is a sink "
    "for both incentives (DEFECT)")  // NOLINT
{
  using namespace penalty_sink_battery_test;
  // DEFECT-DOCUMENTING TEST (data-side).  gtopt's demand slack is
  // one-sided (load ≤ lmax), so a firm demand cannot over-consume; but a
  // demand with `fcost = 0` is served only when the bus price is ≤ 0,
  // and a penalty incentive makes it so.
  const auto down = bat_solve({.with_battery = false, .dump_lmax = 50.0});
  CHECK(down.dump == doctest::Approx(20.0).epsilon(1e-4));
  CHECK(down.burned == doctest::Approx(20.0).epsilon(1e-4));
  // Interior sink of value 0: the −9 990 price is floored at 0.
  CHECK(std::abs(down.lambda) < 1e-6);

  // CPF: unlike the battery charge, a plain load is not subtracted from
  // D, so serving it raises D and lowers the requirement.
  const auto cpf =
      bat_solve({.incentive = "cpf", .with_battery = false, .dump_lmax = 50.0});
  CHECK(cpf.dump == doctest::Approx(50.0).epsilon(1e-4));
  CHECK(cpf.lambda == doctest::Approx(kBatGcost - (kBatBeta * kBatPenalty)));
}
