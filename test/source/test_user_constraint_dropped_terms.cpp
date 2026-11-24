// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Broad guard for user-constraint terms that resolve to no LP column.
//
//  (a) Intentional absences — an inactive element, a zero-capacity block,
//      a per-block omission — stay an implicit 0 in every mode, but are
//      counted per constraint and reported once at INFO.
//  (b) A registration / scope mismatch — the element is ACTIVE at the
//      block and its column was registered at an earlier stage of the same
//      LP, but not here — is an error in strict mode and a warning in
//      normal mode.
//  An unknown explicit id inside `sum(...)` warns.

#include <format>
#include <stdexcept>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>

#include "log_capture.hpp"

using namespace gtopt;
using gtopt::test::LogCapture;

namespace
{

/// One bus, one phase with two single-block stages, cheap g1 and dearer g2,
/// a 50 MW load; @p extra is spliced into the system body.
[[nodiscard]] std::string dropped_json(std::string_view mode,
                                       std::string_view g2_extra,
                                       std::string_view extra,
                                       std::string_view ucs)
{
  return std::format(R"js({{
  "options": {{
    "annual_discount_rate": 0.0,
    "constraint_mode": "{}",
    "model_options": {{ "use_single_bus": true, "scale_objective": 1,
                        "demand_fail_cost": 1000, "lp_reduction": true }}
  }},
  "simulation": {{
    "block_array": [ {{ "uid": 1, "duration": 1 }}, {{ "uid": 2, "duration": 1 }} ],
    "stage_array": [
      {{ "uid": 1, "first_block": 0, "count_block": 1 }},
      {{ "uid": 2, "first_block": 1, "count_block": 1 }}
    ],
    "scenario_array": [ {{ "uid": 1 }} ]
  }},
  "system": {{
    "name": "uc_dropped_terms",
    "bus_array": [ {{ "uid": 1, "name": "b1" }} ],
    "demand_array": [ {{ "uid": 1, "name": "d1", "bus": 1, "capacity": 50 }} ],
    "generator_array": [
      {{ "uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100 }},
      {{ "uid": 2, "name": "g2", "bus": 1, "gcost": 20, "capacity": 100 {} }}
    ]{},
    "user_constraint_array": [ {} ]
  }}
}})js",
                     mode,
                     g2_extra,
                     extra,
                     ucs);
}

constexpr std::string_view g2_cap_uc =
    R"js({ "uid": 1, "name": "g2cap",
           "expression": "generator('g2').generation <= 30" })js";

[[nodiscard]] std::size_t dropped(const PlanningLP& plp,
                                  const std::string& uc,
                                  const std::string& reason)
{
  const auto counts = plp.simulation().dropped_term_counts();
  const auto it = counts.find(uc);
  if (it == counts.end()) {
    return 0;
  }
  const auto rit = it->second.find(reason);
  return rit == it->second.end() ? 0 : rit->second;
}

/// Reserve zone active in stage 1 only: its provision (active in both
/// stages) builds the `up` column in stage 1 and none in stage 2.
constexpr std::string_view reserve_extra = R"js(,
    "reserve_zone_array": [ { "uid": 1, "name": "rz1", "active": [1, 0],
                              "urreq": 10, "urcost": 1000 } ],
    "reserve_provision_array": [ { "uid": 1, "name": "rp1",
        "generator": "g2", "reserve_zones": [1], "urmax": 50,
        "ur_provision_factor": 1, "urcost": 1 } ])js";

constexpr std::string_view rp_cap_uc =
    R"js({ "uid": 1, "name": "rpcap",
           "expression": "reserve_provision('rp1').up <= 30" })js";

}  // namespace

TEST_CASE("Dropped UC terms: inactive element is an implicit 0")  // NOLINT
{
  LogCapture logs(1024);
  PlanningLP plp(parse_planning_json(
      dropped_json("strict", R"(, "active": [1, 0])", "", g2_cap_uc)));
  REQUIRE(plp.resolve().has_value());
  CHECK(dropped(plp, "g2cap", "inactive element") == 1);
  CHECK(logs.contains("user_constraint 'g2cap': 1 term occurrence(s)"));
  CHECK(logs.contains("inactive element: 1"));
}

TEST_CASE("Dropped UC terms: zero-capacity block is an implicit 0")  // NOLINT
{
  PlanningLP plp(parse_planning_json(
      dropped_json("strict", R"(, "pmax": [[100], [0]])", "", g2_cap_uc)));
  REQUIRE(plp.resolve().has_value());
  CHECK(dropped(plp, "g2cap", "zero capacity") == 1);
  CHECK(dropped(plp, "g2cap", "scope mismatch") == 0);
}

TEST_CASE("Dropped UC terms: registration mismatch, strict error")  // NOLINT
{
  CHECK_THROWS_WITH_AS(PlanningLP(parse_planning_json(dropped_json(
                           "strict", "", reserve_extra, rp_cap_uc))),
                       doctest::Contains("registration / scope mismatch"),
                       std::runtime_error);
}

TEST_CASE("Dropped UC terms: registration mismatch, normal warning")  // NOLINT
{
  LogCapture logs(1024);
  PlanningLP plp(parse_planning_json(
      dropped_json("normal", "", reserve_extra, rp_cap_uc)));
  CHECK(logs.contains("registration / scope mismatch"));
  CHECK(logs.contains("constraint_mode=normal"));
  REQUIRE(plp.resolve().has_value());
  // Not counted as an intentional drop.
  CHECK(dropped(plp, "rpcap", "scope mismatch") == 0);
}

TEST_CASE("Dropped UC terms: mismatch inside sum(...) is caught too")  // NOLINT
{
  constexpr std::string_view uc =
      R"js({ "uid": 1, "name": "rpsum",
             "expression": "sum(reserve_provision('rp1').up) <= 30" })js";
  CHECK_THROWS_WITH_AS(PlanningLP(parse_planning_json(
                           dropped_json("strict", "", reserve_extra, uc))),
                       doctest::Contains("registration / scope mismatch"),
                       std::runtime_error);
}

TEST_CASE("Dropped UC terms: unknown explicit id inside sum warns")  // NOLINT
{
  constexpr std::string_view uc =
      R"js({ "uid": 1, "name": "gsum",
             "expression": "sum(generator('g1', 'nope').generation) <= 80" })js";
  LogCapture logs(1024);
  PlanningLP plp(parse_planning_json(dropped_json("strict", "", "", uc)));
  CHECK(logs.contains("names 'nope'"));
  REQUIRE(plp.resolve().has_value());
}

TEST_CASE("Dropped UC terms: inactive element inside sum is counted")  // NOLINT
{
  constexpr std::string_view uc =
      R"js({ "uid": 1, "name": "gsum",
             "expression": "sum(generator('g1', 'g2').generation) <= 80" })js";
  PlanningLP plp(parse_planning_json(
      dropped_json("strict", R"(, "active": [1, 0])", "", uc)));
  REQUIRE(plp.resolve().has_value());
  CHECK(dropped(plp, "gsum", "inactive element") == 1);
}
