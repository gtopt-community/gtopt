// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_ampl_param_registry_golden.cpp
 * @brief     Golden pin of the AMPL (class, attribute) parameter registry
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `register_ampl_param_dispatchers` (ampl_dispatch_registry.cpp) fills
 * the `(class, attribute) -> resolver` table read by user constraints.
 * `kGolden` below was dumped from the hand-written shims (one function
 * per pair, before the table-driven rewrite) and pins two contracts:
 *
 *  1. the registered set is EXACTLY the golden set (nothing dropped,
 *     nothing added by accident — adding an attribute means adding a
 *     golden row);
 *  2. every registered resolver resolves: called on a fixture element
 *     of its class it returns the golden value (the fixtures give most
 *     attributes distinct values, so a resolver wired to the wrong
 *     accessor or the wrong class fails).
 *
 * A golden row with `std::nullopt` is an attribute the fixture leaves
 * unset: it still proves the resolver is callable on a real element.
 */

#include <algorithm>
#include <array>
#include <format>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_system.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct GoldenParam
{
  std::string_view class_name;
  std::string_view attribute;
  std::optional<double> value;
};

[[nodiscard]] constexpr GoldenParam golden_row(std::string_view class_name,
                                               std::string_view attribute,
                                               std::optional<double> value)
{
  return {
      .class_name = class_name,
      .attribute = attribute,
      .value = value,
  };
}

