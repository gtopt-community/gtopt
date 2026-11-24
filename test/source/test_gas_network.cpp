// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Gas-network elements (GasNode, GasSupply, GasDemand, GasPipe,
// GasCompressor, GasTurbine) and their physics helpers:
//
//   * Weymouth tangent envelope vs the exact K f² curve (both layouts);
//   * Saldarriaga compressor fuel plane vs the analytic Taylor partials,
//     adiabatic γ, electric κ;
//   * gas-turbine pressure gate (hard stop + derate) rows;
//   * full LP solves of the gas-network prototype's tiny cases and the
//     Saldarriaga PhD Fig. 2.2 pipe chain.  Expected values come from
//     the prototype's independent scipy/HiGHS LP
//     (scripts/gasnet2gtopt/cases/tiny_expected.json and
//     cases/literature/expected.json): objective to 1e-5 relative,
//     pressures to 0.01 bar.

#include <doctest/doctest.h>
#include <gtopt/gas_compressor_lp.hpp>
#include <gtopt/gas_demand_lp.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_physics.hpp>
#include <gtopt/gas_pipe_lp.hpp>
#include <gtopt/gas_supply_lp.hpp>
#include <gtopt/gas_turbine_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

// ── Native systems emitted by `gasnet2gtopt` (power + gas arrays) ─────

constexpr std::string_view kGasMini3 = R"({
 "name":"mini3","bus_array":[{"uid":1,"name":"B"}],
 "generator_array":[
  {"uid":1,"name":"G_M","bus":"B","pmax":500,"capacity":500,"gcost":0},
  {"uid":2,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0},
  {"uid":3,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
   "gcost":220}],
 "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":900}],
 "gas_node_array":[
  {"uid":1,"name":"S","pmin":30,"pmax":70,"p_init":70,
   "pressure_cost":-0.0001},
  {"uid":2,"name":"M","pmin":30,"pmax":70,"p_init":60,
   "pressure_cost":-0.0001},
  {"uid":3,"name":"T","pmin":35,"pmax":70,"p_init":45,
   "pressure_cost":-0.0001}],
 "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"S","fmax":1000,
   "cost":300}],
 "gas_demand_array":[{"uid":1,"name":"CITY","gas_node":"T","load":60,
   "fail_cost":3000}],
 "gas_pipe_array":[
  {"uid":1,"name":"S_M","gas_node_a":"S","gas_node_b":"M","length":50,
   "diameter":406,"fmax":300,"segments":8},
  {"uid":2,"name":"M_T","gas_node_a":"M","gas_node_b":"T","length":80,
   "diameter":406,"fmax":300,"segments":8}],
 "gas_turbine_array":[
  {"uid":1,"name":"G_M_gas","generator":"G_M","gas_node":"M",
   "heat_rate":0.2},
  {"uid":2,"name":"G_T_gas","generator":"G_T","gas_node":"T",
   "heat_rate":0.2}]})";

/// tiny_compressor, station bypassed (cmp_off); variants edit it.
constexpr std::string_view kGasTinyCompressor = R"({
 "name":"tiny_compressor","bus_array":[{"uid":1,"name":"B"}],
 "generator_array":[
  {"uid":1,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0},
  {"uid":2,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
   "gcost":220}],
 "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":700}],
 "gas_node_array":[
  {"uid":1,"name":"S","pmin":30,"pmax":60,"p_init":60,
   "pressure_cost":-0.0001},
  {"uid":2,"name":"A","pmin":30,"pmax":60,"p_init":60,
   "pressure_cost":-0.0001},
  {"uid":3,"name":"B","pmin":30,"pmax":70,"p_init":60,
   "pressure_cost":-0.0001},
  {"uid":4,"name":"T","pmin":40,"pmax":70,"p_init":50,
   "pressure_cost":-0.0001}],
 "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"S","fmax":1000,
   "cost":300}],
 "gas_demand_array":[{"uid":1,"name":"CITY","gas_node":"T","load":50,
   "fail_cost":3000}],
 "gas_pipe_array":[
  {"uid":1,"name":"S_A","gas_node_a":"S","gas_node_b":"A","length":5,
   "diameter":610,"fmax":300,"segments":8},
  {"uid":2,"name":"B_T","gas_node_a":"B","gas_node_b":"T","length":100,
   "diameter":406,"fmax":300,"segments":8}],
 "gas_compressor_array":[
  {"uid":1,"name":"CS","gas_node_in":"A","gas_node_out":"B","fmax":300,
   "max_ratio":1.0,"fuel_model":"none","allow_bypass":true}],
 "gas_turbine_array":[{"uid":1,"name":"G_T_gas","generator":"G_T",
   "gas_node":"T","heat_rate":0.2}]})";

/// tiny_linepack: one 80 km pipe, city peak in block 3 (4 blocks).
constexpr std::string_view kGasTinyLinepack = R"({
 "name":"tiny_linepack","bus_array":[{"uid":1,"name":"B"}],
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

/// tiny_plant with the electric booster on (plant_booster).
constexpr std::string_view kGasTinyPlant = R"({
 "name":"tiny_plant","bus_array":[{"uid":1,"name":"B"}],
 "generator_array":[
  {"uid":1,"name":"G_R","bus":"B","pmax":500,"capacity":500,"gcost":0},
  {"uid":2,"name":"DIESEL","bus":"B","pmax":2000,"capacity":2000,
   "gcost":220}],
 "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":600}],
 "gas_node_array":[
  {"uid":1,"name":"S","pmin":30,"pmax":45,"p_init":45,
   "pressure_cost":-0.0001},
  {"uid":2,"name":"T","pmin":20,"pmax":45,"p_init":35,
   "pressure_cost":-0.0001},
  {"uid":3,"name":"RL","pmin":5,"pmax":45,"p_init":35,
   "pressure_cost":-0.0001},
  {"uid":4,"name":"R","pmin":5,"pmax":60,"p_init":35,
   "pressure_cost":-0.0001}],
 "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"S","fmax":1000,
   "cost":300,"type":"lng_regas"}],
 "gas_demand_array":[{"uid":1,"name":"CITY","gas_node":"T","load":60,
   "fail_cost":3000}],
 "gas_pipe_array":[
  {"uid":1,"name":"S_T","gas_node_a":"S","gas_node_b":"T","length":80,
   "diameter":406,"fmax":300,"segments":8},
  {"uid":2,"name":"T_RL","gas_node_a":"T","gas_node_b":"RL","length":10,
   "diameter":406,"fmax":150,"segments":8}],
 "gas_compressor_array":[
  {"uid":1,"name":"BOOSTER","gas_node_in":"RL","gas_node_out":"R",
   "fmax":150,"type":"fuel_gas_booster","max_ratio":1.6,
   "fuel_model":"saldarriaga","op_flow":80,"op_ratio":[1.3],
   "op_suction_pressure":25,"allow_bypass":true,"drive":"electric",
   "bus":"B","motor_efficiency":0.95,"power_max":5}],
 "gas_turbine_array":[{"uid":1,"name":"G_R_gas","generator":"G_R",
   "gas_node":"R","heat_rate":0.2,"min_pressure":30.0,
   "pressure_band":0.5}]})";

