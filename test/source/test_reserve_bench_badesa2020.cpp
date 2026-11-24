// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_bench_badesa2020.cpp
 * @brief     Benchmark: Badesa, Teng, Strbac (2020) frequency-secured ED/UC
 * @date      2026-11-23
 *
 * Reference: L. Badesa, F. Teng, G. Strbac, "Pricing inertia and Frequency
 * Response with diverse dynamics in a Mixed-Integer Second-Order Cone
 * Programming formulation", Applied Energy 260 (2020) 114334, preprint
 * <https://arxiv.org/abs/1909.06671>.  Only the published numbers (facts)
 * are used; the authors' code (no licence) is not.
 *
 * 1. ED, Tables 1-3 (preprint p8): nuclear 100 MW (the largest loss, no
 *    FR), 5 × type 1 (400 MW, FR ≤ 225, 17 £/MWh), 5 × type 2 (300 MW,
 *    FR ≤ 175, 18 £/MWh); fixed H makes the nadir a FIXED requirement
 *    R ≥ 372 MW (eq. 32), FR offered at 0 £/MW, R ≤ Pmax − P.
 *      PD = 250: P = 100/150/0, energy 17 £/MWh, FR price 0.
 *      PD = 400: P = 100/203/97, FR = 0/197/175, energy 18, FR price 1.
 *    → exact reproduction expected.
 *
 * 2. UC, Table 11 data (p11): nuclear 1800 MW (10 £/MWh), 30 × type 1
 *    (250-500 MW, 95 £/MWh, no-load 500 £), 30 × type 2 (75-150 MW,
 *    50 £/MWh, no-load 500 £), H = 5 s, PD = 24 GW, RES 10 / 18 GW at
 *    0 £/MWh (curtailable).  Each type is ONE aggregated unit with a
 *    relaxed Commitment (u = share of the 30 units online), exactly the
 *    paper's pricing step (relaxed y).  Only the RoCoF row is kept:
 *      H_sys = Σ H_g Pmax_g y_g − Pmax_L H_L ≥ PL·f0 / (2·RoCoF_max)
 *            = 1800·50 / 2 = 45 000 MWs           (eq. 37, RoCoF 1 Hz/s)
 *    → InertiaZone requirement 45 000 with commitment credits
 *      5·15 000 = 75 000 (type 1) and 5·4 500 = 22 500 MWs (type 2); the
 *      nuclear term H·1800 − 1800·H_L is zero.
 *    The q-s-s row Σ R ≥ PL is a ReserveZone whose plane is the
 *    contingency term (contingency_generators = nuclear) — the requirement
 *    is set by the largest unit's output.
 *    The paper's UC prices (inertia 0.041 / 4.6 £/MWs, FR 0.79 / 51.2)
 *    come from the binding NADIR row H·(R1/T1 + R2/T2) ≥ ..., which is
 *    bilinear and not modelled here, so they are NOT comparable.  The
 *    expected values below are derived by hand for the RoCoF-only model:
 *      low RES (10 GW): u2 = 1, P2 = 4 500; P1 = 7 700; FR needs
 *        headroom 1 800 from type 1 → u1 = 9 500 / 15 000; RoCoF slack.
 *        λ = 95 + 1 = 96 £/MWh, FR = 1 £/MW (no-load per MW of headroom),
 *        inertia price 0, objective 999 000 £.
 *      high RES (18 GW): inertia binds; type 2 is the cheaper inertia
 *        (127 500 £ / 22 500 MWs) so u2 = 1, then u1 = 0.3 at MSG
 *        (727 500 £ / 75 000 MWs = 9.7 £/MWs); thermal MSG output 4.5 GW
 *        > net demand 4.2 GW → 0.3 GW RES curtailed, λ = 0, FR slack.
 *        inertia price 9.7 £/MWs, objective 363 750 £.
 */

#include <cmath>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view kBdsSim = R"(
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1,
                     "chronological": true}],
    "scenario_array": [{"uid": 1, "probability_factor": 1}]
  },)";

