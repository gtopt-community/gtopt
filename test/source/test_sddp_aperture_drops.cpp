// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_aperture_drops.cpp
 * @brief     Persistent aperture infeasibility: tracking + convergence gate
 * @date      2026-11-24
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Issue #551: an infeasible backward-pass aperture is dropped and the
 * expected cut renormalised over the remaining ones.  Without relatively
 * complete recourse (hard `efin`, no `efin_cost`) the same aperture is
 * dropped every iteration, so the bounds describe a rescaled
 * distribution.  The solver must (1) escalate one persistent-infeasibility
 * warning per (scene, phase, aperture), (2) report the per-iteration drop
 * count, and (3) never report `converged` on an iteration that dropped
 * apertures.
 */

#include <algorithm>
#include <array>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_aperture_drops.hpp>
#include <gtopt/sddp_method.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

TEST_CASE("ApertureDropTracker - streaks, escalation, counts")  // NOLINT
{
  ApertureDropTracker tracker(/*warn_after=*/3);
  const auto s1 = make_uid<Scene>(1);
  const auto s2 = make_uid<Scene>(2);
  const auto p5 = make_uid<Phase>(5);
  const std::array<Uid, 1> ap3 {Uid {3}};
  const std::array<Uid, 2> ap34 {Uid {3}, Uid {4}};

  SUBCASE("empty record is a no-op")
  {
    const auto rec =
        tracker.record(s1, p5, IterationIndex {0}, std::span<const Uid> {});
    CHECK(rec.newly_persistent.empty());
    CHECK_FALSE(rec.all_already_persistent);
    CHECK(tracker.total_drops() == 0);
    CHECK(tracker.drops_in_iteration(IterationIndex {0}) == 0);
  }

  SUBCASE("escalates once after warn_after consecutive iterations")
  {
    CHECK(tracker.record(s1, p5, IterationIndex {0}, ap3)
              .newly_persistent.empty());
    CHECK(tracker.record(s1, p5, IterationIndex {1}, ap3)
              .newly_persistent.empty());
    const auto third = tracker.record(s1, p5, IterationIndex {2}, ap3);
    REQUIRE(third.newly_persistent.size() == 1);
    CHECK(third.newly_persistent.front().aperture_uid == 3);
    CHECK(third.newly_persistent.front().consecutive == 3);
    CHECK_FALSE(third.all_already_persistent);
    CHECK(tracker.persistent_count() == 1);

    // Fourth drop: already escalated → no new warning, per-cell message
    // may be demoted.
    const auto fourth = tracker.record(s1, p5, IterationIndex {3}, ap3);
    CHECK(fourth.newly_persistent.empty());
    CHECK(fourth.all_already_persistent);
    CHECK(tracker.persistent_count() == 1);

    // A new aperture in the same cell is not yet persistent.
    const auto mixed = tracker.record(s1, p5, IterationIndex {4}, ap34);
    CHECK(mixed.newly_persistent.empty());
    CHECK_FALSE(mixed.all_already_persistent);
    CHECK(tracker.total_drops() == 6);
  }

  SUBCASE("a feasible iteration breaks the streak")
  {
    (void)tracker.record(s1, p5, IterationIndex {0}, ap3);
    (void)tracker.record(s1, p5, IterationIndex {1}, ap3);
    // Iteration 2: aperture solved (nothing recorded).
    (void)tracker.record(s1, p5, IterationIndex {3}, ap3);
    CHECK(tracker.record(s1, p5, IterationIndex {4}, ap3)
              .newly_persistent.empty());
    CHECK(tracker.persistent_count() == 0);
    CHECK(
        tracker.record(s1, p5, IterationIndex {5}, ap3).newly_persistent.size()
        == 1);
  }

  SUBCASE("streaks are per (scene, phase, aperture); counts per iteration")
  {
    (void)tracker.record(s1, p5, IterationIndex {0}, ap3);
    (void)tracker.record(s2, p5, IterationIndex {0}, ap34);
    (void)tracker.record(s1, make_uid<Phase>(6), IterationIndex {0}, ap3);
    // Duplicate record in the same iteration counts once.
    (void)tracker.record(s1, p5, IterationIndex {0}, ap3);
    CHECK(tracker.drops_in_iteration(IterationIndex {0}) == 4);
    CHECK(tracker.scene_drops_in_iteration(s1, IterationIndex {0}) == 2);
    CHECK(tracker.scene_drops_in_iteration(s2, IterationIndex {0}) == 2);
    CHECK(tracker.scene_drops_in_iteration(s2, IterationIndex {1}) == 0);
    CHECK(tracker.drops_in_iteration(IterationIndex {1}) == 0);

    tracker.reset();
    CHECK(tracker.total_drops() == 0);
    CHECK(tracker.drops_in_iteration(IterationIndex {0}) == 0);
  }

  SUBCASE("warn_after is clamped to at least 1")
  {
    ApertureDropTracker eager(0);
    CHECK(eager.warn_after() == 1);
    CHECK(eager.record(s1, p5, IterationIndex {0}, ap3).newly_persistent.size()
          == 1);
  }
}