/// Saldarriaga PhD (UTP 2018) Fig. 2.2(b): three pipes in series.
constexpr std::string_view kGasSaldarriaga = R"({
 "name":"saldarriaga_fig2_2","bus_array":[{"uid":1,"name":"B"}],
 "gas_node_array":[
  {"uid":1,"name":"N1","pmin":90,"pmax":90,"p_init":90,
   "pressure_cost":-0.0001},
  {"uid":2,"name":"N2","pmin":30,"pmax":90,"p_init":70,
   "pressure_cost":-0.0001},
  {"uid":3,"name":"N3","pmin":30,"pmax":90,"p_init":50,
   "pressure_cost":-0.0001},
  {"uid":4,"name":"N4","pmin":30,"pmax":90,"p_init":30,
   "pressure_cost":-0.0001}],
 "gas_supply_array":[{"uid":1,"name":"INJ_N1","gas_node":"N1",
   "fmax":1250.0,"cost":1.0}],
 "gas_demand_array":[
  {"uid":1,"name":"D_N2","gas_node":"N2","load":101.049969,
   "fail_cost":1000.0},
  {"uid":2,"name":"D_N3","gas_node":"N3","load":119.863931,
   "fail_cost":1000.0},
  {"uid":3,"name":"D_N4","gas_node":"N4","load":533.333333,
   "fail_cost":1000.0}],
 "gas_pipe_array":[
  {"uid":1,"name":"P12","gas_node_a":"N1","gas_node_b":"N2",
   "weymouth_k":0.005625000000000001,"fmax":833.3333333333333,
   "segments":32},
  {"uid":2,"name":"P23","gas_node_a":"N2","gas_node_b":"N3",
   "weymouth_k":0.005625000000000001,"fmax":833.3333333333333,
   "segments":32},
  {"uid":3,"name":"P34","gas_node_a":"N3","gas_node_b":"N4",
   "weymouth_k":0.005625000000000001,"fmax":833.3333333333333,
   "segments":32}]})";

System gas_parse_system(std::string_view json)
{
  return daw::json::from_json<System>(json);
}

Simulation gas_make_simulation(int nblocks)
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
      {.uid = Uid {1}},
  };
  return sim;
}

struct GasSolution
{
  bool optimal {false};
  double obj {0.0};
  std::map<std::string, std::vector<double>> pressure;
  std::map<std::string, std::vector<double>> price;
  std::map<std::string, std::vector<double>> generation;
  std::map<std::string, std::vector<double>> pipe_flow;
  std::map<std::string, std::vector<double>> linepack;
  std::map<std::string, std::vector<double>> fuel;
  std::map<std::string, std::vector<double>> power;
  std::map<std::string, std::vector<double>> pipe_flow_out;
  std::map<std::string, std::vector<double>> supply;
  std::map<std::string, std::pair<double, double>> pi_bounds;
  std::vector<double> unserved;
};

GasSolution gas_solve(const System& sys, int nblocks, const GasOptions& go = {})
{
  const auto simulation = gas_make_simulation(nblocks);
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1000.0;
  popts.gas_options = go;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  GasSolution out;
  if (!result.has_value() || result.value() != 0) {
    return out;
  }
  out.optimal = true;
  out.obj = li.get_obj_value();

  const auto& scenario = system_lp.scene().scenarios()[0];
  const auto& stage = system_lp.phase().stages()[0];
  const auto sol = li.get_col_sol();
  const auto dual = li.get_row_dual();
  const auto col_low = li.get_col_low();
  const auto col_upp = li.get_col_upp();

  for (const auto& node : system_lp.elements<GasNodeLP>()) {
    auto& p = out.pressure[node.gas_node().name];
    for (const auto& [buid, col] : node.pi_cols_at(scenario, stage)) {
      p.push_back(std::sqrt(std::max(0.0, sol[col])));
      out.pi_bounds[node.gas_node().name] = {col_low[col], col_upp[col]};
    }
    auto& d = out.price[node.gas_node().name];
    for (const auto& [buid, row] : node.balance_rows_at(scenario, stage)) {
      d.push_back(dual[row]);
    }
  }
  for (const auto& gen : system_lp.elements<GeneratorLP>()) {
    auto& g = out.generation[gen.generator().name];
    for (const auto& [buid, col] : gen.lookup_generation_cols(scenario, stage))
    {
      g.push_back(sol[col]);
    }
  }
  for (const auto& pipe : system_lp.elements<GasPipeLP>()) {
    const auto& name = pipe.gas_pipe().name;
    if (pipe.has_linepack()) {
      for (const auto& [buid, col] : pipe.linepack_cols_at(scenario, stage)) {
        out.linepack[name].push_back(sol[col]);
      }
      for (const auto& [buid, col] : pipe.flow_in_cols_at(scenario, stage)) {
        out.pipe_flow[name].push_back(sol[col]);
      }
      for (const auto& [buid, col] : pipe.flow_out_cols_at(scenario, stage)) {
        out.pipe_flow_out[name].push_back(sol[col]);
      }
    } else {
      for (const auto& [buid, col] : pipe.flow_cols_at(scenario, stage)) {
        out.pipe_flow[name].push_back(sol[col]);
      }
    }
  }
  for (const auto& cs : system_lp.elements<GasCompressorLP>()) {
    const auto& name = cs.gas_compressor().name;
    if (cs.fuel_model() == GasCompressorLP::FuelModel::saldarriaga) {
      for (const auto& [buid, col] : cs.fuel_cols_at(scenario, stage)) {
        out.fuel[name].push_back(sol[col]);
      }
    }
    if (cs.is_electric()) {
      for (const auto& [buid, col] : cs.power_cols_at(scenario, stage)) {
        out.power[name].push_back(sol[col]);
      }
    }
  }
  for (const auto& gs : system_lp.elements<GasSupplyLP>()) {
    for (const auto& [buid, col] : gs.flow_cols_at(scenario, stage)) {
      out.supply[gs.gas_supply().name].push_back(sol[col]);
    }
  }
  for (const auto& gd : system_lp.elements<GasDemandLP>()) {
    double total = 0.0;
    for (const auto& [buid, col] : gd.fail_cols_at(scenario, stage)) {
      total += sol[col];
    }
    out.unserved.push_back(total);
  }
  return out;
}

void gas_check_pressures(const GasSolution& s,
                         const std::map<std::string, std::vector<double>>& exp)
{
  for (const auto& [node, ps] : exp) {
    CAPTURE(node);
    const auto it = s.pressure.find(node);
    REQUIRE(it != s.pressure.end());
    REQUIRE(it->second.size() >= ps.size());
    for (std::size_t b = 0; b < ps.size(); ++b) {
      CAPTURE(b);
      CHECK(std::abs(it->second[b] - ps[b]) <= 0.01);
    }
  }
}

