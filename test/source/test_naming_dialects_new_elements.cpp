// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Naming-dialect coverage of the newer element classes (gas network,
// LNG terminal, hydrogen / ammonia / thermal carriers, carrier converter,
// allowance pool, line commitment, inertia, decision variables) and of
// newer fields on the classic elements (battery
// exclusive_charge_discharge, generator initial_generation, line overload
// band).  For every dialect of the shipped dictionary the planning must
// survive canonical -> dialect -> canonical unchanged, and the dialect
// form must parse to the same elements as the canonical form.

#include <array>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/json_canonicalize.hpp>
#include <gtopt/names_registry.hpp>

using namespace gtopt;

namespace test_naming_dialects_new_elements_detail
{
namespace
{

// clang-format off
constexpr std::string_view kNewElementsPlanning = R"json({
  "options": {
    "model_options": {"demand_fail_cost": 1000},
    "gas_options": {"slp_iterations": 2, "linepack_cyclic": true}
  },
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
    "scenario_array": [{"uid": 1}]
  },
  "system": {
    "name": "new_elements",
    "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 100,
       "gcost": 30, "capacity": 100, "initial_generation": 20}
    ],
    "demand_array": [{"uid": 1, "name": "d1", "bus": "b2", "lmax": 50}],
    "line_array": [
      {"uid": 1, "name": "l1", "bus_a": "b1", "bus_b": "b2",
       "reactance": 0.1, "tmax_ab": 80, "tmax_ba": 80,
       "tmax_normal_ab": 60, "overload_penalty": 500}
    ],
    "battery_array": [
      {"uid": 1, "name": "bat1", "bus": "b1", "emin": 0, "emax": 10,
       "eini": 5, "efin": 5, "daily_cycle": true,
       "input_efficiency": 0.95, "output_efficiency": 0.95,
       "exclusive_charge_discharge": true}
    ],
    "gas_node_array": [
      {"uid": 1, "name": "n1", "pmin": 30, "pmax": 70},
      {"uid": 2, "name": "n2", "pmin": 30, "pmax": 70}
    ],
    "gas_supply_array": [
      {"uid": 1, "name": "field1", "gas_node": "n1", "fmax": 5, "cost": 2}
    ],
    "gas_demand_array": [
      {"uid": 1, "name": "gd1", "gas_node": "n2", "load": 1,
       "fail_cost": 100}
    ],
    "gas_pipe_array": [
      {"uid": 1, "name": "p1", "gas_node_a": "n1", "gas_node_b": "n2",
       "fmax": 6, "weymouth_mode": "linearized", "linepack": true,
       "linepack_coeff": 0.01}
    ],
    "gas_compressor_array": [
      {"uid": 1, "name": "c1", "gas_node_in": "n1", "gas_node_out": "n2",
       "fmax": 4, "max_ratio": 1.5}
    ],
    "gas_turbine_array": [
      {"uid": 1, "name": "gt1", "generator": "g1", "gas_node": "n2",
       "heat_rate": 0.2}
    ],
    "lng_terminal_array": [
      {"uid": 1, "name": "lng1", "emin": 0, "emax": 100, "eini": 50,
       "ecost": 0.1, "spillway_cost": 10, "annual_standing_loss": 0.01,
       "generators": [{"generator": "g1", "heat_rate": 0.18}]}
    ],
    "hydrogen_node_array": [{"uid": 1, "name": "h2"}],
    "hydrogen_storage_array": [
      {"uid": 1, "name": "h2s", "hydrogen_node": "h2", "emax": 20,
       "eini": 10, "daily_cycle": true}
    ],
    "hydrogen_demand_array": [
      {"uid": 1, "name": "h2d", "hydrogen_node": "h2", "lmax": 3}
    ],
    "ammonia_node_array": [{"uid": 1, "name": "nh3"}],
    "ammonia_storage_array": [
      {"uid": 1, "name": "nh3s", "emax": 20, "eini": 10}
    ],
    "thermal_node_array": [{"uid": 1, "name": "th"}],
    "thermal_storage_array": [
      {"uid": 1, "name": "ths", "emax": 20, "eini": 10,
       "daily_cycle": false}
    ],
    "carrier_converter_array": [
      {"uid": 1, "name": "elz", "from_carrier": "electric",
       "to_carrier": "hydrogen", "from_node": "b1", "to_node": "h2",
       "efficiency": 0.7, "capacity": 10}
    ],
    "allowance_pool_array": [
      {"uid": 1, "name": "ets", "emax": 1000, "eini": 100,
       "auction_price": 80, "auction_cap": 50, "delivery": 10}
    ],
    "line_commitment_array": [
      {"uid": 1, "name": "lc1", "line": "l1", "initial_status": 1,
       "startup_cost": 5, "min_up_time": 2, "min_down_time": 2,
       "starts_scope": "day"}
    ],
    "inertia_zone_array": [
      {"uid": 1, "name": "iz", "requirement": 1000, "cost": 50}
    ],
    "inertia_provision_array": [
      {"uid": 1, "name": "ip1", "generator": "g1", "inertia_zones": ["iz"],
       "inertia_constant": 4, "rated_power": 100}
    ],
    "decision_variable_array": [
      {"uid": 1, "name": "dv1", "lower_bound": 0, "upper_bound": 10,
       "cost": 1}
    ]
  }
})json";
// clang-format on

/// Every dialect tag of the shipped dictionary.
constexpr std::array<std::string_view, 8> kDialects {
    "gtopt",
    "gtopt-legacy",
    "plp",
    "sddp",
    "plexos",
    "pypsa",
    "pandapower",
    "ucjl",
};

}  // namespace
}  // namespace test_naming_dialects_new_elements_detail

