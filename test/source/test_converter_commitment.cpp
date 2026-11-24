// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_converter_commitment.cpp
/// @brief LP-level tests for ``Converter.commitment`` — the per-block
///        integer binary gating of the synthetic charge demand and
///        discharge generator floors/ceilings.
///
/// Verifies the wiring landed in ``ConverterLP::add_to_lp`` (see
/// ``source/converter_lp.cpp``).  Builds a minimal single-bus system
/// with a battery that has ``pmin_charge`` / ``pmin_discharge`` set,
/// then toggles ``Battery.commitment`` and inspects the LP:
///
///   * Without commitment: the static col floors fire every block
///     (load ≥ lmin, gen ≥ pmin), even when the unit would prefer to
///     be idle.
///   * With commitment: per-mode binaries gate the floors — the
///     converter's ``u_charge`` on the charge side, the synthesised
///     ``uc_<bat>_gen`` status on the discharge side.  The LP can idle
///     the battery (load = 0, gen = 0) even when ``lmin`` / ``pmin``
///     are positive, and the two modes are independent (charging does
///     not force the discharge floor and vice versa).

#include <cmath>

#include <doctest/doctest.h>
#include <gtopt/battery.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

using namespace gtopt;

/// 1-stage 2-block simulation.
Simulation make_two_block_simulation()
{
  return {
      .block_array =
          {
              {.uid = Uid {1}, .duration = 1.0},
              {.uid = Uid {2}, .duration = 1.0},
          },
      .stage_array =
          {
              {.uid = Uid {1}, .first_block = 0, .count_block = 2},
          },
      .scenario_array =
          {
              {.uid = Uid {0}},
          },
  };
}

/// Build a single-bus system with cheap thermal g1 (200 MW @ $10), a
/// 50 MW demand, and one unified battery (passed in).
System make_system(Battery battery)
{
  return {
      .name = "CommitmentTest",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {{.uid = Uid {1}, .name = "d1", .bus = Uid {1}, .lmax = 50.0}},
      .generator_array = {{.uid = Uid {1},
                           .name = "g1",
                           .bus = Uid {1},
                           .gcost = 10.0,
                           .capacity = 200.0}},
      .battery_array = {std::move(battery)},
  };
}

/// Locate the synthetic charge demand by name and return its
/// per-block load col ids for the first scenario/stage.
auto charge_load_cols(const SystemLP& sys_lp, const SimulationLP& sim_lp)
{
  const auto& dem_lps = sys_lp.elements<DemandLP>();
  const DemandLP* charge_dem = nullptr;
  for (const auto& dl : dem_lps) {
    if (dl.demand().name == "bat1_dem") {
      charge_dem = &dl;
      break;
    }
  }
  REQUIRE(charge_dem != nullptr);
  return charge_dem->load_cols_at(sim_lp.scenarios().front(),
                                  sim_lp.stages().front());
}

/// Same for the synthetic discharge generator.
auto discharge_gen_cols(const SystemLP& sys_lp, const SimulationLP& sim_lp)
{
  const auto& gen_lps = sys_lp.elements<GeneratorLP>();
  const GeneratorLP* disc_gen = nullptr;
  for (const auto& gl : gen_lps) {
    if (gl.generator().name == "bat1_gen") {
      disc_gen = &gl;
      break;
    }
  }
  REQUIRE(disc_gen != nullptr);
  return disc_gen->generation_cols_at(sim_lp.scenarios().front(),
                                      sim_lp.stages().front());
}

}  // namespace

TEST_CASE("Converter.commitment unset: hard static lmin/pmin floor")  // NOLINT
{
  // Battery with pmin_charge=5 but NO commitment — the synthetic
  // charge Demand's lmin=5 stays as a hard col floor, forcing the
  // LP to charge ≥ 5 MW every block even when it would prefer idle.
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = 100.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .pmin_charge = 5.0,
      .pmin_discharge = 2.0,
      .discharge_cost = 1.0,
      // .commitment intentionally unset
  };
  auto system = make_system(std::move(battery));
  // PlanningLP normally invokes this; we use SystemLP directly so
  // call it explicitly to materialise bat1_gen / bat1_dem / bat1_conv.
  system.expand_batteries();
  const auto simulation = make_two_block_simulation();
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  // Converter commitment emits integer u columns; pin a MIP-capable
  // solver so an ambient GTOPT_SOLVER=clp (CI) doesn't trip the LP-only
  // guard in LinearInterface::load_flat.
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  // With commitment unset, the static lmin survives as the col
  // lower bound.  Read via the LinearInterface's bound spans.
  const auto col_low = lp.get_col_low();
  const auto col_upp = lp.get_col_upp();
  auto&& load_cols = charge_load_cols(sys_lp, sim_lp);
  REQUIRE(load_cols.size() == 2);
  for (const auto& [buid, lcol] : load_cols) {
    CHECK(col_low[lcol] == doctest::Approx(5.0));
    CHECK(col_upp[lcol] == doctest::Approx(10.0));
  }
}

