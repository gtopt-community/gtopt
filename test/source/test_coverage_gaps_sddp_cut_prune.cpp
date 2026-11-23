// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_coverage_gaps_sddp_cut_prune.cpp
 * @brief     Drive SDDPCutManager::prune_inactive_cuts / cap_stored_cuts
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Coverage audit 2026-11-22: `prune_inactive_cuts` (sddp_cut_store.cpp
 * 315-458) and the dropping branch of `cap_stored_cuts` (469-485) had
 * zero hits.  The existing "cut pruning bounds stored cuts" test in
 * test_sddp_method.cpp never reaches the prune trigger (it converges
 * before iteration `cut_prune_interval`), so its `<= 30` check is
 * vacuous.  These tests call the cut manager directly after a real
 * SDDP solve so the row-deletion + StoredCut row-remapping logic is
 * exercised deterministically.
 */

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <map>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_cut_store.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace test_coverage_gaps_sddp_cut_prune_ns
{
namespace
{

/// Solve the 3-phase hydro fixture for exactly @p iters iterations
/// (convergence disabled) with pruning / capping turned off, so the
/// test can invoke them by hand afterwards.
void run_fixed_iterations(SDDPMethod& sddp, int iters)
{
  auto& opts = sddp.mutable_options();
  opts.max_iterations = iters;
  opts.min_iterations = iters;
  opts.convergence_tol = -1.0;  // empty convergence band → never converge
  opts.stationary_tol = 0.0;  // disable ΔUB stationarity
  opts.max_cuts_per_phase = 0;
  opts.max_stored_cuts = 0;
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
}

/// Map PhaseUid → PhaseIndex for the planning's single scene.
[[nodiscard]] auto phase_index_by_uid(const PlanningLP& planning_lp)
    -> std::map<PhaseUid, PhaseIndex>
{
  std::map<PhaseUid, PhaseIndex> out;
  const auto& phases = planning_lp.simulation().phases();
  for (std::size_t i = 0; i < phases.size(); ++i) {
    out.emplace(phases[i].uid(), PhaseIndex {static_cast<Index>(i)});
  }
  return out;
}

}  // namespace
}  // namespace test_coverage_gaps_sddp_cut_prune_ns

using test_coverage_gaps_sddp_cut_prune_ns::phase_index_by_uid;
using test_coverage_gaps_sddp_cut_prune_ns::run_fixed_iterations;

TEST_CASE(
    "SDDPCutManager::prune_inactive_cuts deletes rows and remaps cut rows")
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  SDDPOptions sddp_opts;
  sddp_opts.enable_api = false;
  SDDPMethod sddp(planning_lp, sddp_opts);
  run_fixed_iterations(sddp, 4);

  const auto& states = sddp.all_scene_phase_states();
  const auto first_scene = SceneIndex {0};
  const auto num_phases = planning_lp.simulation().phase_count();

  // Cut rows per phase LP before pruning.
  std::vector<int> cut_rows_before;
  for (Index p = 0; p < static_cast<Index>(num_phases); ++p) {
    const auto pi = PhaseIndex {p};
    const auto& li = planning_lp.system(first_scene, pi).linear_interface();
    cut_rows_before.push_back(static_cast<int>(
        li.get_numrows()
        - static_cast<Index>(states[first_scene][pi].base_nrows)));
  }
  const auto max_before = *std::ranges::max_element(cut_rows_before);
  // Four iterations must have produced more than one cut on some phase,
  // otherwise the prune path is not reached and the test is vacuous.
  REQUIRE(max_before > 1);

  const auto stored_before = sddp.num_stored_cuts();
  REQUIRE(stored_before > 0);

  // Keep at most one cut per (scene, phase); an enormous dual threshold
  // makes every surplus cut eligible.
  SDDPOptions prune_opts = sddp.options();
  prune_opts.max_cuts_per_phase = 1;
  prune_opts.prune_dual_threshold = 1e300;

  sddp.cut_manager().prune_inactive_cuts(prune_opts, planning_lp, states);

  int total_removed = 0;
  for (Index p = 0; p < static_cast<Index>(num_phases); ++p) {
    const auto pi = PhaseIndex {p};
    const auto& li = planning_lp.system(first_scene, pi).linear_interface();
    const auto cut_rows_after = static_cast<int>(
        li.get_numrows()
        - static_cast<Index>(states[first_scene][pi].base_nrows));
    const auto before = cut_rows_before[static_cast<std::size_t>(p)];
    CAPTURE(p);
    CAPTURE(before);
    CAPTURE(cut_rows_after);
    if (before > 1) {
      CHECK(cut_rows_after == 1);
    } else {
      CHECK(cut_rows_after == before);
    }
    total_removed += before - cut_rows_after;
  }
  CHECK(total_removed > 0);

  // Every stored cut that survived must point at a live cut row of its
  // own phase LP, and the LP row must still carry the cut's RHS (this
  // pins the row-shift bookkeeping after delete_rows).
  const auto phase_map = phase_index_by_uid(planning_lp);
  const auto remaining = sddp.stored_cuts();
  CHECK(std::ssize(remaining) <= stored_before);
  for (const auto& cut : remaining) {
    const auto it = phase_map.find(cut.phase_uid);
    REQUIRE(it != phase_map.end());
    const auto pi = it->second;
    const auto& li = planning_lp.system(first_scene, pi).linear_interface();
    const auto base = static_cast<Index>(states[first_scene][pi].base_nrows);
    const auto row = static_cast<Index>(cut.row);
    CAPTURE(row);
    CHECK(row >= base);
    CHECK(row < li.get_numrows());
    if (row >= base && row < li.get_numrows()) {
      const auto row_low = li.get_row_low();
      CHECK(row_low[cut.row] == doctest::Approx(cut.rhs).epsilon(1e-6));
    }
  }

  // The pruned LPs must still solve.
  for (Index p = 0; p < static_cast<Index>(num_phases); ++p) {
    auto& li =
        planning_lp.system(first_scene, PhaseIndex {p}).linear_interface();
    auto r = li.resolve();
    REQUIRE(r.has_value());
    CHECK(li.is_optimal());
  }
}

