// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Tests for ``HydrogenDemandLP`` — hydrogen offtake at a HydrogenNode:
//   * hard per-stage energy target met by a flexible electrolyser that
//     concentrates its consumption in the cheapest block;
//   * soft target (shortfall slack) when hydrogen is too expensive;
//   * price-responsive offtake (negative cost) bounded by lmax;
//   * JSON round trip and PAMPL references to ``hydrogen_demand`` and
//     ``carrier_converter`` columns from a UserConstraint.

#include <doctest/doctest.h>
#include <gtopt/carrier_converter_lp.hpp>
#include <gtopt/hydrogen_demand_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

Simulation make_two_block_simulation()
{
  return {
      .block_array =
          {
              {.uid = Uid {0}, .duration = 1.0},
              {.uid = Uid {1}, .duration = 1.0},
          },
      .stage_array =
          {
              {
                  .uid = Uid {0},
                  .first_block = 0,
                  .count_block = 2,
              },
          },
      .scenario_array =
          {
              {.uid = Uid {0}},
          },
  };
}

/// Bus b1 with a $10/MWh generator available 200 MW in block 0 and
/// 50 MW in block 1, plus a $50/MWh backup; electrolyser η = 0.5,
/// 200 MW_e, feeding HydrogenNode h2 (uid 11).
System make_h2_system()
{
  System sys;
  sys.name = "h2_demand";
  sys.bus_array = {
      {.uid = Uid {1}, .name = "b1"},
  };
  sys.generator_array = {
      {
          .uid = Uid {1},
          .name = "cheap",
          .bus = Uid {1},
          .pmin = 0.0,
          .pmax = std::vector<std::vector<double>> {{200.0, 50.0}},
          .gcost = 10.0,
          .capacity = 200.0,
      },
      {
          .uid = Uid {2},
          .name = "backup",
          .bus = Uid {1},
          .pmin = 0.0,
          .pmax = 500.0,
          .gcost = 50.0,
          .capacity = 500.0,
      },
  };
  sys.hydrogen_node_array = {
      {
          {
              .uid = Uid {11},
              .name = "h2",
          },
      },
  };
  sys.carrier_converter_array = {
      {
          .uid = Uid {1},
          .name = "electrolyser",
          .type = "electrolyser",
          .from_carrier = Carrier::Electric,
          .to_carrier = Carrier::Hydrogen,
          .from_node = SingleId {Uid {1}},
          .to_node = SingleId {Uid {11}},
          .efficiency = 0.5,
          .capacity = 200.0,
      },
  };
  return sys;
}

struct Solved
{
  double obj;
  std::vector<double> load;
  std::vector<double> input;
};

Solved solve(const System& sys)
{
  const auto simulation = make_two_block_simulation();
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  const auto& scenario = system_lp.scene().scenarios()[0];
  const auto& stage = system_lp.phase().stages()[0];
  const auto sol = li.get_col_sol();

  Solved out {
      .obj = li.get_obj_value(),
      .load = {},
      .input = {},
  };
  const auto& hd = system_lp.elements<HydrogenDemandLP>();
  REQUIRE(hd.size() == 1);
  for (const auto& [buid, col] : hd.front().load_cols_at(scenario, stage)) {
    out.load.push_back(sol[col]);
  }
  const auto& cc = system_lp.elements<CarrierConverterLP>();
  for (const auto& [buid, col] : cc.front().input_cols_at(scenario, stage)) {
    out.input.push_back(sol[col]);
  }
  return out;
}

}  // namespace

TEST_CASE("HydrogenDemand: hard stage energy target is met flexibly")  // NOLINT
{
  auto sys = make_h2_system();
  // 100 MWh_LHV over the stage = 200 MWh_e at η = 0.5.  The cheap
  // generator has 200 MW in block 0, so all production happens there.
  sys.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_offtake",
          .hydrogen_node = SingleId {Uid {11}},
          .min_energy = 100.0,
          .max_energy = 100.0,
      },
  };
  const auto s = solve(sys);
  REQUIRE(s.load.size() == 2);
  CHECK(s.load[0] == doctest::Approx(100.0));
  CHECK(s.load[1] == doctest::Approx(0.0));
  CHECK(s.input[0] == doctest::Approx(200.0));
  CHECK(s.input[1] == doctest::Approx(0.0));
  CHECK(s.obj == doctest::Approx(2000.0).epsilon(1e-6));
}

TEST_CASE("HydrogenDemand: per-block lmax forces spreading")  // NOLINT
{
  auto sys = make_h2_system();
  // Same target but at most 60 MW_LHV per block: block 0 takes 60
  // (120 MWh_e cheap), block 1 the remaining 40 (80 MWh_e: 50 cheap +
  // 30 backup) → 170·10 + 30·50 = 3200.
  sys.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_offtake",
          .hydrogen_node = SingleId {Uid {11}},
          .lmax = 60.0,
          .min_energy = 100.0,
      },
  };
  const auto s = solve(sys);
  CHECK(s.load[0] == doctest::Approx(60.0));
  CHECK(s.load[1] == doctest::Approx(40.0));
  CHECK(s.obj == doctest::Approx(3200.0).epsilon(1e-6));
}

