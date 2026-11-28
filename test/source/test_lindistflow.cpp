// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_lindistflow.cpp
 * @brief     Unit tests of the LinDistFlow network model
 *            (`model_options.network_model = "lindistflow"`)
 *
 * Two-bus feeder: slack `s` (|V| = 1 p.u.) —line r, x at 10 kV→ bus `b`
 * with a 1 MW load.  LinDistFlow gives
 *   w_b = w_s/t² − 2(r·P + x·Q)/V²,
 * so every expected value below is a closed-form number.
 */

#include <cmath>
#include <format>
#include <numbers>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/lindistflow.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct Feeder
{
  std::string model {R"(, "network_model": "lindistflow")"};
  std::string bus_b {};  ///< extra fields of bus b
  std::string line {};  ///< extra fields of the line
  std::string demand {R"(, "power_factor": 0.8)"};
  std::string gens {};  ///< extra generator objects (leading comma)
  std::string lines {};  ///< extra line objects (leading comma)
  double lmax {1.0};
};

std::string feeder_json(const Feeder& f)
{
  return std::format(
      R"({{
  "options": {{
    "annual_discount_rate": 0.0,
    "model_options": {{
      "use_single_bus": false, "use_kirchhoff": true,
      "use_line_losses": false,
      "scale_objective": 1, "demand_fail_cost": 1000{}
    }}
  }},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]
  }},
  "system": {{
    "name": "ldf2",
    "bus_array": [
      {{"uid": 1, "name": "s", "voltage": 10, "reference_theta": 0}},
      {{"uid": 2, "name": "b", "voltage": 10{}}}
    ],
    "generator_array": [
      {{"uid": 1, "name": "src", "bus": 1, "gcost": 50, "pmax": 100,
        "qmin": -100, "qmax": 100}}{}
    ],
    "demand_array": [
      {{"uid": 1, "name": "d", "bus": 2, "lmax": {}{}}}
    ],
    "line_array": [
      {{"uid": 1, "name": "l", "bus_a": 1, "bus_b": 2, "voltage": 10,
        "resistance": 1.0, "reactance": 2.0{}}}{}
    ]
  }}
}})",
      f.model,
      f.bus_b,
      f.gens,
      f.lmax,
      f.demand,
      f.line,
      f.lines);
}

struct Result
{
  bool optimal {};
  double obj {};
  int ncols {};
  double w_s {};
  double w_b {};
  double qflow {};
  double pflow {};
  double served {};
  double qgen {-1.0};
  double vsqtap {-1.0};
  bool has_vsq {};
  bool has_theta {};
};

Result solve(const Feeder& f)
{
  auto planning =
      daw::json::from_json<Planning>(feeder_json(f), StrictParsePolicy);
  const PlanningOptionsLP options_lp(planning.options);
  SimulationLP simulation_lp(planning.simulation, options_lp);
  SystemLP system_lp(planning.system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  Result r;
  r.ncols = static_cast<int>(lp.get_numcols());
  auto res = lp.resolve();
  REQUIRE(res.has_value());
  r.optimal = lp.is_optimal();
  r.obj = lp.get_obj_value_raw();
  const auto sol = lp.get_col_sol_raw();
  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto buid = stage.blocks().front().uid();

  const auto& buses = system_lp.elements<BusLP>();
  for (const auto& bus : buses) {
    const auto& w = bus.vsq_cols_at(scenario, stage);
    if (const auto it = w.find(buid); it != w.end()) {
      r.has_vsq = true;
      (bus.uid() == Uid {1} ? r.w_s : r.w_b) = sol[it->second];
    }
    r.has_theta =
        r.has_theta || bus.lookup_theta_col(scenario, stage, buid).has_value();
  }
  for (const auto& line : system_lp.elements<LineLP>()) {
    const auto& q = line.qflow_cols_at(scenario, stage);
    if (const auto it = q.find(buid); it != q.end()) {
      r.qflow = sol[it->second];
    }
    const auto& y = line.vsqtap_cols_at(scenario, stage);
    if (const auto it = y.find(buid); it != y.end()) {
      r.vsqtap = sol[it->second];
    }
    const auto& fp = line.flowp_cols_at(scenario, stage);
    const auto& fn = line.flown_cols_at(scenario, stage);
    if (const auto it = fp.find(buid); it != fp.end()) {
      r.pflow += sol[it->second];
    }
    if (const auto it = fn.find(buid); it != fn.end()) {
      r.pflow -= sol[it->second];
    }
  }
  for (const auto& gen : system_lp.elements<GeneratorLP>()) {
    if (gen.uid() == Uid {1}) {
      continue;
    }
    const auto& q = gen.qgen_cols_at(scenario, stage);
    if (const auto it = q.find(buid); it != q.end()) {
      r.qgen = sol[it->second];
    }
  }
  r.served = r.pflow;  // single load, lossless line
  return r;
}

}  // namespace