TEST_CASE(  // NOLINT
    "naming dialects — new element classes round-trip canonical -> dialect")
{
  using test_naming_dialects_new_elements_detail::kDialects;
  using test_naming_dialects_new_elements_detail::kNewElementsPlanning;

  const auto& registry = NamesRegistry::instance();
  const auto canonical = parse_planning_json(kNewElementsPlanning);
  const auto& sys = canonical.system;
  REQUIRE(sys.gas_pipe_array.size() == 1);
  REQUIRE(sys.lng_terminal_array.size() == 1);

  for (const auto dialect : kDialects) {
    CAPTURE(dialect);
    const auto dialect_form =
        decanonicalize_json_keys(kNewElementsPlanning, registry, dialect);

    // The key rewrite is lossless ...
    CHECK(canonicalize_json_keys(dialect_form, registry)
          == kNewElementsPlanning);

    // ... and the dialect form parses to the same elements.
    {
      const auto parsed = parse_planning_json(dialect_form);
      const auto& got = parsed.system;
      CHECK(daw::json::to_json(got.gas_node_array)
            == daw::json::to_json(sys.gas_node_array));
      CHECK(daw::json::to_json(got.gas_supply_array)
            == daw::json::to_json(sys.gas_supply_array));
      CHECK(daw::json::to_json(got.gas_demand_array)
            == daw::json::to_json(sys.gas_demand_array));
      CHECK(daw::json::to_json(got.gas_pipe_array)
            == daw::json::to_json(sys.gas_pipe_array));
      CHECK(daw::json::to_json(got.gas_compressor_array)
            == daw::json::to_json(sys.gas_compressor_array));
      CHECK(daw::json::to_json(got.gas_turbine_array)
            == daw::json::to_json(sys.gas_turbine_array));
      CHECK(daw::json::to_json(got.lng_terminal_array)
            == daw::json::to_json(sys.lng_terminal_array));
      CHECK(daw::json::to_json(got.hydrogen_storage_array)
            == daw::json::to_json(sys.hydrogen_storage_array));
      CHECK(daw::json::to_json(got.hydrogen_demand_array)
            == daw::json::to_json(sys.hydrogen_demand_array));
      CHECK(daw::json::to_json(got.ammonia_storage_array)
            == daw::json::to_json(sys.ammonia_storage_array));
      CHECK(daw::json::to_json(got.thermal_storage_array)
            == daw::json::to_json(sys.thermal_storage_array));
      CHECK(daw::json::to_json(got.carrier_converter_array)
            == daw::json::to_json(sys.carrier_converter_array));
      CHECK(daw::json::to_json(got.allowance_pool_array)
            == daw::json::to_json(sys.allowance_pool_array));
      CHECK(daw::json::to_json(got.line_commitment_array)
            == daw::json::to_json(sys.line_commitment_array));
      CHECK(daw::json::to_json(got.inertia_zone_array)
            == daw::json::to_json(sys.inertia_zone_array));
      CHECK(daw::json::to_json(got.inertia_provision_array)
            == daw::json::to_json(sys.inertia_provision_array));
      CHECK(daw::json::to_json(got.decision_variable_array)
            == daw::json::to_json(sys.decision_variable_array));
      CHECK(daw::json::to_json(got.battery_array)
            == daw::json::to_json(sys.battery_array));
      CHECK(daw::json::to_json(got.generator_array)
            == daw::json::to_json(sys.generator_array));
      CHECK(daw::json::to_json(got.line_array)
            == daw::json::to_json(sys.line_array));
    }
  }
}

TEST_CASE(  // NOLINT
    "naming dialects — aliases of the new element classes canonicalise")
{
  const auto& registry = NamesRegistry::instance();

  SUBCASE("PLP GNL terminal columns (plpcnfgnl.dat)")
  {
    CHECK(canonicalize_json_keys(
              R"({"VMax": 100, "Vini": 50, "CVer": 10, "CAlm": 0.1})", registry)
          == R"({"emax": 100, "eini": 50, "spillway_cost": 10, "ecost": 0.1})");
  }

  SUBCASE("PyPSA Store / Link attributes")
  {
    CHECK(canonicalize_json_keys(
              R"({"e_initial": 5, "e_cyclic": true, "bus0": "b1", )"
              R"("bus1": "h2", "p_nom": 10})",
              registry)
          == R"({"eini": 5, "daily_cycle": true, "from_node": "b1", )"
             R"("to_node": "h2", "capacity": 10})");
  }

  SUBCASE("PLEXOS gas properties")
  {
    CHECK(canonicalize_json_keys(
              R"({"Production Cost": 2, "Max Production": 5})", registry)
          == R"({"cost": 2, "fmax": 5})");
    CHECK(canonicalize_json_keys(R"({"Max Flow Day": 6})", registry)
          == R"({"fmax": 6})");
  }

  SUBCASE("a PLP-dialect LNG terminal parses into the canonical struct")
  {
    const auto planning = parse_planning_json(R"json({
      "system": {
        "name": "s",
        "lng_terminal_array": [
          {"uid": 1, "name": "lng1", "VMax": 100, "Vini": 50, "CVer": 10}
        ]
      }
    })json");
    REQUIRE(planning.system.lng_terminal_array.size() == 1);
    const auto& lng = planning.system.lng_terminal_array[0];
    CHECK(lng.emax.has_value());
    CHECK(lng.eini.has_value());
    CHECK(lng.spillway_cost.has_value());
  }
}
