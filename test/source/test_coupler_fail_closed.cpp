// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Coupler "fail-closed" rule (add_to_lp audit, R2 / P0-26, P0-11, P0-15).
//
// A coupler element (Turbine, GasTurbine, Converter, Pump, electric
// GasCompressor) converts a resource of its partner into another one.
// When the coupler is not *effective* in a stage — it is inactive, or one
// of the references it needs (flow, junction, gas node, battery, pump
// load, bus) is inactive or has no column — the partner's producing
// columns must be closed (pinned to 0), never left free.  Before the fix
// each of these fixtures let the LP produce energy / lift water / compress
// gas for free, so the optimum was strictly cheaper than the physically
// reachable one.
//
// Fixtures: two 1-hour stages with one block each, probability 1, no
// discount, `scale_objective = 1`, so the objective is the hand-computed
// sum of $/MWh × MWh.

#include <algorithm>
#include <format>
#include <limits>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

Simulation make_stages_simulation(int nstages)
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

/// Solve @p system_json over @p nstages one-hour stages; return the
/// physical objective (NaN when not optimal).
double solve_obj(std::string_view system_json, int nstages = 2)
{
  const auto sys = daw::json::from_json<System>(system_json);
  const auto simulation = make_stages_simulation(nstages);
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = true;
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

bool has_error(const ValidationResult& r, std::string_view needle)
{
  return std::ranges::any_of(r.errors,
                             [&](const auto& e) { return e.contains(needle); });
}

/// validate_planning over @p system_json with two one-block stages.
ValidationResult validate_json(std::string_view system_json)
{
  Planning planning;
  planning.simulation = make_stages_simulation(2);
  planning.system = daw::json::from_json<System>(system_json);
  return validate_planning(planning);
}

}  // namespace

TEST_CASE("Turbine not effective closes the hydro generator")  // NOLINT
{
  // 100 MW demand; thermal at $100/MWh; hydro generator fed by a 50 m³/s
  // flow through turbine t1 (rate 1 MW per m³/s).  Stage 1: hydro 50 +
  // thermal 50 = $5000.  Stage 2: the turbine (or its flow) is inactive,
  // so the hydro unit cannot produce: thermal 100 = $10000.
  constexpr double expected = 5000.0 + 10000.0;

  SUBCASE("turbine inactive")
  {
    constexpr std::string_view json = R"({
     "name":"turbine_inactive","bus_array":[{"uid":1,"name":"b1"}],
     "generator_array":[
      {"uid":1,"name":"gh","bus":"b1","pmax":200,"capacity":200,"gcost":0},
      {"uid":2,"name":"gt","bus":"b1","pmax":500,"capacity":500,
       "gcost":100}],
     "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":100}],
     "flow_array":[{"uid":1,"name":"f1","discharge":50}],
     "turbine_array":[{"uid":1,"name":"t1","generator":"gh","flow":"f1",
       "active":[1,0]}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected));
  }

  SUBCASE("flow inactive")
  {
    constexpr std::string_view json = R"({
     "name":"flow_inactive","bus_array":[{"uid":1,"name":"b1"}],
     "generator_array":[
      {"uid":1,"name":"gh","bus":"b1","pmax":200,"capacity":200,"gcost":0},
      {"uid":2,"name":"gt","bus":"b1","pmax":500,"capacity":500,
       "gcost":100}],
     "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":100}],
     "flow_array":[{"uid":1,"name":"f1","discharge":50,"active":[1,0]}],
     "turbine_array":[{"uid":1,"name":"t1","generator":"gh","flow":"f1"}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected));
  }

  SUBCASE("junction_a inactive (built-in waterway turbine)")
  {
    constexpr std::string_view json = R"({
     "name":"junction_inactive","bus_array":[{"uid":1,"name":"b1"}],
     "generator_array":[
      {"uid":1,"name":"gh","bus":"b1","pmax":200,"capacity":200,"gcost":0},
      {"uid":2,"name":"gt","bus":"b1","pmax":500,"capacity":500,
       "gcost":100}],
     "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":100}],
     "junction_array":[{"uid":1,"name":"j1","active":[1,0]}],
     "flow_array":[{"uid":1,"name":"f1","junction":"j1","direction":1,
       "discharge":50}],
     "turbine_array":[{"uid":1,"name":"t1","generator":"gh",
       "junction_a":"j1"}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected));
  }
}