TEST_CASE("reactive_ratio of a signed power factor")  // NOLINT
{
  CHECK(reactive_ratio(1.0) == doctest::Approx(0.0));
  CHECK(reactive_ratio(0.0) == doctest::Approx(0.0));
  CHECK(reactive_ratio(1.5) == doctest::Approx(0.0));
  CHECK(reactive_ratio(0.8) == doctest::Approx(0.75));
  CHECK(reactive_ratio(-0.8) == doctest::Approx(-0.75));
  CHECK(reactive_ratio(0.6) == doctest::Approx(4.0 / 3.0));
}

TEST_CASE("network_model option")  // NOLINT
{
  PlanningOptions po;
  SUBCASE("default is dc, Kirchhoff unchanged")
  {
    const PlanningOptionsLP o(po);
    CHECK(o.network_model() == NetworkModel::dc);
    CHECK(o.use_kirchhoff());
    CHECK_FALSE(o.use_lindistflow());
    CHECK(o.mva_polygon_sides() == 8);
  }
  SUBCASE("lindistflow turns the DC angle rows off")
  {
    po.model_options.network_model = "lindistflow";
    po.model_options.mva_polygon_sides = 0;
    const PlanningOptionsLP o(po);
    CHECK(o.network_model() == NetworkModel::lindistflow);
    CHECK_FALSE(o.use_kirchhoff());
    CHECK(o.use_lindistflow());
    CHECK(o.mva_polygon_sides() == 0);
  }
  SUBCASE("single bus disables lindistflow; unknown name falls back")
  {
    po.model_options.network_model = "lindistflow";
    po.model_options.use_single_bus = true;
    CHECK_FALSE(PlanningOptionsLP(po).use_lindistflow());
    po.model_options.network_model = "acopf";
    po.model_options.use_single_bus = false;
    CHECK(PlanningOptionsLP(po).network_model() == NetworkModel::dc);
  }
  SUBCASE("merge and covers")
  {
    ModelOptions a;
    ModelOptions b;
    b.network_model = "lindistflow";
    b.mva_polygon_sides = 12;
    CHECK(b.has_any());
    CHECK_FALSE(a.covers(b));
    a.merge(b);
    CHECK(a.network_model.value_or("") == "lindistflow");
    CHECK(a.mva_polygon_sides.value_or(0) == 12);
  }
}

