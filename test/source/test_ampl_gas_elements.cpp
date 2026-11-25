// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// PAMPL / user-constraint surface of the gas-network family (GasNode,
// GasSupply, GasDemand, GasPipe, GasCompressor, GasTurbine) and the LNG
// terminal (GNL):
//
//   * every registered parameter resolves to its input value;
//   * a user constraint on each LP attribute binds and moves the
//     dispatch to the analytically expected point;
//   * `sum(<class>(all : <predicate>).<attr>)` uses the registered
//     iterators + metadata (`type`, `gas_node`);
//   * a misspelled attribute / element name fails with an informative
//     error naming the valid attributes / candidate names.
//
// The base case (one stage, one 1-h block) is economically transparent:
// gas costs 300 $/km³ at SUPPLY (field, node S) and 500 $/km³ at REGAS
// (LNG regas, node T); the gas turbine burns 0.2 km³/MWh, so its power
// costs 60 (SUPPLY) or 100 (REGAS) $/MWh against a 220 $/MWh diesel.
// Unconstrained: G_T = 600 MW, DIESEL = 100 MW, SUPPLY = 50 + 0.2·600 =
// 170 km³/h through S→A (pipe) → T (compressor), REGAS = 0.

#include <algorithm>
#include <map>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/constraint_parser.hpp>
#include <gtopt/gas_compressor_lp.hpp>
#include <gtopt/gas_demand_lp.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_pipe_lp.hpp>
#include <gtopt/gas_supply_lp.hpp>
#include <gtopt/gas_turbine_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view kAmplGas = R"({
 "name":"ampl_gas","bus_array":[{"uid":1,"name":"B"}],
 "generator_array":[
  {"uid":1,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0},
  {"uid":2,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
   "gcost":220}],
 "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":700}],
 "gas_node_array":[
  {"uid":1,"name":"S","type":"field","pmin":30,"pmax":60,
   "pressure_cost":-0.0001},
  {"uid":2,"name":"A","pmin":30,"pmax":60,"pressure_cost":-0.0001},
  {"uid":3,"name":"T","type":"city_gate","pmin":40,"pmax":70,
   "pressure_cost":-0.0001}],
 "gas_supply_array":[
  {"uid":1,"name":"SUPPLY","type":"field","gas_node":"S","fmin":5,
   "fmax":1000,"cost":300,"volume":5000},
  {"uid":2,"name":"REGAS","type":"lng_regas","gas_node":"T","fmax":1000,
   "cost":500}],
 "gas_demand_array":[{"uid":1,"name":"CITY","type":"residential",
   "gas_node":"T","load":50,"fail_cost":3000}],
 "gas_pipe_array":[{"uid":1,"name":"S_A","type":"transmission",
   "gas_node_a":"S","gas_node_b":"A","weymouth_mode":"none","fmax":300}],
 "gas_compressor_array":[{"uid":1,"name":"CS","gas_node_in":"A",
   "gas_node_out":"T","fmax":300,"max_ratio":1.5,"fuel_model":"none",
   "allow_bypass":true,"power_max":7}],
 "gas_turbine_array":[{"uid":1,"name":"G_T_gas","type":"ccgt",
   "generator":"G_T","gas_node":"T","heat_rate":0.2}]})";

/// tiny_linepack (4 blocks): one 80 km Weymouth pipe with line pack.
constexpr std::string_view kAmplGasLinepack = R"({
 "name":"ampl_gas_linepack","bus_array":[{"uid":1,"name":"B"}],
 "generator_array":[
  {"uid":1,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0},
  {"uid":2,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
   "gcost":220}],
 "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":500}],
 "gas_node_array":[
  {"uid":1,"name":"S","pmin":30,"pmax":60,"p_init":55,
   "pressure_cost":-0.0001},
  {"uid":2,"name":"T","pmin":35,"pmax":60,"p_init":40,
   "pressure_cost":-0.0001}],
 "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"S","fmax":1000,
   "cost":300}],
 "gas_demand_array":[{"uid":1,"name":"CITY","gas_node":"T",
   "load":[[40,40,160,40]],"fail_cost":3000}],
 "gas_pipe_array":[{"uid":1,"name":"S_T","gas_node_a":"S","gas_node_b":"T",
   "length":80,"diameter":406,"fmax":300,"segments":8,"linepack":true}],
 "gas_turbine_array":[{"uid":1,"name":"G_T_gas","generator":"G_T",
   "gas_node":"T","heat_rate":0.2}]})";

