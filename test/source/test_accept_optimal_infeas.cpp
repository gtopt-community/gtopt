// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_accept_optimal_infeas.cpp
 * @brief     SolverOptions::accept_optimal_infeas: plumbing and solves
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * CPLEX ends some numerically hard LPs with CPX_STAT_OPTIMAL_INFEAS (5,
 * optimal with unscaled infeasibilities).  gtopt used to treat it as not
 * optimal and run the barrier -> dual -> primal fallback cycle (the CEN
 * PCP corridor variants: 1036 s first solve, then two presolve-off
 * fallback solves, then a numerical "infeasible").  Now status 5 is
 * accepted by default with a warning carrying the max unscaled
 * infeasibilities; `accept_optimal_infeas = false` restores the old
 * rejection.  Every fallback cycle also logs each attempt's native
 * backend status at WARN.
 *
 * Status 5 cannot be produced on demand by a small LP, so these tests
 * cover the plumbing (defaults, equality, merge / overlay, JSON, --set)
 * and that the option never changes an ordinary optimal solve.
 */

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

#include "log_capture.hpp"

using namespace gtopt;

namespace
{

constexpr auto aoi_test_json = R"(
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
      "name": "aoi_test",
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

[[nodiscard]] bool have_aoi_solver(const std::string& name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver(name);
}

/// min x + 2y  s.t.  x + y >= 1,  x - y <= 0.5,  x, y in [0, 10].
/// Optimum 1.25 at (0.75, 0.25).
void add_aoi_lp(LinearInterface& li)
{
  const auto x = li.add_col(SparseCol {
      .uppb = 10.0,
      .cost = 1.0,
  });
  const auto y = li.add_col(SparseCol {
      .uppb = 10.0,
      .cost = 2.0,
  });
  SparseRow r1;
  r1[x] = 1.0;
  r1[y] = 1.0;
  r1.lowb = 1.0;
  r1.uppb = LinearProblem::DblMax;
  (void)li.add_row(r1);
  SparseRow r2;
  r2[x] = 1.0;
  r2[y] = -1.0;
  r2.lowb = -LinearProblem::DblMax;
  r2.uppb = 0.5;
  (void)li.add_row(r2);
}

}  // namespace

TEST_CASE("accept_optimal_infeas defaults to unset (accept)")  // NOLINT
{
  const SolverOptions so {};
  CHECK_FALSE(so.accept_optimal_infeas.has_value());
  CHECK(so.accept_optimal_infeas_enabled());

  SolverOptions on {};
  on.accept_optimal_infeas = true;
  CHECK(on.accept_optimal_infeas_enabled());

  SolverOptions off {};
  off.accept_optimal_infeas = false;
  CHECK_FALSE(off.accept_optimal_infeas_enabled());

  // Equality sees the field, so the CPLEX "same options" shortcut in
  // apply_options never swallows a toggle.
  CHECK_FALSE(so == on);
  CHECK_FALSE(on == off);
  CHECK_FALSE(so == off);
}

TEST_CASE("accept_optimal_infeas merge and overlay")  // NOLINT
{
  SolverOptions base {};
  SolverOptions other {};
  other.accept_optimal_infeas = true;
  base.merge(other);
  CHECK(base.accept_optimal_infeas.value_or(false));

  SolverOptions empty {};
  base.merge(empty);
  CHECK(base.accept_optimal_infeas.value_or(false));

  SolverOptions later {};
  later.accept_optimal_infeas = false;
  base.merge(later);
  CHECK_FALSE(base.accept_optimal_infeas.value_or(true));

  SolverOptions backend {};
  backend.accept_optimal_infeas = false;
  auto a = backend;
  a.overlay(SolverOptions {});
  CHECK_FALSE(a.accept_optimal_infeas.value_or(true));
  auto b = backend;
  b.overlay(other);
  CHECK(b.accept_optimal_infeas_enabled());
}

TEST_CASE("accept_optimal_infeas JSON round-trip")  // NOLINT
{
  for (const bool value : {true, false}) {
    CAPTURE(value);
    const auto json = std::string {R"({"accept_optimal_infeas":)"}
        + (value ? "true" : "false") + "}";
    const auto so =
        daw::json::from_json<SolverOptions>(std::string_view {json});
    CHECK(so.accept_optimal_infeas == value);
    const auto back =
        daw::json::from_json<SolverOptions>(daw::json::to_json(so));
    CHECK(back.accept_optimal_infeas == value);
  }
  const auto unset =
      daw::json::from_json<SolverOptions>(std::string_view {"{}"});
  CHECK_FALSE(unset.accept_optimal_infeas.has_value());
  const auto unset_back =
      daw::json::from_json<SolverOptions>(daw::json::to_json(unset));
  CHECK_FALSE(unset_back.accept_optimal_infeas.has_value());

  const auto planning = daw::json::from_json<Planning>(
      std::string_view {R"({"options":{"solver_options":)"
                        R"({"accept_optimal_infeas":true}}})"},
      StrictParsePolicy);
  CHECK(planning.options.solver_options.accept_optimal_infeas_enabled());
}

