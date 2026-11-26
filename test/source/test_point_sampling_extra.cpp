// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_point_sampling_extra.cpp
 * @brief     Point-sampled stages: JSON round trip, backward
 *            compatibility, edge cases, point duals, block_time output
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cmath>
#include <filesystem>
#include <format>
#include <fstream>
#include <map>
#include <numbers>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/json/json_block.hpp>
#include <gtopt/json/json_model_options.hpp>
#include <gtopt/json/json_stage.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

#include "test_csv_helpers.hpp"

using namespace gtopt;

namespace point_extra_test
{
namespace
{

/// Lobatto nodes t_k = T/2·(1 − cos(πk/n)) with Clenshaw–Curtis weights.
struct Grid
{
  std::vector<double> t {};
  std::vector<double> w {};
};

[[nodiscard]] Grid lobatto_cc(int n, double period)
{
  Grid g;
  for (int k = 0; k <= n; ++k) {
    const double theta = std::numbers::pi * k / n;
    g.t.push_back(0.5 * period * (1.0 - std::cos(theta)));
    double s = 0.0;
    for (int j = 1; j <= n / 2; ++j) {
      const double b = (2 * j == n) ? 1.0 : 2.0;
      s += b / ((4.0 * j * j) - 1.0) * std::cos(2.0 * j * theta);
    }
    const double c = (k == 0 || k == n) ? 1.0 : 2.0;
    g.w.push_back(c / n * (1.0 - s) * 0.5 * period);
  }
  return g;
}

[[nodiscard]] std::string blocks_json(const Grid& g, int uid0 = 1)
{
  std::string out;
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    out +=
        std::format(R"({}{{"uid": {}, "duration": {:.17g}, "time": {:.17g}}})",
                    k == 0 ? "" : ", ",
                    static_cast<int>(k) + uid0,
                    g.w[k],
                    g.t[k]);
  }
  return out;
}

[[nodiscard]] std::string list(const std::vector<double>& v)
{
  std::string out;
  for (std::size_t k = 0; k < v.size(); ++k) {
    out += std::format("{}{:.17g}", k == 0 ? "" : ", ", v[k]);
  }
  return out;
}

/// One day, cheap energy before noon (10 $/MWh, 100 MW), 50 MW load
/// after noon served by `dear` (100 $/MWh) or the battery.
[[nodiscard]] std::string battery_day(const Grid& g,
                                      std::string_view sampling,
                                      std::string_view integration,
                                      double emax)
{
  std::vector<double> lmax;
  std::vector<double> pmax;
  for (const double t : g.t) {
    lmax.push_back(t < 12.0 ? 0.0 : 50.0);
    pmax.push_back(t < 12.0 ? 100.0 : 0.0);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1
                                  {}}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {} {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "point_extra",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10,
        "pmax": [[{}]], "capacity": 100}},
      {{"uid": 2, "name": "dear", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": {}, "eini": 0, "efin": 0,
                        "daily_cycle": false, "use_state_variable": true,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": {}}}]}}
}})",
      integration.empty()
          ? std::string {}
          : std::format(R"(, "storage_integration": "{}")", integration),
      blocks_json(g),
      g.t.size(),
      sampling,
      list(lmax),
      list(pmax),
      emax,
      emax);
}

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

constexpr std::string_view kPoint = R"(, "sampling": "point")";

}  // namespace
}  // namespace point_extra_test

TEST_CASE(  // NOLINT
    "Point sampling - JSON round trip of sampling, time, integration")
{
  Stage st;
  st.uid = 3;
  st.first_block = 0;
  st.count_block = 2;
  st.sampling = "point";
  const auto st_json = daw::json::to_json(st);
  const auto st2 = daw::json::from_json<Stage>(st_json);
  CHECK(st2.sampling.value_or("") == "point");
  CHECK(st_json.find(R"("sampling")") != std::string::npos);

  Block blk;
  blk.uid = 7;
  blk.duration = 0.25;
  blk.time = 12.5;
  const auto b_json = daw::json::to_json(blk);
  const auto b2 = daw::json::from_json<Block>(b_json);
  CHECK(b2.time.value_or(-1.0) == doctest::Approx(12.5));
  CHECK(b2.duration == doctest::Approx(0.25));

  // a block without time keeps it unset
  Block plain;
  plain.uid = 1;
  plain.duration = 1.0;
  const auto p2 = daw::json::from_json<Block>(daw::json::to_json(plain));
  CHECK_FALSE(p2.time.has_value());

  ModelOptions mo;
  mo.storage_integration = "lg4";
  const auto mo2 = daw::json::from_json<ModelOptions>(daw::json::to_json(mo));
  CHECK(mo2.storage_integration.value_or("") == "lg4");
}

