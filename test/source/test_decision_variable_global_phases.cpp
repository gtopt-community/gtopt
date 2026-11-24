// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// A `scope: "global"` DecisionVariable is ONE value for the whole horizon.
// With several phases each (scene, phase) LP holds a copy; without an
// explicit `state`, the copies are chained as an implicit `state: true,
// link: true` variable (phase p pinned to phase p−1, carried by SDDP cuts)
// and its cost is charged once, in the first phase.
//
// Case — an LNG cargo calendar decided once: `cargo` ∈ [0, 100] MWh/h at
// 1 $/unit (raw) caps the LNG unit (10 $/MWh) in every block; a 100 $/MWh
// peaker covers the rest.  Load: 50 MW (stage 1), 80 MW (stage 2).
//   true optimum (one LP):   cargo = 80, obj = 10·50 + 10·80 + 80 = 1380
//   SDDP over 2 phases:      converges to the same 1380, cargo = 80 in both
//   monolithic, 2 phases:    phases are solved in sequence (myopic): phase 1
//                            picks cargo = 50, phase 2 is pinned to 50 and
//                            buys 30 MWh at 100 → the SAME value in both.

#include <format>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/decision_variable_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

// clang-format off
constexpr std::string_view lng_one_phase = R"js(
  "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 } ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 1 },
    { "uid": 2, "first_block": 1, "count_block": 1 }
  ],
  "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
)js";

constexpr std::string_view lng_two_phases = R"js(
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
// clang-format on

[[nodiscard]] std::string lng_json(std::string_view sim)
{
  return std::format(R"js({{
  "options": {{
    "annual_discount_rate": 0.0,
    "output_format": "csv",
    "output_compression": "uncompressed",
    "model_options": {{ "use_single_bus": true, "scale_objective": 1,
                        "demand_fail_cost": 10000 }}
  }},
  "simulation": {{ {} }},
  "system": {{
    "name": "lng_global",
    "bus_array": [ {{ "uid": 1, "name": "b1" }} ],
    "demand_array": [ {{ "uid": 1, "name": "d1", "bus": 1,
                        "lmax": [[50], [80]] }} ],
    "generator_array": [
      {{ "uid": 1, "name": "lng", "bus": 1, "gcost": 10, "capacity": 200 }},
      {{ "uid": 2, "name": "peaker", "bus": 1, "gcost": 100,
         "capacity": 200 }}
    ],
    "decision_variable_array": [
      {{ "uid": 1, "name": "cargo", "lower_bound": 0, "upper_bound": 100,
         "cost": 1.0, "scope": "global" }}
    ],
    "user_constraint_array": [
      {{ "uid": 1, "name": "lng_cap",
         "expression":
           "generator('lng').generation <= decision_variable('cargo').value"
      }}
    ]
  }}
}})js",
                     sim);
}

[[nodiscard]] Planning lng_planning(std::string_view sim)
{
  Planning base;
  base.merge(parse_planning_json(lng_json(sim)));
  return base;
}

/// Value of `cargo` in every phase LP of scene 0.
[[nodiscard]] std::vector<double> cargo_by_phase(PlanningLP& plp)
{
  std::vector<double> out;
  for (auto& sys : plp.systems().front()) {
    const auto& dv = sys.elements<DecisionVariableLP>().front();
    const auto& holder = dv.value_cols_holder();
    REQUIRE_FALSE(holder.empty());
    const auto& bcols = holder.begin()->second;
    REQUIRE_FALSE(bcols.empty());
    out.push_back(sys.linear_interface().get_col_sol()[bcols.begin()->second]);
  }
  return out;
}

}  // namespace

TEST_CASE("Global DecisionVariable: one-phase monolithic reference")  // NOLINT
{
  PlanningLP plp(lng_planning(lng_one_phase));
  REQUIRE(plp.resolve().has_value());
  const auto& li = plp.systems().front().front().linear_interface();
  CHECK(li.get_obj_value() == doctest::Approx(1380.0));
  const auto cargo = cargo_by_phase(plp);
  REQUIRE(cargo.size() == 1);
  CHECK(cargo.front() == doctest::Approx(80.0));
}

