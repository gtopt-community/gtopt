// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_solver_obj_offset_per_solver.cpp
 * @brief     Per-solver tests for the native objective-offset channel
 *            (SolverBackend::set_obj_offset via
 * LinearInterface::add_obj_constant).
 * @date      2026-06-30
 * @author    Marcelo Matus
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `LinearInterface::add_obj_constant(c)` accumulates the constant in
 * `m_obj_constant_raw_` and pushes the running total to the backend's NATIVE
 * objective offset:
 *   - CPLEX   : CPXchgobjoffset
 *   - HiGHS   : HighsLp::offset_ / changeObjectiveOffset
 *   - Gurobi  : GRB_DBL_ATTR_OBJCON
 *   - MindOpt : MDO_DBL_ATTR_OBJ_CON
 *   - cuOpt   : objective_offset (cuOptCreateRangedProblem)
 *   - SCIP    : SCIPaddOrigObjoffset
 *   - OSI/CLP : ClpModel::setObjectiveOffset (negated — CLP subtracts)
 * `get_obj_value_raw()` no longer re-adds the constant gtopt-side, so a
 * reported objective that includes `c` proves the constant reached the solver.
 *
 * Closed-form fixture (LP-relaxation optimum == MIP optimum):
 *   min  10 x + 1 y      x >= 0 (continuous),  y in [0, 3]
 *   s.t.  x + y >= 5      ⇒  optimum y = 3, x = 2, obj = 23.
 * The argmin is offset-invariant, so a constant only shifts the reported value.
 */

#include <array>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_backend.hpp>
#include <gtopt/solver_enums.hpp>
#include <gtopt/solver_registry.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace test_solver_obj_offset
{
namespace
{
/// Build the closed-form fixture into `lp`; return the two column indices.
/// When `integer_y` is true, `y` is declared integer (MIP path).
struct Fixture
{
  ColIndex x;
  ColIndex y;
};

[[nodiscard]] Fixture build_fixture(LinearInterface& lp, bool integer_y)
{
  const auto x = lp.add_col(SparseCol {
      .lowb = 0.0,
      .cost = 10.0,
  });
  const auto y = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 3.0,
      .cost = 1.0,
  });
  if (integer_y) {
    lp.set_integer(y);
  }
  SparseRow row;
  row[x] = 1.0;
  row[y] = 1.0;
  row.greater_equal(5.0);
  [[maybe_unused]] const auto row_idx = lp.add_row(row);
  return Fixture {.x = x, .y = y};
}
}  // namespace
}  // namespace test_solver_obj_offset

TEST_CASE(  // NOLINT
    "add_obj_constant - native offset shifts LP objective by exactly c "
    "(per solver)")
{
  using namespace test_solver_obj_offset;
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();

  const double c = 137.5;
  const SolverOptions opts;
  bool tested_any = false;

  for (const auto& name : reg.available_solvers()) {
    CAPTURE(name);  // logs the failing plugin under ctest

    const bool ran = gtopt::solver_test::run_or_skip_license(
        [&]
        {
          // ── Baseline: no offset ──────────────────────────────────────
          double baseline_obj = 0.0;
          double baseline_x = 0.0;
          double baseline_y = 0.0;
          {
            LinearInterface lp(name);
            const auto f = build_fixture(lp, /*integer_y=*/false);
            REQUIRE(lp.initial_solve(opts).has_value());
            REQUIRE(lp.is_optimal());
            baseline_obj = lp.get_obj_value();
            const auto sol = lp.get_col_sol();
            baseline_x = sol[f.x];
            baseline_y = sol[f.y];
            CHECK(baseline_obj == doctest::Approx(23.0).epsilon(1e-6));
          }

          // ── Offset applied via add_obj_constant on the LIVE backend ──
          LinearInterface lp(name);
          const auto f = build_fixture(lp, /*integer_y=*/false);
          REQUIRE(lp.initial_solve(opts).has_value());
          REQUIRE(lp.is_optimal());

          lp.add_obj_constant(c);
          REQUIRE(lp.resolve(opts).has_value());
          REQUIRE(lp.is_optimal());

          // (1) Reported objective shifts by exactly c — since the
          //     gtopt-side re-add is gone, this proves the offset reached
          //     the solver.
          CHECK(lp.get_obj_value()
                == doctest::Approx(baseline_obj + c).epsilon(1e-6));

          // (2) The argmin is unchanged (a constant does not move the
          //     optimum).
          {
            const auto sol = lp.get_col_sol();
            CHECK(sol[f.x] == doctest::Approx(baseline_x).epsilon(1e-6));
            CHECK(sol[f.y] == doctest::Approx(baseline_y).epsilon(1e-6));
          }

          // (3) clone() preserves the offset: the cloned backend re-solves
          //     to the same offset-inclusive objective without any further
          //     add_obj_constant.
          {
            auto cloned = lp.clone();
            REQUIRE(cloned.resolve(opts).has_value());
            REQUIRE(cloned.is_optimal());
            CHECK(cloned.get_obj_value()
                  == doctest::Approx(baseline_obj + c).epsilon(1e-6));
          }
        });
    if (!ran) {
      MESSAGE("solver '" << name << "' license unavailable — skipping");
      continue;
    }
    tested_any = true;
  }

  if (!tested_any) {
    MESSAGE("no solver plugin loaded — skipping per-solver obj-offset test");
  }
}

