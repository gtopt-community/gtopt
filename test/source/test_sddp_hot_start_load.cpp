// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_hot_start_load.cpp
 * @brief     Hot start from a per-iteration cut file loads N > 0 cuts
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The irrigation-agreement review ran a simulation with
 * `cuts_input_file = <dir>/sddp_cuts_N.parquet` and got
 * "SDDP HotStart: cuts=cold" with no reason.  Root cause: the CLI forces
 * `recovery_mode = none` unless `--recover` is passed, and
 * `initialize_solver` then skipped `cuts_input_file` silently.  These
 * tests pin (1) that the per-iteration versioned file IS a valid hot
 * start (N > 0 cuts, iteration offset advanced) and (2) the documented
 * gate: with recovery_mode = none the file is not loaded (now with a
 * WARN carrying the reason).
 */

#include <algorithm>
#include <filesystem>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

/// Train 3 iterations writing cuts under `dir`; return the latest
/// per-iteration versioned cut file (sddp_cuts_<iter>.parquet).
[[nodiscard]] std::filesystem::path train_and_get_versioned(
    const std::filesystem::path& dir)
{
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  {
    auto planning = make_3phase_hydro_planning();
    PlanningLP plp(std::move(planning));
    SDDPOptions opts;
    opts.max_iterations = 3;
    opts.convergence_tol = 1e-9;
    opts.enable_api = false;
    opts.cuts_output_file = (dir / "sddp_cuts.parquet").string();
    SDDPMethod sddp(plp, opts);
    REQUIRE(sddp.solve().has_value());
    REQUIRE(sddp.num_stored_cuts() > 0);
  }
  std::filesystem::path latest;
  for (const auto& entry : std::filesystem::directory_iterator(dir)) {
    const auto name = entry.path().filename().string();
    if (name.starts_with("sddp_cuts_") && name.ends_with(".parquet")) {
      if (latest.empty() || name > latest.filename().string()) {
        latest = entry.path();
      }
    }
  }
  REQUIRE_FALSE(latest.empty());
  return latest;
}

[[nodiscard]] std::ptrdiff_t load_into_fresh_solver(
    const std::filesystem::path& file, RecoveryMode mode)
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP plp(std::move(planning));
  SDDPOptions opts;
  opts.max_iterations = 1;
  opts.enable_api = false;
  opts.cuts_input_file = file.string();
  opts.recovery_mode = mode;
  SDDPMethod sddp(plp, opts);
  REQUIRE(sddp.ensure_initialized().has_value());
  if (mode >= RecoveryMode::cuts) {
    CHECK(sddp.iteration_offset() > IterationIndex {0});
  }
  return sddp.num_stored_cuts();
}

}  // namespace

TEST_CASE(  // NOLINT
    "SDDP hot start - per-iteration versioned cut file loads N > 0 cuts")
{
  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_test_hot_start_load";
  const auto versioned = train_and_get_versioned(dir);

  SUBCASE("recovery_mode = cuts loads the cuts")
  {
    CHECK(load_into_fresh_solver(versioned, RecoveryMode::cuts) > 0);
  }

  SUBCASE("recovery_mode = none (no --recover) leaves the solver cold")
  {
    CHECK(load_into_fresh_solver(versioned, RecoveryMode::none) == 0);
  }

  std::filesystem::remove_all(dir);
}
