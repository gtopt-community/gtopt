// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_user_constraint_sum_predicates.cpp
 * @brief     Every comparison operator of a `sum(... : pred)` filter
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The existing filter tests only use `=` and `in`, and only check that
 * the LP solves.  Here the filter picks which generators are forced to
 * zero by `sum(generator(all : PRED).generation) <= 0`, and the objective
 * reveals the selected set exactly (multi-bus network so the `bus`
 * metadata keeps the generator's own bus uid): the cheapest generator NOT
 * matched by the predicate serves the whole 100 MW load.
 *
 *   g1  type "b"  bus 2  10 $/MWh
 *   g2  type "a"  bus 1  20 $/MWh
 *   g3  type "c"  bus 3  30 $/MWh
 */

#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace uc_sum_pred_test
{
namespace
{

[[nodiscard]] double solve_obj(std::string_view pred)
{
  const auto json = std::format(
      R"({{
    "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
                "model_options": {{"use_single_bus": false,
                                  "use_kirchhoff": false,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
    "simulation": {{
      "block_array": [{{"uid": 1, "duration": 1}}],
      "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
      "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
    "system": {{
      "name": "uc_sum_pred",
      "bus_array": [{{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}},
                    {{"uid": 3, "name": "b3"}}],
      "line_array": [
        {{"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
          "tmax_ab": 1000, "tmax_ba": 1000, "capacity": 1000}},
        {{"uid": 2, "name": "l13", "bus_a": "b1", "bus_b": "b3",
          "tmax_ab": 1000, "tmax_ba": 1000, "capacity": 1000}}],
      "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 100}}],
      "generator_array": [
        {{"uid": 1, "name": "g1", "bus": "b2", "gcost": 10, "capacity": 200,
          "type": "b"}},
        {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 20, "capacity": 200,
          "type": "a"}},
        {{"uid": 3, "name": "g3", "bus": "b3", "gcost": 30, "capacity": 200,
          "type": "c"}}],
      "user_constraint_array": [{{"uid": 1, "name": "block",
        "expression": "sum(generator(all : {}).generation) <= 0"}}]}}
  }})",
      pred);
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  return plp.systems().front().front().linear_interface().get_obj_value();
}

}  // namespace
}  // namespace uc_sum_pred_test

TEST_CASE("UserConstraint sum filter - string predicates")  // NOLINT
{
  using namespace uc_sum_pred_test;
  CHECK(solve_obj("type = 'b'") == doctest::Approx(2000.0));  // {g1}
  CHECK(solve_obj("type != 'b'") == doctest::Approx(1000.0));  // {g2,g3}
  CHECK(solve_obj("type < 'c'") == doctest::Approx(3000.0));  // {g1,g2}
  CHECK(solve_obj("type <= 'b'") == doctest::Approx(3000.0));  // {g1,g2}
  CHECK(solve_obj("type > 'a'") == doctest::Approx(2000.0));  // {g1,g3}
  CHECK(solve_obj("type >= 'b'") == doctest::Approx(2000.0));  // {g1,g3}
}

TEST_CASE("UserConstraint sum filter - numeric predicates")  // NOLINT
{
  using namespace uc_sum_pred_test;
  CHECK(solve_obj("bus = 2") == doctest::Approx(2000.0));  // {g1}
  CHECK(solve_obj("bus != 2") == doctest::Approx(1000.0));  // {g2,g3}
  CHECK(solve_obj("bus < 3") == doctest::Approx(3000.0));  // {g1,g2}
  CHECK(solve_obj("bus <= 2") == doctest::Approx(3000.0));  // {g1,g2}
  CHECK(solve_obj("bus > 1") == doctest::Approx(2000.0));  // {g1,g3}
  CHECK(solve_obj("bus >= 2") == doctest::Approx(2000.0));  // {g1,g3}
}

TEST_CASE("UserConstraint sum filter - set membership")  // NOLINT
{
  using namespace uc_sum_pred_test;
  CHECK(solve_obj("type in {'b', 'c'}") == doctest::Approx(2000.0));
  // Numeric metadata is stringified for `in`.
  CHECK(solve_obj("bus in {2, 3}") == doctest::Approx(2000.0));
}

TEST_CASE(
    "UserConstraint sum filter - type mismatches match nothing")  // NOLINT
{
  using namespace uc_sum_pred_test;
  // Numeric RHS against string metadata, and vice versa: no generator is
  // selected, so the cheapest one (g1) serves the load.
  CHECK(solve_obj("type = 5") == doctest::Approx(1000.0));
  CHECK(solve_obj("bus = 'b2'") == doctest::Approx(1000.0));
  // Unknown attribute: no metadata entry, predicate fails.
  CHECK(solve_obj("color = 'red'") == doctest::Approx(1000.0));
}