TEST_CASE(  // NOLINT
    "add_obj_constant - native offset reaches the MIP reported objective "
    "(per MIP solver)")
{
  using namespace test_solver_obj_offset;
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();

  const double c = 500.0;
  const SolverOptions opts;
  bool tested_any = false;

  // exact_mip_solvers() drops LP-only backends (covered by the LP test
  // above) and cuOpt (thread-unsafe B&B logger; see helper doc).
  for (const auto& name : gtopt::solver_test::exact_mip_solvers()) {
    CAPTURE(name);

    const bool ran = gtopt::solver_test::run_or_skip_license(
        [&]
        {
          LinearInterface lp(name);
          const auto f = build_fixture(lp, /*integer_y=*/true);
          REQUIRE(lp.initial_solve(opts).has_value());
          REQUIRE(lp.is_optimal());
          REQUIRE(lp.has_integer_cols());
          const double mip_obj = lp.get_obj_value();
          CHECK(mip_obj == doctest::Approx(23.0).epsilon(1e-6));

          // Apply the native offset and re-solve the MIP.  The offset must
          // move the solver's reported optimal objective by exactly c —
          // the property CPLEX's MIP relative-gap is computed against —
          // while the integer optimum holds.
          lp.add_obj_constant(c);
          REQUIRE(lp.resolve(opts).has_value());
          REQUIRE(lp.is_optimal());
          CHECK(lp.get_obj_value()
                == doctest::Approx(mip_obj + c).epsilon(1e-6));
          {
            const auto sol = lp.get_col_sol();
            CHECK(sol[f.y] == doctest::Approx(3.0).epsilon(1e-6));
            CHECK(sol[f.x] == doctest::Approx(2.0).epsilon(1e-6));
          }
        });
    if (!ran) {
      MESSAGE("solver '" << name << "' license unavailable — skipping");
      continue;
    }
    tested_any = true;
  }

  if (!tested_any) {
    MESSAGE(
        "no MIP-capable solver plugin loaded — skipping MIP obj-offset "
        "test");
  }
}

TEST_CASE(  // NOLINT
    "add_obj_constant - successive calls accumulate; clone carries the sum")
{
  using namespace test_solver_obj_offset;
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  const auto solvers = reg.available_solvers();
  if (solvers.empty()) {
    MESSAGE("no solver plugin loaded — skipping obj-offset accumulation test");
    return;
  }

  const auto& name = solvers.front();
  CAPTURE(name);
  const SolverOptions opts;

  LinearInterface lp(name);
  const auto f = build_fixture(lp, /*integer_y=*/false);
  REQUIRE(lp.initial_solve(opts).has_value());
  REQUIRE(lp.is_optimal());
  const double base = lp.get_obj_value();
  CHECK(base == doctest::Approx(23.0).epsilon(1e-6));

  // Two successive constants accumulate: the objective shifts by their SUM,
  // proving each add_obj_constant pushes the running total (not a delta that
  // overwrites the previous).
  const double c1 = 40.0;
  const double c2 = -15.0;  // include a negative to exercise sign handling
  lp.add_obj_constant(c1);
  lp.add_obj_constant(c2);
  REQUIRE(lp.resolve(opts).has_value());
  REQUIRE(lp.is_optimal());
  CHECK(lp.get_obj_value() == doctest::Approx(base + c1 + c2).epsilon(1e-6));

  // The argmin is still offset-invariant.
  {
    const auto sol = lp.get_col_sol();
    CHECK(sol[f.x] == doctest::Approx(2.0).epsilon(1e-6));
    CHECK(sol[f.y] == doctest::Approx(3.0).epsilon(1e-6));
  }

  // clone() carries the accumulated sum.
  auto cloned = lp.clone();
  REQUIRE(cloned.resolve(opts).has_value());
  REQUIRE(cloned.is_optimal());
  CHECK(cloned.get_obj_value()
        == doctest::Approx(base + c1 + c2).epsilon(1e-6));
}