// clang-format off
constexpr auto kGolden = std::to_array<GoldenParam>({
    // Dumped from the hand-written shims (origin/master 47aa89eae).
    golden_row("allowance_pool", "delivery", 60),
    golden_row("allowance_pool", "ecost", 0.050000000000000003),
    golden_row("allowance_pool", "emax", 505),
    golden_row("allowance_pool", "emin", 5),
    golden_row("ammonia_storage", "ecost", 0.29999999999999999),
    golden_row("ammonia_storage", "emax", 303),
    golden_row("ammonia_storage", "emin", 3),
    golden_row("ammonia_storage", "input_efficiency", 0.70999999999999996),
    golden_row("ammonia_storage", "output_efficiency", 0.71999999999999997),
    golden_row("battery", "ecost", 0.40000000000000002),
    golden_row("battery", "emax", 404),
    golden_row("battery", "emin", 4),
    golden_row("battery", "input_efficiency", 0.93000000000000005),
    golden_row("battery", "output_efficiency", 0.93999999999999995),
    golden_row("carrier_converter", "efficiency", 0.65000000000000002),
    golden_row("carrier_converter", "ocost", 4),
    golden_row("demand", "fcost", 501),
    golden_row("demand", "lmax", 51),
    golden_row("demand", "lossfactor", 0.021000000000000001),
    golden_row("emission_source", "rate", 0.40000000000000002),
    golden_row("emission_source", "upstream_rate", 0.050000000000000003),
    golden_row("emission_zone", "cap", 1000),
    golden_row("emission_zone", "cap_cost", 50),
    golden_row("emission_zone", "price", 25),
    golden_row("flow_right", "fcost", 3),
    golden_row("flow_right", "fmax", 20),
    golden_row("flow_right", "fmin", 1),
    golden_row("flow_right", "target", 10),
    golden_row("flow_right", "uvalue", 4),
    golden_row("fuel", "combustion_emission_factor", 0.20000000000000001),
    golden_row("fuel", "heat_content", 1.1000000000000001),
    golden_row("fuel", "max_offtake", 1000),
    golden_row("fuel", "max_offtake_cost", 7),
    golden_row("fuel", "min_offtake", 10),
    golden_row("fuel", "min_offtake_cost", 3),
    golden_row("fuel", "price", 5.5),
    golden_row("fuel", "upstream_emission_factor", 0.01),
    golden_row("gas_compressor", "fmax", 310),
    golden_row("gas_compressor", "max_ratio", 1.5),
    golden_row("gas_compressor", "power_max", 7),
    golden_row("gas_demand", "fail_cost", 3000),
    golden_row("gas_demand", "load", 50),
    golden_row("gas_node", "pmax", 60),
    golden_row("gas_node", "pmin", 30),
    golden_row("gas_node", "pressure_cost", -0.0001),
    golden_row("gas_pipe", "fmax", 300),
    golden_row("gas_supply", "cost", 300),
    golden_row("gas_supply", "fmax", 1000),
    golden_row("gas_supply", "fmin", 5),
    golden_row("gas_supply", "volume", 5000),
    golden_row("gas_turbine", "heat_rate", 0.20000000000000001),
    golden_row("generator", "emission_rate", 0.40999999999999998),
    golden_row("generator", "gcost", 21),
    golden_row("generator", "heat_rate", 9.0999999999999996),
    golden_row("generator", "initial_generation", 41),
    golden_row("generator", "lossfactor", 0.031),
    golden_row("generator", "pmax", 101),
    golden_row("generator", "pmin", 11),
    golden_row("hydrogen_demand", "lmax", 12),
    golden_row("hydrogen_demand", "lmin", 2),
    golden_row("hydrogen_demand", "max_energy", 9),
    golden_row("hydrogen_demand", "min_energy", 1),
    golden_row("hydrogen_demand", "price", 6),
    golden_row("hydrogen_storage", "ecost", 0.20000000000000001),
    golden_row("hydrogen_storage", "emax", 202),
    golden_row("hydrogen_storage", "emin", 2),
    golden_row("hydrogen_storage", "input_efficiency", 0.81000000000000005),
    golden_row("hydrogen_storage", "output_efficiency", 0.81999999999999995),
    golden_row("line", "conductor_rating_ab", 230),
    golden_row("line", "conductor_rating_ba", 231),
    golden_row("line", "equipment_rating_ab", 240),
    golden_row("line", "equipment_rating_ba", 241),
    golden_row("line", "lossfactor", 0.012),
    golden_row("line", "max_flow", 201),
    golden_row("line", "max_rating", 210),
    golden_row("line", "min_flow", -202),
    golden_row("line", "min_rating", -209),
    golden_row("line", "outage_max_rating", 180),
    golden_row("line", "outage_min_rating", -179),
    golden_row("line", "overload_max_rating", 220),
    golden_row("line", "overload_min_rating", -219),
    golden_row("line", "phase_shift_deg", 2.5),
    golden_row("line", "reactance", 0.11),
    golden_row("line", "resistance", 0.012999999999999999),
    golden_row("line", "security_limit_ab", 250),
    golden_row("line", "security_limit_ba", 251),
    golden_row("line", "tap_ratio", 1.01),
    golden_row("line", "tcost", 0.69999999999999996),
    golden_row("line", "tmax_ab", 201),
    golden_row("line", "tmax_ba", 202),
    golden_row("line", "units_out", 0),
    golden_row("line", "voltage", 220),
    golden_row("lng_terminal", "delivery", 500),
    golden_row("lng_terminal", "ecost", 0.59999999999999998),
    golden_row("lng_terminal", "emax", 1000),
    golden_row("lng_terminal", "emin", 10),
    golden_row("reservoir", "capacity", 9500),
    golden_row("reservoir", "ecost", 0.5),
    golden_row("reservoir", "emax", 9000),
    golden_row("reservoir", "emin", 100),
    golden_row("thermal_storage", "ecost", 0.10000000000000001),
    golden_row("thermal_storage", "emax", 101),
    golden_row("thermal_storage", "emin", 1),
    golden_row("thermal_storage", "input_efficiency", 0.91000000000000003),
    golden_row("thermal_storage", "output_efficiency", 0.92000000000000004),
    golden_row("turbine", "capacity", 200),
    golden_row("turbine", "efficiency", 0.94999999999999996),
    golden_row("turbine", "production_factor", 2.5),
    golden_row("user_constraint", "rhs", 123),
    golden_row("volume_right", "demand", 100),
    golden_row("volume_right", "emax", 500),
    golden_row("volume_right", "emin", 0),
    golden_row("volume_right", "fail_cost", 1000),
    golden_row("volume_right", "fmax", 30),
    golden_row("volume_right", "saving_rate", 0.5),
    golden_row("waterway", "capacity", 75),
    golden_row("waterway", "fcost", 0.40000000000000002),
    golden_row("waterway", "fmax", 50),
    golden_row("waterway", "fmin", 5),
    golden_row("waterway", "lossfactor", 0.10000000000000001),
});
// clang-format on