TEST_CASE("ApertureDropTracker - end-of-solve summary")  // NOLINT
{
  ApertureDropTracker tracker(/*warn_after=*/2);
  const auto s1 = make_uid<Scene>(1);
  const auto p5 = make_uid<Phase>(5);
  const auto p6 = make_uid<Phase>(6);
  const std::array<ApertureDrop, 2> two {
      ApertureDrop {
          .aperture_uid = Uid {3},
          .infeasible = true,
      },
      ApertureDrop {
          .aperture_uid = Uid {4},
          .infeasible = false,  // time limit / numerical
      },
  };
  const std::array<ApertureDrop, 1> ap3 {
      ApertureDrop {
          .aperture_uid = Uid {3},
          .infeasible = true,
      },
  };

  SUBCASE("empty tracker")
  {
    const auto sum = tracker.summary();
    CHECK(sum.solves == 0);
    CHECK(sum.dropped() == 0);
    CHECK(sum.dropped_fraction() == doctest::Approx(0.0));
    CHECK_FALSE(sum.last.has_value());
  }

  SUBCASE("counts, split, last iteration and top lists")
  {
    // i0: p5 drops a3 (infeasible) + a4 (failed) of 4; p6 solves 4 clean.
    (void)tracker.record(s1, p5, IterationIndex {0}, 4, two);
    (void)tracker.record(s1, p6, IterationIndex {0}, 4, {});
    // i1: p5 drops a3 again of 4; p6 clean.
    (void)tracker.record(s1, p5, IterationIndex {1}, 4, ap3);
    (void)tracker.record(s1, p6, IterationIndex {1}, 4, {});

    const auto sum = tracker.summary();
    CHECK(sum.solves == 16);
    CHECK(sum.infeasible == 2);
    CHECK(sum.failed == 1);
    CHECK(sum.dropped_fraction() == doctest::Approx(3.0 / 16.0));
    CHECK(sum.iterations == 2);
    CHECK(sum.distinct_dropped == 2);  // (s1, p5, a3), (s1, p5, a4)
    CHECK(sum.dropped_in_last_iteration == 1);  // only a3 in i1
    CHECK(sum.persistent == 1);  // a3: 2 consecutive (warn_after = 2)
    REQUIRE(sum.first.has_value());
    REQUIRE(sum.last.has_value());
    CHECK(sum.first.value_or(ApertureIterationCounts {}).dropped() == 2);
    CHECK(sum.last.value_or(ApertureIterationCounts {}).dropped() == 1);
    CHECK(sum.last.value_or(ApertureIterationCounts {}).solves == 8);
    REQUIRE(sum.top_phases.size() == 1);
    CHECK(sum.top_phases.front().uid == 5);
    CHECK(sum.top_phases.front().drops == 3);
    REQUIRE(sum.top_apertures.size() == 2);
    CHECK(sum.top_apertures.front().uid == 3);
    CHECK(sum.top_apertures.front().drops == 2);
    CHECK(tracker.drops_in_iteration(IterationIndex {0}) == 2);

    // Logging never throws, with or without drops.
    log_aperture_summary(sum, "test");
    log_aperture_summary(ApertureSummary {}, "test");

    // Cascade total.
    ApertureSummary total;
    total.accumulate(sum);
    total.accumulate(sum);
    CHECK(total.solves == 32);
    CHECK(total.infeasible == 4);
    CHECK(total.failed == 2);
    CHECK(total.dropped_in_last_iteration == 1);
    CHECK(total.top_apertures.size() == 2);
  }
}

