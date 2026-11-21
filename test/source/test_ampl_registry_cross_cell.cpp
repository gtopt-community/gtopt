// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_ampl_registry_cross_cell.cpp
 * @brief     Cross-cell AMPL registry queries are thread-safe and survive
 *            `release_ampl_cell`.
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Regression for an SDDP write-out SIGSEGV
 * (`stamp_ref` <- `resolve_col_to_row` <- `UserConstraintLP::build_coarse_row`
 * <- `SystemLP::rebuild_collections_if_needed` <- `write_out_cell`).
 *
 * The user-constraint silent-zero checks
 * (`find_ampl_variable_for_element` / `find_ampl_class_attribute`) used to
 * walk EVERY (scene, phase) cell's variable map.  Under the parallel
 * write-out each cell's rebuild replays its own registry
 * (`insert_or_assign`), so one thread iterated an `unordered_map` another
 * thread was rehashing.  Any dormant reference (an element whose column
 * is absent in a phase, e.g. `generator(...).capainst` of a candidate
 * before its build stage) took that path.  The checks are now answered
 * from a mutex-guarded cross-cell index.
 */

#include <atomic>
#include <format>
#include <string>
#include <thread>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation.hpp>
#include <gtopt/simulation_lp.hpp>

using namespace gtopt;

namespace ampl_cross_cell_test
{
namespace
{

constexpr int num_phases = 8;

[[nodiscard]] auto make_simulation() -> Simulation
{
  Simulation sim;
  for (int k = 0; k < num_phases; ++k) {
    sim.block_array.push_back({
        .uid = Uid {k + 1},
        .duration = 1,
    });
    sim.stage_array.push_back({
        .uid = Uid {k + 1},
        .first_block = static_cast<Size>(k),
        .count_block = 1,
    });
    sim.phase_array.push_back({
        .uid = Uid {k + 1},
        .first_stage = static_cast<Size>(k),
        .count_stage = 1,
    });
  }
  sim.scenario_array = {
      {
          .uid = Uid {1},
          .probability_factor = 1.0,
      },
  };
  return sim;
}

}  // namespace
}  // namespace ampl_cross_cell_test

TEST_CASE(  // NOLINT
    "SimulationLP - cross-cell attribute index survives release_ampl_cell")
{
  using namespace ampl_cross_cell_test;
  const auto sim = make_simulation();
  const PlanningOptions popts;
  const PlanningOptionsLP options(popts);
  SimulationLP sim_lp(sim, options);
  sim_lp.set_need_ampl_variables(true);

  sim_lp.add_ampl_variable(SceneIndex {0},
                           PhaseIndex {3},
                           "generator",
                           Uid {7},
                           "capainst",
                           make_uid<Scenario>(1),
                           make_uid<Stage>(4),
                           ColIndex {11});

  CHECK(
      sim_lp.find_ampl_variable_for_element("generator", Uid {7}, "capainst"));
  CHECK(sim_lp.find_ampl_class_attribute("generator", "capainst"));
  CHECK_FALSE(
      sim_lp.find_ampl_variable_for_element("generator", Uid {8}, "capainst"));
  CHECK_FALSE(sim_lp.find_ampl_class_attribute("generator", "capacost"));
  CHECK_FALSE(sim_lp.find_ampl_class_attribute("battery", "capainst"));

  // Releasing the owning cell (production path after flatten) must not
  // make a later phase's dormant reference look like a typo.
  sim_lp.release_ampl_cell(SceneIndex {0}, PhaseIndex {3});
  CHECK(
      sim_lp.find_ampl_variable_for_element("generator", Uid {7}, "capainst"));
  CHECK(sim_lp.find_ampl_class_attribute("generator", "capainst"));
}

TEST_CASE(  // NOLINT
    "SimulationLP - cross-cell queries race-free against per-cell replay")
{
  using namespace ampl_cross_cell_test;
  const auto sim = make_simulation();
  const PlanningOptions popts;
  const PlanningOptionsLP options(popts);
  SimulationLP sim_lp(sim, options);
  sim_lp.set_need_ampl_variables(true);

  // Writers: one thread per (scene, phase) cell, each repeatedly
  // releasing and re-registering its own cell (what the parallel
  // write-out rebuild does).  Readers: the user-constraint silent-zero
  // checks running concurrently from other cells.  Before the fix the
  // readers iterated the writers' unordered_maps mid-rehash.
  constexpr int rounds = 40;
  constexpr int vars_per_round = 300;
  std::atomic<bool> stop {false};

  {
    std::vector<std::jthread> readers;
    readers.reserve(4);
    for (int r = 0; r < 4; ++r) {
      readers.emplace_back(
          [&]
          {
            while (!stop.load(std::memory_order_relaxed)) {
              (void)sim_lp.find_ampl_class_attribute("generator", "capainst");
              (void)sim_lp.find_ampl_variable_for_element(
                  "generator", Uid {999999}, "capainst");
            }
          });
    }

    std::vector<std::jthread> writers;
    writers.reserve(num_phases);
    for (int p = 0; p < num_phases; ++p) {
      writers.emplace_back(
          [&, p]
          {
            for (int round = 0; round < rounds; ++round) {
              sim_lp.release_ampl_cell(SceneIndex {0}, PhaseIndex {p});
              for (int v = 0; v < vars_per_round; ++v) {
                sim_lp.add_ampl_variable(SceneIndex {0},
                                         PhaseIndex {p},
                                         "generator",
                                         Uid {(p * vars_per_round) + v + 1},
                                         "capainst",
                                         make_uid<Scenario>(1),
                                         make_uid<Stage>(p + 1),
                                         ColIndex {v});
              }
            }
          });
    }
    writers.clear();  // join writers
    stop.store(true, std::memory_order_relaxed);
  }  // join readers

  CHECK(sim_lp.find_ampl_class_attribute("generator", "capainst"));
  CHECK(sim_lp.find_ampl_variable_for_element(
      "generator", Uid {num_phases * vars_per_round}, "capainst"));
}
