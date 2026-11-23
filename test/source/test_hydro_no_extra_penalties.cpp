// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_hydro_no_extra_penalties.cpp
 * @brief     Hydro defaults add no penalty that can make water worth < 0
 *
 * A positive cost on the only way water can leave a full reservoir
 * (spillway / drain) makes storing one more m³ *costly*: the
 * storage-balance dual (the water value) becomes −spillway_cost while
 * the reservoir spills.  gtopt therefore ships every hydro cost as an
 * opt-in field (``spillway_cost``, ``drain_cost``, ``Waterway.fcost``,
 * ``efin_cost``, ``soft_emin_cost``, …) and the converters only emit
 * values that the source model publishes.
 *
 * These tests pin the mechanism on a one-block, one-bus case:
 *   - scarce water            → water value > 0 (reference sign);
 *   - spilling, free spillway → water value = 0 (never negative);
 *   - spilling, spillway_cost → water value has the opposite sign.
 */

#include <cmath>

#include <doctest/doctest.h>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct WaterCase
{
  double eini {0.0};  ///< [hm³]
  double inflow {0.0};  ///< natural inflow [m³/s]
  double demand {10.0};  ///< [MW]
  std::optional<double> spillway_cost {};  ///< unset ⇒ no drain column
  bool unset_fcr {false};  ///< leave flow_conversion_rate unset (JSON path)
};

/// Solve the case and return the raw storage-balance dual of block 1.
[[nodiscard]] double water_dual(const WaterCase& wc)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "hydro_gen",
          .bus = Uid {1},
          .gcost = 0.0,
          .capacity = 1000.0,
      },
      {
          .uid = Uid {2},
          .name = "thermal_gen",
          .bus = Uid {1},
          .gcost = 50.0,
          .capacity = 1000.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = wc.demand,
      },
  };
  const Array<Junction> junction_array = {
      {
          .uid = Uid {1},
          .name = "j_up",
      },
      {
          .uid = Uid {2},
          .name = "j_down",
          .drain = true,
      },
  };
  const Array<Waterway> waterway_array = {
      {
          .uid = Uid {1},
          .name = "ww1",
          .junction_a = Uid {1},
          .junction_b = Uid {2},
          .fmin = 0.0,
          .fmax = 10000.0,
      },
  };
  const Array<Flow> flow_array = {
      {
          .uid = Uid {1},
          .name = "inflow",
          .junction = Uid {1},
          .discharge = wc.inflow,
      },
  };
  // Small reservoir (emax = 0.1 hm³): a large inflow fills it and the
  // surplus must leave through the spillway (the turbine is capped by
  // the demand because the bus has no other sink).
  Array<Reservoir> reservoir_array = {
      {
          .uid = Uid {1},
          .name = "rsv1",
          .junction = Uid {1},
          .spillway_capacity = 100000.0,
          .spillway_cost = wc.spillway_cost,
          .capacity = 0.1,
          .emin = 0.0,
          .emax = 0.1,
          .eini = wc.eini,
      },
  };
  if (wc.unset_fcr) {
    // What a JSON reservoir without the key carries.
    reservoir_array.front().flow_conversion_rate = std::nullopt;
  }
  const Array<Turbine> turbine_array = {
      {
          .uid = Uid {1},
          .name = "tur1",
          .waterway = Uid {1},
          .generator = Uid {1},
          .production_factor = 1.0,
      },
  };
  const Simulation simulation = {
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
  const System system = {
      .name = "HydroNoExtraPenalties",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .junction_array = junction_array,
      .waterway_array = waterway_array,
      .flow_array = flow_array,
      .reservoir_array = reservoir_array,
      .turbine_array = turbine_array,
  };

  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options {opts};
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(system, simulation_lp);

  auto& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  const auto& rsv_lp = system_lp.elements<ReservoirLP>().front();
  // Unset and default resolve to the same hm³/(m³/s·h) rate.
  CHECK(rsv_lp.flow_conversion_rate()
        == doctest::Approx(Reservoir::default_flow_conversion_rate));
  const auto& erows = rsv_lp.energy_rows_at(simulation_lp.scenarios().front(),
                                            simulation_lp.stages().front());
  const auto it = erows.find(simulation_lp.blocks().front().uid());
  REQUIRE(it != erows.end());

  lp.ensure_duals();
  return lp.get_row_dual_raw()[it->second];
}

}  // namespace

TEST_CASE("Hydro defaults - spilling reservoir has zero water value")  // NOLINT
{
  // Reference sign: water is scarce, thermal sets the price → the
  // storage-balance dual is non-zero; its sign is "water is valuable".
  const double scarce = water_dual({
      .eini = 0.001,
      .inflow = 0.0,
  });
  REQUIRE(std::abs(scarce) > 1e-6);

  // Flood: 1000 m³/s ≈ 3.6 hm³ in one hour into a 0.1 hm³ reservoir
  // with a 10 MW load → the reservoir must spill.
  const WaterCase flood {
      .eini = 0.1,
      .inflow = 1000.0,
  };

  SUBCASE("free spillway (spillway_cost = 0) - water value is zero")
  {
    // `spillway_cost = 0` is what the converters emit to open the free
    // storage drain (an unset cost creates no drain column at all).
    auto wc = flood;
    wc.spillway_cost = 0.0;
    CHECK(water_dual(wc) == doctest::Approx(0.0).epsilon(1e-9));
  }

  SUBCASE("positive spillway_cost - water value turns negative")
  {
    // The mechanism the converters must not trigger with invented
    // costs: an extra m³ in a spilling reservoir costs `spillway_cost`.
    auto wc = flood;
    wc.spillway_cost = 5.0;
    const double dual = water_dual(wc);
    REQUIRE(std::abs(dual) > 1e-9);
    CHECK(std::signbit(dual) != std::signbit(scarce));
  }
}

TEST_CASE(
    "Reservoir - unset flow_conversion_rate resolves to 0.0036")  // NOLINT
{
  CHECK(Reservoir::default_flow_conversion_rate == doctest::Approx(0.0036));

  // Scarce water: the water value is set by the thermal unit through the
  // conversion rate, so an unset rate must give exactly the same LP as the
  // explicit default (it used to fall back to 3.6, 1000x larger).
  const WaterCase scarce {
      .eini = 0.001,
      .inflow = 0.0,
  };
  auto unset = scarce;
  unset.unset_fcr = true;
  CHECK(water_dual(unset) == doctest::Approx(water_dual(scarce)));
}
