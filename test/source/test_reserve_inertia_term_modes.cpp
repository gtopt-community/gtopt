// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_inertia_term_modes.cpp
 * @brief     ReserveZone.inertia_term (commitment / exogenous / endogenous)
 *            on one hand-solvable case, LP and MIP
 * @date      2026-11-23
 *
 * One bus, one 1-hour block, demand D = 700 MW:
 *   solar  must-take 600 MW (pmin = pmax), 0 $/MWh;
 *   hydro  20 $/MWh, ≤ 300 MW (no inertia);
 *   gas    50 $/MWh, ≤ 400 MW, SimpleCommitment dispatch_pmin 200
 *          (relaxed or integer), inertia provision pf = 10 MWs/MW,
 *          provision_max 400 → I_c = 4 000 MWs;
 *   res    the only CPF provider, μ = 100 $/MW.
 * CPF plane: R ≥ 300 − 0.05·I [I in MWs]  (= 300 − 200·u in commitment).
 * Energy balance: p_gas + p_hydro = 100.
 *
 * Hand optimum per mode (cost = 50 p + 20 h + 100 R):
 *  - commitment, relaxed u: u = p/200 → cost 30 000 − 50·D at p = D:
 *    u = 0.5, I = 2 000, R = 200, λ = −50 (the relaxed-commitment credit
 *    documented in 953ce17f1 — u rises with pmin·u generation);
 *  - commitment, INTEGER u (MIP): u = 1 needs p ≥ 200 > 100, so u = 0,
 *    I = 0, R = 300, λ = 20 (hydro marginal) — no negative price;
 *  - exogenous fallback (gas not must-run): I = 0, R = 300, λ = 20;
 *  - exogenous with system_inertia 2 000: R = 200 as data, λ = 20;
 *  - endogenous (I = pf·r, r ≤ p): every gas MW lowers R by 0.5 (saves
 *    50) → p = 100, I = 1 000, R = 250, λ = 50 − 0.5·100 = 0.
 * Objectives: 22 000 (exogenous 2 000) < 25 000 (commitment, relaxed)
 * < 30 000 (endogenous) < 32 000 (exogenous fallback = integer commitment).
 *
 * A second case makes every inertia unit must-take (pmin = pmax = 400, no
 * commitment): all three modes must then give the same I = 4 000, R = 100
 * and objective.
 *
 * The MIP subcases pin an exact MIP solver (never CLP) via
 * solver_test::first_mip_solver() and skip when none is loaded.
 */

#include <format>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

struct ItmCase
{
  std::string_view mode {"commitment"};
  std::string_view zone_extra {};  ///< extra cpf keys (leading comma)
  bool with_uc {true};
  bool relax {true};
  bool must_take_gas {false};
  bool must_run {false};  ///< SimpleCommitment must_run (u pinned to 1)
  double demand {700.0};
  double provision_max {400.0};
  int nblocks {1};
};

[[nodiscard]] std::string itm_json(const ItmCase& c)
{
  const std::string gas = c.must_take_gas
      ? R"({"uid": 3, "name": "gas", "bus": 1, "gcost": 50, "pmin": 400,
           "pmax": 400, "capacity": 400})"
      : R"({"uid": 3, "name": "gas", "bus": 1, "gcost": 50,
           "capacity": 400})";
  std::string uc;
  if (c.with_uc) {
    uc = std::format(R"(,
    "simple_commitment_array": [{{"uid": 1, "name": "uc_gas",
      "generator": "gas", "dispatch_pmin": 200, "relax": {},
      "must_run": {}}}])",
                     c.relax ? "true" : "false",
                     c.must_run ? "true" : "false");
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1.0e5,
      "scale_objective": 1.0{0}}}}},
  "simulation": {{"block_array": [{{"uid": 1, "duration": 1}}{7}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {8}}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "itm",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "lmax": {1},
                      "capacity": 2000}}],
    "generator_array": [
      {{"uid": 1, "name": "solar", "bus": 1, "gcost": 0, "pmin": 600,
        "pmax": 600, "capacity": 600}},
      {{"uid": 2, "name": "hydro", "bus": 1, "gcost": 20, "capacity": 300}},
      {2},
      {{"uid": 4, "name": "res", "bus": 1, "gcost": 999,
        "capacity": 1000}}]{3},
    "inertia_zone_array": [{{"uid": 1, "name": "sen"}}],
    "inertia_provision_array": [{{"uid": 1, "name": "ip_gas",
      "generator": "gas", "inertia_zones": ["sen"],
      "provision_factor": 10, "provision_max": {4}}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf", "urcost": 5000,
      "urreq_planes": [{{"alpha": 300, "inertia_coef": -0.05}}],
      "inertia_zone": "sen", "inertia_term": "{5}",
      "load_term": "exogenous"{6}}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 1000,
      "ur_provision_factor": 1, "urcost": 100}}]
  }}
}})",
      c.relax ? "" : R"(, "continuous_phases": "none")",
      c.demand,
      gas,
      uc,
      c.provision_max,
      c.mode,
      c.zone_extra,
      c.nblocks > 1 ? R"(, {"uid": 2, "duration": 1})" : "",
      c.nblocks);
}

