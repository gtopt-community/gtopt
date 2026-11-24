// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Reserve ↔ commitment coupling (add_to_lp audit, P0-21, P0-22):
//
//  * P0-21 — with an expandable generator the up-reserve headroom row is
//    `g + r − capacity ≤ 0` (RHS 0).  CommitmentLP read that RHS as Pmax
//    and stamped `−0·u`, so a de-committed unit still offered reserve.
//  * P0-22 — Commitment / ReserveProvision / Converter paired generators
//    by comparing raw `SingleId`s, so `5` and `"g5"` (the same generator)
//    did not match and the u-gating was silently skipped.

#include <format>
#include <limits>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/waterway_lp.hpp>

using namespace gtopt;

namespace
{

Simulation make_chrono_simulation()
{
  return Simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
                  .chronological = true,
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

struct LpResult
{
  double obj {std::numeric_limits<double>::quiet_NaN()};
  Index ncols {0};
  Index nrows {0};
};

LpResult build_and_solve(const std::string& system_json, bool solve = true)
{
  const auto sys = daw::json::from_json<System>(system_json);
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = true;
  const PlanningOptionsLP options(popts);
  const auto simulation = make_chrono_simulation();
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& li = system_lp.linear_interface();
  LpResult res {
      .ncols = li.get_numcols(),
      .nrows = li.get_numrows(),
  };
  if (solve) {
    const auto result = li.resolve();
    if (result.has_value() && result.value() == 0) {
      res.obj = li.get_obj_value();
    }
  }
  return res;
}

/// 50 MW demand served by cheap g1 ($10).  g5 ($20, Pmax 100, relaxed
/// commitment with $1000/h no-load) is the only reserve provider for a
/// 30 MW up-reserve requirement ($5000/MW shortage).  A unit can offer
/// reserve only when committed: `g5 + r ≤ 100·u` ⇒ u ≥ 0.3 ⇒ cost
/// 500 + 0.3·1000 = 800.  An ungated reserve gives 500.
std::string reserve_system(std::string_view gen5_extra,
                           std::string_view cmt_gen,
                           std::string_view rp_gen)
{
  return std::format(R"({{
   "name":"rsv_uc","bus_array":[{{"uid":1,"name":"b1"}}],
   "generator_array":[
    {{"uid":1,"name":"g1","bus":"b1","pmax":200,"capacity":200,"gcost":10}},
    {{"uid":5,"name":"g5","bus":"b1","pmax":100,"capacity":100,
      "gcost":20{}}}],
   "demand_array":[{{"uid":1,"name":"d1","bus":"b1","lmax":50}}],
   "commitment_array":[{{"uid":1,"name":"c5","generator":{},
     "noload_cost":1000,"relax":true}}],
   "reserve_zone_array":[{{"uid":1,"name":"rz","urreq":30,"urcost":5000}}],
   "reserve_provision_array":[{{"uid":1,"name":"rp5","generator":{},
     "reserve_zones":["rz"],"urmax":100,"ur_provision_factor":1}}]}})",
                     gen5_extra,
                     cmt_gen,
                     rp_gen);
}

}  // namespace

TEST_CASE("Off-line unit offers no reserve with expansion enabled")  // NOLINT
{
  constexpr double expected = 500.0 + (0.3 * 1000.0);

  SUBCASE("no expansion (reference)")
  {
    CHECK(build_and_solve(reserve_system("", "5", "5")).obj
          == doctest::Approx(expected));
  }
  SUBCASE("expandable generator")
  {
    // Expansion module never worth building (huge annual cost): the
    // physics is identical to the reference, only the headroom row
    // switches to its capacity-column form.
    CHECK(build_and_solve(
              reserve_system(R"(,"expcap":100,"expmod":1,"capmax":200,)"
                             R"("annual_capcost":1e9)",
                             "5",
                             "5"))
              .obj
          == doctest::Approx(expected));
  }
}

