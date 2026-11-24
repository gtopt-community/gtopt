// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_cascade_reserve_extended.cpp
 * @brief     Two-level cascade on the extended-reserve hydro-thermal case:
 *            the reserve model and its options survive each level rebuild
 * @date      2026-11-23
 *
 * Case: xres_sddp_case.hpp with `with_ctf`: on top of the dynamic CPF
 * planes, the inertia credits and the capped BESS, a `ctf` zone (150 MW,
 * NO zone urcost) is served by at most 50 MW, so 100 MW are short in each
 * of the 12 blocks, priced by `model_options.reserve_shortage_cost`.
 *
 * Each cascade level overrides `reserve_shortage_cost` (100, then 300), so
 * each level rebuilds its PlanningLP from the cloned Planning.  Every level
 * must converge to the one-phase LP optimum computed with ITS OWN option —
 * if a level lost the reserve arrays (zones, planes, provisions, inertia)
 * or the per-level option, its bound would move.  The two optima differ by
 * exactly 12 · 100 · (300 − 100) = 240 000.
 */

#include <format>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/cascade_method.hpp>
#include <gtopt/cascade_options.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>

#include "xres_sddp_case.hpp"

using namespace gtopt;
using namespace gtopt::test_xres;

namespace
{

[[nodiscard]] double cxr_one_phase_optimum(double shortage_cost)
{
  XresCase c;
  c.phases = false;
  c.with_ctf = true;
  c.model_options_extra =
      std::format(R"(, "reserve_shortage_cost": {})", shortage_cost);
  PlanningLP plp(xres_planning(c));
  REQUIRE(plp.resolve().has_value());
  return xres_snapshot(plp).objective;
}

[[nodiscard]] CascadeLevel cxr_level(const char* name, double shortage_cost)
{
  return CascadeLevel {
      .name = OptName {name},
      .model_options =
          ModelOptions {
              .reserve_shortage_cost = OptReal {shortage_cost},
          },
      .sddp_options =
          CascadeLevelMethod {
              .max_iterations = OptInt {200},
              .apertures = Array<Uid> {},
              .convergence_tol = OptReal {1.0e-8},
              .stationary_tol = OptReal {0.0},
          },
  };
}

}  // namespace

TEST_CASE("Cascade extended reserves: per-level reserve options")  // NOLINT
{
  const double ref100 = cxr_one_phase_optimum(100.0);
  const double ref300 = cxr_one_phase_optimum(300.0);
  REQUIRE(ref300 - ref100 == doctest::Approx(12.0 * 100.0 * 200.0));

  XresCase c;
  c.with_ctf = true;
  c.model_options_extra = R"(, "reserve_shortage_cost": 50)";
  PlanningLP plp(xres_planning(c));

  SDDPOptions base;
  base.max_iterations = 200;
  base.convergence_tol = 1.0e-8;
  base.convergence_mode = ConvergenceMode::gap_only;
  base.enable_api = false;
  base.apertures = std::vector<Uid> {};

  CascadeOptions copts;
  copts.level_array = {
      cxr_level("short100", 100.0),
      cxr_level("short300", 300.0),
  };
  CascadePlanningMethod cascade(std::move(base), std::move(copts));
  auto r = cascade.solve(plp, SolverOptions {});
  REQUIRE(r.has_value());

  const auto& stats = cascade.level_stats();
  REQUIRE(stats.size() == 2);
  // Each level overrides model_options, so each rebuilds its LP; the
  // owned LPs are released at the end of solve(), so the count is 0 here.
  CHECK(cascade.owned_lps_count() == 0);

  CHECK(stats[0].converged);
  CHECK(stats[0].upper_bound == doctest::Approx(ref100).epsilon(1e-6));
  CHECK(stats[0].lower_bound == doctest::Approx(ref100).epsilon(1e-6));

  CHECK(stats[1].converged);
  CHECK(stats[1].upper_bound == doctest::Approx(ref300).epsilon(1e-6));
  CHECK(stats[1].lower_bound == doctest::Approx(ref300).epsilon(1e-6));
}
