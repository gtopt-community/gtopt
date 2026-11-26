// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_deterministic_parallel.cpp
 * @brief     SolverOptions::deterministic_parallel: plumbing and solves
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `deterministic_parallel = true` maps to CPLEX
 * `CPX_PARAM_PARALLELMODE = CPX_PARALLEL_DETERMINISTIC`; unset / false
 * keeps gtopt's opportunistic default.  Under machine load the
 * opportunistic barrier + crossover ended exp_bat_4b_24 (a dual-degenerate
 * LP) on a different optimal basis in 6 runs out of 100, flipping the
 * reduced costs its golden pins.  These tests cover the option's
 * plumbing (JSON, --set, merge / overlay, equality) and that a solve
 * with it is optimal and repeatable on every backend; the e2e runner
 * (integration_test/cmake/run_gtopt.cmake) sets it for the golden tests.
 *
 * Degenerate LP (two buses' worth of a dispatch, equal-cost units):
 *   min 10 g1 + 10 g2 + 30 g3
 *   s.t. g1 + g2 + g3 = 100          (balance)
 *        g1 + g2      <= 100         (binding at the optimum: degenerate)
 *        g1 <= 60, g2 <= 60, g3 <= 100
 * Optimum 1000 with any g1 + g2 = 100 split; the balance / cap duals
 * share the 10 $/MWh between them in more than one way.
 */

#include <array>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

constexpr auto dp_test_json = R"(
  {
    "options": {
      "output_compression": "uncompressed",
      "model_options": {
        "demand_fail_cost": 1000
      }
    },
    "simulation": {
      "block_array": [{"uid": 1, "duration": 1}],
      "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
      "scenario_array": [{"uid": 1}]
    },
    "system": {
      "name": "dp_test",
      "bus_array": [{"uid": 1, "name": "b1"}],
      "generator_array": [
        {"uid": 1, "name": "g1", "bus": 1, "gcost": 10.0, "capacity": 200.0}
      ],
      "demand_array": [
        {"uid": 1, "name": "d1", "bus": 1, "capacity": 50.0}
      ]
    }
  }
)";

[[nodiscard]] bool have_solver(const std::string& name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver(name);
}

void add_degenerate_lp(LinearInterface& li)
{
  const std::array<double, 3> cost {
      10.0,
      10.0,
      30.0,
  };
  const std::array<double, 3> uppb {
      60.0,
      60.0,
      100.0,
  };
  std::array<ColIndex, 3> cols {};
  for (std::size_t j = 0; j < cols.size(); ++j) {
    cols[j] = li.add_col(SparseCol {
        .uppb = uppb[j],
        .cost = cost[j],
    });
  }
  SparseRow balance;
  for (const auto c : cols) {
    balance[c] = 1.0;
  }
  balance.lowb = 100.0;
  balance.uppb = 100.0;
  (void)li.add_row(balance);

  SparseRow cap;
  cap[cols[0]] = 1.0;
  cap[cols[1]] = 1.0;
  cap.lowb = -LinearProblem::DblMax;
  cap.uppb = 100.0;
  (void)li.add_row(cap);
}

struct LpSnapshot
{
  double obj {};
  std::vector<double> sol;
  std::vector<double> dual;
  std::vector<double> rcost;
};

[[nodiscard]] std::optional<LpSnapshot> solve_degenerate(
    const std::string& solver, const SolverOptions& opts)
{
  LinearInterface li(solver);
  add_degenerate_lp(li);
  const auto res = li.initial_solve(opts);
  if (!res.has_value() || !li.is_optimal()) {
    return std::nullopt;
  }
  const auto sol = li.get_col_sol_raw();
  const auto dual = li.get_row_dual_raw();
  const auto rcost = li.get_col_cost_raw();
  return LpSnapshot {
      .obj = li.get_obj_value(),
      .sol = {sol.begin(), sol.end()},
      .dual = {dual.begin(), dual.end()},
      .rcost = {rcost.begin(), rcost.end()},
  };
}

}  // namespace

