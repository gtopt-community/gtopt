/**
 * @file      test_mip_polish.cpp
 * @brief     Post-MIP polish: fix the incumbent's integers, re-solve the LP
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Regression (CEN PCP 2026-04-22): CPLEX stopped at its time limit
 * (`CPXMIP_TIME_LIM_FEAS`) with an incumbent of 3.056e9 whose CONTINUOUS
 * part was far from optimal — the LP with the incumbent's integers fixed
 * solves to 1.577e9.  gtopt only ran the fixed-integer LP after a
 * proven-optimal MIP (and only when duals were requested), so it published
 * the raw incumbent.  `LinearInterface::polish_mip` now runs after ANY MIP
 * stop that holds an incumbent (`SolverBackend::has_mip_incumbent`).
 *
 * The fixture forces a bad incumbent deterministically: a MIP start whose
 * integer part is optimal (commit the cheap unit) but whose continuous part
 * dispatches the EXPENSIVE uncommitted unit, plus `mip_solution_limit = 1`
 * so the MIP stops right after accepting it.  The polish must recover the
 * cheap dispatch (objective strictly lower) and publish duals.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <cmath>
#include <optional>
#include <ranges>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/commitment.hpp>
#include <gtopt/generator.hpp>
#include <gtopt/json/json_solver_options.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/solution_csv.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

/// Every loaded MIP-capable backend except the LP-only CLP (never used for
/// MIP) and the GPU cuOpt (no solution-limit knob; device-dependent).
[[nodiscard]] std::vector<std::string> mip_backends()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  std::vector<std::string> out;
  for (const auto& name : reg.available_solvers()) {
    if (name != "clp" && name != "cuopt" && reg.supports_mip(name)) {
      out.push_back(name);
    }
  }
  return out;
}

constexpr double demand = 60.0;
constexpr double cheap_cost = 50.0;
constexpr double dear_cost = 200.0;
constexpr double commit_cost = 100.0;

/// Indices of the polish fixture's columns / rows.
struct PolishFixture
{
  FlatLinearProblem flat;
  ColIndex cheap {};
  ColIndex dear {};
  ColIndex commit {};
  RowIndex balance {};
};

/// Single bus, demand 60 (equality), two generators:
///   cheap ∈ [0, 100], cost 50, needs `commit` (binary, cost 100);
///   dear  ∈ [0, 100], cost 200, no commitment.
/// MIP optimum: commit = 1, cheap = 60 → 100 + 3000 = 3100.
/// Bad incumbent (the MIP start): commit = 1, dear = 60 → 100 + 12000.
[[nodiscard]] PolishFixture make_fixture()
{
  LinearProblem lp("mip_polish");
  PolishFixture fx;
  fx.cheap = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 100.0,
      .cost = cheap_cost,
  });
  fx.dear = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 100.0,
      .cost = dear_cost,
  });
  fx.commit = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 1.0,
      .cost = commit_cost,
      .is_integer = true,
  });

  auto balance = SparseRow {};
  balance[fx.cheap] = 1.0;
  balance[fx.dear] = 1.0;
  balance.equal(demand);
  fx.balance = lp.add_row(std::move(balance));

  auto gate = SparseRow {};
  gate[fx.cheap] = 1.0;
  gate[fx.commit] = -100.0;
  gate.less_equal(0.0);
  std::ignore = lp.add_row(std::move(gate));

  fx.flat = lp.flatten({
      .equilibration_method = LpEquilibrationMethod::none,
  });
  return fx;
}

}  // namespace

TEST_CASE("mip_polish_reserve")  // NOLINT
{
  CHECK(mip_polish_reserve(std::nullopt) == doctest::Approx(0.0));
  CHECK(mip_polish_reserve(0.0) == doctest::Approx(0.0));
  CHECK(mip_polish_reserve(-5.0) == doctest::Approx(0.0));
  CHECK(mip_polish_reserve(100.0) == doctest::Approx(5.0));
  // 5 % of 3600 s = 180 s, under the 300 s cap.
  CHECK(mip_polish_reserve(3600.0) == doctest::Approx(180.0));
  // 5 % of 36000 s = 1800 s, capped.
  CHECK(mip_polish_reserve(36000.0)
        == doctest::Approx(max_mip_polish_reserve_s));
}

TEST_CASE("SolverOptions mip_polish defaults, merge and JSON")  // NOLINT
{
  const SolverOptions dflt {};
  CHECK(dflt.mip_polish_enabled());
  CHECK_FALSE(dflt.mip_polish.has_value());
  CHECK_FALSE(dflt.mip_solution_limit.has_value());

  SolverOptions base {};
  base.overlay(SolverOptions {
      .mip_polish = false,
      .mip_solution_limit = 3,
  });
  CHECK_FALSE(base.mip_polish_enabled());
  CHECK(base.mip_solution_limit.value_or(0) == 3);

  SolverOptions merged {};
  merged.merge(SolverOptions {.mip_polish = false});
  CHECK_FALSE(merged.mip_polish_enabled());

  const auto parsed =
      daw::json::from_json<SolverOptions>(R"({"mip_polish": false})");
  CHECK(parsed.mip_polish.has_value());
  CHECK_FALSE(parsed.mip_polish_enabled());
  const auto back = daw::json::from_json<SolverOptions>(
      daw::json::to_json(SolverOptions {.mip_polish = true}));
  CHECK(back.mip_polish.value_or(false));
}

TEST_CASE("polish_mip is a no-op on a pure LP")  // NOLINT
{
  LinearProblem lp("pure_lp");
  const auto x = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 10.0,
      .cost = 1.0,
  });
  auto row = SparseRow {};
  row[x] = 1.0;
  row.greater_equal(3.0);
  std::ignore = lp.add_row(std::move(row));
  LinearInterface li("", lp.flatten({}));
  REQUIRE(li.initial_solve({}).has_value());
  CHECK_FALSE(li.has_mip_incumbent());
  const auto pol = li.polish_mip({});
  REQUIRE(pol.has_value());
  CHECK_FALSE(pol->attempted);
  CHECK_FALSE(pol->polished);
  CHECK_FALSE(li.last_mip_polish().attempted);
}

TEST_CASE(  // NOLINT
    "polish_mip re-optimises a bad incumbent after a solution-limit stop")
{
  const auto backends = mip_backends();
  if (backends.empty()) {
    MESSAGE("No MIP-capable solver plugin available — skipping");
    return;
  }

  for (const auto& solver : backends) {
    CAPTURE(solver);
    auto fx = make_fixture();
    LinearInterface li(solver, fx.flat);

    // Bad start: integer part optimal, continuous part on the dear unit.
    std::vector<double> start(static_cast<std::size_t>(li.get_numcols()), 0.0);
    start[static_cast<std::size_t>(fx.commit)] = 1.0;
    start[static_cast<std::size_t>(fx.dear)] = demand;
    const bool injected =
        li.set_mip_start(start, MipStartEffort::check_feasibility);

    const SolverOptions mip_opts {
        .presolve = false,
        .max_fallbacks = 0,
        .mip_solution_limit = 1,
    };
    // A solution-limit stop is non-optimal: resolve reports an error but
    // the backend keeps the incumbent.  A backend that ignored the start or
    // the limit may still reach the optimum — both are acceptable here;
    // the polish contract is checked either way.
    std::ignore = li.resolve(mip_opts);
    const bool stopped_early = !li.is_optimal();
    CAPTURE(injected);
    CAPTURE(stopped_early);

    if (stopped_early && !li.has_mip_incumbent()) {
      MESSAGE(solver << ": stopped without an incumbent — nothing to polish");
      continue;
    }
    REQUIRE(li.has_mip_incumbent());

    const double incumbent_obj = li.get_obj_value();
    auto pol = li.polish_mip({});
    REQUIRE(pol.has_value());
    CHECK(pol->attempted);
    REQUIRE(pol->polished);
    CHECK(pol->mip_optimal == !stopped_early);
    CHECK(pol->incumbent_obj == doctest::Approx(incumbent_obj));
    CHECK(li.last_mip_polish().polished);

    // Fixed-integer LP optimum: commit = 1, all demand on the cheap unit.
    const double optimum = commit_cost + (cheap_cost * demand);
    CHECK(pol->polished_obj == doctest::Approx(optimum).epsilon(1e-6));
    CHECK(pol->polished_obj <= incumbent_obj + 1e-6);
    CHECK(li.get_obj_value() == doctest::Approx(optimum).epsilon(1e-6));
    if (stopped_early && injected && incumbent_obj > optimum + 1.0) {
      // The bad start is still the incumbent (HiGHS may improve it before
      // honouring the solution limit): the polish must strictly improve.
      CHECK(pol->polished_obj < incumbent_obj - 1.0);
    }

    const auto sol = li.get_col_sol();
    CHECK(sol[fx.commit] == doctest::Approx(1.0));
    CHECK(sol[fx.cheap] == doctest::Approx(demand));
    CHECK(sol[fx.dear] == doctest::Approx(0.0));

    // Duals of the fixed LP: the balance LMP is the cheap unit's cost.
    const auto duals = li.get_row_dual();
    REQUIRE(duals.size() == 2);
    CHECK(std::abs(duals[fx.balance])
          == doctest::Approx(cheap_cost).epsilon(1e-4));
  }
}

TEST_CASE(  // NOLINT
    "CPLEX: has_mip_incumbent after CPXMIP_SOL_LIM and polish improves it")
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("cplex")) {
    MESSAGE("CPLEX plugin not available — skipping");
    return;
  }
  auto fx = make_fixture();
  LinearInterface li("cplex", fx.flat);
  std::vector<double> start(static_cast<std::size_t>(li.get_numcols()), 0.0);
  start[static_cast<std::size_t>(fx.commit)] = 1.0;
  start[static_cast<std::size_t>(fx.dear)] = demand;
  REQUIRE(li.set_mip_start(start, MipStartEffort::check_feasibility));

  const auto r = li.resolve(SolverOptions {
      .presolve = false,
      .max_fallbacks = 0,
      .mip_solution_limit = 1,
  });
  REQUIRE_FALSE(r.has_value());
  REQUIRE_FALSE(li.is_optimal());
  REQUIRE(li.has_mip_incumbent());
  const double incumbent_obj = li.get_obj_value();
  CHECK(incumbent_obj
        == doctest::Approx(commit_cost + (dear_cost * demand)).epsilon(1e-6));

  auto pol = li.polish_mip({});
  REQUIRE(pol.has_value());
  REQUIRE(pol->polished);
  CHECK_FALSE(pol->mip_optimal);
  CHECK(pol->polished_obj
        == doctest::Approx(commit_cost + (cheap_cost * demand)).epsilon(1e-6));
}

namespace
{

[[nodiscard]] Simulation polish_simulation()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {0},
                  .duration = 1.0,
              },
              {
                  .uid = Uid {1},
                  .duration = 1.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {0},
                  .first_block = 0,
                  .count_block = 2,
                  .chronological = true,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

[[nodiscard]] System polish_system(const std::string& solver)
{
  System system;
  system.name = "MipPolish_" + solver;
  system.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  system.demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .lmax = 60.0,
          .capacity = 100.0,
      },
  };
  system.generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .pmin = 0.0,
          .pmax = 100.0,
          .gcost = 50.0,
          .capacity = 100.0,
      },
  };
  system.commitment_array = {
      {
          .uid = Uid {1},
          .name = "cmt1",
          .generator = Uid {1},
          .startup_cost = 100.0,
          .pmin = 30.0,
          .initial_status = 0.0,
      },
  };
  return system;
}

}  // namespace

TEST_CASE(  // NOLINT
    "SystemLP::resolve polishes the MIP without dual output, opt-out")
{
  const auto backends = mip_backends();
  if (backends.empty()) {
    MESSAGE("No MIP-capable solver plugin available — skipping");
    return;
  }
  const auto& solver = backends.front();
  CAPTURE(solver);

  for (const bool polish : {true, false}) {
    CAPTURE(polish);
    PlanningOptions poptions;
    poptions.model_options.demand_fail_cost = 1000.0;
    poptions.model_options.use_single_bus = true;
    // Primal only: before the polish change the fixed-integer LP only ran
    // when dual / reduced-cost output was requested.
    poptions.write_out = OutputSelection {OutputFlags::solution};
    poptions.lp_matrix_options.solver_name = solver;
    PlanningOptionsLP options(std::move(poptions));
    const auto simulation = polish_simulation();
    SimulationLP simulation_lp(simulation, options);
    LpMatrixOptions flat_opts;
    flat_opts.solver_name = solver;
    SystemLP system_lp(polish_system(solver), simulation_lp, flat_opts);

    auto& li = system_lp.linear_interface();
    REQUIRE(li.has_integer_cols());

    const auto result = system_lp.resolve(SolverOptions {
        .time_limit = 60.0,
        .mip_polish = polish,
    });
    REQUIRE(result.has_value());
    REQUIRE(li.is_optimal());
    const auto& pol = li.last_mip_polish();
    if (polish) {
      CHECK(pol.attempted);
      CHECK(pol.polished);
      CHECK(pol.mip_optimal);
      CHECK(pol.polished_obj <= pol.incumbent_obj + 1e-6);
      // Integrality relaxed by the fixed-integer LP.
      CHECK_FALSE(li.has_integer_cols());
    } else {
      CHECK_FALSE(pol.attempted);
      CHECK(li.has_integer_cols());
    }
  }
}

TEST_CASE(  // NOLINT
    "SystemLP::resolve keeps a polished limit stop non-optimal")
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("cplex")) {
    MESSAGE("CPLEX plugin not available — skipping");
    return;
  }
  PlanningOptions poptions;
  poptions.model_options.demand_fail_cost = 1000.0;
  poptions.model_options.use_single_bus = true;
  poptions.write_out =
      OutputSelection {OutputFlags::solution | OutputFlags::dual};
  poptions.lp_matrix_options.solver_name = "cplex";
  PlanningOptionsLP options(std::move(poptions));
  const auto simulation = polish_simulation();
  SimulationLP simulation_lp(simulation, options);
  LpMatrixOptions flat_opts;
  flat_opts.solver_name = "cplex";
  SystemLP system_lp(polish_system("cplex"), simulation_lp, flat_opts);
  auto& li = system_lp.linear_interface();

  const auto result = system_lp.resolve(SolverOptions {
      .presolve = false,
      .max_fallbacks = 0,
      .mip_solution_limit = 1,
  });
  if (result.has_value()) {
    MESSAGE(
        "CPLEX reached the optimum before the solution limit — the "
        "non-optimal path is covered by the LinearInterface test");
    return;
  }
  const auto& pol = li.last_mip_polish();
  REQUIRE(pol.polished);
  CHECK_FALSE(pol.mip_optimal);
  CHECK(pol.polished_obj <= pol.incumbent_obj + 1e-6);
  CHECK(result.error().message.find("polished") != std::string::npos);
  // Duals of the polished LP are published.
  CHECK_FALSE(li.duals_unavailable());
  CHECK_FALSE(li.get_row_dual().empty());
}