[[nodiscard]] std::string bds_ed_json(double demand)
{
  return std::format(R"({{
  "options": {{"output_compression": "uncompressed",
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 10000}}}},
  {0}
  "system": {{
    "name": "badesa_ed",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d", "bus": "b1", "lmax": {1}}}],
    "generator_array": [
      {{"uid": 1, "name": "nuclear", "bus": "b1", "pmin": 100, "pmax": 100,
       "gcost": 15, "capacity": 100}},
      {{"uid": 2, "name": "type1", "bus": "b1", "pmin": 0, "pmax": 400,
       "gcost": 17, "capacity": 400}},
      {{"uid": 3, "name": "type2", "bus": "b1", "pmin": 0, "pmax": 300,
       "gcost": 18, "capacity": 300}}
    ],
    "reserve_zone_array": [
      {{"uid": 1, "name": "fr", "urreq": 372, "urcost": 10000}}
    ],
    "reserve_provision_array": [
      {{"uid": 1, "name": "fr1", "generator": "type1", "reserve_zones": ["fr"],
       "urmax": 225, "ur_provision_factor": 1, "urcost": 0}},
      {{"uid": 2, "name": "fr2", "generator": "type2", "reserve_zones": ["fr"],
       "urmax": 175, "ur_provision_factor": 1, "urcost": 0}}
    ]
  }}
}})",
                     kBdsSim,
                     demand);
}

[[nodiscard]] std::string bds_uc_json(double res)
{
  return std::format(R"({{
  "options": {{"output_compression": "uncompressed",
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 10000}}}},
  {0}
  "system": {{
    "name": "badesa_uc",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d", "bus": "b1", "lmax": 24000}}],
    "generator_array": [
      {{"uid": 1, "name": "nuclear", "bus": "b1", "pmin": 1800, "pmax": 1800,
       "gcost": 10, "capacity": 1800}},
      {{"uid": 2, "name": "type1", "bus": "b1", "pmin": 0, "pmax": 15000,
       "gcost": 95, "capacity": 15000}},
      {{"uid": 3, "name": "type2", "bus": "b1", "pmin": 0, "pmax": 4500,
       "gcost": 50, "capacity": 4500}},
      {{"uid": 4, "name": "res", "bus": "b1", "pmin": 0, "pmax": {1},
       "gcost": 0, "capacity": {1}}}
    ],
    "commitment_array": [
      {{"uid": 1, "name": "uc1", "generator": "type1", "pmin": 7500,
       "noload_cost": 15000, "initial_status": 1, "relax": true}},
      {{"uid": 2, "name": "uc2", "generator": "type2", "pmin": 2250,
       "noload_cost": 15000, "initial_status": 1, "relax": true}}
    ],
    "inertia_zone_array": [
      {{"uid": 1, "name": "rocof", "requirement": 45000, "cost": 10000,
       "requirement_credits": [
         {{"name": "h_type1", "generators": ["type1"], "units": 1,
          "credit": 75000}},
         {{"name": "h_type2", "generators": ["type2"], "units": 1,
          "credit": 22500}}
       ]}}
    ],
    "reserve_zone_array": [
      {{"uid": 1, "name": "qss", "urcost": 10000,
       "urreq_planes": [{{"alpha": 0, "contingency_coef": 1}}],
       "contingency_generators": ["nuclear"]}}
    ],
    "reserve_provision_array": [
      {{"uid": 1, "name": "fr1", "generator": "type1", "reserve_zones": ["qss"],
       "ur_provision_factor": 1, "urcost": 0}},
      {{"uid": 2, "name": "fr2", "generator": "type2", "reserve_zones": ["qss"],
       "ur_provision_factor": 1, "urcost": 0}}
    ]
  }}
}})",
                     kBdsSim,
                     res);
}

struct BdsResult
{
  double objective {};
  double lambda {};
  double fr_price {};
  double inertia_price {};
  double p_type1 {};
  double p_type2 {};
  double r_total {};
  double r_type2 {};
};

template<typename E>
[[nodiscard]] const E& bds_find(SystemLP& sys, std::string_view name)
{
  for (const auto& e : sys.template elements<E>()) {
    if (e.object().name == name) {
      return e;
    }
  }
  FAIL("element not found: " << name);
  return sys.template elements<E>().front();
}

