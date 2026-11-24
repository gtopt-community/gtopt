// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// PENDING DESIGN DECISION (add_to_lp audit, P0-16).
//
// The soft-constraint slack of a UserConstraint is priced with the raw
// `penalty` (or `penalty · Δt · 3600` for `penalty_class = hydro_flow`),
// WITHOUT the scenario probability / stage discount weighting that every
// other cost in the objective carries (`CostHelper`).  This matches the
// header documentation ("fed directly into the slack column's LP
// objective coefficient") but means that, with S equiprobable scenarios,
// a violation weighs S times more than the same violation in a
// single-scenario run, and is not discounted.
//
// This test encodes the probability/discount-weighted semantics and is
// marked `should_fail` until that decision is taken; see the report for
// the options.  When the fix lands, drop `should_fail`.

#include <limits>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

TEST_CASE(  // NOLINT
    "UserConstraint soft penalty is probability-weighted (pending)"
    * doctest::should_fail())
{
  // g1 ($10) must serve 100 MW; `g1 ≤ 60` is soft at $50/MW; g2 costs
  // $200, so the cap is violated by 40 MW in both equiprobable scenarios.
  // Weighted: Σ_s 0.5 · (10·100 + 50·40) = 3000.  Raw penalty (current):
  // 1000 + 2 · 2000 = 5000.
  constexpr std::string_view json = R"({
   "name":"uc_soft","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[
    {"uid":1,"name":"g1","bus":"b1","pmax":200,"capacity":200,"gcost":10},
    {"uid":2,"name":"g2","bus":"b1","pmax":200,"capacity":200,
     "gcost":200}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":100}],
   "user_constraint_array":[{"uid":1,"name":"cap",
     "expression":"generator('g1').generation <= 60","penalty":50}]})";
  const auto sys = daw::json::from_json<System>(json);
  const Simulation simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {1},
                  .probability_factor = 0.5,
              },
              {
                  .uid = Uid {2},
                  .probability_factor = 0.5,
              },
          },
  };
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = true;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);
  CHECK(li.get_obj_value() == doctest::Approx(3000.0));
}
