// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_decision_variable_scope_reach.cpp
/// @brief Coarse-scope DecisionVariables referenced from block-level
///        UserConstraints, and the unreachable-column guard.
///
/// Regression for the "global DV dropped as 0" bug: a `scope: "global"`
/// (or `"phase"`) DecisionVariable used to be built in the planning pass,
/// AFTER the per-(scenario, stage) operational sweep that assembles
/// block-scoped user constraints, and was registered for the resolver at
/// one representative (scenario, stage) only.  A block row referencing it
/// found no column and took the "element known, no column for this block"
/// silent-zero branch — `x <= g` became `x <= 0`, even in strict mode.
///
/// Pinned here:
///   * global scope: one column for the whole (scene, phase) cell, reachable
///     from every block (repro objective 1 instead of infeasible);
///   * stage scope: one column per (scenario, stage), shared by its blocks;
///   * multi-scenario: a global column is shared by all scenarios of the
///     scene — non-anticipativity — and its probability-weighted cost
///     carries the scene's full probability mass;
///   * a column that exists but is unreachable from a row (single-block
///     selector) errors in strict mode and warns in normal mode;
///   * a global column over several phases is carried as an implicit
///     cross-phase state (explicit `state: false` opts out with a warning).

#include <format>
#include <limits>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace dvscope_test
{
namespace
{

// clang-format off

/// One-stage, one-block, one-scenario simulation (the repro's shape).
constexpr std::string_view sim_1x1 = R"js(
  "block_array": [ { "uid": 1, "duration": 1 } ],
  "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
  "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
)js";

/// Two stages x two blocks (block uids 1..4), one scenario, one phase.
constexpr std::string_view sim_2x2 = R"js(
  "block_array": [
    { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 },
    { "uid": 3, "duration": 1 }, { "uid": 4, "duration": 1 }
  ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 2 },
    { "uid": 2, "first_block": 2, "count_block": 2 }
  ],
  "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
)js";

/// One stage, one block, two equiprobable scenarios in ONE scene.
constexpr std::string_view sim_2scen = R"js(
  "block_array": [ { "uid": 1, "duration": 1 } ],
  "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 0.5 },
    { "uid": 2, "probability_factor": 0.5 }
  ]
)js";

/// Two stages (one block each) split into TWO phases.
constexpr std::string_view sim_2phase = R"js(
  "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 } ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 1 },
    { "uid": 2, "first_block": 1, "count_block": 1 }
  ],
  "phase_array": [
    { "uid": 1, "first_stage": 0, "count_stage": 1 },
    { "uid": 2, "first_stage": 1, "count_stage": 1 }
  ],
  "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
)js";

/// Assemble a planning JSON: single bus, objective unscaled, the given
/// simulation body, decision variables, user constraints and mode.
[[nodiscard]] auto make_json(std::string_view sim,
                             std::string_view dvs,
                             std::string_view ucs,
                             std::string_view mode = "strict") -> std::string
{
  return std::format(R"js(
  {{
    "options": {{
      "output_format": "csv",
      "output_compression": "uncompressed",
      "constraint_mode": "{}",
      "model_options": {{ "use_single_bus": true, "scale_objective": 1 }}
    }},
    "simulation": {{ {} }},
    "system": {{
      "name": "dv_scope_reach",
      "bus_array": [ {{ "uid": 1, "name": "b1" }} ],
      "decision_variable_array": [ {} ],
      "user_constraint_array": [ {} ]
    }}
  }})js",
                     mode,
                     sim,
                     dvs,
                     ucs);
}

// clang-format on

/// Build + solve (monolithic) and return the optimal objective, or NaN when
/// the solve did not succeed.
[[nodiscard]] auto solve_obj(const std::string& json) -> double
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP planning_lp(std::move(base));
  const auto result = planning_lp.resolve();
  if (!result.has_value()) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  const auto& li = systems.front().front().linear_interface();
  if (!li.is_optimal()) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  return li.get_obj_value();
}

/// Build only (no solve) — used for the strict-mode error paths.
void build_only(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  const PlanningLP planning_lp(std::move(base));
}

/// `x` block-scoped, `g` with the given scope and a unit cost.
[[nodiscard]] auto dvs_x_g(std::string_view g_scope,
                           std::string_view g_cost_type = "raw") -> std::string
{
  return std::format(
      R"js({{ "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 10 }},
         {{ "uid": 2, "name": "g", "lower_bound": 0, "upper_bound": 10,
            "cost": 1.0, "cost_type": "{}" {} }})js",
      g_cost_type,
      g_scope.empty() ? std::string {}
                      : std::format(R"js(, "scope": "{}")js", g_scope));
}

}  // namespace
}  // namespace dvscope_test

