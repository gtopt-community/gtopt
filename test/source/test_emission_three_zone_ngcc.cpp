// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Emission-model test: three natural-gas combined-cycle (NGCC) plants in
// three New England zones (MA / CT / ME) plus one solar and one wind
// plant, aggregated onto a SINGLE gtopt bus with one global CO2 cap.
// It exercises the fuel-emission expand pass and a binding CO2 cap.
//
// This is an independent test written for gtopt.  It uses only a few
// public, factual technology parameters (below); no third-party code,
// data file or table is copied.  The parameter values coincide with
// those of the "1_three_zones" example documented by the GenX project
// (https://github.com/GenXProject/GenX), which was used only as a
// cross-check that they form a realistic, widely used set.
//
// ## Parameters and their public sources
//
//   * CO2 content of natural gas: 53.06 kg CO2/MMBtu = 0.05306 t/MMBtu,
//     the US EPA default emission factor (40 CFR Part 98, Subpart C,
//     Table C-1; EPA GHG Emission Factors Hub).
//   * Heat rates: 7.12 and 7.43 MMBtu/MWh for the CT and MA combined
//     cycles and 12.62 MMBtu/MWh for the older ME unit -- representative
//     values: the US EIA Electric Power Annual (Table 8.1) reports average
//     operating heat rates of roughly 7.5-7.7 MMBtu/MWh for natural-gas
//     combined cycles and above 10 MMBtu/MWh for natural-gas steam and
//     combustion-turbine units.
//   * Variable O&M: 3.55 / 3.57 / 4.50 $/MWh for the NGCCs and
//     0.10 $/MWh for wind, of the order of the VOM values in the NREL
//     Annual Technology Baseline and the EIA AEO cost and performance
//     assumptions.
//   * Delivered gas prices: 5.28 $/MMBtu (MA) and 5.45 $/MMBtu (CT, ME),
//     of the order of EIA's New England natural-gas prices for electric
//     power.
//
// Capacities, demand, the CO2 cap and the single-bus topology are
// chosen for this test (see "Expected dispatch").
//
// ## Unit conversion (1 MMBtu = 1.05506 GJ)
//
//   heat_rate (GJ/MWh)         = heat_rate_MMBtu x 1.05506
//   price ($/GJ)               = price_MMBtu / 1.05506
//   combustion (tCO2/GJ)       = CO2_content_MMBtu / 1.05506
//
// All three NGCCs share the same fuel CO2 content, so the per-MWh CO2
// rate (`heat_rate * combustion`) is set by the heat rate alone:
//
//   MA_NGCC: heat rate 7.83909 GJ/MWh, price 5.00351 $/GJ,
//            SRMC 42.781 $/MWh, CO2 0.39423 t/MWh
//   CT_NGCC: heat rate 7.51203 GJ/MWh, price 5.16464 $/GJ,
//            SRMC 42.374 $/MWh, CO2 0.37779 t/MWh
//   ME_NGCC: heat rate 13.31486 GJ/MWh, price 5.16464 $/GJ,
//            SRMC 73.279 $/MWh, CO2 0.66962 t/MWh
//
// CT_NGCC is the cheapest thermal; ME_NGCC the dirtiest and most
// expensive.  MA_NGCC sits between them on both axes.
//
// ## Expected dispatch
//
// Single bus, single 1-hour block, demand 300 MW.  Capacities chosen
// so the residual after renewables exceeds the cheapest thermal's MW:
//
//   solar 80 MW + wind 80 MW          (must-take, gcost 0)
//   CT_NGCC 100 MW  (SRMC 42.374, CO2 0.37779)
//   MA_NGCC  50 MW  (SRMC 42.781, CO2 0.39423)
//   ME_NGCC 100 MW  (SRMC 73.279, CO2 0.66962)
//
// Residual thermal demand = 300 − 160 = 140 MW.
//
// ### Uncapped baseline
//
// Merit order: CT (full 100) + MA (40 to fill).  ME stays off (most
// expensive).
//   CO2  = 100·0.37779 + 40·0.39423 = 53.548 tCO2
//   Cost = 100·42.374 + 40·42.781   = 5948.64 $
//
// ### Binding cap (45 tCO2 — below the uncapped 53.548)
//
// With `demand_fail_cost = 1000 $/MWh` the LP can buy unserved demand
// at the penalty.  Let CT, MA be the only thermal variables (ME stays
// off — confirmed by SRMC).
//
//   minimize 42.374 CT + 42.781 MA + 1000 (140 − CT − MA)
//   s.t.     0.37779 CT + 0.39423 MA ≤ 45
//            0 ≤ CT ≤ 100, 0 ≤ MA ≤ 50
//
// Simplifying the objective: 140000 − 957.626 CT − 957.219 MA  ⇒
// maximise 957.626 CT + 957.219 MA.  CT's coefficient is larger so
// the LP first pushes CT to its upper bound 100; the cap then leaves
//   0.39423 MA ≤ 45 − 37.779 = 7.221  ⇒  MA = 18.31407.
// Unserved = 140 − 100 − 18.31407 = 21.68593 MW.
//
// Final dispatch:
//   CT = 100,  MA = 18.31407,  ME = 0,  unserved = 21.68593,
//   CO2 = 45 (binding).