void gas_check_vec(const std::vector<double>& got,
                   const std::vector<double>& exp,
                   double abs_tol)
{
  REQUIRE(got.size() == exp.size());
  for (std::size_t b = 0; b < exp.size(); ++b) {
    CAPTURE(b);
    CHECK(std::abs(got[b] - exp[b]) <= abs_tol);
  }
}

void gas_check_obj(double got, double exp)
{
  CHECK(std::abs(got - exp) <= 1e-5 * std::abs(exp));
}

/// Turns the bypassed tiny_compressor station into a compressing one.
void gas_set_station(System& sys, std::string_view model)
{
  auto& cs = sys.gas_compressor_array.front();
  cs.max_ratio = 1.4;
  cs.fuel_model = Name {model};
  cs.allow_bypass.reset();  // model default
  if (model == "saldarriaga") {
    cs.op_flow = 170.0;
    cs.op_ratio = {1.2};
    cs.op_suction_pressure = 58.0;
  }
}

/// Bypasses the tiny_plant booster (booster "off").
void gas_booster_off(System& sys)
{
  auto& cs = sys.gas_compressor_array.front();
  cs.max_ratio = 1.0;
  cs.fuel_model = Name {"none"};
  cs.allow_bypass = true;
  cs.drive.reset();
}

}  // namespace

// ── Physics ─────────────────────────────────────────────────────────────

TEST_CASE("gas physics - Weymouth constant and line pack")  // NOLINT
{
  // Menon SI Weymouth: K grows linearly with length and ~D^-5.333.
  const gas::PipeGeometry g80 {
      .length_km = 80.0,
      .diameter_mm = 406.0,
  };
  const double k80 = gas::weymouth_k(g80);
  CHECK(k80 > 0.0);
  auto g160 = g80;
  g160.length_km = 160.0;
  CHECK(gas::weymouth_k(g160) == doctest::Approx(2.0 * k80));
  auto g2d = g80;
  g2d.diameter_mm = 812.0;
  CHECK(gas::weymouth_k(g2d)
        == doctest::Approx(k80 * std::pow(2.0, -2.0 * 2.667)));

  // Line pack: geometric volume / (Z Pb) per bar.
  const double c = gas::linepack_coeff(80.0, 406.0);
  const double vol = std::numbers::pi * 0.406 * 0.406 / 4.0 * 80.0e3;
  CHECK(c == doctest::Approx(vol / (0.9 * gas::kBasePressureBar) / 1000.0));
}

TEST_CASE("gas physics - Weymouth tangent envelope vs exact curve")  // NOLINT
{
  const double kw = gas::weymouth_k({
      .length_km = 80.0,
      .diameter_mm = 406.0,
  });
  const double fmax = 300.0;

  for (const int nseg : {2, 4, 8, 16}) {
    CAPTURE(nseg);
    const auto t =
        gas::tangent_points(kw, fmax, nseg, gas::TangentLayout::uniform, 70.0);
    REQUIRE(t.size() == static_cast<std::size_t>(nseg));
    const auto envelope = [&](double f)
    {
      double best = -1e300;
      for (const double tk : t) {
        best = std::max(best, kw * ((2.0 * tk * f) - (tk * tk)));
      }
      return best;
    };
    // Outer approximation: never above K f², max gap K (F/2N)² at the
    // segment edges, zero at each tangent point.
    double max_gap = 0.0;
    for (int i = 0; i <= 3000; ++i) {
      const double f = fmax * i / 3000.0;
      const double gap = (kw * f * f) - envelope(f);
      CHECK(gap >= -1e-9);
      max_gap = std::max(max_gap, gap);
    }
    const double bound = kw * std::pow(fmax / (2.0 * nseg), 2.0);
    CHECK(max_gap == doctest::Approx(bound).epsilon(1e-6));
    for (const double tk : t) {
      CHECK(envelope(tk) == doctest::Approx(kw * tk * tk));
    }
  }

  SUBCASE("relative_pressure layout is denser near capacity")
  {
    const auto t = gas::tangent_points(
        kw, fmax, 8, gas::TangentLayout::relative_pressure, 70.0);
    REQUIRE(t.size() == 8);
    for (std::size_t k = 1; k < t.size(); ++k) {
      CHECK(t[k] > t[k - 1]);
    }
    const double first_gap = t[1] - t[0];
    const double last_gap = t[7] - t[6];
    CHECK(last_gap < first_gap);
    // Relative outlet-pressure error is more even than uniform: compare
    // the worst relative pressure gap over [0, F] for both layouts.
    const auto worst_rel = [&](const std::vector<double>& pts)
    {
      double worst = 0.0;
      for (int i = 0; i <= 2000; ++i) {
        const double f = fmax * i / 2000.0;
        double env = -1e300;
        for (const double tk : pts) {
          env = std::max(env, kw * ((2.0 * tk * f) - (tk * tk)));
        }
        const double p_exact = std::sqrt((70.0 * 70.0) - (kw * f * f));
        const double p_lp = std::sqrt((70.0 * 70.0) - env);
        worst = std::max(worst, (p_lp - p_exact) / p_exact);
      }
      return worst;
    };
    const auto uni =
        gas::tangent_points(kw, fmax, 8, gas::TangentLayout::uniform, 70.0);
    CHECK(worst_rel(t) < worst_rel(uni));
  }

  CHECK(gas::parse_tangent_layout("relative_pressure")
        == gas::TangentLayout::relative_pressure);
  CHECK(gas::parse_tangent_layout("uniform") == gas::TangentLayout::uniform);
  CHECK_FALSE(gas::parse_tangent_layout("bogus").has_value());
}