Simulation ampl_gas_simulation(int nblocks)
{
  Simulation sim;
  for (int b = 0; b < nblocks; ++b) {
    sim.block_array.push_back({
        .uid = Uid {b + 1},
        .duration = 1.0,
    });
  }
  sim.stage_array = {
      {
          .uid = Uid {1},
          .first_block = 0,
          .count_block = static_cast<Size>(nblocks),
      },
  };
  sim.scenario_array = {
      {
          .uid = Uid {1},
      },
  };
  return sim;
}

PlanningOptionsLP ampl_gas_options()
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1000.0;
  return PlanningOptionsLP {popts};
}

System with_ucs(std::string_view json, std::vector<std::string> exprs)
{
  auto sys = daw::json::from_json<System>(json);
  int uid = 1;
  for (auto& e : exprs) {
    sys.user_constraint_array.push_back({
        .uid = Uid {uid},
        .name = std::format("uc{}", uid),
        .expression = std::move(e),
    });
    ++uid;
  }
  return sys;
}

/// Per-element, per-block primal values of one solved case.
struct AmplGasSol
{
  bool optimal {false};
  std::map<std::string, std::vector<double>> gen;
  std::map<std::string, std::vector<double>> supply;
  std::map<std::string, std::vector<double>> pipe;  ///< flow or mean flow
  std::map<std::string, std::vector<double>> comp;
  std::map<std::string, std::vector<double>> fail;
  std::map<std::string, std::vector<double>> pi;
};

AmplGasSol ampl_gas_solve(const System& sys, int nblocks = 1)
{
  const auto simulation = ampl_gas_simulation(nblocks);
  const auto options = ampl_gas_options();
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  AmplGasSol out;
  if (!result.has_value() || result.value() != 0) {
    return out;
  }
  out.optimal = true;
  const auto& scenario = system_lp.scene().scenarios()[0];
  const auto& stage = system_lp.phase().stages()[0];
  const auto sol = li.get_col_sol();

  for (const auto& g : system_lp.elements<GeneratorLP>()) {
    for (const auto& [b, c] : g.lookup_generation_cols(scenario, stage)) {
      out.gen[g.generator().name].push_back(sol[c]);
    }
  }
  for (const auto& s : system_lp.elements<GasSupplyLP>()) {
    for (const auto& [b, c] : s.flow_cols_at(scenario, stage)) {
      out.supply[s.gas_supply().name].push_back(sol[c]);
    }
  }
  for (const auto& p : system_lp.elements<GasPipeLP>()) {
    const auto& name = p.gas_pipe().name;
    if (p.has_linepack()) {
      const auto& fout = p.flow_out_cols_at(scenario, stage);
      for (const auto& [b, c] : p.flow_in_cols_at(scenario, stage)) {
        out.pipe[name].push_back(0.5 * (sol[c] + sol[fout.at(b)]));
      }
    } else {
      for (const auto& [b, c] : p.flow_cols_at(scenario, stage)) {
        out.pipe[name].push_back(sol[c]);
      }
    }
  }
  for (const auto& cs : system_lp.elements<GasCompressorLP>()) {
    for (const auto& [b, c] : cs.flow_cols_at(scenario, stage)) {
      out.comp[cs.gas_compressor().name].push_back(sol[c]);
    }
  }
  for (const auto& d : system_lp.elements<GasDemandLP>()) {
    for (const auto& [b, c] : d.fail_cols_at(scenario, stage)) {
      out.fail[d.gas_demand().name].push_back(sol[c]);
    }
  }
  for (const auto& n : system_lp.elements<GasNodeLP>()) {
    for (const auto& [b, c] : n.pi_cols_at(scenario, stage)) {
      out.pi[n.gas_node().name].push_back(sol[c]);
    }
  }
  return out;
}

