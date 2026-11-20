// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_gap_convergence.cpp
 * @brief     Regression tests for the primary SDDP gap convergence test
 * @date      2026-11-18
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The 2026-05-14 convergence rewrite made ΔUB stationarity the only
 * stop signal and dropped the ``gap < convergence_tol`` exit.  A run
 * with stationarity disabled (``stationary_tol = 0`` or
 * ``convergence_mode = gap_only``) then had NO exit: bounds met exactly
 * (``gap = ±0.0000 %``) yet it iterated until ``max_iterations``.
 * Contract pinned here:
 *
 *  1. ``sddp_gap_converged`` band semantics
 *     ``(-kSddpGapFpEpsilon, convergence_tol)`` and
 *     ``SDDPOptions::stationarity_enabled``.
 *  2. Stationarity disabled → the gap test stops the run.
 *  3. ``gap_only`` mode → the gap test stops the run.
 *  4. Negative ``convergence_tol`` → gap test disabled, the run uses the
 *     full ``max_iterations`` budget.
 *  5. Multi-scene (2 scenes) synchronous path converges on the gap.
 *  6. Stationarity ENABLED → the gap is NOT a stop signal: a run whose
 *     gap is already inside the band keeps iterating until the ΔUB
 *     window fires (``stationary_converged``).
 */

#include <algorithm>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

constexpr int kMaxIterations = 30;

/// Index of the first converged iteration result, or -1.
[[nodiscard]] auto first_converged(
    const std::vector<SDDPIterationResult>& results) -> int
{
  const auto it = std::ranges::find_if(
      results, [](const SDDPIterationResult& ir) { return ir.converged; });
  return it == results.end()
      ? -1
      : static_cast<int>(std::distance(results.begin(), it));
}

[[nodiscard]] auto gap_only_opts() -> SDDPOptions
{
  SDDPOptions opts;
  opts.max_iterations = kMaxIterations;
  opts.convergence_tol = 1e-4;
  opts.stationary_tol = 0.0;  // ΔUB stationarity can never fire
  opts.enable_api = false;
  return opts;
}

}  // namespace

TEST_CASE("sddp_gap_converged band semantics")  // NOLINT
{
  constexpr double tol = 1e-4;

  CHECK(sddp_gap_converged(0.0, tol));
  CHECK(sddp_gap_converged(-0.0, tol));
  CHECK(sddp_gap_converged(5e-5, tol));
  CHECK(sddp_gap_converged(-0.5 * kSddpGapFpEpsilon, tol));

  CHECK_FALSE(sddp_gap_converged(tol, tol));
  CHECK_FALSE(sddp_gap_converged(0.5, tol));
  // LB > UB beyond rounding noise is an overshoot, never "converged".
  CHECK_FALSE(sddp_gap_converged(-kSddpGapFpEpsilon, tol));
  CHECK_FALSE(sddp_gap_converged(-1e-3, tol));
  CHECK_FALSE(sddp_gap_converged(-11.84, tol));

  // A negative tolerance empties the band (gap test disabled).
  CHECK_FALSE(sddp_gap_converged(0.0, -1.0));

  static_assert(sddp_gap_converged(0.0, 1e-4));

  SDDPOptions opts;
  CHECK(opts.stationarity_enabled());  // defaults: 0.005 / 4 / gap_stationary
  opts.stationary_tol = 0.0;
  CHECK_FALSE(opts.stationarity_enabled());
  opts.stationary_tol = 0.005;
  opts.stationary_window = 0;
  CHECK_FALSE(opts.stationarity_enabled());
  opts.stationary_window = 4;
  opts.convergence_mode = ConvergenceMode::gap_only;
  CHECK_FALSE(opts.stationarity_enabled());
  opts.convergence_mode = ConvergenceMode::statistical;
  CHECK(opts.stationarity_enabled());
  static_assert(!sddp_gap_converged(-1.0, 1e-4));
}

