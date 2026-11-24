// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Multi-carrier chains solved end to end with hand-computed optima
// (issue #546).
//
// HydrogenStorage / ThermalStorage used to default
// `use_state_variable = false`, which makes StorageLP add the close row
// `efin == eini`.  Any `efin` target above `eini` (e.g. eini 0, efin 50)
// then made the LP infeasible.  They now default to `true`, like
// AmmoniaStorage and Reservoir.  None of the cases below sets
// `use_state_variable`, so they pin the defaults.
//
// Common data: one bus, a 10 $/MWh generator (200 MW), two 1 h blocks,
// scale_objective = 1.  Helpers are prefixed `mcc_` for unity builds.

#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/carrier_converter_lp.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] auto mcc_simulation() -> Simulation
{
  return Simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
              },
              {
                  .uid = Uid {2},
                  .duration = 1.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 2,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {1},
              },
          },
  };
}

[[nodiscard]] auto mcc_base_system() -> System
{
  return System {
      .name = "multi_carrier_chain",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 10.0,
                  .capacity = 200.0,
              },
          },
  };
}

struct McCResult
{
  double obj {};
  double converter_input {};  ///< Σ_b input of converter uid 1 [MWh].
};

[[nodiscard]] auto mcc_solve(const System& sys, const Simulation& sim)
    -> McCResult
{
  PlanningOptions opts;
  opts.model_options.scale_objective = 1.0;
  opts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options {opts};
  SimulationLP simulation_lp {sim, options};
  SystemLP system_lp {sys, simulation_lp};
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(lp.is_optimal());

  McCResult out {
      .obj = lp.get_obj_value_raw(),
  };
  const auto sol = lp.get_col_sol();
  const auto& scenario = system_lp.scene().scenarios().front();
  const auto& stage = system_lp.phase().stages().front();
  const auto& ccs = system_lp.elements<CarrierConverterLP>();
  if (!ccs.empty()) {
    for (const auto& [buid, col] : ccs.front().input_cols_at(scenario, stage)) {
      out.converter_input += sol[col];
    }
  }
  return out;
}

/// Mirrors the issue's h2d.json: electrolyser (η 0.5, 100 MW_e) →
/// HydrogenNode → HydrogenStorage (eini 0, efin 50) and a hard
/// HydrogenDemand of 30 MWh_LHV, all referenced by name.
constexpr std::string_view mcc_h2_chain_json = R"({
  "options": {
    "model_options": {"scale_objective": 1, "demand_fail_cost": 1000}
  },
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2}],
    "scenario_array": [{"uid": 1}]
  },
  "system": {
    "name": "h2_chain",
    "bus_array": [{"uid": 1, "name": "b1"}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": "b1", "gcost": 10, "capacity": 200}
    ],
    "hydrogen_node_array": [{"uid": 11, "name": "h2n"}],
    "hydrogen_storage_array": [
      {"uid": 1, "name": "st", "hydrogen_node": "h2n", "emin": 0,
       "emax": 500, "eini": 0, "efin": 50, "capacity": 500}
    ],
    "hydrogen_demand_array": [
      {"uid": 1, "name": "offtake", "hydrogen_node": "h2n",
       "min_energy": 30, "max_energy": 30}
    ],
    "carrier_converter_array": [
      {"uid": 1, "name": "ely", "from_carrier": "electric",
       "to_carrier": "hydrogen", "from_node": "b1", "to_node": "h2n",
       "efficiency": 0.5, "capacity": 100}
    ]
  }
})";

}  // namespace

TEST_CASE(  // NOLINT
    "Electrolyser → H₂ node → H₂ storage + H₂ demand (JSON, #546)")
{
  // H₂ needed: 50 (storage efin) + 30 (demand) = 80 MWh_LHV
  //   → 80 / 0.5 = 160 MWh_e ≤ 2 h × 100 MW_e.  Cost 160 × 10 = 1600.
  const auto planning = daw::json::from_json<Planning>(mcc_h2_chain_json);
  REQUIRE(planning.system.hydrogen_storage_array.size() == 1);
  CHECK_FALSE(
      planning.system.hydrogen_storage_array.front().use_state_variable);
  const auto res = mcc_solve(planning.system, planning.simulation);
  CHECK(res.converter_input == doctest::Approx(160.0));
  CHECK(res.obj == doctest::Approx(1600.0));
}

