// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_memory_saving_volume_rules.cpp
 * @brief     memory_saving (low_memory) equivalence with volume-dependent
 *            update_lp elements (FlowRight bound_rule, production factor)
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_sddp_off_compress_parity.cpp` pins off↔compress parity on a
 * plain hydro fixture whose LP has NO `update_lp` element, so the
 * volume-dependent coefficient refresh is never exercised there.
 *
 * This file adds the missing generic guard: an irrigation-style
 * `FlowRight` whose `bound_rule` reads the reservoir volume, plus a
 * `ReservoirProductionFactor` (both refreshed by `update_lp` through
 * `average_volume_from_cache`), solved under every `LowMemoryMode`
 * (and both in-memory codecs of `compress`).  The SDDP trajectory —
 * per-iteration LB / UB / per-scene bounds, cut count and the stored
 * cut coefficients — must be identical across modes.
 *
 * The second half pins the silent-default site behind the bug class
 * observed under `--memory-saving compress` on a real case (the gate
 * saw `(v0 + eini_case) / 2`):
 * `physical_efin_from_cache` (include/gtopt/update_context.hpp) falls
 * back to `rc.default_volume` (the case `eini`) whenever the LI is not
 * optimal — and in the production sequence `propagate_trial_values`
 * (which pins the eini column, i.e. a bound mutation) ALWAYS runs
 * right before `update_lp_for_phase`, so the LI is never optimal when
 * the rule is evaluated.
 */

#include <algorithm>
#include <array>
#include <cmath>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/flow_right_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/right_bound_rule.hpp>
#include <gtopt/sddp_enums.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/state_variable.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/update_context.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

constexpr double msv_kCaseEini = 100.0;

/// Irrigation rule: allowed extraction = 0.1 · V  [m³/s], capped at 20.
[[nodiscard]] RightBoundRule msv_make_irrigation_rule()
{
  return RightBoundRule {
      .reservoir = SingleId {Uid {1}},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.1,
                  .constant = 0.0,
              },
          },
      .cap = 20.0,
  };
}

/// `make_2scene_3phase_hydro_planning` (hydro + thermal, one reservoir
/// that drains well below its case `eini` along the horizon) plus two
/// volume-dependent `update_lp` elements:
///   * FlowRight `irr` extracting from the reservoir junction, soft
///     target 10 m³/s (shortfall penalised), hard cap from the
///     volume rule above;
///   * ReservoirProductionFactor on the turbine (head effect).
[[nodiscard]] Planning msv_make_volume_rule_planning()
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);

  // Rising thermal cost per stage (50 → 55 → 60 $/MWh).  With the flat
  // 50 $/MWh of the base fixture the thermal unit is marginal at the
  // same price in every stage, so moving water between stages is free:
  // the forward-pass storage trajectory is a tie and the backend may
  // return ANY optimal vertex.  HiGHS returns a different one warm
  // (`off`, resident basis) than cold (`compress`, rebuilt LP), and the
  // volume rules then diverge for a reason that has nothing to do with
  // the memory mode.  Distinct stage prices make the trajectory unique,
  // so the off == compress parity below is a property of gtopt, not of
  // the solver's vertex choice.
  planning.system.generator_array[1].gcost = std::vector<std::vector<double>> {
      {50.0, 50.0, 50.0, 50.0},
      {55.0, 55.0, 55.0, 55.0},
      {60.0, 60.0, 60.0, 60.0},
  };

  planning.system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "irr",
          .junction_a = Uid {1},
          .direction = -1,
          .fmax = 20.0,
          .target = 10.0,
          .fcost = 500.0,
          .bound_rule = msv_make_irrigation_rule(),
      },
  };

  planning.system.reservoir_production_factor_array = {
      {
          .uid = Uid {1},
          .name = "rpf1",
          .turbine = Uid {1},
          .reservoir = Uid {1},
          .mean_production_factor = 0.9,
          .segments =
              {
                  {
                      .volume = 0.0,
                      .slope = 0.005,
                      .constant = 0.5,
                  },
                  {
                      .volume = 80.0,
                      .slope = 0.0025,
                      .constant = 0.7,
                  },
              },
      },
  };
  return planning;
}

struct msv_RunTrace
{
  std::vector<SDDPIterationResult> iters;
  std::vector<StoredCut> cuts;
};

[[nodiscard]] msv_RunTrace msv_run_volume_rule_sddp(LowMemoryMode mode,
                                                    CompressionCodec codec)
{
  PlanningLP planning_lp(msv_make_volume_rule_planning());

  SDDPOptions opts;
  opts.max_iterations = 15;
  opts.convergence_tol = 1.0e-4;
  opts.enable_api = false;
  opts.low_memory_mode = mode;
  opts.memory_codec = codec;
  opts.cut_sharing = CutSharingMode::none;
  opts.apertures = std::nullopt;

  SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());

  auto cuts = sddp.stored_cuts();
  std::ranges::sort(cuts,
                    [](const StoredCut& a, const StoredCut& b)
                    { return a.key() < b.key(); });
  return {
      .iters = std::move(*results),
      .cuts = std::move(cuts),
  };
}

[[nodiscard]] bool msv_any_lp_solver_available()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return !reg.available_solvers().empty();
}

void msv_check_same_trace(const msv_RunTrace& ref, const msv_RunTrace& got)
{
  constexpr double eps = 1.0e-6;
  REQUIRE(ref.iters.size() == got.iters.size());
  for (std::size_t i = 0; i < ref.iters.size(); ++i) {
    const auto& r = ref.iters[i];
    const auto& g = got.iters[i];
    INFO("iter ", i);
    CHECK(g.lower_bound == doctest::Approx(r.lower_bound).epsilon(eps));
    CHECK(g.upper_bound == doctest::Approx(r.upper_bound).epsilon(eps));
    CHECK(g.cuts_added == r.cuts_added);
    REQUIRE(g.scene_upper_bounds.size() == r.scene_upper_bounds.size());
    for (std::size_t s = 0; s < r.scene_upper_bounds.size(); ++s) {
      INFO("scene ", s);
      CHECK(g.scene_upper_bounds[s]
            == doctest::Approx(r.scene_upper_bounds[s]).epsilon(eps));
    }
    REQUIRE(g.scene_lower_bounds.size() == r.scene_lower_bounds.size());
    for (std::size_t s = 0; s < r.scene_lower_bounds.size(); ++s) {
      INFO("scene ", s);
      CHECK(g.scene_lower_bounds[s]
            == doctest::Approx(r.scene_lower_bounds[s]).epsilon(eps));
    }
  }
  CHECK(got.iters.back().converged == ref.iters.back().converged);

  REQUIRE(ref.cuts.size() == got.cuts.size());
  for (std::size_t k = 0; k < ref.cuts.size(); ++k) {
    const auto& rc = ref.cuts[k];
    const auto& gc = got.cuts[k];
    INFO("cut ", k);
    CHECK(gc.phase_uid == rc.phase_uid);
    CHECK(gc.scene_uid == rc.scene_uid);
    CHECK(gc.rhs * gc.scale == doctest::Approx(rc.rhs * rc.scale).epsilon(eps));
    REQUIRE(gc.coefficients.size() == rc.coefficients.size());
    for (std::size_t j = 0; j < rc.coefficients.size(); ++j) {
      CHECK(gc.coefficients[j].first == rc.coefficients[j].first);
      CHECK(gc.coefficients[j].second
            == doctest::Approx(rc.coefficients[j].second).epsilon(eps));
    }
  }
}

}  // namespace

TEST_CASE(  // NOLINT
    "memory_saving equivalence — volume-dependent FlowRight bound_rule + "
    "production factor: off == compress (zstd, lz4, uncompressed)")
{
  if (!msv_any_lp_solver_available()) {
    MESSAGE("skip: no LP solver plugin available");
    return;
  }

  const auto ref = msv_run_volume_rule_sddp(LowMemoryMode::off,
                                            CompressionCodec::auto_select);
  REQUIRE_FALSE(ref.cuts.empty());
  // The fixture must actually make the rule bind: the reservoir drains
  // far from its case eini so the evaluated bound moves.
  CHECK(ref.iters.back().upper_bound > 0.0);

  SUBCASE("compress / zstd")
  {
    msv_check_same_trace(ref,
                         msv_run_volume_rule_sddp(LowMemoryMode::compress,
                                                  CompressionCodec::zstd));
  }
  SUBCASE("compress / lz4")
  {
    msv_check_same_trace(ref,
                         msv_run_volume_rule_sddp(LowMemoryMode::compress,
                                                  CompressionCodec::lz4));
  }
  SUBCASE("compress / uncompressed")
  {
    msv_check_same_trace(
        ref,
        msv_run_volume_rule_sddp(LowMemoryMode::compress,
                                 CompressionCodec::uncompressed));
  }
}

// ─── Silent-default site: physical_efin_from_cache ─────────────────────────
//
// Mirrors the production sequence of the SDDP forward pass
// (sddp_forward_pass.cpp: `propagate_trial_values` → `update_lp_for_phase`):
// the phase LP was solved in the previous iteration, then the eini
// column is pinned to the new trial value, then the rule is evaluated.
// The pin is a bound mutation, which clears the LI's optimal flag in
// every low_memory mode, so `physical_efin_from_cache` returns the case
// `eini` instead of any solved volume: the rule axis becomes
// `(trial_vini + eini_case) / 2` — exactly the observed `(v0 + 683)/2`.
//
// Fixed by `solved_stage_state` (the efin StateVariable written by
// `capture_state_variable_values` after every forward solve survives any
// LP mutation): the CHECKs encode the contract "never the case eini once
// a solution has existed".
TEST_CASE(  // NOLINT
    "physical_efin_from_cache — reads the solved efin, not the case eini, "
    "after the propagate pin (real forward-pass sequence)")
{
  if (!msv_any_lp_solver_available()) {
    MESSAGE("skip: no LP solver plugin available");
    return;
  }

  for (const auto mode : {LowMemoryMode::off, LowMemoryMode::compress}) {
    INFO("low_memory_mode=", static_cast<int>(mode));
    PlanningLP planning_lp(msv_make_volume_rule_planning());
    auto& sys = planning_lp.system(first_scene_index(), PhaseIndex {1});
    auto& li = sys.linear_interface();
    li.set_low_memory(mode, CompressionCodec::zstd);
    REQUIRE(li.resolve().has_value());
    REQUIRE(li.is_optimal());

    const auto& rsv = sys.elements<ReservoirLP>().front();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().front();
    const auto cache = make_reservoir_ref_cache(rsv, scenario, stage);
    REQUIRE(cache.default_volume == doctest::Approx(msv_kCaseEini));

    const double solved_efin = li.get_col_sol()[cache.efin_col];
    // The drained trajectory must differ from the case eini, otherwise
    // the fallback is invisible.
    REQUIRE(std::abs(solved_efin - msv_kCaseEini) > 1.0);

    // Sanity: before any mutation the helper reads the solution.
    CHECK(physical_efin_from_cache(sys, scenario, stage, cache)
          == doctest::Approx(solved_efin));

    // capture_state_variable_values: the forward pass writes the solved
    // efin into the stage's StateVariable right after the solve.
    {
      static constexpr auto cname = ReservoirLP::Element::class_name;
      auto svar = planning_lp.simulation().state_variable(
          StateVariable::key(scenario, stage, cname, rsv.uid(), "efin"));
      REQUIRE(svar.has_value());
      if (svar.has_value()) {
        const auto& sv = svar->get();
        sv.set_col_sol(solved_efin / sv.var_scale());
      }
    }

    // propagate_trial_values: pin eini to a new trial value ('B').
    const std::array<ColIndex, 1> idx {cache.eini_col};
    const std::array<char, 1> lu {'B'};
    const std::array<double, 1> trial {20.0};
    li.set_col_bounds(idx, lu, trial);

    const double vfin = physical_efin_from_cache(sys, scenario, stage, cache);
    // Contract: once the cell has been solved, the efin estimate is
    // never the static case eini.
    CHECK(vfin != doctest::Approx(msv_kCaseEini));
    CHECK(vfin == doctest::Approx(solved_efin));
  }
}
