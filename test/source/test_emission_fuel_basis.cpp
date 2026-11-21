// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Fuel-basis emission accounting (issue #545).
//
// `System::expand_fuel_emission_sources` used to skip generators whose
// heat rate is not a single scalar — piecewise `heat_rate_segments` and
// per-(stage, block) `heat_rate` schedules — so their fuel-derived
// emissions were never counted (PLEXOS Load Point / Heat Rate Incr
// units and per-block import bids in the SEM case).  They now get an
// `EmissionSource.fuel_rate` [t / fuel-unit] that EmissionSourceLP
// applies to `GeneratorLP::fuel_burn_legs`, i.e. the exact fuel burn:
//   piecewise:  h₁ · p + Σₖ (hₖ₊₁ − hₖ) · s_k   (heat-rate slack cols)
//   per-block:  hr_b · p
//
// Every case below is hand-computed: fuel price 1 $/GJ, CO₂ factor
// 0.05 t/GJ, heat_content unset (heat rate already in GJ/MWh), one
// 80 MW demand, 1 h blocks, scale_objective = 1.

#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] auto efb_simulation(Size n_blocks) -> Simulation
{
  Simulation sim;
  for (Size b = 1; b <= n_blocks; ++b) {
    sim.block_array.push_back({
        .uid = Uid {static_cast<int>(b)},
        .duration = 1.0,
    });
  }
  sim.stage_array = {
      {
          .uid = Uid {1},
          .first_block = 0,
          .count_block = n_blocks,
      },
  };
  sim.scenario_array = {
      {
          .uid = Uid {1},
      },
  };
  return sim;
}

/// One bus, 80 MW demand, gas generator `thermal` (uid 1) plus an
/// emission-free 100 $/MWh backup (uid 2), one CO₂ zone.
[[nodiscard]] auto efb_system(Generator thermal, EmissionZone zone) -> System
{
  System sys {
      .name = "emission_fuel_basis",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .capacity = 80.0,
              },
          },
      .generator_array =
          {
              std::move(thermal),
              {
                  .uid = Uid {2},
                  .name = "clean",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 100.0,
              },
          },
      .fuel_array =
          {
              {
                  .uid = Uid {1},
                  .name = "gas",
                  .price = 1.0,
                  .emission_factors =
                      {
                          {
                              .emission = SingleId {Uid {1}},
                              .combustion = 0.05,
                          },
                      },
              },
          },
      .emission_array =
          {
              {
                  .uid = Uid {1},
                  .name = "co2",
              },
          },
      .emission_zone_array = {std::move(zone)},
  };
  sys.expand_fuel_emission_sources();
  return sys;
}

[[nodiscard]] auto efb_piecewise() -> Generator
{
  return Generator {
      .uid = Uid {1},
      .name = "pw_ccgt",
      .bus = Uid {1},
      .fuel = SingleId {Uid {1}},
      .pmax_segments = {50.0, 100.0},
      .heat_rate_segments = {7.0, 9.0},
      .capacity = 100.0,
  };
}

[[nodiscard]] auto efb_zone(OptTRealFieldSched cap, OptTRealFieldSched price)
    -> EmissionZone
{
  return EmissionZone {
      .uid = Uid {1},
      .name = "z_co2",
      .emissions =
          {
              {
                  .emission = SingleId {Uid {1}},
                  .weight = 1.0,
              },
          },
      .cap = std::move(cap),
      .price = std::move(price),
  };
}

[[nodiscard]] auto efb_solve(const System& sys, Size n_blocks) -> double
{
  const auto simulation = efb_simulation(n_blocks);
  PlanningOptions opts;
  opts.model_options.scale_objective = 1.0;
  opts.model_options.demand_fail_cost = 10000.0;
  const PlanningOptionsLP options {opts};
  SimulationLP simulation_lp {simulation, options};
  SystemLP system_lp {sys, simulation_lp};
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(lp.is_optimal());
  return lp.get_obj_value_raw();
}

}  // namespace

