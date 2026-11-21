// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Regression tests: Emission references (`EmissionZone.emissions[]`,
// `EmissionSource.emission`, `Fuel.emission_factors[]`,
// `Generator.emission_captures[]`) may name the pollutant by uid OR by
// name.  The legacy folds and `EmissionSourceLP` used to compare the
// raw `SingleId` variants, so `"co2"` vs `Uid{1}` silently failed to
// match: a priced zone listing `"co2"` by name was shadowed by a fresh
// unpriced `default_co2` zone, and mixed-form sources were dropped.
//
// Also covers the `heat_rate_segments` fuel-basis fold in
// `System::expand_fuel_emission_sources`.

#include <algorithm>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/emission.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

/// 1 bus, 1 block (1 h), one 50 MW demand served by one generator
/// (gcost 10 $/MWh, legacy `emission_rate` 0.5 t/MWh).  The single
/// zone lists CO₂ BY NAME and prices it at 20 $/t.
///
/// Expected objective = 50 · (10 + 20 · 0.5) = 1000.  Before the fix
/// the legacy fold created an unpriced `default_co2` zone and the
/// objective was 500.
constexpr std::string_view priced_zone_by_name_json = R"json(
  {
    "options": {
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {
        "use_single_bus": true,
        "scale_objective": 1,
        "demand_fail_cost": 10000
      }
    },
    "simulation": {
      "block_array": [ { "uid": 1, "duration": 1 } ],
      "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
      "scenario_array": [ { "uid": 1, "probability_factor": 1 } ]
    },
    "system": {
      "name": "priced_zone_by_name",
      "bus_array": [ { "uid": 1, "name": "b1" } ],
      "generator_array": [
        {
          "uid": 1,
          "name": "g1",
          "bus": "b1",
          "gcost": 10,
          "capacity": 100,
          "emission_rate": 0.5
        }
      ],
      "demand_array": [
        { "uid": 1, "name": "d1", "bus": "b1", "lmax": [[50.0]] }
      ],
      "emission_array": [ { "uid": 1, "name": "co2" } ],
      "emission_zone_array": [
        {
          "uid": 1,
          "name": "co2_tax",
          "emissions": [ { "emission": "co2", "weight": 1.0 } ],
          "price": 20
        }
      ]
    }
  }
)json";

}  // namespace

TEST_CASE("same_emission — resolves name and uid references")  // NOLINT
{
  const Array<Emission> emissions {
      {.uid = Uid {1}, .name = "co2"},
      {.uid = Uid {2}, .name = "ch4"},
  };
  CHECK(same_emission(SingleId {Uid {1}}, SingleId {Name {"co2"}}, emissions));
  CHECK(same_emission(SingleId {Name {"co2"}}, SingleId {Uid {1}}, emissions));
  CHECK(same_emission(
      SingleId {Name {"ch4"}}, SingleId {Name {"ch4"}}, emissions));
  CHECK_FALSE(
      same_emission(SingleId {Uid {2}}, SingleId {Name {"co2"}}, emissions));
  // Unknown names never match a different reference.
  CHECK_FALSE(
      same_emission(SingleId {Name {"nox"}}, SingleId {Uid {3}}, emissions));
  CHECK(
      resolve_emission_uid(SingleId {Name {"ch4"}}, emissions).value_or(Uid {0})
      == Uid {2});
  CHECK_FALSE(
      resolve_emission_uid(SingleId {Name {"nox"}}, emissions).has_value());
}

TEST_CASE(
    "fold_legacy_emission_rate — reuses a zone that lists co2 by "
    "name")  // NOLINT
{
  System sys;
  sys.emission_array = {{.uid = Uid {5}, .name = "co2"}};
  sys.emission_zone_array = {{
      .uid = Uid {3},
      .name = "co2_tax",
      .emissions = {{.emission = SingleId {Name {"co2"}}, .weight = 1.0}},
      .price = 20.0,
  }};
  sys.generator_array = {
      {.uid = Uid {1}, .name = "g1", .bus = Uid {1}, .emission_rate = 0.5}};

  sys.fold_legacy_emission_rate();

  CHECK(sys.emission_array.size() == 1);
  // No unpriced `default_co2` zone shadowing the priced one.
  REQUIRE(sys.emission_zone_array.size() == 1);
  REQUIRE(sys.emission_source_array.size() == 1);
  CHECK(std::get<Uid>(sys.emission_source_array.front().zone) == Uid {3});
}

TEST_CASE(
    "fold_legacy_fuel_emission_factors — merges into a co2 row named by "
    "name")  // NOLINT
{
  System sys;
  sys.emission_array = {{.uid = Uid {1}, .name = "co2"}};
  sys.fuel_array = {Fuel {
      .uid = Uid {1},
      .name = "gas",
      .combustion_emission_factor = OptTRealFieldSched {0.3},
      .upstream_emission_factor = OptTRealFieldSched {0.05},
      .emission_factors = {{.emission = SingleId {Name {"co2"}},
                            .combustion = OptTRealFieldSched {0.2}}},
  }};

  sys.fold_legacy_fuel_emission_factors();

  const auto& fefs = sys.fuel_array.front().emission_factors;
  REQUIRE(fefs.size() == 1);  // merged, not duplicated
  // New-table value wins on conflict; legacy fills the empty slot.
  CHECK(std::get<Real>(fefs.front().combustion.value_or(0.0))
        == doctest::Approx(0.2));
  CHECK(std::get<Real>(fefs.front().upstream.value_or(0.0))
        == doctest::Approx(0.05));
}

