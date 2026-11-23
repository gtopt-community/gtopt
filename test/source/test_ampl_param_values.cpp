/**
 * @file      test_ampl_param_values.cpp
 * @brief     AMPL param accessors return the configured element values
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_ampl_dispatch_registry.cpp` pins that every (class, attribute)
 * pair is *registered*; this file evaluates the resolvers of the
 * multi-carrier / fuel-offtake / LNG / user-constraint attributes on a
 * system that carries one element of each class with distinct values,
 * so a resolver wired to the wrong field (or the wrong element type)
 * fails here.
 */

#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

Simulation one_block_simulation()
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

System multi_carrier_system()
{
  System sys;
  sys.name = "ampl_param_values";
  sys.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  sys.generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .pmax = 100.0,
          .gcost = 10.0,
          .capacity = 100.0,
      },
  };
  sys.demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 20.0,
      },
  };
  sys.fuel_array = {
      {
          .uid = Uid {1},
          .name = "gas",
          .price = 5.0,
          .max_offtake = 1000.0,
          .max_offtake_cost = 7.0,
          .min_offtake = 10.0,
          .min_offtake_cost = 3.0,
      },
  };
  sys.lng_terminal_array = {
      {
          .uid = Uid {1},
          .name = "lng1",
          .emin = 0.0,
          .emax = 1000.0,
          .eini = 100.0,
          .delivery = 500.0,
      },
  };
  sys.thermal_node_array = {
      {{
          .uid = Uid {31},
          .name = "th_node",
      }},
  };
  sys.thermal_storage_array = {
      {
          .uid = Uid {1},
          .name = "tes",
          .thermal_node = SingleId {Uid {31}},
          .input_efficiency = 0.91,
          .output_efficiency = 0.92,
          .emin = 1.0,
          .emax = 101.0,
          .ecost = 0.1,
          .eini = 1.0,
          .capacity = 101.0,
      },
  };
  sys.hydrogen_node_array = {
      {{
          .uid = Uid {11},
          .name = "h2_node",
      }},
  };
  sys.hydrogen_storage_array = {
      {
          .uid = Uid {1},
          .name = "h2s",
          .hydrogen_node = SingleId {Uid {11}},
          .input_efficiency = 0.81,
          .output_efficiency = 0.82,
          .emin = 2.0,
          .emax = 202.0,
          .ecost = 0.2,
          .eini = 2.0,
          .capacity = 202.0,
      },
  };
  sys.ammonia_node_array = {
      {{
          .uid = Uid {22},
          .name = "nh3_node",
      }},
  };
  sys.ammonia_storage_array = {
      {
          .uid = Uid {1},
          .name = "nh3s",
          .ammonia_node = SingleId {Uid {22}},
          .input_efficiency = 0.71,
          .output_efficiency = 0.72,
          .emin = 3.0,
          .emax = 303.0,
          .ecost = 0.3,
          .eini = 3.0,
          .capacity = 303.0,
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
          .efficiency = 0.65,
          .ocost = 4.0,
          .capacity = 50.0,
      },
  };
  sys.user_constraint_array = {
      {
          .uid = Uid {1},
          .name = "gen_cap",
          .expression = "generator(\"g1\").generation <= 150",
          .rhs = 123.0,
      },
  };
  return sys;
}

}  // namespace

TEST_CASE("AMPL param accessors read the configured values")  // NOLINT
{
  const PlanningOptionsLP options;
  const auto sim = one_block_simulation();
  SimulationLP simulation_lp(sim, options);
  simulation_lp.set_need_ampl_variables(/*v=*/true);
  const auto system = multi_carrier_system();
  SystemLP system_lp(system, simulation_lp);
  const auto& sc = system_lp.system_context();

  const auto s1 = make_uid<Stage>(1);
  const auto b1 = make_uid<Block>(1);

  const auto value = [&](std::string_view cls, std::string_view attr)
  {
    const auto fn = sc.find_ampl_param(cls, attr);
    REQUIRE_MESSAGE(fn != nullptr, cls << "." << attr << " not registered");
    return fn(sc, Uid {1}, s1, b1);
  };

  SUBCASE("fuel offtake caps")
  {
    CHECK(value("fuel", "price").value_or(-1.0) == doctest::Approx(5.0));
    CHECK(value("fuel", "max_offtake").value_or(-1.0)
          == doctest::Approx(1000.0));
    CHECK(value("fuel", "max_offtake_cost").value_or(-1.0)
          == doctest::Approx(7.0));
    CHECK(value("fuel", "min_offtake").value_or(-1.0) == doctest::Approx(10.0));
    CHECK(value("fuel", "min_offtake_cost").value_or(-1.0)
          == doctest::Approx(3.0));
  }

  SUBCASE("LNG delivery")
  {
    CHECK(value("lng_terminal", "delivery").value_or(-1.0)
          == doctest::Approx(500.0));
  }

  SUBCASE("thermal storage")
  {
    CHECK(value("thermal_storage", "emin").value_or(-1.0)
          == doctest::Approx(1.0));
    CHECK(value("thermal_storage", "emax").value_or(-1.0)
          == doctest::Approx(101.0));
    CHECK(value("thermal_storage", "ecost").value_or(-1.0)
          == doctest::Approx(0.1));
    CHECK(value("thermal_storage", "input_efficiency").value_or(-1.0)
          == doctest::Approx(0.91));
    CHECK(value("thermal_storage", "output_efficiency").value_or(-1.0)
          == doctest::Approx(0.92));
  }

  SUBCASE("hydrogen storage")
  {
    CHECK(value("hydrogen_storage", "emin").value_or(-1.0)
          == doctest::Approx(2.0));
    CHECK(value("hydrogen_storage", "emax").value_or(-1.0)
          == doctest::Approx(202.0));
    CHECK(value("hydrogen_storage", "ecost").value_or(-1.0)
          == doctest::Approx(0.2));
    CHECK(value("hydrogen_storage", "input_efficiency").value_or(-1.0)
          == doctest::Approx(0.81));
    CHECK(value("hydrogen_storage", "output_efficiency").value_or(-1.0)
          == doctest::Approx(0.82));
  }

  SUBCASE("ammonia storage")
  {
    CHECK(value("ammonia_storage", "emin").value_or(-1.0)
          == doctest::Approx(3.0));
    CHECK(value("ammonia_storage", "emax").value_or(-1.0)
          == doctest::Approx(303.0));
    CHECK(value("ammonia_storage", "ecost").value_or(-1.0)
          == doctest::Approx(0.3));
    CHECK(value("ammonia_storage", "input_efficiency").value_or(-1.0)
          == doctest::Approx(0.71));
    CHECK(value("ammonia_storage", "output_efficiency").value_or(-1.0)
          == doctest::Approx(0.72));
  }

  SUBCASE("carrier converter")
  {
    CHECK(value("carrier_converter", "efficiency").value_or(-1.0)
          == doctest::Approx(0.65));
    CHECK(value("carrier_converter", "ocost").value_or(-1.0)
          == doctest::Approx(4.0));
  }

  SUBCASE("user constraint rhs override")
  {
    CHECK(value("user_constraint", "rhs").value_or(-1.0)
          == doctest::Approx(123.0));
  }
}
