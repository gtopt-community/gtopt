// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `options.lp_matrix_options` must take effect for a directly constructed
// `PlanningLP` exactly as for the production runner (`gtopt_lp_runner`).
// Before the fix the equilibration default (ruiz for multi-bus Kirchhoff)
// and the JSON `lp_matrix_options` fields were only applied through the
// explicit `LpMatrixOptions` argument the runner builds, so a test that set
// `planning.options.lp_matrix_options.equilibration_method = ruiz` silently
// ran with `row_max` — which is how the ruiz cut-slope bug went unnoticed.

#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/lp_matrix_options.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>

using namespace gtopt;

namespace
{

// 3 buses, lines with reactances spanning two orders of magnitude, so the
// Kirchhoff rows have heterogeneous coefficients and Ruiz moves the
// column scales away from their pre-equilibration values.
constexpr std::string_view three_bus_json = R"json(
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
      "name": "eq3",
      "bus_array": [
        {"uid": 1, "name": "b1"},
        {"uid": 2, "name": "b2"},
        {"uid": 3, "name": "b3"}
      ],
      "generator_array": [
        {"uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 300,
         "gcost": 10, "capacity": 300},
        {"uid": 2, "name": "g3", "bus": "b3", "pmin": 0, "pmax": 50,
         "gcost": 40, "capacity": 50}
      ],
      "demand_array": [
        {"uid": 1, "name": "d2", "bus": "b2", "lmax": [[120.0]]}
      ],
      "line_array": [
        {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
         "reactance": 0.5, "tmax_ab": 200, "tmax_ba": 200},
        {"uid": 2, "name": "l23", "bus_a": "b2", "bus_b": "b3",
         "reactance": 0.005, "tmax_ab": 200, "tmax_ba": 200},
        {"uid": 3, "name": "l13", "bus_a": "b1", "bus_b": "b3",
         "reactance": 0.05, "tmax_ab": 200, "tmax_ba": 200}
      ]
    }
  }
)json";

[[nodiscard]] auto make_planning() -> Planning
{
  return daw::json::from_json<Planning>(three_bus_json, StrictParsePolicy);
}

struct BuildResult
{
  LpEquilibrationMethod method {LpEquilibrationMethod::none};
  std::vector<double> col_scales;
  double obj {0.0};
};

[[nodiscard]] auto build_and_solve(Planning planning,
                                   const LpMatrixOptions& flat_opts = {})
    -> BuildResult
{
  PlanningLP planning_lp(std::move(planning), flat_opts);
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  const auto& li = systems.front().front().linear_interface();
  const auto& scales = li.get_col_scales();
  return {
      .method = li.equilibration_method(),
      .col_scales = std::vector<double>(scales.begin(), scales.end()),
      .obj = li.get_obj_value(),
  };
}

}  // namespace

TEST_CASE("PlanningOptionsLP equilibration default policy")  // NOLINT
{
  PlanningOptions po;

  SUBCASE("multi-bus Kirchhoff defaults to ruiz")
  {
    CHECK(PlanningOptionsLP {po}.equilibration_method()
          == LpEquilibrationMethod::ruiz);
  }

  SUBCASE("single-bus defaults to row_max")
  {
    po.model_options.use_single_bus = true;
    CHECK(PlanningOptionsLP {po}.equilibration_method()
          == LpEquilibrationMethod::row_max);
  }

  SUBCASE("copper-plate multi-bus defaults to row_max")
  {
    po.model_options.use_kirchhoff = false;
    CHECK(PlanningOptionsLP {po}.equilibration_method()
          == LpEquilibrationMethod::row_max);
  }

  SUBCASE("user value always wins")
  {
    po.lp_matrix_options.equilibration_method = LpEquilibrationMethod::none;
    CHECK(PlanningOptionsLP {po}.equilibration_method()
          == LpEquilibrationMethod::none);
  }
}