/// Build (no solve) and return the message of the build-time error.
std::string ampl_gas_build_error(const System& sys, int nblocks = 1)
{
  const auto simulation = ampl_gas_simulation(nblocks);
  const auto options = ampl_gas_options();
  SimulationLP simulation_lp(simulation, options);
  try {
    const SystemLP system_lp(sys, simulation_lp);
  } catch (const std::exception& ex) {
    return ex.what();
  }
  return {};
}

}  // namespace

TEST_CASE("AMPL gas elements - baseline dispatch")  // NOLINT
{
  const auto s = ampl_gas_solve(with_ucs(kAmplGas, {}));
  REQUIRE(s.optimal);
  CHECK(s.gen.at("G_T")[0] == doctest::Approx(600.0));
  CHECK(s.gen.at("DIESEL")[0] == doctest::Approx(100.0));
  CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(170.0));
  CHECK(s.supply.at("REGAS")[0] == doctest::Approx(0.0).scale(1.0));
  CHECK(s.pipe.at("S_A")[0] == doctest::Approx(170.0));
  CHECK(s.comp.at("CS")[0] == doctest::Approx(170.0));
  CHECK(s.fail.at("CITY")[0] == doctest::Approx(0.0).scale(1.0));
}

TEST_CASE("AMPL gas elements - parameters resolve to input values")  // NOLINT
{
  const auto simulation = ampl_gas_simulation(1);
  const auto options = ampl_gas_options();
  SimulationLP simulation_lp(simulation, options);
  simulation_lp.set_need_ampl_variables(/*v=*/true);
  const auto sys = with_ucs(kAmplGas, {});
  const SystemLP system_lp(sys, simulation_lp);
  const auto& sc = system_lp.system_context();

  const auto s1 = make_uid<Stage>(1);
  const auto b1 = make_uid<Block>(1);
  const auto param = [&](std::string_view cls, std::string_view attr, int uid)
  {
    CAPTURE(cls);
    CAPTURE(attr);
    const auto fn = sc.find_ampl_param(cls, attr);
    REQUIRE(fn != nullptr);
    return fn(sc, Uid {uid}, s1, b1).value_or(-12345.0);
  };

  CHECK(param("gas_node", "pmin", 3) == doctest::Approx(40.0));
  CHECK(param("gas_node", "pmax", 3) == doctest::Approx(70.0));
  CHECK(param("gas_node", "pressure_cost", 3) == doctest::Approx(-0.0001));
  CHECK(param("gas_supply", "fmin", 1) == doctest::Approx(5.0));
  CHECK(param("gas_supply", "fmax", 1) == doctest::Approx(1000.0));
  CHECK(param("gas_supply", "cost", 1) == doctest::Approx(300.0));
  CHECK(param("gas_supply", "volume", 1) == doctest::Approx(5000.0));
  CHECK(param("gas_supply", "cost", 2) == doctest::Approx(500.0));
  CHECK(param("gas_demand", "load", 1) == doctest::Approx(50.0));
  CHECK(param("gas_demand", "fail_cost", 1) == doctest::Approx(3000.0));
  CHECK(param("gas_pipe", "fmax", 1) == doctest::Approx(300.0));
  CHECK(param("gas_compressor", "fmax", 1) == doctest::Approx(300.0));
  CHECK(param("gas_compressor", "max_ratio", 1) == doctest::Approx(1.5));
  CHECK(param("gas_compressor", "power_max", 1) == doctest::Approx(7.0));
  CHECK(param("gas_turbine", "heat_rate", 1) == doctest::Approx(0.2));

  // Unset optional fields resolve to "no value", not to a fake zero.
  const auto fn = sc.find_ampl_param("gas_supply", "volume");
  REQUIRE(fn != nullptr);
  CHECK_FALSE(fn(sc, Uid {2}, s1, b1).has_value());

  // Every gas class is iterable for `sum(<class>(all).<attr>)`.
  for (const auto* cls : {"gas_node",
                          "gas_supply",
                          "gas_demand",
                          "gas_pipe",
                          "gas_compressor",
                          "gas_turbine",
                          "lng_terminal",
                          "line_commitment"})
  {
    CAPTURE(cls);
    CHECK(sc.find_ampl_iter(cls) != nullptr);
  }
}