TEST_CASE("gas physics - compressor fuel vs analytic Taylor")  // NOLINT
{
  const double gamma = gas::adiabatic_gamma();
  CHECK(gamma == doctest::Approx(0.04333528512949229).epsilon(1e-9));
  // compressor_fuel_fraction(r) == γ (r^α − 1) exactly.
  for (const double r : {1.1, 1.4, 2.0}) {
    CHECK(gas::compressor_fuel_fraction(r)
          == doctest::Approx(gamma * (std::pow(r, gas::kAlpha) - 1.0)));
  }

  // Exact fuel LC(Ψ, π_in, π_out) = γ Ψ ((π_out/π_in)^(α/2) − 1).
  const double a = gas::kAlpha;
  const auto lc = [&](double psi, double pin, double pout)
  { return gamma * psi * (std::pow(pout / pin, a / 2.0) - 1.0); };
  const double psi0 = 170.0;
  const double beta0 = 1.2;
  const double p_in0 = 58.0;
  const double pin0 = p_in0 * p_in0;
  const double pout0 = beta0 * beta0 * pin0;
  const auto pl = gas::saldarriaga_plane(gamma, a, psi0, beta0, p_in0);

  const double h = 1e-4;
  const double d_psi =
      (lc(psi0 + h, pin0, pout0) - lc(psi0 - h, pin0, pout0)) / (2.0 * h);
  const double d_pout =
      (lc(psi0, pin0, pout0 + h) - lc(psi0, pin0, pout0 - h)) / (2.0 * h);
  const double d_pin =
      (lc(psi0, pin0 + h, pout0) - lc(psi0, pin0 - h, pout0)) / (2.0 * h);
  CHECK(pl.c_psi == doctest::Approx(d_psi).epsilon(1e-6));
  CHECK(pl.c_pi == doctest::Approx(d_pout).epsilon(1e-6));
  CHECK(-pl.c_pi * pl.r0 == doctest::Approx(d_pin).epsilon(1e-6));
  CHECK(pl.r0 == doctest::Approx(beta0 * beta0));
  // Euler: the plane has no constant term and is exact at the point.
  const double plane0 =
      (pl.c_psi * psi0) + (pl.c_pi * (pout0 - (pl.r0 * pin0)));
  CHECK(plane0 == doctest::Approx(lc(psi0, pin0, pout0)));
  // Concave in β: the tangent plane over-estimates away from β0.
  for (const double b : {1.05, 1.35}) {
    const double pout = b * b * pin0;
    const double plane =
        (pl.c_psi * psi0) + (pl.c_pi * (pout - (pl.r0 * pin0)));
    CHECK(plane >= lc(psi0, pin0, pout));
  }

  // Electric drive: κ = LHV · η_driver / η_motor ≈ 3.3 MW per km³/h.
  CHECK(gas::gas_lhv_mw_per_km3h() == doctest::Approx(9.6).epsilon(0.01));
  CHECK(gas::electric_kappa()
        == doctest::Approx(gas::gas_lhv_mw_per_km3h() * 0.33 / 0.95));
}

TEST_CASE("gas physics - turbine pressure gate rows")  // NOLINT
{
  const double pmax = 500.0;
  const auto cap_at = [](const gas::CapabilityRows& rows, double p)
  {
    double cap = 1e300;
    for (const auto& r : rows.rows) {
      cap = std::min(cap, (r.slope * p * p) + r.intercept);
    }
    return cap;
  };

  SUBCASE("hard stop")
  {
    const auto rows = gas::plant_capability_rows(pmax, 30.0, 0.5, 0.0, 0.0);
    REQUIRE(rows.rows.size() == 1);
    CHECK(cap_at(rows, 29.5) == doctest::Approx(0.0).epsilon(1e-9).scale(1));
    CHECK(cap_at(rows, 30.0) == doctest::Approx(pmax));
    CHECK(cap_at(rows, 29.75) > 0.0);
    CHECK(cap_at(rows, 29.75) < pmax);
  }

  SUBCASE("derate")
  {
    const double p_nom = 30.0 * 1.15;
    const auto rows = gas::plant_capability_rows(pmax, 30.0, 0.5, p_nom, 0.0);
    REQUIRE(rows.rows.size() == 2);
    CHECK_FALSE(rows.derate_dropped);
    // out_min defaults to p_min / p_nom.
    CHECK(cap_at(rows, 30.0) == doctest::Approx(pmax * 30.0 / p_nom));
    CHECK(cap_at(rows, p_nom) == doctest::Approx(pmax));
    CHECK(cap_at(rows, 29.5) == doctest::Approx(0.0).epsilon(1e-9).scale(1));
    // Concave: midpoint above the chord.
    const double mid = std::sqrt(0.5 * (900.0 + (p_nom * p_nom)));
    CHECK(cap_at(rows, mid) >= (0.5 * (cap_at(rows, 30.0) + pmax)) - 1e-9);
  }

  SUBCASE("non-concave derate is dropped")
  {
    // out_min = 0.01: a ramp to 5 MW and a steep derate to 500 MW.
    const auto rows = gas::plant_capability_rows(pmax, 30.0, 5.0, 30.1, 0.01);
    CHECK(rows.derate_dropped);
    CHECK(rows.rows.size() == 1);
  }
}

// ── JSON ────────────────────────────────────────────────────────────────

TEST_CASE("gas network JSON round trip")  // NOLINT
{
  const auto sys = gas_parse_system(kGasTinyPlant);
  REQUIRE(sys.gas_node_array.size() == 4);
  REQUIRE(sys.gas_pipe_array.size() == 2);
  REQUIRE(sys.gas_compressor_array.size() == 1);
  REQUIRE(sys.gas_turbine_array.size() == 1);
  const auto& cs = sys.gas_compressor_array.front();
  CHECK(cs.fuel_model.value_or("") == "saldarriaga");
  CHECK(cs.op_ratio.size() == 1);
  CHECK(cs.drive.value_or("") == "electric");
  CHECK(cs.power_max.value_or(0.0) == doctest::Approx(5.0));
  const auto& gt = sys.gas_turbine_array.front();
  CHECK(gt.min_pressure.value_or(0.0) == doctest::Approx(30.0));
  CHECK(sys.gas_supply_array.front().type.value_or("") == "lng_regas");

  const auto text = daw::json::to_json(sys);
  const auto again = daw::json::from_json<System>(text);
  CHECK(again.gas_node_array.size() == 4);
  CHECK(again.gas_compressor_array.front().op_ratio.size() == 1);
  CHECK(again.gas_turbine_array.front().pressure_band.value_or(0.0)
        == doctest::Approx(0.5));

  System merged;
  merged.merge(gas_parse_system(kGasMini3));
  merged.merge(gas_parse_system(kGasSaldarriaga));
  CHECK(merged.gas_node_array.size() == 7);
  CHECK(merged.gas_pipe_array.size() == 5);
}

// ── LP: tiny cases vs the prototype's reference LP ─────────────────────

TEST_CASE("gas network LP - mini3 Weymouth chain")  // NOLINT
{
  const auto s = gas_solve(gas_parse_system(kGasMini3), 1);
  REQUIRE(s.optimal);
  gas_check_obj(s.obj, 94323.5851);
  gas_check_pressures(s,
                      {
                          {"S", {70.0}},
                          {"M", {48.3686}},
                          {"T", {35.0}},
                      });
  gas_check_vec(s.generation.at("G_T"), {260.4723}, 0.01);
  gas_check_vec(s.price.at("T"), {1100.0}, 0.01);
}

