// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Output streams completed by the add_to_lp audit (B2, P1-5).  The LP is
// unchanged; these columns / rows were built but never written:
//   * StorageLP `efin_slack` (soft end-of-horizon target, `efin_cost`);
//   * VolumeRight `extraction`, `saving`, `forfeit`, `fail` and the `demand` /
//     `reset_debit` duals;
//   * Waterway soft-`fmin` shortfall `fmin_unserved` and the `fmin_soft`
//     dual;
//   * GasSupply stage `volume` cap dual.
// Stock rows / columns (volume, right deficit, efin slack) read back with
// the Energy basis: $/unit, no 1/duration.

#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/gas_supply_lp.hpp>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/volume_right_lp.hpp>
#include <gtopt/waterway_lp.hpp>

using namespace gtopt;

namespace
{

/// One stage of one 2-hour block, probability 1, no discount.
Simulation b2_outputs_simulation()
{
  return Simulation {
      .block_array = {{.uid = Uid {1}, .duration = 2.0}},
      .stage_array = {{
          .uid = Uid {1},
          .first_block = 0,
          .count_block = 1,
      }},
      .scenario_array = {{.uid = Uid {1}}},
  };
}

/// Values of `<cname>/<field>_<kind>` in @p oc (empty when not emitted).
std::vector<double> b2_field_values(const OutputContext& oc,
                                    std::string_view cname,
                                    std::string_view field,
                                    std::string_view kind)
{
  const OutputContext::ClassFieldName key {cname, field, kind};
  std::vector<double> out;
  const auto it = oc.fields().find(key);
  if (it == oc.fields().end()) {
    return out;
  }
  for (const auto& entry : it->second) {
    for (const auto& [slot, value] : entry.entries) {
      out.push_back(value);
    }
  }
  return out;
}

struct B2Solved
{
  explicit B2Solved(std::string_view json)
      : simulation(b2_outputs_simulation())
      , options(make_options())
      , simulation_lp(simulation, options)
      , system_lp(daw::json::from_json<System>(json), simulation_lp)
  {
    auto&& li = system_lp.linear_interface();
    const auto result = li.resolve();
    REQUIRE(result.has_value());
    REQUIRE(result.value() == 0);
  }

  static PlanningOptionsLP make_options()
  {
    PlanningOptions popts;
    popts.model_options.demand_fail_cost = 1000.0;
    popts.model_options.scale_objective = 1.0;
    popts.model_options.use_single_bus = true;
    return PlanningOptionsLP {popts};
  }

  OutputContext output()
  {
    return OutputContext {system_lp.system_context(),
                          system_lp.linear_interface(),
                          system_lp.scene().uid(),
                          system_lp.phase().uid(),
                          system_lp.phase().is_continuous()};
  }

  Simulation simulation;
  PlanningOptionsLP options;
  SimulationLP simulation_lp;
  SystemLP system_lp;
};

}  // namespace

TEST_CASE("Waterway soft fmin writes its shortfall and floor dual")  // NOLINT
{
  // No water reaches j1, so the 10 m³/s floor is missed entirely:
  // fmin_unserved = 10, priced 100 $/(m³/s·h) → floor dual 100.
  constexpr std::string_view json = R"({
   "name":"soft_fmin","bus_array":[{"uid":1,"name":"b1"}],
   "junction_array":[{"uid":1,"name":"j1"},
                     {"uid":2,"name":"j2","drain":true}],
   "waterway_array":[{"uid":1,"name":"w1","junction_a":"j1",
     "junction_b":"j2","fmin":10,"fmax":100,"fmin_fcost":100}]})";
  B2Solved s(json);
  auto oc = s.output();
  REQUIRE(s.system_lp.elements<WaterwayLP>().front().add_to_output(oc));
  const auto unserved =
      b2_field_values(oc, "Waterway", WaterwayLP::FminUnservedName, "sol");
  REQUIRE(unserved.size() == 1);
  CHECK(unserved.front() == doctest::Approx(10.0));
  const auto dual =
      b2_field_values(oc, "Waterway", WaterwayLP::FminSoftName, "dual");
  REQUIRE(dual.size() == 1);
  CHECK(std::abs(dual.front()) == doctest::Approx(100.0));
}