TEST_CASE("AMPL gas elements - user constraints bind")  // NOLINT
{
  SUBCASE("gas_supply.flow cap shifts gas to the LNG regas")
  {
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas, {"gas_supply('SUPPLY').flow <= 100"}));
    REQUIRE(s.optimal);
    CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(100.0));
    CHECK(s.supply.at("REGAS")[0] == doctest::Approx(70.0));
    CHECK(s.gen.at("G_T")[0] == doctest::Approx(600.0));
  }

  SUBCASE("gas_supply.fmax parameter on the right-hand side")
  {
    const auto s = ampl_gas_solve(with_ucs(
        kAmplGas,
        {"gas_supply('SUPPLY').flow <= 0.1 * gas_supply('SUPPLY').fmax"}));
    REQUIRE(s.optimal);
    CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(100.0));
  }

  SUBCASE("gas_pipe.flow with its fmax parameter")
  {
    const auto s = ampl_gas_solve(with_ucs(
        kAmplGas, {"gas_pipe('S_A').flow <= gas_pipe('S_A').fmax - 220"}));
    REQUIRE(s.optimal);
    CHECK(s.pipe.at("S_A")[0] == doctest::Approx(80.0));
    CHECK(s.supply.at("REGAS")[0] == doctest::Approx(90.0));
  }

  SUBCASE("gas_compressor.flow with its fmax parameter")
  {
    const auto s = ampl_gas_solve(with_ucs(
        kAmplGas,
        {"gas_compressor('CS').flow <= 0.25 * gas_compressor('CS').fmax"}));
    REQUIRE(s.optimal);
    CHECK(s.comp.at("CS")[0] == doctest::Approx(75.0));
    CHECK(s.supply.at("REGAS")[0] == doctest::Approx(95.0));
  }

  SUBCASE("gas_turbine.generation is the linked generator column")
  {
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas, {"gas_turbine('G_T_gas').generation <= 400"}));
    REQUIRE(s.optimal);
    CHECK(s.gen.at("G_T")[0] == doctest::Approx(400.0));
    CHECK(s.gen.at("DIESEL")[0] == doctest::Approx(300.0));
    CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(130.0));
  }

  SUBCASE("gas_turbine.fuel = heat_rate * generation")
  {
    // fuel <= 250 * heat_rate = 50 km³/h  ⇒  G_T = 50 / 0.2 = 250 MW.
    const auto s =
        ampl_gas_solve(with_ucs(kAmplGas,
                                {"gas_turbine('G_T_gas').fuel <= 250 * "
                                 "gas_turbine('G_T_gas').heat_rate"}));
    REQUIRE(s.optimal);
    CHECK(s.gen.at("G_T")[0] == doctest::Approx(250.0));
    CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(100.0));
  }

  SUBCASE("gas_demand.served = load - fail")
  {
    const auto s = ampl_gas_solve(with_ucs(
        kAmplGas,
        {"gas_demand('CITY').served <= gas_demand('CITY').load - 20"}));
    REQUIRE(s.optimal);
    CHECK(s.fail.at("CITY")[0] == doctest::Approx(20.0));
  }

  SUBCASE("gas_demand.fail floor")
  {
    const auto s =
        ampl_gas_solve(with_ucs(kAmplGas, {"gas_demand('CITY').fail >= 5"}));
    REQUIRE(s.optimal);
    CHECK(s.fail.at("CITY")[0] == doctest::Approx(5.0));
  }

  SUBCASE("gas_node.pi (squared pressure) with its pmin parameter")
  {
    // pi_T <= 50 * pmin = 2000 bar² (the tie-break pushes it to the cap).
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas, {"gas_node('T').pi <= 50 * gas_node('T').pmin"}));
    REQUIRE(s.optimal);
    CHECK(s.pi.at("T")[0] == doctest::Approx(2000.0));
  }
}

