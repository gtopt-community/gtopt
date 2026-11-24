// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// A relaxed commitment (`relax: true`) has a continuous status u ∈ [0, 1];
// its `status_sol` output must be the LP value, not the integer-snapped one
// (`OutputContext::add_col_sol_integer` used to round 0.3 → 0 / 0.6 → 1 even
// though nothing enforced integrality on the column).

#include <vector>

#include <doctest/doctest.h>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

/// One bus, one 40–100 MW unit at 10 $/MWh with a 100 $/h no-load cost and
/// a 30 MW load: the relaxed optimum is u = p / pmax = 0.3 (the no-load
/// cost drives u to its floor).
[[nodiscard]] System make_system(bool simple)
{
  System sys {
      .name = "uc_relaxed_output",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array = {{
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 30.0,
      }},
      .generator_array = {{
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .pmax = 100.0,
          .gcost = 10.0,
          .capacity = 100.0,
      }},
  };
  if (simple) {
    sys.simple_commitment_array = {{
        .uid = Uid {1},
        .name = "g1_sc",
        .generator = Uid {1},
        .dispatch_pmin = 40.0,
        .relax = true,
    }};
  } else {
    sys.commitment_array = {{
        .uid = Uid {1},
        .name = "g1_uc",
        .generator = Uid {1},
        .noload_cost = 100.0,
        .pmin = 40.0,
        .initial_status = 1.0,
        .relax = true,
    }};
  }
  sys.fold_legacy_simple_commitments();
  return sys;
}

[[nodiscard]] Simulation make_simulation()
{
  return Simulation {
      .block_array = {{.uid = Uid {1}, .duration = 1.0}},
      .stage_array = {{
          .uid = Uid {1},
          .first_block = 0,
          .count_block = 1,
          .chronological = true,
      }},
      .scenario_array = {{.uid = Uid {1}}},
  };
}

/// Values of the `<cname>/status_sol` output field (one block).
[[nodiscard]] std::vector<double> status_output(const SystemLP& sys,
                                                const OutputContext& oc,
                                                std::string_view cname)
{
  const OutputContext::ClassFieldName key {cname, "status", "sol"};
  const auto it = oc.fields().find(key);
  std::vector<double> out;
  if (it == oc.fields().end() || it->second.empty()) {
    return out;
  }
  for (const auto& [slot, value] : it->second.front().entries) {
    out.push_back(value);
  }
  static_cast<void>(sys);
  return out;
}

}  // namespace

TEST_CASE("Relaxed Commitment status_sol keeps the LP value")  // NOLINT
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.use_single_bus = OptBool {true};
  const PlanningOptionsLP options(popts);
  const auto simulation = make_simulation();
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(make_system(/*simple=*/false), simulation_lp);
  auto&& li = system_lp.linear_interface();
  REQUIRE(li.resolve().has_value());

  const auto& cm = system_lp.elements<CommitmentLP>().front();
  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto* ucols = cm.find_status_cols(scenario, stage);
  REQUIRE(ucols != nullptr);
  REQUIRE_FALSE(ucols->empty());
  const double u = li.get_col_sol()[ucols->begin()->second];
  CHECK(u == doctest::Approx(0.3));

  OutputContext oc(system_lp.system_context(),
                   li,
                   system_lp.scene().uid(),
                   system_lp.phase().uid(),
                   system_lp.phase().is_continuous());
  REQUIRE(cm.add_to_output(oc));
  const auto values = status_output(system_lp, oc, "Commitment");
  REQUIRE(values.size() == 1);
  CHECK(values.front() == doctest::Approx(0.3));

  SUBCASE("a non-relaxed column in a MIP phase is still snapped")
  {
    OutputContext oc2(system_lp.system_context(),
                      li,
                      system_lp.scene().uid(),
                      system_lp.phase().uid(),
                      /*is_continuous_phase=*/false);
    STBIndexHolder<ColIndex> holder;
    holder[{scenario.uid(), stage.uid()}] = *ucols;
    oc2.add_col_sol_integer(
        "Commitment", "status", cm.id(), holder, /*relaxed=*/false);
    const auto snapped = status_output(system_lp, oc2, "Commitment");
    // round(0.3) = 0: the zero entry is kept in memory (dropped on write).
    REQUIRE(snapped.size() == 1);
    CHECK(snapped.front() == doctest::Approx(0.0));
  }
}

TEST_CASE("Relaxed SimpleCommitment status_sol keeps the LP value")  // NOLINT
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.use_single_bus = OptBool {true};
  const PlanningOptionsLP options(popts);
  const auto simulation = make_simulation();
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(make_system(/*simple=*/true), simulation_lp);
  auto&& li = system_lp.linear_interface();
  REQUIRE(li.resolve().has_value());

  const auto& cm = system_lp.elements<CommitmentLP>().front();
  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto ucol = cm.lookup_status_col(
      scenario, stage, simulation_lp.blocks().front().uid());
  REQUIRE(ucol.has_value());
  const double u = li.get_col_sol()[ucol.value_or(ColIndex {0})];

  OutputContext oc(system_lp.system_context(),
                   li,
                   system_lp.scene().uid(),
                   system_lp.phase().uid(),
                   system_lp.phase().is_continuous());
  REQUIRE(cm.add_to_output(oc));
  const auto values = status_output(system_lp, oc, "Commitment");
  // u ∈ [0.3, 0.75] here (no cost on u); whatever the LP picks is written
  // unrounded.
  if (u != 0.0) {
    REQUIRE(values.size() == 1);
    CHECK(values.front() == doctest::Approx(u));
  }
  CHECK(u >= 0.3 - 1e-9);
  CHECK(u <= 0.75 + 1e-9);
}
