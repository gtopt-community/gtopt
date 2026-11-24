// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_decision_variable_integer.cpp
 * @brief     Unit tests for the DecisionVariable `integer` / `relax` flags
 *
 * Pins the contract of `DecisionVariable::integer`:
 *   * JSON round-trip of `integer` / `relax` (unset by default).
 *   * the `value` columns are integer LP columns (and registered in the
 *     integer-variable registry with the right domain / scope) only when
 *     `integer: true`, for the block, stage and phase scopes;
 *   * `relax: true` and a continuous phase keep them continuous;
 *   * a tiny MIP whose optimum differs from its LP relaxation.
 */

#include <optional>
#include <string>
#include <utility>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/decision_variable.hpp>
#include <gtopt/json/json_decision_variable.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct DvCase
{
  OptBool integer {};
  OptBool relax {};
  OptName scope {};
  double lower {0.0};
  double upper {10.0};
  bool continuous_phase {false};
};

/// One bus, one stage of two 1-hour blocks, one scenario.  A
/// DecisionVariable `x` in [lower, upper] with raw cost -1 (maximise x),
/// capped by the raw user constraint `2·x ≤ 5` — LP optimum x = 2.5,
/// integer optimum x = 2.
auto make_case(const DvCase& c) -> std::pair<System, Simulation>
{
  System sys;
  sys.name = "dv_integer";
  sys.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  sys.decision_variable_array = {
      DecisionVariable {
          .uid = Uid {1},
          .name = "x",
          .lower_bound = c.lower,
          .upper_bound = c.upper,
          .cost = -1.0,
          .cost_type = OptName {"raw"},
          .scope = c.scope,
          .integer = c.integer,
          .relax = c.relax,
      },
  };
  sys.user_constraint_array = {
      UserConstraint {
          .uid = Uid {1},
          .name = "cap_x",
          .expression = Name {"2 * decision_variable('x').value <= 5"},
          .constraint_type = OptName {"raw"},
          .scope = c.scope,
      },
  };

  Simulation sim;
  sim.block_array = {
      {
          .uid = Uid {0},
          .duration = 1.0,
      },
      {
          .uid = Uid {1},
          .duration = 1.0,
      },
  };
  sim.stage_array = {
      {
          .uid = Uid {0},
          .first_block = 0,
          .count_block = 2,
          .chronological = true,
      },
  };
  sim.scenario_array = {
      {
          .uid = Uid {0},
      },
  };
  if (c.continuous_phase) {
    sim.phase_array = {
        {
            .uid = Uid {0},
            .continuous = true,
        },
    };
  }
  return {std::move(sys), std::move(sim)};
}

/// First loaded MIP-capable solver ("" when none — never CLP).
auto mip_solver_name() -> std::string
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  for (const auto& name : reg.available_solvers()) {
    if (reg.supports_mip(name)) {
      return name;
    }
  }
  return {};
}

auto count_integer_cols(const LinearInterface& li) -> int
{
  int n = 0;
  for (Index i = 0; i < li.get_numcols(); ++i) {
    if (li.is_integer(ColIndex {i})) {
      ++n;
    }
  }
  return n;
}

struct BuildResult
{
  int integer_cols {0};
  int registry_entries {0};
  std::optional<IntegerDomain> domain {};
  std::optional<IntegerScope> scope {};
  std::optional<double> obj {};
};

/// Build the block/stage-scope case through SystemLP; solve it when a MIP
/// solver is available.
auto build_system(const DvCase& c, bool solve) -> BuildResult
{
  auto [sys, sim] = make_case(c);
  PlanningOptions popts;
  popts.model_options.scale_objective = 1.0;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(sim, options);
  LpMatrixOptions flat_opts;
  // Integer columns need a MIP-capable backend even to build (an LP-only
  // ambient GTOPT_SOLVER, e.g. clp in CI, rejects them).
  flat_opts.solver_name = mip_solver_name();
  SystemLP system_lp(sys, simulation_lp, flat_opts);

  BuildResult r;
  auto&& li = system_lp.linear_interface();
  r.integer_cols = count_integer_cols(li);
  const auto& reg =
      simulation_lp.integer_variables(SceneIndex {0}, PhaseIndex {0});
  r.registry_entries = static_cast<int>(reg.size());
  if (!reg.empty()) {
    r.domain = reg.begin()->second.domain();
    r.scope = reg.begin()->second.scope();
  }
  if (solve) {
    const auto result = li.resolve();
    REQUIRE(result.has_value());
    REQUIRE(result.value() == 0);
    r.obj = li.get_obj_value_raw();
  }
  return r;
}

}  // namespace

