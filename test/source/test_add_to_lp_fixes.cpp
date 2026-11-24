// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// LP-changing fixes of the add_to_lp audit (P0-2/3, P0-4, P0-8, P0-14, P0-18,
// P0-24, P0-25, §9 uniform elision, P1-17, P1-18).  Each fixture first
// reproduced the bug on master (the hand-computed optimum below differed from
// the solved one) and pins the fixed behaviour.
//
// Fixtures: one-block stages of 1 h, probability 1, no discount,
// `scale_objective = 1`, so objectives are hand-computable in $.

#include <algorithm>
#include <cmath>
#include <format>
#include <limits>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/carrier_converter_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/hydrogen_demand_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/hydrogen_storage_lp.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_options.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>
#include <gtopt/waterway_lp.hpp>

using namespace gtopt;

namespace
{

Simulation a2lp_stages(int nstages)
{
  Simulation sim;
  for (int t = 0; t < nstages; ++t) {
    sim.block_array.push_back({
        .uid = Uid {t + 1},
        .duration = 1.0,
    });
    sim.stage_array.push_back({
        .uid = Uid {t + 1},
        .first_block = static_cast<Size>(t),
        .count_block = 1,
    });
  }
  sim.scenario_array = {
      {.uid = Uid {1}},
  };
  return sim;
}

PlanningOptions a2lp_options()
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = true;
  return popts;
}

/// Solve @p sys over @p nstages one-hour stages; physical objective (NaN
/// when not optimal).
double a2lp_solve(const System& sys,
                  int nstages = 2,
                  const PlanningOptions& popts = a2lp_options())
{
  const auto simulation = a2lp_stages(nstages);
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  if (!result.has_value() || result.value() != 0) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  return li.get_obj_value();
}

double a2lp_solve_json(std::string_view json,
                       int nstages = 2,
                       const PlanningOptions& popts = a2lp_options())
{
  return a2lp_solve(daw::json::from_json<System>(json), nstages, popts);
}

}  // namespace

// ─── P0-2 / P0-3: inactive storages and carrier converters ─────────────────

namespace
{

/// H₂ node with a priced offtake (`price` 200 $/MWh, up to 100 MW): the
/// LP serves it whenever H₂ is available below 200 $/MWh.
System h2_market_system()
{
  System sys;
  sys.name = "h2_market";
  sys.bus_array = {
      {.uid = Uid {1}, .name = "b1"},
  };
  sys.generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .pmax = 500.0,
          .gcost = 50.0,
          .capacity = 500.0,
      },
  };
  sys.hydrogen_node_array = {
      {
          {
              .uid = Uid {11},
              .name = "h2",
          },
      },
  };
  sys.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "offtake",
          .hydrogen_node = SingleId {Uid {11}},
          .lmax = 100.0,
          .price = 200.0,
      },
  };
  return sys;
}

}  // namespace

TEST_CASE("P0-2 inactive H2 storage injects nothing into its node")  // NOLINT
{
  // No H₂ source at all: the offtake cannot be served, objective 0.  The
  // storage is out of service (`active = 0`); before the fix its `fout`
  // column was created and stamped +1 into the node balance with no
  // energy balance, i.e. free hydrogen: 2 × (−200 × 100) = −40000.
  auto sys = h2_market_system();
  sys.hydrogen_storage_array = {
      {
          .uid = Uid {1},
          .name = "cavern",
          .active = OptActive {IntBool {0}},
          .hydrogen_node = SingleId {Uid {11}},
          .emin = 0.0,
          .emax = 1000.0,
          .eini = 0.0,
          .capacity = 1000.0,
      },
  };
  CHECK(a2lp_solve(sys) == doctest::Approx(0.0).epsilon(1e-9));
}

TEST_CASE("P0-3 inactive carrier converter converts nothing")  // NOLINT
{
  // Electrolyser (η = 1) active only in stage 1: H₂ at 50 $/MWh sold at
  // 200 → −15000 in stage 1, nothing in stage 2.  Before the fix the
  // converter ignored `active` and converted in both stages (−30000).
  auto sys = h2_market_system();
  sys.carrier_converter_array = {
      {
          .uid = Uid {1},
          .name = "electrolyser",
          .active = OptActive {std::vector<IntBool> {1, 0}},
          .from_carrier = Carrier::Electric,
          .to_carrier = Carrier::Hydrogen,
          .from_node = SingleId {Uid {1}},
          .to_node = SingleId {Uid {11}},
          .efficiency = 1.0,
          .capacity = 500.0,
      },
  };
  CHECK(a2lp_solve(sys) == doctest::Approx(-15000.0));

  SUBCASE("inactive destination node: no conversion, no throw")
  {
    sys.carrier_converter_array.front().active = {};
    sys.hydrogen_node_array.front().active =
        OptActive {std::vector<IntBool> {1, 0}};
    sys.hydrogen_demand_array.front().active =
        OptActive {std::vector<IntBool> {1, 0}};
    CHECK(a2lp_solve(sys) == doctest::Approx(-15000.0));
  }
}

