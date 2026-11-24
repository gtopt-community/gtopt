// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Why an SDDP solve / cascade level stops: convergence_tol (gap test),
// ΔUB stationarity (own window + signed gap below stationary_gap_ceiling),
// max_iterations, cascade global budget.
//
// Case — two one-stage phases, two scenes (wet / dry, p = 0.5), an LNG
// cargo `cargo` (global, state across phases) caps the LNG unit; a peaker
// covers the rest.  SDDP bounds: iter 1 UB 5000 / LB 3000 (40 % gap),
// then 6150 / 3000, 5075 / 4400, 5000 / 5000 — the optimum is 5000.

#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/cascade_method.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>

using namespace gtopt;

namespace
{

// clang-format off
constexpr std::string_view stop_case_json = R"js({
  "options": {
    "annual_discount_rate": 0.0,
    "output_format": "csv",
    "output_compression": "uncompressed",
    "output_directory": "output_stop_reasons",
    "model_options": { "use_single_bus": true, "scale_objective": 1,
                       "demand_fail_cost": 10000 }
  },
  "simulation": {
    "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 } ],
    "stage_array": [
      { "uid": 1, "first_block": 0, "count_block": 1 },
      { "uid": 2, "first_block": 1, "count_block": 1 }
    ],
    "phase_array": [
      { "uid": 1, "first_stage": 0, "count_stage": 1 },
      { "uid": 2, "first_stage": 1, "count_stage": 1 }
    ],
    "scenario_array": [
      { "uid": 1, "probability_factor": 0.5 },
      { "uid": 2, "probability_factor": 0.5 }
    ],
    "scene_array": [
      { "uid": 1, "name": "wet", "first_scenario": 0, "count_scenario": 1 },
      { "uid": 2, "name": "dry", "first_scenario": 1, "count_scenario": 1 }
    ]
  },
  "system": {
    "name": "stop_reasons",
    "bus_array": [ { "uid": 1, "name": "b1" } ],
    "demand_array": [ { "uid": 1, "name": "d1", "bus": 1, "lmax": 100 } ],
    "generator_array": [
      { "uid": 1, "name": "solar", "bus": 1, "gcost": 0, "capacity": 100 },
      { "uid": 2, "name": "lng", "bus": 1, "gcost": 10, "capacity": 200 },
      { "uid": 3, "name": "peaker", "bus": 1, "gcost": 100, "capacity": 200 }
    ],
    "generator_profile_array": [
      { "uid": 1, "name": "solar_p", "generator": 1,
        "profile": [[[0.5], [0.5]], [[0.5], [0.2]]] }
    ],
    "decision_variable_array": [
      { "uid": 1, "name": "cargo", "lower_bound": 0, "upper_bound": 100,
        "cost": 50.0, "cost_type": "energy", "scope": "global",
        "nonanticipative": true }
    ],
    "user_constraint_array": [
      { "uid": 1, "name": "lng_cap",
        "expression":
          "generator('lng').generation <= decision_variable('cargo').value" }
    ]
  }
})js";
// clang-format on

[[nodiscard]] Planning stop_planning()
{
  Planning base;
  base.merge(parse_planning_json(std::string {stop_case_json}));
  return base;
}

[[nodiscard]] SDDPOptions base_opts()
{
  SDDPOptions opts;
  opts.max_iterations = 30;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  opts.apertures = Array<Uid> {};
  return opts;
}

/// Index (0-based, within the training results) of the converged iterate,
/// or -1.
[[nodiscard]] int converged_at(const std::vector<SDDPIterationResult>& r)
{
  for (std::size_t i = 0; i < r.size(); ++i) {
    if (r[i].converged) {
      return static_cast<int>(i);
    }
  }
  return -1;
}

}  // namespace

TEST_CASE("SDDP stop reason: ΔUB stationary below the gap ceiling")  // NOLINT
{
  PlanningLP plp(stop_planning());
  auto opts = base_opts();
  opts.stationary_tol = 0.005;
  opts.stationary_window = 2;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  const auto at = converged_at(*r);
  REQUIRE(at >= 0);
  const auto& c = (*r)[static_cast<std::size_t>(at)];
  CHECK(c.stationary_converged);
  CHECK(c.gap < opts.stationary_gap_ceiling);
  CHECK(c.upper_bound == doctest::Approx(5000.0));
  CHECK(c.lower_bound == doctest::Approx(5000.0));
  // Own window: a cold start needs window (2) + the cut-less first
  // iteration before the earliest stationary stop (0-based index 3).
  CHECK(at >= 3);
}