#include <algorithm>

#include <doctest/doctest.h>
#include <gtopt/emission.hpp>
#include <gtopt/emission_source.hpp>
#include <gtopt/emission_zone.hpp>
#include <gtopt/fuel.hpp>
#include <gtopt/generator.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

// Wrap the file body in a uniquely-named namespace so the inner
// anonymous namespace doesn't collide with the other emission test
// files when CMake batches them into a unity TU.
namespace test_emission_three_zone_ngcc
{
namespace
{

// 1 MMBtu = 1.05506 GJ — IEEE / IAPWS conversion constant.
constexpr Real kMMBtuPerGJ = 1.05506;

// Public technology parameters of one NGCC plant (see file header).
struct NgccPlant
{
  Real heat_rate_mmbtu;  // MMBtu/MWh
  Real var_om;  // $/MWh
  Real fuel_price_mmbtu;  // $/MMBtu
  Real fuel_co2_mmbtu;  // tCO2/MMBtu (EPA natural-gas default)
};

constexpr NgccPlant kMA_NGCC = {
    .heat_rate_mmbtu = 7.43,
    .var_om = 3.55,
    .fuel_price_mmbtu = 5.28,
    .fuel_co2_mmbtu = 0.05306,
};
constexpr NgccPlant kCT_NGCC = {
    .heat_rate_mmbtu = 7.12,
    .var_om = 3.57,
    .fuel_price_mmbtu = 5.45,
    .fuel_co2_mmbtu = 0.05306,
};
constexpr NgccPlant kME_NGCC = {
    .heat_rate_mmbtu = 12.62,
    .var_om = 4.50,
    .fuel_price_mmbtu = 5.45,
    .fuel_co2_mmbtu = 0.05306,
};

// Derived gtopt-side quantities (see header table).
[[nodiscard]] constexpr Real heat_rate_gj(const NgccPlant& t) noexcept
{
  return t.heat_rate_mmbtu * kMMBtuPerGJ;
}
[[nodiscard]] constexpr Real price_per_gj(const NgccPlant& t) noexcept
{
  return t.fuel_price_mmbtu / kMMBtuPerGJ;
}
[[nodiscard]] constexpr Real combustion_per_gj(const NgccPlant& t) noexcept
{
  return t.fuel_co2_mmbtu / kMMBtuPerGJ;
}
[[nodiscard]] constexpr Real srmc(const NgccPlant& t) noexcept
{
  // SRMC = price_per_GJ × heat_rate_GJ + var_OM
  //      = (fuel_price_MMBtu / 1.05506) × (heat_rate_MMBtu × 1.05506) + VOM
  //      = fuel_price_MMBtu × heat_rate_MMBtu + VOM  — kMMBtuPerGJ cancels.
  return (t.fuel_price_mmbtu * t.heat_rate_mmbtu) + t.var_om;
}
[[nodiscard]] constexpr Real co2_rate(const NgccPlant& t) noexcept
{
  // tCO2/MWh = heat_rate_GJ × combustion_per_GJ
  //         = heat_rate_MMBtu × CO2_content_MMBtu  — kMMBtuPerGJ cancels.
  return t.heat_rate_mmbtu * t.fuel_co2_mmbtu;
}

// Capacities (MW) — see header rationale.
constexpr Real kCapMA = 50.0;
constexpr Real kCapCT = 100.0;
constexpr Real kCapME = 100.0;
constexpr Real kCapSolar = 80.0;
constexpr Real kCapWind = 80.0;

// Demand (MW) and block duration (h).
constexpr Real kDemand = 300.0;
constexpr Real kDur = 1.0;

constexpr Real kDemandFailCost = 1000.0;

// Build the test system.  `co2_cap = std::nullopt` ⇒ uncapped.
[[nodiscard]] System make_test_system(std::optional<Real> co2_cap)
{
  System sys;
  sys.name = "ThreeZoneNgcc";
  sys.bus_array = {{
      .uid = Uid {1},
      .name = "bus",
  }};
  sys.demand_array = {
      {
          .uid = Uid {1},
          .name = "demand",
          .bus = Uid {1},
          .capacity = kDemand,
      },
  };

  // Single CO2 pollutant + single global zone covering all three
  // aggregated zones.
  sys.emission_array = {
      {
          .uid = Uid {1},
          .name = "co2",
      },
  };

  EmissionZone zone {
      .uid = Uid {1},
      .name = "global_co2",
      .emissions =
          {
              {
                  .emission = Uid {1},
                  .weight = 1.0,
              },
          },
  };
  if (co2_cap.has_value()) {
    zone.cap = OptTRealFieldSched {*co2_cap};
  }
  sys.emission_zone_array = {
      zone,
  };

  // One fuel per generator (per-zone delivered gas price).  All three
  // fuels share the same combustion factor -- the EPA natural-gas
  // default CO2 content.
  sys.fuel_array = {
      {
          .uid = Uid {1},
          .name = "MA_NG",
          .price = price_per_gj(kMA_NGCC),
          .emission_factors =
              {
                  {
                      .emission = SingleId {Uid {1}},
                      .combustion = combustion_per_gj(kMA_NGCC),
                  },
              },
      },
      {
          .uid = Uid {2},
          .name = "CT_NG",
          .price = price_per_gj(kCT_NGCC),
          .emission_factors =
              {
                  {
                      .emission = SingleId {Uid {1}},
                      .combustion = combustion_per_gj(kCT_NGCC),
                  },
              },
      },
      {
          .uid = Uid {3},
          .name = "ME_NG",
          .price = price_per_gj(kME_NGCC),
          .emission_factors =
              {
                  {
                      .emission = SingleId {Uid {1}},
                      .combustion = combustion_per_gj(kME_NGCC),
                  },
              },
      },
  };

  // Three NGCC thermals + one solar + one wind.  gcost = variable O&M
  // ($/MWh); the fuel cost is added by the fuel expand pass.
  sys.generator_array = {
      {
          .uid = Uid {1},
          .name = "MA_NGCC",
          .bus = Uid {1},
          .gcost = kMA_NGCC.var_om,
          .fuel = SingleId {Uid {1}},
          .heat_rate = heat_rate_gj(kMA_NGCC),
          .capacity = kCapMA,
      },
      {
          .uid = Uid {2},
          .name = "CT_NGCC",
          .bus = Uid {1},
          .gcost = kCT_NGCC.var_om,
          .fuel = SingleId {Uid {2}},
          .heat_rate = heat_rate_gj(kCT_NGCC),
          .capacity = kCapCT,
      },
      {
          .uid = Uid {3},
          .name = "ME_NGCC",
          .bus = Uid {1},
          .gcost = kME_NGCC.var_om,
          .fuel = SingleId {Uid {3}},
          .heat_rate = heat_rate_gj(kME_NGCC),
          .capacity = kCapME,
      },
      {
          .uid = Uid {4},
          .name = "MA_solar_pv",
          .bus = Uid {1},
          .gcost = 0.0,
          .capacity = kCapSolar,
      },
      {
          .uid = Uid {5},
          .name = "CT_onshore_wind",
          .bus = Uid {1},
          .gcost = 0.10,
          .capacity = kCapWind,
      },
  };
  return sys;
}

[[nodiscard]] Simulation make_one_block_simulation()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = kDur,
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
                  .probability_factor = 1.0,
              },
          },
  };
}

