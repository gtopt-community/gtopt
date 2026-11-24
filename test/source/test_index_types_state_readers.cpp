// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_index_types_state_readers.cpp
 * @brief     Phase-local vs global index types; solved-state readers
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * 1. `PositionVector` accepts its own strong index (and raw positions)
 *    but rejects every other strong index at compile time: the global
 *    stage array can no longer be indexed with a `PhaseStageIndex`.
 * 2. Phase / scene copies of `StageLP` / `ScenarioLP` carry BOTH their
 *    global and their local position, consistent with
 *    `SimulationLP::stages()` / `scenarios()` (also with inactive
 *    elements before the phase / scene).
 * 3. The reservoir state readers used by `update_lp`
 *    (`physical_efin_from_cache`, `physical_eini_from_cache`,
 *    `build_time_reservoir_volume`) return the SOLVED value after the LP
 *    was mutated (what `propagate_trial_values`, a cut row or a
 *    low_memory reconstruct do), instead of the case `eini`.
 */

#include <cmath>
#include <cstddef>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/position_vector.hpp>
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

// ── 1. compile-time index discipline ─────────────────────────────────────

using GlobalStages = PositionVector<StageIndex, int>;
using PhaseStages = PositionVector<PhaseStageIndex, int>;

template<typename V, typename I>
concept Indexable = requires(V v, const V cv, I i) {
  v[i];
  cv[i];
  v.at(i);
};

static_assert(Indexable<GlobalStages, StageIndex>);
static_assert(Indexable<GlobalStages, std::size_t>);
static_assert(!Indexable<GlobalStages, PhaseStageIndex>);
static_assert(!Indexable<GlobalStages, PhaseIndex>);
static_assert(Indexable<PhaseStages, PhaseStageIndex>);
static_assert(!Indexable<PhaseStages, StageIndex>);
static_assert(
    !Indexable<PositionVector<ScenarioIndex, int>, SceneScenarioIndex>);
static_assert(
    !Indexable<PositionVector<SceneScenarioIndex, int>, ScenarioIndex>);

constexpr double kCaseEini = 100.0;