TEST_CASE("GasTurbine not effective closes the gas-fired generator")  // NOLINT
{
  // 100 MW demand; diesel at $220/MWh; gas unit G_T burns 0.2 gas units
  // per MWh bought at $300 ⇒ $60/MWh.  Stage 1: G_T 100 = $6000.
  // Stage 2: the gas turbine (or its gas node) is inactive ⇒ G_T cannot
  // burn gas ⇒ diesel 100 = $22000.
  constexpr double expected = 6000.0 + 22000.0;

  SUBCASE("gas turbine inactive")
  {
    constexpr std::string_view json = R"({
     "name":"gt_inactive","bus_array":[{"uid":1,"name":"B"}],
     "generator_array":[
      {"uid":1,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0},
      {"uid":2,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
       "gcost":220}],
     "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":100}],
     "gas_node_array":[{"uid":1,"name":"T","pmin":30,"pmax":70,
       "p_init":50}],
     "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"T",
       "fmax":1000,"cost":300}],
     "gas_turbine_array":[{"uid":1,"name":"G_T_gas","generator":"G_T",
       "gas_node":"T","heat_rate":0.2,"active":[1,0]}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected).epsilon(1e-6));
  }

  SUBCASE("gas node inactive")
  {
    constexpr std::string_view json = R"({
     "name":"node_inactive","bus_array":[{"uid":1,"name":"B"}],
     "generator_array":[
      {"uid":1,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0},
      {"uid":2,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
       "gcost":220}],
     "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":100}],
     "gas_node_array":[{"uid":1,"name":"T","pmin":30,"pmax":70,
       "p_init":50,"active":[1,0]}],
     "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"T",
       "fmax":1000,"cost":300}],
     "gas_turbine_array":[{"uid":1,"name":"G_T_gas","generator":"G_T",
       "gas_node":"T","heat_rate":0.2}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected).epsilon(1e-6));
  }
}

TEST_CASE("Converter not effective closes the battery discharge")  // NOLINT
{
  // Traditional (multi-element) battery: discharge generator bgen, charge
  // demand bdem (fcost 0 = dispatchable), converter conv.  Nothing can be
  // gained by storage within one stage at η = 1, so the only legal
  // dispatch is thermal 100 MW in both stages = $20000.  Before the fix a
  // non-effective converter left bgen (or the battery fout) free.
  constexpr double expected = 2 * 100.0 * 100.0;

  SUBCASE("converter inactive, battery active")
  {
    constexpr std::string_view json = R"({
     "name":"conv_inactive","bus_array":[{"uid":1,"name":"b1"}],
     "generator_array":[
      {"uid":1,"name":"gt","bus":"b1","pmax":500,"capacity":500,
       "gcost":100},
      {"uid":2,"name":"bgen","bus":"b1","pmax":50,"capacity":50,
       "gcost":0}],
     "demand_array":[
      {"uid":1,"name":"d1","bus":"b1","lmax":100},
      {"uid":2,"name":"bdem","bus":"b1","lmax":50,"fcost":0}],
     "battery_array":[{"uid":1,"name":"bat","emin":0,"emax":100,
       "eini":0,"input_efficiency":1,"output_efficiency":1}],
     "converter_array":[{"uid":1,"name":"conv","battery":"bat",
       "generator":"bgen","demand":"bdem","active":[1,0]}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected));
  }

  SUBCASE("battery not yet in service, converter active")
  {
    constexpr std::string_view json = R"({
     "name":"bat_inactive","bus_array":[{"uid":1,"name":"b1"}],
     "generator_array":[
      {"uid":1,"name":"gt","bus":"b1","pmax":500,"capacity":500,
       "gcost":100},
      {"uid":2,"name":"bgen","bus":"b1","pmax":50,"capacity":50,
       "gcost":0}],
     "demand_array":[
      {"uid":1,"name":"d1","bus":"b1","lmax":100},
      {"uid":2,"name":"bdem","bus":"b1","lmax":50,"fcost":0}],
     "battery_array":[{"uid":1,"name":"bat","emin":0,"emax":100,
       "eini":0,"input_efficiency":1,"output_efficiency":1,
       "active":[0,1]}],
     "converter_array":[{"uid":1,"name":"conv","battery":"bat",
       "generator":"bgen","demand":"bdem"}]})";
    CHECK(solve_obj(json) == doctest::Approx(expected));
  }
}