TEST_CASE("P0-2 inactive battery creates no flow columns")  // NOLINT
{
  // A battery out of service in stage 2 has no charge / discharge
  // columns there (before the fix: free [0, inf) columns with no energy
  // balance, registered for user constraints).
  constexpr std::string_view json = R"({
   "name":"bat_inactive","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[{"uid":1,"name":"g1","bus":"b1","pmax":200,
     "capacity":200,"gcost":10}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":50}],
   "battery_array":[{"uid":1,"name":"bat","bus":"b1","emin":0,"emax":100,
     "eini":0,"capacity":100,"pmax_charge":50,"pmax_discharge":50,
     "use_state_variable":false,"daily_cycle":false,
     "active":[1,0]}]})";
  const auto sys = daw::json::from_json<System>(json);
  const auto simulation = a2lp_stages(2);
  const PlanningOptionsLP options(a2lp_options());
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  const auto& bat = system_lp.elements<BatteryLP>().front();
  const auto& scenario = system_lp.scene().scenarios().front();
  const auto& stages = system_lp.phase().stages();
  CHECK_FALSE(bat.finp_cols_at(scenario, stages[0]).empty());
  CHECK(bat.finp_cols_at(scenario, stages[1]).empty());
  CHECK(bat.fout_cols_at(scenario, stages[1]).empty());
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  CHECK(result.value() == 0);
  CHECK(li.get_obj_value() == doctest::Approx(1000.0));
}

// ─── P0-4: carrier-converter capacity row under expansion ──────────────────

TEST_CASE("P0-4 expandable carrier converter pays for its capacity")  // NOLINT
{
  // No installed capacity; up to 100 MW can be built at an annualised
  // cost that makes one hour of H₂ sales (150 $/MWh margin × 100 MWh)
  // not worth it, so the optimum builds nothing and converts nothing:
  // objective 0.  Before the fix the input column was bounded by capmax
  // with no `input ≤ capainst` row, so the unbuilt capacity was usable
  // for free: 2 × (−15000) = −30000.
  auto sys = h2_market_system();
  sys.carrier_converter_array = {
      {
          .uid = Uid {1},
          .name = "electrolyser",
          .from_carrier = Carrier::Electric,
          .to_carrier = Carrier::Hydrogen,
          .from_node = SingleId {Uid {1}},
          .to_node = SingleId {Uid {11}},
          .efficiency = 1.0,
          .capacity = 0.0,
          .expcap = 100.0,
          .expmod = 1.0,
          .capmax = 100.0,
          .annual_capcost = 1.0e9,
      },
  };
  CHECK(a2lp_solve(sys) == doctest::Approx(0.0).epsilon(1e-9));

  SUBCASE("cheap capacity is built and then used")
  {
    // 87.6 $/MW-yr over 2 h = 0.02 $/MW: build 100 MW (2 $) and sell.
    sys.carrier_converter_array.front().annual_capcost = 87.6;
    const double obj = a2lp_solve(sys);
    CHECK(obj < -29000.0);
  }
}

// ─── P0-18: α-rebased stage / block decision variables are free below ──────

TEST_CASE(  // NOLINT
    "P0-18 obj_constant decision variable defaults free below in every scope")
{
  // An α-rebased column (`obj_constant` set) may be negative; the only
  // lower limit is the user constraint `value >= -5`.  Objective per
  // stage = cost·value + obj_constant = −5 + 100.  Before the fix the
  // stage / block path defaulted `lower_bound` to 0 (only the global path
  // honoured the documented rule), so value = 0 and the objective was
  // 2 × 100 = 200 instead of 2 × 95 = 190.
  for (const std::string_view scope : {"stage", "block"}) {
    CAPTURE(scope);
    const auto json = std::format(R"({{
     "name":"dv_rebase","bus_array":[{{"uid":1,"name":"b1"}}],
     "decision_variable_array":[{{"uid":1,"name":"alpha","scope":"{}",
       "cost":1.0,"cost_type":"raw","obj_constant":100.0}}],
     "user_constraint_array":[{{"uid":1,"name":"floor","scope":"{}",
       "constraint_type":"raw",
       "expression":"decision_variable('alpha').value >= -5"}}]}})",
                                  scope,
                                  scope);
    CHECK(a2lp_solve_json(json) == doctest::Approx(190.0));
  }
}

