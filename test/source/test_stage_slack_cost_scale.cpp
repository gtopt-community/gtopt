// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Stage-level penalty slacks priced `cost · prob · disc` (no duration)
// must carry `cost_scale_type = Energy` so the reduced-cost readback
// divides by the same factor (add_to_lp audit, P0-17).  With the default
// `Power` the reported `*_cost` was additionally divided by the stage
// duration.  Output-only: the LP itself does not change.

#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/emission_zone_lp.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

TEST_CASE("EmissionZone cap slack uses the Energy cost basis")  // NOLINT
{
  constexpr std::string_view json = R"({
   "name":"ez","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[{"uid":1,"name":"g1","bus":"b1","pmax":100,
     "capacity":100,"gcost":10}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":50}],
   "emission_array":[{"uid":1,"name":"co2"}],
   "emission_zone_array":[{"uid":1,"name":"z","emission":"co2","cap":10,
     "cap_cost":50}]})";
  const auto sys = daw::json::from_json<System>(json);
  const Simulation simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 4.0,
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
  popts.model_options.use_single_bus = true;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  const auto& li = system_lp.linear_interface();

  const auto& zone = system_lp.elements<EmissionZoneLP>().front();
  REQUIRE_FALSE(zone.cap_slack_cols().empty());
  for (const auto& [st_key, col] : zone.cap_slack_cols()) {
    CHECK(li.col_cost_scale_type_at(col) == ConstraintScaleType::Energy);
  }
}