TEST_CASE("deterministic_parallel defaults to unset (opportunistic)")  // NOLINT
{
  const SolverOptions so {};
  CHECK_FALSE(so.deterministic_parallel.has_value());
  CHECK_FALSE(so.deterministic_parallel_enabled());

  SolverOptions on {};
  on.deterministic_parallel = true;
  CHECK(on.deterministic_parallel_enabled());

  SolverOptions off {};
  off.deterministic_parallel = false;
  CHECK_FALSE(off.deterministic_parallel_enabled());

  // Equality sees the field: the CPLEX backend's "same options, skip the
  // re-apply" shortcut must not swallow a toggle.
  CHECK_FALSE(so == on);
  CHECK_FALSE(on == off);
  CHECK_FALSE(so == off);  // unset and explicit false are distinct requests
}

TEST_CASE("deterministic_parallel merge: a set value wins")  // NOLINT
{
  // Same rule as every optional SolverOptions field (merge_opt): a set
  // value in `other` replaces this one, an unset one leaves it alone.
  SolverOptions base {};
  SolverOptions other {};
  other.deterministic_parallel = true;
  base.merge(other);
  CHECK(base.deterministic_parallel.value_or(false) == true);

  SolverOptions empty {};
  base.merge(empty);
  CHECK(base.deterministic_parallel.value_or(false) == true);

  SolverOptions later {};
  later.deterministic_parallel = false;
  base.merge(later);
  CHECK(base.deterministic_parallel.value_or(true) == false);
}

TEST_CASE("deterministic_parallel overlay: user wins when set")  // NOLINT
{
  SolverOptions backend {};
  backend.deterministic_parallel = false;

  SolverOptions user_unset {};
  auto a = backend;
  a.overlay(user_unset);
  CHECK(a.deterministic_parallel.value_or(true) == false);

  SolverOptions user_on {};
  user_on.deterministic_parallel = true;
  auto b = backend;
  b.overlay(user_on);
  CHECK(b.deterministic_parallel_enabled());

  SolverOptions none {};
  none.overlay(user_on);
  CHECK(none.deterministic_parallel_enabled());
}

TEST_CASE("deterministic_parallel JSON round-trip")  // NOLINT
{
  for (const bool value : {true, false}) {
    CAPTURE(value);
    const auto json = std::string {R"({"deterministic_parallel":)"}
        + (value ? "true" : "false") + "}";
    const auto so =
        daw::json::from_json<SolverOptions>(std::string_view {json});
    CHECK(so.deterministic_parallel == value);
    const auto back =
        daw::json::from_json<SolverOptions>(daw::json::to_json(so));
    CHECK(back.deterministic_parallel == value);
  }

  const auto unset =
      daw::json::from_json<SolverOptions>(std::string_view {"{}"});
  CHECK_FALSE(unset.deterministic_parallel.has_value());
  const auto unset_back =
      daw::json::from_json<SolverOptions>(daw::json::to_json(unset));
  CHECK_FALSE(unset_back.deterministic_parallel.has_value());
}

TEST_CASE("deterministic_parallel in a planning JSON")  // NOLINT
{
  const auto planning = daw::json::from_json<Planning>(
      std::string_view {R"({"options":{"solver_options":)"
                        R"({"deterministic_parallel":true}}})"},
      StrictParsePolicy);
  CHECK(planning.options.solver_options.deterministic_parallel_enabled());
}

TEST_CASE("--set solver_options.deterministic_parallel lands")  // NOLINT
{
  auto planning = daw::json::from_json<Planning>(
      std::string_view {dp_test_json}, StrictParsePolicy);
  const auto& so = planning.options.solver_options;
  CHECK_FALSE(so.deterministic_parallel.has_value());

  REQUIRE(apply_set_options(planning,
                            {"solver_options.deterministic_parallel=true"}));
  CHECK(so.deterministic_parallel == true);
  // --set must not disturb the other solver fields (threads stays the
  // backend sentinel 0: the e2e runner relies on that).
  CHECK(so.threads == 0);

  REQUIRE(apply_set_options(planning,
                            {"solver_options.deterministic_parallel=false"}));
  CHECK(so.deterministic_parallel == false);

  CHECK_FALSE(apply_set_options(
      planning, {"solver_options.deterministic_parallel=maybe"}));
}

