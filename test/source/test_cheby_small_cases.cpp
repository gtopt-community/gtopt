// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_cheby_small_cases.cpp
 * @brief     Regression of the small Chebyshev case library
 *            (cases/cheby_small, block and point versions) with HiGHS
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Objectives: hand / closed form where noted in each case README,
 * regression otherwise.  Skipped when the HiGHS plugin is not loaded.
 */

#include <array>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace cheby_small_test
{
namespace
{

[[nodiscard]] bool highs_available()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver("highs");
}

[[nodiscard]] std::filesystem::path library_dir()
{
  return std::filesystem::path(__FILE__)
             .parent_path()
             .parent_path()
             .parent_path()
      / "cases" / "cheby_small";
}

[[nodiscard]] double solve_case(const std::string& rel)
{
  const auto path = library_dir() / rel;
  REQUIRE(std::filesystem::exists(path));
  std::ifstream in(path);
  std::stringstream buf;
  buf << in.rdbuf();
  Planning base;
  base.merge(parse_planning_json(buf.str()));
  base.options.lp_matrix_options.solver_name = "highs";
  const auto out = std::filesystem::temp_directory_path()
      / ("gtopt_cheby_small_" + path.stem().string());
  base.options.output_directory = out.string();
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  return plp.systems().front().front().linear_interface().get_obj_value();
}

struct Expected
{
  const char* json;
  double obj;
  double rel_tol;
};

}  // namespace
}  // namespace cheby_small_test

TEST_CASE(  // NOLINT
    "cheby_small library - block and point objectives")
{
  using namespace cheby_small_test;
  if (!highs_available()) {
    MESSAGE("Skipping cheby_small library — HiGHS plugin not loaded");
    return;
  }
  // objectives: PLP reference (caso2emb), by hand (caso1emb
  // block), closed form (congested_line point), regression otherwise;
  // uc_minup_ramp point is a MIP (looser tolerance)
  const std::array cases {
      Expected {.json = "caso2emb/caso2emb_block.json",
                .obj = 135000.0,
                .rel_tol = 1e-6},
      Expected {.json = "caso2emb/caso2emb_point.json",
                .obj = 135000.0,
                .rel_tol = 1e-6},
      Expected {.json = "caso1emb/caso1emb_block.json",
                .obj = 252000.0,
                .rel_tol = 1e-6},
      Expected {.json = "caso1emb/caso1emb_point.json",
                .obj = 247143.292205529,
                .rel_tol = 1e-6},
      Expected {.json = "losses/losses_block.json",
                .obj = 626454.116041310,
                .rel_tol = 1e-6},
      Expected {.json = "losses/losses_point.json",
                .obj = 626454.116041310,
                .rel_tol = 1e-6},
      Expected {.json = "system2_week/system2_week_block.json",
                .obj = 48407753.8832999,
                .rel_tol = 1e-6},
      Expected {.json = "system2_week/system2_week_point.json",
                .obj = 46675189.8127262,
                .rel_tol = 1e-6},
      Expected {.json = "mini/battery_ramp/battery_ramp_block.json",
                .obj = 7260.0,
                .rel_tol = 1e-6},
      Expected {.json = "mini/hydro/hydro_block.json",
                .obj = 27400.0,
                .rel_tol = 1e-6},
      Expected {.json = "mini/hydro/hydro_point.json",
                .obj = 27400.0,
                .rel_tol = 1e-6},
      Expected {.json = "mini/congested_line/congested_line_block.json",
                .obj = 46000.0,
                .rel_tol = 1e-6},
      Expected {.json = "mini/congested_line/congested_line_point.json",
                .obj = 46008.0745048467,
                .rel_tol = 1e-6},
      Expected {.json = "mini/uc_minup_ramp/uc_minup_ramp_block.json",
                .obj = 31500.0,
                .rel_tol = 1e-6},
      Expected {.json = "mini/uc_minup_ramp/uc_minup_ramp_point.json",
                .obj = 33065.9545765050,
                .rel_tol = 1e-4},
      Expected {.json = "mini/periodic_week/periodic_week_block.json",
                .obj = 168000.0,
                .rel_tol = 1e-6},
      Expected {.json = "mini/periodic_week/periodic_week_point.json",
                .obj = 168000.0,
                .rel_tol = 1e-6},
  };
  for (const auto& c : cases) {
    CAPTURE(c.json);
    CHECK(solve_case(c.json) == doctest::Approx(c.obj).epsilon(c.rel_tol));
  }
}

TEST_CASE(  // NOLINT
    "cheby_small library - battery_ramp point (binding emax)")
{
  using namespace cheby_small_test;
  if (!highs_available()) {
    MESSAGE("Skipping cheby_small library — HiGHS plugin not loaded");
    return;
  }
  // emax binds on the point stage; the converter's cc rule and lg4_pos
  // (with its envelope rows) give the same optimum
  const double obj = solve_case("mini/battery_ramp/battery_ramp_point.json");
  CHECK(obj == doctest::Approx(7241.46825689373).epsilon(1e-6));
  CHECK(obj <= 7260.0);
}