TEST_CASE("--set accept_optimal_infeas lands, also per pass")  // NOLINT
{
  auto planning = daw::json::from_json<Planning>(
      std::string_view {aoi_test_json}, StrictParsePolicy);
  const auto& so = planning.options.solver_options;
  CHECK_FALSE(so.accept_optimal_infeas.has_value());

  REQUIRE(apply_set_options(planning,
                            {"solver_options.accept_optimal_infeas=true"}));
  CHECK(so.accept_optimal_infeas == true);
  REQUIRE(apply_set_options(planning,
                            {"solver_options.accept_optimal_infeas=false"}));
  CHECK(so.accept_optimal_infeas == false);
  CHECK_FALSE(apply_set_options(
      planning, {"solver_options.accept_optimal_infeas=maybe"}));

  REQUIRE(apply_set_options(
      planning,
      {
          "sddp_options.forward_solver_options.accept_optimal_infeas=true",
          "monolithic_options.solver_options.accept_optimal_infeas=true",
      }));
  CHECK(planning.options.sddp_options.forward_solver_options
            .value_or(SolverOptions {})
            .accept_optimal_infeas
        == true);
  CHECK(planning.options.monolithic_options.solver_options
            .value_or(SolverOptions {})
            .accept_optimal_infeas
        == true);
}

TEST_CASE(  // NOLINT
    "accept_optimal_infeas never changes an ordinary optimal solve")
{
  for (const std::string solver : {"cplex", "highs", "clp"}) {
    if (!have_aoi_solver(solver)) {
      continue;
    }
    for (const auto algo : {LPAlgo::barrier, LPAlgo::dual, LPAlgo::primal}) {
      for (const std::optional<bool> accept : {std::optional<bool> {},
                                               std::optional<bool> {false},
                                               std::optional<bool> {true}})
      {
        CAPTURE(solver);
        CAPTURE(static_cast<int>(algo));
        CAPTURE(accept.has_value() ? static_cast<int>(*accept) : -1);
        LinearInterface li(solver);
        add_aoi_lp(li);
        const SolverOptions opts {
            .algorithm = algo,
            .threads = 1,
            .accept_optimal_infeas = accept,
        };
        const auto res = li.initial_solve(opts);
        REQUIRE(res.has_value());
        CHECK(res.value_or(-1) == 0);
        CHECK(li.is_optimal());
        CHECK(li.get_status() == 0);
        CHECK(li.get_obj_value() == doctest::Approx(1.25));
        CHECK(li.solver_stats().fallback_solves == 0);

        // A resolve with the option toggled stays optimal too.
        auto toggled = opts;
        toggled.accept_optimal_infeas = !accept.value_or(true);
        REQUIRE(li.resolve(toggled).has_value());
        CHECK(li.is_optimal());
        CHECK(li.get_obj_value() == doctest::Approx(1.25));
      }
    }
  }
}

namespace
{

/// Infeasible LP: x >= 2 and x <= 1 (as rows), x in [0, 10].
void add_infeasible_lp(LinearInterface& li)
{
  const auto x = li.add_col(SparseCol {
      .uppb = 10.0,
      .cost = 1.0,
  });
  SparseRow lo;
  lo[x] = 1.0;
  lo.lowb = 2.0;
  lo.uppb = LinearProblem::DblMax;
  (void)li.add_row(lo);
  SparseRow hi;
  hi[x] = 1.0;
  hi.lowb = -LinearProblem::DblMax;
  hi.uppb = 1.0;
  (void)li.add_row(hi);
}

[[nodiscard]] std::size_t count_containing(const test::LogCapture& logs,
                                           std::string_view needle)
{
  std::size_t n = 0;
  for (const auto& m : logs.messages()) {
    if (m.contains(needle)) {
      ++n;
    }
  }
  return n;
}

}  // namespace

TEST_CASE(  // NOLINT
    "fallback cycle logs every attempt's native status at WARN")
{
  for (const std::string solver : {"cplex", "highs"}) {
    if (!have_aoi_solver(solver)) {
      continue;
    }
    CAPTURE(solver);
    for (const bool use_resolve : {false, true}) {
      CAPTURE(use_resolve);
      LinearInterface li(solver);
      add_infeasible_lp(li);
      const SolverOptions opts {
          .algorithm = LPAlgo::barrier,
          .threads = 1,
          .max_fallbacks = 2,
      };
      const test::LogCapture logs(4096);
      const auto res = use_resolve ? li.resolve(opts) : li.initial_solve(opts);
      CHECK_FALSE(res.has_value());
      CHECK_FALSE(li.is_optimal());
      CHECK(li.solver_stats().fallback_solves >= 2);

      // One WARN summary line naming every attempt with its native
      // status code and name, and the outcome.
      const std::string_view cycle = use_resolve
          ? "resolve fallback cycle: "
          : "initial_solve fallback cycle: ";
      CHECK(count_containing(logs, cycle) == 1);
      bool found = false;
      for (const auto& m : logs.messages()) {
        if (!m.contains(cycle)) {
          continue;
        }
        found = true;
        CHECK(m.contains("barrier: status "));
        CHECK(m.contains(" -> dual: status "));
        CHECK(m.contains(" -> primal: status "));
        CHECK(m.contains("final: non-optimal"));
        CHECK_FALSE(m.contains("status -1 'n/a'"));  // native status known
        if (solver == "cplex") {
          // CPX_STAT_INFEASIBLE (3) or CPX_STAT_INForUNBD (4).
          CHECK((m.contains("status 3 '") || m.contains("status 4 '")));
        }
      }
      CHECK(found);
    }
  }
}

TEST_CASE("optimal solve logs no fallback cycle")  // NOLINT
{
  for (const std::string solver : {"cplex", "highs"}) {
    if (!have_aoi_solver(solver)) {
      continue;
    }
    CAPTURE(solver);
    LinearInterface li(solver);
    add_aoi_lp(li);
    const test::LogCapture logs(4096);
    REQUIRE(li.resolve(SolverOptions {
                           .algorithm = LPAlgo::barrier,
                           .threads = 1,
                       })
                .has_value());
    CHECK(count_containing(logs, "fallback cycle") == 0);
    CHECK(count_containing(logs, "non-optimal with") == 0);
  }
}