/// Electric + hydro + emissions + user constraint.
constexpr std::string_view kGoldenElectricHydroJson = R"({
 "name":"ampl_golden_electric_hydro",
 "bus_array":[{"uid":1,"name":"b1"},{"uid":2,"name":"b2"}],
 "generator_array":[
  {"uid":1,"name":"g1","bus":"b1","pmin":11,"pmax":101,"gcost":21,
   "lossfactor":0.031,"heat_rate":9.1,"emission_rate":0.41,
   "initial_generation":41,"fuel":"gas","capacity":101},
  {"uid":2,"name":"g_hydro","bus":"b2","pmax":300,"gcost":0,
   "capacity":300}],
 "demand_array":[{"uid":1,"name":"d1","bus":"b2","lmax":51,"fcost":501,
   "lossfactor":0.021,"capacity":51}],
 "line_array":[{"uid":1,"name":"l1","bus_a":"b1","bus_b":"b2",
   "tmax_ab":201,"tmax_ba":202,"reactance":0.11,"voltage":220,
   "tap_ratio":1.01,"phase_shift_deg":2.5,"lossfactor":0.012,
   "resistance":0.013,"tcost":0.7,"min_flow":-150,"max_rating":210,
   "min_rating":-209,"overload_max_rating":220,"overload_min_rating":-219,
   "outage_max_rating":180,"outage_min_rating":-179,"units_out":0,
   "conductor_rating_ab":230,"conductor_rating_ba":231,
   "equipment_rating_ab":240,"equipment_rating_ba":241,
   "security_limit_ab":250,"security_limit_ba":251}],
 "fuel_array":[{"uid":1,"name":"gas","price":5.5,"heat_content":1.1,
   "combustion_emission_factor":0.2,"upstream_emission_factor":0.01,
   "max_offtake":1000,"max_offtake_cost":7,"min_offtake":10,
   "min_offtake_cost":3}],
 "emission_array":[{"uid":1,"name":"co2"}],
 "emission_zone_array":[{"uid":1,"name":"ez1","emissions":[{"emission":1}],
   "cap":1000,"cap_cost":50,"price":25}],
 "emission_source_array":[{"uid":1,"name":"es1","generator":1,"zone":1,
   "emission":1,"rate":0.4,"upstream_rate":0.05}],
 "junction_array":[{"uid":1,"name":"j_up"},
   {"uid":2,"name":"j_down","drain":true}],
 "waterway_array":[{"uid":1,"name":"ww1","junction_a":1,"junction_b":2,
   "capacity":75,"lossfactor":0.1,"fmin":5,"fmax":50,"fcost":0.4}],
 "reservoir_array":[{"uid":1,"name":"rsv1","junction":1,"capacity":9500,
   "emin":100,"emax":9000,"ecost":0.5,"eini":5000}],
 "turbine_array":[{"uid":1,"name":"t1","waterway":1,"generator":2,
   "production_factor":2.5,"efficiency":0.95,"capacity":200}],
 "flow_right_array":[{"uid":1,"name":"fr1","junction_a":1,"fmin":1,
   "fmax":20,"target":10,"fcost":3,"uvalue":4}],
 "volume_right_array":[{"uid":1,"name":"vr1","reservoir":1,"emin":0,
   "emax":500,"eini":0,"demand":100,"fmax":30,"saving_rate":0.5,
   "fail_cost":1000}],
 "user_constraint_array":[{"uid":1,"name":"uc1",
   "expression":"generator(\"g1\").generation <= 150","rhs":123}]})";

