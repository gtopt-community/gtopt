// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// add_to_lp reference rule (active_ref.hpp): a reference to an element
// that is inactive in the stage, or whose LP entity was elided, never
// throws — the dependent term is omitted for that cell.  One test per
// former `.at()` crash site of the add_to_lp audit (family P0-9/P0-10).

#include <doctest/doctest.h>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/demand_profile_lp.hpp>
#include <gtopt/generator_profile_lp.hpp>
#include <gtopt/hydrogen_demand_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/reservoir_discharge_limit_lp.hpp>
#include <gtopt/reservoir_seepage_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/volume_right_lp.hpp>

using namespace gtopt;

namespace
{

/// Three one-hour stages, one block each, one scenario.
Simulation three_stage_simulation()
{
  return {
      .block_array =
          {
              {.uid = Uid {1}, .duration = 1},
              {.uid = Uid {2}, .duration = 1},
              {.uid = Uid {3}, .duration = 1},
          },
      .stage_array =
          {
              {.uid = Uid {1}, .first_block = 0, .count_block = 1},
              {.uid = Uid {2}, .first_block = 1, .count_block = 1},
              {.uid = Uid {3}, .first_block = 2, .count_block = 1},
          },
      .scenario_array = {{.uid = Uid {0}}},
  };
}

/// Hydro system: reservoir at j1 → waterway ww1 (turbine tur1) → j2
/// (drain), plus a thermal backup.  The caller tweaks the elements.
System hydro_system()
{
  return System {
      .name = "tolerant_refs",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {
              {.uid = Uid {1}, .name = "d1", .bus = Uid {1}, .capacity = 50.0},
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "hydro_gen",
                  .bus = Uid {1},
                  .gcost = 5.0,
                  .capacity = 500.0,
              },
              {
                  .uid = Uid {2},
                  .name = "thermal_gen",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 200.0,
              },
          },
      .junction_array =
          {
              {.uid = Uid {1}, .name = "j1"},
              {.uid = Uid {2}, .name = "j2", .drain = true},
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww1",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 500.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv1",
                  .junction = Uid {1},
                  .capacity = 10000.0,
                  .emin = 0.0,
                  .emax = 10000.0,
                  .eini = 5000.0,
              },
          },
      .turbine_array =
          {
              {
                  .uid = Uid {1},
                  .name = "tur1",
                  .waterway = Uid {1},
                  .generator = Uid {1},
                  .production_factor = 1.0,
              },
          },
  };
}

/// Build (no throw) and solve; returns the SystemLP for inspection.
void build_and_solve(const System& system, bool lp_reduction = false)
{
  const auto simulation = three_stage_simulation();
  PlanningOptions popts;
  popts.model_options.lp_reduction = lp_reduction;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  std::unique_ptr<SystemLP> system_lp;
  REQUIRE_NOTHROW(system_lp =
                      std::make_unique<SystemLP>(system, simulation_lp));
  auto&& lp = system_lp->linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  CHECK(result.value() == 0);
  // update_lp visits every (scenario, stage), including the inactive ones.
  CHECK_NOTHROW((void)system_lp->update_lp());
}

}  // namespace