TEST_CASE("Converter.commitment=true: lmin/pmin migrated to u-gated rows")
{
  // Same battery, but with commitment=true.  The synthetic
  // Converter's add_to_lp should:
  //   (a) reset Demand.load col_lowb from 5 → 0,
  //   (b) reset Generator.gen  col_lowb from 2 → 0,
  //   (c) add per-block u_charge / u_discharge integer binaries,
  //   (d) add 4 C2-style gating rows per block.
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = 100.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .pmin_charge = 5.0,
      .pmin_discharge = 2.0,
      .discharge_cost = 1.0,
      .commitment = true,
  };
  auto system = make_system(std::move(battery));
  // PlanningLP normally invokes this; we use SystemLP directly so
  // call it explicitly to materialise bat1_gen / bat1_dem / bat1_conv.
  system.expand_batteries();
  const auto simulation = make_two_block_simulation();
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  // Converter commitment emits integer u columns; pin a MIP-capable
  // solver so an ambient GTOPT_SOLVER=clp (CI) doesn't trip the LP-only
  // guard in LinearInterface::load_flat.
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  const auto col_low = lp.get_col_low();
  const auto col_upp = lp.get_col_upp();

  // Charge side: static lowb migrated to u-gated row.
  auto&& load_cols = charge_load_cols(sys_lp, sim_lp);
  for (const auto& [buid, lcol] : load_cols) {
    CHECK(col_low[lcol] == doctest::Approx(0.0));
    CHECK(col_upp[lcol] == doctest::Approx(10.0));
  }
  // Discharge side: same migration.
  auto&& gen_cols = discharge_gen_cols(sys_lp, sim_lp);
  for (const auto& [buid, gcol] : gen_cols) {
    CHECK(col_low[gcol] == doctest::Approx(0.0));
    CHECK(col_upp[gcol] == doctest::Approx(10.0));
  }
}

TEST_CASE("Converter.commitment lets battery idle (load=0 below lmin)")
{
  // Economic setup: g1 supplies 50 MW at $10/MWh both blocks.
  // Battery has charge floor 5 MW, discharge floor 2 MW.  With
  // commitment binary gating, the LP can pick u_charge=u_discharge=0
  // and idle the battery (load=0, gen=0) — cheaper than paying the
  // round-trip loss on a forced 5 MW charge.
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = 100.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .pmin_charge = 5.0,
      .pmin_discharge = 2.0,
      .discharge_cost = 100.0,  // 10× pricier than g1
      .charge_cost = 100.0,  // round-trip uneconomic
      .commitment = true,
  };
  auto system = make_system(std::move(battery));
  // PlanningLP normally invokes this; we use SystemLP directly so
  // call it explicitly to materialise bat1_gen / bat1_dem / bat1_conv.
  system.expand_batteries();
  const auto simulation = make_two_block_simulation();
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  // Converter commitment emits integer u columns; pin a MIP-capable
  // solver so an ambient GTOPT_SOLVER=clp (CI) doesn't trip the LP-only
  // guard in LinearInterface::load_flat.
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  // The optimum has battery idle: load = 0, gen = 0, every block.
  // This is only possible because commitment lets the binaries
  // collapse to 0.
  const auto col_sol = lp.get_col_sol();
  auto&& load_cols = charge_load_cols(sys_lp, sim_lp);
  for (const auto& [buid, lcol] : load_cols) {
    CHECK(col_sol[lcol] == doctest::Approx(0.0).epsilon(1e-6));
  }
  auto&& gen_cols = discharge_gen_cols(sys_lp, sim_lp);
  for (const auto& [buid, gcol] : gen_cols) {
    CHECK(col_sol[gcol] == doctest::Approx(0.0).epsilon(1e-6));
  }

  // Objective check is intentionally loose: the LP picks the
  // cheapest mix between g1 dispatch and any (uneconomic) battery
  // activity.  With discharge_cost = charge_cost = 100 ≫ g1 = $10
  // the LP prefers g1; we only assert the result is finite + the
  // battery has been driven idle by the commitment binaries.
  CHECK(std::isfinite(lp.get_obj_value_raw()));
}