/// Gas network + LNG terminal.
constexpr std::string_view kGoldenGasJson = R"({
 "name":"ampl_golden_gas","bus_array":[{"uid":1,"name":"B"}],
 "generator_array":[
  {"uid":1,"name":"G_T","bus":"B","pmax":600,"capacity":600,"gcost":0}],
 "demand_array":[{"uid":1,"name":"D","bus":"B","lmax":100}],
 "gas_node_array":[
  {"uid":1,"name":"S","pmin":30,"pmax":60,"pressure_cost":-0.0001},
  {"uid":2,"name":"T","pmin":40,"pmax":70,"pressure_cost":-0.0002}],
 "gas_supply_array":[{"uid":1,"name":"SUPPLY","gas_node":"S","fmin":5,
   "fmax":1000,"cost":300,"volume":5000}],
 "gas_demand_array":[{"uid":1,"name":"CITY","gas_node":"T","load":50,
   "fail_cost":3000}],
 "gas_pipe_array":[{"uid":1,"name":"S_T","gas_node_a":"S",
   "gas_node_b":"T","weymouth_mode":"none","fmax":300}],
 "gas_compressor_array":[{"uid":1,"name":"CS","gas_node_in":"S",
   "gas_node_out":"T","fmax":310,"max_ratio":1.5,"fuel_model":"none",
   "allow_bypass":true,"power_max":7}],
 "gas_turbine_array":[{"uid":1,"name":"G_T_gas","generator":"G_T",
   "gas_node":"T","heat_rate":0.2}],
 "lng_terminal_array":[{"uid":1,"name":"lng1","emin":10,"emax":1000,
   "ecost":0.6,"eini":100,"delivery":500}]})";

/// Multi-carrier storage / conversion + battery + allowance pool.
constexpr std::string_view kGoldenMultiCarrierJson = R"({
 "name":"ampl_golden_multi_carrier",
 "bus_array":[{"uid":1,"name":"b1"}],
 "generator_array":[{"uid":1,"name":"g1","bus":"b1","pmax":100,
   "gcost":10,"capacity":100}],
 "demand_array":[{"uid":1,"name":"d1","bus":"b1","capacity":20}],
 "battery_array":[{"uid":1,"name":"bat1","bus":"b1","emin":4,"emax":404,
   "ecost":0.4,"eini":4,"input_efficiency":0.93,"output_efficiency":0.94,
   "capacity":404}],
 "thermal_node_array":[{"uid":31,"name":"th_node"}],
 "thermal_storage_array":[{"uid":1,"name":"tes","thermal_node":31,
   "input_efficiency":0.91,"output_efficiency":0.92,"emin":1,"emax":101,
   "ecost":0.1,"eini":1,"capacity":101}],
 "hydrogen_node_array":[{"uid":11,"name":"h2_node"}],
 "hydrogen_storage_array":[{"uid":1,"name":"h2s","hydrogen_node":11,
   "input_efficiency":0.81,"output_efficiency":0.82,"emin":2,"emax":202,
   "ecost":0.2,"eini":2,"capacity":202}],
 "hydrogen_demand_array":[{"uid":1,"name":"h2d","hydrogen_node":11,
   "lmax":12,"lmin":2,"price":6,"min_energy":1,"max_energy":9}],
 "ammonia_node_array":[{"uid":22,"name":"nh3_node"}],
 "ammonia_storage_array":[{"uid":1,"name":"nh3s","ammonia_node":22,
   "input_efficiency":0.71,"output_efficiency":0.72,"emin":3,"emax":303,
   "ecost":0.3,"eini":3,"capacity":303}],
 "carrier_converter_array":[{"uid":1,"name":"electrolyser",
   "from_carrier":"electric","to_carrier":"hydrogen","from_node":1,
   "to_node":11,"efficiency":0.65,"ocost":4,"capacity":50}],
 "emission_array":[{"uid":1,"name":"co2"}],
 "allowance_pool_array":[{"uid":1,"name":"pool","emission":1,"emin":5,
   "emax":505,"ecost":0.05,"eini":50,"delivery":60}]})";

[[nodiscard]] Simulation golden_one_block_simulation()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

[[nodiscard]] std::string golden_format_value(const std::optional<double>& v)
{
  return v ? std::format("{:.17g}", *v) : std::string {"std::nullopt"};
}

}  // namespace

