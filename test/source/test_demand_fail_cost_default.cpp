// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `model_options.demand_fail_cost` defaults to 1000 $/MWh when unset
// (`PlanningOptionsLP::default_demand_fail_cost`).  An explicit 0 keeps
// its literal meaning (unpenalized, freely sheddable load) and
// `Demand.forced` pins the load at `lmax` (hard demand).

#include <doctest/doctest.h>
#include <gtopt/demand.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Simulation make_one_block_simulation()
{
  return {
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
                  .uid = Uid {0},
              },
          },
  };
}

/// One bus, one 100 MW demand without `fcost`, one generator.
[[nodiscard]] System make_system(double gcost,
                                 double gen_capacity,
                                 OptBool forced = {})
{
  return System {
      .name = "DemandFailCostDefault",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .forced = forced,
                  .capacity = 100.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = gcost,
                  .capacity = gen_capacity,
              },
          },
  };
}

[[nodiscard]] double solve_obj(const System& system, PlanningOptions popts)
{
  popts.model_options.scale_objective = 1.0;
  const auto simulation = make_one_block_simulation();
  const PlanningOptionsLP options(std::move(popts));
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  return lp.get_obj_value();
}

}  // namespace

TEST_CASE("demand_fail_cost default 1000 prices unserved load")  // NOLINT
{
  SUBCASE("cheap generation: unset default serves all load")
  {
    // 100 MW × 50 $/MWh; shedding would cost 1000 $/MWh.
    CHECK(solve_obj(make_system(50.0, 300.0), {}) == doctest::Approx(5'000.0));
  }

  SUBCASE("short supply: shortfall priced at the 1000 default")
  {
    // 60 MW × 50 + 40 MW × 1000.
    CHECK(solve_obj(make_system(50.0, 60.0), {}) == doctest::Approx(43'000.0));
  }

  SUBCASE("generation dearer than default VoLL is shed")
  {
    // 2000 $/MWh > 1000 $/MWh: shed all 100 MW at 1000.
    CHECK(solve_obj(make_system(2'000.0, 300.0), {})
          == doctest::Approx(100'000.0));
  }

  SUBCASE("explicit 0 keeps unpenalized (free) shedding")
  {
    PlanningOptions popts;
    popts.model_options.demand_fail_cost = 0.0;
    CHECK(solve_obj(make_system(50.0, 300.0), popts) == doctest::Approx(0.0));
  }

  SUBCASE("forced demand is served even above the default VoLL")
  {
    CHECK(solve_obj(make_system(2'000.0, 300.0, true), {})
          == doctest::Approx(200'000.0));
  }
}
