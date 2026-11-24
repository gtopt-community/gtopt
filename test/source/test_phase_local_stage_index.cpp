// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_phase_local_stage_index.cpp
 * @brief     Phase-local vs global stage index in later phases
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `StageLP::index()` is the stage's position INSIDE its phase; schedules
 * and the global `SimulationLP::stages()` list use the global position.
 * Regressions (same class as the `SimulationLP::prev_stage` fix):
 *
 *  1. `CapacityObjectBase::add_to_lp` decided whether the previous phase
 *     publishes `capainst`/`capacost` by scanning the global stages while
 *     `s.index() <= prev_stage->index()` — a phase-local bound.  From the
 *     third phase on (or after a multi-stage phase) the scan stopped too
 *     early, an expansion stage of an intermediate phase was missed, and
 *     the capacity built there vanished at the next phase boundary.
 *  2. `EmissionSourceLP` read the per-stage `emission_captures[].rate`
 *     vector at `stage.index()`: every later phase used the values of the
 *     first stages.
 */

#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/monolithic_method.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_options.hpp>

using namespace gtopt;

namespace
{

/// `{"uid":..,"first_stage":..,"count_stage":..}` list for a layout.
[[nodiscard]] auto phase_json(const std::vector<int>& layout) -> std::string
{
  std::string out;
  int first = 0;
  for (std::size_t p = 0; p < layout.size(); ++p) {
    out += std::format(R"({{"uid":{},"first_stage":{},"count_stage":{}}},)",
                       p + 1,
                       first,
                       layout[p]);
    first += layout[p];
  }
  out.pop_back();
  return out;
}

/// `count` one-hour stages, one block each.
[[nodiscard]] auto stage_json(int count) -> std::pair<std::string, std::string>
{
  std::string blocks;
  std::string stages;
  for (int t = 0; t < count; ++t) {
    blocks += std::format(R"({{"uid":{},"duration":1}},)", t + 1);
    stages += std::format(
        R"({{"uid":{},"first_block":{},"count_block":1}},)", t + 1, t);
  }
  blocks.pop_back();
  stages.pop_back();
  return {blocks, stages};
}

constexpr int kStages = 4;

/// 100 MW load every hour.  `gnew` (10 $/MWh, no existing capacity) may
/// build one 100 MW module ONLY in stage `build_stage`, at 8760 $/MW-yr
/// (1 $/MW·h on the carried `capacost`); `gold` (100 $/MWh) covers the
/// rest.  Building pays back within its own stage, so even a myopic
/// phase builds, and the optimum is
///   10000 · build_stage + 1100 · (kStages − build_stage).
[[nodiscard]] auto expansion_case(const std::vector<int>& layout,
                                  int build_stage,
                                  std::string_view method) -> std::string
{
  const auto [blocks, stages] = stage_json(kStages);
  std::string expmod;
  for (int t = 0; t < kStages; ++t) {
    expmod += (t == build_stage) ? "1," : "0,";
  }
  expmod.pop_back();
  return std::format(
      R"({{"options":{{"method":"{}","annual_discount_rate":0.0,)"
      R"("model_options":{{"use_single_bus":true,"demand_fail_cost":10000,)"
      R"("scale_objective":1}}}},)"
      R"("simulation":{{"block_array":[{}],"stage_array":[{}],)"
      R"("scenario_array":[{{"uid":1,"probability_factor":1}}],)"
      R"("phase_array":[{}]}},)"
      R"("system":{{"name":"cap_prev_stage",)"
      R"("bus_array":[{{"uid":1,"name":"b1"}}],)"
      R"("demand_array":[{{"uid":1,"name":"d1","bus":"b1","lmax":100}}],)"
      R"("generator_array":[)"
      R"({{"uid":1,"name":"gnew","bus":"b1","gcost":10,"capacity":0,)"
      R"("expcap":100,"expmod":[{}],"capmax":1000,"annual_capcost":8760}},)"
      R"({{"uid":2,"name":"gold","bus":"b1","gcost":100,"capacity":1000}}]}}}})",
      method,
      blocks,
      stages,
      phase_json(layout),
      expmod);
}

/// Σ over phases of the (scene 0) phase-LP objectives after a solve.
[[nodiscard]] auto total_obj(PlanningLP& plp) -> double
{
  double obj = 0.0;
  const auto num_phases = static_cast<Index>(plp.simulation().phases().size());
  for (Index p = 0; p < num_phases; ++p) {
    obj += plp.system(first_scene_index(), PhaseIndex {p})
               .linear_interface()
               .get_obj_value();
  }
  return obj;
}

[[nodiscard]] auto monolithic_total(const std::string& json) -> double
{
  PlanningLP plp(parse_planning_json(json));
  MonolithicMethod solver;
  auto res = solver.solve(plp, {});
  REQUIRE(res.has_value());
  return total_obj(plp);
}

struct Bounds
{
  double lb;
  double ub;
};

[[nodiscard]] auto sddp_bounds(const std::string& json) -> Bounds
{
  PlanningLP plp(parse_planning_json(json));
  SDDPOptions opts;
  opts.max_iterations = 15;
  opts.convergence_tol = 1.0e-9;
  opts.stationary_tol = 0.0;
  opts.apertures = std::vector<Uid> {};
  opts.enable_api = false;
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  return {
      .lb = results->back().lower_bound,
      .ub = results->back().upper_bound,
  };
}

[[nodiscard]] auto layouts() -> std::vector<std::vector<int>>
{
  return {
      {4},
      {1, 3},
      {2, 2},
      {3, 1},
      {1, 1, 2},
      {1, 2, 1},
      {2, 1, 1},
      {1, 1, 1, 1},
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "Capacity built in an intermediate phase is carried to later phases")
{
  for (int build_stage = 0; build_stage < kStages; ++build_stage) {
    const double opt =
        (10000.0 * build_stage) + (1100.0 * (kStages - build_stage));
    for (const auto& layout : layouts()) {
      CAPTURE(build_stage);
      CAPTURE(phase_json(layout));
      CHECK(monolithic_total(expansion_case(layout, build_stage, "monolithic"))
            == doctest::Approx(opt).epsilon(1e-9));
      if (layout.size() > 1) {
        const auto b = sddp_bounds(expansion_case(layout, build_stage, "sddp"));
        CHECK(b.lb == doctest::Approx(opt).epsilon(1e-8));
        CHECK(b.ub == doctest::Approx(opt).epsilon(1e-8));
      }
    }
  }
}

TEST_CASE(  // NOLINT
    "Emission capture rate schedule uses the global stage position")
{
  // g1 serves 50 MW every hour at 10 $/MWh, emits 0.4 t/MWh into a
  // 50 $/t price-only zone, and captures `rate[t]` of it (cost 0).
  // Stage cost = 500 + 1000 · (1 − rate[t]); rate = [0, 1, 0.5, 1]
  // ⇒ 1500 + 500 + 1000 + 500 = 3500 on every phase layout.
  const auto [blocks, stages] = stage_json(kStages);
  for (const auto& layout : layouts()) {
    CAPTURE(phase_json(layout));
    const auto json = std::format(
        R"({{"options":{{"method":"monolithic","annual_discount_rate":0.0,)"
        R"("model_options":{{"use_single_bus":true,"demand_fail_cost":1e6,)"
        R"("scale_objective":1}}}},)"
        R"("simulation":{{"block_array":[{}],"stage_array":[{}],)"
        R"("scenario_array":[{{"uid":1,"probability_factor":1}}],)"
        R"("phase_array":[{}]}},)"
        R"("system":{{"name":"capture_sched",)"
        R"("bus_array":[{{"uid":1,"name":"b1"}}],)"
        R"("demand_array":[{{"uid":1,"name":"d1","bus":"b1","lmax":50}}],)"
        R"("generator_array":[{{"uid":1,"name":"g1","bus":"b1","gcost":10,)"
        R"("capacity":200,"emission_captures":[{{"emission":1,)"
        R"("rate":[0,1,0.5,1],"cost":0}}]}}],)"
        R"("emission_array":[{{"uid":1,"name":"co2"}}],)"
        R"("emission_zone_array":[{{"uid":1,"name":"tax",)"
        R"("emissions":[{{"emission":1,"weight":1}}],"price":50}}],)"
        R"("emission_source_array":[{{"uid":1,"name":"g1_co2",)"
        R"("generator":1,"zone":1,"emission":1,"rate":0.4}}]}}}})",
        blocks,
        stages,
        phase_json(layout));
    CHECK(monolithic_total(json) == doctest::Approx(3500.0).epsilon(1e-9));
  }
}