TEST_CASE("ReservoirSeepage: inactive waterway omits the row")  // NOLINT
{
  auto system = hydro_system();
  // A dedicated seepage waterway j1 → j2, out of service in stage 2.
  system.waterway_array.push_back({
      .uid = Uid {2},
      .name = "ww_seep",
      .active = std::vector<IntBool> {True, False, True},
      .junction_a = Uid {1},
      .junction_b = Uid {2},
      .fmin = 0.0,
      .fmax = 100.0,
  });
  system.reservoir_seepage_array = {
      {
          .uid = Uid {1},
          .name = "seep1",
          .waterway = Uid {2},
          .reservoir = Uid {1},
          .segments =
              {
                  {.volume = 0.0, .slope = 0.001, .constant = 1.0},
                  {.volume = 4000.0, .slope = 0.002, .constant = 0.5},
              },
      },
  };

  const auto simulation = three_stage_simulation();
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(simulation, options);
  std::unique_ptr<SystemLP> system_lp;
  REQUIRE_NOTHROW(system_lp =
                      std::make_unique<SystemLP>(system, simulation_lp));

  const auto& seep = system_lp->elements<ReservoirSeepageLP>().front();
  const auto& scenario = system_lp->scene().scenarios()[0];
  const auto& stages = system_lp->phase().stages();
  CHECK(seep.seepage_rows_at(scenario, stages[0]).size() == 1);
  CHECK(seep.seepage_rows_at(scenario, stages[2]).size() == 1);

  auto&& lp = system_lp->linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  CHECK(result.value() == 0);
  CHECK_NOTHROW((void)system_lp->update_lp());
}

TEST_CASE("ReservoirSeepage: elided waterway flow (fmin=fmax=0)")  // NOLINT
{
  auto system = hydro_system();
  // Zero-capacity seepage waterway: under `lp_reduction` its flow column
  // is elided (waterway_lp.cpp), which used to crash the seepage.  (With
  // the flag off the column is a fixed 0 and the seepage row `0 = 1 +
  // slope·V` is infeasible, as the data says.)
  system.waterway_array.push_back({
      .uid = Uid {2},
      .name = "ww_seep",
      .junction_a = Uid {1},
      .junction_b = Uid {2},
      .fmin = 0.0,
      .fmax = 0.0,
  });
  system.reservoir_seepage_array = {
      {
          .uid = Uid {1},
          .name = "seep1",
          .waterway = Uid {2},
          .reservoir = Uid {1},
          .slope = 0.001,
          .constant = 1.0,
      },
  };
  build_and_solve(system, /*lp_reduction=*/true);
}

TEST_CASE("ReservoirDischargeLimit: commissioned reservoir")  // NOLINT
{
  auto system = hydro_system();
  // The reservoir enters service in stage 2 (commissioning).
  system.reservoir_array.front().active =
      std::vector<IntBool> {False, True, True};
  system.reservoir_discharge_limit_array = {
      {
          .uid = Uid {1},
          .name = "rdl1",
          .waterway = Uid {1},
          .reservoir = Uid {1},
          .segments =
              {
                  {.volume = 0.0, .slope = 0.1, .intercept = 100.0},
                  {.volume = 6000.0, .slope = 0.05, .intercept = 400.0},
              },
      },
  };
  build_and_solve(system);
}

TEST_CASE("ReservoirProductionFactor: commissioned reservoir")  // NOLINT
{
  auto system = hydro_system();
  system.reservoir_array.front().active =
      std::vector<IntBool> {False, True, True};
  system.turbine_array.front().active =
      std::vector<IntBool> {False, True, True};
  system.reservoir_production_factor_array = {
      {
          .uid = Uid {1},
          .name = "pf1",
          .turbine = Uid {1},
          .reservoir = Uid {1},
          .mean_production_factor = 1.0,
          .segments =
              {
                  {.volume = 0.0, .slope = 0.0001, .constant = 0.5},
              },
      },
  };
  build_and_solve(system);
}

TEST_CASE("GeneratorProfile: generator with every block elided")  // NOLINT
{
  auto system = hydro_system();
  system.generator_array.push_back({
      .uid = Uid {3},
      .name = "solar",
      .bus = Uid {1},
      .pmax = 0.0,
      .gcost = 0.0,
      .capacity = 100.0,
  });
  system.generator_profile_array = {
      {
          .uid = Uid {1},
          .name = "solar_profile",
          .generator = Uid {3},
          .profile = 0.5,
      },
  };
  build_and_solve(system);
}