/// 2 phases × 2 stages × 4 one-hour blocks (plus, optionally, one
/// inactive stage in front of everything), one reservoir (eini 100)
/// drained 5 per block by a cheap 15 MW hydro unit against an 80 MW load
/// with a 10 inflow: solved stage efins 80 / 60 / 40 / 20.
[[nodiscard]] Planning make_planning(bool leading_inactive_stage = false)
{
  const std::size_t lead = leading_inactive_stage ? 1 : 0;
  auto stages = make_uniform_stages(4 + lead, 4);
  if (leading_inactive_stage) {
    stages.front().active = false;
  }
  Simulation simulation = {
      .block_array = make_uniform_blocks(4 * (4 + lead), 1.0),
      .stage_array = std::move(stages),
      .scenario_array =
          {
              {.uid = Uid {7}, .name = {}, .active = false},
              {.uid = Uid {1}},
          },
      .phase_array =
          {
              {.uid = Uid {1}, .first_stage = lead, .count_stage = 2},
              {.uid = Uid {2}, .first_stage = lead + 2, .count_stage = 2},
          },
      .scene_array =
          {
              {
                  .uid = Uid {1},
                  .name = {},
                  .active = {},
                  .first_scenario = 1,
                  .count_scenario = 1,
              },
          },
  };

  System system = {
      .name = "index_types_state_readers",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array =
          {
              {.uid = Uid {1}, .name = "d1", .bus = Uid {1}, .capacity = 80.0},
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
                  .eini = kCaseEini,
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

/// Re-set an upper bound to its current value: no change to the LP, but
/// it clears the LI's optimal flag (and, under compress, its solution
/// cache) exactly like a cut row or a `propagate_trial_values` pin.
void touch_col_upper(LinearInterface& li, ColIndex col)
{
  li.set_col_upp(col, li.get_col_upp()[col]);
}

/// What `SDDPMethod::capture_state_variable_values` does after a
/// forward solve: write every efin state variable of the phase.
void capture_efin_states(PlanningLP& planning_lp, SystemLP& sys)
{
  static constexpr auto cname = ReservoirLP::Element::class_name;
  const auto& li = sys.linear_interface();
  const auto& rsv = sys.elements<ReservoirLP>().front();
  const auto& scenario = sys.scene().scenarios().front();
  for (const auto& stage : sys.phase().stages()) {
    auto svar = planning_lp.simulation().state_variable(
        StateVariable::key(scenario, stage, cname, rsv.uid(), "efin"));
    REQUIRE(svar.has_value());
    const double phys = li.get_col_sol()[rsv.efin_col_at(scenario, stage)];
    if (svar) {
      const auto& sv = svar->get();
      sv.set_col_sol(phys / sv.var_scale());
    }
  }
}

}  // namespace

TEST_CASE(
    "index types - phase and scene copies carry both positions")  // NOLINT
{
  for (const bool lead : {false, true}) {
    CAPTURE(lead);
    const PlanningLP planning_lp(make_planning(lead));
    const auto& sim = planning_lp.simulation();

    REQUIRE(sim.stages().size() == 4);
    for (const auto& phase : sim.phases()) {
      for (const auto& [i, stage] : enumerate<PhaseStageIndex>(phase.stages()))
      {
        CHECK(stage.phase_stage_index() == i);
        CHECK(stage.phase_index() == phase.index());
        // The global position addresses the same stage in the flat list.
        CHECK(sim.stages()[stage.stage_index()].uid() == stage.uid());
      }
    }
    // Second stage of the second phase: global 3, local 1.
    const auto& p1s1 = sim.phases()[PhaseIndex {1}].stages()[std::size_t {1}];
    CHECK(p1s1.stage_index() == StageIndex {3});
    CHECK(p1s1.phase_stage_index() == PhaseStageIndex {1});
    CHECK_FALSE(p1s1.is_horizon_first());
    CHECK(sim.phases()[PhaseIndex {0}].stages().front().is_horizon_first());

    // prev_stage of a non-first stage of a later phase is the previous
    // stage of the SAME phase (the f46531179 bug returned stage 0).
    const auto [prev, prev_phase] = sim.prev_stage(p1s1);
    REQUIRE(prev != nullptr);
    CHECK(prev_phase == nullptr);
    CHECK(prev->stage_index() == StageIndex {2});
    CHECK(prev->phase_index() == PhaseIndex {1});

    // Scene copy of the only active scenario: local 0, global 0 (the
    // inactive scenario in front is not in the global list).
    const auto& sc = sim.scenes()[SceneIndex {0}].scenarios().front();
    CHECK(sc.scene_scenario_index() == first_scene_scenario_index());
    CHECK(sc.scenario_index() == first_scenario_index());
    CHECK(sim.scenarios()[sc.scenario_index()].uid() == sc.uid());
    CHECK(sc.is_first());
  }
}

TEST_CASE("StateVariable - has_col_sol distinguishes 0 from unset")  // NOLINT
{
  const StateVariable sv {LPKey {}, ColIndex {0}, 0.0, 1.0, LpContext {}};
  CHECK_FALSE(sv.has_col_sol());
  sv.set_col_sol(0.0);
  CHECK(sv.has_col_sol());
  CHECK(sv.col_sol_physical() == doctest::Approx(0.0));
}

TEST_CASE(  // NOLINT
    "state readers - solved values survive an LP mutation (no case eini)")
{
  PlanningLP planning_lp(make_planning());
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  auto& li0 = sys0.linear_interface();
  REQUIRE(li0.resolve().has_value());
  capture_efin_states(planning_lp, sys0);

  const auto& rsv = sys0.elements<ReservoirLP>().front();
  const auto& scenario = sys0.scene().scenarios().front();
  const auto& st0 = sys0.phase().stages()[std::size_t {0}];
  const auto& st1 = sys0.phase().stages()[std::size_t {1}];
  const double efin0 = li0.get_col_sol()[rsv.efin_col_at(scenario, st0)];
  const double efin1 = li0.get_col_sol()[rsv.efin_col_at(scenario, st1)];
  REQUIRE(std::abs(efin0 - kCaseEini) > 1.0);
  REQUIRE(std::abs(efin1 - kCaseEini) > 1.0);

  const auto cache1 = make_reservoir_ref_cache(rsv, scenario, st1);
  touch_col_upper(li0, cache1.efin_col);
  // CPLEX / HiGHS drop the optimal status on any bound change; OSI
  // (CLP, CBC) keeps it until the next solve.  The readers must
  // return the solved values either way.
  if (li0.is_optimal()) {
    MESSAGE("backend keeps its optimal status after a bound change");
  }

  SUBCASE("efin of the current stage (rule axis vfin)")
  {
    CHECK(physical_efin_from_cache(sys0, scenario, st1, cache1)
          == doctest::Approx(efin1));
  }
  SUBCASE("eini of an intra-phase stage = previous stage's efin")
  {
    CHECK(physical_eini_from_cache(sys0, scenario, st1, cache1)
          == doctest::Approx(efin0));
  }
  SUBCASE("(vini + vfin) / 2 uses both solved values")
  {
    CHECK(average_volume_from_cache(sys0, scenario, st1, cache1)
          == doctest::Approx((efin0 + efin1) / 2.0));
  }
}

TEST_CASE(  // NOLINT
    "state readers - second stage of a later phase reads its own phase")
{
  PlanningLP planning_lp(make_planning());
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  REQUIRE(sys0.linear_interface().resolve().has_value());
  capture_efin_states(planning_lp, sys0);

  auto& sys1 = planning_lp.system(first_scene_index(), PhaseIndex {1});
  auto& li1 = sys1.linear_interface();
  const auto& rsv1 = sys1.elements<ReservoirLP>().front();
  const auto& scenario = sys1.scene().scenarios().front();
  const auto& p1s0 = sys1.phase().stages()[std::size_t {0}];
  const auto& p1s1 = sys1.phase().stages()[std::size_t {1}];
  const double efin_p0 =
      sys0.linear_interface()
          .get_col_sol()[sys0.elements<ReservoirLP>().front().efin_col_at(
              scenario, sys0.phase().stages().back())];
  li1.set_col(rsv1.eini_col_at(scenario, p1s0), efin_p0);
  REQUIRE(li1.resolve().has_value());
  capture_efin_states(planning_lp, sys1);
  const double efin_p1s0 = li1.get_col_sol()[rsv1.efin_col_at(scenario, p1s0)];
  REQUIRE(std::abs(efin_p1s0 - efin_p0) > 1.0);

  const auto cache = make_reservoir_ref_cache(rsv1, scenario, p1s1);
  touch_col_upper(li1, cache.efin_col);
  // CPLEX / HiGHS drop the optimal status on any bound change; OSI
  // (CLP, CBC) keeps it until the next solve.  The readers must
  // return the solved values either way.
  if (li1.is_optimal()) {
    MESSAGE("backend keeps its optimal status after a bound change");
  }

  // Previously: the predecessor PHASE's efin (efin_p0) for every stage of
  // the phase, or the case eini once the LP was mutated.
  CHECK(physical_eini_from_cache(sys1, scenario, p1s1, cache)
        == doctest::Approx(efin_p1s0));
}

TEST_CASE(  // NOLINT
    "state readers - a drained predecessor (efin = 0) is a solved state")
{
  PlanningLP planning_lp(make_planning());
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  auto& sys1 = planning_lp.system(first_scene_index(), PhaseIndex {1});
  const auto& scenario = sys1.scene().scenarios().front();
  const auto& rsv1 = sys1.elements<ReservoirLP>().front();
  const auto& p1s0 = sys1.phase().stages().front();

  static constexpr auto cname = ReservoirLP::Element::class_name;
  auto svar = planning_lp.simulation().state_variable(StateVariable::key(
      scenario, sys0.phase().stages().back(), cname, rsv1.uid(), "efin"));
  REQUIRE(svar.has_value());

  // Unset: build-time estimate is the case eini.
  CHECK(build_time_reservoir_volume(sys1.system_context(), scenario, p1s0, rsv1)
        == doctest::Approx(kCaseEini));

  // Solved to exactly 0: 0, not the case eini (old `v > 0` guard).
  if (svar) {
    svar->get().set_col_sol(0.0);
  }
  CHECK(build_time_reservoir_volume(sys1.system_context(), scenario, p1s0, rsv1)
        == doctest::Approx(0.0));
}
