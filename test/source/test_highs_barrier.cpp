// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_highs_barrier.cpp
 * @brief     HiGHS barrier (HiPO / IPX) option mapping and solution quality
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `algorithm = barrier` maps to HiGHS `solver = ipm` (HiPO when the HiGHS
 * build ships it, IPX otherwise) and `crossover` to `run_crossover`.
 * The crossover switch must be applied in BOTH directions on every
 * apply_options(): the SDDP forward pass solves a cell with
 * `crossover = none` and the backward pass re-solves the same backend
 * with crossover on, expecting a vertex (basis + basic duals).
 *
 * LP:  min x + y + 2 z   s.t.  x + y + z >= 2,  x - y <= 1,  x, y, z >= 0.
 * Optimal face x + y = 2, z = 0, x - y <= 1 (degenerate in x, y): every
 * optimum costs 2 and the row duals are unique, (1, 0).  A vertex sits
 * at a corner of the face; the interior point does not.
 */

#include <array>
#include <cmath>
#include <memory>
#include <optional>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/solver_backend.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] std::unique_ptr<SolverBackend> make_highs_barrier_backend()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("highs")) {
    return nullptr;
  }
  return reg.create("highs");
}

void load_barrier_face_lp(SolverBackend& b)
{
  const double inf = b.infinity();
  const std::array<int, 4> matbeg {
      0,
      2,
      4,
      5,
  };
  const std::array<int, 5> matind {
      0,
      1,
      0,
      1,
      0,
  };
  const std::array<double, 5> matval {
      1.0,
      1.0,
      1.0,
      -1.0,
      1.0,
  };
  const std::array<double, 3> collb {
      0.0,
      0.0,
      0.0,
  };
  const std::array<double, 3> colub {
      inf,
      inf,
      inf,
  };
  const std::array<double, 3> obj {
      1.0,
      1.0,
      2.0,
  };
  const std::array<double, 2> rowlb {
      2.0,
      -inf,
  };
  const std::array<double, 2> rowub {
      inf,
      1.0,
  };
  b.load_problem(3,
                 2,
                 matbeg.data(),
                 matind.data(),
                 matval.data(),
                 collb.data(),
                 colub.data(),
                 obj.data(),
                 rowlb.data(),
                 rowub.data());
}

[[nodiscard]] SolverOptions barrier_opts(CrossoverMode xover, int threads = 2)
{
  return SolverOptions {
      .algorithm = LPAlgo::barrier,
      .threads = threads,
      .crossover = xover,
  };
}

/// Optimal objective, primal feasibility and the unique duals (1, 0),
/// reduced costs (0, 0, 1) — valid for a vertex and an interior optimum.
void check_barrier_face_optimum(const SolverBackend& b, double tol)
{
  REQUIRE(b.is_proven_optimal());
  CHECK(b.obj_value() == doctest::Approx(2.0).epsilon(tol));
  const double* x = b.col_solution();
  CHECK(x[0] >= -tol);
  CHECK(x[1] >= -tol);
  CHECK(x[0] + x[1] + x[2] >= 2.0 - tol);
  CHECK(x[0] - x[1] <= 1.0 + tol);
  CHECK(std::abs(x[2]) <= tol);
  const double* y = b.row_price();
  CHECK(y[0] == doctest::Approx(1.0).epsilon(tol));
  CHECK(std::abs(y[1]) <= tol);
  const double* d = b.reduced_cost();
  CHECK(std::abs(d[0]) <= tol);
  CHECK(std::abs(d[1]) <= tol);
  CHECK(d[2] == doctest::Approx(1.0).epsilon(tol));
}

}  // namespace

TEST_CASE("HiGHS barrier with crossover returns a vertex")  // NOLINT
{
  auto b = make_highs_barrier_backend();
  if (!b) {
    return;
  }
  load_barrier_face_lp(*b);
  b->apply_options(barrier_opts(CrossoverMode::automatic));
  b->initial_solve();
  check_barrier_face_optimum(*b, 1e-7);
  CHECK(b->get_algorithm() == LPAlgo::barrier);
  CHECK(b->get_basis().has_value());
  // No kappa check: HiGHS computes it from the simplex factorisation,
  // which an IPM + crossover solve does not build.
}

