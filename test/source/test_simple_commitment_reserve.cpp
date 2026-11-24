// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_simple_commitment_reserve.cpp
 * @brief     SimpleCommitment gates the reserve headroom rows with u
 * @date      2026-11-24
 *
 * ReserveProvisionLP is built before the commitment elements, so its
 * online headroom rows carry the generator's plain bounds:
 *   up:   g + r_up <= Pmax          down:  g - r_dn >= Generator.pmin
 * CommitmentLP rewrites them with its status u; SimpleCommitmentLP used
 * not to, so with a SimpleCommitment
 *  - an OFF unit (u = 0, g = 0) offered up to Pmax of up reserve,
 *  - the down row ignored dispatch_pmin (g - r_dn >= 0: free down
 *    reserve at the committed minimum), and
 *  - with Generator.pmin > 0 the down row kept an always-on floor that
 *    SimpleCommitment had removed from the column, so u = 0 was
 *    infeasible and the unit was forced on,
 *  - offline (cold) up reserve was offered while the unit ran.
 * Now the same gating as CommitmentLP applies:
 *   g + r_up - Pmax·u <= 0,  g - r_dn - dispatch_pmin·u >= 0,
 *   r_cold <= urmax·(1 - u).
 */

#include <string>

#include <doctest/doctest.h>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{
/// Single-bus, single-block case.  "base" (10 $/MWh, cap @p base_cap) and
/// "unit" (50 $/MWh, pmax 200, SimpleCommitment with binary u).
[[nodiscard]] std::string sc_case(const std::string& base_cap,
                                  const std::string& demand,
                                  const std::string& unit_pmin,
                                  const std::string& sc_extra,
                                  const std::string& zones,
                                  const std::string& provisions)
{
  return R"({
  "options": {"annual_discount_rate": 0.0,
    "model_options": {"use_single_bus": true, "demand_fail_cost": 1.0e5,
                      "scale_objective": 1}},
  "simulation": {"block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "sc_reserve", "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": )"
      + demand + R"(}],
    "generator_array": [
      {"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": )"
      + base_cap + R"(},
      {"uid": 2, "name": "unit", "bus": 1, "gcost": 50, "pmin": )"
      + unit_pmin + R"(, "pmax": 200, "capacity": 200}],
    "simple_commitment_array": [{"uid": 1, "name": "sc_unit",
      "generator": "unit")"
      + sc_extra + R"(}],
    "reserve_zone_array": )"
      + zones + R"(,
    "reserve_provision_array": )"
      + provisions + R"(
  }
})";
}

struct Result
{
  double obj;
  double unit_gen;
};

[[nodiscard]] Result solve(const std::string& json)
{
  PlanningLP plp(parse_planning_json(json));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto col = sys.elements<GeneratorLP>().at(1).lookup_generation_col(
      scenario, stage, stage.blocks().front().uid());
  REQUIRE(col.has_value());
  const auto& li = sys.linear_interface();
  return {
      .obj = li.get_obj_value(),
      .unit_gen = li.get_col_sol()[col.value_or(ColIndex {0})],
  };
}
}  // namespace

TEST_CASE("SimpleCommitment gates reserve headroom with u")  // NOLINT
{
  SUBCASE("up reserve needs the unit online")
  {
    // Demand 100 is all served by base.  30 MW of up reserve can only
    // come from "unit" (shortage 1000 $/MW).  An OFF unit must not offer
    // it, so the unit commits at dispatch_pmin = 20:
    //   obj = 80·10 + 20·50 = 1800   (ungated: 100·10 = 1000, g = 0).
    const auto r = solve(sc_case("100",
                                 "100",
                                 "0",
                                 R"(, "dispatch_pmin": 20)",
                                 R"([{"uid": 1, "name": "up", "urreq": 30,
                                      "urcost": 1000}])",
                                 R"([{"uid": 1, "name": "rp_unit",
                                      "generator": "unit",
                                      "reserve_zones": [1], "urmax": 200,
                                      "ur_provision_factor": 1}])"));
    CHECK(r.unit_gen == doctest::Approx(20.0));
    CHECK(r.obj == doctest::Approx(1800.0));
  }

  SUBCASE("down reserve floor is dispatch_pmin (Generator.pmin = 0)")
  {
    // Demand 150, base capped at 50: unit runs at 100 = dispatch_pmin and
    // must offer 3 MW of down reserve:  g - r_dn >= 100·u  =>  g = 103.
    const auto r = solve(sc_case("50",
                                 "150",
                                 "0",
                                 R"(, "dispatch_pmin": 100)",
                                 R"([{"uid": 1, "name": "dn", "drreq": 3,
                                      "drcost": 1000}])",
                                 R"([{"uid": 1, "name": "rp_unit",
                                      "generator": "unit",
                                      "reserve_zones": [1], "drmax": 200,
                                      "dr_provision_factor": 1}])"));
    CHECK(r.unit_gen == doctest::Approx(103.0));
    // base 47·10 + unit 103·50 = 5620.
    CHECK(r.obj == doctest::Approx(5620.0));
  }

  SUBCASE("Generator.pmin is not re-imposed as an always-on floor")
  {
    // Generator.pmin = 20 (= the committed minimum).  Base covers the
    // demand and also offers the down reserve, so the unit should stay
    // OFF.  Ungated, g - r_dn >= 20 forced u = 1 and g >= 20:
    //   obj = 80·10 + 20·50 = 1800 instead of 100·10 = 1000.
    const auto r = solve(sc_case("200",
                                 "100",
                                 "20",
                                 "",
                                 R"([{"uid": 1, "name": "dn", "drreq": 3,
                                      "drcost": 1000}])",
                                 R"([{"uid": 1, "name": "rp_base",
                                      "generator": "base",
                                      "reserve_zones": [1], "drmax": 200,
                                      "dr_provision_factor": 1},
                                     {"uid": 2, "name": "rp_unit",
                                      "generator": "unit",
                                      "reserve_zones": [1], "drmax": 200,
                                      "dr_provision_factor": 1,
                                      "drcost": 1}])"));
    CHECK(r.unit_gen == doctest::Approx(0.0));
    CHECK(r.obj == doctest::Approx(1000.0));
  }

  SUBCASE("offline (cold) up reserve only while the unit is off")
  {
    // Demand 150 with base capped at 50 forces the unit on (g = 100).
    // Its 30 MW offline reserve is then unavailable: 30 MW short at
    // 1000 $/MW.  obj = 50·10 + 100·50 + 30·1000 = 35500
    // (ungated: 5500).
    const auto r = solve(sc_case("50",
                                 "150",
                                 "0",
                                 "",
                                 R"([{"uid": 1, "name": "up", "urreq": 30,
                                      "urcost": 1000}])",
                                 R"([{"uid": 1, "name": "rp_cold",
                                      "generator": "unit",
                                      "reserve_zones": [1], "urmax": 30,
                                      "ur_provision_factor": 1,
                                      "offline": true}])"));
    CHECK(r.unit_gen == doctest::Approx(100.0));
    CHECK(r.obj == doctest::Approx(35500.0));
  }
}