TEST_CASE(  // NOLINT
    "Global DecisionVariable: SDDP over 2 phases matches the monolithic LP")
{
  PlanningLP plp(lng_planning(lng_two_phases));
  SDDPOptions opts;
  opts.max_iterations = 20;
  opts.convergence_tol = 1.0e-6;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  CHECK(r->back().upper_bound == doctest::Approx(1380.0));
  CHECK(r->back().lower_bound == doctest::Approx(1380.0));
  const auto cargo = cargo_by_phase(plp);
  REQUIRE(cargo.size() == 2);
  CHECK(cargo[0] == doctest::Approx(80.0));
  CHECK(cargo[1] == doctest::Approx(cargo[0]));
}

TEST_CASE(  // NOLINT
    "Global DecisionVariable: multi-phase monolithic pins later phases")
{
  // Monolithic multi-phase solves the phases in sequence (no cuts), so
  // phase 1 is myopic — but the decision is still ONE value: phase 2 is
  // pinned to phase 1's choice and its cost is not charged twice.
  PlanningLP plp(lng_planning(lng_two_phases));
  REQUIRE(plp.resolve().has_value());
  const auto cargo = cargo_by_phase(plp);
  REQUIRE(cargo.size() == 2);
  CHECK(cargo[0] == doctest::Approx(50.0));
  CHECK(cargo[1] == doctest::Approx(cargo[0]));
  double total = 0.0;
  for (auto& sys : plp.systems().front()) {
    total += sys.linear_interface().get_obj_value();
  }
  // 10·50 + 50 (cargo, phase 1 only) + 10·50 + 100·30.
  CHECK(total == doctest::Approx(4050.0));
}

TEST_CASE(  // NOLINT
    "Global DecisionVariable: 3-phase SDDP with Ruiz equilibration")
{
  // Three one-stage phases (loads 50 / 80 / 60).  The implicit cross-phase
  // state copies of `cargo` are Ruiz-scaled independently in each phase
  // LP; the cut slope must use the DEPENDENT column's scale (review P1-5:
  // with the source scale the cuts are mis-sloped and LB exceeds UB).
  // True optimum: cargo = 80 -> 10·(50 + 80 + 60) + 80 = 1980.
  constexpr std::string_view sim3 = R"js(
  "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 },
                   { "uid": 3, "duration": 1 } ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 1 },
    { "uid": 2, "first_block": 1, "count_block": 1 },
    { "uid": 3, "first_block": 2, "count_block": 1 }
  ],
  "phase_array": [
    { "uid": 1, "first_stage": 0, "count_stage": 1 },
    { "uid": 2, "first_stage": 1, "count_stage": 1 },
    { "uid": 3, "first_stage": 2, "count_stage": 1 }
  ],
  "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
)js";
  auto json = lng_json(sim3);
  const std::string lmax = R"("lmax": [[50], [80]])";
  json.replace(json.find(lmax), lmax.size(), R"("lmax": [[50], [80], [60]])");
  Planning planning;
  planning.merge(parse_planning_json(json));
  planning.options.lp_matrix_options.equilibration_method =
      LpEquilibrationMethod::ruiz;
  PlanningLP plp(std::move(planning));
  SDDPOptions opts;
  opts.max_iterations = 30;
  opts.convergence_tol = 1.0e-6;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  CHECK(r->back().upper_bound == doctest::Approx(1980.0));
  CHECK(r->back().lower_bound == doctest::Approx(1980.0));
  for (const auto& it : *r) {
    CHECK(it.lower_bound <= it.upper_bound + 1e-6);
  }
  const auto cargo = cargo_by_phase(plp);
  REQUIRE(cargo.size() == 3);
  CHECK(cargo[0] == doctest::Approx(80.0));
  CHECK(cargo[1] == doctest::Approx(80.0));
  CHECK(cargo[2] == doctest::Approx(80.0));
}