TEST_CASE("AMPL gas elements - sum over classes with predicates")  // NOLINT
{
  SUBCASE("type predicate on gas_supply")
  {
    // SUPPLY ≤ 100, LNG regas ≤ 30 ⇒ 130 km³/h ⇒ G_T = (130 − 50)/0.2.
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas,
                 {
                     "gas_supply('SUPPLY').flow <= 100",
                     "sum(gas_supply(all : type = 'lng_regas').flow) <= 30",
                 }));
    REQUIRE(s.optimal);
    CHECK(s.supply.at("REGAS")[0] == doctest::Approx(30.0));
    CHECK(s.gen.at("G_T")[0] == doctest::Approx(400.0));
  }

  SUBCASE("gas_node predicate (numeric uid) on gas_supply")
  {
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas,
                 {
                     "gas_supply('SUPPLY').flow <= 100",
                     "sum(gas_supply(all : gas_node = 3).flow) <= 20",
                 }));
    REQUIRE(s.optimal);
    CHECK(s.supply.at("REGAS")[0] == doctest::Approx(20.0));
    CHECK(s.gen.at("G_T")[0] == doctest::Approx(350.0));
  }

  SUBCASE("sum over all gas turbines")
  {
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas, {"sum(gas_turbine(all).generation) <= 450"}));
    REQUIRE(s.optimal);
    CHECK(s.gen.at("G_T")[0] == doctest::Approx(450.0));
  }

  SUBCASE("sum over pipes and compressors by type")
  {
    const auto s = ampl_gas_solve(
        with_ucs(kAmplGas,
                 {
                     "sum(gas_pipe(all : type = 'transmission').flow) <= 120",
                     "sum(gas_compressor(all : gas_node_out = 3).flow) <= 110",
                 }));
    REQUIRE(s.optimal);
    CHECK(s.pipe.at("S_A")[0] == doctest::Approx(110.0));
    CHECK(s.comp.at("CS")[0] == doctest::Approx(110.0));
  }
}

TEST_CASE("AMPL gas elements - line-pack pipe flow is the mean flow")  // NOLINT
{
  const auto base = ampl_gas_solve(with_ucs(kAmplGasLinepack, {}), 4);
  REQUIRE(base.optimal);
  const auto& f0 = base.pipe.at("S_T");
  REQUIRE(*std::ranges::max_element(f0) > 100.0 + 1e-6);

  const auto s = ampl_gas_solve(
      with_ucs(kAmplGasLinepack, {"gas_pipe('S_T').flow <= 100"}), 4);
  REQUIRE(s.optimal);
  const auto& f = s.pipe.at("S_T");
  REQUIRE(f.size() == 4);
  for (const double v : f) {
    CHECK(v <= 100.0 + 1e-6);
  }
  CHECK(*std::ranges::max_element(f) == doctest::Approx(100.0));
}

