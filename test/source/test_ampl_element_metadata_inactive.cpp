// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_ampl_element_metadata_inactive.cpp
 * @brief     Filter metadata is registered for inactive elements too
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <string>
#include <string_view>
#include <variant>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_system.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Simulation inactive_metadata_simulation()
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

}  // namespace

TEST_CASE(  // NOLINT
    "AMPL element metadata — inactive Battery / Generator still register "
    "their filter metadata")
{
  // Filter metadata (`type`, `bus`) is a static attribute of the element:
  // it is registered before the `is_active(stage)` early-out so a filtered
  // `sum(<class>(all : type=...))` sees an inactive element and counts its
  // term as an inactive drop, the same as the unfiltered sum.
  constexpr std::string_view json = R"({
   "name":"inactive_metadata","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[
    {"uid":1,"name":"g_on","bus":"b1","pmax":100,"gcost":10,"capacity":100},
    {"uid":2,"name":"g_off","type":"diesel","bus":"b1","active":0,
     "pmax":50,"gcost":20,"capacity":50}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":10}],
   "battery_array":[{"uid":1,"name":"bat_off","type":"li_ion","bus":"b1",
     "active":0,"emin":0,"emax":100,"eini":0,"capacity":100}]})";

  const PlanningOptionsLP options;
  const auto sim = inactive_metadata_simulation();
  SimulationLP simulation_lp(sim, options);
  simulation_lp.set_need_ampl_variables(/*v=*/true);
  const auto system = daw::json::from_json<System>(json);
  const SystemLP system_lp(system, simulation_lp);
  const auto& sc = system_lp.system_context();

  const auto type_of = [&](std::string_view cls, Uid uid) -> std::string
  {
    const auto* md = sc.find_ampl_element_metadata(cls, uid);
    if (md == nullptr) {
      return "<none>";
    }
    const auto it = std::ranges::find_if(
        *md, [](const auto& kv) { return kv.first == "type"; });
    if (it == md->end()) {
      return "<no type>";
    }
    const auto* s = std::get_if<std::string>(&it->second);
    return s != nullptr ? *s : std::string {"<not a string>"};
  };

  CHECK(type_of("generator", Uid {2}) == "diesel");
  CHECK(type_of("battery", Uid {1}) == "li_ion");
}
