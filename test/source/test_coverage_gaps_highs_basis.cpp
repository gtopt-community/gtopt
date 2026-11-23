// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_coverage_gaps_highs_basis.cpp
 * @brief     HiGHS plugin get_basis / set_basis and basis-status mapping
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Coverage audit 2026-11-22: `HighsSolverBackend::get_basis`,
 * `from_highs_status` (highs_solver_backend.cpp 693-763) had zero hits
 * — only the MindOpt plugin had a basis round-trip test.  The basis
 * hand-off is what the SDDP cross-basis warm start relies on, so a
 * wrong status mapping would silently turn every warm start into a
 * cold one (or a wrong vertex).
 *
 * Every TEST_CASE silently skips when the HiGHS plugin is absent.
 */

#include <array>
#include <memory>

#include <doctest/doctest.h>
#include <gtopt/basis.hpp>
#include <gtopt/solver_backend.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace test_coverage_gaps_highs_basis_ns
{
namespace
{

[[nodiscard]] std::unique_ptr<SolverBackend> make_highs_or_null()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("highs")) {
    return nullptr;
  }
  return reg.create("highs");
}

// max x + y  (as min −x − y)
//   s.t.  x + 2y <= 4      (row 0, binding)
//         x       <= 3     (column upper bound, binding → x at_upper)
//         y >= 0
// Optimum: x = 3, y = 0.5, obj = −3.5; x non-basic at its upper bound,
// y basic, row 0 non-basic (slack at bound).
struct UpperBoundLP
{
  static constexpr int ncols = 2;
  static constexpr int nrows = 1;
  std::array<int, 3> matbeg {
      0,
      1,
      2,
  };
  std::array<int, 2> matind {
      0,
      0,
  };
  std::array<double, 2> matval {
      1.0,
      2.0,
  };
  std::array<double, 2> collb {
      0.0,
      0.0,
  };
  std::array<double, 2> colub {
      3.0,
      0.0,
  };
  std::array<double, 2> obj {
      -1.0,
      -1.0,
  };
  std::array<double, 1> rowlb {0.0};
  std::array<double, 1> rowub {4.0};

  explicit UpperBoundLP(double inf)
  {
    colub[1] = inf;
    rowlb[0] = -inf;
  }

  void load_into(SolverBackend& b) const
  {
    b.load_problem(ncols,
                   nrows,
                   matbeg.data(),
                   matind.data(),
                   matval.data(),
                   collb.data(),
                   colub.data(),
                   obj.data(),
                   rowlb.data(),
                   rowub.data());
  }
};

}  // namespace
}  // namespace test_coverage_gaps_highs_basis_ns

using test_coverage_gaps_highs_basis_ns::make_highs_or_null;
using test_coverage_gaps_highs_basis_ns::UpperBoundLP;

TEST_CASE("HiGHS get_basis is empty before any solve")
{
  auto backend = make_highs_or_null();
  if (!backend) {
    return;
  }
  const UpperBoundLP lp {backend->infinity()};
  lp.load_into(*backend);
  CHECK_FALSE(backend->get_basis().has_value());
}

TEST_CASE("HiGHS get_basis maps basic / at_upper statuses and round-trips")
{
  auto backend = make_highs_or_null();
  if (!backend) {
    return;
  }
  const UpperBoundLP lp {backend->infinity()};
  lp.load_into(*backend);

  SolverOptions opts;
  opts.algorithm = LPAlgo::dual;
  backend->apply_options(opts);
  backend->initial_solve();
  REQUIRE(backend->is_proven_optimal());
  CHECK(backend->obj_value() == doctest::Approx(-3.5));

  const auto opt_basis = backend->get_basis();
  REQUIRE(opt_basis.has_value());
  const Basis basis = opt_basis.value_or(Basis {});
  REQUIRE(basis.num_cols() == 2);
  REQUIRE(basis.num_rows() == 1);

  // x sits at its finite upper bound, y is basic: one basic per row.
  CHECK(basis.col_status[0] == BasisStatus::at_upper);
  CHECK(basis.col_status[1] == BasisStatus::basic);
  CHECK(basis.row_status[0] != BasisStatus::basic);

  SUBCASE("installing the basis in a fresh backend reproduces the optimum")
  {
    auto warm = make_highs_or_null();
    REQUIRE(warm);
    const UpperBoundLP lp2 {warm->infinity()};
    lp2.load_into(*warm);
    REQUIRE(warm->set_basis(basis));

    SolverOptions wopts;
    wopts.algorithm = LPAlgo::dual;
    wopts.advanced_basis = true;
    warm->apply_options(wopts);
    warm->resolve();
    REQUIRE(warm->is_proven_optimal());
    CHECK(warm->obj_value() == doctest::Approx(-3.5));

    // The warm basis must be returned unchanged (already optimal).
    const auto opt_again = warm->get_basis();
    REQUIRE(opt_again.has_value());
    const Basis again = opt_again.value_or(Basis {});
    CHECK(again.col_status == basis.col_status);
    CHECK(again.row_status == basis.row_status);
  }

  SUBCASE("set_basis rejects a basis with the wrong dimensions")
  {
    Basis wrong = basis;
    wrong.col_status.push_back(BasisStatus::at_lower);
    CHECK_FALSE(backend->set_basis(wrong));

    Basis wrong_rows = basis;
    wrong_rows.row_status.clear();
    CHECK_FALSE(backend->set_basis(wrong_rows));
  }

  SUBCASE("an all-slack at_lower basis is accepted and re-optimised")
  {
    // A (non-optimal) all-slack basis: both structurals at lower, the row
    // basic.  HiGHS must accept it and still reach the optimum.
    Basis slack;
    slack.col_status = {
        BasisStatus::at_lower,
        BasisStatus::at_lower,
    };
    slack.row_status = {BasisStatus::basic};
    REQUIRE(backend->set_basis(slack));
    backend->resolve();
    REQUIRE(backend->is_proven_optimal());
    CHECK(backend->obj_value() == doctest::Approx(-3.5));
  }
}