TEST_CASE(  // NOLINT
    "Converter.commitment reuses CommitmentLP u (no u_charge / u_discharge)")
{
  // With Battery.commitment=true AND pmin_discharge set, expand_batteries
  // synthesizes a Commitment("uc_bat1_gen") whose CommitmentLP creates
  // the per-block `status` (u_commit) columns.  ConverterLP must look
  // that CommitmentLP up by generator_sid() and NOT create its own
  // `u_discharge` cols (the status is the discharge-mode binary).  The
  // charge side keeps its own `u_charge` mode binary — sharing the
  // status would force both floors at once (see the independent-modes
  // test below).
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = 100.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .pmin_charge = 5.0,
      .pmin_discharge = 2.0,
      .discharge_cost = 1.0,
      .commitment = true,
  };
  auto system = make_system(std::move(battery));
  system.expand_batteries();

  // Sanity: a Commitment row was synthesized for bat1_gen.
  REQUIRE(system.commitment_array.size() == 1);
  CHECK(system.commitment_array.front().name == "uc_bat1_gen");

  // CommitmentLP::add_to_lp short-circuits on non-chronological
  // stages, so this test uses a chronological 1-stage 2-block sim
  // (rather than the file-level `make_two_block_simulation`, which
  // defaults `.chronological = false`).
  Simulation simulation {
      .block_array =
          {
              {.uid = Uid {1}, .duration = 1.0},
              {.uid = Uid {2}, .duration = 1.0},
          },
      .stage_array =
          {
              {.uid = Uid {1},
               .first_block = 0,
               .count_block = 2,
               .chronological = true},
          },
      .scenario_array =
          {
              {.uid = Uid {0}},
          },
  };
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  opts.lp_matrix_options.col_with_names = true;
  opts.lp_matrix_options.col_with_name_map = true;
  // MIP-capable pin — integer u columns (see above).
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);

  LpMatrixOptions flat {};
  flat.col_with_names = true;
  flat.col_with_name_map = true;
  SystemLP sys_lp(system, sim_lp, flat);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  // Inspect col names: a CommitmentLP `status` col must exist for
  // each block, and NO Converter `u_charge` / `u_discharge` cols
  // should have been added.  Match on the substring `_status_`
  // (CommitmentLP) and `_u_charge_` / `_u_discharge_` (Converter
  // fallback) without anchoring the class-name prefix, since the
  // exact col-name format depends on the LpMatrix flat options.
  const auto& col_map = lp.col_name_map();
  int n_status = 0;
  int n_u_charge = 0;
  int n_u_discharge = 0;
  for (const auto& [name, idx] : col_map) {
    (void)idx;
    if (name.contains("status")) {
      ++n_status;
    }
    if (name.contains("u_charge")) {
      ++n_u_charge;
    }
    if (name.contains("u_discharge")) {
      ++n_u_discharge;
    }
  }
  CHECK(n_status == 2);  // one per block of the 1-stage 2-block sim
  CHECK(n_u_charge == 2);  // charge mode: own binary per block
  CHECK(n_u_discharge == 0);  // discharge mode: the commitment status
}

TEST_CASE("Converter.commitment skipped when pmin/lmin = 0")  // NOLINT
{
  // When the floors are 0, the gating lower-row is omitted (col >= 0
  // × u is trivial); the upper row col ≤ pmax × u still fires but
  // the LP should solve cleanly and the unit must still be allowed
  // to dispatch up to its max.
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = 100.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      // pmin_charge / pmin_discharge unset → 0
      .discharge_cost = 1.0,
      .commitment = true,
  };
  auto system = make_system(std::move(battery));
  // PlanningLP normally invokes this; we use SystemLP directly so
  // call it explicitly to materialise bat1_gen / bat1_dem / bat1_conv.
  system.expand_batteries();
  const auto simulation = make_two_block_simulation();
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  // Converter commitment emits integer u columns; pin a MIP-capable
  // solver so an ambient GTOPT_SOLVER=clp (CI) doesn't trip the LP-only
  // guard in LinearInterface::load_flat.
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);
}