TEST_CASE(
    "expand_fuel_emission_sources — zone by uid, fuel factor by "
    "name")  // NOLINT
{
  System sys;
  sys.emission_array = {{.uid = Uid {1}, .name = "co2"}};
  sys.emission_zone_array = {{
      .uid = Uid {1},
      .name = "global_co2",
      .emissions = {{.emission = SingleId {Uid {1}}, .weight = 1.0}},
  }};
  sys.fuel_array = {Fuel {
      .uid = Uid {1},
      .name = "gas",
      .emission_factors = {{.emission = SingleId {Name {"co2"}},
                            .combustion = OptTRealFieldSched {0.2}}},
  }};
  sys.generator_array = {Generator {
      .uid = Uid {7},
      .name = "ngcc",
      .bus = Uid {1},
      .fuel = OptSingleId {SingleId {Uid {1}}},
      .heat_rate = OptTBRealFieldSched {7.0},
      .capacity = 100.0,
  }};

  sys.expand_fuel_emission_sources();

  REQUIRE(sys.emission_source_array.size() == 1);
  CHECK(std::get<Real>(sys.emission_source_array.front().rate.value_or(0.0))
        == doctest::Approx(7.0 * 0.2));
}

TEST_CASE(
    "expand_fuel_emission_sources — heat_rate_segments get a "
    "fuel-basis source (#545)")  // NOLINT
{
  System sys;
  sys.emission_array = {{.uid = Uid {1}, .name = "co2"}};
  sys.emission_zone_array = {{
      .uid = Uid {1},
      .name = "global_co2",
      .emissions = {{.emission = SingleId {Uid {1}}, .weight = 1.0}},
  }};
  sys.fuel_array = {Fuel {
      .uid = Uid {1},
      .name = "gas",
      .emission_factors = {{.emission = SingleId {Uid {1}},
                            .combustion = OptTRealFieldSched {0.2}}},
  }};
  sys.generator_array = {Generator {
      .uid = Uid {7},
      .name = "pw_ngcc",
      .bus = Uid {1},
      .fuel = OptSingleId {SingleId {Uid {1}}},
      .pmax_segments = {50.0, 100.0},
      .heat_rate_segments = {7.0, 9.0},
      .capacity = 100.0,
  }};

  sys.expand_fuel_emission_sources();

  // Piecewise burn is carried on the fuel basis (t / fuel-unit), no
  // per-MWh rate: heat_content unset ⇒ fuel_rate = combustion factor.
  REQUIRE(sys.emission_source_array.size() == 1);
  const auto& src = sys.emission_source_array.front();
  CHECK_FALSE(src.rate.has_value());
  CHECK(std::get<Real>(src.fuel_rate.value_or(0.0)) == doctest::Approx(0.2));
}

TEST_CASE(
    "EmissionSourceLP — source by uid, zone lists the pollutant by "
    "name")  // NOLINT
{
  // Price 20 $/t × rate 0.5 t/MWh = 10 $/MWh adder on top of gcost 10.
  // Before the fix the source was dropped (obj = 500).
  const Simulation simulation = {
      .block_array = {{.uid = Uid {1}, .duration = 1.0}},
      .stage_array = {{.uid = Uid {1}, .first_block = 0, .count_block = 1}},
      .scenario_array = {{.uid = Uid {0}}},
  };

  const System system = {
      .name = "EmissionSourceMixedRefs",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array = {{.uid = Uid {1},
                        .name = "d1",
                        .bus = Uid {1},
                        .fcost = 1.0e4,
                        .capacity = 50.0}},
      .generator_array = {{.uid = Uid {1},
                           .name = "g1",
                           .bus = Uid {1},
                           .gcost = 10.0,
                           .capacity = 200.0}},
      .emission_array = {{.uid = Uid {1}, .name = "co2"}},
      .emission_zone_array = {{
          .uid = Uid {1},
          .name = "co2_tax",
          .emissions = {{.emission = SingleId {Name {"co2"}}, .weight = 1.0}},
          .price = 20.0,
      }},
      .emission_source_array = {{.uid = Uid {1},
                                 .name = "g1_co2",
                                 .generator = OptSingleId {Uid {1}},
                                 .zone = Uid {1},
                                 .emission = SingleId {Uid {1}},
                                 .rate = 0.5}},
  };

  PlanningOptions popts;
  popts.model_options.scale_objective = 1.0;
  const PlanningOptionsLP options {popts};
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(system, simulation_lp);

  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);
  CHECK(lp.get_obj_value() == doctest::Approx(50.0 * (10.0 + 20.0 * 0.5)));
}

TEST_CASE(
    "Planning — priced zone listing co2 by name + legacy emission_rate "
    "pays the price")  // NOLINT
{
  auto planning = daw::json::from_json<Planning>(priced_zone_by_name_json,
                                                 StrictParsePolicy);
  PlanningLP planning_lp(std::move(planning));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());

  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  const auto& li = systems.front().front().linear_interface();
  CHECK(li.get_obj_value_raw() == doctest::Approx(1000.0).epsilon(1e-6));
}
