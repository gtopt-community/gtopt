// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_highs_mip_threads.cpp
 * @brief     HiGHS default thread count: all physical cores for a MIP
 *
 * HiGHS >= 1.16 runs one MIP tree-search worker per scheduler thread, so
 * the plugin's default must not cap a MIP at the small LP pool.  With no
 * user `threads`, a MIP gets physical_concurrency(); an LP keeps 4; an
 * explicit value always wins.
 */

#include <algorithm>

#include <doctest/doctest.h>
#include <gtopt/hardware_info.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

/// max x + y s.t. x + y <= 3.5, x, y in [0, 2]; @p integer makes x integer.
void add_small_model(LinearInterface& li, bool integer)
{
  const auto x = li.add_col(SparseCol {
      .uppb = 2.0,
      .cost = -1.0,
  });
  const auto y = li.add_col(SparseCol {
      .uppb = 2.0,
      .cost = -1.0,
  });
  SparseRow row;
  row[x] = 1.0;
  row[y] = 1.0;
  row.lowb = -LinearProblem::DblMax;
  row.uppb = 3.5;
  (void)li.add_row(row);
  if (integer) {
    li.set_integer(x);
  }
}

}  // namespace

TEST_CASE("HiGHS default threads: all physical cores for a MIP")  // NOLINT
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("highs")) {
    return;
  }
  const int cores = std::max(1, static_cast<int>(physical_concurrency()));

  SUBCASE("MIP without a user value uses every physical core")
  {
    LinearInterface li("highs");
    add_small_model(li, /*integer=*/true);
    REQUIRE(li.initial_solve(SolverOptions {}).has_value());
    CHECK(li.get_threads() == cores);
    CHECK(li.get_obj_value() == doctest::Approx(-3.5));
  }

  SUBCASE("LP without a user value keeps the small pool")
  {
    LinearInterface li("highs");
    add_small_model(li, /*integer=*/false);
    REQUIRE(li.initial_solve(SolverOptions {}).has_value());
    CHECK(li.get_threads() == 4);
  }

  SUBCASE("an explicit user value wins for a MIP")
  {
    LinearInterface li("highs");
    add_small_model(li, /*integer=*/true);
    REQUIRE(li.initial_solve(SolverOptions {.threads = 2}).has_value());
    CHECK(li.get_threads() == 2);
  }
}
