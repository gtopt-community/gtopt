// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_gas_network_slp.cpp
 * @brief     Linearised (meshed) gas pipes, the gas SLP and valves
 * @date      Mon Nov 23 2026
 *
 * Reference: a triangle S → A, S → B, A → B with S held at 70 bar.  Its
 * exact Weymouth split (scipy brentq on the loop equation
 * K_SA f_SA|f_SA| + K_AB f_AB|f_AB| = K_SB f_SB|f_SB|) is:
 *
 *   loads A 60, B 40:  f_AB = 1.254921336  p_A 67.266511  p_B 67.265925
 *   loads A 10, B 90:  f_AB = 47.716791997 p_A 67.578674  p_B 66.731047
 */

#include <cmath>
#include <format>
#include <map>
#include <string>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_pipe_lp.hpp>
#include <gtopt/gas_slp.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

constexpr double kSlpKsa = 0.10;
constexpr double kSlpKsb = 0.25;
constexpr double kSlpKab = 0.05;

System slp_triangle(double load_a, double load_b, const char* extra_ab = "")
{
  const auto json = std::format(
      R"({{
   "name":"triangle","bus_array":[{{"uid":1,"name":"B"}}],
   "gas_node_array":[
    {{"uid":1,"name":"S","pmin":70,"pmax":70}},
    {{"uid":2,"name":"A","pmin":1,"pmax":100}},
    {{"uid":3,"name":"B","pmin":1,"pmax":100}}],
   "gas_supply_array":[{{"uid":1,"name":"SUP","gas_node":"S","fmax":1000,
     "cost":1}}],
   "gas_demand_array":[
    {{"uid":1,"name":"DA","gas_node":"A","load":{},"fail_cost":1e4}},
    {{"uid":2,"name":"DB","gas_node":"B","load":{},"fail_cost":1e4}}],
   "gas_pipe_array":[
    {{"uid":1,"name":"SA","gas_node_a":"S","gas_node_b":"A",
      "weymouth_k":{},"fmax":200,"weymouth_mode":"linearized"}},
    {{"uid":2,"name":"SB","gas_node_a":"S","gas_node_b":"B",
      "weymouth_k":{},"fmax":200,"weymouth_mode":"linearized"}},
    {{"uid":3,"name":"AB","gas_node_a":"A","gas_node_b":"B",
      "weymouth_k":{},"fmax":200,"weymouth_mode":"linearized"{}}}]}})",
      load_a,
      load_b,
      kSlpKsa,
      kSlpKsb,
      kSlpKab,
      extra_ab);
  return daw::json::from_json<System>(json);
}

Simulation slp_simulation()
{
  Simulation sim;
  sim.block_array = {{.uid = Uid {1}, .duration = 1.0}};
  sim.stage_array = {
      {.uid = Uid {1}, .first_block = 0, .count_block = 1},
  };
  sim.scenario_array = {{.uid = Uid {1}}};
  return sim;
}

struct SlpResult
{
  bool optimal {false};
  GasSlpStats stats {};
  std::map<std::string, double> flow;
  std::map<std::string, double> pressure;
  double obj {0.0};
};

SlpResult slp_read(SystemLP& system_lp)
{
  SlpResult out;
  auto&& li = system_lp.linear_interface();
  out.optimal = li.is_optimal();
  out.obj = li.get_obj_value();
  const auto& scenario = system_lp.scene().scenarios()[0];
  const auto& stage = system_lp.phase().stages()[0];
  const auto sol = li.get_col_sol();
  for (const auto& node : system_lp.elements<GasNodeLP>()) {
    for (const auto& [buid, col] : node.pi_cols_at(scenario, stage)) {
      out.pressure[node.gas_node().name] = std::sqrt(sol[col]);
    }
  }
  for (const auto& pipe : system_lp.elements<GasPipeLP>()) {
    for (const auto& [buid, col] : pipe.flow_cols_at(scenario, stage)) {
      out.flow[pipe.gas_pipe().name] = sol[col];
    }
  }
  return out;
}