TEST_CASE("HiGHS barrier without crossover returns interior duals")  // NOLINT
{
  auto b = make_highs_barrier_backend();
  if (!b) {
    return;
  }
  load_barrier_face_lp(*b);
  b->apply_options(barrier_opts(CrossoverMode::none));
  b->initial_solve();
  // Interior point: no basis, but the primal is optimal and the duals
  // are the (here unique) optimal duals — usable as a Benders subgradient.
  check_barrier_face_optimum(*b, 1e-5);
  CHECK_FALSE(b->get_basis().has_value());
  CHECK_FALSE(b->get_kappa().has_value());
}

TEST_CASE(  // NOLINT
    "HiGHS crossover switches back on for a later solve of the same backend")
{
  auto b = make_highs_barrier_backend();
  if (!b) {
    return;
  }
  load_barrier_face_lp(*b);

  // SDDP forward: barrier, no crossover.
  b->apply_options(barrier_opts(CrossoverMode::none));
  b->initial_solve();
  REQUIRE(b->is_proven_optimal());
  CHECK_FALSE(b->get_basis().has_value());

  // SDDP backward on the same backend: crossover requested — must get a
  // vertex, not a stale `run_crossover = off`.
  b->apply_options(barrier_opts(CrossoverMode::automatic));
  b->resolve();
  check_barrier_face_optimum(*b, 1e-7);
  CHECK(b->get_basis().has_value());

  SUBCASE("and off again")
  {
    b->clear_basis();
    b->apply_options(barrier_opts(CrossoverMode::none));
    b->resolve();
    REQUIRE(b->is_proven_optimal());
    CHECK_FALSE(b->get_basis().has_value());
  }
}

TEST_CASE("HiGHS crossover = none does not affect simplex")  // NOLINT
{
  auto b = make_highs_barrier_backend();
  if (!b) {
    return;
  }
  load_barrier_face_lp(*b);
  b->apply_options(SolverOptions {
      .algorithm = LPAlgo::dual,
      .crossover = CrossoverMode::none,
  });
  b->initial_solve();
  check_barrier_face_optimum(*b, 1e-9);
  CHECK(b->get_basis().has_value());  // simplex always ends on a vertex
}

TEST_CASE("HiGHS barrier thread counts give the same optimum")  // NOLINT
{
  // HiPO parallelises its factorisation over the HiGHS scheduler; the
  // thread count must not change the answer.  Each count goes through
  // apply_options(), which resets this thread's HiGHS scheduler when the
  // count changes (HiGHS refuses a run() whose `threads` differs from the
  // live scheduler size).
  for (const int threads : {1, 2, 4}) {
    CAPTURE(threads);
    auto b = make_highs_barrier_backend();
    if (!b) {
      return;
    }
    load_barrier_face_lp(*b);
    b->apply_options(barrier_opts(CrossoverMode::automatic, threads));
    b->initial_solve();
    CHECK(b->get_threads() == threads);
    check_barrier_face_optimum(*b, 1e-7);
  }
}

TEST_CASE("HiGHS barrier_solver auto / ipx / hipo reach the optimum")  // NOLINT
{
  // Every value is a valid selection whatever the HiGHS build: `hipo`
  // falls back to IPX (as `auto` does) when HiGHS has no HiPO.
  for (const auto bs :
       {
           BarrierSolver::automatic,
           BarrierSolver::ipx,
           BarrierSolver::hipo,
       })
  {
    for (const auto xover :
         {
             CrossoverMode::automatic,
             CrossoverMode::none,
         })
    {
      CAPTURE(static_cast<int>(bs));
      CAPTURE(static_cast<int>(xover));
      auto b = make_highs_barrier_backend();
      if (!b) {
        return;
      }
      load_barrier_face_lp(*b);
      auto opts = barrier_opts(xover);
      opts.barrier_solver = bs;
      b->apply_options(opts);
      b->initial_solve();
      check_barrier_face_optimum(*b,
                                 xover == CrossoverMode::none ? 1e-5 : 1e-7);
      CHECK(b->get_basis().has_value() == (xover != CrossoverMode::none));
    }
  }
}

