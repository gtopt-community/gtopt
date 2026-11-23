// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_cut_parquet_orphans.cpp
 * @brief     save_cuts_parquet with cuts that cannot be resolved
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A cut whose phase is unknown is written with empty `cls` / `var`
 * placeholders so the file keeps its structure and the loader skips it;
 * a cut in a known phase that references a column that is not a state
 * variable is a hard save error (the file would otherwise carry an
 * unloadable coefficient).  Both paths sit outside the round-trip tests.
 */

#include <filesystem>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_cut_io.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

TEST_CASE("save_cuts_parquet handles unresolvable cuts")  // NOLINT
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  SDDPOptions sddp_opts;
  sddp_opts.max_iterations = 2;
  sddp_opts.convergence_tol = 1e-6;
  SDDPMethod sddp(planning_lp, sddp_opts);
  REQUIRE(sddp.solve().has_value());

  const auto& stored = sddp.stored_cuts();
  REQUIRE_FALSE(stored.empty());
  REQUIRE_FALSE(stored.front().coefficients.empty());

  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_test_cut_parquet_orphans";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  SUBCASE("unknown phase: placeholders written, loader skips the cut")
  {
    std::vector<StoredCut> cuts {stored.front()};
    cuts.front().phase_uid = make_uid<Phase>(9999);
    const auto file = (dir / "orphan_phase.parquet").string();
    REQUIRE(save_cuts_parquet(cuts, planning_lp, file).has_value());
    CHECK(std::filesystem::exists(file));

    auto planning2 = make_3phase_hydro_planning();
    PlanningLP planning_lp2(std::move(planning2));
    const LabelMaker lm {LpNamesLevel::all};
    const auto loaded =
        load_cuts_parquet(planning_lp2, file, /*scale_alpha=*/1.0, lm);
    // Either the loader reports an error or it loads nothing — never a
    // cut wired to the wrong phase.
    if (loaded.has_value()) {
      CHECK(loaded->count == 0);
    }
  }

  SUBCASE("known phase, column that is not a state variable: save fails")
  {
    std::vector<StoredCut> cuts {stored.front()};
    cuts.front().coefficients.emplace_back(ColIndex {987654}, 1.0);
    const auto file = (dir / "orphan_col.parquet").string();
    const auto res = save_cuts_parquet(cuts, planning_lp, file);
    REQUIRE_FALSE(res.has_value());
    CHECK(res.error().message.find("not a registered state variable")
          != std::string::npos);
  }

  std::filesystem::remove_all(dir);
}