TEST_CASE(  // NOLINT
    "Battery.pmin_discharge without commitment is a hard floor")
{
  // Regression: with Battery.commitment unset, pmin_discharge was routed
  // only to the synthesised (LP-relaxed) Commitment.pmin.  The rows
  // gen ≥ 2·u, gen ≤ 10·u with continuous u ∈ [0, 1] admit any
  // gen ∈ [0, 10] (u = gen/10), so the floor vanished: the LP idled the
  // battery (gen = 0) although pmin_charge — the charge-side twin — is a
  // hard Demand.lmin floor.  Discharging is uneconomic here (cost 100 vs
  // g1 at 10), so only a hard floor makes gen ≥ 2.
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = 100.0,
      .eini = 50.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .pmin_discharge = 2.0,
      .discharge_cost = 100.0,
      // .commitment intentionally unset
  };
  auto system = make_system(std::move(battery));
  system.expand_batteries();
  const auto simulation = make_two_block_simulation();
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  const auto col_low = lp.get_col_low();
  const auto col_sol = lp.get_col_sol();
  auto&& gen_cols = discharge_gen_cols(sys_lp, sim_lp);
  REQUIRE(gen_cols.size() == 2);
  for (const auto& [buid, gcol] : gen_cols) {
    CHECK(col_low[gcol] == doctest::Approx(2.0));
    CHECK(col_sol[gcol] >= 2.0 - 1e-6);
  }
}

TEST_CASE(  // NOLINT
    "Battery.commitment: charge and discharge floors are independent modes")
{
  // Regression: with Battery.commitment = true the charge demand was
  // gated by the DISCHARGE commitment status u, so u = 1 required
  // load ≥ pmin_charge AND gen ≥ pmin_discharge in the same block.
  //
  // g1: 50 MW @ 10 $/MWh.  Demand: 40 MW (block 1), 56 MW (block 2).
  // Lossless battery (η = 1, no self-discharge, no daily-cycle scaling),
  // eini = 0 and closing efin = eini, ratings 10/10 MW, floors 6/6 MW.
  //
  // Correct optimum: block 1 charge c ∈ [6, 10] (g1 = 40 + c), block 2
  // discharge c (g1 = 56 − c ≤ 50 ⇒ c ≥ 6):
  //   obj = 10·(40 + c) + 10·(56 − c) = 960 $.
  // Shared-u bug: operating in a block needs c ≥ 6 and d ≥ 6 together,
  // which the power envelope c/10 + d/10 ≤ 1 forbids ⇒ u ≡ 0, the
  // battery idles and block 2 sheds 6 MWh at 1000 $/MWh:
  //   obj = 400 + 500 + 6000 = 6900 $.
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 1.0,
      .output_efficiency = 1.0,
      .annual_standing_loss = 0.0,
      .emin = 0.0,
      .emax = 100.0,
      .eini = 0.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .pmin_charge = 6.0,
      .pmin_discharge = 6.0,
      .daily_cycle = false,
      .commitment = true,
  };
  System system {
      .name = "IndependentModes",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = std::vector<std::vector<double>> {{40.0, 56.0}},
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 10.0,
                  .capacity = 50.0,
              },
          },
      .battery_array = {std::move(battery)},
  };
  system.expand_batteries();
  const auto simulation = make_two_block_simulation();
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  CHECK(lp.get_obj_value() == doctest::Approx(960.0).epsilon(1e-6));

  const auto col_sol = lp.get_col_sol();
  auto&& load_cols = charge_load_cols(sys_lp, sim_lp);
  auto&& gen_cols = discharge_gen_cols(sys_lp, sim_lp);
  REQUIRE(load_cols.size() == 2);
  REQUIRE(gen_cols.size() == 2);
  // Block 1: charging only; block 2: discharging only.
  CHECK(col_sol[load_cols.at(make_uid<Block>(1))] >= 6.0 - 1e-6);
  CHECK(col_sol[gen_cols.at(make_uid<Block>(1))] == doctest::Approx(0.0));
  CHECK(col_sol[load_cols.at(make_uid<Block>(2))] == doctest::Approx(0.0));
  CHECK(col_sol[gen_cols.at(make_uid<Block>(2))] >= 6.0 - 1e-6);
}