TEST_CASE("Piecewise heat rate — fuel-derived source is synthesized")  // NOLINT
{
  const auto sys = efb_system(efb_piecewise(), efb_zone({}, {}));
  REQUIRE(sys.emission_source_array.size() == 1);
  const auto& src = sys.emission_source_array.front();
  CHECK_FALSE(src.rate.has_value());
  CHECK(std::get<Real>(src.fuel_rate.value_or(0.0)) == doctest::Approx(0.05));
}

TEST_CASE("Piecewise heat rate — emission cap binds on the PWL burn")  // NOLINT
{
  // Cap 25 t ⇒ burn ≤ 500 GJ.  Above 50 MW: burn(p) = 9p − 100, so the
  // CCGT stops at p = 600/9 = 66.667 MW (burn exactly 500 GJ, cost 500 $)
  // and the clean unit covers 13.333 MW × 100 = 1333.33 $.
  // Total 1833.33.  (A t/MWh rate at h₁ = 7 would allow 71.43 MW, one
  // at h₂ = 9 only 55.6 MW; skipping the unit — the old behaviour —
  // leaves the cap slack and gives 620 $.)
  const auto sys = efb_system(efb_piecewise(), efb_zone(25.0, {}));
  CHECK(efb_solve(sys, 1) == doctest::Approx(500.0 + (40.0 / 3.0) * 100.0));
}

TEST_CASE("Piecewise heat rate — carbon price on every burn leg")  // NOLINT
{
  // Price 10 $/t ⇒ segment costs 7 + 0.35 × 10 = 10.5 and
  // 9 + 0.45 × 10 = 13.5 $/MWh, both below the 100 $/MWh backup, so the
  // CCGT serves 80 MW: burn 7 × 80 + 2 × 30 = 620 GJ → fuel 620 $ plus
  // 31 t × 10 $/t = 310 $.  Total 930.
  const auto sys = efb_system(efb_piecewise(), efb_zone({}, 10.0));
  CHECK(efb_solve(sys, 1) == doctest::Approx(930.0));
}

TEST_CASE("Per-block heat rate — fuel-basis carbon price")  // NOLINT
{
  // heat_rate 8 / 10 GJ/MWh in blocks 1 / 2, price 10 $/t ⇒
  //   b1: fuel 8 × 80 = 640 $, 32 t → 320 $
  //   b2: fuel 10 × 80 = 800 $, 40 t → 400 $
  // Total 2160.
  Generator gen {
      .uid = Uid {1},
      .name = "imports",
      .bus = Uid {1},
      .fuel = SingleId {Uid {1}},
      .heat_rate =
          OptTBRealFieldSched {
              std::vector<std::vector<Real>> {
                  {8.0, 10.0},
              },
          },
      .capacity = 100.0,
  };
  const auto sys = efb_system(std::move(gen), efb_zone({}, 10.0));
  REQUIRE(sys.emission_source_array.size() == 1);
  CHECK(efb_solve(sys, 2) == doctest::Approx(2160.0));
}

TEST_CASE("Scalar heat rate — t/MWh rate path unchanged")  // NOLINT
{
  // Scalar hr 8 keeps the generation-basis rate 8 × 0.05 = 0.4 t/MWh.
  // Price 10 $/t: 80 × (8 + 4) = 960.
  Generator gen {
      .uid = Uid {1},
      .name = "ocgt",
      .bus = Uid {1},
      .fuel = SingleId {Uid {1}},
      .heat_rate = 8.0,
      .capacity = 100.0,
  };
  const auto sys = efb_system(std::move(gen), efb_zone({}, 10.0));
  REQUIRE(sys.emission_source_array.size() == 1);
  const auto& src = sys.emission_source_array.front();
  CHECK(std::get<Real>(src.rate.value_or(0.0)) == doctest::Approx(0.4));
  CHECK_FALSE(src.fuel_rate.has_value());
  CHECK(efb_solve(sys, 1) == doctest::Approx(960.0));
}