// ─── P0-24: cycle-basis KVL with an intra-stage line outage ────────────────

namespace
{

/// Square 1-2-3-4-1 with the diagonal 1-3.  BFS from bus 1 takes the
/// diagonal as a tree edge, so both fundamental cycles (1-2-3, 1-3-4)
/// contain it.  Cheap g1 at bus 1, expensive g3 and a 100 MW load at bus
/// 3; line 2-3 is limited to 30 MW.  Block 2 has the diagonal out of
/// service (`in_service = 0`).
constexpr std::string_view kvl_square_json = R"json(
{
  "options": {
    "annual_discount_rate": 0.0,
    "model_options": {
      "use_single_bus": false, "use_kirchhoff": true,
      "scale_objective": 1, "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "block_array": [ {"uid": 1, "duration": 1}, {"uid": 2, "duration": 1} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 2} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "kvl_square",
    "bus_array": [ {"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"},
                   {"uid": 3, "name": "b3"}, {"uid": 4, "name": "b4"} ],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": "b1", "pmax": 500, "gcost": 10,
       "capacity": 500},
      {"uid": 3, "name": "g3", "bus": "b3", "pmax": 500, "gcost": 100,
       "capacity": 500}
    ],
    "demand_array": [ {"uid": 1, "name": "d3", "bus": "b3", "lmax": 100} ],
    "line_array": [
      {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
       "reactance": 0.1, "tmax_ab": 500, "tmax_ba": 500},
      {"uid": 2, "name": "l23", "bus_a": "b2", "bus_b": "b3",
       "reactance": 0.1, "tmax_ab": 30, "tmax_ba": 30},
      {"uid": 3, "name": "l34", "bus_a": "b3", "bus_b": "b4",
       "reactance": 0.1, "tmax_ab": 500, "tmax_ba": 500},
      {"uid": 4, "name": "l41", "bus_a": "b4", "bus_b": "b1",
       "reactance": 0.1, "tmax_ab": 500, "tmax_ba": 500},
      {"uid": 5, "name": "l13", "bus_a": "b1", "bus_b": "b3",
       "reactance": 0.1, "tmax_ab": 500, "tmax_ba": 500,
       "in_service": [[1, 0]]}
    ]
  }
}
)json";

double kvl_square_obj(std::string_view mode)
{
  auto planning = daw::json::from_json<Planning>(kvl_square_json);
  planning.options.model_options.kirchhoff_mode = OptName {std::string {mode}};
  PlanningLP planning_lp(std::move(planning));
  const auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  return planning_lp.systems()
      .front()
      .front()
      .linear_interface()
      .get_obj_value();
}

}  // namespace

TEST_CASE("P0-24 cycle basis keeps the loops that survive an outage")  // NOLINT
{
  // Block 1 (diagonal in): 1-3 direct carries the load, all cheap: 1000.
  // Block 2 (diagonal out): paths 1-2-3 and 1-4-3 have equal reactance,
  // so KVL around 1-2-3-4 splits the flow 50/50 and line 2-3 (30 MW)
  // caps BOTH paths at 30: 60 cheap + 40 expensive = 600 + 4000 = 4600.
  // Before the fix the outage dropped both fundamental cycles (each
  // contains the diagonal), the outer loop had no KVL row, and block 2
  // routed 70 MW via 1-4-3 for free: 1000 instead of 4600.
  const double node_angle = kvl_square_obj("node_angle");
  const double cycle_basis = kvl_square_obj("cycle_basis");
  CHECK(node_angle == doctest::Approx(1000.0 + 4600.0));
  CHECK(cycle_basis == doctest::Approx(node_angle));
}

// ─── P0-25: Converter coupling under Option C (demand_fail_rhs_shift) ──────

