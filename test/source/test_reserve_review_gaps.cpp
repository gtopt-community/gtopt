// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_review_gaps.cpp
 * @brief     Doctests from the feat/sscc-integration C++ review, §5
 * @date      2026-11-23
 *
 * One TEST_CASE per item of the review's "Concrete doctests worth adding"
 * list.  The cases that pinned open findings (formerly
 * `doctest::may_fail()`) pass since the fixes on feat/sscc-integration.
 *
 *  §5.1  inertia credit prices a zero-cost unit negative     (P0-2, passes)
 *  §5.2  plane coefficient without support is rejected       (P0-1)
 *  §5.3  Σ plane duals = requirement dual (KKT identity)     (passes)
 *  §5.4  duals unfolded under scale_objective / duration      (passes)
 *  §5.6  provision with zone factor 0 keeps its column       (P1-3)
 *  §5.7  offline provision guards                            (P1-4)
 *  §5.8  stage-scoped DV inactive at a stage, strict         (P1-6)
 *  §5.10 BESS split: no redundant aggregate headroom row     (P2-1)
 *  §5.11 low_memory compress replay of planes/contingency/I  (passes)
 *  §5.12 eq sense + planes + surplus cost warns              (P1-7)
 *  §5.13 InertiaZone credit `units = 0` rejected             (review §4)
 * §5.5 (converter with a missing demand) is not reachable from JSON: the
 * battery always synthesizes its converter's demand.
 */

#include <algorithm>
#include <cmath>
#include <exception>
#include <format>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

/// Single-bus case from raw JSON array bodies (`body` is the inside of
/// `system` after `bus_array`, leading comma included).
[[nodiscard]] std::string rvg_case(std::string_view body,
                                   std::string_view mode = "strict",
                                   int nblocks = 1,
                                   double duration = 1.0,
                                   double scale_objective = 1.0,
                                   std::string_view scenarios = R"({"uid": 1})")
{
  std::string blocks;
  for (int b = 1; b <= nblocks; ++b) {
    blocks += std::format(
        R"({}{{"uid": {}, "duration": {}}})", b > 1 ? "," : "", b, duration);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "constraint_mode": "{}",
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1.0e5,
                      "scale_objective": {}}}}},
  "simulation": {{"block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {}}}],
    "scenario_array": [{}]}},
  "system": {{"name": "rvg", "bus_array": [{{"uid": 1, "name": "b1"}}]{}
  }}
}})",
      mode,
      scale_objective,
      blocks,
      nblocks,
      scenarios,
      body);
}

struct RvgSolved
{
  PlanningLP plp;
  explicit RvgSolved(const std::string& json)
      : plp(parse_planning_json(json))
  {
    REQUIRE(plp.resolve().has_value());
  }
  [[nodiscard]] SystemLP& sys() { return plp.systems().front().front(); }
  [[nodiscard]] double lambda(std::size_t scen = 0, std::size_t blk = 0)
  {
    auto& s = sys();
    const auto& scenario = s.scene().scenarios().at(scen);
    const auto& stage = s.phase().stages().front();
    const auto buid = stage.blocks().at(blk).uid();
    return s.linear_interface().get_row_dual()
        [s.elements<BusLP>().front().balance_rows_at(scenario, stage).at(buid)];
  }
};

}  // namespace

// ─── §5.1 ──────────────────────────────────────────────────────────────────

