// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_mip_root_options.cpp
 * @brief     MIP root-LP / IPM / heuristic solver options on every value
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `mip_lp_algorithm` (HiGHS `mip_lp_solver`, CPLEX `CPX_PARAM_STARTALG`),
 * `barrier_solver` (HiGHS `mip_ipm_solver`) and `mip_heuristic_effort`
 * (HiGHS) only steer HOW a MIP is solved: every value must give the same
 * proven optimum, and an LP must ignore them.
 *
 * MIP:  max 5a + 4b + 3c + 0.5d  s.t.  2a + 3b + c + d <= 5.5,
 *       4a + b + 2c <= 11,  3a + 4b + 2c <= 8,  a, b, c in Z_+, d in [0, 1].
 * Optimum a = 2, b = 0, c = 1, d = 0.5:  objective 13.25.
 */

#include <array>
#include <optional>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] bool have_mip_solver(const std::string& name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver(name);
}

void add_root_option_mip(LinearInterface& li, bool integer)
{
  const std::array<double, 4> cost {
      -5.0,
      -4.0,
      -3.0,
      -0.5,
  };
  std::array<ColIndex, 4> cols {};
  for (std::size_t j = 0; j < cols.size(); ++j) {
    cols[j] = li.add_col(SparseCol {
        .uppb = j == 3 ? 1.0 : 10.0,
        .cost = cost[j],
    });
  }
  const auto add = [&](std::array<double, 4> coef, double ub)
  {
    SparseRow row;
    for (std::size_t j = 0; j < cols.size(); ++j) {
      if (coef[j] != 0.0) {
        row[cols[j]] = coef[j];
      }
    }
    row.lowb = -LinearProblem::DblMax;
    row.uppb = ub;
    (void)li.add_row(row);
  };
  add({2.0, 3.0, 1.0, 1.0}, 5.5);
  add({4.0, 1.0, 2.0, 0.0}, 11.0);
  add({3.0, 4.0, 2.0, 0.0}, 8.0);
  if (integer) {
    for (std::size_t j = 0; j < 3; ++j) {
      li.set_integer(cols[j]);
    }
  }
}

void check_mip_root_options(const std::string& solver)
{
  if (!have_mip_solver(solver)) {
    return;
  }
  const std::array<std::optional<LPAlgo>, 5> root_algos {
      std::nullopt,
      LPAlgo::default_algo,
      LPAlgo::primal,
      LPAlgo::dual,
      LPAlgo::barrier,
  };
  const std::array<std::optional<BarrierSolver>, 4> ipms {
      std::nullopt,
      BarrierSolver::automatic,
      BarrierSolver::ipx,
      BarrierSolver::hipo,
  };
  for (const auto& root : root_algos) {
    for (const auto& ipm : ipms) {
      CAPTURE(root.has_value() ? static_cast<int>(*root) : -1);
      CAPTURE(ipm.has_value() ? static_cast<int>(*ipm) : -1);
      LinearInterface li(solver);
      add_root_option_mip(li, /*integer=*/true);
      const auto res = li.initial_solve(SolverOptions {
          .threads = 2,
          .barrier_solver = ipm,
          .mip_lp_algorithm = root,
      });
      REQUIRE(res.has_value());
      CHECK(li.get_obj_value() == doctest::Approx(-13.25));
    }
  }
}

}  // namespace

TEST_CASE(
    "mip_lp_algorithm x barrier_solver: same MIP optimum, HiGHS")  // NOLINT
{
  check_mip_root_options("highs");
}

TEST_CASE(
    "mip_lp_algorithm x barrier_solver: same MIP optimum, CPLEX")  // NOLINT
{
  check_mip_root_options("cplex");
}

TEST_CASE("mip_heuristic_effort: every value keeps the MIP optimum")  // NOLINT
{
  for (const std::string solver : {"highs", "cplex"}) {
    if (!have_mip_solver(solver)) {
      continue;
    }
    for (const std::optional<double> effort :
         {
             std::optional<double> {},
             std::optional<double> {0.0},
             std::optional<double> {0.3},
             std::optional<double> {1.0},
             std::optional<double> {5.0},  // clamped to 1 (HiGHS)
         })
    {
      CAPTURE(solver);
      CAPTURE(effort.value_or(-1.0));
      LinearInterface li(solver);
      add_root_option_mip(li, /*integer=*/true);
      const auto res = li.initial_solve(SolverOptions {
          .threads = 2,
          .mip_heuristic_effort = effort,
      });
      REQUIRE(res.has_value());
      CHECK(li.get_obj_value() == doctest::Approx(-13.25));
    }
  }
}

TEST_CASE("MIP root options are ignored by an LP")  // NOLINT
{
  for (const std::string solver : {"highs", "cplex"}) {
    if (!have_mip_solver(solver)) {
      continue;
    }
    CAPTURE(solver);
    LinearInterface li(solver);
    add_root_option_mip(li, /*integer=*/false);
    const auto res = li.initial_solve(SolverOptions {
        .algorithm = LPAlgo::dual,
        .threads = 2,
        .barrier_solver = BarrierSolver::ipx,
        .mip_lp_algorithm = LPAlgo::barrier,
        .mip_heuristic_effort = 1.0,
    });
    REQUIRE(res.has_value());
    // The relaxation solves as a plain LP: at least as good as the MIP.
    CHECK(li.get_obj_value() <= -13.25 + 1e-9);
  }
}