namespace
{

/// One stage, two 1-h blocks: energy at 10 $/MWh in block 1 and only at
/// 100 $/MWh in block 2, where a 50 MW load sits.  A 100 MWh battery
/// (expanded into converter + discharge generator + charge demand) can
/// shift 50 MWh: charge in block 1, discharge in block 2.
constexpr std::string_view optc_battery_json = R"json(
{
  "options": {
    "annual_discount_rate": 0.0,
    "model_options": {
      "use_single_bus": true, "scale_objective": 1, "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "block_array": [ {"uid": 1, "duration": 1}, {"uid": 2, "duration": 1} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 2} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "optc_battery",
    "bus_array": [ {"uid": 1, "name": "b1"} ],
    "generator_array": [
      {"uid": 1, "name": "cheap", "bus": "b1", "pmax": [[200, 0]],
       "gcost": 10, "capacity": 200},
      {"uid": 2, "name": "dear", "bus": "b1", "pmax": 200, "gcost": 100,
       "capacity": 200}
    ],
    "demand_array": [ {"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[0, 50]]} ],
    "battery_array": [
      {"uid": 1, "name": "bat", "bus": "b1", "emin": 0, "emax": 100,
       "eini": 0, "capacity": 100, "pmax_charge": 100,
       "pmax_discharge": 100, "input_efficiency": 1,
       "output_efficiency": 1, "annual_standing_loss": 0,
       "use_state_variable": false, "daily_cycle": false}
    ]
  }
}
)json";

double optc_battery_obj(bool option_c)
{
  auto planning = daw::json::from_json<Planning>(optc_battery_json);
  planning.options.model_options.demand_fail_rhs_shift = option_c;
  PlanningLP planning_lp(std::move(planning));
  const auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  return planning_lp.systems()
      .front()
      .front()
      .linear_interface()
      .get_obj_value();
}

}  // namespace

TEST_CASE("P0-25 converter couples the charge demand under Option C")  // NOLINT
{
  // Charge 50 MWh at 10 $/MWh in block 1, discharge them in block 2:
  // objective 500.  Under Option C the charge-demand column carries
  // `load − lmax`, but the converter used it as `load`: the coupling row
  // was wrong by lmax (charging without drawing from the bus).
  const double option_a = optc_battery_obj(/*option_c=*/false);
  CHECK(option_a == doctest::Approx(500.0));
  CHECK(optc_battery_obj(/*option_c=*/true) == doctest::Approx(option_a));
}

// ─── P1-17: LineCommitment fixed_status outside [0, 1] leaves u free ───────

TEST_CASE(
    "P1-17 LineCommitment fixed_status -1 does not open the line")  // NOLINT
{
  // g1 (10 $/MWh) at b1 serves a 100 MW load at b2 through l12.  The
  // breaker schedule marks the block un-pinned (`-1`, UC.jl's null), so
  // u is free and the line closes: objective 1000.  Before the fix any
  // value other than "unset" pinned u (−1 < 0.5 → open), shedding the
  // load at 1000 $/MWh: 100000.  The relaxed status keeps this an LP.
  auto planning = daw::json::from_json<Planning>(R"json(
{
  "options": {
    "annual_discount_rate": 0.0,
    "model_options": {
      "use_single_bus": false, "use_kirchhoff": false,
      "scale_objective": 1, "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "block_array": [ {"uid": 1, "duration": 1} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 1,
                      "chronological": true} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "lc_fixed",
    "bus_array": [ {"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"} ],
    "generator_array": [ {"uid": 1, "name": "g1", "bus": "b1",
                          "pmax": 500, "gcost": 10, "capacity": 500} ],
    "demand_array": [ {"uid": 1, "name": "d2", "bus": "b2", "lmax": 100} ],
    "line_array": [ {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
                     "reactance": 0.05, "tmax_ab": 200, "tmax_ba": 200} ],
    "line_commitment_array": [ {"uid": 1, "name": "l12_ots", "line": "l12",
                                "relax": true, "fixed_status": -1} ]
  }
}
)json");
  PlanningLP planning_lp(std::move(planning));
  const auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  CHECK(planning_lp.systems().front().front().linear_interface().get_obj_value()
        == doctest::Approx(1000.0));
}

// ─── P1-18: SimpleCommitment gates the reserve headroom rows by u ──────────

TEST_CASE(
    "P1-18 SimpleCommitment off unit is not forced on by reserves")  // NOLINT
{
  // g1 (pmin 50) is under SimpleCommitment; free g2 covers the rest of
  // the 20 MW load.  g1 must offer 10 MW of down reserve (shortage
  // 1000 $/MW).  With the row gated by u, `g1 − dr ≥ pmin·u` and
  // `g1 ≤ pmax·u` give g1 = 10 + 50u ≤ 100u, i.e. u = 0.2, g1 = 20:
  // objective 200.  Before the fix the headroom row kept the generator's
  // pmin although SimpleCommitment had moved it onto `g1 ≥ pmin·u`:
  // the row `g1 − dr ≥ 50` forced g1 ≥ 50 against a 20 MW load: the
  // LP was infeasible.  Commitment
  // already rewrites the row as `g − dr − pmin·u ≥ 0`; SimpleCommitment
  // now does the same.
  constexpr std::string_view json = R"({
   "name":"simple_uc_reserve","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[
     {"uid":1,"name":"g1","bus":"b1","pmin":50,"pmax":100,
      "capacity":100,"gcost":10},
     {"uid":2,"name":"g2","bus":"b1","pmax":200,"capacity":200,
      "gcost":0}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":20}],
   "simple_commitment_array":[{"uid":1,"name":"g1_uc","generator":"g1",
     "relax":true}],
   "reserve_zone_array":[{"uid":1,"name":"rz","drreq":10,"drcost":1000}],
   "reserve_provision_array":[{"uid":1,"name":"g1_dn","generator":"g1",
     "reserve_zones":["rz"],"drmax":30,"dr_provision_factor":1}]})";
  CHECK(a2lp_solve_json(json, 1) == doctest::Approx(200.0));
}

