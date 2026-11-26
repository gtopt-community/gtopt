// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_period_hours_blocks.cpp
 * @brief     Non-uniform / weighted blocks: Stage.period_hours, ramps
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A typical-period stage (`Stage.period_hours`) has WEIGHTED block
 * durations (energy / cost weights, summing to period × repetitions) and
 * real time steps Δt_b = duration_b · period_hours / stage_duration.
 * Storage cycles, ramps and minimum up / down times run on the real
 * steps.  `ramp_formula = central2` uses the second-order derivative on a
 * non-uniform node grid (`Block.time`).  All optima are hand-computed.
 */

#include <array>
#include <cmath>
#include <filesystem>
#include <format>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/bus_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "test_csv_helpers.hpp"

using namespace gtopt;

namespace period_hours_test
{
namespace
{

[[nodiscard]] double solve_obj(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  return plp.systems().front().front().linear_interface().get_obj_value();
}

[[nodiscard]] std::string options(std::string_view extra = "")
{
  return std::format(
      R"("options": {{"annual_discount_rate": 0.0, "output_format": "csv",
                 "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                    "demand_fail_cost": 10000,
                                    "scale_objective": 1 {}}}}})",
      extra);
}

}  // namespace
}  // namespace period_hours_test

TEST_CASE(  // NOLINT
    "Period stage - daily_cycle battery cycles on real 1 h / 2 h steps")
{
  using namespace period_hours_test;
  // Period of 4 real hours (1 h, 2 h, 1 h) repeated 10 times: weighted
  // durations 10, 20, 10 (stage 40 h), period_hours 4.  Prices 1 / 100 /
  // 1 $/MWh, load 10 MW, battery 15 MWh (η = 1) closed over the period.
  // Per period: the battery moves at most 15 MWh into the 2 h block that
  // needs 20 MWh:  cheap 10 + 10 + 15 = 35 MWh × 1, dear 5 MWh × 100.
  //   10 periods × (35 + 500) = 5350
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 10}}, {{"uid": 2, "duration": 20}},
                    {{"uid": 3, "duration": 10}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 3,
                      "period_hours": 4}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "period_battery",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[1, 100, 1]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": 15,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 15, "daily_cycle": true}}]}}
}})",
      options());
  CHECK(solve_obj(json) == doctest::Approx(10.0 * (35.0 + 500.0)));
}

namespace period_hours_test
{
namespace
{

/// One chronological stage, `g1` (1 $/MWh, must-run, ramp-up limited)
/// and `g2` (100 $/MWh); per-block load `[[...]]`.
[[nodiscard]] std::string ramp_case(std::string_view model_extra,
                                    std::string_view blocks,
                                    std::string_view stage_extra,
                                    std::string_view lmax,
                                    int nblocks)
{
  return std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "ramp_case",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": {}}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "ramp_up": 10, "startup_ramp": 0, "initial_status": 1,
                           "initial_power": 0, "relax": true,
                           "must_run": true}}]}}
}})",
      options(model_extra),
      blocks,
      nblocks,
      stage_extra,
      lmax);
}

}  // namespace
}  // namespace period_hours_test

TEST_CASE(  // NOLINT
    "Period stage - backward ramp uses the real time step")
{
  using namespace period_hours_test;
  // Weighted durations 10, 20 with period_hours 3 ⇒ real steps 1 h, 2 h.
  // RU = 10 MW/h from initial_power 0: p1 ≤ 10, p2 ≤ p1 + 10·2 = 30.
  // Loads 10, 50:  cost = 10·10·1 + 30·20·1 + 20·20·100 = 40700.
  // (Ramps on the weighted durations would allow 100 MW/step: 1100.)
  const auto json = ramp_case("",
                              R"({"uid": 1, "duration": 10},
                                 {"uid": 2, "duration": 20})",
                              R"(, "period_hours": 3)",
                              "[[10, 50]]",
                              2);
  CHECK(solve_obj(json) == doctest::Approx(40700.0));
}