TEST_CASE("DecisionVariable integer — JSON round-trip")  // NOLINT
{
  SUBCASE("unset by default")
  {
    const auto dv = daw::json::from_json<DecisionVariable>(
        R"({"uid": 1, "name": "x"})", StrictParsePolicy);
    CHECK_FALSE(dv.integer.has_value());
    CHECK_FALSE(dv.relax.has_value());
  }

  SUBCASE("integer + relax round-trip")
  {
    const auto dv = daw::json::from_json<DecisionVariable>(
        R"({"uid": 1, "name": "x", "upper_bound": 1,
            "integer": true, "relax": false})",
        StrictParsePolicy);
    CHECK(dv.integer.value_or(false));
    CHECK((dv.relax && !*dv.relax));

    const auto emitted = daw::json::to_json(dv);
    CHECK(emitted.find("\"integer\":true") != std::string::npos);
    const auto dv2 =
        daw::json::from_json<DecisionVariable>(emitted, StrictParsePolicy);
    CHECK(dv2.integer.value_or(false));
    CHECK((dv2.relax && !*dv2.relax));
    CHECK(dv2.upper_bound.value_or(0.0) == doctest::Approx(1.0));
  }
}

TEST_CASE("DecisionVariableLP integer — column integrality")  // NOLINT
{
  SUBCASE("default: continuous, nothing registered")
  {
    const auto r = build_system({}, /*solve=*/false);
    CHECK(r.integer_cols == 0);
    CHECK(r.registry_entries == 0);
  }

  SUBCASE("block scope: one Integer column per block")
  {
    const auto r = build_system({.integer = true}, /*solve=*/false);
    CHECK(r.integer_cols == 2);
    CHECK(r.registry_entries == 2);
    CHECK(r.domain == IntegerDomain::Integer);
    CHECK(r.scope == IntegerScope::Block);
  }

  SUBCASE("bounds [0, 1] ⇒ Binary domain")
  {
    const auto r =
        build_system({.integer = true, .upper = 1.0}, /*solve=*/false);
    CHECK(r.integer_cols == 2);
    CHECK(r.domain == IntegerDomain::Binary);
  }

  SUBCASE("stage scope: one Stage-scoped column")
  {
    const auto r = build_system({.integer = true, .scope = OptName {"stage"}},
                                /*solve=*/false);
    CHECK(r.integer_cols == 1);
    CHECK(r.registry_entries == 1);
    CHECK(r.scope == IntegerScope::Stage);
  }

  SUBCASE("relax: true keeps the columns continuous (Relaxed)")
  {
    const auto r =
        build_system({.integer = true, .relax = true}, /*solve=*/false);
    CHECK(r.integer_cols == 0);
    CHECK(r.registry_entries == 2);
    CHECK(r.domain == IntegerDomain::Relaxed);
  }

  SUBCASE("a continuous phase relaxes them")
  {
    const auto r = build_system({.integer = true, .continuous_phase = true},
                                /*solve=*/false);
    CHECK(r.integer_cols == 0);
    CHECK(r.domain == IntegerDomain::Relaxed);
  }
}

TEST_CASE("DecisionVariableLP integer — phase-scope cell column")  // NOLINT
{
  auto [sys, sim] =
      make_case({.integer = true, .scope = OptName {"phase"}, .upper = 1.0});
  Planning planning {
      .simulation = std::move(sim),
      .system = std::move(sys),
  };
  planning.options.model_options.scale_objective = 1.0;
  planning.options.lp_matrix_options.solver_name = mip_solver_name();
  PlanningLP planning_lp(std::move(planning));
  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  auto&& li = systems.front().front().linear_interface();
  CHECK(count_integer_cols(li) == 1);
  const auto& reg = planning_lp.simulation().integer_variables(SceneIndex {0},
                                                               PhaseIndex {0});
  REQUIRE(reg.size() == 1);
  CHECK(reg.begin()->second.scope() == IntegerScope::Phase);
  CHECK(reg.begin()->second.domain() == IntegerDomain::Binary);
}

TEST_CASE(
    "DecisionVariableLP integer — coarse state column may be integer")  // NOLINT
{
  // An integer coarse `state` column is legal: it is registered as both a
  // state and an integer variable (orthogonal registries).
  auto [sys, sim] = make_case({.integer = true, .scope = OptName {"phase"}});
  sys.decision_variable_array.front().state = OptBool {true};
  sys.decision_variable_array.front().link = OptBool {true};
  Planning planning {
      .simulation = std::move(sim),
      .system = std::move(sys),
  };
  planning.options.lp_matrix_options.solver_name = mip_solver_name();
  PlanningLP planning_lp(std::move(planning));
  auto&& li = planning_lp.systems().front().front().linear_interface();
  CHECK(count_integer_cols(li) == 1);
}

TEST_CASE(
    "DecisionVariableLP integer — MIP optimum vs LP relaxation")  // NOLINT
{
  if (mip_solver_name().empty()) {
    MESSAGE("Skipping MIP test — no MIP-capable solver loaded");
    return;
  }
  // LP relaxation: x = 2.5 on both blocks ⇒ obj = -5.
  const auto lp =
      build_system({.integer = true, .relax = true}, /*solve=*/true);
  // MIP: x = 2 on both blocks ⇒ obj = -4.
  const auto mip = build_system({.integer = true}, /*solve=*/true);
  // Continuous (default): same as the relaxation.
  const auto cont = build_system({}, /*solve=*/true);
  CHECK(lp.obj.value_or(0.0) == doctest::Approx(-5.0));
  CHECK(cont.obj.value_or(0.0) == doctest::Approx(-5.0));
  CHECK(mip.obj.value_or(0.0) == doctest::Approx(-4.0));
}