TEST_CASE("Pump without load closes the pumping waterway")  // NOLINT
{
  // Inflow 50 m³/s at the lower junction jl (which may spill).  The only
  // way to the upper junction ju — where turbine t1 feeds hydro unit gh at
  // 1 MW per m³/s — is waterway w, lifted by pump p (1 MW per m³/s).
  // Pumping 50 costs 50 MW and yields 50 MW, so with a working pump the
  // optimum is thermal 100 = $10000 per stage.  With no pump load (lmax 0
  // or pump inactive) nothing may be lifted ⇒ also $10000 per stage.
  // Before the fix the water rose for free and gh covered 50 MW.
  constexpr double expected = 2 * 100.0 * 100.0;

  const auto make_json =
      [](std::string_view pump_extra, std::string_view pump_lmax)
  {
    return std::format(R"({{
     "name":"pump","bus_array":[{{"uid":1,"name":"b1"}}],
     "generator_array":[
      {{"uid":1,"name":"gh","bus":"b1","pmax":200,"capacity":200,
        "gcost":0}},
      {{"uid":2,"name":"gt","bus":"b1","pmax":500,"capacity":500,
        "gcost":100}}],
     "demand_array":[
      {{"uid":1,"name":"d1","bus":"b1","lmax":100}},
      {{"uid":2,"name":"pd","bus":"b1","lmax":{},"fcost":0}}],
     "junction_array":[{{"uid":1,"name":"jl","drain":true}},
                       {{"uid":2,"name":"ju"}}],
     "flow_array":[{{"uid":1,"name":"f1","junction":"jl","direction":1,
       "discharge":50}}],
     "waterway_array":[{{"uid":1,"name":"w","junction_a":"jl",
       "junction_b":"ju","fmin":0,"fmax":100}}],
     "turbine_array":[{{"uid":1,"name":"t1","generator":"gh",
       "junction_a":"ju"}}],
     "pump_array":[{{"uid":1,"name":"p","waterway":"w","demand":"pd",
       "pump_factor":1{}}}]}})",
                       pump_lmax,
                       pump_extra);
  };

  SUBCASE("working pump (reference)")
  {
    CHECK(solve_obj(make_json("", "100")) == doctest::Approx(expected));
  }
  SUBCASE("pump load lmax = 0")
  {
    CHECK(solve_obj(make_json("", "0")) == doctest::Approx(expected));
  }
  SUBCASE("pump inactive")
  {
    CHECK(solve_obj(make_json(R"(,"active":0)", "100"))
          == doctest::Approx(expected));
  }
}

TEST_CASE(
    "Electric compressor with an inactive bus draws no free power")  // NOLINT
{
  // Gas city at node T (pmin 50 bar) is supplied from S (pmax 40 bar)
  // through an electric compressor whose motor bus Bc (with a $220/MWh
  // unit) is active only in stage 1.  In stage 2 the compressor has no
  // power source, so it cannot move gas (fixed-fraction energy model):
  // the city is unserved at $3000 per unit (50 units ⇒ $150000).
  // Before the fix the compressor ran on free electricity in stage 2.
  constexpr std::string_view json = R"({
   "name":"cmp_bus","bus_array":[{"uid":1,"name":"Bc","active":[1,0]}],
   "generator_array":[{"uid":1,"name":"DIESEL","bus":"Bc","pmax":2000,
     "capacity":2000,"gcost":220}],
   "gas_node_array":[
    {"uid":1,"name":"S","pmin":30,"pmax":40,"p_init":40},
    {"uid":2,"name":"T","pmin":50,"pmax":70,"p_init":50}],
   "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"S",
     "fmax":1000,"cost":300}],
   "gas_demand_array":[{"uid":1,"name":"CITY","gas_node":"T","load":50,
     "fail_cost":3000}],
   "gas_compressor_array":[{"uid":1,"name":"CS","gas_node_in":"S",
     "gas_node_out":"T","fmax":300,"max_ratio":2.0,
     "fuel_model":"fixed_fraction","fuel_fraction":0.01,
     "drive":"electric","bus":"Bc"}]})";
  const double obj = solve_obj(json);
  // Stage 2 alone costs the full city failure.
  constexpr double city_fail = 50.0 * 3000.0;
  CHECK(obj >= city_fail - 1e-6);
  // Stage 1 serves the city (gas $300 × 50 + a little diesel).
  CHECK(obj < 2.0 * city_fail);
}

