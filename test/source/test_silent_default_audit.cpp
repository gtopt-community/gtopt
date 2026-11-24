// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_silent_default_audit.cpp
 * @brief     Audit of silent fallbacks to the case eini / default volume
 *            when a solution is unavailable (reservoir state readers)
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * One test per fallback site, each proving whether the site is reached
 * on a real SDDP solve path (the sequence is reproduced step by step:
 * solve → mutation (cut row / propagate pin) → update_lp read).
 *
 * Sites (include/gtopt/update_context.hpp):
 *
 *  S1 `physical_efin_from_cache` → `rc.default_volume` when the LI is
 *     not optimal.  Covered in test_sddp_memory_saving_volume_rules.cpp.
 *  S2 `physical_eini_from_cache` step 3 → `rc.default_volume` for an
 *     intra-phase stage (stage index > 0) of phase 0 once the phase LP
 *     has been mutated after its solve (backward-pass cut rows do this
 *     on every iteration ≥ 1).  The LP still knows the volume (the
 *     previous stage's efin) — the fallback discards it.
 *  S3 `physical_eini_from_cache` step 1 for an intra-phase stage of a
 *     phase > 0: returns the PREDECESSOR PHASE's efin StateVariable
 *     even though the stage's own incoming volume is the previous
 *     stage of the SAME phase.  Wrong even with an optimal LP.
 *  S4 `build_time_reservoir_volume`: a predecessor that drained to
 *     exactly 0 is indistinguishable from "not yet solved" (`v > 0`
 *     guard), so the case eini is used at (re)build time.
 *
 * The CHECKs encode the correct contract; sites that currently violate
 * it were reported as KNOWN BUGs; all four are fixed by
 * `solved_stage_state` (refactor/index-types-no-silent-defaults).  Positive
 * controls (the non-fallback branch) are plain tests.
 */

#include <array>
#include <cmath>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/state_variable.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/update_context.hpp>

#include "fixture_helpers.hpp"

using namespace gtopt;
using gtopt::test_fixtures::make_uniform_blocks;
using gtopt::test_fixtures::make_uniform_stages;

namespace
{

constexpr double sda_kCaseEini = 100.0;

/// 2 phases × 2 stages × 4 one-hour blocks, one reservoir (eini 100)
/// that drains 5 per block (inflow 10, turbine forced to 15 by the
/// cheap hydro unit against an 80 MW load): stage efins 80 / 60 / 40 /
/// 20 when solved phase by phase.
[[nodiscard]] Planning sda_make_two_stage_phase_planning()
{
  Array<Phase> phase_array = {
      {
          .uid = Uid {1},
          .first_stage = 0,
          .count_stage = 2,
      },
      {
          .uid = Uid {2},
          .first_stage = 2,
          .count_stage = 2,
      },
  };

  Simulation simulation = {
      .block_array = make_uniform_blocks(16, 1.0),
      .stage_array = make_uniform_stages(4, 4),
      .scenario_array = {{.uid = Uid {1}}},
      .phase_array = std::move(phase_array),
  };

  System system = {
      .name = "silent_default_audit",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .capacity = 80.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "hydro_gen",
                  .bus = Uid {1},
                  .gcost = 5.0,
                  .capacity = 15.0,
              },
              {
                  .uid = Uid {2},
                  .name = "thermal_gen",
                  .bus = Uid {1},
                  .gcost = 50.0,
                  .capacity = 200.0,
              },
          },
      .junction_array =
          {
              {.uid = Uid {1}, .name = "j_up"},
              {.uid = Uid {2}, .name = "j_down", .drain = true},
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww1",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 100.0,
              },
          },
      .flow_array =
          {
              {
                  .uid = Uid {1},
                  .name = "inflow",
                  .direction = 1,
                  .junction = Uid {1},
                  .discharge = 10.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv1",
                  .junction = Uid {1},
                  .capacity = 500.0,
                  .emin = 0.0,
                  .emax = 500.0,
                  .eini = sda_kCaseEini,
                  .fmin = -1000.0,
                  .fmax = 1000.0,
                  .flow_conversion_rate = 1.0,
                  .use_state_variable = true,
                  .daily_cycle = false,
              },
          },
      .turbine_array =
          {
              {
                  .uid = Uid {1},
                  .name = "tur1",
                  .waterway = Uid {1},
                  .generator = Uid {1},
                  .production_factor = 1.0,
              },
          },
  };

  PlanningOptions options;
  options.model_options.demand_fail_cost = 1000.0;
  options.model_options.use_single_bus = OptBool {true};
  options.model_options.scale_objective = OptReal {1.0};

  return Planning {
      .options = std::move(options),
      .simulation = std::move(simulation),
      .system = std::move(system),
  };
}