TEST_CASE("gas network LP - compressor station")  // NOLINT
{
  SUBCASE("bypassed (cmp_off)")
  {
    const auto s = gas_solve(gas_parse_system(kGasTinyCompressor), 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 103300.1334);
    gas_check_pressures(s,
                        {
                            {"S", {60.0}},
                            {"A", {59.9055}},
                            {"B", {59.9055}},
                            {"T", {40.0}},
                        });
    gas_check_vec(s.generation.at("G_T"), {410.6164}, 0.01);
    gas_check_vec(s.price.at("T"), {1100.0}, 0.01);
  }

  SUBCASE("fixed fraction (cmp_fixed)")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    gas_set_station(sys, "fixed_fraction");
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 73177.078);
    gas_check_vec(s.generation.at("G_T"), {600.0}, 0.01);
    gas_check_vec(s.price.at("T"), {301.0536}, 0.01);
    gas_check_pressures(s,
                        {
                            {"S", {60.0}},
                            {"A", {59.8423}},
                            {"B", {70.0}},
                            {"T", {40.0965}},
                        });
  }

  SUBCASE("Saldarriaga plane (cmp_sald)")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    gas_set_station(sys, "saldarriaga");
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 73079.0759);
    gas_check_pressures(s,
                        {
                            {"S", {60.0}},
                            {"A", {59.8429}},
                            {"B", {69.9448}},
                            {"T", {40.0}},
                        });
    gas_check_vec(s.generation.at("G_T"), {600.0}, 0.01);
    gas_check_vec(s.price.at("T"), {302.6834}, 0.01);
    // The fuel column sits on the plane (it costs gas).
    REQUIRE(s.fuel.contains("CS"));
    CHECK(s.fuel.at("CS")[0] > 0.0);
  }
}

TEST_CASE("gas network LP - line pack")  // NOLINT
{
  SUBCASE("steady state (lp_steady)")
  {
    auto sys = gas_parse_system(kGasTinyLinepack);
    sys.gas_pipe_array.front().linepack = false;
    const auto s = gas_solve(sys, 4);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 282729.2305);
    gas_check_pressures(s,
                        {
                            {"S", {60.0, 60.0, 60.0, 60.0}},
                            {"T", {42.6688, 42.6688, 35.0, 42.6688}},
                        });
    gas_check_vec(s.generation.at("G_T"), {500.0, 500.0, 7.9291, 500.0}, 0.01);
  }

  SUBCASE("packing before the peak (lp_pack)")
  {
    const auto s = gas_solve(gas_parse_system(kGasTinyLinepack), 4);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 271755.0835);
    // Blocks 1-3 are unique.  In block 4 only π_S + π_T is pinned (the
    // closing line pack LP_4 = LP_0 depends on the sum and the
    // tie-break rewards the sum): the pair is an alternative optimum of
    // both the prototype LP and this one, so it is checked on the sum.
    gas_check_pressures(s,
                        {
                            {"S", {60.0, 60.0, 60.0}},
                            {"T", {38.3326, 40.8889, 35.0}},
                        });
    const double pi_sum4 = std::pow(s.pressure.at("S")[3], 2.0)
        + std::pow(s.pressure.at("T")[3], 2.0);
    CHECK(pi_sum4
          == doctest::Approx((55.9352 * 55.9352) + (38.6814 * 38.6814))
                 .epsilon(1e-5));
    gas_check_vec(s.generation.at("G_T"), {500.0, 500.0, 76.5184, 500.0}, 0.01);
    gas_check_vec(s.price.at("T"), {464.5079, 638.7375, 1100.0, 300.0}, 0.01);

    // Line pack follows the linearised pressure and closes the cycle.
    const auto& lp = s.linepack.at("S_T");
    REQUIRE(lp.size() == 4);
    const double c = gas::linepack_coeff(80.0, 406.0);
    const double pr = 0.25 * (30.0 + 60.0 + 35.0 + 60.0);
    const double lp0 =
        (c / (4.0 * pr) * ((55.0 * 55.0) + (40.0 * 40.0))) + (c * pr / 2.0);
    for (std::size_t b = 0; b < 4; ++b) {
      const double pa = s.pressure.at("S")[b];
      const double pb = s.pressure.at("T")[b];
      CHECK(lp[b]
            == doctest::Approx((c / (4.0 * pr) * ((pa * pa) + (pb * pb)))
                               + (c * pr / 2.0)));
    }
    CHECK(lp[3] >= lp0 - 1e-6);
    // The pipe is packed before the peak and drawn down during it.
    CHECK(lp[1] > lp[0]);
    CHECK(lp[2] < lp[1]);
  }
}

TEST_CASE("gas network LP - plant pressure gate, booster, derate")  // NOLINT
{
  SUBCASE("no pressure requirement (plant_none)")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    gas_booster_off(sys);
    sys.gas_turbine_array.front().min_pressure.reset();
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 91161.8452);
    gas_check_pressures(s,
                        {
                            {"S", {45.0}},
                            {"T", {20.0}},
                            {"RL", {18.4144}},
                            {"R", {18.4144}},
                        });
    gas_check_vec(s.generation.at("G_R"), {367.7365}, 0.01);
  }

  SUBCASE("hard minimum pressure (plant_hard)")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    gas_booster_off(sys);
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 108578.9034);
    gas_check_pressures(s,
                        {
                            {"S", {45.0}},
                            {"T", {30.2642}},
                            {"RL", {29.7599}},
                            {"R", {29.7599}},
                        });
    gas_check_vec(s.generation.at("G_R"), {258.8789}, 0.01);
    gas_check_vec(s.price.at("R"), {1087.1034}, 0.01);
    // The gate binds: P = s₁ (π − (p_min − band)²).
    const auto rows = gas::plant_capability_rows(500.0, 30.0, 0.5, 0.0, 0.0);
    const double pi_r = std::pow(s.pressure.at("R")[0], 2.0);
    CHECK(
        s.generation.at("G_R")[0]
        == doctest::Approx((rows.rows[0].slope * pi_r) + rows.rows[0].intercept)
               .epsilon(1e-6));
  }

  SUBCASE("electric booster (plant_booster)")
  {
    const auto s = gas_solve(gas_parse_system(kGasTinyPlant), 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 91690.7872);
    gas_check_pressures(s,
                        {
                            {"S", {45.0}},
                            {"T", {20.2189}},
                            {"RL", {18.6667}},
                            {"R", {29.8667}},
                        });
    gas_check_vec(s.generation.at("G_R"), {365.8963}, 0.01);
    gas_check_vec(s.price.at("R"), {1096.3387}, 0.01);
    // Motor load = κ · LC on the power bus.
    const double kappa = gas::electric_kappa(0.33, 0.95);
    REQUIRE(s.power.contains("BOOSTER"));
    CHECK(s.power.at("BOOSTER")[0]
          == doctest::Approx(kappa * s.fuel.at("BOOSTER")[0]));
    CHECK(s.power.at("BOOSTER")[0] > 0.0);
  }

  SUBCASE("gas-driven booster (plant_booster_gas)")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    sys.gas_compressor_array.front().drive = Name {"gas"};
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 91805.9964);
    gas_check_pressures(s,
                        {
                            {"S", {45.0}},
                            {"T", {20.2181}},
                            {"RL", {18.6657}},
                            {"R", {29.8651}},
                        });
    gas_check_vec(s.generation.at("G_R"), {364.3084}, 0.01);
    CHECK(s.power.empty());
  }

  SUBCASE("derate (plant_derate)")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    gas_booster_off(sys);
    sys.gas_turbine_array.front().nominal_pressure = 30.0 * 1.15;
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 108678.8212);
    gas_check_pressures(s,
                        {
                            {"S", {45.0}},
                            {"T", {30.2994}},
                            {"RL", {29.798}},
                            {"R", {29.798}},
                        });
    gas_check_vec(s.generation.at("G_R"), {258.2544}, 0.01);
  }

  SUBCASE("derate with booster (plant_derate_booster)")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    sys.gas_turbine_array.front().nominal_pressure = 30.0 * 1.15;
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    gas_check_obj(s.obj, 91730.9497);
    gas_check_pressures(s,
                        {
                            {"S", {45.0}},
                            {"T", {20.2484}},
                            {"RL", {18.7007}},
                            {"R", {29.9211}},
                        });
    gas_check_vec(s.generation.at("G_R"), {365.6467}, 0.01);
  }
}

