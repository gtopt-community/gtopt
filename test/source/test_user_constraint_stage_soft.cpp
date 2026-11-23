/**
 * @file      test_user_constraint_stage_soft.cpp
 * @brief     Stage-scoped user constraints: soft slacks and rhs override
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_user_constraint_scope.cpp` pins the row counts of the scopes
 * with hard constraints.  A stage-scoped row is built by its own path
 * (`UserConstraintLP::build_coarse_row`), which also adds the soft
 * slacks (one for <= / >=, a +/- pair for ==) and applies the `rhs`
 * override at the representative block.  These tests check the optimum
 * of each case on a model small enough to solve by hand:
 *
 *   3 blocks x 8 h, demand 100 MW, g1 at 5 $/MWh, fail cost 1000 $/MWh
 *   -> 2400 MWh needed, unconstrained cost 12000.
 *   E := sum{b in stage} dur[b] * generation   (MWh over the stage)
 */

#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace ucstage_soft_test
{
namespace
{

// clang-format off
auto make_json(std::string_view uc_block) -> std::string
{
  return std::format(
      R"(
  {{
    "options": {{
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {{
        "use_single_bus": true,
        "demand_fail_cost": 1000,
        "scale_objective": 1
      }}
    }},
    "simulation": {{
      "block_array": [
        {{ "uid": 1, "duration": 8 }},
        {{ "uid": 2, "duration": 8 }},
        {{ "uid": 3, "duration": 8 }}
      ],
      "stage_array": [
        {{ "uid": 1, "first_block": 0, "count_block": 3, "active": 1 }}
      ],
      "scenario_array": [ {{ "uid": 1, "probability_factor": 1 }} ]
    }},
    "system": {{
      "name": "uc_stage_soft",
      "bus_array": [ {{ "uid": 1, "name": "b1" }} ],
      "generator_array": [
        {{ "uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 200,
           "gcost": 5, "capacity": 200 }}
      ],
      "demand_array": [
        {{ "uid": 1, "name": "d1", "bus": "b1",
           "lmax": [ [ 100.0, 100.0, 100.0 ] ] }}
      ],
      "user_constraint_array": [ {} ]
    }}
  }})",
      uc_block);
}
// clang-format on

struct Result
{
  double obj {};
  Index numcols {};
};

[[nodiscard]] auto solve(std::string_view uc_block) -> Result
{
  Planning base;
  base.merge(parse_planning_json(make_json(uc_block)));
  PlanningLP planning_lp(std::move(base));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  const auto& li = planning_lp.systems().front().front().linear_interface();
  return {
      .obj = li.get_obj_value_raw(),
      .numcols = li.get_numcols(),
  };
}

constexpr std::string_view energy =
    "sum{b in stage} dur[b] * "
    "generator('g1').generation";

}  // namespace
}  // namespace ucstage_soft_test

TEST_CASE("Stage-scope user constraint — hard rhs override")  // NOLINT
{
  using namespace ucstage_soft_test;

  // Expression bound 99999 is replaced by rhs = 1800: E <= 1800 ->
  // 1800 MWh at 5 + 600 MWh unserved at 1000 = 609000.
  const auto r = solve(std::format(
      R"({{ "uid": 1, "name": "uc", "scope": "stage", "rhs": 1800,
            "expression": "{} <= 99999" }})",
      energy));
  CHECK(r.obj == doctest::Approx(609000.0).epsilon(1e-6));
}

TEST_CASE("Stage-scope user constraint — soft slacks")  // NOLINT
{
  using namespace ucstage_soft_test;

  const auto hard = solve(std::format(
      R"({{ "uid": 1, "name": "uc", "scope": "stage",
            "expression": "{} <= 1800" }})",
      energy));
  CHECK(hard.obj == doctest::Approx(609000.0).epsilon(1e-6));

  // A tiny penalty makes violating the row far cheaper than shedding
  // load, so the optimum serves all demand (12000) plus a negligible
  // slack cost.
  SUBCASE("<= adds one slack column")
  {
    const auto r = solve(std::format(
        R"({{ "uid": 1, "name": "uc", "scope": "stage", "penalty": 1e-6,
              "expression": "{} <= 1800" }})",
        energy));
    CHECK(r.numcols == hard.numcols + 1);
    CHECK(r.obj == doctest::Approx(12000.0).epsilon(1e-4));
  }

  SUBCASE(">= adds one slack column")
  {
    // E >= 3000 cannot be met (demand is 2400); the slack absorbs it.
    const auto r = solve(std::format(
        R"({{ "uid": 1, "name": "uc", "scope": "stage", "penalty": 1e-6,
              "expression": "{} >= 3000" }})",
        energy));
    CHECK(r.numcols == hard.numcols + 1);
    CHECK(r.obj == doctest::Approx(12000.0).epsilon(1e-4));
  }

  SUBCASE("== adds a +/- slack pair")
  {
    const auto r = solve(std::format(
        R"({{ "uid": 1, "name": "uc", "scope": "stage", "penalty": 1e-6,
              "expression": "{} = 1800" }})",
        energy));
    CHECK(r.numcols == hard.numcols + 2);
    CHECK(r.obj == doctest::Approx(12000.0).epsilon(1e-4));
  }

  SUBCASE("a large penalty keeps the row binding")
  {
    // Slack cost far above the fail cost: shedding load is cheaper.
    const auto r = solve(std::format(
        R"({{ "uid": 1, "name": "uc", "scope": "stage", "penalty": 1e5,
              "expression": "{} <= 1800" }})",
        energy));
    CHECK(r.obj == doctest::Approx(609000.0).epsilon(1e-6));
  }
}