TEST_CASE(  // NOLINT
    "Period stage - central2 ramp on a 3-node non-uniform grid")
{
  using namespace period_hours_test;
  // Nodes t = 0, 1, 3 (Block.time), durations 1, 4, 1; loads 0, 100,
  // 100 ⇒ P0 = 0.  RU = 10 MW/h:
  //   first  (forward)   (P1 − P0)/1                  ≤ 10  ⇒ P1 ≤ 10
  //   middle (h₋=1,h₊=2) (1·P2 − 4·P0 + 3·P1)/(2·1·3) ≤ 10  ⇒ P2+3P1 ≤ 60
  //   last   (backward)  (P2 − P1)/2                  ≤ 10  ⇒ P2 ≤ P1+20
  // max 4·P1 + P2 ⇒ P1 = 10 (first-node bound binds), P2 = 30.
  //   cost = (4·10 + 30)·1 + (4·90 + 70)·100 = 70 + 43000 = 43070
  // (backward ramps would give P1 = 40, P2 = 50: 160+50 + (240+50)·100.)
  const auto* const blocks = R"({"uid": 1, "duration": 1, "time": 0},
                         {"uid": 2, "duration": 4, "time": 1},
                         {"uid": 3, "duration": 1, "time": 3})";
  const auto json = ramp_case(
      R"(, "ramp_formula": "central2")", blocks, "", "[[0, 100, 100]]", 3);
  CHECK(solve_obj(json) == doctest::Approx(43070.0));

  const auto backward = ramp_case("", blocks, "", "[[0, 100, 100]]", 3);
  CHECK(solve_obj(backward)
        == doctest::Approx(210.0 + ((240.0 + 50.0) * 100.0)));
}

// ---------------------------------------------------------------------
// Storage integration on mixed block durations (1, 2, 0.5, 3.5 h)
// ---------------------------------------------------------------------

namespace period_hours_test
{
namespace
{

struct StageSolution
{
  double obj {0.0};
  std::vector<double> soc;  ///< storage level at each block end
  std::vector<double> lmp;  ///< bus price per block [$/MWh]
};

template<typename StorageLPT>
[[nodiscard]] StageSolution solve_stage(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();

  StageSolution out;
  out.obj = li.get_obj_value();
  const auto sol = li.get_col_sol();
  const auto& st = sys.elements<StorageLPT>().front();
  for (const auto& [buid, col] : st.energy_cols_at(scenario, stage)) {
    out.soc.push_back(st.to_physical(sol[col]));
  }
  const auto duals = li.get_row_dual();
  const auto& bus = sys.elements<BusLP>().front();
  const auto& blocks = stage.blocks();
  std::size_t k = 0;
  for (const auto& [buid, row] : bus.balance_rows_at(scenario, stage)) {
    // LP duals carry the cost factor prob·discount·Δt (= Δt here)
    out.lmp.push_back(std::abs(duals[static_cast<std::size_t>(row)])
                      / blocks[k++].duration());
  }
  return out;
}

constexpr std::string_view kMixedBlocks =
    R"({"uid": 1, "duration": 1}, {"uid": 2, "duration": 2},
       {"uid": 3, "duration": 0.5}, {"uid": 4, "duration": 3.5})";

}  // namespace
}  // namespace period_hours_test

TEST_CASE(  // NOLINT
    "Mixed durations - battery SoC recursion, closure, bounds and prices")
{
  using namespace period_hours_test;
  // Δt = 1, 2, 0.5, 3.5 h; price 1 / 100 / 1 / 50 $/MWh; load 10 MW.
  // Battery 9 MWh, η_c = η_d = 0.9, charge ≤ 10 MW, closed over the stage.
  //   b1: charge 10 MW·1 h → +0.9·10 = 9      SoC 9 (emax binds)
  //   b2: discharge 9 cell → 8.1 MWh to grid  SoC 0
  //   b3: charge 10 MW·0.5 h → +4.5           SoC 4.5
  //   b4: discharge 4.5 cell → 4.05 MWh       SoC 0 = start (closure)
  //   cost = 20·1 + (20 − 8.1)·100 + 10·1 + (35 − 4.05)·50 = 2767.5
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "mixed_battery",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[1, 100, 1, 50]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 0.9, "output_efficiency": 0.9,
                        "emin": 0, "emax": 9,
                        "pmax_charge": 10, "pmax_discharge": 100,
                        "capacity": 9, "daily_cycle": false,
                        "use_state_variable": false}}]}}
}})",
      options(),
      kMixedBlocks);
  const auto s = solve_stage<BatteryLP>(json);
  CHECK(s.obj == doctest::Approx(20.0 + 1190.0 + 10.0 + 1547.5));
  REQUIRE(s.soc.size() == 4);
  CHECK(s.soc[0] == doctest::Approx(9.0));
  CHECK(s.soc[1] == doctest::Approx(0.0));
  CHECK(s.soc[2] == doctest::Approx(4.5));
  CHECK(s.soc[3] == doctest::Approx(0.0));
  REQUIRE(s.lmp.size() == 4);
  // the generator is marginal in every block: price = its cost
  CHECK(s.lmp[1] == doctest::Approx(100.0));
  CHECK(s.lmp[3] == doctest::Approx(50.0));
}