TEST_CASE("DemandProfile: demand with every block elided")  // NOLINT
{
  auto system = hydro_system();
  system.demand_array.push_back({
      .uid = Uid {2},
      .name = "d_off",
      .bus = Uid {1},
      .lmax = 0.0,
      .capacity = 10.0,
  });
  system.demand_profile_array = {
      {
          .uid = Uid {1},
          .name = "d_off_profile",
          .demand = Uid {2},
          .profile = 0.5,
      },
  };
  build_and_solve(system);
}

TEST_CASE("SimpleCommitment: generator with every block elided")  // NOLINT
{
  auto system = hydro_system();
  system.generator_array.push_back({
      .uid = Uid {3},
      .name = "peaker",
      .bus = Uid {1},
      .pmax = 0.0,
      .gcost = 50.0,
      .capacity = 100.0,
  });
  system.simple_commitment_array = {
      {
          .uid = Uid {1},
          .name = "uc_peaker",
          .generator = Uid {3},
          .relax = true,
      },
  };
  system.fold_legacy_simple_commitments();
  build_and_solve(system);
}

TEST_CASE("VolumeRight: inactive parent right")  // NOLINT
{
  // The withdrawing right and its reservoir are commissioned together in
  // stage 2; the parent ledger only exists from stage 3 — the parent debit
  // is omitted in stage 2.  (A right active on an INACTIVE reservoir is
  // an error instead: see test_lp_build_error.cpp.)
  auto system = hydro_system();
  system.reservoir_array.front().active =
      std::vector<IntBool> {False, True, True};
  system.volume_right_array = {
      {
          .uid = Uid {1},
          .name = "parent",
          .active = std::vector<IntBool> {False, False, True},
          .emax = 1000.0,
          .eini = 500.0,
      },
      {
          .uid = Uid {2},
          .name = "child",
          .active = std::vector<IntBool> {False, True, True},
          .reservoir = Uid {1},
          .right_reservoir = Uid {1},
          .emax = 1000.0,
          .eini = 500.0,
          .fmax = 10.0,
      },
  };
  build_and_solve(system);
}

TEST_CASE("FlowRight: inactive junction_b (return / bypass end)")  // NOLINT
{
  // junction_b out of service in stage 2: the return credit is omitted
  // (the water leaves at junction_a, never created) and the bypass arc is
  // not built.  (An inactive junction_a is an error: see
  // test_lp_build_error.cpp.)
  auto system = hydro_system();
  system.junction_array.push_back({
      .uid = Uid {3},
      .name = "j3",
      .active = std::vector<IntBool> {True, False, True},
      .drain = true,
  });
  system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "fr_return",
          .junction_a = Uid {1},
          .fmax = 5.0,
          .junction_b = Uid {3},
          .consumptive = false,
      },
      {
          .uid = Uid {2},
          .name = "fr_bypass",
          .junction_a = Uid {1},
          .fmax = 5.0,
          .junction_b = Uid {3},
          .use_bypass = true,
      },
  };
  build_and_solve(system);
}

TEST_CASE("HydrogenDemand: inactive node skips the demand")  // NOLINT
{
  auto system = hydro_system();
  system.hydrogen_node_array = {
      {
          {
              .uid = Uid {11},
              .name = "h2",
              .active = std::vector<IntBool> {True, False, True},
          },
      },
  };
  system.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_offtake",
          .hydrogen_node = Uid {11},
          .lmax = 0.0,
      },
  };

  const auto simulation = three_stage_simulation();
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(simulation, options);
  std::unique_ptr<SystemLP> system_lp;
  REQUIRE_NOTHROW(system_lp =
                      std::make_unique<SystemLP>(system, simulation_lp));
  const auto& hd = system_lp->elements<HydrogenDemandLP>().front();
  const auto& scenario = system_lp->scene().scenarios()[0];
  const auto& stages = system_lp->phase().stages();
  CHECK(hd.load_cols_at(scenario, stages[0]).size() == 1);
  CHECK_THROWS((void)hd.load_cols_at(scenario, stages[1]));  // not built
  CHECK(hd.load_cols_at(scenario, stages[2]).size() == 1);
}