TEST_CASE("gas network LP - Saldarriaga PhD Fig. 2.2 chain")  // NOLINT
{
  const auto s = gas_solve(gas_parse_system(kGasSaldarriaga), 1);
  REQUIRE(s.optimal);
  gas_check_obj(s.obj, 752.606855);
  gas_check_pressures(s,
                      {
                          {"N1", {90.0}},
                          {"N2", {70.0058}},
                          {"N3", {50.0148}},
                          {"N4", {30.0247}},
                      });
  gas_check_vec(s.pipe_flow.at("P12"), {754.2472}, 0.01);
  gas_check_vec(s.pipe_flow.at("P23"), {653.1973}, 0.01);
  gas_check_vec(s.pipe_flow.at("P34"), {533.3333}, 0.01);
  for (const double u : s.unserved) {
    CHECK(u == doctest::Approx(0.0).epsilon(1e-9).scale(1));
  }
  // Published Fig. 2.2(b): 90 / 70 / 50 / 30 bar within 0.025 bar.
  CHECK(std::abs(s.pressure.at("N2")[0] - 70.0) < 0.025);
  CHECK(std::abs(s.pressure.at("N3")[0] - 50.0) < 0.025);
  CHECK(std::abs(s.pressure.at("N4")[0] - 30.0) < 0.025);
}

// ── Per-element formulation pieces ─────────────────────────────────────

TEST_CASE("GasNode - squared-pressure bounds and tie-break")  // NOLINT
{
  const auto s = gas_solve(gas_parse_system(kGasMini3), 1);
  REQUIRE(s.optimal);
  // π ∈ [pmin², pmax²]
  const std::map<std::string, std::pair<double, double>> band {
      {"S", {30.0, 70.0}},
      {"M", {30.0, 70.0}},
      {"T", {35.0, 70.0}},
  };
  for (const auto& [node, b] : band) {
    CAPTURE(node);
    CHECK(s.pi_bounds.at(node).first == doctest::Approx(b.first * b.first));
    CHECK(s.pi_bounds.at(node).second == doctest::Approx(b.second * b.second));
    CHECK(s.pressure.at(node)[0] >= b.first - 1e-6);
    CHECK(s.pressure.at(node)[0] <= b.second + 1e-6);
  }
  // The negative pressure_cost pushes the source to its ceiling; the
  // end of the chain sits at its floor (the binding Weymouth drop).
  CHECK(s.pressure.at("S")[0] == doctest::Approx(70.0));
  CHECK(s.pressure.at("T")[0] == doctest::Approx(35.0));
}

TEST_CASE("Gas balance - heat-rate link and price dual units")  // NOLINT
{
  const auto s = gas_solve(gas_parse_system(kGasMini3), 1);
  REQUIRE(s.optimal);
  // Supply = city load + Σ heat_rate · P (no line pack, no fuel).
  const double burn =
      0.2 * (s.generation.at("G_M")[0] + s.generation.at("G_T")[0]);
  CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(60.0 + burn));
  // Gas price duals [$/km³], positive: the source node prices the
  // supply cost; the congested end prices the displaced diesel,
  // 220 $/MWh / 0.2 km³/MWh = 1100 $/km³.
  CHECK(s.price.at("S")[0] == doctest::Approx(300.0));
  CHECK(s.price.at("T")[0] == doctest::Approx(220.0 / 0.2));
  CHECK(s.price.at("M")[0] >= s.price.at("S")[0] - 1e-6);
  CHECK(s.price.at("M")[0] <= s.price.at("T")[0] + 1e-6);
}

TEST_CASE("GasSupply / GasDemand - limits, regas and shedding")  // NOLINT
{
  SUBCASE("supply cap forces shedding at the fail cost")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    sys.gas_supply_array.front().fmax = 40.0;
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(40.0));
    REQUIRE(s.unserved.size() == 1);
    CHECK(s.unserved[0] == doctest::Approx(10.0));
    CHECK(s.generation.at("G_T")[0]
          == doctest::Approx(0.0).epsilon(1e-9).scale(1));
    CHECK(s.price.at("T")[0] == doctest::Approx(3000.0));
  }

  SUBCASE("minimum take")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    sys.gas_supply_array.front().fmin = 120.0;
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    CHECK(s.supply.at("SUPPLY")[0] >= 120.0 - 1e-6);
  }

  SUBCASE("hard load without fail_cost is infeasible when short")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    sys.gas_supply_array.front().fmax = 40.0;
    sys.gas_demand_array.front().fail_cost = OptTRealFieldSched {};
    CHECK_FALSE(gas_solve(sys, 1).optimal);
  }

  SUBCASE("two sources: LNG regas tagged supply dispatched in merit")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    sys.gas_supply_array.front().fmax = 30.0;
    auto regas = sys.gas_supply_array.front();
    regas.uid = Uid {2};
    regas.name = "REGAS";
    regas.type = Name {"lng_regas"};
    regas.fmax = 1000.0;
    regas.cost = 500.0;
    sys.gas_supply_array.push_back(regas);
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    CHECK(s.supply.at("SUPPLY")[0] == doctest::Approx(30.0));
    CHECK(s.supply.at("REGAS")[0] > 0.0);
    CHECK(s.price.at("S")[0] == doctest::Approx(500.0));
  }
}