// ── Offset set BEFORE the first solve ────────────────────────────────────────
//
// The tests above apply the constant only AFTER a first solve.  That order
// masked a MindOpt 2.3.0 double count: its PRIMAL_OBJ_VAL already includes
// MDO_DBL_ATTR_OBJ_CON, but a change to ObjCon alone does not invalidate the
// solution, so the post-offset re-solve was a no-op that kept the
// offset-free PrimalObjVal and the plugin's manual `+ offset` looked right.
// Every real gtopt run sets the offset at load time (load_flat →
// set_obj_offset) before the first solve, which is the order pinned here.

namespace test_solver_obj_offset
{
namespace
{
/// Backend-level fixture (same closed form as `build_fixture`):
///   min 10 x + 1 y,  x >= 0,  y in [0, 3],  x + y >= 5  ⇒  obj = 23.
void load_backend_fixture(SolverBackend& b, bool integer_y)
{
  const double inf = b.infinity();
  const std::array<int, 3> matbeg {
      0,
      1,
      2,
  };
  const std::array<int, 2> matind {
      0,
      0,
  };
  const std::array<double, 2> matval {
      1.0,
      1.0,
  };
  const std::array<double, 2> collb {
      0.0,
      0.0,
  };
  const std::array<double, 2> colub {
      inf,
      3.0,
  };
  const std::array<double, 2> obj {
      10.0,
      1.0,
  };
  const std::array<double, 1> rowlb {
      5.0,
  };
  const std::array<double, 1> rowub {
      inf,
  };
  b.load_problem(2,
                 1,
                 matbeg.data(),
                 matind.data(),
                 matval.data(),
                 collb.data(),
                 colub.data(),
                 obj.data(),
                 rowlb.data(),
                 rowub.data());
  if (integer_y) {
    b.set_integer(1);
  }
}

/// Run the offset-before-solve / change / clone sequence on `name` through
/// LinearInterface::add_obj_constant.
void check_offset_before_first_solve(const std::string& name, bool integer_y)
{
  const SolverOptions opts;
  const double c1 = 250.0;
  const double c2 = -40.0;

  LinearInterface lp(name);
  const auto f = build_fixture(lp, integer_y);
  lp.add_obj_constant(c1);  // before ANY solve — the load_flat order
  REQUIRE(lp.initial_solve(opts).has_value());
  REQUIRE(lp.is_optimal());
  CHECK(lp.get_obj_value() == doctest::Approx(23.0 + c1).epsilon(1e-6));
  {
    const auto sol = lp.get_col_sol();
    CHECK(sol[f.x] == doctest::Approx(2.0).epsilon(1e-6));
    CHECK(sol[f.y] == doctest::Approx(3.0).epsilon(1e-6));
  }

  // Change the offset between solves (warm re-solve).
  lp.add_obj_constant(c2);
  REQUIRE(lp.resolve(opts).has_value());
  REQUIRE(lp.is_optimal());
  CHECK(lp.get_obj_value() == doctest::Approx(23.0 + c1 + c2).epsilon(1e-6));

  // A clone carries the accumulated offset exactly once.
  auto cloned = lp.clone();
  REQUIRE(cloned.resolve(opts).has_value());
  REQUIRE(cloned.is_optimal());
  CHECK(cloned.get_obj_value()
        == doctest::Approx(23.0 + c1 + c2).epsilon(1e-6));
}
}  // namespace
}  // namespace test_solver_obj_offset

