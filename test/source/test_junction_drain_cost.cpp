// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_junction_drain_cost.cpp
/// @brief `Junction.drain_cost` is priced per (m³/s)·h like
///        `Waterway.fcost` / `Reservoir.spillway_cost`.
///
/// plp2gtopt and plexos2gtopt collapse a costed spill arc
/// (`<central>_ver` / `Vert_*` Waterway with `fcost`) onto the source
/// junction's `drain_cost`, documenting both encodings as the same LP.
/// That only holds if the drain column goes through
/// `CostHelper::block_ecost` (× block duration × probability ×
/// discount) exactly like the Waterway it replaces.  A raw per-block
/// coefficient makes the spill `duration` times cheaper on multi-hour
/// blocks.

#include <doctest/doctest.h>
#include <gtopt/bus.hpp>
#include <gtopt/flow.hpp>
#include <gtopt/junction.hpp>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/waterway.hpp>

using namespace gtopt;

namespace
{
namespace junction_drain_cost_test
{

constexpr double kInflow = 10.0;  // m³/s, forced into the source junction
constexpr double kSpillCost = 2.0;  // $/(m³/s)/h

/// Two blocks of 1 h and 4 h in one stage, single scenario.
Simulation make_simulation()
{
  return Simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
              },
              {
                  .uid = Uid {2},
                  .duration = 4.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 2,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

PlanningOptions make_options()
{
  PlanningOptions opts;
  opts.model_options.use_single_bus = true;
  opts.model_options.scale_objective = 1.0;
  return opts;
}

double solve_objective(const System& system)
{
  const auto opts = make_options();
  const PlanningOptionsLP options(opts);
  const auto simulation = make_simulation();
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);
  auto&& lp = sys_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  return lp.get_obj_value_raw();
}

Array<Bus> make_buses()
{
  return {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
}

Array<Flow> make_inflows()
{
  return {
      {
          .uid = Uid {1},
          .name = "inflow",
          .junction = Uid {1},
          .discharge = kInflow,
      },
  };
}

}  // namespace junction_drain_cost_test
}  // namespace

TEST_CASE("Junction drain_cost is weighted by block duration")  // NOLINT
{
  using namespace junction_drain_cost_test;

  // The only exit of j_src is its own costed drain, so the whole inflow
  // spills in both blocks: cost = c·q·(1 h + 4 h).
  const System system = {
      .name = "JunctionDrainCost",
      .bus_array = make_buses(),
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_src",
                  .drain = true,
                  .drain_cost = kSpillCost,
              },
          },
      .flow_array = make_inflows(),
  };

  const double expected = kSpillCost * kInflow * (1.0 + 4.0);
  CHECK(solve_objective(system) == doctest::Approx(expected));
}

TEST_CASE(  // NOLINT
    "Junction drain_cost matches the equivalent costed spill Waterway")
{
  using namespace junction_drain_cost_test;

  // Legacy encoding: j_src → Waterway{fcost} → free-drain ocean junction.
  const System waterway_form = {
      .name = "SpillWaterway",
      .bus_array = make_buses(),
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_src",
              },
              {
                  .uid = Uid {2},
                  .name = "j_ocean",
                  .drain = true,
              },
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_src_ver",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fcost = kSpillCost,
              },
          },
      .flow_array = make_inflows(),
  };

  // Collapsed encoding: the same price on the source junction's drain.
  const System drain_form = {
      .name = "SpillDrain",
      .bus_array = make_buses(),
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_src",
                  .drain = true,
                  .drain_cost = kSpillCost,
              },
          },
      .flow_array = make_inflows(),
  };

  const double obj_waterway = solve_objective(waterway_form);
  CHECK(obj_waterway == doctest::Approx(kSpillCost * kInflow * 5.0));
  CHECK(solve_objective(drain_form) == doctest::Approx(obj_waterway));
}