TEST_CASE("Generator pairing resolves uid and name references alike")  // NOLINT
{
  constexpr double expected = 500.0 + (0.3 * 1000.0);

  SUBCASE("Commitment by uid, ReserveProvision by name")
  {
    CHECK(build_and_solve(reserve_system("", "5", R"("g5")")).obj
          == doctest::Approx(expected));
  }
  SUBCASE("Commitment by name, ReserveProvision by uid")
  {
    CHECK(build_and_solve(reserve_system("", R"("g5")", "5")).obj
          == doctest::Approx(expected));
  }

  SUBCASE("shared_headroom owner found across uid / name")
  {
    // Two provisions of g5 sharing one headroom row: the second must
    // stamp into the first one's row, not add its own (which would
    // double the headroom).  Same LP shape for either spelling.
    const auto make = [](std::string_view second_gen)
    {
      return std::format(R"({{
       "name":"shared","bus_array":[{{"uid":1,"name":"b1"}}],
       "generator_array":[{{"uid":5,"name":"g5","bus":"b1","pmax":100,
         "capacity":100,"gcost":20}}],
       "demand_array":[{{"uid":1,"name":"d1","bus":"b1","lmax":50}}],
       "reserve_zone_array":[
        {{"uid":1,"name":"rz1","urreq":30,"urcost":5000}},
        {{"uid":2,"name":"rz2","urreq":30,"urcost":5000}}],
       "reserve_provision_array":[
        {{"uid":1,"name":"rp1","generator":5,"reserve_zones":["rz1"],
          "urmax":100,"ur_provision_factor":1,"shared_headroom":true}},
        {{"uid":2,"name":"rp2","generator":{},"reserve_zones":["rz2"],
          "urmax":100,"ur_provision_factor":1,"shared_headroom":true}}]}})",
                         second_gen);
    };
    const auto by_uid = build_and_solve(make("5"));
    const auto by_name = build_and_solve(make(R"("g5")"));
    CHECK(by_name.nrows == by_uid.nrows);
    CHECK(by_name.obj == doctest::Approx(by_uid.obj));
  }

  SUBCASE("Converter shares the Commitment u across uid / name")
  {
    // Converter.commitment = true reuses the discharge generator's
    // Commitment u; when the pairing fails it falls back to creating its
    // own u_charge / u_discharge columns.
    const auto make = [](std::string_view conv_gen)
    {
      return std::format(R"({{
       "name":"conv","bus_array":[{{"uid":1,"name":"b1"}}],
       "generator_array":[
        {{"uid":1,"name":"gt","bus":"b1","pmax":500,"capacity":500,
          "gcost":100}},
        {{"uid":2,"name":"bgen","bus":"b1","pmax":50,"capacity":50,
          "gcost":0}}],
       "demand_array":[
        {{"uid":1,"name":"d1","bus":"b1","lmax":100}},
        {{"uid":2,"name":"bdem","bus":"b1","lmax":50,"fcost":0}}],
       "battery_array":[{{"uid":1,"name":"bat","emin":0,"emax":100,
         "eini":0}}],
       "commitment_array":[{{"uid":1,"name":"cb","generator":2,
         "relax":true}}],
       "converter_array":[{{"uid":1,"name":"conv","battery":"bat",
         "generator":{},"demand":"bdem","commitment":true}}]}})",
                         conv_gen);
    };
    const auto by_uid = build_and_solve(make("2"), /*solve=*/false);
    const auto by_name = build_and_solve(make(R"("bgen")"), /*solve=*/false);
    CHECK(by_name.ncols == by_uid.ncols);
    CHECK(by_name.nrows == by_uid.nrows);
  }
}

TEST_CASE("Waterway self-loop is detected across uid / name")  // NOLINT
{
  // junction_a = 1 and junction_b = "j1" are the same junction: the
  // waterway is a self-loop and emits no flow column (the raw SingleId
  // compare missed it).
  const auto sys = daw::json::from_json<System>(R"({
   "name":"loop","bus_array":[{"uid":1,"name":"b1"}],
   "junction_array":[{"uid":1,"name":"j1","drain":true}],
   "waterway_array":[{"uid":1,"name":"w","junction_a":1,
     "junction_b":"j1","fmax":10}]})");
  const PlanningOptionsLP options;
  const auto simulation = make_chrono_simulation();
  SimulationLP simulation_lp(simulation, options);
  const SystemLP system_lp(sys, simulation_lp);
  const auto& ww = system_lp.elements<WaterwayLP>().front();
  const auto& scenario = system_lp.scene().scenarios().front();
  const auto& stage = system_lp.phase().stages().front();
  CHECK_FALSE(ww.lookup_flow_col(scenario, stage, stage.blocks().front().uid())
                  .has_value());
}
