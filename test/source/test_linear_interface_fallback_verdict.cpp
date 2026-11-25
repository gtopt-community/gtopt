// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_linear_interface_fallback_verdict.cpp
 * @brief     The algorithm-fallback cycle keeps a proof of infeasibility
 *
 * An LP proven infeasible by the first algorithm is still sent through the
 * fallback cycle (dual → primal → barrier, presolve off).  A later attempt
 * that merely gives up must not overwrite the proof: HiGHS 1.15's IPX
 * without presolve fails ("IPM failed", kError) on such an LP, which used
 * to surface as an abandoned solve (status 1) instead of infeasible
 * (status 2) and turned SDDP aperture drops into "solver failures".
 */

#include <string>

#include <doctest/doctest.h>
#include <gtopt/error.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

/// x <= 5 and x >= 10: primal infeasible.
void add_infeasible_lp(LinearInterface& li)
{
  const auto x1 = li.add_col(SparseCol {
      .uppb = 5.0,
      .cost = 1.0,
  });
  SparseRow row;
  row[x1] = 1.0;
  row.lowb = 10.0;
  row.uppb = LinearProblem::DblMax;
  (void)li.add_row(row);
}

}  // namespace

TEST_CASE(  // NOLINT
    "LinearInterface - fallback cycle keeps a proven infeasibility")
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();

  // LP-only backends (CLP is fine for a pure LP).
  for (const auto* solver : {"highs", "cplex", "clp"}) {
    if (!reg.has_solver(solver)) {
      continue;
    }
    CAPTURE(solver);
    for (const auto algo : {LPAlgo::dual, LPAlgo::primal, LPAlgo::barrier}) {
      CAPTURE(algo);
      const SolverOptions opts {
          .algorithm = algo,
          .log_level = 0,
          .max_fallbacks = 2,
      };

      {
        LinearInterface li(solver);
        add_infeasible_lp(li);
        const auto result = li.initial_solve(opts);
        REQUIRE_FALSE(result.has_value());
        CHECK(result.error().code == ErrorCode::SolverError);
        CHECK(result.error().status == 2);
        CHECK(li.get_status() == 2);
      }
      {
        LinearInterface li(solver);
        add_infeasible_lp(li);
        const auto result = li.resolve(opts);
        REQUIRE_FALSE(result.has_value());
        CHECK(result.error().code == ErrorCode::SolverError);
        CHECK(result.error().status == 2);
        CHECK(li.get_status() == 2);
      }
    }
  }
}