TEST_CASE(
    "validate_planning: couplers and partners share the schedule")  // NOLINT
{
  // The LP backstop above closes the partner; validation rejects the
  // inconsistent input up front (class / uid / name and stage / phase).
  constexpr std::string_view head = R"({
   "name":"v","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[
    {"uid":1,"name":"gh","bus":"b1","pmax":200,"capacity":200},
    {"uid":2,"name":"gh2","bus":"b1","pmax":200,"capacity":200}],
   "flow_array":[{"uid":1,"name":"f1","discharge":50}],
   "junction_array":[{"uid":1,"name":"j1","active":[1,0]}],)";

  SUBCASE("turbine inactive, generator active")
  {
    const auto r = validate_json(std::string(head) + R"(
     "turbine_array":[{"uid":7,"name":"t7","generator":"gh","flow":"f1",
       "active":[1,0]}]})");
    CHECK(has_error(r, "Turbine uid=7 't7': its generator Generator uid=1"));
    CHECK(has_error(r, "stage uid=2"));
  }
  SUBCASE("turbine active, junction_a inactive (shared partner)")
  {
    const auto r = validate_json(std::string(head) + R"(
     "turbine_array":[{"uid":7,"name":"t7","generator":"gh",
       "junction_a":"j1"}]})");
    CHECK(has_error(r, "its junction_a Junction uid=1 'j1' is inactive"));
  }
  SUBCASE("turbine inactive with its junction is valid")
  {
    const auto r = validate_json(std::string(head) + R"(
     "turbine_array":[{"uid":7,"name":"t7","generator":"gh",
       "junction_a":"j1","active":[1,0]}]})");
    CHECK_FALSE(has_error(r, "Turbine uid=7 't7': its junction_a"));
    // ... but the generator stays active in stage 2.
    CHECK(has_error(r, "Turbine uid=7 't7': its generator"));
  }
  SUBCASE("two turbines on one generator (uid and name spelling)")
  {
    const auto r = validate_json(std::string(head) + R"(
     "turbine_array":[
      {"uid":7,"name":"t7","generator":"gh","flow":"f1"},
      {"uid":8,"name":"t8","generator":1,"flow":"f1"}]})");
    CHECK(has_error(r, "Generator uid=1 'gh' is fed by 2 turbines"));
  }
  SUBCASE("must_run unit whose turbine is inactive")
  {
    const auto r = validate_json(std::string(head) + R"(
     "turbine_array":[{"uid":7,"name":"t7","generator":"gh","flow":"f1",
       "active":[1,0]}],
     "commitment_array":[{"uid":3,"name":"c3","generator":"gh",
       "must_run":true}]})");
    CHECK(has_error(r, "Commitment uid=3 'c3': must_run unit Generator"));
  }
  SUBCASE("converter vs battery, pump vs load, compressor vs bus")
  {
    const auto r = validate_json(R"({
     "name":"v2","bus_array":[{"uid":1,"name":"b1"},
       {"uid":2,"name":"bc","active":[1,0]}],
     "generator_array":[{"uid":2,"name":"bgen","bus":"b1","pmax":50}],
     "demand_array":[{"uid":2,"name":"bdem","bus":"b1","lmax":50},
       {"uid":3,"name":"pd","bus":"b1","lmax":50,"active":[0,1]}],
     "battery_array":[{"uid":1,"name":"bat","emax":100,"active":[0,1]}],
     "converter_array":[{"uid":1,"name":"conv","battery":"bat",
       "generator":"bgen","demand":"bdem"}],
     "junction_array":[{"uid":1,"name":"jl"},{"uid":2,"name":"ju"}],
     "waterway_array":[{"uid":1,"name":"w","junction_a":"jl",
       "junction_b":"ju"}],
     "pump_array":[{"uid":1,"name":"p","waterway":"w","demand":"pd"}],
     "gas_node_array":[{"uid":1,"name":"S","pmin":30,"pmax":40},
       {"uid":2,"name":"T","pmin":30,"pmax":70}],
     "gas_compressor_array":[{"uid":1,"name":"CS","gas_node_in":"S",
       "gas_node_out":"T","fmax":300,"drive":"electric","bus":"bc"}]})");
    CHECK(has_error(r, "Converter uid=1 'conv': its battery Battery uid=1"));
    CHECK(has_error(r, "Pump uid=1 'p': its load demand Demand uid=3"));
    CHECK(has_error(r, "GasCompressor uid=1 'CS': its motor bus Bus uid=2"));
  }
}