TEST_CASE(  // NOLINT
    "Mixed durations - reservoir volume recursion V += (Qin − Qturb)·Δt·fcr")
{
  using namespace period_hours_test;
  // fcr = 1 (volume per m³/s·h), inflow 10, V0 = 50, free end volume;
  // turbine 1 MW per m³/s; thermal 10 / 100 / 20 / 50 $/MWh; load 20 MW.
  // Water (50 + 10·7 = 120) goes to the dearest hours: b2 40, b4 70,
  // b3 10 (= 20 MW · 0.5 h), b1 none:
  //   V = 50+10 = 60, 60+20−40 = 40, 40+5−10 = 35, 35+35−70 = 0
  //   cost = 20 MWh · 10 = 200
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "mixed_reservoir",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 20}}],
    "generator_array": [
      {{"uid": 1, "name": "hyd", "bus": "b1", "gcost": 0, "capacity": 1000}},
      {{"uid": 2, "name": "thermal", "bus": "b1",
        "gcost": [[10, 100, 20, 50]], "capacity": 1000}}],
    "junction_array": [{{"uid": 1, "name": "j1"}},
                       {{"uid": 2, "name": "j2", "drain": true}}],
    "waterway_array": [{{"uid": 1, "name": "ww1", "junction_a": 1,
                         "junction_b": 2, "fmin": 0, "fmax": 500}}],
    "flow_array": [{{"uid": 1, "name": "q_in", "direction": 1,
                     "junction": 1, "discharge": 10}}],
    "reservoir_array": [{{"uid": 1, "name": "rsv", "junction": 1,
                          "flow_conversion_rate": 1, "capacity": 100,
                          "emin": 0, "emax": 100, "eini": 50}}],
    "turbine_array": [{{"uid": 1, "name": "tur", "waterway": 1,
                        "generator": 1, "production_factor": 1.0}}]}}
}})",
      options(),
      kMixedBlocks);
  const auto s = solve_stage<ReservoirLP>(json);
  CHECK(s.obj == doctest::Approx(200.0));
  REQUIRE(s.soc.size() == 4);
  CHECK(s.soc[0] == doctest::Approx(60.0));
  CHECK(s.soc[1] == doctest::Approx(40.0));
  CHECK(s.soc[2] == doctest::Approx(35.0));
  CHECK(s.soc[3] == doctest::Approx(0.0));
}

TEST_CASE(  // NOLINT
    "Mixed durations - 2 h blocks equal paired 1 h blocks")
{
  using namespace period_hours_test;
  // Prices 1, 100 on two 2 h blocks vs the same data on four 1 h blocks;
  // battery 15 MWh η 0.9 closed: identical objective and end levels.
  const auto make = [](std::string_view blocks, std::string_view gcost, int n)
  {
    return std::format(
        R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "pairs",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[{}]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 0.9, "output_efficiency": 0.9,
                        "emin": 0, "emax": 15,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 15, "daily_cycle": false,
                        "use_state_variable": false}}]}}
}})",
        options(),
        blocks,
        n,
        gcost);
  };
  const auto two = solve_stage<BatteryLP>(make(
      R"({"uid": 1, "duration": 2}, {"uid": 2, "duration": 2})", "1, 100", 2));
  const auto one = solve_stage<BatteryLP>(
      make(R"({"uid": 1, "duration": 1}, {"uid": 2, "duration": 1},
              {"uid": 3, "duration": 1}, {"uid": 4, "duration": 1})",
           "1, 1, 100, 100",
           4));
  // 15 cell MWh bought at 1/0.9, 13.5 MWh displace 100 $/MWh:
  //   20·1 + 15/0.9 + (20 − 13.5)·100 = 686.67
  CHECK(two.obj == doctest::Approx(20.0 + (15.0 / 0.9) + 650.0));
  CHECK(one.obj == doctest::Approx(two.obj));
  REQUIRE(two.soc.size() == 2);
  REQUIRE(one.soc.size() == 4);
  CHECK(one.soc[1] == doctest::Approx(two.soc[0]));
  CHECK(one.soc[3] == doctest::Approx(two.soc[1]));
}