TEST_CASE("GasCompressor - regulator, ratio bound, fuel by drive")  // NOLINT
{
  SUBCASE("no bypass at ratio 1: a straight connection")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    sys.gas_compressor_array.front().allow_bypass = false;
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    CHECK(s.pressure.at("B")[0] == doctest::Approx(s.pressure.at("A")[0]));
  }

  SUBCASE("ratio bound p_out <= beta p_in")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    gas_set_station(sys, "saldarriaga");
    sys.gas_compressor_array.front().max_ratio = 1.05;
    const auto s = gas_solve(sys, 1);
    REQUIRE(s.optimal);
    CHECK(s.pressure.at("B")[0] <= (1.05 * s.pressure.at("A")[0]) + 1e-6);
  }

  SUBCASE("gas drive burns suction gas, electric drive loads the bus")
  {
    auto gsys = gas_parse_system(kGasTinyPlant);
    gsys.gas_compressor_array.front().drive = Name {"gas"};
    const auto g = gas_solve(gsys, 1);
    REQUIRE(g.optimal);
    const double fuel = g.fuel.at("BOOSTER")[0];
    CHECK(fuel > 0.0);
    CHECK(g.supply.at("SUPPLY")[0]
          == doctest::Approx(60.0 + (0.2 * g.generation.at("G_R")[0]) + fuel)
                 .epsilon(1e-7));

    const auto e = gas_solve(gas_parse_system(kGasTinyPlant), 1);
    REQUIRE(e.optimal);
    CHECK(e.supply.at("SUPPLY")[0]
          == doctest::Approx(60.0 + (0.2 * e.generation.at("G_R")[0]))
                 .epsilon(1e-7));
    CHECK(e.power.at("BOOSTER")[0]
          == doctest::Approx(gas::electric_kappa() * e.fuel.at("BOOSTER")[0]));
  }
}

TEST_CASE("GasSupply - volume over the stage")  // NOLINT
{
  // CITY needs 50/h; a 70-unit allotment over 2 h leaves 30 unserved.
  auto sys = gas_parse_system(kGasTinyCompressor);
  sys.gas_supply_array.front().volume = 70.0;
  const auto s = gas_solve(sys, 2);
  REQUIRE(s.optimal);
  const auto& f = s.supply.at("SUPPLY");
  CHECK(f[0] + f[1] == doctest::Approx(70.0));
  REQUIRE(s.unserved.size() == 1);
  CHECK(s.unserved[0] == doctest::Approx(30.0));
}

TEST_CASE("GasPipe - capacity-only arc (weymouth_mode none)")  // NOLINT
{
  // B_T as a transfer: no pressure coupling (T may sit at pmax with B at
  // pmin), flow capped at fmax.
  auto sys = gas_parse_system(kGasTinyCompressor);
  auto& arc = sys.gas_pipe_array.back();
  arc.weymouth_mode = Name {"none"};
  arc.fmax = 35.0;
  const auto s = gas_solve(sys, 1);
  REQUIRE(s.optimal);
  CHECK(s.pipe_flow.at("B_T")[0] == doctest::Approx(35.0));
  REQUIRE(s.unserved.size() == 1);
  CHECK(s.unserved[0] == doctest::Approx(15.0));
  // T is decoupled from B: the pressure tie-break lifts it to pmax.
  CHECK(s.pressure.at("T")[0] == doctest::Approx(70.0));
}

TEST_CASE("GasPipe - line pack cycle option")  // NOLINT
{
  const auto sys = gas_parse_system(kGasTinyLinepack);
  const auto cyclic = gas_solve(sys, 4);
  GasOptions go;
  go.linepack_cyclic = false;
  const auto open = gas_solve(sys, 4, go);
  REQUIRE(cyclic.optimal);
  REQUIRE(open.optimal);
  const double c = gas::linepack_coeff(80.0, 406.0);
  const double pr = 0.25 * (30.0 + 60.0 + 35.0 + 60.0);
  const double lp0 =
      (c / (4.0 * pr) * ((55.0 * 55.0) + (40.0 * 40.0))) + (c * pr / 2.0);
  CHECK(cyclic.linepack.at("S_T").back() >= lp0 - 1e-6);
  // Without the cycle the pack ends drawn down and the cost is lower.
  CHECK(open.linepack.at("S_T").back() < lp0 - 1e-3);
  CHECK(open.obj < cyclic.obj);
}

TEST_CASE("GasPipe - line pack balance per block")  // NOLINT
{
  const auto s = gas_solve(gas_parse_system(kGasTinyLinepack), 4);
  REQUIRE(s.optimal);
  const auto& lp = s.linepack.at("S_T");
  const auto& fin = s.pipe_flow.at("S_T");
  const auto& fout = s.pipe_flow_out.at("S_T");
  const double c = gas::linepack_coeff(80.0, 406.0);
  const double pr = 0.25 * (30.0 + 60.0 + 35.0 + 60.0);
  double prev =
      (c / (4.0 * pr) * ((55.0 * 55.0) + (40.0 * 40.0))) + (c * pr / 2.0);
  for (std::size_t b = 0; b < 4; ++b) {
    CAPTURE(b);
    CHECK(fin[b] - fout[b] == doctest::Approx(lp[b] - prev).epsilon(1e-7));
    prev = lp[b];
  }
  // The city peak (block 3) draws on the pack: outflow above inflow.
  CHECK(fout[2] > fin[2]);
}

TEST_CASE("GasTurbine - gate rows hold the node above the band")  // NOLINT
{
  // The gate rows P ≤ a π + b have b < 0, so even an idle plant needs
  // π ≥ −b/a (the bottom of the ramp band): an LP cannot express
  // "off below the band" (that is P ≤ max(0, ramp), non-convex).  A
  // delivery node capped below the band is therefore infeasible, and
  // feasible again once the plant's min_pressure is dropped.
  auto sys = gas_parse_system(kGasTinyPlant);
  gas_booster_off(sys);
  sys.gas_node_array[3].pmax = 25.0;
  CHECK_FALSE(gas_solve(sys, 1).optimal);

  sys.gas_turbine_array.front().min_pressure.reset();
  const auto s = gas_solve(sys, 1);
  REQUIRE(s.optimal);
  CHECK(s.pressure.at("R")[0] <= 25.0 + 1e-6);
}

TEST_CASE("gas network - input validation errors")  // NOLINT
{
  SUBCASE("missing required reference")
  {
    CHECK_THROWS(gas_parse_system(R"({"name":"x",
      "gas_supply_array":[{"uid":1,"name":"S","fmax":10}]})"));
  }

  const auto build_throws = [](const System& sys)
  {
    const auto simulation = gas_make_simulation(1);
    const PlanningOptions popts;
    const PlanningOptionsLP options(popts);
    SimulationLP simulation_lp(simulation, options);
    CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::runtime_error);
  };

  SUBCASE("unknown fuel_model")
  {
    auto sys = gas_parse_system(kGasTinyCompressor);
    sys.gas_compressor_array.front().fuel_model = Name {"magic"};
    build_throws(sys);
  }
  SUBCASE("unknown drive")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    sys.gas_compressor_array.front().drive = Name {"steam"};
    build_throws(sys);
  }
  SUBCASE("electric drive without a bus")
  {
    auto sys = gas_parse_system(kGasTinyPlant);
    sys.gas_compressor_array.front().bus.reset();
    build_throws(sys);
  }
  SUBCASE("unknown tangent_layout")
  {
    auto sys = gas_parse_system(kGasMini3);
    sys.gas_pipe_array.front().tangent_layout = Name {"spline"};
    build_throws(sys);
  }
  SUBCASE("pipe without fmax")
  {
    auto sys = gas_parse_system(kGasMini3);
    sys.gas_pipe_array.front().fmax = OptTRealFieldSched {};
    build_throws(sys);
  }
}