TEST_CASE("P1-18 SimpleCommitment off unit offers no up reserve")  // NOLINT
{
  // A 30 MW up requirement (shortage 1000 $/MW).  g1 can only offer it
  // while committed: `g1 + ur ≤ pmax·u` with `g1 ≥ pmin·u = 50·u`, and
  // the 20 MW load caps g1 at 20, hence u ≤ 0.4 and ur ≤ 100·0.4 − 20 =
  // 20: g1 = 20 at 10 $/MWh plus 10 MW short, 200 + 10000 = 10200.
  // Before the fix the off unit offered the full 30 MW for free
  // (`g1 + ur ≤ 100`, g1 = 0): objective 0.
  constexpr std::string_view json = R"({
   "name":"simple_uc_up","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[
     {"uid":1,"name":"g1","bus":"b1","pmin":50,"pmax":100,
      "capacity":100,"gcost":10},
     {"uid":2,"name":"g2","bus":"b1","pmax":200,"capacity":200,
      "gcost":0}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":20}],
   "simple_commitment_array":[{"uid":1,"name":"g1_uc","generator":"g1",
     "relax":true}],
   "reserve_zone_array":[{"uid":1,"name":"rz","urreq":30,"urcost":1000}],
   "reserve_provision_array":[{"uid":1,"name":"g1_up","generator":"g1",
     "reserve_zones":["rz"],"urmax":100,"ur_provision_factor":1}]})";
  CHECK(a2lp_solve_json(json, 1) == doctest::Approx(10200.0));
}

// ─── §9: zero-bound elision only under lp_reduction ────────────────────────

TEST_CASE(
    "§9 Waterway / Demand keep fixed-zero columns unless lp_reduction")  // NOLINT
{
  // Stage 2 closes the waterway (fmax 0) and has no load (lmax 0).  The
  // honest LP (lp_reduction off, the default) keeps a [0, 0] column in
  // those cells, as Generator / Reservoir / ReserveProvision do; only
  // `lp_reduction = true` elides them.  Before the fix Waterway and
  // Demand elided unconditionally (absent output cells, consumers seeing
  // no column although the flag was off).
  constexpr std::string_view json = R"({
   "name":"elide","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[{"uid":1,"name":"g1","bus":"b1","pmax":100,
     "capacity":100,"gcost":10}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":[[50],[0]]}],
   "junction_array":[{"uid":1,"name":"j1"},
                     {"uid":2,"name":"j2","drain":true}],
   "waterway_array":[{"uid":1,"name":"w1","junction_a":"j1",
     "junction_b":"j2","fmin":0,"fmax":[[10],[0]]}]})";
  const auto sys = daw::json::from_json<System>(json);
  for (const bool reduce : {false, true}) {
    CAPTURE(reduce);
    auto popts = a2lp_options();
    popts.model_options.lp_reduction = reduce;
    const auto simulation = a2lp_stages(2);
    const PlanningOptionsLP options(popts);
    SimulationLP simulation_lp(simulation, options);
    SystemLP system_lp(sys, simulation_lp);
    const auto& scenario = system_lp.scene().scenarios().front();
    const auto& stages = system_lp.phase().stages();
    const auto& ww = system_lp.elements<WaterwayLP>().front();
    const auto& dem = system_lp.elements<DemandLP>().front();
    CHECK(ww.flow_cols_at(scenario, stages[0]).size() == 1);
    CHECK(dem.lookup_load_cols(scenario, stages[0]).size() == 1);
    const std::size_t expected = reduce ? 0 : 1;
    CHECK(ww.flow_cols_at(scenario, stages[1]).size() == expected);
    CHECK(dem.lookup_load_cols(scenario, stages[1]).size() == expected);
    auto&& li = system_lp.linear_interface();
    const auto result = li.resolve();
    REQUIRE(result.has_value());
    CHECK(li.get_obj_value() == doctest::Approx(500.0));
  }
}