TEST_CASE("Review 5.1: inertia credit prices a zero-cost unit negative")
// NOLINT
{
  // 100 MW load, free hydro (300 MW, curtailing), inertia provision on the
  // hydro (100 MWs/MW).  Plane R ≥ 200 − 0.001·I, μ = 10 (res bid).  Each
  // extra MW of hydro would add 100 MWs → −0.1 MW of R → saves 1 $; the
  // balance row prices that: λ = 0 − 100·0.001·10 = −1.  Without the
  // inertia term λ = 0.  This documents the PLEXOS-faithful coupling (P0-2)
  // that the exogenous load term does NOT remove; the default commitment
  // inertia term does (no dispatch column in I).
  const auto body = [](std::string_view inertia, std::string_view term)
  {
    return std::format(
        R"(,
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}}],
    "generator_array": [
      {{"uid": 1, "name": "hydro", "bus": 1, "gcost": 0, "capacity": 300}},
      {{"uid": 2, "name": "res", "bus": 1, "gcost": 50, "capacity": 500}}],
    "inertia_zone_array": [{{"uid": 1, "name": "sen"}}],
    "inertia_provision_array": [{{"uid": 1, "name": "ip_hydro",
      "generator": "hydro", "inertia_zones": ["sen"],
      "provision_factor": 100, "provision_max": 1.0e6}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{{"alpha": 200{}}}], "inertia_zone": "sen",
      "load_term": "exogenous"{}}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 500,
      "ur_provision_factor": 1, "urcost": 10}}])",
        inertia,
        term);
  };
  SUBCASE("endogenous inertia term: negative LMP")
  {
    RvgSolved s(rvg_case(body(R"(, "inertia_coef": -0.001)",
                              R"(, "inertia_term": "endogenous")")));
    CHECK(s.lambda() == doctest::Approx(-1.0));
  }
  SUBCASE("default (commitment) inertia term: LMP 0")
  {
    // The hydro has no Commitment and pmin 0: it is not forced on, so the
    // commitment term counts nothing and no dispatch column reaches the
    // plane.
    RvgSolved s(rvg_case(body(R"(, "inertia_coef": -0.001)", "")));
    CHECK(s.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }
  SUBCASE("without inertia_coef: LMP 0")
  {
    RvgSolved s(rvg_case(body("", "")));
    CHECK(s.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }
}

// ─── §5.2 ──────────────────────────────────────────────────────────────────

namespace
{
[[nodiscard]] std::string rvg_support_case(std::string_view plane,
                                           std::string_view mode)
{
  return rvg_case(std::format(R"(,
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 500}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{{"alpha": 50, {}}}]}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp", "generator": "g1",
      "reserve_zones": [1], "urmax": 500, "ur_provision_factor": 1,
      "urcost": 1}}])",
                              plane),
                  mode);
}
}  // namespace

TEST_CASE(
    "Review 5.2: plane coefficient without its support is rejected")  // NOLINT
{
  // P0-1 (reserve_zone_lp.cpp plane stamp `if (icol && ic != 0.0)` etc.):
  // a plane term whose support (inertia_zone / contingency candidates /
  // flow_lines) is missing silently vanishes.  Expected: strict throws.
  SUBCASE("inertia_coef without inertia_zone")
  {
    CHECK_THROWS(PlanningLP(parse_planning_json(
        rvg_support_case(R"("inertia_coef": -0.0123)", "strict"))));
  }
  SUBCASE("contingency_coef without contingency candidates")
  {
    CHECK_THROWS(PlanningLP(parse_planning_json(
        rvg_support_case(R"("contingency_coef": 1.25)", "strict"))));
  }
  SUBCASE("flow_coef without flow_lines")
  {
    CHECK_THROWS(PlanningLP(parse_planning_json(
        rvg_support_case(R"("flow_coef": 1.0)", "strict"))));
  }
  SUBCASE("normal mode builds (warning) instead of throwing")
  {
    CHECK_NOTHROW(PlanningLP(parse_planning_json(
        rvg_support_case(R"("inertia_coef": -0.0123)", "normal"))));
  }
}

TEST_CASE("Review 5.2: negative contingency_coef is rejected")  // NOLINT
{
  // reserve_zone.hpp documents contingency_coef ≥ 0; a negative value
  // turns the largest unit into a price credit.  Not checked (P0-1).
  const auto json = rvg_case(R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 500}],
    "reserve_zone_array": [{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{"alpha": 50, "contingency_coef": -1.0}],
      "contingency_generators": ["g1"]}],
    "reserve_provision_array": [{"uid": 1, "name": "rp", "generator": "g1",
      "reserve_zones": [1], "urmax": 500, "ur_provision_factor": 1,
      "urcost": 1}])");
  auto planning = parse_planning_json(json);
  const bool flagged = !validate_planning(planning).errors.empty();
  bool threw = false;
  try {
    PlanningLP plp(parse_planning_json(json));
  } catch (const std::exception&) {
    threw = true;
  }
  CHECK((flagged || threw));
}