TEST_CASE("PlanningOptionsLP::resolve_lp_matrix_options merge")  // NOLINT
{
  PlanningOptions po;
  po.lp_matrix_options.equilibration_method = LpEquilibrationMethod::none;
  po.lp_matrix_options.fast_sqrt_method = FastSqrtMethod::newton1;
  po.lp_matrix_options.ruiz_max_iterations = 2;
  po.lp_matrix_options.ruiz_tolerance = 1e-2;
  po.lp_matrix_options.compute_stats = true;
  po.lp_matrix_options.lp_coeff_ratio_threshold = 1e5;
  po.lp_matrix_options.validation.coeff_warn_max = 1e8;
  po.lp_matrix_options.validation.enable = true;
  const PlanningOptionsLP options {po};

  SUBCASE("unset explicit fields are filled from planning options")
  {
    LpMatrixOptions explicit_opts;
    explicit_opts.eps = 1e-12;
    explicit_opts.solver_name = "some_solver";
    const auto r = options.resolve_lp_matrix_options(explicit_opts);
    CHECK(r.equilibration_method.value_or(LpEquilibrationMethod::ruiz)
          == LpEquilibrationMethod::none);
    CHECK(r.fast_sqrt_method.value_or(FastSqrtMethod::std_sqrt)
          == FastSqrtMethod::newton1);
    CHECK(r.ruiz_max_iterations.value_or(-1) == 2);
    CHECK(r.ruiz_tolerance.value_or(-1.0) == doctest::Approx(1e-2));
    CHECK(r.compute_stats.value_or(false));
    CHECK(r.lp_coeff_ratio_threshold.value_or(0.0) == doctest::Approx(1e5));
    CHECK(r.validation.coeff_warn_max.value_or(0.0) == doctest::Approx(1e8));
    // Build-internal fields are untouched.
    CHECK(r.eps == doctest::Approx(1e-12));
    CHECK(r.solver_name == "some_solver");
  }

  SUBCASE("explicit fields win when set")
  {
    LpMatrixOptions explicit_opts;
    explicit_opts.equilibration_method = LpEquilibrationMethod::ruiz;
    explicit_opts.ruiz_max_iterations = 7;
    explicit_opts.compute_stats = false;
    explicit_opts.validation.enable = false;
    const auto r = options.resolve_lp_matrix_options(explicit_opts);
    CHECK(r.equilibration_method.value_or(LpEquilibrationMethod::none)
          == LpEquilibrationMethod::ruiz);
    CHECK(r.ruiz_max_iterations.value_or(-1) == 7);
    CHECK_FALSE(r.compute_stats.value_or(true));
    CHECK_FALSE(r.validation.enable.value_or(true));
    // Unset siblings still come from the planning options.
    CHECK(r.ruiz_tolerance.value_or(-1.0) == doctest::Approx(1e-2));
    CHECK(r.validation.coeff_warn_max.value_or(0.0) == doctest::Approx(1e8));
  }

  SUBCASE("unset everywhere -> topology default")
  {
    const PlanningOptionsLP defaults {PlanningOptions {}};
    const auto r = defaults.resolve_lp_matrix_options({});
    CHECK(r.equilibration_method.value_or(LpEquilibrationMethod::none)
          == LpEquilibrationMethod::ruiz);
    CHECK_FALSE(r.ruiz_max_iterations.has_value());
  }
}

TEST_CASE(  // NOLINT
    "PlanningLP built directly honours options.lp_matrix_options")
{
  SUBCASE("default multi-bus Kirchhoff build applies ruiz (as production)")
  {
    const auto r = build_and_solve(make_planning());
    CHECK(r.method == LpEquilibrationMethod::ruiz);
  }

  SUBCASE("single-bus default build applies row_max")
  {
    auto planning = make_planning();
    planning.options.model_options.use_single_bus = true;
    const auto r = build_and_solve(std::move(planning));
    CHECK(r.method == LpEquilibrationMethod::row_max);
  }

  SUBCASE("planning-level ruiz == runner-style explicit ruiz")
  {
    auto via_planning = make_planning();
    via_planning.options.lp_matrix_options.equilibration_method =
        LpEquilibrationMethod::ruiz;
    const auto a = build_and_solve(std::move(via_planning));

    LpMatrixOptions explicit_opts;
    explicit_opts.equilibration_method = LpEquilibrationMethod::ruiz;
    const auto b = build_and_solve(make_planning(), explicit_opts);

    auto none_planning = make_planning();
    none_planning.options.lp_matrix_options.equilibration_method =
        LpEquilibrationMethod::none;
    const auto c = build_and_solve(std::move(none_planning));

    CHECK(a.method == LpEquilibrationMethod::ruiz);
    CHECK(b.method == LpEquilibrationMethod::ruiz);
    CHECK(c.method == LpEquilibrationMethod::none);

    // Ruiz really ran: the column scales differ from the none build ...
    REQUIRE(a.col_scales.size() == c.col_scales.size());
    REQUIRE(a.col_scales.size() == b.col_scales.size());
    bool any_differs = false;
    for (std::size_t i = 0; i < a.col_scales.size(); ++i) {
      any_differs =
          any_differs || a.col_scales[i] != doctest::Approx(c.col_scales[i]);
    }
    CHECK(any_differs);
    // ... and match the runner-style explicit-argument build exactly.
    for (std::size_t i = 0; i < a.col_scales.size(); ++i) {
      CHECK(a.col_scales[i] == doctest::Approx(b.col_scales[i]));
    }
    // Equilibration never changes the physical optimum.
    CHECK(a.obj == doctest::Approx(c.obj).epsilon(1e-6));
  }

  SUBCASE("explicit argument wins over the planning option")
  {
    auto planning = make_planning();
    planning.options.lp_matrix_options.equilibration_method =
        LpEquilibrationMethod::ruiz;
    LpMatrixOptions explicit_opts;
    explicit_opts.equilibration_method = LpEquilibrationMethod::none;
    const auto r = build_and_solve(std::move(planning), explicit_opts);
    CHECK(r.method == LpEquilibrationMethod::none);
  }
}
