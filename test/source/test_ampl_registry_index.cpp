// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_ampl_registry_index.cpp
 * @brief     Thread-safe "ever registered" AMPL index (silent-zero checks)
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `find_ampl_variable_for_element` / `find_ampl_class_attribute` used to
 * scan EVERY (scene, phase) cell map while the other scene-build threads
 * were inserting into (and releasing) their own cells — a data race that
 * segfaulted the plp_2y irrigation build inside `stamp_ref`.  They now
 * read a mutex-guarded index fed by `add_ampl_variable`.
 */

#include <atomic>
#include <thread>

#include <doctest/doctest.h>
#include <gtopt/simulation_lp.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Simulation make_index_sim()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1,
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
}

}  // namespace

TEST_CASE(  // NOLINT
    "AMPL registry index - survives release_ampl_cell and is exact")
{
  const auto sim = make_index_sim();
  const PlanningOptionsLP options;
  SimulationLP sim_lp(sim, options);
  sim_lp.set_need_ampl_variables(/*v=*/true);

  sim_lp.add_ampl_variable(SceneIndex {0},
                           PhaseIndex {0},
                           "volume_right",
                           Uid {7},
                           "saving",
                           make_uid<Scenario>(1),
                           make_uid<Stage>(1),
                           ColIndex {3});
  sim_lp.release_ampl_cell(SceneIndex {0}, PhaseIndex {0});

  CHECK(
      sim_lp.find_ampl_variable_for_element("volume_right", Uid {7}, "saving"));
  CHECK_FALSE(
      sim_lp.find_ampl_variable_for_element("volume_right", Uid {8}, "saving"));
  CHECK_FALSE(
      sim_lp.find_ampl_variable_for_element("volume_right", Uid {7}, "sav"));
  CHECK(sim_lp.find_ampl_class_attribute("volume_right", "saving"));
  CHECK_FALSE(sim_lp.find_ampl_class_attribute("flow_right", "saving"));
}

TEST_CASE(  // NOLINT
    "AMPL registry index - concurrent register and lookup do not race")
{
  const auto sim = make_index_sim();
  const PlanningOptionsLP options;
  SimulationLP sim_lp(sim, options);
  sim_lp.set_need_ampl_variables(/*v=*/true);

  constexpr int kN = 20000;
  std::atomic<bool> done {false};
  std::thread writer(
      [&]
      {
        for (int i = 0; i < kN; ++i) {
          sim_lp.add_ampl_variable(SceneIndex {0},
                                   PhaseIndex {0},
                                   "waterway",
                                   Uid {i},
                                   "flow",
                                   make_uid<Scenario>(1),
                                   make_uid<Stage>(1),
                                   ColIndex {i});
          if (i % 4096 == 0) {
            sim_lp.release_ampl_cell(SceneIndex {0}, PhaseIndex {0});
          }
        }
        done = true;
      });
  int hits = 0;
  while (!done) {
    hits += sim_lp.find_ampl_variable_for_element("waterway", Uid {0}, "flow")
        ? 1
        : 0;
    std::ignore = sim_lp.find_ampl_class_attribute("waterway", "flow");
  }
  writer.join();
  CHECK(hits >= 0);
  CHECK(
      sim_lp.find_ampl_variable_for_element("waterway", Uid {kN - 1}, "flow"));
}