namespace
{

struct ApertureDropRun
{
  std::vector<SDDPIterationResult> results;
  int persistent {};
  int total_drops {};
  ApertureSummary summary {};
};

auto run_4aperture_fixture(bool soft_efin,
                           const std::string& solver,
                           int max_iterations,
                           int max_async_spread) -> ApertureDropRun
{
  auto planning = make_4aperture_10phase_two_reservoir_planning(soft_efin);
  planning.options.lp_matrix_options.solver_name = solver;
  PlanningLP plp(std::move(planning));
  SDDPOptions sddp_opts;
  sddp_opts.max_iterations = max_iterations;
  sddp_opts.convergence_tol = 1e-4;
  sddp_opts.enable_api = false;
  sddp_opts.apertures = std::nullopt;  // per-phase aperture lists
  sddp_opts.aperture_solve_mode = ApertureSolveMode::warm;
  if (max_async_spread > 0) {
    sddp_opts.cut_sharing = CutSharingMode::none;
    sddp_opts.max_async_spread = max_async_spread;
  }
  SDDPMethod sddp(plp, sddp_opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  return {
      .results = std::move(*results),
      .persistent = sddp.aperture_drop_tracker().persistent_count(),
      .total_drops = sddp.aperture_drop_tracker().total_drops(),
      .summary = sddp.aperture_drop_tracker().summary(),
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "SDDPMethod - hard efin: persistent aperture drop blocks convergence")
{
  const auto solver = pick_non_mindopt_solver();
  if (solver.empty()) {
    MESSAGE("Skipping — only MindOpt available (see pick_non_mindopt_solver)");
    return;
  }

  for (const int spread : {0, 2}) {
    CAPTURE(spread);
    const auto hard = run_4aperture_fixture(
        /*soft_efin=*/false, solver, /*max_iterations=*/20, spread);
    MESSAGE("hard efin spread=",
            spread,
            " iters=",
            hard.results.size(),
            " drops=",
            hard.total_drops,
            " persistent=",
            hard.persistent,
            " lb=",
            hard.results.back().lower_bound,
            " ub=",
            hard.results.back().upper_bound);

    // Regression (#570): the async per-scene and aggregate LB were read
    // after the backward pass, from a phase-0 LP that had just received a
    // cut and was not re-solved, and came out as 0.  Sync and async must
    // both report the real (positive) master objective.
    CHECK(hard.results.back().lower_bound > 0.0);
    for (const double lb : hard.results.back().scene_lower_bounds) {
      CHECK(lb > 0.0);
    }

    // The dry aperture is dropped on consecutive iterations → escalated.
    CHECK(hard.total_drops > 0);
    CHECK(hard.persistent >= 1);

    // Per-iteration drop counts are surfaced, and no iteration that
    // dropped apertures is ever flagged converged.
    int iters_with_drops = 0;
    for (const auto& r : hard.results) {
      if (r.apertures_dropped > 0) {
        ++iters_with_drops;
        CHECK_FALSE(r.converged);
      }
    }
    CHECK(iters_with_drops > 0);
    CHECK_FALSE(hard.results.back().converged);

    // End-of-run aperture statistics: the dry aperture is PROVEN infeasible
    // and still dropped at the end.  A warm solve the backend abandons
    // (HiGHS 1.15 gives up on some warm dual re-solves here, where 1.12
    // proves infeasibility) is retried cold, which proves it, so no drop is
    // counted as a solver failure.
    const auto& sum = hard.summary;
    CHECK(sum.solves > 0);
    CHECK(sum.infeasible > 0);
    CHECK(sum.failed == 0);
    CHECK(sum.persistent > 0);
    CHECK(sum.dropped_in_last_iteration > 0);
    CHECK(sum.last.value_or(ApertureIterationCounts {}).infeasible > 0);
    REQUIRE_FALSE(sum.top_apertures.empty());
    CHECK(sum.top_apertures.front().uid == 3);  // the dry scenario
  }
}

TEST_CASE(  // NOLINT
    "SDDPMethod - soft efin (efin_cost): no persistent drop, converges")
{
  const auto solver = pick_non_mindopt_solver();
  if (solver.empty()) {
    MESSAGE("Skipping — only MindOpt available (see pick_non_mindopt_solver)");
    return;
  }

  const auto soft = run_4aperture_fixture(
      /*soft_efin=*/true,
      solver,
      /*max_iterations=*/200,
      /*max_async_spread=*/0);
  MESSAGE("soft efin iters=",
          soft.results.size(),
          " drops=",
          soft.total_drops,
          " persistent=",
          soft.persistent);
  // Relatively complete recourse on both sides (soft efin + spillway):
  // no aperture LP is ever infeasible.  Before the fixture had a spillway
  // the wet aperture 4 was dropped at iteration 2, phases 8-10, because
  // the forward pass had filled both reservoirs (issue #569).
  CHECK(soft.total_drops == 0);
  CHECK(soft.persistent == 0);
  // Nothing infeasible in the final iteration.
  CHECK(soft.summary.solves > 0);
  CHECK(soft.summary.dropped_in_last_iteration == 0);
  CHECK(soft.summary.last.value_or(ApertureIterationCounts {}).infeasible == 0);
  const auto conv = std::ranges::find_if(
      soft.results, [](const SDDPIterationResult& r) { return r.converged; });
  REQUIRE(conv != soft.results.end());
  CHECK(conv->apertures_dropped == 0);
  CHECK(soft.results.back().converged);
  CHECK(soft.results.back().lower_bound
        == doctest::Approx(soft.results.back().upper_bound).epsilon(1e-3));
}

TEST_CASE(  // NOLINT
    "SDDPMethod - async LB is the forward-pass master objective (#570)")
{
  const auto solver = pick_non_mindopt_solver();
  if (solver.empty()) {
    MESSAGE("Skipping — only MindOpt available (see pick_non_mindopt_solver)");
    return;
  }

  // Feasible fixture (soft efin), async with apertures.  Before any cut
  // the α floor makes the LB legitimately negative, so the check is the
  // bug's signature instead: no LB is exactly 0 (an unsolved phase-0 LP),
  // none exceeds the UB, and the first-iteration LB matches the sync run's
  // (both read the phase-0 objective of the first forward pass).
  const auto async_run = run_4aperture_fixture(
      /*soft_efin=*/true,
      solver,
      /*max_iterations=*/15,
      /*max_async_spread=*/2);
  const auto sync_run = run_4aperture_fixture(
      /*soft_efin=*/true, solver, /*max_iterations=*/1, /*max_async_spread=*/0);
  REQUIRE_FALSE(async_run.results.empty());
  REQUIRE_FALSE(sync_run.results.empty());

  for (const auto& r : async_run.results) {
    CHECK(r.lower_bound != 0.0);
    CHECK(r.lower_bound <= r.upper_bound * (1.0 + 1e-6));
    for (const double lb : r.scene_lower_bounds) {
      CHECK(lb != 0.0);
    }
  }
  CHECK(async_run.results.front().lower_bound
        == doctest::Approx(sync_run.results.front().lower_bound).epsilon(1e-6));
}