SlpResult slp_solve(const System& sys,
                    const GasOptions& go,
                    LowMemoryMode mode = LowMemoryMode::off)
{
  const auto simulation = slp_simulation();
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.gas_options = go;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  LpMatrixOptions flat_opts;
  flat_opts.low_memory_mode = mode;
  SystemLP system_lp(sys, simulation_lp, flat_opts);
  const auto first = system_lp.resolve();
  if (!first || *first != 0) {
    return {};
  }
  auto stats = gas_slp_refine(system_lp);
  if (!stats) {
    return {};
  }
  system_lp.rebuild_collections_if_needed();
  auto out = slp_read(system_lp);
  out.stats = *stats;

  if (mode != LowMemoryMode::off) {
    // The SLP's coefficient writes must survive a release / reconstruct
    // (replay buffer): re-solving the reconstructed LP gives the same
    // optimum.
    system_lp.release_backend();
    system_lp.reconstruct_backend();
    const auto again = system_lp.resolve();
    REQUIRE(again.has_value());
    const auto replayed = slp_read(system_lp);
    CHECK(replayed.obj == doctest::Approx(out.obj).epsilon(1e-9));
    for (const auto& [name, f] : out.flow) {
      CAPTURE(name);
      CHECK(replayed.flow.at(name) == doctest::Approx(f).epsilon(1e-7));
    }
  }
  return out;
}

GasOptions slp_options(int iters, double damping = 0.5, double tol = 1e-8)
{
  GasOptions go;
  go.slp_iterations = iters;
  go.slp_tolerance = tol;
  go.slp_damping = damping;
  return go;
}

}  // namespace

TEST_CASE("GasPipe linearized - secant and tangent rows")  // NOLINT
{
  // Single pipe S → T carrying 50 with K = 0.1, S at 70 bar.
  const auto one_pipe = [](const char* lin, double f0)
  {
    auto sys = slp_triangle(0.0, 0.0);
    sys.gas_pipe_array.resize(1);
    sys.gas_demand_array.front().load = 50.0;
    sys.gas_demand_array.resize(1);
    auto& pipe = sys.gas_pipe_array.front();
    pipe.linearization = Name {lin};
    pipe.reference_flow = f0;
    sys.gas_node_array.pop_back();
    return sys;
  };
  const double pi_s = 4900.0;
  const auto pressure = [&](const char* lin, double f0)
  {
    const auto r = slp_solve(one_pipe(lin, f0), GasOptions {});
    REQUIRE(r.optimal);
    CHECK(r.flow.at("SA") == doctest::Approx(50.0));
    return r.pressure.at("A");
  };
  // At f₀ = f both rows are the exact equation.
  const double exact = std::sqrt(pi_s - (kSlpKsa * 50.0 * 50.0));
  CHECK(pressure("secant", 50.0) == doctest::Approx(exact));
  CHECK(pressure("tangent", 50.0) == doctest::Approx(exact));
  // Off the reference: secant K|f₀| f, tangent 2K|f₀| f − K f₀|f₀|.
  CHECK(pressure("secant", 25.0)
        == doctest::Approx(std::sqrt(pi_s - (kSlpKsa * 25.0 * 50.0))));
  CHECK(pressure("tangent", 25.0)
        == doctest::Approx(std::sqrt(
            pi_s - ((2.0 * kSlpKsa * 25.0 * 50.0) - (kSlpKsa * 625.0)))));
  // A negative reference uses |f₀| (secant) and stays a valid tangent.
  CHECK(pressure("secant", -25.0) == doctest::Approx(pressure("secant", 25.0)));
}

TEST_CASE("Gas SLP - triangle loop converges to the exact split")  // NOLINT
{
  // Secant with the default damping, tangent (Newton) undamped.
  for (const auto& [lin, omega] :
       {std::pair {"secant", 0.5}, std::pair {"tangent", 1.0}})
  {
    CAPTURE(lin);
    auto sys = slp_triangle(60.0, 40.0);
    for (auto& p : sys.gas_pipe_array) {
      p.linearization = Name {lin};
    }
    const auto r = slp_solve(sys, slp_options(100, omega));
    REQUIRE(r.optimal);
    CHECK(r.stats.linearized_pipes == 3);
    CHECK(r.stats.converged);
    CHECK(r.flow.at("AB") == doctest::Approx(1.254921336).epsilon(1e-6));
    CHECK(r.flow.at("SA") == doctest::Approx(61.25492134).epsilon(1e-7));
    CHECK(r.flow.at("SB") == doctest::Approx(38.74507866).epsilon(1e-7));
    CHECK(r.pressure.at("A") == doctest::Approx(67.26651070).epsilon(1e-8));
    CHECK(r.pressure.at("B") == doctest::Approx(67.26592540).epsilon(1e-8));
  }
}