// ─── §5.3 / §5.4 ───────────────────────────────────────────────────────────

namespace
{
/// Three planes on D = 1 000 (exogenous `system_load`): {100}, {50 + 0.05·D}
/// = 100 (two binding) and {20}.  μ = 10 (provider bid).
[[nodiscard]] std::string rvg_dual_body()
{
  return R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 30, "capacity": 500}],
    "reserve_zone_array": [{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{"alpha": 100}, {"alpha": 50, "demand_coef": 0.05},
                       {"alpha": 20}],
      "system_load": 1000, "load_term": "exogenous"}],
    "reserve_provision_array": [{"uid": 1, "name": "rp", "generator": "g1",
      "reserve_zones": [1], "urmax": 500, "ur_provision_factor": 1,
      "urcost": 10}])";
}
}  // namespace

TEST_CASE("Review 5.3: plane duals sum to the requirement dual")  // NOLINT
{
  for (const double scale : {1.0, 1000.0}) {
    CAPTURE(scale);
    RvgSolved s(rvg_case(rvg_dual_body(),
                         "strict",
                         1,
                         1.0,
                         scale,
                         R"({"uid": 1, "probability_factor": 0.3},
                            {"uid": 2, "probability_factor": 0.7})"));
    auto& sys = s.sys();
    const auto duals = sys.linear_interface().get_row_dual();
    const auto& stage = sys.phase().stages().front();
    const auto buid = stage.blocks().front().uid();
    const auto& zone = sys.elements<ReserveZoneLP>().front();
    const auto& scenarios = sys.scene().scenarios();
    REQUIRE(scenarios.size() == 2);
    for (std::size_t si = 0; si < scenarios.size(); ++si) {
      const auto& scenario = scenarios[si];
      CAPTURE(si);
      double sum_planes = 0.0;
      const auto rows = zone.urplane_rows(scenario, stage, buid);
      REQUIRE(rows.size() == 3);
      for (const auto& r : rows) {
        sum_planes += std::abs(duals[r]);
      }
      const auto& req = zone.urequirement_rows();
      const auto it = req.find({scenario.uid(), stage.uid()});
      REQUIRE(it != req.end());
      const double nu = std::abs(duals[it->second.at(buid)]);
      // R > 0: the floor's reduced cost is 0, so Σ μ_k = ν exactly.
      CHECK(sum_planes == doctest::Approx(nu).epsilon(1e-9));
      // Unfolded by the scenario probability: the bid, whatever the scale.
      const double prob = si == 0 ? 0.3 : 0.7;
      CHECK(nu / prob == doctest::Approx(10.0));
      // The non-binding plane {20} carries nothing.
      CHECK(std::abs(duals[rows[2]]) < 1e-9);
    }
  }
}

TEST_CASE(  // NOLINT
    "Review 5.4: output duals are physical under scale_objective and "
    "block duration")
{
  // scale_objective 1000 and 3 h blocks: the written LMP and plane/
  // requirement duals stay in $/MWh and $/MW (60 energy, 10 reserve).
  RvgSolved s(rvg_case(rvg_dual_body(), "strict", 1, 3.0, 1000.0));
  auto& sys = s.sys();
  OutputContext oc(sys.system_context(),
                   sys.linear_interface(),
                   sys.scene().uid(),
                   sys.phase().uid(),
                   sys.phase().is_continuous());
  const auto& zone = sys.elements<ReserveZoneLP>().front();
  REQUIRE(zone.add_to_output(oc));
  REQUIRE(sys.elements<BusLP>().front().add_to_output(oc));
  const auto value = [&](std::string_view cls,
                         std::string_view field,
                         std::string_view kind) -> double
  {
    const OutputContext::ClassFieldName key {cls, field, kind};
    const auto it = oc.fields().find(key);
    REQUIRE(it != oc.fields().end());
    REQUIRE_FALSE(it->second.empty());
    REQUIRE_FALSE(it->second.front().entries.empty());
    return it->second.front().entries.front().second;
  };
  CHECK(std::abs(value("ReserveZone", ReserveZoneLP::UrequirementName, "dual"))
        == doctest::Approx(10.0));
  const double planes = std::abs(value("ReserveZone", "urplane1", "dual"))
      + std::abs(value("ReserveZone", "urplane2", "dual"));
  CHECK(planes == doctest::Approx(10.0));
  CHECK(value("Bus", "balance", "dual") == doctest::Approx(30.0));
}

// ─── §5.6 ──────────────────────────────────────────────────────────────────

TEST_CASE(
    "Review 5.6: provision with zone factor 0 keeps its column")  // NOLINT
{
  // P1-3 (reserve_provision_lp.cpp: `zone_factor == 0.0 -> continue`, then
  // the early return when no requirement row is left): the column vanishes
  // and a UserConstraint on it silently binds nothing.
  const auto json = rvg_case(R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 500}],
    "reserve_zone_array": [{"uid": 1, "name": "z", "urreq": 10,
      "urcost": 5000}],
    "reserve_provision_array": [{"uid": 1, "name": "rp", "generator": "g1",
      "reserve_zones": [1], "ur_zone_factors": [0.0], "urmax": 500,
      "ur_provision_factor": 1, "urcost": 1}],
    "user_constraint_array": [{"uid": 1, "name": "rp_min",
      "expression": "reserve_provision('rp').up >= 30"}])");
  RvgSolved s(json);
  auto& sys = s.sys();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto col =
      sys.elements<ReserveProvisionLP>().front().lookup_up_provision_col(
          scenario, stage, stage.blocks().front().uid());
  REQUIRE(col.has_value());
  CHECK(sys.linear_interface().get_col_sol()[col.value_or(ColIndex {0})]
        >= 30.0 - 1e-9);
}

