// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `PlanningLP::auto_scale_theta` must compute the adaptive `theta_max`
// (Σ_l tmax_l · x_τ_l, clamped to [2π, 1e5]) even when `scale_theta` is
// pinned by the user (e.g. a manual `scale_theta = 1` or `1000`).
// Otherwise the bus-angle columns silently keep the ±2π default and cap
// line flows below `tmax` whenever `tmax · x_τ > 2π`.
//
// Fixture: 2 buses, one line with x = 0.1 p.u. and tmax = 100 MW
// (tmax · x = 10 rad > 2π), 10 $/MWh generator at b1, 100 MW demand at
// b2.  With θ bounded by ±2π the line carries at most 2π / 0.1 ≈ 62.8 MW.

#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view two_bus_json = R"json(
  {
    "options": {
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {
        "use_single_bus": false,
        "use_kirchhoff": true,
        "kirchhoff_mode": "node_angle",
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
      "name": "theta2",
      "bus_array": [
        {"uid": 1, "name": "b1"},
        {"uid": 2, "name": "b2"}
      ],
      "generator_array": [
        {"uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 300,
         "gcost": 10, "capacity": 300}
      ],
      "demand_array": [
        {"uid": 1, "name": "d2", "bus": "b2", "lmax": [[100.0]]}
      ],
      "line_array": [
        {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
         "reactance": 0.1, "tmax_ab": 100, "tmax_ba": 100}
      ]
    }
  }
)json";

struct ThetaResult
{
  double obj {0.0};
  double theta_max {0.0};
  double scale_theta {0.0};
};

[[nodiscard]] auto solve_two_bus(std::optional<double> scale_theta,
                                 std::optional<double> theta_max) -> ThetaResult
{
  auto planning =
      daw::json::from_json<Planning>(two_bus_json, StrictParsePolicy);
  planning.options.model_options.scale_theta = scale_theta;
  planning.options.model_options.theta_max = theta_max;

  PlanningLP planning_lp(std::move(planning));
  const double resolved_theta_max = planning_lp.options().theta_max();
  const double resolved_scale_theta = planning_lp.options().scale_theta();
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  return {
      .obj = systems.front().front().linear_interface().get_obj_value(),
      .theta_max = resolved_theta_max,
      .scale_theta = resolved_scale_theta,
  };
}

}  // namespace

TEST_CASE("auto theta_max is computed even with scale_theta pinned")  // NOLINT
{
  // Full delivery: 100 MW × 10 $/MWh.
  constexpr double full_obj = 100.0 * 10.0;

  SUBCASE("scale_theta unset: auto scale and auto theta_max")
  {
    const auto r = solve_two_bus(std::nullopt, std::nullopt);
    CHECK(r.scale_theta == doctest::Approx(1.0 / (3.0 * 0.1)));
    CHECK(r.theta_max == doctest::Approx(10.0));
    CHECK(r.obj == doctest::Approx(full_obj).epsilon(1e-6));
  }

  SUBCASE("scale_theta = 1 (plp2gtopt): theta_max still Σ tmax·x")
  {
    const auto r = solve_two_bus(1.0, std::nullopt);
    CHECK(r.theta_max == doctest::Approx(10.0));
    // The line reaches tmax: no curtailment.
    CHECK(r.obj == doctest::Approx(full_obj).epsilon(1e-6));
  }

  SUBCASE("scale_theta = 1000 (manual mrad): kept, theta_max still auto")
  {
    // The manual scale wins over auto (1/(3·median(X/V²)) ≈ 3.33 here) and the
    // adaptive theta_max is still computed.
    const auto r = solve_two_bus(1000.0, std::nullopt);
    CHECK(r.scale_theta == doctest::Approx(1000.0));
    CHECK(r.theta_max == doctest::Approx(10.0));
    CHECK(r.obj == doctest::Approx(full_obj).epsilon(1e-6));
  }

  SUBCASE("explicit theta_max is honoured")
  {
    // θ ∈ [−3, 3] ⇒ flow ≤ 3 / 0.1 = 30 MW; 70 MW curtailed.
    const auto r = solve_two_bus(1.0, 3.0);
    CHECK(r.theta_max == doctest::Approx(3.0));
    CHECK(r.obj
          == doctest::Approx((30.0 * 10.0) + (70.0 * 1000.0)).epsilon(1e-6));
  }
}