// ── Elevation ─────────────────────────────────────────────────────────

namespace
{

/// One pipe S → T carrying exactly 100 (a single tangent at t = 100 is
/// exact there).  S is pinned at 70 bar; T floats with a tie-break.
System gas_elevation_system(double h_s, double h_t)
{
  auto sys = gas_parse_system(R"({
   "name":"elev","bus_array":[{"uid":1,"name":"B"}],
   "gas_node_array":[
    {"uid":1,"name":"S","pmin":70,"pmax":70},
    {"uid":2,"name":"T","pmin":1,"pmax":100,"pressure_cost":-0.0001}],
   "gas_supply_array":[{"uid":1,"name":"SUP","gas_node":"S","fmax":1000,
     "cost":1}],
   "gas_demand_array":[{"uid":1,"name":"D","gas_node":"T","load":100,
     "fail_cost":1000}],
   "gas_pipe_array":[{"uid":1,"name":"S_T","gas_node_a":"S",
     "gas_node_b":"T","weymouth_k":0.1,"fmax":200,"segments":1}]})");
  sys.gas_node_array[0].elevation = h_s;
  sys.gas_node_array[1].elevation = h_t;
  return sys;
}

/// Exact outlet pressure of the elevation pipe equation.
double gas_exact_outlet(double s, double k, double p_in, double f)
{
  const double ke = k * (s == 0.0 ? 1.0 : std::expm1(s) / s);
  return std::sqrt(((p_in * p_in) - (ke * f * f)) / std::exp(s));
}

}  // namespace

TEST_CASE("gas physics - elevation exponent and K_e")  // NOLINT
{
  // K_e factor: exactly 1 at s = 0, continuous through the series
  // branch, (e^s − 1)/s elsewhere.
  CHECK(gas::elevation_k_factor(0.0) == 1.0);
  CHECK(gas::elevation_k_factor(1e-12) == doctest::Approx(1.0));
  for (const double s : {-0.7, -1e-3, 1e-3, 0.3, 0.632}) {
    CAPTURE(s);
    CHECK(gas::elevation_k_factor(s) == doctest::Approx(std::expm1(s) / s));
  }
  // Both sides of the series switch agree with expm1 to 1e-12.
  for (const double s : {0.99e-4, 1.01e-4, -0.99e-4}) {
    CAPTURE(s);
    CHECK(gas::elevation_k_factor(s)
          == doctest::Approx(std::expm1(s) / s).epsilon(1e-12));
  }

  // s = 2 g M Δh / (Z R T): ≈ 0.632 for a 4000 m climb at G = 0.6.
  const double s4000 = gas::elevation_exponent(4000.0, 0.6, 0.9, 288.15);
  const double hand =
      2.0 * 9.80665 * 0.6 * 0.028964 * 4000.0 / (0.9 * 8.314 * 288.15);
  CHECK(s4000 == doctest::Approx(hand));
  CHECK(s4000 == doctest::Approx(0.6323).epsilon(1e-3));
  CHECK(gas::elevation_exponent(-4000.0, 0.6, 0.9, 288.15)
        == doctest::Approx(-s4000));
  CHECK(gas::elevation_exponent(0.0, 0.6, 0.9, 288.15) == 0.0);
}

TEST_CASE("GasPipe - elevation term")  // NOLINT
{
  const auto flat = gas_solve(gas_elevation_system(0.0, 0.0), 1);
  REQUIRE(flat.optimal);
  // s = 0 is the flat model: p_T = √(70² − K f²).
  CHECK(flat.pressure.at("T")[0]
        == doctest::Approx(std::sqrt(4900.0 - 1000.0)));

  SUBCASE("equal elevations and an explicit s = 0 reduce to flat")
  {
    const auto same = gas_solve(gas_elevation_system(2500.0, 2500.0), 1);
    REQUIRE(same.optimal);
    CHECK(same.obj == doctest::Approx(flat.obj));
    CHECK(same.pressure.at("T")[0]
          == doctest::Approx(flat.pressure.at("T")[0]));
    auto sys = gas_elevation_system(0.0, 1000.0);
    sys.gas_pipe_array[0].elevation_exponent = 0.0;
    const auto forced = gas_solve(sys, 1);
    REQUIRE(forced.optimal);
    CHECK(forced.pressure.at("T")[0]
          == doctest::Approx(flat.pressure.at("T")[0]));
  }

  SUBCASE("uphill: gravity costs pressure")
  {
    const auto up = gas_solve(gas_elevation_system(0.0, 2000.0), 1);
    REQUIRE(up.optimal);
    const double s = gas::elevation_exponent(2000.0, 0.6, 0.9, 288.15);
    CHECK(up.pressure.at("T")[0]
          == doctest::Approx(gas_exact_outlet(s, 0.1, 70.0, 100.0)));
    CHECK(up.pressure.at("T")[0] < flat.pressure.at("T")[0] - 1.0);
  }

  SUBCASE("downhill: gravity helps")
  {
    const auto down = gas_solve(gas_elevation_system(2000.0, 0.0), 1);
    REQUIRE(down.optimal);
    const double s = gas::elevation_exponent(-2000.0, 0.6, 0.9, 288.15);
    CHECK(down.pressure.at("T")[0]
          == doctest::Approx(gas_exact_outlet(s, 0.1, 70.0, 100.0)));
    CHECK(down.pressure.at("T")[0] > flat.pressure.at("T")[0] + 1.0);
  }

  SUBCASE("network gas properties come from options.gas_options")
  {
    // A heavier gas (G = 0.7) climbs worse than the default 0.6.
    const auto sys = gas_elevation_system(0.0, 2000.0);
    const auto simulation = gas_make_simulation(1);
    PlanningOptions popts;
    popts.gas_options.gas_specific_gravity = 0.7;
    const PlanningOptionsLP options(popts);
    SimulationLP simulation_lp(simulation, options);
    SystemLP system_lp(sys, simulation_lp);
    const auto& pipe = system_lp.elements<GasPipeLP>().front();
    const double s = gas::elevation_exponent(2000.0, 0.7, 0.9, 288.15);
    CHECK(pipe.outlet_factor() == doctest::Approx(std::exp(s)));
    CHECK(pipe.effective_k()
          == doctest::Approx(0.1 * gas::elevation_k_factor(s)));
  }
}