struct ItmSolved
{
  double objective {0.0};
  double lambda {0.0};
  double ureq {0.0};  ///< R of block 1
  std::vector<double> ureqs;  ///< R per block
  std::optional<double> inertia;  ///< I column (absent: data / no term)
};

[[nodiscard]] ItmSolved itm_solve(const ItmCase& c,
                                  std::string_view solver = {})
{
  auto planning = parse_planning_json(itm_json(c));
  if (!solver.empty()) {
    planning.options.lp_matrix_options.solver_name = std::string {solver};
  }
  PlanningLP plp(std::move(planning));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto duals = li.get_row_dual();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto& zone = sys.elements<ReserveZoneLP>().front();
  const auto& bus = sys.elements<BusLP>().front();

  ItmSolved out;
  out.objective = li.get_obj_value();
  out.lambda = duals[bus.balance_rows_at(scenario, stage).at(buid)];
  for (const auto& block : stage.blocks()) {
    const auto rcol =
        zone.lookup_urequirement_col(scenario, stage, block.uid());
    REQUIRE(rcol.has_value());
    out.ureqs.push_back(sol[rcol.value_or(ColIndex {0})]);
  }
  out.ureq = out.ureqs.front();
  if (const auto icol = zone.lookup_inertia_col(scenario, stage, buid)) {
    out.inertia = sol[*icol];
  }
  return out;
}

}  // namespace

TEST_CASE("inertia_term modes: hand-solved single-bus case (LP)")  // NOLINT
{
  SUBCASE("commitment, relaxed u: fractional u and the negative credit")
  {
    const auto s = itm_solve({.mode = "commitment"});
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(2000.0));
    CHECK(s.ureq == doctest::Approx(200.0));
    CHECK(s.lambda == doctest::Approx(-50.0));
    CHECK(s.objective == doctest::Approx(25000.0));
  }
  SUBCASE("exogenous fallback: gas is not must-run, I = 0")
  {
    const auto s = itm_solve({.mode = "exogenous"});
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(0.0));
    CHECK(s.ureq == doctest::Approx(300.0));
    CHECK(s.lambda == doctest::Approx(20.0));
    CHECK(s.objective == doctest::Approx(32000.0));
  }
  SUBCASE("exogenous with system_inertia: data, no I column")
  {
    const auto s = itm_solve(
        {.mode = "exogenous", .zone_extra = R"(, "system_inertia": 2000)"});
    CHECK_FALSE(s.inertia.has_value());
    CHECK(s.ureq == doctest::Approx(200.0));
    CHECK(s.lambda == doctest::Approx(20.0));
    CHECK(s.objective == doctest::Approx(22000.0));
  }
  SUBCASE("system_inertia is ignored outside the exogenous mode")
  {
    const auto s = itm_solve(
        {.mode = "commitment", .zone_extra = R"(, "system_inertia": 2000)"});
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(2000.0));
    CHECK(s.lambda == doctest::Approx(-50.0));
  }
  SUBCASE("endogenous: dispatch inertia, credit = pf·|γ|·μ")
  {
    const auto s = itm_solve({.mode = "endogenous"});
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(1000.0));
    CHECK(s.ureq == doctest::Approx(250.0));
    CHECK(s.lambda == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(s.objective == doctest::Approx(30000.0));
  }
  SUBCASE("endogenous does not need a status column")
  {
    const auto s = itm_solve({.mode = "endogenous", .with_uc = false});
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(1000.0));
    CHECK(s.objective == doctest::Approx(30000.0));
  }
}

