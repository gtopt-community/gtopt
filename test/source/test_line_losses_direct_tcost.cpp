// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `piecewise_direct` line losses must charge the full transfer cost
// `tcost · |f|` (add_to_lp audit, P0-23).  The flow is split into K
// segment columns with Σ_k seg_k = |f|, so each segment carries the full
// per-MWh `tcost`; dividing it by K (as before the fix) charged only
// `tcost · |f| / K`, unlike every other loss mode.

#include <format>
#include <string>
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

double solve_two_bus(std::string_view mode)
{
  // g1 ($10) at b1 serves 100 MW at b2 through l1 (tcost $5/MWh, K = 4,
  // negligible resistance); g2 at b2 costs $20.  Full delivery through
  // the line: (10 + 5) · 100 = 1500.
  const auto json = std::format(R"({{
   "name":"pwd_tcost",
   "bus_array":[{{"uid":1,"name":"b1"}},{{"uid":2,"name":"b2"}}],
   "generator_array":[
    {{"uid":1,"name":"g1","bus":"b1","pmax":500,"capacity":500,
      "gcost":10}},
    {{"uid":2,"name":"g2","bus":"b2","pmax":500,"capacity":500,
      "gcost":20}}],
   "demand_array":[{{"uid":1,"name":"d2","bus":"b2","lmax":100}}],
   "line_array":[{{"uid":1,"name":"l1","bus_a":"b1","bus_b":"b2",
     "tmax_ab":200,"tmax_ba":200,"tcost":5,"resistance":0.0001,
     "voltage":100,"loss_segments":4,"line_losses_mode":"{}"}}]}})",
                                mode);
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
              },
          },
  };
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = false;
  popts.model_options.use_kirchhoff = false;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);
  return li.get_obj_value();
}

}  // namespace

TEST_CASE("piecewise_direct charges the full line transfer cost")  // NOLINT
{
  constexpr double expected = (10.0 + 5.0) * 100.0;
  SUBCASE("piecewise (reference)")
  {
    CHECK(solve_two_bus("piecewise")
          == doctest::Approx(expected).epsilon(1e-4));
  }
  SUBCASE("piecewise_direct")
  {
    CHECK(solve_two_bus("piecewise_direct")
          == doctest::Approx(expected).epsilon(1e-4));
  }
}
