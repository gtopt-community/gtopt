// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Coverage gaps of the extended reserve / inertia models:
//
//  * ReserveZone `flow_lines` (plane `flow_coef` term) on a line whose flow
//    is split into several LP columns by the loss model —
//    `tangent_signed_flow` (one signed column `f`) and `piecewise_direct`
//    (per-direction segment columns, no `flowp` / `flown` aggregator).  The
//    term must reference the line's NET A→B flow columns in both modes.
//  * InertiaZone `requirement_credits` naming a generator without a
//    commitment / status column: a credit that can never be earned is a
//    modelling error (strict) or a warning (normal), never a silent no-op.

#include <format>
#include <stdexcept>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/inertia_provision_lp.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

/// Two buses, cheap g1 at b1 serving a 300 MW load at b2 over a lossy line
/// l12; the up requirement is max(3, 3 + F_12) with F_12 the net A→B flow.
[[nodiscard]] std::string flow_term_case(std::string_view mode)
{
  return std::format(R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": false, "use_kirchhoff": true,
                       "demand_fail_cost": 1.0e5, "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "rdr_flow_losses",
    "bus_array": [{{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}}],
    "line_array": [{{"uid": 1, "name": "l12", "bus_a": 1, "bus_b": 2,
                    "reactance": 0.1, "voltage": 220, "resistance": 5,
                    "line_losses_mode": "{}", "loss_segments": 4,
                    "tmax_ab": 1000, "tmax_ba": 1000, "capacity": 1000}}],
    "demand_array": [{{"uid": 1, "name": "d2", "bus": 2, "capacity": 300}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 1000}},
      {{"uid": 2, "name": "res", "bus": 2, "gcost": 100, "capacity": 1000}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf_north", "urcost": 5000,
      "urreq_planes": [{{"alpha": 3}}, {{"alpha": 3, "flow_coef": 1}}],
      "flow_lines": ["l12"]}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 1000,
      "ur_provision_factor": 1, "urcost": 5}}]
  }}}})",
                     mode);
}

}  // namespace

TEST_CASE("Reserve flow term: lossy line modes use the net flow")  // NOLINT
{
  for (const std::string_view mode :
       {"tangent_signed_flow", "piecewise_direct"})
  {
    CAPTURE(mode);
    PlanningLP plp(parse_planning_json(flow_term_case(mode)));
    REQUIRE(plp.resolve().has_value());
    auto& sys = plp.systems().front().front();
    auto& li = sys.linear_interface();
    const auto sol = li.get_col_sol();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().front();
    const auto buid = stage.blocks().front().uid();
    const auto& line = sys.elements<LineLP>().front();

    // Net A→B flow read straight off the mode's own columns.
    double flow = 0.0;
    if (mode == "tangent_signed_flow") {
      const auto& fs = line.flows_cols_at(scenario, stage);
      REQUIRE(fs.contains(buid));
      CHECK(line.flowp_cols_at(scenario, stage).empty());
      flow = sol[fs.at(buid)];
    } else {
      CHECK(line.flowp_cols_at(scenario, stage).empty());
      CHECK(line.flown_cols_at(scenario, stage).empty());
      const auto& sp = line.flowp_seg_cols_at(scenario, stage);
      const auto& sn = line.flown_seg_cols_at(scenario, stage);
      REQUIRE(sp.contains(buid));
      REQUIRE(sn.contains(buid));
      REQUIRE(sp.at(buid).size() > 1);
      for (const auto& c : sp.at(buid)) {
        flow += sol[c];
      }
      for (const auto& c : sn.at(buid)) {
        flow -= sol[c];
      }
    }
    // The cheap unit ships the load (plus its losses) across the line.
    CHECK(flow > 290.0);

    const auto& rz = sys.elements<ReserveZoneLP>().front();
    const auto r = rz.lookup_urequirement_col(scenario, stage, buid);
    REQUIRE(r.has_value());
    // R = max(3, 3 + F): the flow plane binds with the full net flow.
    CHECK(sol[r.value_or(ColIndex {0})] == doctest::Approx(3.0 + flow));
  }
}

namespace
{

/// Inertia zone `ng` (13 400 MWs) with a 3 900 MWs credit for 2 committed
/// units among g1 / g2; `commit_g2` adds a must-run Commitment on g2 only
/// (g1 never has one).  "syn" supplies 100 MWs per MW at 1 $/MW.
[[nodiscard]] std::string credit_case(std::string_view mode, bool commit_g2)
{
  return std::format(R"({{
  "options": {{"annual_discount_rate": 0.0, "constraint_mode": "{}",
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1.0e5,
                       "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1,
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "credit_no_commitment",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "capacity": 200}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": 1, "gcost": 10, "pmin": 50,
        "pmax": 100, "capacity": 100}},
      {{"uid": 3, "name": "syn", "bus": 1, "gcost": 0, "capacity": 200}}],
    {}
    "inertia_zone_array": [{{"uid": 1, "name": "ng", "requirement": 13400,
      "requirement_credits": [{{"name": "guacolda", "generators": ["g1", "g2"],
        "units": 1, "credit": 3900, "integer": false}}]}}],
    "inertia_provision_array": [{{"uid": 1, "name": "ip_syn",
      "generator": "syn", "inertia_zones": ["ng"], "provision_factor": 100,
      "provision_max": 200, "cost": 1}}]
  }}}})",
                     mode,
                     commit_g2 ? R"("commitment_array": [{"uid": 1,
                       "name": "c2", "generator": "g2", "must_run": true}],)"
                               : "");
}

[[nodiscard]] double syn_inertia(PlanningLP& plp)
{
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  const auto& ip = sys.elements<InertiaProvisionLP>().front();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto col =
      ip.lookup_provision_col(scenario, stage, stage.blocks().front().uid());
  REQUIRE(col.has_value());
  return sys.linear_interface().get_col_sol()[col.value_or(ColIndex {0})];
}

}  // namespace

TEST_CASE(  // NOLINT
    "Inertia credit on a generator without commitment: strict error")
{
  for (const bool commit_g2 : {false, true}) {
    CAPTURE(commit_g2);
    CHECK_THROWS_WITH_AS(
        PlanningLP(parse_planning_json(credit_case("strict", commit_g2))),
        doctest::Contains("counts generator 'g1'"),
        std::runtime_error);
  }
}

TEST_CASE(  // NOLINT
    "Inertia credit on a generator without commitment: normal mode drops it")
{
  SUBCASE("no generator committed: no relief, exogenous requirement")
  {
    PlanningLP plp(solver_test::with_mip_solver(
        parse_planning_json(credit_case("normal", /*commit_g2=*/false))));
    CHECK(syn_inertia(plp) == doctest::Approx(134.0));
  }
  SUBCASE("the committed generator still earns the credit")
  {
    // g2 must-run: u = 1 ≥ units·y ⇒ y = 1, relief 3 900 ⇒ r = 95.
    PlanningLP plp(solver_test::with_mip_solver(
        parse_planning_json(credit_case("normal", /*commit_g2=*/true))));
    CHECK(syn_inertia(plp) == doctest::Approx(95.0));
  }
}