// ─── §5.7 ──────────────────────────────────────────────────────────────────

namespace
{
[[nodiscard]] std::string rvg_offline_case(std::string_view gen_extra,
                                           std::string_view prov_extra,
                                           bool commitment)
{
  return rvg_case(std::format(R"(,
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}}],
    "generator_array": [
      {{"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": 200}},
      {{"uid": 2, "name": "peaker", "bus": 1, "gcost": 90{}}}],
    "reserve_zone_array": [{{"uid": 1, "name": "ctf", "urreq": 50,
      "urcost": 1000}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_cold",
      "generator": "peaker", "reserve_zones": [1], "ur_provision_factor": 1,
      "urcost": 1, "offline": true{}}}]{})",
                              gen_extra,
                              prov_extra,
                              commitment ? R"(,
    "commitment_array": [{"uid": 1, "name": "uc_peaker",
      "generator": "peaker", "pmin": 50, "relax": true,
      "initial_status": 0}])"
                                         : ""),
                  "strict");
}
}  // namespace

TEST_CASE("Review 5.7: offline (cold) provision guards")  // NOLINT
{
  // P1-4 (commitment_lp.cpp big-M `r + urmax·u ≤ urmax`).
  SUBCASE("offline without a commitment is an error")
  {
    const auto json = rvg_offline_case(
        R"(, "capacity": 100)", R"(, "urmax": 100)", /*commitment=*/false);
    auto planning = parse_planning_json(json);
    const bool flagged = !validate_planning(planning).errors.empty();
    bool threw = false;
    try {
      PlanningLP plp(parse_planning_json(json));
    } catch (const std::exception&) {
      threw = true;
    }
    CHECK((flagged || threw));
  }
  SUBCASE("offline + urmin > 0 is an error")
  {
    const auto json = rvg_offline_case(R"(, "capacity": 100)",
                                       R"(, "urmax": 100, "urmin": 10)",
                                       /*commitment=*/true);
    auto planning = parse_planning_json(json);
    const bool flagged = !validate_planning(planning).errors.empty();
    bool threw = false;
    try {
      PlanningLP plp(parse_planning_json(json));
    } catch (const std::exception&) {
      threw = true;
    }
    CHECK((flagged || threw));
  }
  SUBCASE("offline with no urmax and no capacity: finite big-M")
  {
    // No urmax, no capacity: the big-M must not be the 1e20/DblMax
    // sentinel (or the gating must be skipped with a diagnostic).
    PlanningLP plp(
        parse_planning_json(rvg_offline_case("", "", /*commitment=*/true)));
    auto& li = plp.systems().front().front().linear_interface();
    double max_abs = 0.0;
    for (Index r = 0; r < li.get_numrows(); ++r) {
      const auto* lbl = li.row_label_at(RowIndex {r});
      if (lbl == nullptr) {
        continue;
      }
      for (Index c = 0; c < li.get_numcols(); ++c) {
        max_abs = std::max(max_abs,
                           std::abs(li.get_coeff(RowIndex {r}, ColIndex {c})));
      }
    }
    CHECK(max_abs < 1.0e9);
  }
}

