// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_inertia_committed_pmin.cpp
 * @brief     InertiaProvision defaults use the committed minimum
 * @date      2026-11-24
 *
 * Without an explicit ``provision_factor`` / ``provision_max`` the inertia
 * provision defaults to ``FE = H·S / Pmin`` and ``r_inertia ≤ Pmin``,
 * where Pmin is the unit's minimum stable output when committed.  It used
 * to be read from the generation column's lower bound, which is 0 both
 * with the documented UC pattern (``Generator.pmin = 0`` plus
 * ``Commitment.pmin``) and with any SimpleCommitment (it zeroes the
 * column bound before InertiaProvisionLP runs), so the provision fell
 * back to Pmax: FE = 500 / 200 = 2.5 and a 200 MW cap instead of the
 * committed minimum.
 */

#include <string>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/inertia_provision_lp.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{
/// One unit (pmax 200, H = 5 s, S = 100 MVA, so H·S = 500 MWs) feeding a
/// 300 MWs inertia zone.  @p gen_pmin is ``Generator.pmin``, @p uc_json
/// the commitment array entry (empty: no commitment).
[[nodiscard]] std::string inertia_case(const std::string& gen_pmin,
                                       const std::string& uc_json)
{
  return R"({
  "options": {"annual_discount_rate": 0.0,
    "model_options": {"use_single_bus": true, "demand_fail_cost": 1.0e5,
                      "scale_objective": 1}},
  "simulation": {"block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1,
                     "chronological": true}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "inertia_pmin", "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 120}],
    "generator_array": [
      {"uid": 1, "name": "unit", "bus": 1, "gcost": 40, "pmin": )"
      + gen_pmin + R"(, "pmax": 200, "capacity": 200}],
    )" + uc_json
      + R"(
    "inertia_zone_array": [{"uid": 1, "name": "iz", "requirement": 300,
                            "cost": 1000}],
    "inertia_provision_array": [{"uid": 1, "name": "ip", "generator": "unit",
      "inertia_zones": [1], "inertia_constant": 5, "rated_power": 100}]
  }
})";
}

struct ProvisionTerms
{
  double uppb;  ///< r_inertia upper bound (provision_max)
  double factor;  ///< coefficient of r_inertia in the zone requirement row
};

[[nodiscard]] ProvisionTerms provision_terms(PlanningLP& plp)
{
  auto& sys = plp.systems().front().front();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto& li = sys.linear_interface();

  const auto pcol =
      sys.elements<InertiaProvisionLP>().front().lookup_provision_col(
          scenario, stage, buid);
  REQUIRE(pcol.has_value());
  const auto col = pcol.value_or(ColIndex {0});

  const auto& rows = sys.elements<InertiaZoneLP>().front().requirement_rows();
  const auto it = rows.find(std::tuple {scenario.uid(), stage.uid()});
  REQUIRE(it != rows.end());
  const auto rit = it->second.find(buid);
  REQUIRE(rit != it->second.end());

  return {
      .uppb = li.get_col_upp()[col],
      .factor = li.get_coeff(rit->second, col),
  };
}
}  // namespace

TEST_CASE("InertiaProvision defaults use the committed minimum")  // NOLINT
{
  SUBCASE("Commitment.pmin with Generator.pmin = 0")
  {
    PlanningLP plp(parse_planning_json(inertia_case(
        "0",
        R"("commitment_array": [{"uid": 1, "name": "uc", "generator": "unit",
            "initial_status": 1, "pmin": 50}],)")));
    REQUIRE(plp.resolve().has_value());
    const auto t = provision_terms(plp);
    // Pmin = Commitment.pmin = 50: FE = 500 / 50 = 10, r_inertia <= 50.
    CHECK(t.factor == doctest::Approx(10.0));
    CHECK(t.uppb == doctest::Approx(50.0));
  }

  SUBCASE("SimpleCommitment.dispatch_pmin")
  {
    PlanningLP plp(parse_planning_json(
        inertia_case("0",
                     R"("simple_commitment_array": [{"uid": 1, "name": "sc",
            "generator": "unit", "dispatch_pmin": 40, "relax": true}],)")));
    REQUIRE(plp.resolve().has_value());
    const auto t = provision_terms(plp);
    // Pmin = dispatch_pmin = 40: FE = 500 / 40 = 12.5, r_inertia <= 40.
    CHECK(t.factor == doctest::Approx(12.5));
    CHECK(t.uppb == doctest::Approx(40.0));
  }

  SUBCASE("SimpleCommitment without dispatch_pmin keeps Generator.pmin")
  {
    // SimpleCommitment zeroes the generation column's lower bound; the
    // committed minimum is still Generator.pmin = 40.
    PlanningLP plp(parse_planning_json(
        inertia_case("40",
                     R"("simple_commitment_array": [{"uid": 1, "name": "sc",
            "generator": "unit", "relax": true}],)")));
    REQUIRE(plp.resolve().has_value());
    const auto t = provision_terms(plp);
    CHECK(t.factor == doctest::Approx(12.5));
    CHECK(t.uppb == doctest::Approx(40.0));
  }

  SUBCASE("no commitment: Generator.pmin (unchanged behaviour)")
  {
    PlanningLP plp(parse_planning_json(inertia_case("50", "")));
    REQUIRE(plp.resolve().has_value());
    const auto t = provision_terms(plp);
    CHECK(t.factor == doctest::Approx(10.0));
    CHECK(t.uppb == doctest::Approx(50.0));
  }

  SUBCASE("no committed minimum at all: Pmax fallback (unchanged)")
  {
    PlanningLP plp(parse_planning_json(inertia_case("0", "")));
    REQUIRE(plp.resolve().has_value());
    const auto t = provision_terms(plp);
    // FE = 500 / 200 = 2.5, r_inertia <= 200.
    CHECK(t.factor == doctest::Approx(2.5));
    CHECK(t.uppb == doctest::Approx(200.0));
  }
}