TEST_CASE("§9 Demand with negative lmax is a zero-load block")  // NOLINT
{
  // A negative `lmax` (UC.jl bus with net injection, e.g. the vendored
  // base_with_storage instance) serves nothing.  Before §9 such a block
  // was elided; after it, with lp_reduction off, the load column was
  // built as [0, lmax < 0] and the LP was primal infeasible.  It is now
  // a [0, 0] column (elided under lp_reduction): stage 2 costs nothing.
  constexpr std::string_view json = R"({
   "name":"neg_lmax","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[{"uid":1,"name":"g1","bus":"b1","pmax":100,
     "capacity":100,"gcost":10}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":[[50],[-5]]}]})";
  const auto sys = daw::json::from_json<System>(json);
  for (const bool reduce : {false, true}) {
    CAPTURE(reduce);
    auto popts = a2lp_options();
    popts.model_options.lp_reduction = reduce;
    const auto simulation = a2lp_stages(2);
    const PlanningOptionsLP options(popts);
    SimulationLP simulation_lp(simulation, options);
    SystemLP system_lp(sys, simulation_lp);
    const auto& scenario = system_lp.scene().scenarios().front();
    const auto& stages = system_lp.phase().stages();
    const auto& dem = system_lp.elements<DemandLP>().front();
    const std::size_t expected = reduce ? 0 : 1;
    const auto& cols = dem.lookup_load_cols(scenario, stages[1]);
    REQUIRE(cols.size() == expected);
    auto&& li = system_lp.linear_interface();
    for (const auto& [buid, col] : cols) {
      CHECK(li.get_col_low()[col] == doctest::Approx(0.0));
      CHECK(li.get_col_upp()[col] == doctest::Approx(0.0));
    }
    const auto result = li.resolve();
    REQUIRE(result.has_value());
    CHECK(result.value_or(-1) == 0);
    CHECK(li.get_obj_value() == doctest::Approx(500.0));
  }
}

// ─── P0-14: open line pack over several stages is rejected ─────────────────

namespace
{

bool a2lp_has_error(const ValidationResult& r, std::string_view needle)
{
  return std::ranges::any_of(r.errors,
                             [&](const auto& e) { return e.contains(needle); });
}

Planning linepack_planning(int nstages, bool cyclic)
{
  Planning p;
  p.simulation = a2lp_stages(nstages);
  p.options.gas_options.linepack_cyclic = cyclic;
  p.system.gas_node_array = {
      {.uid = Uid {1}, .name = "S", .pmin = 30.0, .pmax = 60.0},
      {.uid = Uid {2}, .name = "T", .pmin = 30.0, .pmax = 60.0},
  };
  p.system.gas_pipe_array = {
      {
          .uid = Uid {1},
          .name = "S_T",
          .gas_node_a = Uid {1},
          .gas_node_b = Uid {2},
          .length = 80.0,
          .diameter = 406.0,
          .fmax = 300.0,
          .linepack = true,
      },
  };
  return p;
}

}  // namespace

TEST_CASE("P0-14 non-cyclic line pack needs a single stage")  // NOLINT
{
  // The line pack is not a state variable: every stage restarts from
  // `linepack_ini`.  With `linepack_cyclic = false` each stage could draw
  // the pack down to its pressure floor and start full again in the next
  // one — gas created at every stage boundary.  Rejected up front.
  auto open_multi = linepack_planning(2, /*cyclic=*/false);
  auto open_single = linepack_planning(1, /*cyclic=*/false);
  auto cyclic_multi = linepack_planning(2, /*cyclic=*/true);
  CHECK(a2lp_has_error(validate_planning(open_multi), "linepack_cyclic"));
  CHECK_FALSE(
      a2lp_has_error(validate_planning(open_single), "linepack_cyclic"));
  CHECK_FALSE(
      a2lp_has_error(validate_planning(cyclic_multi), "linepack_cyclic"));
}

// ─── P0-8: storage commissioned after the first stage ──────────────────────