[[nodiscard]] PlanningOptions make_options()
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = kDemandFailCost;
  popts.model_options.scale_objective = 1.0;
  return popts;
}

// Find a generator's MW dispatch in the (only) cell.
[[nodiscard]] std::optional<Real> generation_of(SystemLP& system_lp,
                                                std::string_view gen_name)
{
  const auto& gens = system_lp.elements<GeneratorLP>();
  const auto it = std::ranges::find_if(
      gens, [&](const auto& g) { return g.generator().name == gen_name; });
  if (it == gens.end()) {
    return std::nullopt;
  }
  const auto& s = system_lp.scene().scenarios()[0];
  const auto& t = system_lp.phase().stages()[0];
  const auto& gen_cols = it->generation_cols_at(s, t);
  const auto buid = t.blocks()[0].uid();
  const auto col_it = gen_cols.find(buid);
  if (col_it == gen_cols.end()) {
    return std::nullopt;
  }
  return system_lp.linear_interface().get_col_sol()[col_it->second];
}

}  // namespace

// ── Uncapped baseline ──────────────────────────────────────────────────

TEST_CASE(
    "Three-zone NGCC emission test — uncapped baseline: renewables + cheapest "
    "NGCC (CT) saturate, MA fills the remainder, ME stays off")  // NOLINT
{
  auto sys = make_test_system(/*co2_cap=*/std::nullopt);
  sys.fold_legacy_fuel_emission_factors();
  sys.expand_fuel_emission_sources();

  const auto simulation = make_one_block_simulation();
  const PlanningOptionsLP options(make_options());
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());

  // Renewables go first (lowest SRMC, must-take by capacity).
  CHECK(generation_of(system_lp, "MA_solar_pv").value_or(-1.0)
        == doctest::Approx(kCapSolar).epsilon(1e-5));
  CHECK(generation_of(system_lp, "CT_onshore_wind").value_or(-1.0)
        == doctest::Approx(kCapWind).epsilon(1e-5));

  // Residual 140 MW: CT (cheapest thermal) full + MA fills the gap.
  const Real residual = kDemand - kCapSolar - kCapWind;  // 140 MW
  const Real ct_expected = kCapCT;  // 100 MW
  const Real ma_expected = residual - kCapCT;  // 40 MW

  CHECK(generation_of(system_lp, "CT_NGCC").value_or(-1.0)
        == doctest::Approx(ct_expected).epsilon(1e-5));
  CHECK(generation_of(system_lp, "MA_NGCC").value_or(-1.0)
        == doctest::Approx(ma_expected).epsilon(1e-5));
  CHECK(generation_of(system_lp, "ME_NGCC").value_or(-1.0)
        == doctest::Approx(0.0).epsilon(1e-5));

  // Objective: wind VOM (0.10 $/MWh) + CT + MA dispatch costs.
  const Real obj_expected = (kCapWind * 0.10)  // 8.0
      + (ct_expected * srmc(kCT_NGCC))  // 100 × 42.374  = 4237.40
      + (ma_expected * srmc(kMA_NGCC));  //  40 × 42.781  = 1711.24
  CHECK(lp.get_obj_value() == doctest::Approx(obj_expected).epsilon(1e-5));
}

