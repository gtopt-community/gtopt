// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_mip_time_limit_budget_e2e.cpp
 * @brief     time_limit budget end-to-end on a real (hard) MIP with a
 *            MIP start, under CPLEX
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_solve_time_budget.cpp` pins the fallback-budget logic on an
 * infeasible LP (every attempt fails instantly).  The production
 * regression was a PCP MIP that ran 3 × time_limit: the attempt that
 * exhausts the budget is a MIP that stops ON the time limit with an
 * incumbent (from the injected MIP start), not an instant failure.
 *
 * Fixture: a Cornuéjols–Dawande market-split instance (5 equality rows
 * over 40 binaries, elastic slacks penalised), whose LP relaxation is 0
 * and which no MIP solver closes in seconds.  The all-zero start (slack
 * = rhs) is feasible and is injected with `set_mip_start`.
 *
 * Asserts: total wall time ≤ time_limit + slack, and NO algorithm
 * fallback retry is started once the budget is exhausted.
 *
 * CPLEX only (skipped when the plugin is absent); never CLP — this is a
 * MIP.
 */

#include <chrono>
#include <cmath>
#include <cstdint>
#include <tuple>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] bool mtl_cplex_available()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver("cplex");
}

struct mtl_MarketSplit
{
  std::vector<double> start;  ///< raw-space feasible MIP start
  std::size_t n_bin {};
};

/// Market split: Σ_j a_ij x_j + s⁺_i − s⁻_i = ⌊Σ_j a_ij / 2⌋,
/// x binary, min Σ (s⁺ + s⁻).  a_ij ∈ [0, 99] from a fixed LCG.
[[nodiscard]] mtl_MarketSplit mtl_build_market_split(LinearInterface& li)
{
  constexpr int m = 5;
  constexpr int n = 40;
  std::uint64_t state = 0x9E3779B97F4A7C15ULL;
  const auto next = [&state]
  {
    state = (state * 6364136223846793005ULL) + 1442695040888963407ULL;
    return static_cast<double>((state >> 33U) % 100U);
  };

  std::vector<ColIndex> x;
  x.reserve(n);
  for (int j = 0; j < n; ++j) {
    const auto col = li.add_col(SparseCol {
        .uppb = 1.0,
        .cost = 0.0,
    });
    // `add_col` does not carry `is_integer` to the backend; mark it.
    li.set_integer(col);
    x.push_back(col);
  }

  mtl_MarketSplit out {
      .start = {},
      .n_bin = n,
  };
  out.start.assign(static_cast<std::size_t>(n), 0.0);
  for (int i = 0; i < m; ++i) {
    SparseRow row;
    double total = 0.0;
    for (int j = 0; j < n; ++j) {
      const double a = next();
      total += a;
      row[x[static_cast<std::size_t>(j)]] = a;
    }
    const double rhs = std::floor(total / 2.0);
    const auto sp = li.add_col(SparseCol {
        .uppb = LinearProblem::DblMax,
        .cost = 1.0,
    });
    const auto sm = li.add_col(SparseCol {
        .uppb = LinearProblem::DblMax,
        .cost = 1.0,
    });
    row[sp] = 1.0;
    row[sm] = -1.0;
    row.lowb = rhs;
    row.uppb = rhs;
    (void)li.add_row(row);  // NOLINT
    // x = 0 ⇒ s⁺ = rhs, s⁻ = 0.
    out.start.push_back(rhs);
    out.start.push_back(0.0);
  }
  return out;
}

}  // namespace

TEST_CASE(  // NOLINT
    "time_limit budget e2e — hard MIP with MIP start stops at the limit, "
    "no fallback retry after exhaustion (CPLEX)")
{
  if (!mtl_cplex_available()) {
    MESSAGE("skip: cplex plugin not available");
    return;
  }

  constexpr double time_limit = 2.0;
  // Process start-up / presolve / incumbent write-back headroom.
  constexpr double slack = 3.0;

  for (const bool initial : {true, false}) {
    CAPTURE(initial);
    LinearInterface li("cplex");
    const auto ms = mtl_build_market_split(li);
    REQUIRE(ms.start.size() == static_cast<std::size_t>(li.get_numcols()));

    SolverOptions opts;
    opts.time_limit = time_limit;
    opts.max_fallbacks = 2;
    opts.threads = 2;
    opts.log_level = 0;

    if (!initial) {
      // resolve() path: a first short solve, then re-solve with the
      // budget (the SDDP / cascade re-solve entry point).
      SolverOptions warm = opts;
      warm.time_limit = 0.2;
      // Outcome irrelevant (it stops on its own 0.2 s limit).
      std::ignore = li.initial_solve(warm);
    }

    CHECK(li.set_mip_start(ms.start, MipStartEffort::check_feasibility));

    const auto before = li.solver_stats().fallback_solves;
    const auto t0 = std::chrono::steady_clock::now();
    const auto result = initial ? li.initial_solve(opts) : li.resolve(opts);
    const double wall =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - t0)
            .count();
    const auto retries = li.solver_stats().fallback_solves - before;

    INFO("wall=",
         wall,
         "s  retries=",
         retries,
         "  has_value=",
         result.has_value(),
         "  optimal=",
         li.is_optimal());
    if (li.is_optimal()) {
      // The instance was closed inside the budget: the time-limit path
      // was not exercised on this machine — nothing to assert.
      MESSAGE(
          "market split closed within the time limit; budget path not "
          "exercised");
      continue;
    }
    CHECK(wall <= time_limit + slack);
    // Budget exhausted by the first attempt: no retry may start.
    CHECK(retries == 0);
  }
}