[[nodiscard]] BdsResult bds_solve(const std::string& json,
                                  std::string_view zone,
                                  bool with_inertia)
{
  PlanningLP plp(parse_planning_json(json));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto duals = li.get_row_dual();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();

  BdsResult out;
  out.objective = li.get_obj_value();
  const auto& bus = sys.elements<BusLP>().front();
  out.lambda = duals[bus.balance_rows_at(scenario, stage).at(buid)];

  const auto& rz = bds_find<ReserveZoneLP>(sys, zone);
  const auto rit = rz.urequirement_rows().find({scenario.uid(), stage.uid()});
  REQUIRE(rit != rz.urequirement_rows().end());
  out.fr_price = std::abs(duals[rit->second.at(buid)]);

  if (with_inertia) {
    const auto& iz = bds_find<InertiaZoneLP>(sys, "rocof");
    const auto iit = iz.requirement_rows().find({scenario.uid(), stage.uid()});
    REQUIRE(iit != iz.requirement_rows().end());
    out.inertia_price = std::abs(duals[iit->second.at(buid)]);
  }

  const auto gen_p = [&](std::string_view g)
  {
    const auto& gl = bds_find<GeneratorLP>(sys, g);
    return sol[gl.generation_cols_at(scenario, stage).at(buid)];
  };
  out.p_type1 = gen_p("type1");
  out.p_type2 = gen_p("type2");

  const auto prov = [&](std::string_view p)
  {
    const auto& rp = bds_find<ReserveProvisionLP>(sys, p);
    const auto c = rp.lookup_up_provision_col(scenario, stage, buid);
    REQUIRE(c.has_value());
    return sol[c.value_or(ColIndex {0})];
  };
  out.r_type2 = prov("fr2");
  out.r_total = prov("fr1") + out.r_type2;
  return out;
}

}  // namespace

TEST_CASE("Benchmark Badesa 2020 ED: fixed 372 MW FR requirement")  // NOLINT
{
  SUBCASE("Table 2: PD = 250 MW")
  {
    const auto r = bds_solve(bds_ed_json(250.0), "fr", /*with_inertia=*/false);
    CHECK(r.p_type1 == doctest::Approx(150.0));
    CHECK(r.p_type2 == doctest::Approx(0.0));
    // FR is offered at 0 £/MW and the requirement is slack-priced (0):
    // any provision >= 372 MW is optimal (paper reports 197 + 175).
    CHECK(r.r_total >= 372.0 - 1.0e-6);
    CHECK(r.lambda == doctest::Approx(17.0));
    CHECK(r.fr_price == doctest::Approx(0.0));
  }
  SUBCASE("Table 3: PD = 400 MW")
  {
    const auto r = bds_solve(bds_ed_json(400.0), "fr", /*with_inertia=*/false);
    CHECK(r.p_type1 == doctest::Approx(203.0));
    CHECK(r.p_type2 == doctest::Approx(97.0));
    CHECK(r.r_type2 == doctest::Approx(175.0));
    CHECK(r.r_total - r.r_type2 == doctest::Approx(197.0));
    CHECK(r.lambda == doctest::Approx(18.0));
    CHECK(r.fr_price == doctest::Approx(1.0));
    // 15·100 + 17·203 + 18·97
    CHECK(r.objective == doctest::Approx(1500.0 + 3451.0 + 1746.0));
  }
}

TEST_CASE(  // NOLINT
    "Benchmark Badesa 2020 UC (Table 11 data): RoCoF min inertia by commitment")
{
  SUBCASE("low RES 10 GW: inertia slack, FR priced by no-load")
  {
    const auto r =
        bds_solve(bds_uc_json(10000.0), "qss", /*with_inertia=*/true);
    CHECK(r.p_type2 == doctest::Approx(4500.0));
    CHECK(r.p_type1 == doctest::Approx(7700.0));
    CHECK(r.r_total == doctest::Approx(1800.0));
    CHECK(r.lambda == doctest::Approx(96.0));
    CHECK(r.fr_price == doctest::Approx(1.0));
    CHECK(r.inertia_price == doctest::Approx(0.0));
    CHECK(r.objective == doctest::Approx(999000.0));
  }
  SUBCASE("high RES 18 GW: inertia binds at 9.7 GBP/MWs, energy free")
  {
    const auto r =
        bds_solve(bds_uc_json(18000.0), "qss", /*with_inertia=*/true);
    CHECK(r.p_type2 == doctest::Approx(2250.0));
    CHECK(r.p_type1 == doctest::Approx(2250.0));
    CHECK(r.r_total >= 1800.0 - 1.0e-6);
    CHECK(r.lambda == doctest::Approx(0.0));
    CHECK(r.fr_price == doctest::Approx(0.0));
    CHECK(r.inertia_price == doctest::Approx(9.7));
    CHECK(r.objective == doctest::Approx(363750.0));
  }
}