TEST_CASE("LinDistFlow two-bus feeder")  // NOLINT
{
  SUBCASE("dc default builds no LinDistFlow columns")
  {
    const auto r = solve(Feeder {.model = "", .demand = ""});
    CHECK(r.optimal);
    CHECK_FALSE(r.has_vsq);
    CHECK(r.qflow == doctest::Approx(0.0));
  }

  SUBCASE("voltage drop with a lagging load")
  {
    // Q = 0.75; w_b = 1 − 2(1·1 + 2·0.75)/100 = 0.95.
    const auto r = solve(Feeder {});
    REQUIRE(r.optimal);
    CHECK(r.has_vsq);
    CHECK_FALSE(r.has_theta);
    CHECK(r.w_s == doctest::Approx(1.0));
    CHECK(r.pflow == doctest::Approx(1.0));
    CHECK(r.qflow == doctest::Approx(0.75));
    CHECK(r.w_b == doctest::Approx(0.95));
    CHECK(r.obj == doctest::Approx(50.0));
  }

  SUBCASE("leading load raises the voltage")
  {
    // Q = −0.75; w_b = 1 − 2(1 − 1.5)/100 = 1.01.
    const auto r = solve(Feeder {.demand = R"(, "power_factor": -0.8)"});
    REQUIRE(r.optimal);
    CHECK(r.qflow == doctest::Approx(-0.75));
    CHECK(r.w_b == doctest::Approx(1.01));
  }

  SUBCASE("unity load: resistive drop only")
  {
    const auto r = solve(Feeder {.demand = ""});
    REQUIRE(r.optimal);
    CHECK(r.qflow == doctest::Approx(0.0));
    CHECK(r.w_b == doctest::Approx(0.98));
  }

  SUBCASE("shunt capacitor supplies the load's reactive power")
  {
    // Q = 0.75(1 − w_b), w_b = 0.98 − 0.04·Q  →  w_b = 0.95/0.97.
    const auto r = solve(Feeder {.bus_b = R"(, "shunt_q": 0.75)"});
    REQUIRE(r.optimal);
    const double w = 0.95 / 0.97;
    CHECK(r.w_b == doctest::Approx(w));
    CHECK(r.qflow == doctest::Approx(0.75 * (1.0 - w)));
  }

  SUBCASE("vmin is met by local reactive support")
  {
    // vmin 0.98 → w_b ≥ 0.9604 → 2(1 + 2(0.75 − q))/100 ≤ 0.0396
    // → q ≥ 0.26 from the local var source.
    const auto r = solve(Feeder {
        .bus_b = R"(, "vmin_pu": 0.98, "vmax_pu": 1.05)",
        .gens = R"(, {"uid": 2, "name": "var", "bus": 2, "pmax": 0,
                     "qmin": 0, "qmax": 1})",
    });
    REQUIRE(r.optimal);
    CHECK(r.w_b >= 0.9604 - 1e-7);
    CHECK(r.qgen >= 0.26 - 1e-7);
    CHECK(r.obj == doctest::Approx(50.0));
  }

  SUBCASE("vmin without support sheds load")
  {
    // w_b = 1 − 0.05·P ≥ 0.9604 → P ≤ 0.792.
    const auto r = solve(Feeder {.bus_b = R"(, "vmin_pu": 0.98)"});
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(0.792));
    CHECK(r.obj == doctest::Approx((0.792 * 50.0) + (0.208 * 1000.0)));
  }

  SUBCASE("fixed tap ratio")
  {
    // w_b = 1/0.95² − 0.05.
    const auto r = solve(Feeder {.line = R"(, "tap_ratio": 0.95)"});
    REQUIRE(r.optimal);
    CHECK(r.w_b == doctest::Approx((1.0 / (0.95 * 0.95)) - 0.05));
  }

  SUBCASE("optimised tap restores the voltage")
  {
    // vmin 1.0 → y = w_s/t² ≥ 1.05 → t ≤ 0.9759, inside [0.9, 1.1].
    const auto r = solve(Feeder {
        .bus_b = R"(, "vmin_pu": 1.0)",
        .line = R"(, "tap_ratio_min": 0.9, "tap_ratio_max": 1.1)",
    });
    REQUIRE(r.optimal);
    CHECK(r.w_b >= 1.0 - 1e-7);
    CHECK(r.vsqtap >= 1.05 - 1e-7);
    CHECK(r.vsqtap <= (1.0 / 0.81) + 1e-7);
    CHECK(r.obj == doctest::Approx(50.0));
  }

  SUBCASE("tap range too narrow sheds load")
  {
    // y ≤ 1/0.99² = 1.0203; w_b = y − 0.05·P ≥ 1 → P ≤ 0.4061.
    const auto r = solve(Feeder {
        .bus_b = R"(, "vmin_pu": 1.0)",
        .line = R"(, "tap_ratio_min": 0.99, "tap_ratio_max": 1.01)",
    });
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(((1.0 / (0.99 * 0.99)) - 1.0) / 0.05));
  }
}

