// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_dprov_commitment_pmin.cpp
 * @brief     Down-reserve headroom uses the committed minimum (Commitment.pmin)
 * @date      2026-11-24
 *
 * The documented UC pattern is ``Generator.pmin = 0`` (no always-on floor)
 * plus a conditional ``Commitment.pmin``.  CommitmentLP turns the
 * down-provision headroom row ``g - r_dn >= Pmin`` into
 * ``g - r_dn - Pmin*u >= 0``; it used to read Pmin from the generation
 * column's lowb only, i.e. 0 here, so down reserve came for free and the
 * unit sat at its minimum while "providing" it.  Found by the Sienna
 * (PowerSimulations) reserve cross-check: Sundance at 100 MW instead of
 * 100 + r_dn.
 */

#include <string>

#include <doctest/doctest.h>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{
[[nodiscard]] std::string dprov_case(bool commitment_pmin)
{
  // Demand 150, the cheap base unit is capped at 50, so the 40 $/MWh unit
  // must run (u = 1, integer).  Only that unit offers the 3 MW down reserve.
  const std::string unit_pmin = commitment_pmin ? "0" : "100";
  const std::string uc_pmin = commitment_pmin ? R"(, "pmin": 100)" : "";
  return R"({
  "options": {"annual_discount_rate": 0.0,
    "model_options": {"use_single_bus": true, "demand_fail_cost": 1.0e5,
                      "scale_objective": 1}},
  "simulation": {"block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1,
                     "chronological": true}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "dprov", "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 150}],
    "generator_array": [
      {"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": 50},
      {"uid": 2, "name": "unit", "bus": 1, "gcost": 40, "pmin": )"
      + unit_pmin + R"(, "pmax": 200, "capacity": 200}],
    "commitment_array": [{"uid": 1, "name": "uc_unit", "generator": "unit",
      "initial_status": 1)"
      + uc_pmin + R"(}],
    "reserve_zone_array": [{"uid": 1, "name": "down", "drreq": 3}],
    "reserve_provision_array": [{"uid": 1, "name": "rp_unit",
      "generator": "unit", "reserve_zones": [1], "drmax": 200,
      "dr_provision_factor": 1}]
  }
})";
}

[[nodiscard]] double unit_generation(PlanningLP& plp)
{
  auto& sys = plp.systems().front().front();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto& gens = sys.elements<GeneratorLP>();
  const auto col = gens.at(1).lookup_generation_col(
      scenario, stage, stage.blocks().front().uid());
  REQUIRE(col.has_value());
  return sys.linear_interface().get_col_sol()[col.value_or(ColIndex {0})];
}
}  // namespace

TEST_CASE("Down reserve headroom uses Commitment.pmin")  // NOLINT
{
  SUBCASE("Commitment.pmin with Generator.pmin = 0")
  {
    PlanningLP plp(parse_planning_json(dprov_case(/*commitment_pmin=*/true)));
    REQUIRE(plp.resolve().has_value());
    // g - r_dn >= 100 * u with u = 1 and r_dn = 3  =>  g >= 103.
    CHECK(unit_generation(plp) == doctest::Approx(103.0));
  }
  SUBCASE("legacy Generator.pmin only (no Commitment.pmin)")
  {
    PlanningLP plp(parse_planning_json(dprov_case(/*commitment_pmin=*/false)));
    REQUIRE(plp.resolve().has_value());
    CHECK(unit_generation(plp) == doctest::Approx(103.0));
  }
}