using namespace dvscope_test;

// ─── The LNG study repro ─────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "DecisionVariable scope — repro: global DV in a block UC solves obj = 1")
{
  // Verbatim system of `global_scope_block_uc.json`: x in [0,1], g in [0,1]
  // cost 1 global; x >= 1 and x <= g, both block-scoped.  Before the fix the
  // g term was dropped and the LP was infeasible (x <= 0 vs x >= 1).
  constexpr std::string_view repro = R"js({
    "options": {
      "output_format": "csv",
      "output_compression": "uncompressed",
      "output_directory": "output",
      "model_options": { "use_single_bus": true, "scale_objective": 1 }
    },
    "simulation": {
      "block_array": [ { "uid": 1, "duration": 1 } ],
      "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
      "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
    },
    "system": {
      "name": "global_scope_repro",
      "bus_array": [ { "uid": 1, "name": "b1" } ],
      "decision_variable_array": [
        { "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 1 },
        { "uid": 2, "name": "g", "lower_bound": 0, "upper_bound": 1,
          "cost": 1.0, "scope": "global" }
      ],
      "user_constraint_array": [
        { "uid": 1, "name": "x_min",
          "expression": "decision_variable('x').value >= 1" },
        { "uid": 2, "name": "x_le_g",
          "expression":
            "decision_variable('x').value <= decision_variable('g').value" }
      ]
    }
  })js";
  CHECK(solve_obj(std::string {repro}) == doctest::Approx(1.0));

  // control_block_scope.json: identical but g block-scoped.
  std::string control {repro};
  const std::string needle = R"js(, "scope": "global")js";
  control.erase(control.find(needle), needle.size());
  CHECK(solve_obj(control) == doctest::Approx(1.0));
}

TEST_CASE(  // NOLINT
    "DecisionVariable scope — phase scope is reachable from block rows")
{
  const auto json = make_json(sim_1x1,
                              dvs_x_g("phase"),
                              R"js({ "uid": 1, "name": "x_min",
           "expression": "decision_variable('x').value >= 1" },
         { "uid": 2, "name": "x_le_g",
           "expression":
             "decision_variable('x').value <= decision_variable('g').value" })js");
  CHECK(solve_obj(json) == doctest::Approx(1.0));
}

// ─── One column per cell vs per block vs per stage ─────────────────────────

namespace
{
/// x >= 2 on block 1, x >= 3 on block 2 (stage 1), x >= 4 on block 3
/// (stage 2), and x <= g on every block.  The cost of g discriminates the
/// scope: block = 2+3+4 = 9, stage = max(2,3)+max(4,0) = 7, global = 4.
constexpr std::string_view ucs_2x2 = R"js(
  { "uid": 1, "name": "x_b1",
    "expression": "decision_variable('x').value >= 2, for(block in {1})" },
  { "uid": 2, "name": "x_b2",
    "expression": "decision_variable('x').value >= 3, for(block in {2})" },
  { "uid": 3, "name": "x_b3",
    "expression": "decision_variable('x').value >= 4, for(block in {3})" },
  { "uid": 4, "name": "x_le_g",
    "expression":
      "decision_variable('x').value <= decision_variable('g').value" }
)js";
}  // namespace

TEST_CASE(  // NOLINT
    "DecisionVariable scope — block / stage / global across 2 stages x 2 "
    "blocks")
{
  SUBCASE("block scope: one column per block")
  {
    CHECK(solve_obj(make_json(sim_2x2, dvs_x_g(""), ucs_2x2))
          == doctest::Approx(9.0));
  }
  SUBCASE("stage scope: one column per stage, shared by its blocks")
  {
    CHECK(solve_obj(make_json(sim_2x2, dvs_x_g("stage"), ucs_2x2))
          == doctest::Approx(7.0));
  }
  SUBCASE("global scope: ONE column for every block of every stage")
  {
    CHECK(solve_obj(make_json(sim_2x2, dvs_x_g("global"), ucs_2x2))
          == doctest::Approx(4.0));
  }
  SUBCASE("phase scope: same as global in a single-phase run")
  {
    CHECK(solve_obj(make_json(sim_2x2, dvs_x_g("phase"), ucs_2x2))
          == doctest::Approx(4.0));
  }
}

// ─── Non-anticipativity across scenarios ───────────────────────────────────