TEST_CASE(  // NOLINT
    "SDDP gap test stops the run when stationarity is disabled")
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  SDDPMethod sddp(planning_lp, gap_only_opts());
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());

  const auto idx = first_converged(*results);
  REQUIRE(idx >= 0);
  CHECK(idx < kMaxIterations - 1);

  const auto& ir = (*results)[static_cast<std::size_t>(idx)];
  CHECK(sddp_gap_converged(ir.gap, 1e-4));
  CHECK_FALSE(ir.stationary_converged);
  CHECK_FALSE(ir.statistical_converged);

  // No iteration before the converged one had a gap inside the band.
  for (int i = 0; i < idx; ++i) {
    CHECK_FALSE(
        sddp_gap_converged((*results)[static_cast<std::size_t>(i)].gap, 1e-4));
  }
  CHECK(results->back().converged);
}

TEST_CASE("SDDP gap_only mode converges on the gap test")  // NOLINT
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  auto opts = gap_only_opts();
  opts.stationary_tol = 0.005;  // ignored: gap_only disables stationarity
  opts.convergence_mode = ConvergenceMode::gap_only;

  SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());

  const auto idx = first_converged(*results);
  REQUIRE(idx >= 0);
  CHECK(idx < kMaxIterations - 1);
  const auto& ir = (*results)[static_cast<std::size_t>(idx)];
  CHECK(sddp_gap_converged(ir.gap, 1e-4));
  CHECK_FALSE(ir.stationary_converged);
}

TEST_CASE(  // NOLINT
    "SDDP negative convergence_tol disables the gap test")
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  auto opts = gap_only_opts();
  opts.max_iterations = 6;
  opts.convergence_tol = -1.0;

  SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  CHECK(first_converged(*results) == -1);
  CHECK(std::ssize(*results) >= opts.max_iterations);
}

TEST_CASE(  // NOLINT
    "SDDP multi-scene synchronous run converges on the gap test")
{
  auto planning = make_2scene_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  auto opts = gap_only_opts();
  opts.max_async_spread = 0;  // force the synchronous iterate() path

  SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());

  const auto idx = first_converged(*results);
  REQUIRE(idx >= 0);
  CHECK(idx < kMaxIterations - 1);
  CHECK(
      sddp_gap_converged((*results)[static_cast<std::size_t>(idx)].gap, 1e-4));
}

TEST_CASE(  // NOLINT
    "SDDP gap is not a stop signal while stationarity is enabled")
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  auto opts = gap_only_opts();
  opts.stationary_tol = 0.005;  // default ΔUB stationarity
  opts.stationary_window = 4;
  REQUIRE(opts.stationarity_enabled());

  SDDPMethod sddp(planning_lp, opts);
  // The 3-phase fixture reaches UB == LB at the second iteration with a
  // UB that never moves afterwards, so ΔUB would fire at the same time
  // as the gap.  Seed the ΔUB look-back (the cascade cross-level seed)
  // with a far-away UB so the window only becomes stationary once it
  // has rolled past the seed — i.e. at in-level iteration index 4 —
  // while the gap is already inside the band from iteration 1 on.
  constexpr auto kSeedDepth = 4;
  sddp.seed_prior_bounds(
      std::vector<SDDPMethod::PriorIterBounds>(kSeedDepth,
                                               SDDPMethod::PriorIterBounds {
                                                   .upper_bound = 1.0e12,
                                                   .lower_bound = 0.0,
                                               }));

  auto results = sddp.solve();
  REQUIRE(results.has_value());

  const auto first_gap = std::ranges::find_if(
      *results,
      [&](const SDDPIterationResult& ir)
      { return sddp_gap_converged(ir.gap, opts.convergence_tol); });
  REQUIRE(first_gap != results->end());
  const auto k_gap =
      static_cast<int>(std::distance(results->begin(), first_gap));
  REQUIRE(k_gap < kSeedDepth);

  // Gap inside the band, yet the run continues: the gap is not a stop
  // signal while stationarity is enabled.
  CHECK_FALSE(first_gap->converged);
  CHECK(first_gap->gap_change >= opts.stationary_tol);

  const auto k_conv = first_converged(*results);
  REQUIRE(k_conv >= 0);
  CHECK(k_conv > k_gap);
  CHECK(k_conv == kSeedDepth);  // first window clear of the seed
  const auto& ir = (*results)[static_cast<std::size_t>(k_conv)];
  CHECK(ir.stationary_converged);
  CHECK(ir.gap_change < opts.stationary_tol);
}