TEST_CASE("SDDP stop reason: gap test (stationarity off)")  // NOLINT
{
  PlanningLP plp(stop_planning());
  auto opts = base_opts();
  opts.convergence_mode = ConvergenceMode::gap_only;
  opts.convergence_tol = 1.0e-6;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  const auto at = converged_at(*r);
  REQUIRE(at >= 0);
  const auto& c = (*r)[static_cast<std::size_t>(at)];
  CHECK_FALSE(c.stationary_converged);
  CHECK(c.gap == doctest::Approx(0.0).epsilon(1e-9));
}

TEST_CASE("SDDP stop reason: max_iterations")  // NOLINT
{
  PlanningLP plp(stop_planning());
  auto opts = base_opts();
  opts.max_iterations = 2;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  CHECK(converged_at(*r) == -1);
}

TEST_CASE(  // NOLINT
    "SDDP stop reason: a stationary UB above the gap ceiling keeps going")
{
  // A negative ceiling refuses every stationary stop: the run uses its
  // whole budget although UB is flat at 5000 from iteration 4 on.
  PlanningLP plp(stop_planning());
  auto opts = base_opts();
  opts.max_iterations = 8;
  opts.stationary_window = 2;
  opts.stationary_gap_ceiling = -1.0;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  CHECK(converged_at(*r) == -1);
  CHECK(r->size() >= 8);
}

TEST_CASE(  // NOLINT
    "SDDP stop reason: seeded prior bounds never stop a cold level")
{
  // A previous cascade level ended at UB = 5000.  The new level's first
  // iteration also has UB = 5000 (ΔUB = 0 against the seed) with a 40 %
  // gap.  With the ceiling disabled, only the own-window rule prevents
  // the old instant "stationary" stop.
  PlanningLP plp(stop_planning());
  auto opts = base_opts();
  opts.stationary_window = 2;
  opts.stationary_gap_ceiling = 1.0;
  SDDPMethod sddp(plp, opts);
  sddp.seed_prior_bounds({
      SDDPMethod::PriorIterBounds {.upper_bound = 5000.0,
                                   .lower_bound = 5000.0},
      SDDPMethod::PriorIterBounds {.upper_bound = 5000.0,
                                   .lower_bound = 5000.0},
  });
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  CHECK_FALSE(r->front().converged);
  CHECK(r->front().gap_change == doctest::Approx(0.0));
  CHECK(converged_at(*r) >= 3);
}

TEST_CASE("Cascade stop reasons: budget, then a converged level")  // NOLINT
{
  SUBCASE("global budget exhausted before convergence")
  {
    PlanningLP plp(stop_planning());
    CascadeOptions cascade;
    cascade.sddp_options.max_iterations = 2;  // global budget
    cascade.level_array = {CascadeLevel {.name = OptName {"only"}}};
    CascadePlanningMethod solver(base_opts(), std::move(cascade));
    auto r = solver.solve(plp, SolverOptions {});
    REQUIRE(r.has_value());
    REQUIRE(solver.level_stats().size() == 1);
    CHECK_FALSE(solver.level_stats().front().converged);
  }
  SUBCASE("default criterion converges each level to the optimum")
  {
    PlanningLP plp(stop_planning());
    CascadeOptions cascade;
    cascade.level_array = {
        CascadeLevel {.name = OptName {"l0"}},
        CascadeLevel {.name = OptName {"l1"}},
    };
    CascadePlanningMethod solver(base_opts(), std::move(cascade));
    auto r = solver.solve(plp, SolverOptions {});
    REQUIRE(r.has_value());
    REQUIRE(solver.level_stats().size() == 2);
    for (const auto& st : solver.level_stats()) {
      CHECK(st.converged);
      CHECK(st.upper_bound == doctest::Approx(5000.0));
      CHECK(st.lower_bound == doctest::Approx(5000.0));
    }
  }
}