namespace
{
/// x >= 1 in scenario 1, x >= 3 in scenario 2, x <= g everywhere.
constexpr std::string_view ucs_2scen = R"js(
  { "uid": 1, "name": "x_s1",
    "expression": "decision_variable('x').value >= 1, for(scenario in {1})" },
  { "uid": 2, "name": "x_s2",
    "expression": "decision_variable('x').value >= 3, for(scenario in {2})" },
  { "uid": 3, "name": "x_le_g",
    "expression":
      "decision_variable('x').value <= decision_variable('g').value" }
)js";
}  // namespace

TEST_CASE(  // NOLINT
    "DecisionVariable scope — global DV is non-anticipative across the "
    "scene's scenarios")
{
  SUBCASE("raw cost: block scope adapts per scenario (wait-and-see)")
  {
    // g1 = 1, g2 = 3, face-value cost → 4.
    CHECK(solve_obj(make_json(sim_2scen, dvs_x_g(""), ucs_2scen))
          == doctest::Approx(4.0));
  }
  SUBCASE("raw cost: global scope is one here-and-now value")
  {
    // A single g must cover the worst scenario: g = 3 → 3.
    CHECK(solve_obj(make_json(sim_2scen, dvs_x_g("global"), ucs_2scen))
          == doctest::Approx(3.0));
  }
  SUBCASE("energy cost: block scope weights each scenario by 0.5")
  {
    // 0.5·1 + 0.5·3 = 2.
    CHECK(solve_obj(make_json(sim_2scen, dvs_x_g("", "energy"), ucs_2scen))
          == doctest::Approx(2.0));
  }
  SUBCASE("energy cost: the shared column carries the scene's full mass")
  {
    // (0.5 + 0.5)·3 = 3 — NOT 0.5·3 (first scenario only).
    CHECK(
        solve_obj(make_json(sim_2scen, dvs_x_g("global", "energy"), ucs_2scen))
        == doctest::Approx(3.0));
  }
  SUBCASE("stage scope stays per scenario (anticipative)")
  {
    CHECK(solve_obj(make_json(sim_2scen, dvs_x_g("stage"), ucs_2scen))
          == doctest::Approx(4.0));
  }
}

// ─── UserModel bundle: same reachability for bundled variables ─────────────

TEST_CASE(  // NOLINT
    "DecisionVariable scope — bundled global variable in a block constraint")
{
  // clang-format off
  constexpr std::string_view json = R"js({
    "options": {
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": { "use_single_bus": true, "scale_objective": 1 }
    },
    "simulation": {
      "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 } ],
      "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 2 } ],
      "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
    },
    "system": {
      "name": "bundled_global",
      "bus_array": [ { "uid": 1, "name": "b1" } ],
      "user_model_array": [ {
        "uid": 1, "name": "m",
        "variable_array": [
          { "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 10 },
          { "uid": 2, "name": "g", "lower_bound": 0, "upper_bound": 10,
            "cost": 1.0, "scope": "global" }
        ],
        "constraint_array": [
          { "uid": 1, "name": "x_b1",
            "expression": "decision_variable('x').value >= 2, for(block in {1})" },
          { "uid": 2, "name": "x_b2",
            "expression": "decision_variable('x').value >= 5, for(block in {2})" },
          { "uid": 3, "name": "x_le_g",
            "expression":
              "decision_variable('x').value <= decision_variable('g').value" }
        ]
      } ]
    }
  })js";
  // clang-format on
  CHECK(solve_obj(std::string {json}) == doctest::Approx(5.0));
}

// ─── Unreachable column: strict error / lax warning ────────────────────────

namespace
{
/// `y` exists only on block 1 (single-block selector); a block-scoped row
/// on block 2 cannot reach it.
constexpr std::string_view dvs_single_block = R"js(
  { "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 10 },
  { "uid": 2, "name": "y", "lower_bound": 0, "upper_bound": 10, "cost": 1.0,
    "block": 1 }
)js";
}  // namespace

TEST_CASE(  // NOLINT
    "DecisionVariable scope — unreachable single-block column: strict throws")
{
  const auto json = make_json(sim_2x2,
                              dvs_single_block,
                              R"js({ "uid": 1, "name": "x_le_y",
         "expression":
           "decision_variable('x').value <= decision_variable('y').value" })js");
  CHECK_THROWS_WITH_AS(build_only(json),
                       doctest::Contains("not reachable from this row"),
                       std::runtime_error);
}

TEST_CASE(  // NOLINT
    "DecisionVariable scope — unreachable column inside sum(): strict throws")
{
  const auto json = make_json(sim_2x2,
                              dvs_single_block,
                              R"js({ "uid": 1, "name": "sum_xy",
           "expression":
             "sum(decision_variable('x', 'y').value) <= 5" })js");
  CHECK_THROWS_WITH_AS(build_only(json),
                       doctest::Contains("decision_variable(y).value"),
                       std::runtime_error);
}

