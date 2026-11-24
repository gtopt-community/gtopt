// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Objective-coefficient weighting of element penalties / costs (add_to_lp
// audit, P0-1, P0-23, P0-16).  Every cost an element puts in the
// objective must be weighted by the scenario probability and the stage
// discount factor (and the block duration for per-block power costs) —
// `CostHelper` — so the LP minimises the expected discounted cost.
// Each fixture compares the solved objective with a hand-computed value.

#include <format>
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

namespace
{

/// One stage of one 1-hour block, discount @p discount, and two
/// equiprobable scenarios.
Simulation make_two_scenario_simulation(double discount)
{
  return Simulation {
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
                  .discount_factor = discount,
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
}

/// Solve @p system_json; return the physical objective (NaN if not optimal).
double solve_obj(std::string_view system_json,
                 const Simulation& simulation,
                 const PlanningOptions& popts)
{
  const auto sys = daw::json::from_json<System>(system_json);
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

PlanningOptions base_options()
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = true;
  return popts;
}

}  // namespace

TEST_CASE(
    "VolumeRight fail cost is probability- and discount-weighted")  // NOLINT
{
  // A volume right with a 2 hm³ demand and nothing to extract (empty
  // ledger, fmax 0): the whole demand fails in both scenarios.  Expected
  // cost = Σ_s p_s · disc · fail_cost · 2 = 0.8 · 1000 · 2 = 1600.
  // Before the fix the fail column carried the raw fail_cost in every
  // scenario: 2 · 1000 · 2 = 4000.
  constexpr std::string_view json = R"({
   "name":"vr_fail","bus_array":[{"uid":1,"name":"b1"}],
   "volume_right_array":[{"uid":1,"name":"vr","emin":0,"emax":0,"eini":0,
     "demand":2,"fmax":0,"fail_cost":1000}]})";
  const double obj =
      solve_obj(json, make_two_scenario_simulation(0.8), base_options());
  CHECK(obj == doctest::Approx(0.8 * 1000.0 * 2.0));
}
