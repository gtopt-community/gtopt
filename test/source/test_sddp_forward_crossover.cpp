// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_forward_crossover.cpp
 * @brief     Forward-pass crossover policy and the interior-dual cut guard
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cmath>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/sddp_forward_crossover.hpp>
#include <gtopt/sddp_method.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace forward_crossover_test
{
namespace
{

[[nodiscard]] SolverOptions with(LPAlgo algo, CrossoverMode xover)
{
  SolverOptions o;
  o.algorithm = algo;
  o.crossover = xover;
  return o;
}

struct Bounds
{
  double lb;
  double ub;
  bool converged;
};

[[nodiscard]] Bounds run(Planning planning, const SDDPOptions& opts)
{
  PlanningLP plp(std::move(planning));
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  const auto& last = results->back();
  return {
      .lb = last.lower_bound,
      .ub = last.upper_bound,
      .converged = last.converged,
  };
}

}  // namespace
}  // namespace forward_crossover_test

TEST_CASE(  // NOLINT
    "Forward crossover policy - vertex only where it is consumed")
{
  using namespace forward_crossover_test;
  const auto def = with(LPAlgo::default_algo, CrossoverMode::automatic);
  const auto bar = with(LPAlgo::barrier, CrossoverMode::automatic);

  SUBCASE("forward duals feed the cut ⇒ crossover")
  {
    for (const auto mode : {BasisCrossMode::off, BasisCrossMode::full_cross}) {
      CHECK(resolve_forward_solver_options(
                def, false, /*duals_feed_cuts=*/true, mode)
                .crossover
            == CrossoverMode::automatic);
    }
  }
  SUBCASE("aperture cuts and no forward basis capture ⇒ no crossover")
  {
    for (const auto mode :
         {BasisCrossMode::off, BasisCrossMode::backward_to_forward})
    {
      CHECK(resolve_forward_solver_options(
                bar, false, /*duals_feed_cuts=*/false, mode)
                .crossover
            == CrossoverMode::none);
    }
  }
  SUBCASE("aperture cuts but the forward basis is captured ⇒ crossover")
  {
    for (const auto mode : {BasisCrossMode::warm,
                            BasisCrossMode::forward_to_backward,
                            BasisCrossMode::full_cross})
    {
      CHECK(resolve_forward_solver_options(
                def, false, /*duals_feed_cuts=*/false, mode)
                .crossover
            == CrossoverMode::automatic);
    }
  }
  SUBCASE("an explicit non-auto crossover always wins")
  {
    for (const auto x :
         {CrossoverMode::none, CrossoverMode::primal, CrossoverMode::dual})
    {
      CHECK(resolve_forward_solver_options(with(LPAlgo::barrier, x),
                                           /*explicit_options=*/true,
                                           /*duals_feed_cuts=*/true,
                                           BasisCrossMode::full_cross)
                .crossover
            == x);
    }
    // explicit "auto" is resolved like the default
    CHECK(resolve_forward_solver_options(
              bar, true, /*duals_feed_cuts=*/false, BasisCrossMode::off)
              .crossover
          == CrossoverMode::none);
  }
  SUBCASE("simplex algorithms are left untouched")
  {
    for (const auto algo : {LPAlgo::primal, LPAlgo::dual}) {
      const auto o = with(algo, CrossoverMode::automatic);
      CHECK(resolve_forward_solver_options(
                o, false, /*duals_feed_cuts=*/false, BasisCrossMode::off)
                .crossover
            == CrossoverMode::automatic);
      CHECK_FALSE(is_interior_solve(with(algo, CrossoverMode::none)));
    }
  }
  SUBCASE("interior solves")
  {
    CHECK(is_interior_solve(with(LPAlgo::barrier, CrossoverMode::none)));
    CHECK(is_interior_solve(with(LPAlgo::default_algo, CrossoverMode::none)));
    CHECK_FALSE(is_interior_solve(bar));
  }
}

TEST_CASE(  // NOLINT
    "Forward crossover - interior cut intercept guard")
{
  CHECK(guarded_cut_intercept(316350.0, false) == 316350.0);
  CHECK(guarded_cut_intercept(316350.0, true)
        == doctest::Approx(316350.0 - (316350.0 * interior_cut_rel_gap)));
  CHECK(guarded_cut_intercept(-2.0e6, true)
        == doctest::Approx(-2.0e6 - (2.0e6 * interior_cut_rel_gap)));
  // |z| < 1: absolute shift of the gap itself
  CHECK(guarded_cut_intercept(0.0, true)
        == doctest::Approx(-interior_cut_rel_gap));
}