TEST_CASE(  // NOLINT
    "DecisionVariable scope — unreachable column: normal mode warns and "
    "drops the term")
{
  // x >= 1 everywhere, x <= y.  y only exists on block 1 → on blocks 2..4
  // the term drops (with a warning), leaving x <= 0 there → infeasible.
  // Restrict the requirement to block 1 to keep the LP feasible: y = 1.
  const auto json = make_json(sim_2x2,
                              dvs_single_block,
                              R"js({ "uid": 1, "name": "x_min",
         "expression": "decision_variable('x').value >= 1, for(block in {1})" },
       { "uid": 2, "name": "x_le_y",
         "expression":
           "decision_variable('x').value <= decision_variable('y').value" })js",
                              "normal");
  CHECK(solve_obj(json) == doctest::Approx(1.0));
}

TEST_CASE(  // NOLINT
    "DecisionVariable scope — single-block column referenced on its own "
    "block resolves in strict mode")
{
  const auto json = make_json(sim_2x2,
                              dvs_single_block,
                              R"js({ "uid": 1, "name": "x_min",
         "expression": "decision_variable('x').value >= 1, for(block in {1})" },
       { "uid": 2, "name": "x_le_y",
         "expression":
           "decision_variable('x').value <= decision_variable('y').value, for(block in {1})" })js");
  CHECK(solve_obj(json) == doctest::Approx(1.0));
}

// ─── Global column over several phases ─────────────────────────────────────

TEST_CASE(  // NOLINT
    "DecisionVariable scope — non-state global DV over 2 phases")
{
  constexpr std::string_view ucs = R"js({ "uid": 1, "name": "x_min",
         "expression": "decision_variable('x').value >= 1" },
       { "uid": 2, "name": "x_le_g",
         "expression":
           "decision_variable('x').value <= decision_variable('g').value" })js";

  SUBCASE("strict: carried as an implicit cross-phase state (no error)")
  {
    // One value for the horizon: phase 2's copy is linked to phase 1's.
    // Values / objectives are checked in
    // test_decision_variable_global_phases.cpp.
    CHECK_NOTHROW(build_only(make_json(sim_2phase, dvs_x_g("global"), ucs)));
  }
  SUBCASE("explicit state: false opts out (independent copies, warning)")
  {
    const auto* const dvs =
        R"js({ "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 10 },
         { "uid": 2, "name": "g", "lower_bound": 0, "upper_bound": 10,
           "cost": 1.0, "scope": "global", "state": false })js";
    CHECK_NOTHROW(build_only(make_json(sim_2phase, dvs, ucs)));
  }
  SUBCASE("phase scope makes the per-phase copies explicit (no error)")
  {
    CHECK_NOTHROW(build_only(make_json(sim_2phase, dvs_x_g("phase"), ucs)));
  }
}

TEST_CASE(  // NOLINT
    "DecisionVariable scope — stage-scoped DV inactive at a stage is an "
    "implicit 0, not a scope error")
{
  // g is stage-scoped and active in stage 1 only; the all-stage row
  // x <= g reaches no g column in stage 2 by design (inactive), so strict
  // mode builds and x is pinned to 0 there.
  const auto json = make_json(sim_2x2,
                              R"js(
  { "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 10 },
  { "uid": 2, "name": "g", "lower_bound": 0, "upper_bound": 10, "cost": 1.0,
    "scope": "stage", "active": [1, 0] })js",
                              R"js({ "uid": 1, "name": "x_le_g",
         "expression":
           "decision_variable('x').value <= decision_variable('g').value" })js");
  CHECK_NOTHROW(build_only(json));
}

TEST_CASE(  // NOLINT
    "DecisionVariable scope — sum(all) over mixed scopes skips unreachable "
    "members")
{
  // x is block-scoped, y exists on block 1 only.  sum(all) <= 5 on blocks
  // 2..4 holds x alone; strict mode must build (y skipped and counted).
  const auto json = make_json(sim_2x2,
                              dvs_single_block,
                              R"js({ "uid": 1, "name": "sum_all",
           "expression": "sum(decision_variable(all).value) <= 5" })js");
  Planning base;
  base.merge(parse_planning_json(json));
  const PlanningLP planning_lp(std::move(base));
  const auto counts = planning_lp.simulation().dropped_term_counts();
  const auto it = counts.find("sum_all");
  REQUIRE(it != counts.end());
  CHECK(it->second.at("scope-restricted member of sum(all)") == 3);
}