TEST_CASE("VolumeRight writes extraction, fail and the demand dual")  // NOLINT
{
  // An empty right (eini 0, nothing flows in) cannot serve its 5 hm³
  // stage demand: fail = 5 at 1000 $/hm³, demand-row dual 1000 $/hm³
  // (Energy basis: no 1/duration although the block lasts 2 h).
  constexpr std::string_view json = R"({
   "name":"vr_fail","bus_array":[{"uid":1,"name":"b1"}],
   "volume_right_array":[{"uid":1,"name":"vr1","emin":0,"emax":100,
     "eini":0,"demand":5,"fail_cost":1000,"fmax":10}]})";
  B2Solved s(json);
  auto oc = s.output();
  REQUIRE(s.system_lp.elements<VolumeRightLP>().front().add_to_output(oc));
  const auto fail =
      b2_field_values(oc, "VolumeRight", VolumeRightLP::FailName, "sol");
  REQUIRE(fail.size() == 1);
  CHECK(fail.front() == doctest::Approx(5.0));
  const auto extraction =
      b2_field_values(oc, "VolumeRight", VolumeRightLP::ExtractionName, "sol");
  CHECK(extraction.size() <= 1);  // zero cells may be dropped
  const auto dual =
      b2_field_values(oc, "VolumeRight", VolumeRightLP::DemandName, "dual");
  REQUIRE(dual.size() == 1);
  CHECK(std::abs(dual.front()) == doctest::Approx(1000.0));
}

TEST_CASE("VolumeRight writes its forfeit (free disposal)")  // NOLINT
{
  // The right starts with 10 hm³ but may hold only 5 and cannot be
  // extracted (fmax 0): the excess leaves through the zero-cost forfeit
  // column, which must be written so the ledger balance closes from the
  // output (issue #573).
  constexpr std::string_view json = R"({
   "name":"vr_forfeit","bus_array":[{"uid":1,"name":"b1"}],
   "volume_right_array":[{"uid":1,"name":"vr1","emin":0,"emax":5,
     "eini":10,"fmax":0,"forfeit":true}]})";
  B2Solved s(json);
  auto oc = s.output();
  REQUIRE(s.system_lp.elements<VolumeRightLP>().front().add_to_output(oc));
  const auto forfeit =
      b2_field_values(oc, "VolumeRight", VolumeRightLP::ForfeitName, "sol");
  REQUIRE(forfeit.size() == 1);
  CHECK(forfeit.front() > 0.0);
}

TEST_CASE("Reservoir soft efin writes its slack")  // NOLINT
{
  // No inflow: the 50 hm³ end target is missed by 50 (efin_cost 10).
  constexpr std::string_view json = R"({
   "name":"efin_slack","bus_array":[{"uid":1,"name":"b1"}],
   "junction_array":[{"uid":1,"name":"j1","drain":true}],
   "reservoir_array":[{"uid":1,"name":"r1","junction":"j1","emin":0,
     "emax":100,"eini":0,"efin":50,"efin_cost":10,"capacity":100}]})";
  B2Solved s(json);
  auto oc = s.output();
  REQUIRE(s.system_lp.elements<ReservoirLP>().front().add_to_output(oc));
  const auto slack =
      b2_field_values(oc, "Reservoir", ReservoirLP::EfinSlackName, "sol");
  REQUIRE(slack.size() == 1);
  CHECK(slack.front() == doctest::Approx(50.0));
}

TEST_CASE("GasSupply writes the stage volume dual")  // NOLINT
{
  // Field s1 (1 $/unit) is capped at 50 units over the stage; the rest of
  // the 100-unit (50/h × 2 h) demand comes from s2 at 10 $/unit, so the
  // volume cap is worth 9 $/unit.
  constexpr std::string_view json = R"({
   "name":"gas_volume","bus_array":[{"uid":1,"name":"b1"}],
   "gas_node_array":[{"uid":1,"name":"n1","pmin":0,"pmax":100}],
   "gas_supply_array":[
     {"uid":1,"name":"s1","gas_node":"n1","fmax":100,"cost":1,"volume":50},
     {"uid":2,"name":"s2","gas_node":"n1","fmax":100,"cost":10}],
   "gas_demand_array":[{"uid":1,"name":"d1","gas_node":"n1","load":50,
     "fail_cost":1000}]})";
  B2Solved s(json);
  auto oc = s.output();
  REQUIRE(s.system_lp.elements<GasSupplyLP>().front().add_to_output(oc));
  const auto dual =
      b2_field_values(oc, "GasSupply", GasSupplyLP::VolumeName, "dual");
  REQUIRE(dual.size() == 1);
  CHECK(std::abs(dual.front()) == doctest::Approx(9.0));
}
