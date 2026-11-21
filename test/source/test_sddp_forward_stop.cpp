// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_forward_stop.cpp
 * @brief     A stop/sentinel hit mid-forward-pass is not an infeasibility
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `forward_pass` returns a "cancelled" error when `should_stop()` fires
 * at a phase head.  Before the fix the dispatcher treated it like any
 * failed scene: scene marked infeasible, its accumulated cuts ROLLED
 * BACK under `forward_infeas_rollback`, and — when every scene was
 * cancelled — the whole solve aborted with "all scenes infeasible in
 * forward pass".  A cooperative stop must instead discard the partial
 * iteration and fall through to the simulation pass / output tail with
 * the cuts intact.
 */

#include <atomic>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

struct StopRun
{
  bool solved {false};
  std::string error {};
  std::ptrdiff_t cuts_after_iter1 {0};
  std::ptrdiff_t cuts_at_end {0};
  int observer_calls {0};
  bool any_feasibility_issue {false};
};

/// Run SDDP on the 2-scene × 3-phase hydro fixture and request a stop
/// from inside the forward pass of iteration 2 (the first observer call
/// after iteration 1's 2 × 3 phase heads), i.e. strictly mid-pass.
[[nodiscard]] StopRun run_with_mid_forward_stop(int max_async_spread)
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.sddp_options.forward_infeas_rollback = true;
  PlanningLP plp(std::move(planning));

  SDDPOptions opts;
  opts.max_iterations = 4;
  opts.min_iterations = 4;  // never converge before the stop
  opts.convergence_tol = 0.0;
  opts.enable_api = false;
  opts.forward_infeas_rollback = true;
  opts.max_async_spread = max_async_spread;
  SDDPMethod sddp(plp, opts);

  std::atomic<int> calls {0};
  constexpr int kIter1PhaseHeads = 2 * 3;
  sddp.set_forward_phase_observer(
      [&](SceneIndex /*scene*/, PhaseIndex phase)
      {
        const int n = ++calls;
        if (n > kIter1PhaseHeads && phase > PhaseIndex {0}) {
          sddp.request_stop();
        }
      });

  StopRun out;
  sddp.set_iteration_callback(
      [&](const SDDPIterationResult& ir)
      {
        if (out.cuts_after_iter1 == 0) {
          out.cuts_after_iter1 = sddp.num_stored_cuts();
        }
        out.any_feasibility_issue |= ir.feasibility_issue;
        return false;
      });

  auto result = sddp.solve();
  out.solved = result.has_value();
  if (!result.has_value()) {
    out.error = result.error().message;
  } else {
    for (const auto& ir : *result) {
      out.any_feasibility_issue |= ir.feasibility_issue;
    }
  }
  out.cuts_at_end = sddp.num_stored_cuts();
  out.observer_calls = calls.load();
  return out;
}

}  // namespace

TEST_CASE(  // NOLINT
    "SDDP forward stop - mid-pass stop keeps cuts and finishes (sync)")
{
  const auto r = run_with_mid_forward_stop(/*max_async_spread=*/0);
  INFO("error: " << r.error);
  REQUIRE(r.solved);
  CHECK(r.observer_calls > 2 * 3);  // the stop really fired mid-forward
  CHECK(r.cuts_after_iter1 > 0);
  // No rollback: every iteration-1 cut survives the cancelled pass.
  CHECK(r.cuts_at_end >= r.cuts_after_iter1);
  CHECK_FALSE(r.any_feasibility_issue);
}

TEST_CASE(  // NOLINT
    "SDDP forward stop - mid-pass stop keeps cuts and finishes (async)")
{
  const auto r = run_with_mid_forward_stop(/*max_async_spread=*/1);
  INFO("error: " << r.error);
  REQUIRE(r.solved);
  CHECK(r.observer_calls > 2 * 3);
  CHECK(r.cuts_at_end > 0);
  CHECK_FALSE(r.any_feasibility_issue);
}