TEST_CASE("AMPL gas elements - bad references give clear errors")  // NOLINT
{
  SUBCASE("misspelled attribute lists the valid ones")
  {
    const auto msg = ampl_gas_build_error(
        with_ucs(kAmplGas, {"gas_supply('SUPPLY').flw <= 10"}));
    CAPTURE(msg);
    CHECK(msg.find("unknown attribute 'flw'") != std::string::npos);
    CHECK(msg.find("gas_supply") != std::string::npos);
    CHECK(msg.find("flow") != std::string::npos);
  }

  SUBCASE("misspelled turbine attribute")
  {
    const auto msg = ampl_gas_build_error(
        with_ucs(kAmplGas, {"gas_turbine('G_T_gas').fuell <= 10"}));
    CAPTURE(msg);
    CHECK(msg.find("unknown attribute 'fuell'") != std::string::npos);
    CHECK(msg.find("fuel") != std::string::npos);
  }

  SUBCASE("an unset optional parameter is rejected, not read as zero")
  {
    // REGAS has no stage `volume`.
    const auto msg = ampl_gas_build_error(with_ucs(
        kAmplGas, {"gas_supply('REGAS').flow <= gas_supply('REGAS').volume"}));
    CAPTURE(msg);
    CHECK(msg.find("parameter 'volume' of gas_supply 'REGAS' has no value")
          != std::string::npos);
  }

  SUBCASE("unknown element name suggests candidates")
  {
    const auto msg = ampl_gas_build_error(
        with_ucs(kAmplGas, {"gas_pipe('S_AA').flow <= 10"}));
    CAPTURE(msg);
    CHECK(msg.find("unknown gas_pipe name 'S_AA'") != std::string::npos);
    CHECK(msg.find("S_A") != std::string::npos);
  }
}

// ── LNG terminal (GNL) ─────────────────────────────────────────────────

namespace
{

/// g1 burns LNG (0.2 m³/MWh, 10 $/MWh); g2 is a 100 $/MWh backup.
/// Demand 50 MW over two 1-h blocks; tank 50 000 m³, delivery 20 000 m³
/// over the stage (10 000 m³/h).
System lng_system(std::vector<std::string> exprs)
{
  System sys {
      .name = "AmplLng",
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
                  .lmax = 50.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 10.0,
                  .capacity = 100.0,
              },
              {
                  .uid = Uid {2},
                  .name = "g2",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 100.0,
              },
          },
      .lng_terminal_array =
          {
              {
                  .uid = Uid {1},
                  .name = "gnl1",
                  .emin = 0.0,
                  .emax = 100'000.0,
                  .ecost = 0.5,
                  .eini = 50'000.0,
                  .delivery = 20'000.0,
                  .spillway_cost = 100.0,
                  .spillway_capacity = 100'000.0,
                  .use_state_variable = true,
                  .generators =
                      {
                          {
                              .generator = Uid {1},
                              .heat_rate = 0.2,
                          },
                      },
              },
          },
  };
  int uid = 1;
  for (auto& e : exprs) {
    sys.user_constraint_array.push_back({
        .uid = Uid {uid},
        .name = std::format("lng_uc{}", uid),
        .expression = std::move(e),
    });
    ++uid;
  }
  return sys;
}

struct LngSol
{
  bool optimal {false};
  std::vector<double> g1;
  std::vector<double> g2;
  std::vector<double> volume;
};

LngSol lng_solve(const System& sys)
{
  const auto simulation = ampl_gas_simulation(2);
  const auto options = ampl_gas_options();
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  LngSol out;
  if (!result.has_value() || result.value() != 0) {
    return out;
  }
  out.optimal = true;
  const auto& scenario = system_lp.scene().scenarios()[0];
  const auto& stage = system_lp.phase().stages()[0];
  const auto sol = li.get_col_sol();
  for (const auto& g : system_lp.elements<GeneratorLP>()) {
    auto& v = g.generator().name == "g1" ? out.g1 : out.g2;
    for (const auto& [b, c] : g.lookup_generation_cols(scenario, stage)) {
      v.push_back(sol[c]);
    }
  }
  const auto& lng = system_lp.elements<LngTerminalLP>().front();
  for (const auto& [b, c] : lng.energy_cols_at(scenario, stage)) {
    out.volume.push_back(sol[c]);
  }
  return out;
}

}  // namespace