/// A bound mutation that leaves the LP unchanged in value: re-set the
/// upper bound of `col` to its current upper bound.  It still clears
/// the LI's optimal flag — the same effect as a backward-pass cut row
/// or a `propagate_trial_values` pin in production.
void sda_touch_col_upper(LinearInterface& li, ColIndex col)
{
  const double upp = li.get_col_upp()[col];
  li.set_col_upp(col, upp);
}

/// Write `phys` into the efin StateVariable of `(scenario, stage)` the
/// way `capture_state_variable_values` does after a forward solve.
void sda_set_efin_state(PlanningLP& planning_lp,
                        const ScenarioLP& scenario,
                        const StageLP& stage,
                        Uid rsv_uid,
                        double phys)
{
  static constexpr auto cname = ReservoirLP::Element::class_name;
  auto svar = planning_lp.simulation().state_variable(
      StateVariable::key(scenario, stage, cname, rsv_uid, "efin"));
  REQUIRE(svar.has_value());
  if (svar.has_value()) {
    const auto& sv = svar->get();
    sv.set_col_sol(phys / sv.var_scale());
  }
}

}  // namespace

// ─── S2 ────────────────────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "silent-default S2 positive control — intra-phase eini read from the "
    "solved LP")
{
  PlanningLP planning_lp(sda_make_two_stage_phase_planning());
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  auto& li0 = sys0.linear_interface();
  REQUIRE(li0.resolve().has_value());
  REQUIRE(li0.is_optimal());

  const auto& rsv = sys0.elements<ReservoirLP>().front();
  const auto& scenario = sys0.scene().scenarios().front();
  const auto& st0 = sys0.phase().stages()[0];
  const auto& st1 = sys0.phase().stages()[1];
  const double efin_st0 = li0.get_col_sol()[rsv.efin_col_at(scenario, st0)];
  REQUIRE(std::abs(efin_st0 - sda_kCaseEini) > 1.0);

  const auto cache1 = make_reservoir_ref_cache(rsv, scenario, st1);
  CHECK(physical_eini_from_cache(sys0, scenario, st1, cache1)
        == doctest::Approx(efin_st0));
}

TEST_CASE(  // NOLINT
    "silent-default S2 — intra-phase stage of phase 0 keeps the solved "
    "volume after the solved LP was mutated (cut row)")
{
  PlanningLP planning_lp(sda_make_two_stage_phase_planning());
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  auto& li0 = sys0.linear_interface();
  REQUIRE(li0.resolve().has_value());

  const auto& rsv = sys0.elements<ReservoirLP>().front();
  const auto& scenario = sys0.scene().scenarios().front();
  const auto& st0 = sys0.phase().stages()[0];
  const auto& st1 = sys0.phase().stages()[1];
  const auto efin0_col = rsv.efin_col_at(scenario, st0);
  const double efin_st0 = li0.get_col_sol()[efin0_col];
  REQUIRE(std::abs(efin_st0 - sda_kCaseEini) > 1.0);

  const auto cache1 = make_reservoir_ref_cache(rsv, scenario, st1);

  // capture_state_variable_values writes every stage's efin after the
  // forward solve.
  sda_set_efin_state(planning_lp, scenario, st0, rsv.uid(), efin_st0);

  // Real path: SDDP iteration ≥ 1 — the backward pass added a cut row
  // to phase 0, then the forward pass calls update_lp_for_phase(0)
  // before re-solving.  Any mutation has the same effect on the flag.
  sda_touch_col_upper(li0, efin0_col);
  // CPLEX / HiGHS drop the optimal status on any bound change; OSI
  // (CLP, CBC) keeps it until the next solve.  The readers must
  // return the solved values either way.
  if (li0.is_optimal()) {
    MESSAGE("backend keeps its optimal status after a bound change");
  }

  const double vini = physical_eini_from_cache(sys0, scenario, st1, cache1);
  INFO("efin(stage 0) = ", efin_st0, "  case eini = ", sda_kCaseEini);
  CHECK(vini != doctest::Approx(sda_kCaseEini));
  CHECK(vini == doctest::Approx(efin_st0));
}