TEST_CASE("H₂ storage efin above eini needs no state-variable flag")  // NOLINT
{
  // Storage only (no demand): 50 MWh_LHV → 100 MWh_e → 1000 $.
  // `use_state_variable = false` explicitly restores the closed-cycle
  // row efin == eini, which is infeasible with eini 0 / efin 50.
  auto sys = mcc_base_system();
  sys.hydrogen_node_array = {
      {
          {
              .uid = Uid {11},
              .name = "h2n",
          },
      },
  };
  sys.hydrogen_storage_array = {
      {
          .uid = Uid {1},
          .name = "st",
          .hydrogen_node = SingleId {Uid {11}},
          .emin = 0.0,
          .emax = 500.0,
          .eini = 0.0,
          .efin = 50.0,
          .capacity = 500.0,
      },
  };
  sys.carrier_converter_array = {
      {
          .uid = Uid {1},
          .name = "ely",
          .from_carrier = Carrier::Electric,
          .to_carrier = Carrier::Hydrogen,
          .from_node = SingleId {Uid {1}},
          .to_node = SingleId {Uid {11}},
          .efficiency = 0.5,
          .capacity = 100.0,
      },
  };
  const auto res = mcc_solve(sys, mcc_simulation());
  CHECK(res.converter_input == doctest::Approx(100.0));
  CHECK(res.obj == doctest::Approx(1000.0));
}

TEST_CASE("Electric boiler → thermal node → thermal storage")  // NOLINT
{
  // TES must end at 90 MWh_th from 0; boiler η 0.9 ⇒ 100 MWh_e → 1000 $.
  auto sys = mcc_base_system();
  sys.thermal_node_array = {
      {
          {
              .uid = Uid {21},
              .name = "heat",
          },
      },
  };
  sys.thermal_storage_array = {
      {
          .uid = Uid {1},
          .name = "tes",
          .thermal_node = SingleId {Uid {21}},
          .emin = 0.0,
          .emax = 300.0,
          .eini = 0.0,
          .efin = 90.0,
          .capacity = 300.0,
      },
  };
  sys.carrier_converter_array = {
      {
          .uid = Uid {1},
          .name = "boiler",
          .from_carrier = Carrier::Electric,
          .to_carrier = Carrier::Thermal,
          .from_node = SingleId {Uid {1}},
          .to_node = SingleId {Uid {21}},
          .efficiency = 0.9,
          .capacity = 100.0,
      },
  };
  const auto res = mcc_solve(sys, mcc_simulation());
  CHECK(res.converter_input == doctest::Approx(100.0));
  CHECK(res.obj == doctest::Approx(1000.0));
}

TEST_CASE("Electrolyser → Haber-Bosch → NH₃ tank")  // NOLINT
{
  // NH₃ tank must end at 40 MWh_LHV from 0.  Haber-Bosch η 0.8 needs
  // 50 MWh_LHV of H₂, the electrolyser (η 0.5) 100 MWh_e → 1000 $.
  auto sys = mcc_base_system();
  sys.hydrogen_node_array = {
      {
          {
              .uid = Uid {11},
              .name = "h2n",
          },
      },
  };
  sys.ammonia_node_array = {
      {
          {
              .uid = Uid {31},
              .name = "nh3n",
          },
      },
  };
  sys.ammonia_storage_array = {
      {
          .uid = Uid {1},
          .name = "tank",
          .ammonia_node = SingleId {Uid {31}},
          .emax = 400.0,
          .eini = 0.0,
          .efin = 40.0,
          .capacity = 400.0,
      },
  };
  sys.carrier_converter_array = {
      {
          .uid = Uid {1},
          .name = "ely",
          .from_carrier = Carrier::Electric,
          .to_carrier = Carrier::Hydrogen,
          .from_node = SingleId {Uid {1}},
          .to_node = SingleId {Uid {11}},
          .efficiency = 0.5,
          .capacity = 100.0,
      },
      {
          .uid = Uid {2},
          .name = "haber_bosch",
          .from_carrier = Carrier::Hydrogen,
          .to_carrier = Carrier::Ammonia,
          .from_node = SingleId {Uid {11}},
          .to_node = SingleId {Uid {31}},
          .efficiency = 0.8,
          .capacity = 100.0,
      },
  };
  const auto res = mcc_solve(sys, mcc_simulation());
  CHECK(res.converter_input == doctest::Approx(100.0));
  CHECK(res.obj == doctest::Approx(1000.0));
}
