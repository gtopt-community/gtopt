/**
 * @file      test_solve_time_budget.cpp
 * @brief     `solver_options.time_limit` is a budget for the whole solve
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Regression: the algorithm fallback cycle re-ran a non-optimal solve up to
 * `max_fallbacks` more times, each with the FULL `time_limit`, so a PCP MIP
 * with time_limit=3600 ran for ~3 h (3 attempts that all hit the limit).
 * The budget now covers every attempt; retries get the remaining time and
 * are skipped once less than `min_retry_time_slice_s` is left.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <optional>

#include <doctest/doctest.h>
#include <gtopt/error.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_options.hpp>

using namespace gtopt;

TEST_CASE("remaining_time_limit")
{
  CHECK_FALSE(remaining_time_limit(std::nullopt, 10.0).has_value());
  CHECK_FALSE(remaining_time_limit(0.0, 10.0).has_value());
  CHECK_FALSE(remaining_time_limit(-1.0, 10.0).has_value());
  CHECK(remaining_time_limit(3600.0, 600.0).value_or(-1.0)
        == doctest::Approx(3000.0));
  CHECK(remaining_time_limit(3600.0, 3700.0).value_or(1.0)
        == doctest::Approx(-100.0));
  static_assert(remaining_time_limit(10.0, 4.0).value_or(0.0) == 6.0);
}

TEST_CASE("time_limit budget bounds the algorithm fallback cycle")
{
  // Infeasible LP (x <= 5, x >= 10): every attempt is non-optimal, so the
  // fallback cycle runs as long as the budget allows.
  LinearInterface li;
  const auto x1 = li.add_col(SparseCol {
      .uppb = 5.0,
      .cost = 1.0,
  });
  SparseRow row;
  row[x1] = 1.0;
  row.lowb = 10.0;
  row.uppb = LinearProblem::DblMax;
  (void)li.add_row(row);  // NOLINT

  SolverOptions opts;
  opts.algorithm = LPAlgo::dual;
  opts.log_level = 0;
  opts.max_fallbacks = 2;

  const auto run = [&](bool initial)
  {
    const auto before = li.solver_stats().fallback_solves;
    auto result = initial ? li.initial_solve(opts) : li.resolve(opts);
    REQUIRE_FALSE(result.has_value());
    CHECK(result.error().code == ErrorCode::SolverError);
    return li.solver_stats().fallback_solves - before;
  };

  SUBCASE("no time_limit: full fallback cycle")
  {
    CHECK(run(true) == 2);
    CHECK(run(false) == 2);
  }

  SUBCASE("ample time_limit: fallbacks run on the remaining budget")
  {
    opts.time_limit = 3600.0;
    CHECK(run(true) == 2);
    CHECK(run(false) == 2);
  }

  SUBCASE("budget below the minimum retry slice: no fallback attempted")
  {
    // The first attempt gets the whole (short) budget; afterwards less
    // than `min_retry_time_slice_s` is left, so no retry is started and
    // the first attempt's status is reported.
    opts.time_limit = 0.5 * min_retry_time_slice_s;
    CHECK(run(true) == 0);
    CHECK(run(false) == 0);
  }
}