TEST_CASE(  // NOLINT
    "Mixed durations - cycle group on period_hours stages")
{
  using namespace period_hours_test;
  // Weekend / weekday days of 8 h + 16 h real blocks, weighted by the
  // days represented (2 and 5): durations 16, 32 and 40, 80;
  // period_hours 24; one cycle group.  Same energy as the single-block
  // test in test_storage_cycle_group.cpp: 1200 MWh moved from the
  // weekend (10 $/MWh) to the weekdays ⇒ 12000; with emax 1000 the
  // last-repetition bound caps the move at 1000 MWh ⇒ 30000.
  const auto make = [](double emax)
  {
    return std::format(
        R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 16}}, {{"uid": 2, "duration": 32}},
                    {{"uid": 3, "duration": 40}}, {{"uid": 4, "duration": 80}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 2,
                      "period_hours": 24, "cycle_group": 1}},
                    {{"uid": 2, "first_block": 2, "count_block": 2,
                      "period_hours": 24, "cycle_group": 1}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "group_mixed",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[0, 0], [10, 10]]}}],
    "generator_array": [
      {{"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10,
        "pmax": [[100, 100], [0, 0]], "capacity": 100}},
      {{"uid": 2, "name": "dear", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": {}, "eini": 0,
                        "pmax_charge": 1000, "pmax_discharge": 1000,
                        "capacity": {}}}]}}
}})",
        options(),
        emax,
        emax);
  };
  CHECK(solve_obj(make(2000)) == doctest::Approx(12000.0));
  CHECK(solve_obj(make(1000)) == doctest::Approx(30000.0));
}

// ---------------------------------------------------------------------
// Written duals on mixed / weighted durations are per MWh
// ---------------------------------------------------------------------

namespace period_hours_test
{
namespace
{

/// (block uid, element uid) → value of a long-layout output table.
using BlockValues = std::map<std::pair<int, int>, double>;

[[nodiscard]] BlockValues read_block_values(const std::filesystem::path& path)
{
  BlockValues out;
  auto tbl = csv_read_table(path);
  if (!tbl.has_value()) {
    auto sharded = path;
    sharded += "_s0_p0";
    tbl = csv_read_table(sharded);
  }
  REQUIRE(tbl.has_value());
  const auto& table = *tbl;
  const auto blk = table->GetColumnByName("block")->chunk(0);
  const auto uid = table->GetColumnByName("uid")->chunk(0);
  const auto val = table->GetColumnByName("value")->chunk(0);
  for (int64_t i = 0; i < val->length(); ++i) {
    out[{static_cast<int>(test_helpers::chunk_value_at(blk, i)),
         static_cast<int>(test_helpers::chunk_value_at(uid, i))}] =
        test_helpers::chunk_value_at(val, i);
  }
  return out;
}

/// Solve, write csv output into a fresh directory, return it.
[[nodiscard]] std::filesystem::path solve_and_write(const std::string& json,
                                                    std::string_view tag)
{
  const auto dir = std::filesystem::temp_directory_path()
      / std::format("gtopt_period_duals_{}", tag);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  Planning base = parse_planning_json(json);
  base.options.output_directory = dir.string();
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  plp.write_out();
  return dir;
}

[[nodiscard]] std::string dual_options(std::string_view model_extra = "")
{
  return std::format(
      R"("options": {{"annual_discount_rate": 0.0, "output_format": "csv",
                 "output_compression": "uncompressed", "write_out": "all",
                 "model_options": {{"demand_fail_cost": 10000, "battery_annual_standing_loss": 0,
                                    "scale_objective": 1 {}}}}})",
      model_extra);
}

// period of 7 real hours (1, 2, 0.5, 3.5 h) repeated 3 times
constexpr std::string_view kWeightedBlocks =
    R"({"uid": 1, "duration": 3}, {"uid": 2, "duration": 6},
       {"uid": 3, "duration": 1.5}, {"uid": 4, "duration": 10.5})";

}  // namespace
}  // namespace period_hours_test

