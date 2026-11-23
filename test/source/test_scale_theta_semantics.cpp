// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `model_options.scale_theta` ENLARGES the bus angle in the LP:
//   θ_LP = scale_theta · θ (rad)   — 1 = radians, 1000 = milliradians,
//   auto (unset) = 1 / (3 · median(X/V²)).
// Physical results must not depend on it; LP column bounds and values
// scale by exactly scale_theta.

#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>

using namespace gtopt;

namespace
{

// Meshed 3-bus triangle with unequal reactances: generators at b1 and
// b2, demand at b3; l13 limited so the loop flow is non-trivial.
constexpr std::string_view meshed_json = R"json(
  {
    "options": {
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {
        "use_single_bus": false,
        "use_kirchhoff": true,
        "line_losses_mode": "none",
        "scale_objective": 1,
        "demand_fail_cost": 1000
      }
    },
    "simulation": {
      "block_array": [{"uid": 1, "duration": 1}],
      "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
      "scenario_array": [{"uid": 1, "probability_factor": 1}]
    },
    "system": {
      "name": "mesh3",
      "bus_array": [
        {"uid": 1, "name": "b1"},
        {"uid": 2, "name": "b2"},
        {"uid": 3, "name": "b3"}
      ],
      "generator_array": [
        {"uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 300,
         "gcost": 10, "capacity": 300},
        {"uid": 2, "name": "g2", "bus": "b2", "pmin": 0, "pmax": 300,
         "gcost": 30, "capacity": 300}
      ],
      "demand_array": [
        {"uid": 1, "name": "d3", "bus": "b3", "lmax": [[200.0]]}
      ],
      "line_array": [
        {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
         "reactance": 0.02, "tmax_ab": 300, "tmax_ba": 300},
        {"uid": 2, "name": "l23", "bus_a": "b2", "bus_b": "b3",
         "reactance": 0.05, "tmax_ab": 300, "tmax_ba": 300},
        {"uid": 3, "name": "l13", "bus_a": "b1", "bus_b": "b3",
         "reactance": 0.04, "tmax_ab": 120, "tmax_ba": 120}
      ]
    }
  }
)json";

struct ThetaRun
{
  double obj {0.0};
  double scale_theta {0.0};
  std::vector<double> theta;  ///< physical [rad], per bus
  std::vector<double> theta_raw;  ///< LP units, per bus
  std::vector<double> upp_raw;  ///< LP upper bound, per bus
  double theta_max {0.0};
};

[[nodiscard]] auto solve_mesh(std::string_view mode,
                              std::optional<double> scale_theta) -> ThetaRun
{
  auto planning =
      daw::json::from_json<Planning>(meshed_json, StrictParsePolicy);
  planning.options.model_options.kirchhoff_mode = OptName {std::string {mode}};
  planning.options.model_options.scale_theta = scale_theta;
  // The raw-θ checks isolate scale_theta: pin row_max so the multi-bus
  // Kirchhoff ruiz default does not add its own column scale.
  planning.options.lp_matrix_options.equilibration_method =
      LpEquilibrationMethod::row_max;

  PlanningLP planning_lp(std::move(planning));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());

  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  const auto& sys_lp = systems.front().front();
  const auto& li = sys_lp.linear_interface();

  ThetaRun out {
      .obj = li.get_obj_value(),
      .scale_theta = planning_lp.options().scale_theta(),
      .theta = {},
      .theta_raw = {},
      .upp_raw = {},
      .theta_max = planning_lp.options().theta_max(),
  };
  const auto& sim_lp = sys_lp.system_context().simulation();
  const auto& scenario = sim_lp.scenarios().front();
  const auto& stage = sim_lp.stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto sol = li.get_col_sol();
  const auto sol_raw = li.get_col_sol_raw();
  const auto upp_raw = li.get_col_upp_raw();
  for (const auto& bus_lp : sys_lp.elements<BusLP>()) {
    if (const auto col = bus_lp.lookup_theta_col(scenario, stage, buid)) {
      out.theta.push_back(sol[*col]);
      out.theta_raw.push_back(sol_raw[*col]);
      out.upp_raw.push_back(upp_raw[*col]);
    }
  }
  return out;
}

}  // namespace

TEST_CASE("scale_theta enlarges θ: physical results invariant")  // NOLINT
{
  const auto r1 = solve_mesh("node_angle", 1.0);
  const auto r1k = solve_mesh("node_angle", 1000.0);
  const auto ra = solve_mesh("node_angle", std::nullopt);

  SUBCASE("auto scale_theta = 1 / (3 · median(X/V²))")
  {
    // median{0.02, 0.05, 0.04} = 0.04
    CHECK(ra.scale_theta == doctest::Approx(1.0 / (3.0 * 0.04)));
    CHECK(r1k.scale_theta == doctest::Approx(1000.0));
  }

  SUBCASE("objective and physical θ identical for {1, 1000, auto}")
  {
    CHECK(r1k.obj == doctest::Approx(r1.obj).epsilon(1e-6));
    CHECK(ra.obj == doctest::Approx(r1.obj).epsilon(1e-6));
    REQUIRE(r1.theta.size() == 3);
    REQUIRE(r1k.theta.size() == 3);
    REQUIRE(ra.theta.size() == 3);
    bool any_nonzero = false;
    for (std::size_t i = 0; i < 3; ++i) {
      CHECK(r1k.theta[i] == doctest::Approx(r1.theta[i]).epsilon(1e-6));
      CHECK(ra.theta[i] == doctest::Approx(r1.theta[i]).epsilon(1e-6));
      any_nonzero = any_nonzero || std::abs(r1.theta[i]) > 1e-6;
    }
    CHECK(any_nonzero);
  }

  SUBCASE("LP θ values and bounds scale by scale_theta")
  {
    for (const auto* r : {&r1, &r1k, &ra}) {
      REQUIRE(r->theta.size() == 3);
      for (std::size_t i = 0; i < 3; ++i) {
        CHECK(r->theta_raw[i]
              == doctest::Approx(r->scale_theta * r->theta[i]).epsilon(1e-6));
        // Non-reference buses carry ±theta_max (physical) bounds.
        if (r->theta[i] != 0.0 || r->upp_raw[i] != 0.0) {
          CHECK(
              r->upp_raw[i]
              == doctest::Approx(r->scale_theta * r->theta_max).epsilon(1e-6));
        }
      }
    }
  }
}

TEST_CASE("scale_theta — cycle_basis row scaling is invariant")  // NOLINT
{
  // cycle_basis has no θ columns; scale_theta multiplies the cycle rows
  // (row_scale = scale_theta).  The optimum must not depend on it and
  // must match node_angle.
  const auto na = solve_mesh("node_angle", std::nullopt);
  const auto c1 = solve_mesh("cycle_basis", 1.0);
  const auto c1k = solve_mesh("cycle_basis", 1000.0);
  const auto ca = solve_mesh("cycle_basis", std::nullopt);
  CHECK(c1.theta.empty());
  CHECK(c1.obj == doctest::Approx(na.obj).epsilon(1e-6));
  CHECK(c1k.obj == doctest::Approx(na.obj).epsilon(1e-6));
  CHECK(ca.obj == doctest::Approx(na.obj).epsilon(1e-6));
  CHECK(ca.scale_theta == doctest::Approx(1.0 / (3.0 * 0.04)));
}