TEST_CASE("AMPL LNG terminal - attributes and parameters")  // NOLINT
{
  SUBCASE("parameters resolve")
  {
    const auto simulation = ampl_gas_simulation(2);
    const auto options = ampl_gas_options();
    SimulationLP simulation_lp(simulation, options);
    simulation_lp.set_need_ampl_variables(/*v=*/true);
    const auto sys = lng_system({});
    const SystemLP system_lp(sys, simulation_lp);
    const auto& sc = system_lp.system_context();
    const auto s1 = make_uid<Stage>(1);
    const auto b1 = make_uid<Block>(1);
    for (const auto& [attr, expected] :
         std::to_array<std::pair<std::string_view, double>>({
             {"emin", 0.0},
             {"emax", 100'000.0},
             {"ecost", 0.5},
             {"delivery", 20'000.0},
         }))
    {
      CAPTURE(attr);
      const auto fn = sc.find_ampl_param("lng_terminal", attr);
      REQUIRE(fn != nullptr);
      CHECK(fn(sc, Uid {1}, s1, b1).value_or(-1.0)
            == doctest::Approx(expected));
    }
  }

  SUBCASE("fuel = heat_rate * generation of the linked generators")
  {
    // fuel <= 6 m³/h ⇒ g1 <= 30 MW, g2 covers the remaining 20 MW.
    const auto s = lng_solve(lng_system({"lng_terminal('gnl1').fuel <= 6"}));
    REQUIRE(s.optimal);
    REQUIRE(s.g1.size() == 2);
    for (std::size_t b = 0; b < 2; ++b) {
      CHECK(s.g1[b] == doctest::Approx(30.0));
      CHECK(s.g2[b] == doctest::Approx(20.0));
    }
  }

  SUBCASE("volume is the tank level (alias of energy) vs emax param")
  {
    const auto s = lng_solve(lng_system(
        {"lng_terminal('gnl1').volume <= lng_terminal('gnl1').emax - 45000"}));
    REQUIRE(s.optimal);
    REQUIRE(s.volume.size() == 2);
    for (const double v : s.volume) {
      CHECK(v <= 55'000.0 + 1e-6);
    }
    CHECK(*std::ranges::max_element(s.volume) == doctest::Approx(55'000.0));
  }

  SUBCASE("misspelled attribute is rejected")
  {
    const auto msg = ampl_gas_build_error(
        lng_system({"lng_terminal('gnl1').volum <= 10"}), 2);
    CAPTURE(msg);
    CHECK(msg.find("unknown attribute 'volum'") != std::string::npos);
    CHECK(msg.find("volume") != std::string::npos);
  }
}

TEST_CASE("AMPL parser - every iterable element class is parseable")  // NOLINT
{
  // Mirrors `register_ampl_iter` in ampl_dispatch_registry.cpp (and the
  // Python ELEMENT_TYPES): a class with registered AMPL attributes must
  // also be an element type for the expression parser, or its
  // `class("x").attr` references fail with a parse error before ever
  // reaching the resolver (as `gas_turbine` did).
  for (const auto* cls :
       {
           "allowance_pool",
           "ammonia_node",
           "ammonia_storage",
           "battery",
           "bus",
           "carrier_converter",
           "commitment",
           "converter",
           "decision_variable",
           "demand",
           "emission_source",
           "emission_zone",
           "flow",
           "flow_right",
           "fuel",
           "gas_compressor",
           "gas_demand",
           "gas_node",
           "gas_pipe",
           "gas_supply",
           "gas_turbine",
           "generator",
           "hydrogen_demand",
           "hydrogen_node",
           "hydrogen_storage",
           "inertia_provision",
           "inertia_zone",
           "junction",
           "line",
           "line_commitment",
           "lng_terminal",
           "reserve_provision",
           "reserve_zone",
           "reservoir",
           "reservoir_discharge_limit",
           "seepage",
           "simple_commitment",
           "thermal_node",
           "thermal_storage",
           "turbine",
           "user_constraint",
           "volume_right",
           "waterway",
       })
  {
    CAPTURE(cls);
    const auto single = std::format("{}('x').attr <= 1", cls);
    CHECK_NOTHROW(std::ignore = ConstraintParser::parse(single));
    const auto summed = std::format("sum({}(all).attr) <= 1", cls);
    CHECK_NOTHROW(std::ignore = ConstraintParser::parse(summed));
  }
}