TEST_CASE(  // NOLINT
    "Forward crossover - Benders with interior forward duals keeps LB <= UB")
{
  using namespace forward_crossover_test;
  // 3-phase hydro, pure Benders (the cut IS the forward dual).  Without
  // the guard an explicit crossover = none overshot LB − UB ≈ +2e-6
  // (6e-12 relative) on the cascade version of this case.
  SDDPOptions base;
  base.max_iterations = 30;
  base.convergence_tol = 1e-4;
  base.apertures = std::vector<Uid> {};
  base.enable_api = false;
  base.basis_cross_mode = BasisCrossMode::off;

  const auto vertex = run(make_3phase_hydro_planning(), base);
  CHECK(vertex.converged);
  CHECK(vertex.lb <= vertex.ub + (1e-9 * std::abs(vertex.ub)));

  SDDPOptions interior = base;
  interior.forward_solver_options = with(LPAlgo::barrier, CrossoverMode::none);
  const auto in = run(make_3phase_hydro_planning(), interior);
  CHECK(in.converged);
  CHECK(in.lb <= in.ub);  // strict: the guard absorbs the gap noise
  CHECK(in.ub == doctest::Approx(vertex.ub).epsilon(1e-4));
  CHECK(in.lb == doctest::Approx(vertex.lb).epsilon(1e-4));
}

TEST_CASE(  // NOLINT
    "Forward crossover - aperture cuts with a barrier-only forward pass")
{
  using namespace forward_crossover_test;
  // Synthetic apertures from the two scenarios (cuts from aperture
  // clones), basis reuse off ⇒
  // the forward pass resolves to crossover none; same bounds as with an
  // explicit vertex forward pass.
  SDDPOptions base;
  base.max_iterations = 15;
  base.convergence_tol = 1e-3;
  base.apertures = std::vector<Uid> {Uid {1}, Uid {2}};  // synthetic
  base.enable_api = false;
  base.basis_cross_mode = BasisCrossMode::off;

  const auto none = run(make_2phase_2scenario_planning(), base);
  SDDPOptions vertex_opts = base;
  vertex_opts.forward_solver_options =
      with(LPAlgo::barrier, CrossoverMode::primal);
  const auto vertex = run(make_2phase_2scenario_planning(), vertex_opts);
  CHECK(none.lb <= none.ub + (1e-9 * std::abs(none.ub)));
  CHECK(none.ub == doctest::Approx(vertex.ub).epsilon(1e-6));
  CHECK(none.lb == doctest::Approx(vertex.lb).epsilon(1e-6));
}

TEST_CASE(  // NOLINT
    "Forward crossover - dual infeasibility measure")
{
  constexpr double inf = 1e20;
  // columns: [0, ∞) d = −0.1 (viol 0.1, x = 5 → 0.5); (−∞, 3] d = 0.2
  // (viol 0.2, x = 0 → 0.2); free d = −0.05 (viol 0.05, x = 2 → 0.1);
  // boxed d = −7 (feasible)
  const std::vector<double> lo {0.0, -inf, -inf, 0.0};
  const std::vector<double> up {inf, 3.0, inf, 1.0};
  const std::vector<double> d {-0.1, 0.2, -0.05, -7.0};
  const std::vector<double> x {5.0, 0.0, 2.0, 1.0};
  // rows: ≥ with y = −0.3 (viol 0.3); ≤ with y = 0.4 (viol 0.4);
  // equality y = −9 (feasible)
  const std::vector<double> rlo {1.0, -inf, 2.0};
  const std::vector<double> rup {inf, 4.0, 2.0};
  const std::vector<double> y {-0.3, 0.4, -9.0};
  const auto di = dual_infeasibility(lo, up, d, x, rlo, rup, y, 1e19);
  CHECK(di.max_col == doctest::Approx(0.2));
  CHECK(di.max_row == doctest::Approx(0.4));
  CHECK(di.weighted == doctest::Approx(0.5 + 0.2 + 0.1));
  // a dual-feasible solution measures zero
  const std::vector<double> d_ok {0.1, -0.2, 0.0, -7.0};
  const std::vector<double> y_ok {0.3, -0.4, -9.0};
  const auto ok = dual_infeasibility(lo, up, d_ok, x, rlo, rup, y_ok, 1e19);
  CHECK(ok.max_col == 0.0);
  CHECK(ok.max_row == 0.0);
  CHECK(ok.weighted == 0.0);
}