TEST_CASE(  // NOLINT
    "Period stage duals - bus price, UC and water value per MWh")
{
  using namespace period_hours_test;
  // Single bus, weighted blocks (period 7 h × 3).  `g1` 10 $/MWh capped
  // at 8 MW by a block user constraint, `g2` costs 20 / 40 / 30 / 50
  // $/MWh per block; load 10 MW.
  //   bus price      = g2 cost:              20, 40, 30, 50
  //   UC dual (g1≤8) = price − 10:           10, 30, 20, 40
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "period_hours": 7}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "period_duals",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": [[20, 40, 30, 50]],
        "capacity": 100}}],
    "user_constraint_array": [{{"uid": 1, "name": "cap_g1",
        "expression": "generator(\"g1\").generation <= 8"}}]}}
}})",
      dual_options(R"(, "use_single_bus": true)"),
      kWeightedBlocks);
  const auto dir = solve_and_write(json, "bus_uc");
  const auto lmp = read_block_values(dir / "Bus" / "balance_dual");
  const std::array<double, 4> price {20.0, 40.0, 30.0, 50.0};
  for (int b = 1; b <= 4; ++b) {
    CAPTURE(b);
    CHECK(std::abs(lmp.at({b, 1})) == doctest::Approx(price[b - 1]));
  }
  const auto ucd =
      read_block_values(dir / "UserConstraint" / "constraint_dual");
  for (int b = 1; b <= 4; ++b) {
    CAPTURE(b);
    CHECK(std::abs(ucd.at({b, 1})) == doctest::Approx(price[b - 1] - 10.0));
  }
  std::filesystem::remove_all(dir);
}

TEST_CASE(  // NOLINT
    "Period stage duals - congested line price difference")
{
  using namespace period_hours_test;
  // b1 (cheap 10 $/MWh) → b2 (load 20 MW, dear 50 $/MWh), line 5 MW:
  // prices 10 and 50 in every block, difference 40 = line congestion rent.
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "period_hours": 7}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "period_line",
    "bus_array": [{{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}}],
    "demand_array": [{{"uid": 1, "name": "d2", "bus": "b2", "lmax": 20}}],
    "generator_array": [
      {{"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10, "capacity": 100}},
      {{"uid": 2, "name": "dear", "bus": "b2", "gcost": 50, "capacity": 100}}],
    "line_array": [{{"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
                     "tmax_ab": 5, "tmax_ba": 5}}]}}
}})",
      dual_options(R"(, "use_single_bus": false, "use_kirchhoff": false)"),
      kWeightedBlocks);
  const auto dir = solve_and_write(json, "line");
  const auto lmp = read_block_values(dir / "Bus" / "balance_dual");
  for (int b = 1; b <= 4; ++b) {
    CAPTURE(b);
    CHECK(std::abs(lmp.at({b, 1})) == doctest::Approx(10.0));
    CHECK(std::abs(lmp.at({b, 2})) == doctest::Approx(50.0));
  }
  std::filesystem::remove_all(dir);
}

TEST_CASE(  // NOLINT
    "Period stage duals - daily_cycle battery energy value per MWh")
{
  using namespace period_hours_test;
  // Weighted period (1, 2, 0.5, 3.5 h × 3), prices 10 / 100 / 10 / 100,
  // battery 5 MWh η = 1, charge ≤ 100 MW, closed over the period: the
  // stored MWh is worth the discharge price in the dear blocks and is
  // bought at 10 in the cheap ones; with the battery full at the end of
  // a cheap block and binding emax, the energy value lies in [10, 100].
  // Equal-cost check: the reported value at block 2 (discharging, level
  // above 0 and below emax) equals the 100 $/MWh it displaces.
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "period_hours": 7}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "period_bat",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": [[10, 100, 10, 100]], "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": 5,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 5, "daily_cycle": true}}]}}
}})",
      dual_options(R"(, "use_single_bus": true)"),
      kWeightedBlocks);
  const auto dir = solve_and_write(json, "bat");
  const auto val = read_block_values(dir / "Battery" / "energy_dual");
  // block 2 discharges 5 MWh over its 2 real hours (2.5 MW < 10 MW load):
  // interior level ⇒ the energy value equals the displaced price 100.
  CHECK(std::abs(val.at({2, 1})) == doctest::Approx(100.0));
  std::filesystem::remove_all(dir);
}