TEST_CASE(  // NOLINT
    "Point sampling - block stages ignore storage_integration")
{
  using namespace point_extra_test;
  // backward compatibility: on a block stage every integration option
  // gives the classic block recursion (identical objective)
  const auto g = lobatto_cc(16, 24.0);
  const double ref = solve_obj(battery_day(g, "", "", 100.0));
  for (const auto* mode : {"block", "trapezoid", "lg4", "lg6", "lg4_pos"}) {
    CAPTURE(mode);
    CHECK(solve_obj(battery_day(g, "", mode, 100.0))
          == doctest::Approx(ref).epsilon(1e-12));
  }
  // and a point stage with storage_integration = block reproduces it
  CHECK(solve_obj(battery_day(g, kPoint, "block", 100.0))
        == doctest::Approx(ref).epsilon(1e-12));
}

TEST_CASE(  // NOLINT
    "Point sampling - edge sizes: 1 and 2 nodes, retired battery")
{
  using namespace point_extra_test;
  // 2 nodes (t = 0, 24; trapezoid weights 12, 12): banded rows on the
  // smallest grid; the battery can only move energy from node 0 to node 1
  {
    Grid g {.t = {0.0, 24.0}, .w = {12.0, 12.0}};
    // t_0 < 12 is "morning" (cheap, no load); t_1 = 24 has the load
    const double block = solve_obj(battery_day(g, kPoint, "block", 100.0));
    const double trap = solve_obj(battery_day(g, kPoint, "trapezoid", 100.0));
    CHECK(std::isfinite(block));
    CHECK(std::isfinite(trap));
    // the load (50 MW × 12 h = 600 MWh) costs at most dear energy
    CHECK(trap <= (600.0 * 100.0) + 1e-6);
  }
  // 1 node: banded integration needs ≥ 2 nodes ⇒ block recursion
  {
    Grid g {.t = {12.0}, .w = {24.0}};
    const double obj = solve_obj(battery_day(g, kPoint, "lg4", 100.0));
    // t = 12 ⇒ load 50 MW × 24 h, no cheap energy, battery empty
    CHECK(obj == doctest::Approx(50.0 * 24.0 * 100.0));
  }
  // retired battery (emax = 0): the envelope rows stay feasible and the
  // battery is idle — same as no storage
  {
    const auto g = lobatto_cc(12, 24.0);
    double evening = 0.0;
    for (std::size_t k = 0; k < g.t.size(); ++k) {
      evening += g.t[k] < 12.0 ? 0.0 : g.w[k];
    }
    for (const auto* mode : {"trapezoid", "lg4", "lg6_pos"}) {
      CAPTURE(mode);
      CHECK(solve_obj(battery_day(g, kPoint, mode, 0.0))
            == doctest::Approx(50.0 * evening * 100.0));
    }
  }
}

TEST_CASE(  // NOLINT
    "Point sampling - bus dual ÷ weight is the price AT the node")
{
  using namespace point_extra_test;
  // Single bus, Lobatto N = 8 day; the marginal unit's cost varies per
  // node (c_k = 20 + k): the balance dual of node k is c_k·w_k, written
  // out ÷ w_k ⇒ the price at t_k equals c_k whatever the weight.
  const auto g = lobatto_cc(8, 24.0);
  std::vector<double> cost;
  cost.reserve(g.t.size());
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    cost.push_back(20.0 + static_cast<double>(k));
  }
  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_point_duals_test";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  const auto json = std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "output_compression": "uncompressed", "write_out": "all",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "sampling": "point"}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "point_duals",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 10}}],
    "generator_array": [
      {{"uid": 1, "name": "g", "bus": "b1", "gcost": [[{}]],
        "capacity": 100}}]}}
}})",
      blocks_json(g),
      g.t.size(),
      list(cost));
  Planning base = parse_planning_json(json);
  base.options.output_directory = dir.string();
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  plp.write_out();
  auto tbl = csv_read_table(dir / "Bus" / "balance_dual");
  if (!tbl.has_value()) {
    tbl = csv_read_table(dir / "Bus" / "balance_dual_s0_p0");
  }
  REQUIRE(tbl.has_value());
  const auto& table = *tbl;
  const auto blk = table->GetColumnByName("block")->chunk(0);
  const auto val = table->GetColumnByName("value")->chunk(0);
  std::map<int, double> price;
  for (int64_t i = 0; i < val->length(); ++i) {
    price[static_cast<int>(test_helpers::chunk_value_at(blk, i))] =
        test_helpers::chunk_value_at(val, i);
  }
  REQUIRE(price.size() == g.t.size());
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    CAPTURE(k);
    CHECK(std::abs(price.at(static_cast<int>(k) + 1))
          == doctest::Approx(cost[k]));
  }
  std::filesystem::remove_all(dir);
}