TEST_CASE("inertia_term commitment with integer u (MIP)")  // NOLINT
{
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("SKIPPED: no MIP solver loaded (never CLP for integer u)");
    return;
  }
  CAPTURE(solver);

  SUBCASE("u = 1 is infeasible: u = 0, no credit, non-negative LMP")
  {
    const auto s = itm_solve({.mode = "commitment", .relax = false}, solver);
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(s.ureq == doctest::Approx(300.0));
    CHECK(s.objective == doctest::Approx(32000.0));
    // Post-MIP duals (integers fixed): hydro is marginal.
    CHECK(s.lambda == doctest::Approx(20.0));
  }
  SUBCASE("D = 900: u = 1, I = I_c = pf·provision_max")
  {
    const auto s = itm_solve(
        {.mode = "commitment", .relax = false, .demand = 900.0}, solver);
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(4000.0));
    CHECK(s.ureq == doctest::Approx(100.0));
    // gas at dispatch_pmin 200, hydro 100: 10 000 + 2 000 + 100·100.
    CHECK(s.objective == doctest::Approx(22000.0));
    CHECK(s.lambda == doctest::Approx(20.0));
  }
}

TEST_CASE("inertia_term modes with integer u (MIP) vs relaxed")  // NOLINT
{
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("SKIPPED: no MIP solver loaded (never CLP for integer u)");
    return;
  }
  CAPTURE(solver);
  const auto pick = [&](bool relax) { return relax ? std::string {} : solver; };

  SUBCASE("exogenous fallback: u is irrelevant to I (D = 700 and 900)")
  {
    for (const bool relax : {true, false}) {
      CAPTURE(relax);
      const auto s7 =
          itm_solve({.mode = "exogenous", .relax = relax}, pick(relax));
      CHECK(s7.inertia.value_or(-1.0) == doctest::Approx(0.0));
      CHECK(s7.ureq == doctest::Approx(300.0));
      CHECK(s7.objective == doctest::Approx(32000.0));
      CHECK(s7.lambda == doctest::Approx(20.0));
      // D = 900: hydro 300, gas off (no inertia value): 6 000 + 30 000.
      const auto s9 = itm_solve(
          {.mode = "exogenous", .relax = relax, .demand = 900.0}, pick(relax));
      CHECK(s9.inertia.value_or(-1.0) == doctest::Approx(0.0));
      CHECK(s9.ureq == doctest::Approx(300.0));
      CHECK(s9.objective == doctest::Approx(36000.0));
    }
  }
  SUBCASE("endogenous: integer u = 0 at D = 700 removes the credit")
  {
    // Relaxed: p = 100, I = 1 000, λ = 0 (LP test).  Integer: u = 1 needs
    // p ≥ 200 > 100, so gas is off: I = 0, R = 300, hydro marginal.
    const auto s = itm_solve({.mode = "endogenous", .relax = false}, solver);
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(s.ureq == doctest::Approx(300.0));
    CHECK(s.objective == doctest::Approx(32000.0));
    CHECK(s.lambda == doctest::Approx(20.0));
  }
  SUBCASE("endogenous at D = 900: relaxed and integer agree (u = 1)")
  {
    // cost = 36 000 − 20 p on p ∈ [200, 300] → p = 300, I = 3 000,
    // R = 150, 30 000; the marginal gas MW nets 50 − 0.5·100 = 0.
    for (const bool relax : {true, false}) {
      CAPTURE(relax);
      const auto s = itm_solve(
          {.mode = "endogenous", .relax = relax, .demand = 900.0}, pick(relax));
      CHECK(s.inertia.value_or(-1.0) == doctest::Approx(3000.0));
      CHECK(s.ureq == doctest::Approx(150.0));
      CHECK(s.objective == doctest::Approx(30000.0));
      CHECK(s.lambda == doctest::Approx(0.0).epsilon(1e-9));
    }
  }
  SUBCASE("commitment at D = 900: relaxed and integer agree (u = 1)")
  {
    for (const bool relax : {true, false}) {
      CAPTURE(relax);
      const auto s = itm_solve(
          {.mode = "commitment", .relax = relax, .demand = 900.0}, pick(relax));
      CHECK(s.inertia.value_or(-1.0) == doctest::Approx(4000.0));
      CHECK(s.ureq == doctest::Approx(100.0));
      CHECK(s.objective == doctest::Approx(22000.0));
    }
  }
}