// ─── §5.8 ──────────────────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "Review 5.8: stage-scoped DV inactive at a stage, UC over all stages")
{
  // P1-6: the DV has no column at stage 2 because it is INACTIVE there —
  // a legitimate zero, not a scope mismatch.  Strict must not throw.
  constexpr std::string_view json = R"({
  "options": {"annual_discount_rate": 0.0, "constraint_mode": "strict",
    "model_options": {"use_single_bus": true, "scale_objective": 1,
                      "demand_fail_cost": 1000}},
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1},
                    {"uid": 2, "first_block": 1, "count_block": 1}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "dv_inactive",
    "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 10}],
    "generator_array": [{"uid": 1, "name": "g1", "bus": 1, "gcost": 1,
                         "capacity": 100}],
    "decision_variable_array": [{"uid": 1, "name": "x", "scope": "stage",
      "lower_bound": 0, "upper_bound": 50, "active": [1, 0]}],
    "user_constraint_array": [{"uid": 1, "name": "g_le_x",
      "expression":
        "generator('g1').generation <= 20 + decision_variable('x').value"}]
  }})";
  CHECK_NOTHROW(PlanningLP(parse_planning_json(json)));
}

// ─── §5.10 ─────────────────────────────────────────────────────────────────

TEST_CASE("Review 5.10: BESS split drops the aggregate headroom row")  // NOLINT
{
  // P2-1: with both up caps set the split rows (pd + Σr_dis ≤ Pd,
  // Σr_chg − pc ≤ 0) imply the aggregate `reserve_up_headroom` row; both
  // are emitted → linearly dependent rows, ambiguous duals.
  const auto json = rvg_case(R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 50, "capacity": 300}],
    "battery_array": [{"uid": 1, "name": "bess", "bus": 1,
      "input_efficiency": 1.0, "output_efficiency": 1.0, "emin": 0,
      "emax": 200, "capacity": 200, "eini": 100, "pmax_charge": 40,
      "pmax_discharge": 40, "reserve_sustain_hours": 0.0,
      "use_state_variable": true, "daily_cycle": false}],
    "reserve_zone_array": [{"uid": 1, "name": "rz", "urreq": 60,
      "urcost": 1.0e4}],
    "reserve_provision_array": [{"uid": 1, "name": "rp_bess",
      "generator": "bess_gen", "reserve_zones": [1], "urmax": 1000,
      "ur_provision_factor": 1, "urmax_response": 25,
      "urmax_charge_response": 10}])");
  RvgSolved s(json);
  auto& li = s.sys().linear_interface();
  int aggregate = 0;
  for (Index r = 0; r < li.get_numrows(); ++r) {
    const auto* lbl = li.row_label_at(RowIndex {r});
    if (lbl != nullptr
        && lbl->constraint_name == ConverterLP::ReserveUpHeadroomName)
    {
      ++aggregate;
    }
  }
  CHECK(aggregate == 0);
}