TEST_CASE("--set deterministic_parallel per pass")  // NOLINT
{
  auto planning = daw::json::from_json<Planning>(
      std::string_view {dp_test_json}, StrictParsePolicy);
  REQUIRE(apply_set_options(
      planning,
      {
          "sddp_options.forward_solver_options.deterministic_parallel=true",
          "sddp_options.backward_solver_options.deterministic_parallel=false",
          "monolithic_options.solver_options.deterministic_parallel=true",
      }));
  const auto& sddp = planning.options.sddp_options;
  CHECK(sddp.forward_solver_options.value_or(SolverOptions {})
            .deterministic_parallel
        == true);
  CHECK(sddp.backward_solver_options.value_or(SolverOptions {})
            .deterministic_parallel
        == false);
  CHECK(planning.options.monolithic_options.solver_options
            .value_or(SolverOptions {})
            .deterministic_parallel
        == true);
}

TEST_CASE(
    "deterministic_parallel: every value solves the degenerate LP")  // NOLINT
{
  for (const std::string solver : {"cplex", "highs", "clp"}) {
    if (!have_solver(solver)) {
      continue;
    }
    for (const auto algo : {LPAlgo::barrier, LPAlgo::dual, LPAlgo::primal}) {
      for (const std::optional<bool> det : {std::optional<bool> {},
                                            std::optional<bool> {false},
                                            std::optional<bool> {true}})
      {
        CAPTURE(solver);
        CAPTURE(static_cast<int>(algo));
        CAPTURE(det.has_value() ? static_cast<int>(*det) : -1);
        const auto snap = solve_degenerate(solver,
                                           SolverOptions {
                                               .algorithm = algo,
                                               .threads = 4,
                                               .deterministic_parallel = det,
                                           });
        REQUIRE(snap.has_value());
        CHECK(snap.value_or(LpSnapshot {}).obj == doctest::Approx(1000.0));
      }
    }
  }
}

TEST_CASE(
    "deterministic_parallel=true: repeated multi-threaded solves are "
    "identical")  // NOLINT
{
  for (const std::string solver : {"cplex", "highs"}) {
    if (!have_solver(solver)) {
      continue;
    }
    CAPTURE(solver);
    const SolverOptions opts {
        .algorithm = LPAlgo::barrier,
        .threads = 4,
        .deterministic_parallel = true,
    };
    const auto first = solve_degenerate(solver, opts);
    REQUIRE(first.has_value());
    const auto ref = first.value_or(LpSnapshot {});
    for (int rep = 0; rep < 10; ++rep) {
      CAPTURE(rep);
      const auto again = solve_degenerate(solver, opts);
      REQUIRE(again.has_value());
      const auto snap = again.value_or(LpSnapshot {});
      // Bit-identical, not Approx: the point is reproducibility.
      CHECK(snap.sol == ref.sol);
      CHECK(snap.dual == ref.dual);
      CHECK(snap.rcost == ref.rcost);
    }
  }
}

TEST_CASE(
    "deterministic_parallel survives clone and option toggles (CPLEX)")  // NOLINT
{
  if (!have_solver("cplex")) {
    return;
  }
  LinearInterface li("cplex");
  add_degenerate_lp(li);
  SolverOptions opts {
      .algorithm = LPAlgo::barrier,
      .threads = 4,
      .deterministic_parallel = true,
  };
  REQUIRE(li.initial_solve(opts).has_value());
  REQUIRE(li.is_optimal());
  CHECK(li.get_obj_value() == doctest::Approx(1000.0));

  auto cloned = li.clone();
  REQUIRE(cloned.resolve(opts).has_value());
  REQUIRE(cloned.is_optimal());
  CHECK(cloned.get_obj_value() == doctest::Approx(1000.0));

  // true -> false -> true on the same interface: each re-apply lands
  // (the equality shortcut sees the change) and stays optimal.
  for (const bool det : {false, true, false, true}) {
    CAPTURE(det);
    opts.deterministic_parallel = det;
    REQUIRE(li.resolve(opts).has_value());
    REQUIRE(li.is_optimal());
    CHECK(li.get_obj_value() == doctest::Approx(1000.0));
  }
}