TEST_CASE("HiGHS barrier_solver can be switched on the same backend")  // NOLINT
{
  auto b = make_highs_barrier_backend();
  if (!b) {
    return;
  }
  load_barrier_face_lp(*b);
  auto opts = barrier_opts(CrossoverMode::automatic);
  for (const auto bs :
       {
           BarrierSolver::hipo,
           BarrierSolver::ipx,
           BarrierSolver::automatic,
       })
  {
    CAPTURE(static_cast<int>(bs));
    opts.barrier_solver = bs;
    b->clear_basis();
    b->apply_options(opts);
    b->initial_solve();
    check_barrier_face_optimum(*b, 1e-7);
  }
}

namespace
{

[[nodiscard]] std::unique_ptr<SolverBackend> make_kappa_backend(
    const std::string& name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver(name)) {
    return nullptr;
  }
  return reg.create(name);
}

}  // namespace

TEST_CASE("kappa_mode estimate / exact / off on HiGHS and CPLEX")  // NOLINT
{
  for (const std::string name : {"highs", "cplex"}) {
    std::optional<double> estimate;
    std::optional<double> exact;
    for (const std::optional<KappaMode> mode :
         {
             std::optional<KappaMode> {},
             std::optional<KappaMode> {KappaMode::estimate},
             std::optional<KappaMode> {KappaMode::exact},
             std::optional<KappaMode> {KappaMode::off},
         })
    {
      CAPTURE(name);
      CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
      auto b = make_kappa_backend(name);
      if (!b) {
        break;
      }
      load_barrier_face_lp(*b);
      b->apply_options(SolverOptions {
          .algorithm = LPAlgo::dual,
          .threads = 1,
          .kappa_mode = mode,
      });
      b->initial_solve();
      REQUIRE(b->is_proven_optimal());
      const auto k = b->get_kappa();
      if (mode == KappaMode::off) {
        CHECK_FALSE(k.has_value());
        continue;
      }
      REQUIRE(k.has_value());
      CHECK(k.value_or(0.0) >= 1.0 - 1e-12);
      if (mode == KappaMode::exact) {
        exact = k;
      } else {
        estimate = k;
      }
    }
    // HiGHS's Hager estimate never exceeds the exact value.
    if (estimate && exact && name == "highs") {
      CHECK(estimate.value_or(0.0) <= exact.value_or(0.0) * (1.0 + 1e-9));
    }
  }
}

TEST_CASE(
    "HiGHS cold_solve_without_basis: cold settings or warm ones")  // NOLINT
{
  // barrier + crossover none + advanced_basis on a backend with no basis:
  // with the default (cold solve) the barrier runs and leaves no basis;
  // with `false` the warm settings (simplex) are applied anyway and the
  // solve ends on a vertex with a basis.
  for (const std::optional<bool> cold :
       {
           std::optional<bool> {},
           std::optional<bool> {true},
           std::optional<bool> {false},
       })
  {
    CAPTURE(cold.has_value() ? static_cast<int>(*cold) : -1);
    auto b = make_highs_barrier_backend();
    if (!b) {
      return;
    }
    load_barrier_face_lp(*b);
    auto opts = barrier_opts(CrossoverMode::none);
    opts.advanced_basis = true;
    opts.cold_solve_without_basis = cold;
    b->apply_options(opts);
    b->initial_solve();
    const bool warm_settings = !cold.value_or(true);
    check_barrier_face_optimum(*b, warm_settings ? 1e-7 : 1e-5);
    CHECK(b->get_basis().has_value() == warm_settings);
  }
}