namespace
{

// clang-format off

/// 50 MW load in both 1 h stages, one 100 $/MWh generator and a 100 MWh
/// battery (state variable on).  ACTIVE / EINI / PHASES are substituted.
constexpr std::string_view a2lp_commission_case = R"({
  "options": {"output_format": "csv",
              "model_options": {"use_single_bus": true,
                                "demand_fail_cost": 1000,
                                "scale_objective": 1}},
  "simulation": {
    "annual_discount_rate": 0.0,
    "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1},
                    {"uid": 2, "first_block": 1, "count_block": 1}]PHASES,
    "scenario_array": [{"uid": 1, "probability_factor": 1}]},
  "system": {
    "name": "commission",
    "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": "b1", "lmax": 50}],
    "generator_array": [{"uid": 1, "name": "g1", "bus": "b1", "gcost": 100,
                         "pmax": 200, "capacity": 200}],
    "battery_array": [{"uid": 1, "name": "bat", "bus": "b1",
                       "emin": 0, "emax": 100, "capacity": 100EINI,
                       "pmax_charge": 100, "pmax_discharge": 100,
                       "input_efficiency": 1, "output_efficiency": 1,
                       "annual_standing_loss": 0,
                       "use_state_variable": true, "daily_cycle": false,
                       "active": ACTIVE}]}
})";

// clang-format on

constexpr std::string_view a2lp_two_phases = R"(,
    "phase_array": [{"uid": 1, "first_stage": 0, "count_stage": 1},
                    {"uid": 2, "first_stage": 1, "count_stage": 1}])";

std::string a2lp_commission_json(std::string_view active,
                                 std::string_view eini,
                                 std::string_view phases)
{
  std::string s {a2lp_commission_case};
  s.replace(s.find("PHASES"), 6, phases);
  s.replace(s.find("EINI"), 4, eini);
  s.replace(s.find("ACTIVE"), 6, active);
  return s;
}

Planning a2lp_commission_planning(std::string_view active,
                                  std::string_view eini,
                                  std::string_view phases)
{
  Planning base;
  base.merge(parse_planning_json(a2lp_commission_json(active, eini, phases)));
  return base;
}

/// Monolithic objective (NaN when not optimal).
double a2lp_commission_mono(std::string_view active, std::string_view eini)
{
  PlanningLP plp(a2lp_commission_planning(active, eini, ""));
  const auto res = plp.resolve();
  if (!res.has_value() || *res != 1) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  return plp.systems().front().front().linear_interface().get_obj_value();
}

/// Two-phase SDDP upper bound (NaN when SDDP fails).
double a2lp_commission_sddp(std::string_view active, std::string_view eini)
{
  PlanningLP plp(a2lp_commission_planning(active, eini, a2lp_two_phases));
  SDDPOptions opts;
  opts.max_iterations = 10;
  opts.convergence_tol = 1.0e-9;
  opts.stationary_tol = 0.0;
  opts.apertures = std::vector<Uid> {};
  opts.enable_api = false;
  SDDPMethod sddp(plp, opts);
  const auto results = sddp.solve();
  if (!results.has_value() || results->empty()) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  INFO("LB=", results->back().lower_bound, " UB=", results->back().upper_bound);
  CHECK(results->back().lower_bound
        == doctest::Approx(results->back().upper_bound).epsilon(1e-6));
  return results->back().upper_bound;
}

bool a2lp_has_warning(const ValidationResult& r, std::string_view needle)
{
  return std::ranges::any_of(r.warnings,
                             [&](const auto& w) { return w.contains(needle); });
}

}  // namespace

TEST_CASE("P0-8 storage commissioned in stage 2 starts at eini")  // NOLINT
{
  // The battery enters service in stage 2.  There is no stage-1 state to
  // inherit, so its initial charge is fixed at `eini` (`emin` = 0 when
  // unset): the load is served by g1 in both stages, 2 × 50 × 100 =
  // 10000.  Before the fix the initial-state column was free in
  // [emin, emax] (same LP, eini unset) or a free SDDP `sini` whose state
  // link to the missing stage-1 efin was dropped (across phases, even
  // with eini = 0): 50 MWh of free energy, objective 5000.
  constexpr std::string_view active = "[0, 1]";
  SUBCASE("same phase, eini unset")
  {
    CHECK(a2lp_commission_mono(active, "") == doctest::Approx(10000.0));
  }
  SUBCASE("same phase, eini = 0")
  {
    CHECK(a2lp_commission_mono(active, R"(, "eini": 0)")
          == doctest::Approx(10000.0));
  }
  SUBCASE("phase boundary (SDDP), eini unset")
  {
    CHECK(a2lp_commission_sddp(active, "") == doctest::Approx(10000.0));
  }
  SUBCASE("phase boundary (SDDP), eini = 0")
  {
    CHECK(a2lp_commission_sddp(active, R"(, "eini": 0)")
          == doctest::Approx(10000.0));
  }
  SUBCASE("explicit eini is honoured")
  {
    // eini = 30 MWh: 30 MW of the stage-2 load comes from storage.
    CHECK(a2lp_commission_mono(active, R"(, "eini": 30)")
          == doctest::Approx(7000.0));
    CHECK(a2lp_commission_sddp(active, R"(, "eini": 30)")
          == doctest::Approx(7000.0));
  }
}