TEST_CASE(
    "SDDPCutManager::prune_inactive_cuts is a no-op when nothing exceeds "
    "the cap or the threshold")
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  SDDPOptions sddp_opts;
  sddp_opts.enable_api = false;
  SDDPMethod sddp(planning_lp, sddp_opts);
  run_fixed_iterations(sddp, 3);

  const auto& states = sddp.all_scene_phase_states();
  const auto stored_before = sddp.num_stored_cuts();

  SUBCASE("max_cuts_per_phase <= 0 disables pruning")
  {
    SDDPOptions o = sddp.options();
    o.max_cuts_per_phase = 0;
    sddp.cut_manager().prune_inactive_cuts(o, planning_lp, states);
    CHECK(sddp.num_stored_cuts() == stored_before);
  }

  SUBCASE("zero dual threshold keeps every cut (|dual| < 0 never holds)")
  {
    SDDPOptions o = sddp.options();
    o.max_cuts_per_phase = 1;
    o.prune_dual_threshold = 0.0;
    sddp.cut_manager().prune_inactive_cuts(o, planning_lp, states);
    CHECK(sddp.num_stored_cuts() == stored_before);
  }
}

TEST_CASE("SDDPCutManager::cap_stored_cuts drops the oldest stored cuts")
{
  auto planning = make_3phase_hydro_planning();
  PlanningLP planning_lp(std::move(planning));

  SDDPOptions sddp_opts;
  sddp_opts.enable_api = false;
  SDDPMethod sddp(planning_lp, sddp_opts);
  run_fixed_iterations(sddp, 4);

  const auto before = sddp.stored_cuts();
  REQUIRE(std::ssize(before) > 2);

  SDDPOptions o = sddp.options();
  o.max_stored_cuts = 2;
  sddp.cut_manager().cap_stored_cuts(o, planning_lp);

  const auto after = sddp.stored_cuts();
  REQUIRE(after.size() == 2);
  // The survivors are the two most recent entries, in order.
  const auto n = before.size();
  CHECK(after[0].row == before[n - 2].row);
  CHECK(after[0].phase_uid == before[n - 2].phase_uid);
  CHECK(after[1].row == before[n - 1].row);
  CHECK(after[1].phase_uid == before[n - 1].phase_uid);

  SUBCASE("max_stored_cuts <= 0 is a no-op")
  {
    o.max_stored_cuts = 0;
    sddp.cut_manager().cap_stored_cuts(o, planning_lp);
    CHECK(sddp.num_stored_cuts() == 2);
  }
}

// Coverage audit 2026-11-22: the forward-pass installation of PLP-family
// feasibility cuts (`elastic_filter_mode = state_repair` /
// `farkas_recursive`, sddp_forward_pass.cpp 761-842) had zero hits — the
// cut builders are unit-tested in isolation (test_plp_feasibility_cuts,
// test_farkas_recursive_cuts) but no SDDP run ever installed them.
TEST_CASE("SDDP forward pass installs PLP-family feasibility cuts end-to-end")
{
  for (const auto mode :
       {
           ElasticFilterMode::state_repair,
           ElasticFilterMode::farkas_recursive,
       })
  {
    CAPTURE(static_cast<int>(mode));
    auto planning = make_forced_infeasibility_planning();
    PlanningLP planning_lp(std::move(planning));

    SDDPOptions sddp_opts;
    sddp_opts.max_iterations = 10;
    sddp_opts.convergence_tol = 1e-4;
    sddp_opts.elastic_filter_mode = mode;
    sddp_opts.enable_api = false;

    SDDPMethod sddp(planning_lp, sddp_opts);
    [[maybe_unused]] auto results = sddp.solve();

    int feas_cuts = 0;
    for (const auto& cut : sddp.stored_cuts()) {
      if (cut.type == CutType::Feasibility) {
        ++feas_cuts;
        // A feasibility cut with no state coefficient would be a constant
        // row: either vacuous or an infeasible-by-construction LP.
        CHECK_FALSE(cut.coefficients.empty());
        for (const auto& [col, coeff] : cut.coefficients) {
          CHECK(std::isfinite(coeff));
        }
        CHECK(std::isfinite(cut.rhs));
      }
    }
    // The fixture forces phase 1 infeasible on the first forward pass.
    CHECK(feas_cuts > 0);
  }
}