TEST_CASE(  // NOLINT
    "add_obj_constant - offset set before the first LP solve is counted once "
    "(per solver)")
{
  using namespace test_solver_obj_offset;
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  bool tested_any = false;

  for (const auto& name : reg.available_solvers()) {
    CAPTURE(name);
    const bool ran = gtopt::solver_test::run_or_skip_license(
        [&] { check_offset_before_first_solve(name, /*integer_y=*/false); });
    if (!ran) {
      MESSAGE("solver '" << name << "' license unavailable — skipping");
      continue;
    }
    tested_any = true;
  }
  if (!tested_any) {
    MESSAGE("no solver plugin loaded — skipping");
  }
}

TEST_CASE(  // NOLINT
    "add_obj_constant - offset set before the first MIP solve is counted once "
    "(per MIP solver)")
{
  using namespace test_solver_obj_offset;
  bool tested_any = false;

  for (const auto& name : gtopt::solver_test::exact_mip_solvers()) {
    CAPTURE(name);
    const bool ran = gtopt::solver_test::run_or_skip_license(
        [&] { check_offset_before_first_solve(name, /*integer_y=*/true); });
    if (!ran) {
      MESSAGE("solver '" << name << "' license unavailable — skipping");
      continue;
    }
    tested_any = true;
  }
  if (!tested_any) {
    MESSAGE("no MIP-capable solver plugin loaded — skipping");
  }
}

TEST_CASE(  // NOLINT
    "SolverBackend::set_obj_offset - obj_value == c·x* + offset across load, "
    "reload, offset updates and clone (per solver)")
{
  using namespace test_solver_obj_offset;
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  bool tested_any = false;

  for (const auto& name : reg.available_solvers()) {
    for (const bool integer_y : {false, true}) {
      if (integer_y && (name == "cuopt" || !reg.supports_mip(name))) {
        continue;
      }
      CAPTURE(name);
      CAPTURE(integer_y);
      const bool ran = gtopt::solver_test::run_or_skip_license(
          [&]
          {
            auto b = reg.create(name);
            REQUIRE(b != nullptr);
            CAPTURE(b->solver_version());

            const auto solve_and_check =
                [&](SolverBackend& s, double offset, bool warm)
            {
              if (warm) {
                s.resolve();
              } else {
                s.initial_solve();
              }
              REQUIRE(s.is_proven_optimal());
              const auto* x = s.col_solution();
              REQUIRE(x != nullptr);
              CHECK(x[0] == doctest::Approx(2.0).epsilon(1e-6));
              CHECK(x[1] == doctest::Approx(3.0).epsilon(1e-6));
              CHECK(s.obj_value()
                    == doctest::Approx(23.0 + offset).epsilon(1e-6));
            };

            // (1) Offset stored BEFORE load_problem: the load must keep it.
            b->set_obj_offset(1000.0);
            load_backend_fixture(*b, integer_y);
            solve_and_check(*b, 1000.0, /*warm=*/false);

            // (2) Reload the same problem (load_problem resets the native
            //     model on several backends) — the offset survives, once.
            load_backend_fixture(*b, integer_y);
            solve_and_check(*b, 1000.0, /*warm=*/false);

            // (3) Absolute semantics: two successive sets keep only the
            //     last one; a warm re-solve reports it.
            b->set_obj_offset(7.0);
            b->set_obj_offset(-300.0);
            solve_and_check(*b, -300.0, /*warm=*/true);

            // (4) clone() carries the current offset exactly once, and
            //     an offset update on the clone does not leak back.
            auto cl = b->clone();
            REQUIRE(cl != nullptr);
            solve_and_check(*cl, -300.0, /*warm=*/true);
            cl->set_obj_offset(55.0);
            solve_and_check(*cl, 55.0, /*warm=*/true);
            solve_and_check(*b, -300.0, /*warm=*/true);

            // (5) Back to zero.
            b->set_obj_offset(0.0);
            solve_and_check(*b, 0.0, /*warm=*/true);
          });
      if (!ran) {
        MESSAGE("solver '" << name << "' license unavailable — skipping");
        continue;
      }
      tested_any = true;
    }
  }
  if (!tested_any) {
    MESSAGE("no solver plugin loaded — skipping");
  }
}