// ─── §5.11 ─────────────────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "Review 5.11: low_memory compress replays planes, contingency, inertia")
{
  // Cross-element set_coeff (I row, contingency refs) must survive the
  // compress → reconstruct cycle: two solves under compress give the same
  // objective and plane duals as the off build.
  const auto json = rvg_case(R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1,
                      "lmax": [[900, 1000, 1100]], "capacity": 2000}],
    "generator_array": [
      {"uid": 1, "name": "sync", "bus": 1, "gcost": 5, "capacity": 600},
      {"uid": 2, "name": "big", "bus": 1, "gcost": 20, "capacity": 400},
      {"uid": 3, "name": "flex", "bus": 1, "gcost": 50, "capacity": 2000},
      {"uid": 4, "name": "res", "bus": 1, "gcost": 100, "capacity": 1000}],
    "inertia_zone_array": [{"uid": 1, "name": "sen"}],
    "inertia_provision_array": [{"uid": 1, "name": "ip_sync",
      "generator": "sync", "inertia_zones": ["sen"],
      "provision_factor": 30, "provision_max": 18000}],
    "reserve_zone_array": [{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{"alpha": 300, "demand_coef": -0.05,
                        "inertia_coef": -0.005},
                       {"alpha": 50, "contingency_coef": 1.0}],
      "contingency_generators": ["big"], "inertia_zone": "sen",
      "load_term": "exogenous"}],
    "reserve_provision_array": [{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 1000,
      "ur_provision_factor": 1, "urcost": 7}])",
                             "strict",
                             3);
  const auto run = [&](LowMemoryMode mode, int solves)
  {
    LpMatrixOptions flat;
    flat.low_memory_mode = mode;
    PlanningLP plp(parse_planning_json(json), flat);
    for (int i = 0; i < solves; ++i) {
      REQUIRE(plp.resolve().has_value());
    }
    auto& sys = plp.systems().front().front();
    auto& li = sys.linear_interface();
    const auto duals = li.get_row_dual();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().front();
    const auto& zone = sys.elements<ReserveZoneLP>().front();
    std::vector<double> out {li.get_obj_value()};
    for (const auto& block : stage.blocks()) {
      for (const auto r : zone.urplane_rows(scenario, stage, block.uid())) {
        out.push_back(duals[r]);
      }
    }
    return out;
  };
  const auto off = run(LowMemoryMode::off, 1);
  const auto cmp = run(LowMemoryMode::compress, 2);
  REQUIRE(off.size() == cmp.size());
  REQUIRE(off.size() == 7);
  for (std::size_t i = 0; i < off.size(); ++i) {
    CAPTURE(i);
    CHECK(cmp[i] == doctest::Approx(off[i]).epsilon(1e-12));
  }
}

// ─── §5.12 ─────────────────────────────────────────────────────────────────

TEST_CASE("Review 5.12: eq sense + planes + surplus cost warns")  // NOLINT
{
  // P1-7: with planes, R absorbs any excess, so `ursurplus_cost` is dead.
  auto planning = parse_planning_json(rvg_case(R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 500}],
    "reserve_zone_array": [{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{"alpha": 80}], "requirement_sense": "eq",
      "ursurplus_cost": 1}],
    "reserve_provision_array": [{"uid": 1, "name": "rp", "generator": "g1",
      "reserve_zones": [1], "urmax": 500, "ur_provision_factor": 1,
      "urcost": 1}])"));
  CHECK_FALSE(validate_planning(planning).warnings.empty());
}

// ─── §5.13 ─────────────────────────────────────────────────────────────────

TEST_CASE(
    "Review 5.13: InertiaZone credit with units = 0 is rejected")  // NOLINT
{
  // inertia_zone_lp.cpp clamps `units` to 1e-9 (coefficient 1e-9 in the
  // y·units − Σu ≤ 0 link): the credit is free.  Expected: rejected.
  const auto json = rvg_case(R"(,
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 100}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "pmin": 50,
       "pmax": 100, "capacity": 100},
      {"uid": 2, "name": "syn", "bus": 1, "gcost": 0, "capacity": 200}],
    "simple_commitment_array": [{"uid": 1, "name": "s1", "generator": "g1",
      "relax": true}],
    "inertia_zone_array": [{"uid": 1, "name": "ng", "requirement": 1000,
      "requirement_credits": [{"name": "zero", "generators": ["g1"],
        "units": 0, "credit": 500}]}],
    "inertia_provision_array": [{"uid": 1, "name": "ip_syn",
      "generator": "syn", "inertia_zones": ["ng"], "provision_factor": 10,
      "provision_max": 2000, "cost": 1}])");
  auto planning = parse_planning_json(json);
  const bool flagged = !validate_planning(planning).errors.empty();
  bool threw = false;
  try {
    PlanningLP plp(parse_planning_json(json));
  } catch (const std::exception&) {
    threw = true;
  }
  CHECK((flagged || threw));
}