TEST_CASE(  // NOLINT
    "AMPL param registry — registered set equals the golden set")
{
  const PlanningOptionsLP options;
  const auto sim = golden_one_block_simulation();
  SimulationLP simulation_lp(sim, options);
  simulation_lp.set_need_ampl_variables(/*v=*/true);
  const auto system = daw::json::from_json<System>(kGoldenMultiCarrierJson);
  const SystemLP system_lp(system, simulation_lp);

  std::vector<std::string> registered;
  for (const auto& [key, fn] : simulation_lp.ampl_params()) {
    CHECK(fn != nullptr);
    registered.push_back(std::format("{}.{}", key.class_name, key.attribute));
  }
  std::vector<std::string> golden;
  golden.reserve(kGolden.size());
  for (const auto& g : kGolden) {
    golden.push_back(std::format("{}.{}", g.class_name, g.attribute));
  }
  std::ranges::sort(registered);
  std::ranges::sort(golden);

  std::vector<std::string> missing;
  std::ranges::set_difference(golden, registered, std::back_inserter(missing));
  std::vector<std::string> extra;
  std::ranges::set_difference(registered, golden, std::back_inserter(extra));
  for (const auto& m : missing) {
    FAIL_CHECK("golden (class, attribute) not registered: " << m);
  }
  for (const auto& e : extra) {
    FAIL_CHECK("registered (class, attribute) missing from kGolden: " << e);
  }
  CHECK(std::ranges::adjacent_find(golden) == golden.end());
  CHECK(registered.size() == kGolden.size());
}

TEST_CASE(  // NOLINT
    "AMPL param registry — every registered resolver returns its golden "
    "value on a fixture element")
{
  const PlanningOptionsLP options;
  const auto sim = golden_one_block_simulation();
  const auto s1 = make_uid<Stage>(1);
  const auto b1 = make_uid<Block>(1);

  // One SimulationLP per fixture: the AMPL registries live there.
  std::vector<std::string> resolved;
  for (const auto json :
       {kGoldenElectricHydroJson, kGoldenGasJson, kGoldenMultiCarrierJson})
  {
    SimulationLP simulation_lp(sim, options);
    simulation_lp.set_need_ampl_variables(/*v=*/true);
    const auto system = daw::json::from_json<System>(json);
    const SystemLP system_lp(system, simulation_lp);
    const auto& sc = system_lp.system_context();

    for (const auto& [key, fn] : simulation_lp.ampl_params()) {
      const auto name = std::format("{}.{}", key.class_name, key.attribute);
      if (std::ranges::contains(resolved, name)) {
        continue;
      }
      // Every fixture element of a registered class has uid 1; a class
      // absent from this fixture is resolved by another one.
      const auto iter = sc.find_ampl_iter(key.class_name);
      bool present = false;
      if (iter != nullptr) {
        iter(
            sc,
            &present,
            +[](void* state, Uid uid)
            {
              if (uid == Uid {1}) {
                *static_cast<bool*>(state) = true;
              }
            });
      }
      if (!present) {
        continue;
      }
      CAPTURE(name);
      const auto value = fn(sc, Uid {1}, s1, b1);
      resolved.push_back(name);

      const GoldenParam* golden = nullptr;
      for (const auto& g : kGolden) {
        if (g.class_name == key.class_name && g.attribute == key.attribute) {
          golden = &g;
        }
      }
      if (golden == nullptr) {
        FAIL_CHECK("no golden row; dumped: golden_row(\""
                   << key.class_name << "\", \"" << key.attribute << "\", "
                   << golden_format_value(value) << "),");
        continue;
      }
      CHECK(golden->value.has_value() == value.has_value());
      if (golden->value && value) {
        CHECK(*value == doctest::Approx(*golden->value).epsilon(1e-12));
      }
    }
  }

  // Every golden (class, attribute) was resolved on some fixture.
  for (const auto& g : kGolden) {
    const auto name = std::format("{}.{}", g.class_name, g.attribute);
    CAPTURE(name);
    CHECK(std::ranges::contains(resolved, name));
  }
}
