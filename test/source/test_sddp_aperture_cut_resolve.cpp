// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_aperture_cut_resolve.cpp
 * @brief     Tests for `SddpOptions::aperture_cut_resolve`.
 * @date      2026-11-24
 *
 * The option gates the post-cut re-solve of the source LP on the aperture
 * backward path (an optimality check whose solution nothing reads).
 * Plumbing (default false, explicit values, merge) plus behaviour: the
 * check must not change the cuts, so an aperture SDDP run reaches the
 * same bounds with the check on and off — while doing fewer LP solves
 * with it off.
 */

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_options.hpp>
#include <gtopt/solver_backend.hpp>

#include "require_value.hpp"
#include "sddp_helpers.hpp"

using namespace gtopt;

TEST_CASE("SddpOptions::aperture_cut_resolve defaults to nullopt")  // NOLINT
{
  const SddpOptions opts;
  CHECK_FALSE(opts.aperture_cut_resolve.has_value());
}

TEST_CASE(  // NOLINT
    "PlanningOptionsLP::sddp_aperture_cut_resolve default and overrides")
{
  SUBCASE("default false")
  {
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    const PlanningLP plp(std::move(planning));
    CHECK_FALSE(plp.options().sddp_aperture_cut_resolve());
  }
  SUBCASE("explicit true")
  {
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    planning.options.sddp_options.aperture_cut_resolve = true;
    const PlanningLP plp(std::move(planning));
    CHECK(plp.options().sddp_aperture_cut_resolve());
  }
}

TEST_CASE("SddpOptions::merge propagates aperture_cut_resolve")  // NOLINT
{
  SddpOptions base;
  SddpOptions override_opts;
  override_opts.aperture_cut_resolve = true;
  base.merge(std::move(override_opts));
  CHECK(test_support::require_value(base.aperture_cut_resolve));

  SddpOptions empty;
  base.merge(std::move(empty));
  CHECK(test_support::require_value(base.aperture_cut_resolve));
}

namespace
{

struct ApertureRun
{
  double lower_bound {0.0};
  double upper_bound {0.0};
  long solves {0};
};

[[nodiscard]] ApertureRun run_aperture_sddp(bool cut_resolve)
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  PlanningLP plp(std::move(planning));

  SDDPOptions opts;
  opts.max_iterations = 8;
  opts.convergence_tol = 1e-9;
  opts.stationary_tol = 0.0;
  opts.cut_sharing = CutSharingMode::multicut;
  // Synthetic cross-scenario apertures (no aperture_array in the case).
  opts.apertures = std::vector<Uid> {Uid {1}, Uid {2}};
  opts.aperture_cut_resolve = cut_resolve;
  opts.enable_api = false;

  const auto before = SolveEffortTotals::instance().snapshot();
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  const auto delta = SolveEffortTotals::instance().snapshot() - before;
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  return {
      .lower_bound = results->back().lower_bound,
      .upper_bound = results->back().upper_bound,
      .solves = delta.count,
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "SDDP apertures — aperture_cut_resolve does not change the bounds")
{
  const auto off = run_aperture_sddp(/*cut_resolve=*/false);
  const auto on = run_aperture_sddp(/*cut_resolve=*/true);

  CHECK(off.lower_bound == doctest::Approx(on.lower_bound).epsilon(1e-6));
  CHECK(off.upper_bound == doctest::Approx(on.upper_bound).epsilon(1e-6));
  CHECK(off.lower_bound
        <= off.upper_bound + (1e-6 * std::abs(off.upper_bound)));
  // The check is one extra LP solve per (scene, phase > 0) backward step.
  CHECK(off.solves < on.solves);
}