// ── Binding CO2 cap ────────────────────────────────────────────────────

TEST_CASE(
    "Three-zone NGCC emission test — binding CO2 cap forces unserved demand: "
    "CT stays full, MA throttled to honor the cap")  // NOLINT
{
  // Uncapped CO2 = 53.548 tCO2; pick 45 tCO2 to bind.
  constexpr Real kCap = 45.0;
  auto sys = make_test_system(kCap);
  sys.fold_legacy_fuel_emission_factors();
  sys.expand_fuel_emission_sources();

  const auto simulation = make_one_block_simulation();
  const PlanningOptionsLP options(make_options());
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());

  // Renewables unchanged.
  CHECK(generation_of(system_lp, "MA_solar_pv").value_or(-1.0)
        == doctest::Approx(kCapSolar).epsilon(1e-5));
  CHECK(generation_of(system_lp, "CT_onshore_wind").value_or(-1.0)
        == doctest::Approx(kCapWind).epsilon(1e-5));

  // From the LP-algebra in the file header.  See the derivation in the
  // comment block above the includes.
  const Real ct_expected = kCapCT;  // 100 MW (cheapest, hit upper bound)
  const Real ma_expected =
      (kCap - (co2_rate(kCT_NGCC) * ct_expected)) / co2_rate(kMA_NGCC);
  // ma_expected = (45 − 37.779) / 0.39423 ≈ 18.31407 MW

  CHECK(generation_of(system_lp, "CT_NGCC").value_or(-1.0)
        == doctest::Approx(ct_expected).epsilon(1e-5));
  CHECK(generation_of(system_lp, "MA_NGCC").value_or(-1.0)
        == doctest::Approx(ma_expected).epsilon(1e-5));
  CHECK(generation_of(system_lp, "ME_NGCC").value_or(-1.0)
        == doctest::Approx(0.0).epsilon(1e-5));

  // Cap is binding: total CO2 = cap.
  const Real total_co2 =
      (ct_expected * co2_rate(kCT_NGCC)) + (ma_expected * co2_rate(kMA_NGCC));
  CHECK(total_co2 == doctest::Approx(kCap).epsilon(1e-5));
}

}  // namespace test_emission_three_zone_ngcc