TEST_CASE(  // NOLINT
    "Point sampling - gtopt_main writes block_time.csv and validates cells")
{
  using namespace point_extra_test;
  const auto g = lobatto_cc(4, 24.0);
  const auto tmp = std::filesystem::temp_directory_path();
  const auto write = [&](std::string_view name, const std::string& json)
  {
    auto path = tmp / std::string(name);
    path.replace_extension(".json");
    std::ofstream(path) << json;
    return path.replace_extension("");
  };
  const auto out_dir = tmp / "gtopt_point_block_time_out";
  std::filesystem::remove_all(out_dir);
  const auto stem =
      write("gtopt_point_block_time", battery_day(g, kPoint, "", 100.0));
  auto ok = gtopt_main(MainOptions {
      .planning_files = {stem.string()},
      .output_directory = out_dir.string(),
  });
  REQUIRE(ok.has_value());
  CHECK(*ok == 0);
  std::ifstream bt(out_dir / "block_time.csv");
  REQUIRE(bt.good());
  std::string header;
  std::getline(bt, header);
  CHECK(header == "stage,block,sampling,time,duration");
  std::vector<std::string> rows;
  for (std::string line; std::getline(bt, line);) {
    rows.push_back(line);
  }
  REQUIRE(rows.size() == g.t.size());
  CHECK(rows.front().starts_with("1,1,point,0,"));
  CHECK(rows.back().starts_with("1,5,point,24,"));

  // a node outside its cell (t swapped onto the wrong side) is rejected
  Grid bad = g;
  bad.t[1] = 20.0;
  bad.t[2] = 21.0;
  bad.t[3] = 22.0;
  const auto bad_stem =
      write("gtopt_point_bad_cells", battery_day(bad, kPoint, "", 100.0));
  auto err = gtopt_main(MainOptions {
      .planning_files = {bad_stem.string()},
      .output_directory = (tmp / "gtopt_point_bad_cells_out").string(),
  });
  CHECK_FALSE((err.has_value() && *err == 0));
  std::filesystem::remove_all(out_dir);
}

TEST_CASE(  // NOLINT
    "Point sampling - SDDP over phases of several point stages")
{
  using namespace point_extra_test;
  // 4 point days (Lobatto N = 8), 2 phases × 2 stages.  Day 1 has cheap
  // energy only, days 2–4 a 10 MW load; the battery (200 MWh) carries
  // energy across stage and phase boundaries: the level at t_N of one
  // stage is the initial level of the next.  SDDP must reach the
  // monolithic optimum.
  const auto g = lobatto_cc(8, 24.0);
  const auto n = g.t.size();
  const auto make = [&](bool phases)
  {
    std::string blocks;
    std::string stages;
    std::string lmax;
    std::string pmax;
    for (std::size_t d = 0; d < 4; ++d) {
      blocks +=
          (d == 0 ? "" : ", ") + blocks_json(g, static_cast<int>(d * n) + 1);
      stages += std::format(
          R"({}{{"uid": {}, "first_block": {}, "count_block": {},
              "sampling": "point"}})",
          d == 0 ? "" : ", ",
          d + 1,
          d * n,
          n);
      lmax += std::format("{}[{}]",
                          d == 0 ? "" : ", ",
                          list(std::vector<double>(n, d == 0 ? 0.0 : 10.0)));
      pmax += std::format("{}[{}]",
                          d == 0 ? "" : ", ",
                          list(std::vector<double>(n, d == 0 ? 100.0 : 0.0)));
    }
    return std::format(
        R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{}],
    {}
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "point_sddp",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [{}]}}],
    "generator_array": [
      {{"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10,
        "pmax": [{}], "capacity": 100}},
      {{"uid": 2, "name": "dear", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": 200, "eini": 0,
                        "daily_cycle": false, "use_state_variable": true,
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 200}}]}}
}})",
        blocks,
        stages,
        phases ? R"("phase_array": [{"uid": 1, "first_stage": 0,
                                     "count_stage": 2},
                                    {"uid": 2, "first_stage": 2,
                                     "count_stage": 2}],)"
               : "",
        lmax,
        pmax);
  };
  // monolithic: 200 MWh carried (cost 10) + 3 days × 240 MWh − 200 dear
  const double mono = solve_obj(make(false));
  CHECK(mono == doctest::Approx((200.0 * 10.0) + ((720.0 - 200.0) * 100.0)));

  Planning sddp_p;
  sddp_p.merge(parse_planning_json(make(true)));
  PlanningLP plp(std::move(sddp_p));
  SDDPOptions opts;
  opts.max_iterations = 10;
  opts.convergence_tol = 1e-6;
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  CHECK(results->back().upper_bound == doctest::Approx(mono).epsilon(1e-6));
  CHECK(results->back().lower_bound == doctest::Approx(mono).epsilon(1e-6));
}