TEST_CASE("HydrogenDemand: soft minimum uses the shortfall slack")  // NOLINT
{
  auto sys = make_h2_system();
  // Hydrogen costs ≥ 20 $/MWh_LHV to produce; a 5 $/MWh_LHV shortfall
  // penalty is cheaper, so nothing is produced and 1000 MWh_LHV go to
  // the slack → 5000.
  sys.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_offtake",
          .hydrogen_node = SingleId {Uid {11}},
          .min_energy = 1000.0,
          .min_energy_cost = 5.0,
      },
  };
  const auto s = solve(sys);
  CHECK(s.load[0] == doctest::Approx(0.0));
  CHECK(s.load[1] == doctest::Approx(0.0));
  CHECK(s.obj == doctest::Approx(5000.0).epsilon(1e-6));
}

TEST_CASE("HydrogenDemand: price-responsive offtake")  // NOLINT
{
  auto sys = make_h2_system();
  // Buyer pays 30 $/MWh_LHV: cheap power (20 $/MWh_LHV after η) is
  // worth converting, backup power (100 $/MWh_LHV) is not.
  // Block 0: min(200 MW_e cheap, lmax 80 LHV → 160 MW_e) → 80 LHV.
  // Block 1: 50 MW_e cheap → 25 LHV.
  // obj = 210·10 − 105·30 = 2100 − 3150 = −1050.
  sys.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_buyer",
          .hydrogen_node = SingleId {Uid {11}},
          .lmax = 80.0,
          .price = 30.0,
      },
  };
  const auto s = solve(sys);
  CHECK(s.load[0] == doctest::Approx(80.0));
  CHECK(s.load[1] == doctest::Approx(25.0));
  CHECK(s.obj == doctest::Approx(-1050.0).epsilon(1e-6));
}

TEST_CASE("HydrogenDemand: PAMPL references from a UserConstraint")  // NOLINT
{
  auto sys = make_h2_system();
  sys.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_offtake",
          .hydrogen_node = SingleId {Uid {11}},
          .min_energy = 100.0,
      },
  };
  SUBCASE("hydrogen_demand load capped per block")
  {
    // Cap the offtake at 70 per block → block 0: 70 (140 MWh_e cheap),
    // block 1: 30 (60 MWh_e: 50 cheap + 10 backup) → 1900 + 500 = 2400.
    sys.user_constraint_array = {
        {
            .uid = Uid {1},
            .name = "h2_cap",
            .expression = "hydrogen_demand('h2_offtake').load <= 70",
        },
    };
    const auto s = solve(sys);
    CHECK(s.load[0] == doctest::Approx(70.0));
    CHECK(s.load[1] == doctest::Approx(30.0));
    CHECK(s.obj == doctest::Approx(2400.0).epsilon(1e-6));
  }
  SUBCASE("carrier_converter input capped per block")
  {
    // Electrolyser input ≤ 120 MW_e per block → block 0: 120 (60 LHV),
    // block 1: 80 MW_e (40 LHV; 50 cheap + 30 backup) → 1700 + 1500.
    sys.user_constraint_array = {
        {
            .uid = Uid {1},
            .name = "elec_cap",
            .expression = "carrier_converter('electrolyser').input <= 120",
        },
    };
    const auto s = solve(sys);
    CHECK(s.input[0] == doctest::Approx(120.0));
    CHECK(s.input[1] == doctest::Approx(80.0));
    CHECK(s.obj == doctest::Approx(3200.0).epsilon(1e-6));
  }
}

TEST_CASE("HydrogenDemand: JSON round trip")  // NOLINT
{
  constexpr std::string_view json = R"({
    "name": "h2",
    "hydrogen_node_array": [{"uid": 11, "name": "h2"}],
    "hydrogen_demand_array": [{
      "uid": 1, "name": "h2_offtake", "type": "domestic",
      "hydrogen_node": "h2", "lmax": 60, "min_energy": 100,
      "max_energy": 120, "min_energy_cost": 500, "price": 3.5
    }]
  })";
  const auto sys = daw::json::from_json<System>(json);
  REQUIRE(sys.hydrogen_demand_array.size() == 1);
  const auto& hd = sys.hydrogen_demand_array.front();
  CHECK(hd.name == "h2_offtake");
  CHECK(hd.type.value_or("") == "domestic");
  CHECK(hd.hydrogen_node.has_value());
  CHECK(hd.min_energy.has_value());
  CHECK(hd.max_energy.has_value());
  CHECK(hd.min_energy_cost.has_value());
  CHECK(hd.price.has_value());

  const auto out = daw::json::to_json(sys);
  CHECK(out.find("hydrogen_demand_array") != std::string::npos);
  CHECK(out.find("min_energy_cost") != std::string::npos);
}

TEST_CASE("System::merge keeps the multi-carrier arrays")  // NOLINT
{
  // Regression: the planning-file merge used by the standalone binary
  // dropped hydrogen / thermal / ammonia nodes, storages, converters and
  // allowance pools, so a HydrogenDemand referenced a missing node.
  auto src = make_h2_system();
  src.hydrogen_demand_array = {
      {
          .uid = Uid {1},
          .name = "h2_offtake",
          .hydrogen_node = SingleId {Uid {11}},
      },
  };
  src.thermal_node_array = {{{.uid = Uid {1}, .name = "th"}}};
  src.ammonia_node_array = {{{.uid = Uid {1}, .name = "nh3"}}};
  System dst;
  dst.merge(std::move(src));
  CHECK(dst.hydrogen_node_array.size() == 1);
  CHECK(dst.hydrogen_demand_array.size() == 1);
  CHECK(dst.carrier_converter_array.size() == 1);
  CHECK(dst.thermal_node_array.size() == 1);
  CHECK(dst.ammonia_node_array.size() == 1);
}