TEST_CASE("Gas SLP - flow reversal from a wrong-sign reference")  // NOLINT
{
  // f₀ = −20 on A → B; the exact flow is +47.72 (A feeds B).
  auto sys = slp_triangle(10.0, 90.0, R"(,"reference_flow":-20)");
  for (auto& p : sys.gas_pipe_array) {
    p.linearization = Name {"tangent"};
  }
  const auto r = slp_solve(sys, slp_options(50, 1.0));
  REQUIRE(r.optimal);
  CHECK(r.stats.converged);
  CHECK(r.flow.at("AB") == doctest::Approx(47.716791997).epsilon(1e-7));
  CHECK(r.pressure.at("A") == doctest::Approx(67.57867409).epsilon(1e-8));
  CHECK(r.pressure.at("B") == doctest::Approx(66.73104660).epsilon(1e-8));
}

TEST_CASE("Gas SLP - a zero-flow dead-end pipe settles")  // NOLINT
{
  // B → D feeds a node with no demand: its flow is exactly 0 while f₀
  // sits on the +slp_min_flow·fmax floor.  That pipe must count as
  // settled, so the loop converges on the triangle alone.
  auto sys = slp_triangle(60.0, 40.0);
  const auto tail = daw::json::from_json<System>(R"({
   "name":"tail",
   "gas_node_array":[{"uid":4,"name":"D","pmin":1,"pmax":100}],
   "gas_pipe_array":[
    {"uid":4,"name":"BD","gas_node_a":"B","gas_node_b":"D",
     "weymouth_k":0.05,"fmax":200,"weymouth_mode":"linearized"}]})");
  sys.gas_node_array.push_back(tail.gas_node_array.front());
  sys.gas_pipe_array.push_back(tail.gas_pipe_array.front());
  for (auto& p : sys.gas_pipe_array) {
    p.linearization = Name {"tangent"};
  }
  const auto r = slp_solve(sys, slp_options(30, 1.0));
  REQUIRE(r.optimal);
  CHECK(r.stats.converged);
  CHECK(r.stats.iterations <= 8);
  CHECK(r.flow.at("BD") == doctest::Approx(0.0));
  CHECK(r.flow.at("AB") == doctest::Approx(1.254921336).epsilon(1e-7));
}

TEST_CASE("Gas SLP - degenerate split ends at an exact point")  // NOLINT
{
  // Two equal-cost supplies feed D through their own pipe: every split
  // costs the same, so the LP may alternate between vertices.  The
  // trust region pins the flows, and the final point satisfies the
  // Weymouth equation on both pipes.
  const auto sys = daw::json::from_json<System>(R"({
   "name":"split","bus_array":[{"uid":1,"name":"B"}],
   "gas_node_array":[
    {"uid":1,"name":"S1","pmin":1,"pmax":70},
    {"uid":2,"name":"S2","pmin":1,"pmax":70},
    {"uid":3,"name":"D","pmin":1,"pmax":70}],
   "gas_supply_array":[
    {"uid":1,"name":"G1","gas_node":"S1","fmax":1000,"cost":1},
    {"uid":2,"name":"G2","gas_node":"S2","fmax":1000,"cost":1}],
   "gas_demand_array":[
    {"uid":1,"name":"DD","gas_node":"D","load":100,"fail_cost":1e4}],
   "gas_pipe_array":[
    {"uid":1,"name":"P1","gas_node_a":"S1","gas_node_b":"D",
     "weymouth_k":0.10,"fmax":200,"weymouth_mode":"linearized",
     "linearization":"tangent"},
    {"uid":2,"name":"P2","gas_node_a":"S2","gas_node_b":"D",
     "weymouth_k":0.25,"fmax":200,"weymouth_mode":"linearized",
     "linearization":"tangent"}]})");
  const auto r = slp_solve(sys, slp_options(60, 1.0, 1e-7));
  REQUIRE(r.optimal);
  CHECK(r.stats.converged);
  CHECK(r.obj == doctest::Approx(100.0));
  CHECK(r.flow.at("P1") + r.flow.at("P2") == doctest::Approx(100.0));
  const double pd2 = r.pressure.at("D") * r.pressure.at("D");
  for (const auto& [pipe, node, k] :
       {std::tuple {"P1", "S1", 0.10}, std::tuple {"P2", "S2", 0.25}})
  {
    CAPTURE(pipe);
    const double f = r.flow.at(pipe);
    const double ps2 = r.pressure.at(node) * r.pressure.at(node);
    // A zero flow sits on the ±slp_min_flow·fmax floor: the residual is
    // at most K (2 f_min)² = 0.04 bar² (π ≈ 4900 bar²).
    CHECK(std::abs(ps2 - pd2 - (k * f * std::abs(f))) <= 0.05);
  }
}