TEST_CASE("inertia_term exogenous: system_inertia schedule vs fallback")
// NOLINT
{
  SUBCASE("per-block schedule is data on each block's planes")
  {
    const auto s =
        itm_solve({.mode = "exogenous",
                   .zone_extra = R"(, "system_inertia": [[2000, 0]])",
                   .nblocks = 2});
    CHECK_FALSE(s.inertia.has_value());
    REQUIRE(s.ureqs.size() == 2);
    CHECK(s.ureqs[0] == doctest::Approx(200.0));
    CHECK(s.ureqs[1] == doctest::Approx(300.0));
    // hydro 100 per block: 2 000 + 20 000 + 2 000 + 30 000.
    CHECK(s.objective == doctest::Approx(54000.0));
  }
  SUBCASE("a schedule overrides the must-run fallback")
  {
    const auto s = itm_solve({.mode = "exogenous",
                              .zone_extra = R"(, "system_inertia": 1000)",
                              .must_run = true,
                              .demand = 900.0});
    CHECK_FALSE(s.inertia.has_value());
    CHECK(s.ureq == doctest::Approx(250.0));
  }
  SUBCASE("fallback counts a must_run unit: I = I_c, relaxed or integer")
  {
    // u pinned to 1 → K = pf·provision_max = 4 000, R = 100; gas at its
    // dispatch_pmin 200, hydro 100: 10 000 + 2 000 + 10 000.
    const auto solver = solver_test::first_mip_solver();
    for (const bool relax : {true, false}) {
      if (!relax && solver.empty()) {
        continue;
      }
      CAPTURE(relax);
      const auto s = itm_solve({.mode = "exogenous",
                                .relax = relax,
                                .must_run = true,
                                .demand = 900.0},
                               relax ? std::string {} : solver);
      CHECK(s.inertia.value_or(-1.0) == doctest::Approx(4000.0));
      CHECK(s.ureq == doctest::Approx(100.0));
      CHECK(s.objective == doctest::Approx(22000.0));
      CHECK(s.lambda == doctest::Approx(20.0));
    }
  }
  SUBCASE("fallback ignores a committable but not must-run unit")
  {
    const auto s = itm_solve({.mode = "exogenous", .demand = 900.0});
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(0.0));
    CHECK(s.ureq == doctest::Approx(300.0));
  }
}

TEST_CASE(  // NOLINT
    "inertia_term commitment: I_c capped by the unit's pmax")
{
  // Commitment / must-run I_c = pf · min(provision_max, pmax): a loose
  // provision_max (4 000 MW on a 400 MW unit, harmless in the endogenous
  // form where r ≤ p ≤ pmax) used to inflate I_c tenfold (I = 40 000 MWs,
  // R floored at 0).  Physically I_c = pf · pmax = 4 000.
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("SKIPPED: no MIP solver loaded");
    return;
  }
  const auto s = itm_solve({.mode = "commitment",
                            .relax = false,
                            .demand = 900.0,
                            .provision_max = 4000.0},
                           solver);
  CHECK(s.inertia.value_or(-1.0) == doctest::Approx(4000.0));
  CHECK(s.ureq == doctest::Approx(100.0));
}

TEST_CASE("inertia_term modes agree when every inertia unit is must-take")
// NOLINT
{
  // gas pmin = pmax = 400, no commitment, D = 1 100: commitment and the
  // exogenous fallback count it as forced on (K = I_c = 4 000), the
  // endogenous r ≤ p = 400 reaches the same I.  R = 100, hydro 100 MW.
  const ItmCase base {
      .with_uc = false, .must_take_gas = true, .demand = 1100.0};
  for (const auto* const mode : {"commitment", "exogenous", "endogenous"}) {
    CAPTURE(mode);
    auto c = base;
    c.mode = mode;
    const auto s = itm_solve(c);
    CHECK(s.inertia.value_or(-1.0) == doctest::Approx(4000.0));
    CHECK(s.ureq == doctest::Approx(100.0));
    CHECK(s.lambda == doctest::Approx(20.0));
    // 50·400 + 20·100 + 100·100
    CHECK(s.objective == doctest::Approx(32000.0));
  }
}
