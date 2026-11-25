// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_alpha_release.cpp
 * @brief     α registration releases each low-memory backend right away
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `register_alpha_variables` used to leave every (scene, phase) backend it
 * touched live until the post-initialisation release sweep, so under
 * `low_memory_mode = compress` the whole grid was resident at once (a
 * ~1.7 GiB init peak on the CEN 2y case).  It now releases each cell right
 * after adding α.  These tests pin the invariants that change must keep:
 * the α column survives release + reload at the same index with the same
 * bounds, cost and name; and a released cell solves to the same objective
 * as the `off` reference.
 */

#include <cstddef>
#include <span>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_types.hpp>
#include <gtopt/solver_options.hpp>

#include "lp_names_helpers.hpp"
#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

struct CellProbe
{
  Index ncols_before {0};
  Index ncols_after {0};
  Index alpha_col {-1};
  double alpha_low {-1.0};
  double alpha_upp {-1.0};
  double alpha_cost {-1.0};
  std::string alpha_name {};
  std::vector<std::string> col_names {};
  double obj {0.0};
  double obj_after_cycle {0.0};
  bool released_after_register {false};
};

[[nodiscard]] auto probe_cells(LowMemoryMode mode, bool names)
    -> std::vector<CellProbe>
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.sddp_options.low_memory_mode = mode;
  PlanningLP planning_lp(
      std::move(planning),
      names ? test_support::names_on_flat_opts() : LpMatrixOptions {});
  auto& sim = planning_lp.simulation();
  const auto n_scenes = sim.scene_count();
  const auto n_phases = sim.phase_count();

  std::vector<CellProbe> probes;
  for (const auto si : iota_range<SceneIndex>(0, n_scenes)) {
    for (const auto pi : iota_range<PhaseIndex>(0, n_phases)) {
      auto& sys = planning_lp.system(si, pi);
      sys.set_low_memory(mode, CompressionCodec::zstd);
      probes.push_back(CellProbe {
          .ncols_before = sys.linear_interface().get_numcols(),
      });
    }
  }

  for (const auto si : iota_range<SceneIndex>(0, n_scenes)) {
    register_alpha_variables(planning_lp, si, /*scale_alpha=*/1.0);
  }

  std::size_t k = 0;
  for (const auto si : iota_range<SceneIndex>(0, n_scenes)) {
    for (const auto pi : iota_range<PhaseIndex>(0, n_phases)) {
      auto& p = probes[k++];
      auto& sys = planning_lp.system(si, pi);
      auto& li = sys.linear_interface();
      p.released_after_register = li.is_backend_released();

      const auto* svar = find_alpha_state_var(sim, si, pi);
      REQUIRE(svar != nullptr);
      p.alpha_col = static_cast<Index>(svar->col());

      sys.ensure_lp_built();
      p.ncols_after = li.get_numcols();
      const auto ac = static_cast<std::size_t>(p.alpha_col);
      p.alpha_low = li.get_col_low_raw()[ac];
      p.alpha_upp = li.get_col_upp_raw()[ac];
      p.alpha_cost = li.get_obj_coeff()[ac];
      if (names) {
        std::vector<std::string> row_names;
        li.generate_labels_from_maps(p.col_names, row_names);
        REQUIRE(ac < p.col_names.size());
        p.alpha_name = p.col_names[ac];
      }

      auto r = li.initial_solve(SolverOptions {.log_level = 0});
      REQUIRE(r.has_value());
      REQUIRE(li.is_optimal());
      p.obj = li.get_obj_value_raw();

      // One more release → reload → resolve cycle on the same cell.
      sys.release_backend();
      sys.ensure_lp_built();
      auto r2 = li.resolve(SolverOptions {.log_level = 0});
      REQUIRE(r2.has_value());
      p.obj_after_cycle = li.get_obj_value_raw();
    }
  }
  return probes;
}

}  // namespace

TEST_CASE(  // NOLINT
    "register_alpha_variables releases compress backends and keeps alpha")
{
  for (const bool names : {false, true}) {
    CAPTURE(names);
    const auto off = probe_cells(LowMemoryMode::off, names);
    const auto cmp = probe_cells(LowMemoryMode::compress, names);
    REQUIRE(off.size() == cmp.size());
    REQUIRE_FALSE(off.empty());

    for (std::size_t i = 0; i < off.size(); ++i) {
      CAPTURE(i);
      const auto& o = off[i];
      const auto& c = cmp[i];
      // off never releases; compress is released right after α is added.
      CHECK_FALSE(o.released_after_register);
      CHECK(c.released_after_register);

      // α is one appended column, at the same index in both modes.
      CHECK(o.ncols_after == o.ncols_before + 1);
      CHECK(c.ncols_after == c.ncols_before + 1);
      CHECK(o.alpha_col == o.ncols_before);
      CHECK(c.alpha_col == o.alpha_col);
      // Bootstrap pin and pricing survive the release / reload.
      CHECK(c.alpha_low == doctest::Approx(0.0));
      CHECK(c.alpha_upp == doctest::Approx(0.0));
      CHECK(c.alpha_low == o.alpha_low);
      CHECK(c.alpha_upp == o.alpha_upp);
      CHECK(c.alpha_cost == doctest::Approx(o.alpha_cost));
      // Names (when enabled) are stable across modes and name α.
      CHECK(c.col_names == o.col_names);
      CHECK(c.alpha_name == o.alpha_name);
      if (names) {
        CHECK(c.alpha_name.find("alpha") != std::string::npos);
      }
      // A released-and-reloaded cell solves to the off objective, and an
      // extra release / reload cycle does not move it.
      CHECK(c.obj == doctest::Approx(o.obj).epsilon(1e-9));
      CHECK(c.obj_after_cycle == doctest::Approx(o.obj).epsilon(1e-9));
      CHECK(o.obj_after_cycle == doctest::Approx(o.obj).epsilon(1e-9));
    }
  }
}

TEST_CASE(  // NOLINT
    "register_alpha_variables is idempotent after a release")
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.sddp_options.low_memory_mode = LowMemoryMode::compress;
  PlanningLP planning_lp(std::move(planning));
  for (const auto si :
       iota_range<SceneIndex>(0, planning_lp.simulation().scene_count()))
  {
    for (const auto pi :
         iota_range<PhaseIndex>(0, planning_lp.simulation().phase_count()))
    {
      planning_lp.system(si, pi).set_low_memory(LowMemoryMode::compress);
    }
  }
  const auto s0 = first_scene_index();
  const auto p0 = first_phase_index();
  register_alpha_variables(planning_lp, s0, 1.0);
  auto& li = planning_lp.system(s0, p0).linear_interface();
  const auto ncols = li.get_numcols();
  // A second registration finds α already present: no new column, and the
  // cell is left released.
  register_alpha_variables(planning_lp, s0, 1.0);
  CHECK(li.get_numcols() == ncols);
  CHECK(li.is_backend_released());
}