TEST_CASE("Gas SLP - damping")  // NOLINT
{
  // The undamped secant iteration oscillates on the small loop flow;
  // ω = 0.5 converges.  Tangent (Newton) converges undamped.
  const auto run = [](const char* lin, double omega)
  {
    auto sys = slp_triangle(60.0, 40.0);
    for (auto& p : sys.gas_pipe_array) {
      p.linearization = Name {lin};
    }
    return slp_solve(sys, slp_options(30, omega, 1e-6));
  };
  const auto undamped = run("secant", 1.0);
  REQUIRE(undamped.optimal);
  CHECK_FALSE(undamped.stats.converged);
  CHECK(undamped.stats.iterations == 30);
  const auto damped = run("secant", 0.5);
  CHECK(damped.stats.converged);
  CHECK(damped.stats.iterations < 30);
  const auto newton = run("tangent", 1.0);
  CHECK(newton.stats.converged);
  CHECK(newton.stats.iterations <= 6);
}

TEST_CASE("Gas SLP - no-op without iterations or linearized pipes")  // NOLINT
{
  // slp_iterations = 0: single pass at f₀ (default 0.1 fmax = 20).
  const auto single = slp_solve(slp_triangle(60.0, 40.0), GasOptions {});
  REQUIRE(single.optimal);
  CHECK(single.stats.iterations == 0);
  CHECK(single.stats.linearized_pipes == 0);
  // Radial pipes only: nothing to refine.
  auto radial = slp_triangle(60.0, 40.0);
  radial.gas_pipe_array.pop_back();
  for (auto& p : radial.gas_pipe_array) {
    p.weymouth_mode.reset();
  }
  const auto r = slp_solve(radial, slp_options(10));
  REQUIRE(r.optimal);
  CHECK(r.stats.linearized_pipes == 0);
  CHECK(r.stats.iterations == 0);
}

TEST_CASE("Gas SLP - low_memory compress replays the SLP rows")  // NOLINT
{
  auto sys = slp_triangle(60.0, 40.0);
  for (auto& p : sys.gas_pipe_array) {
    p.linearization = Name {"tangent"};
  }
  const auto off = slp_solve(sys, slp_options(50, 1.0));
  const auto cmp =
      slp_solve(sys, slp_options(50, 1.0), LowMemoryMode::compress);
  REQUIRE(cmp.optimal);
  CHECK(cmp.stats.converged);
  CHECK(cmp.flow.at("AB") == doctest::Approx(off.flow.at("AB")).epsilon(1e-7));
  CHECK(cmp.obj == doctest::Approx(off.obj).epsilon(1e-9));
}

TEST_CASE("GasCompressor - bidirectional valve")  // NOLINT
{
  // Two sources joined by an open valve V (A → B by orientation); the
  // cheap source sits at B, so gas flows B → A through the valve.
  auto sys = daw::json::from_json<System>(R"({
   "name":"valve","bus_array":[{"uid":1,"name":"B"}],
   "gas_node_array":[
    {"uid":1,"name":"A","pmin":40,"pmax":60},
    {"uid":2,"name":"B","pmin":40,"pmax":60}],
   "gas_supply_array":[
    {"uid":1,"name":"SA","gas_node":"A","fmax":100,"cost":10},
    {"uid":2,"name":"SB","gas_node":"B","fmax":100,"cost":1}],
   "gas_demand_array":[{"uid":1,"name":"D","gas_node":"A","load":50,
     "fail_cost":1e4}],
   "gas_compressor_array":[{"uid":1,"name":"V","gas_node_in":"A",
     "gas_node_out":"B","fmax":80,"max_ratio":1,"fuel_model":"none",
     "bidirectional":true}]})");
  const auto simulation = slp_simulation();
  PlanningOptions popts;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  {
    SystemLP system_lp(sys, simulation_lp);
    const auto res = system_lp.resolve();
    REQUIRE(res.has_value());
    CHECK(*res == 0);
    // Supply SB carries the whole load backwards through the valve:
    // cost 50 · 1, not 50 · 10.
    CHECK(system_lp.linear_interface().get_obj_value()
          == doctest::Approx(50.0).epsilon(1e-6));
  }

  SUBCASE("a boosting or fuel-burning station cannot be bidirectional")
  {
    sys.gas_compressor_array.front().max_ratio = 1.2;
    CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::runtime_error);
  }
}

TEST_CASE("GasPipe linearized - input validation")  // NOLINT
{
  const auto simulation = slp_simulation();
  PlanningOptions popts;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  auto sys = slp_triangle(60.0, 40.0);
  sys.gas_pipe_array.front().weymouth_mode = Name {"meshed"};
  CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::runtime_error);
  sys = slp_triangle(60.0, 40.0);
  sys.gas_pipe_array.front().linearization = Name {"chord"};
  CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::runtime_error);
}