TEST_CASE("P0-8 storage active from the first stage is unchanged")  // NOLINT
{
  // Global first stage with eini unset keeps the historical free initial
  // state in [emin, emax]: 100 MWh covers both stages, objective 0.
  constexpr std::string_view active = "[1, 1]";
  CHECK(a2lp_commission_mono(active, "") == doctest::Approx(0.0));
  CHECK(a2lp_commission_mono(active, R"(, "eini": 0)")
        == doctest::Approx(10000.0));
  CHECK(a2lp_commission_sddp(active, R"(, "eini": 0)")
        == doctest::Approx(10000.0));
}

TEST_CASE("P0-8 battery commissioned without eini warns once")  // NOLINT
{
  auto late = a2lp_commission_planning("[0, 1]", "", "");
  auto late_eini = a2lp_commission_planning("[0, 1]", R"(, "eini": 0)", "");
  auto first = a2lp_commission_planning("[1, 1]", "", "");
  const auto r = validate_planning(late);
  CHECK(std::ranges::count_if(r.warnings,
                              [](const auto& w)
                              { return w.contains("is commissioned at"); })
        == 1);
  CHECK_FALSE(
      a2lp_has_warning(validate_planning(late_eini), "is commissioned at"));
  CHECK_FALSE(a2lp_has_warning(validate_planning(first), "is commissioned at"));
}

TEST_CASE("P0-8 reservoir commissioned without eini is an error")  // NOLINT
{
  // Only a battery may default its commissioning state to emin; a
  // reservoir (like every other state-carrying storage) must state it.
  const auto reservoir_planning = [](OptReal eini)
  {
    Planning p;
    p.simulation = a2lp_stages(2);
    p.system.junction_array = {
        {.uid = Uid {1}, .name = "j1"},
    };
    p.system.reservoir_array = {
        {
            .uid = Uid {1},
            .name = "res1",
            .active = std::vector<IntBool> {0, 1},
            .junction = Uid {1},
            .emin = 0.0,
            .emax = 100.0,
            .eini = eini,
        },
    };
    return p;
  };
  auto missing = reservoir_planning(std::nullopt);
  auto given = reservoir_planning(10.0);
  const auto r = validate_planning(missing);
  CHECK(a2lp_has_error(r, "res1"));
  CHECK(a2lp_has_error(r, "has no 'eini'"));
  CHECK(a2lp_has_error(r, "stage uid=2"));
  CHECK_FALSE(a2lp_has_error(validate_planning(given), "has no 'eini'"));
}

TEST_CASE("P0-8 cyclic battery commissioned in stage 2 is unchanged")  // NOLINT
{
  // A battery without a state variable (use_state_variable = false, or
  // daily_cycle) never reads `eini`: commissioning keeps its per-stage
  // cyclic formulation — initial-state column free in [emin, emax],
  // closed by efin == eini — with no warning and no error.  Cost 10000
  // (a closed cycle cannot create energy), as on master.
  const auto cyclic = [](std::string_view mode)
  {
    std::string s = a2lp_commission_json("[0, 1]", "", "");
    const std::string_view usv =
        R"("use_state_variable": true, "daily_cycle": false)";
    s.replace(s.find(usv), usv.size(), mode);
    Planning p;
    p.merge(parse_planning_json(s));
    return p;
  };
  for (const std::string_view mode :
       {
           std::string_view {
               R"("use_state_variable": false, "daily_cycle": false)"},
           std::string_view {R"("daily_cycle": true)"},
       })
  {
    CAPTURE(mode);
    auto planning = cyclic(mode);
    const auto r = validate_planning(planning);
    CHECK_FALSE(a2lp_has_warning(r, "eini"));
    CHECK_FALSE(a2lp_has_error(r, "eini"));

    PlanningLP plp(cyclic(mode));
    const auto res = plp.resolve();
    REQUIRE(res.has_value());
    auto& system_lp = plp.systems().front().front();
    auto&& li = system_lp.linear_interface();
    CHECK(li.get_obj_value() == doctest::Approx(10000.0));
    const auto& bat = system_lp.elements<BatteryLP>().front();
    const auto& scenario = system_lp.scene().scenarios().front();
    const auto& stages = system_lp.phase().stages();
    const auto eini = bat.eini_col_at(scenario, stages[1]);
    CHECK(li.get_col_low()[eini] == doctest::Approx(0.0));
    CHECK(li.get_col_upp()[eini] == doctest::Approx(100.0));
  }
}