TEST_CASE("LinDistFlow MVA polygon")  // NOLINT
{
  SUBCASE("inside the circle: no effect")
  {
    // S = |0.8 + j0.6| = 1.0, polygon circumscribes the circle.
    const auto r = solve(
        Feeder {.line = R"(, "tmax_ab": 1.0, "tmax_ba": 1.0)", .lmax = 0.8});
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(0.8));
  }

  SUBCASE("octagon binds above the rating")
  {
    // P = 0.9, Q = 0.675: the 45° side cos·P + sin·Q ≤ 1 binds →
    // P(1 + 0.75)/√2 = 1 → P = √2/1.75.
    const auto r = solve(
        Feeder {.line = R"(, "tmax_ab": 1.0, "tmax_ba": 1.0)", .lmax = 0.9});
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(std::numbers::sqrt2 / 1.75));
  }

  SUBCASE("sides = 0 keeps only the active-power limit")
  {
    const auto r = solve(Feeder {
        .model = R"(, "network_model": "lindistflow", "mva_polygon_sides": 0)",
        .line = R"(, "tmax_ab": 1.0, "tmax_ba": 1.0)",
        .lmax = 0.9,
    });
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(0.9));
  }

  SUBCASE("expansion sizes the MVA rating")
  {
    // capacity 0 + modules of 0.1 MVA: the octagon needs
    // S ≥ (0.8 + 0.6)/√2 = 0.98995 → 10 modules (continuous: 9.8995).
    const auto r = solve(Feeder {
        .line = R"(, "capacity": 0, "expcap": 0.1, "expmod": 20,
                   "annual_capcost": 8760)",
        .lmax = 0.8,
    });
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(0.8));
    const double s = 1.4 / std::numbers::sqrt2;
    CHECK(r.obj == doctest::Approx((0.8 * 50.0) + s).epsilon(1e-6));
  }

  SUBCASE("integer expansion modules")
  {
    const auto r = solve(Feeder {
        .line = R"(, "capacity": 0, "expcap": 0.1, "expmod": 20,
                   "annual_capcost": 8760, "integer_expmod": true)",
        .lmax = 0.8,
    });
    REQUIRE(r.optimal);
    CHECK(r.obj == doctest::Approx((0.8 * 50.0) + 1.0).epsilon(1e-6));
  }
}

TEST_CASE("LinDistFlow candidate line (disjunctive big-M)")  // NOLINT
{
  // Parallel candidate l2 (same r, x as l): built, the drop halves:
  // w_b = 1 − 0.025 = 0.975 ≥ 0.98² → the 1 MW load is served.
  const auto candidate = [](double annual_capcost, bool integer = true)
  {
    return std::format(
        R"(, {{"uid": 2, "name": "l2", "bus_a": 1, "bus_b": 2,
              "voltage": 10, "resistance": 1.0, "reactance": 2.0,
              "capacity": 0, "expcap": 5, "expmod": 1,
              "integer_expmod": {}, "annual_capcost": {}}})",
        integer,
        annual_capcost);
  };
  SUBCASE("built when cheaper than shedding")
  {
    const auto r = solve(Feeder {
        .bus_b = R"(, "vmin_pu": 0.98, "vmax_pu": 1.05)",
        .lines = candidate(8760.0 / 5.0),
    });
    REQUIRE(r.optimal);
    CHECK(r.w_b == doctest::Approx(0.975));
    CHECK(r.obj == doctest::Approx(50.0 + 1.0));
  }
  SUBCASE("unbuilt candidate does not tie the bus voltages")
  {
    const auto r = solve(Feeder {
        .bus_b = R"(, "vmin_pu": 0.98, "vmax_pu": 1.05)",
        .lines = candidate(1.0e9),
    });
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(0.792));
    CHECK(r.w_b == doctest::Approx(0.9604));
  }
  SUBCASE("LP relaxation: a fraction of the circuit, a fraction of the drop")
  {
    // Continuous module α = C/5.  The two drops sum to
    // 0.02·1 + 0.04·0.75 = 0.05; l carries at most w_a − w_b ≤ 0.0396,
    // so l2 must take z₂ ≥ 0.0104 and the hull row z₂ ≤ M·α with
    // M = max(1 − 0.9604, 1.1025 − 1) = 0.1025 prices α ≥ 0.10146
    // (the MVA polygon alone would accept α ≈ 0.05).
    const auto r = solve(Feeder {
        .bus_b = R"(, "vmin_pu": 0.98, "vmax_pu": 1.05)",
        .lines = candidate(8760.0 / 5.0, false),
    });
    REQUIRE(r.optimal);
    CHECK(r.served == doctest::Approx(1.0));
    CHECK(r.w_b == doctest::Approx(0.9604));
    CHECK(r.obj == doctest::Approx(50.0 + (0.0104 / 0.1025)));
  }
}