namespace
{

/// Negative-price burn case for the charge/discharge exclusivity
/// constraint.  One 1-h block, g1 at −10 $/MWh (capacity 100), demand
/// 50 MW.  Lossy battery (η_in = η_out = 0.5, no self-discharge, no
/// daily-cycle scaling), eini = 0 with the closing efin = eini row,
/// ratings 10/10 MW, commitment = true, no floors.
///
/// Closing SoC: 0.5·c − d/0.5 = 0 ⇒ c = 4d.  Simultaneous operation
/// absorbs c − d = 0.75c MW that g1 is paid (−10 $/MWh) to produce;
/// the power envelope c/10 + d/10 ≤ 1 caps it at c = 8, d = 2:
///   g1 = 56, obj = −560 $.
/// With u_charge + u_discharge ≤ 1 only one mode is on ⇒ c = 4d forces
/// c = d = 0:  g1 = 50, obj = −500 $.
struct BurnResult
{
  double obj;
  double charge;
  double discharge;
};

BurnResult solve_burn_case(OptBool exclusive)
{
  Battery battery {
      .uid = Uid {1},
      .name = "bat1",
      .bus = Uid {1},
      .input_efficiency = 0.5,
      .output_efficiency = 0.5,
      .annual_standing_loss = 0.0,
      .emin = 0.0,
      .emax = 100.0,
      .eini = 0.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .daily_cycle = false,
      .commitment = true,
      .exclusive_charge_discharge = exclusive,
  };
  System system {
      .name = "BurnCase",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {{.uid = Uid {1}, .name = "d1", .bus = Uid {1}, .lmax = 50.0}},
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = -10.0,
                  .capacity = 100.0,
              },
          },
      .battery_array = {std::move(battery)},
  };
  system.expand_batteries();
  const Simulation simulation {
      .block_array = {{.uid = Uid {1}, .duration = 1.0}},
      .stage_array = {{.uid = Uid {1}, .first_block = 0, .count_block = 1}},
      .scenario_array = {{.uid = Uid {0}}},
  };
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  opts.lp_matrix_options.solver_name = solver_test::first_mip_solver();
  PlanningOptionsLP options {opts};
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto&& lp = sys_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value() == 0);

  const auto col_sol = lp.get_col_sol();
  auto&& load_cols = charge_load_cols(sys_lp, sim_lp);
  auto&& gen_cols = discharge_gen_cols(sys_lp, sim_lp);
  REQUIRE(load_cols.size() == 1);
  REQUIRE(gen_cols.size() == 1);
  return {
      .obj = lp.get_obj_value(),
      .charge = col_sol[load_cols.begin()->second],
      .discharge = col_sol[gen_cols.begin()->second],
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "Battery.exclusive_charge_discharge forbids same-block charge+discharge")
{
  SUBCASE("unset behaves as true (default)")
  {
    const auto r = solve_burn_case({});
    CHECK(r.obj == doctest::Approx(-500.0).epsilon(1e-6));
    CHECK(r.charge == doctest::Approx(0.0));
    CHECK(r.discharge == doctest::Approx(0.0));
  }
  SUBCASE("explicit true")
  {
    const auto r = solve_burn_case(true);
    CHECK(r.obj == doctest::Approx(-500.0).epsilon(1e-6));
    CHECK(r.charge == doctest::Approx(0.0));
    CHECK(r.discharge == doctest::Approx(0.0));
  }
  SUBCASE("false restores simultaneous operation (round-trip burn)")
  {
    const auto r = solve_burn_case(false);
    CHECK(r.obj == doctest::Approx(-560.0).epsilon(1e-6));
    CHECK(r.charge == doctest::Approx(8.0));
    CHECK(r.discharge == doctest::Approx(2.0));
  }
}

TEST_CASE(  // NOLINT
    "expand_batteries propagates exclusive_charge_discharge to Converter")
{
  System system;
  system.bus_array = {{.uid = Uid {1}, .name = "b1"}};
  system.battery_array = {
      {
          .uid = Uid {1},
          .name = "bat1",
          .bus = Uid {1},
          .pmax_charge = 10.0,
          .pmax_discharge = 10.0,
          .commitment = true,
          .exclusive_charge_discharge = false,
      },
  };
  system.expand_batteries();
  REQUIRE(system.converter_array.size() == 1);
  CHECK_FALSE(
      system.converter_array.front().exclusive_charge_discharge.value_or(true));
}