// ─── S3 ────────────────────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "silent-default S3 — intra-phase stage of phase > 0 reads its own "
    "previous stage, not the predecessor phase's efin")
{
  PlanningLP planning_lp(sda_make_two_stage_phase_planning());
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  REQUIRE(sys0.linear_interface().resolve().has_value());

  const auto& rsv0 = sys0.elements<ReservoirLP>().front();
  const auto& scenario = sys0.scene().scenarios().front();
  const auto& p0_last = sys0.phase().stages().back();
  const double efin_p0 =
      sys0.linear_interface()
          .get_col_sol()[rsv0.efin_col_at(scenario, p0_last)];
  sda_set_efin_state(planning_lp, scenario, p0_last, rsv0.uid(), efin_p0);

  // Phase 1: wired exactly like SDDPMethod::update_lp_for_phase, eini
  // pinned like propagate_trial_values, then solved.
  auto& sys1 = planning_lp.system(first_scene_index(), PhaseIndex {1});
  sys1.set_prev_phase_sys(&sys0);
  auto& li1 = sys1.linear_interface();
  const auto& rsv1 = sys1.elements<ReservoirLP>().front();
  const auto& p1_st0 = sys1.phase().stages()[0];
  const auto& p1_st1 = sys1.phase().stages()[1];
  li1.set_col(rsv1.eini_col_at(scenario, p1_st0), efin_p0);
  REQUIRE(li1.resolve().has_value());
  REQUIRE(li1.is_optimal());

  const double efin_p1_st0 =
      li1.get_col_sol()[rsv1.efin_col_at(scenario, p1_st0)];
  REQUIRE(std::abs(efin_p1_st0 - efin_p0) > 1.0);

  const auto cache = make_reservoir_ref_cache(rsv1, scenario, p1_st1);
  const double vini = physical_eini_from_cache(sys1, scenario, p1_st1, cache);
  INFO("efin(phase 0) = ", efin_p0, "  efin(phase 1, stage 0) = ", efin_p1_st0);
  // The incoming volume of the second stage of phase 1 is the first
  // stage's efin, not the predecessor phase's efin.
  CHECK(vini == doctest::Approx(efin_p1_st0));
  CHECK(vini != doctest::Approx(efin_p0));

  sys1.set_prev_phase_sys(nullptr);
}

// ─── S4 ────────────────────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "silent-default S4 positive control — build-time volume uses the "
    "predecessor's propagated efin")
{
  PlanningLP planning_lp(sda_make_two_stage_phase_planning());
  auto& sys1 = planning_lp.system(first_scene_index(), PhaseIndex {1});
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  const auto& scenario = sys1.scene().scenarios().front();
  const auto& p0_last = sys0.phase().stages().back();
  const auto& p1_st0 = sys1.phase().stages().front();
  const auto& rsv1 = sys1.elements<ReservoirLP>().front();

  sda_set_efin_state(planning_lp, scenario, p0_last, rsv1.uid(), 30.0);
  CHECK(
      build_time_reservoir_volume(sys1.system_context(), scenario, p1_st0, rsv1)
      == doctest::Approx(30.0));
}

TEST_CASE(  // NOLINT
    "silent-default S4 — build-time volume keeps a drained "
    "predecessor's efin = 0 (not the case eini)")
{
  PlanningLP planning_lp(sda_make_two_stage_phase_planning());
  auto& sys1 = planning_lp.system(first_scene_index(), PhaseIndex {1});
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  const auto& scenario = sys1.scene().scenarios().front();
  const auto& p0_last = sys0.phase().stages().back();
  const auto& p1_st0 = sys1.phase().stages().front();
  const auto& rsv1 = sys1.elements<ReservoirLP>().front();

  // Real path: a forward pass drained the reservoir to emin = 0, then
  // low_memory compress rebuilds phase 1 (add_to_lp → this helper).
  sda_set_efin_state(planning_lp, scenario, p0_last, rsv1.uid(), 0.0);
  const double v = build_time_reservoir_volume(
      sys1.system_context(), scenario, p1_st0, rsv1);
  CHECK(v == doctest::Approx(0.0));
  CHECK(v != doctest::Approx(sda_kCaseEini));
}
