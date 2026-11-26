// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_point_sampling.cpp
 * @brief     `Stage.sampling = "point"` vs the classic block semantics
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Block stages hold a value constant over the block (length = real
 * step).  Point stages carry samples AT `Block.time` (Chebyshev–Lobatto
 * nodes: t_0 = period start, t_N = period end) and the durations are
 * quadrature weights.  Each test builds the same blocks with both
 * semantics and checks that they give different answers.
 */

#include <cmath>
#include <format>
#include <numbers>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace point_sampling_test
{
namespace
{

[[nodiscard]] std::string options(std::string_view extra = "")
{
  return std::format(
      R"("options": {{"annual_discount_rate": 0.0, "output_format": "csv",
                 "model_options": {{"use_single_bus": true,
                                    "demand_fail_cost": 10000,
                                    "scale_objective": 1 {}}}}})",
      extra);
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

/// g1 (1 $/MWh, ramp 10 MW/h from 0) and g2 (100 $/MWh), loads 0/100/100
/// on nodes t = 0, 1, 3 with trapezoid weights 0.5, 1.5, 1 (cells
/// [0, 0.5], [0.5, 2], [2, 3] hold their nodes).
[[nodiscard]] std::string ramp_case(std::string_view model_extra,
                                    std::string_view sampling)
{
  return std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 0.5, "time": 0}},
                    {{"uid": 2, "duration": 1.5, "time": 1}},
                    {{"uid": 3, "duration": 1, "time": 3}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 3,
                      "chronological": true {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "ramp_point",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[0, 100, 100]]}}],
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
      sampling);
}

constexpr std::string_view kPoint = R"(, "sampling": "point")";

}  // namespace
}  // namespace point_sampling_test

TEST_CASE(  // NOLINT
    "Point sampling - backward ramp uses the node spacing")
{
  using namespace point_sampling_test;
  // Point: steps t_k − t_{k−1} = (1 first), 1, 2 ⇒ p0 ≤ 10, p1 ≤ p0 + 10,
  // p2 ≤ p1 + 20 (P0 = 0, no load): P1 = 10, P2 = 30:
  //   cost = (1.5·10 + 1·30)·1 + (1.5·90 + 1·70)·100 = 45 + 20500 = 20545
  // Block: steps = durations 0.5, 1.5, 1 ⇒ P1 ≤ 15, P2 ≤ 25:
  //   cost = (1.5·15 + 25) + (1.5·85 + 75)·100 = 47.5 + 20250 = 20297.5
  const auto* const backward = R"(, "ramp_formula": "backward")";
  CHECK(solve_obj(ramp_case(backward, kPoint)) == doctest::Approx(20545.0));
  CHECK(solve_obj(ramp_case(backward, "")) == doctest::Approx(20297.5));
}

TEST_CASE(  // NOLINT
    "Point sampling - backward is the default ramp formula")
{
  using namespace point_sampling_test;
  // default (backward interval slopes on t = 0, 1, 3) and central2
  // (first (P1 − P0)/1 ≤ 10, middle (P2 + 3P1 − 4P0)/6 ≤ 10, last
  // (P2 − P1)/2 ≤ 10) both give P1 = 10, P2 = 30 here: 20545; the block
  // stage (backward on the durations) is 20297.5.
  CHECK(solve_obj(ramp_case("", kPoint)) == doctest::Approx(20545.0));
  CHECK(solve_obj(ramp_case(R"(, "ramp_formula": "central2")", kPoint))
        == doctest::Approx(20545.0));
  CHECK(solve_obj(ramp_case("", "")) == doctest::Approx(20297.5));
}

TEST_CASE(  // NOLINT
    "Point sampling - wrapped central2 on a periodic N-node grid")
{
  using namespace point_sampling_test;
  // Periodic nodes t = 0, 1, 3 with period 4 (the end instant t = 4 ≡
  // t_0 is not a node; node 0 carries its weight).  The wrapped
  // neighbours are t_2 − 4 (for t_0) and t_0 + 4 (for t_2).
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1.0, "time": 0}},
                    {{"uid": 2, "duration": 1.5, "time": 1}},
                    {{"uid": 3, "duration": 1.5, "time": 3}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 3,
                      "chronological": true, "period_hours": 4,
                      "sampling": "point"}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "wrap_point",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[50, 100, 100]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "ramp_up": 10, "ramp_down": 10,
                           "initial_status": 1, "initial_power": 50,
                           "relax": true, "must_run": true}}]}}
}})",
      options(R"(, "ramp_formula": "central2", "ramp_wrap": true)"));
  const double obj = solve_obj(json);
  CHECK(std::isfinite(obj));
  // Feasible with g1 flat at 50 MW (all derivatives 0); g2 covers the
  // 50 MW gap at the two later nodes (weights 1.5 + 1.5):
  //   upper bound = (1 + 1.5 + 1.5)·50·1 + 3·50·100
  CHECK(obj <= (200.0 + 15000.0) + 1e-6);
}

TEST_CASE(  // NOLINT
    "Point sampling - min up time: block cells vs the uniform 1 h UC grid")
{
  using namespace point_sampling_test;
  // Weights 0.5, 1, 2.5, 2 at t = 0, 1, 2, 6.  g1: 1 $/MWh + no-load
  // 1000 $/h (billed by the quadrature weights), initially off; g2:
  // 100 $/MWh.  Load 50 MW at the first block only.
  // Block stage: min up counts the block durations
  //   MUT 2 h: on for blocks 0, 1, 2 (0.5 + 1 < 2 ≤ 4): 25 + 4000 > 2500
  //            ⇒ g2 serves: 2500
  //   MUT 1 h: blocks 0, 1 (0.5 < 1 ≤ 1.5): 25 + 1000·1.5 = 1525
  // Point stage: the status lives on the uniform 1 h grid, the nodes
  // fall in cells 0, 1, 2, 5 (cells 3, 4 hold no node), min up counts
  // cell hours:
  //   MUT 2 h: cells 0, 1 = 2 h ⇒ nodes 0, 1 on: 25 + 1000·1.5 = 1525
  //   MUT 1 h: cell 0 ⇒ node 0 on: 25 + 1000·0.5 = 525
  const auto make =
      [](std::string_view sampling, double mut, std::string_view grid = "")
  {
    return std::format(
        R"({{ {},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 0.5, "time": 0}},
                    {{"uid": 2, "duration": 1, "time": 1}},
                    {{"uid": 3, "duration": 2.5, "time": 2}},
                    {{"uid": 4, "duration": 2, "time": 6}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "chronological": true {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "minup_point",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[50, 0, 0, 0]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "noload_cost": 1000, "min_up_time": {},
                           "initial_status": 0, "initial_hours": 100,
                           "relax": false{}}}]}}
}})",
        options(),
        sampling,
        mut,
        grid);
  };
  CHECK(solve_obj(make({}, 2.0)) == doctest::Approx(2500.0));
  CHECK(solve_obj(make({}, 1.0)) == doctest::Approx(1525.0));
  CHECK(solve_obj(make(point_sampling_test::kPoint, 2.0))
        == doctest::Approx(1525.0));
  CHECK(solve_obj(make(point_sampling_test::kPoint, 1.0))
        == doctest::Approx(525.0));
  // previous behaviour, one status per node (commitment_grid = "block"):
  // min up counts the node cells like a block stage
  constexpr std::string_view kPerNode = R"(, "commitment_grid": "block")";
  CHECK(solve_obj(make(point_sampling_test::kPoint, 2.0, kPerNode))
        == doctest::Approx(2500.0));
  CHECK(solve_obj(make(point_sampling_test::kPoint, 1.0, kPerNode))
        == doctest::Approx(1525.0));
}

TEST_CASE(  // NOLINT
    "Point sampling - storage level of a linear discharge at the nodes")
{
  using namespace point_sampling_test;
  // Lobatto nodes on a 24 h day (N = 8) with Clenshaw–Curtis weights; a
  // battery (η = 1) is the only source of a load ramping linearly
  // 10 → 34 MW.  eini = the exact energy 528 MWh, so the true level is
  // E(t) = 528 − (10 t + t²/2) AT the node times.  Point stages
  // (trapezoid) reproduce E(t_k) closely; the block recursion reports the
  // level at the block END (Σ w), which differs by hours of discharge.
  const int n = 8;
  const double period = 24.0;
  std::vector<double> t;
  std::vector<double> w;
  for (int k = 0; k <= n; ++k) {
    const double theta = std::numbers::pi * k / n;
    t.push_back(0.5 * period * (1.0 - std::cos(theta)));
    double s = 0.0;
    for (int j = 1; j <= n / 2; ++j) {
      const double b = (2 * j == n) ? 1.0 : 2.0;
      s += b / ((4.0 * j * j) - 1.0) * std::cos(2.0 * j * theta);
    }
    const double c = (k == 0 || k == n) ? 1.0 : 2.0;
    w.push_back(c / n * (1.0 - s) * 0.5 * period);
  }
  std::string blocks;
  std::string lmax;
  for (int k = 0; k <= n; ++k) {
    blocks +=
        std::format(R"({}{{"uid": {}, "duration": {:.17g}, "time": {:.17g}}})",
                    k == 0 ? "" : ", ",
                    k + 1,
                    w[k],
                    t[k]);
    lmax += std::format("{}{:.17g}", k == 0 ? "" : ", ", 10.0 + t[k]);
  }
  const double energy = (10.0 * period) + (0.5 * period * period);
  const auto make = [&](std::string_view sampling)
  {
    return std::format(
        R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {} {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "ramp_storage",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": 1000, "eini": {:.17g},
                        "daily_cycle": false, "use_state_variable": true,
                        "storage_integration": "trapezoid",
                        "pmax_charge": 100, "pmax_discharge": 100,
                        "capacity": 1000}}]}}
}})",
        options(),
        blocks,
        n + 1,
        sampling,
        lmax,
        energy);
  };
  const auto levels = [&](std::string_view sampling)
  {
    Planning base;
    base.merge(parse_planning_json(make(sampling)));
    PlanningLP plp(std::move(base));
    auto res = plp.resolve();
    REQUIRE(res.has_value());
    auto& sys = plp.systems().front().front();
    const auto sol = sys.linear_interface().get_col_sol();
    const auto& bat = sys.elements<BatteryLP>().front();
    std::vector<double> out;
    for (const auto& [buid, col] : bat.energy_cols_at(
             sys.scene().scenarios().front(), sys.phase().stages().front()))
    {
      out.push_back(bat.to_physical(sol[col]));
    }
    return out;
  };
  const auto exact = [&](double tt)
  { return energy - ((10.0 * tt) + (0.5 * tt * tt)); };

  const auto point = levels(point_sampling_test::kPoint);
  const auto block = levels("");
  REQUIRE(point.size() == t.size());
  double err_point = 0.0;
  double err_block = 0.0;
  for (std::size_t k = 0; k < t.size(); ++k) {
    err_point = std::max(err_point, std::abs(point[k] - exact(t[k])));
    err_block = std::max(err_block, std::abs(block[k] - exact(t[k])));
  }
  MESSAGE(
      std::format("level error at the nodes: point {:.3e} MWh, "
                  "block {:.3e} MWh",
                  err_point,
                  err_block));
  // both close the day exactly (CC total)
  CHECK(point.back() == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(block.back() == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(err_point < 0.03 * energy);
  CHECK(err_point < 0.2 * err_block);
}

TEST_CASE(  // NOLINT
    "Point sampling - validation of sampling and node times")
{
  const auto make = [](std::string_view sampling, std::string_view blocks)
  {
    return std::format(
        R"({{
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 2,
                      "sampling": "{}"}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "v", "bus_array": [{{"uid": 1, "name": "b1"}}]}}
}})",
        blocks,
        sampling);
  };
  const auto errors = [&](std::string_view sampling, std::string_view blocks)
  {
    Planning p;
    p.merge(parse_planning_json(make(sampling, blocks)));
    return validate_planning(p).errors;
  };
  const auto has = [](const auto& errs, std::string_view what)
  {
    return std::ranges::any_of(
        errs, [&](const auto& e) { return e.find(what) != std::string::npos; });
  };
  constexpr std::string_view timed =
      R"({"uid": 1, "duration": 1, "time": 0}, {"uid": 2, "duration": 1, "time": 2})";
  constexpr std::string_view untimed =
      R"({"uid": 1, "duration": 1}, {"uid": 2, "duration": 1})";
  CHECK_FALSE(has(errors("point", timed), "sampling"));
  CHECK(has(errors("point", untimed), "Block.time"));
  CHECK(has(errors("steps", timed), "must be 'block' or 'point'"));
  CHECK_FALSE(has(errors("block", untimed), "sampling"));
  // decreasing node times
  CHECK(has(errors("point",
                   R"({"uid": 1, "duration": 1, "time": 1},
                      {"uid": 2, "duration": 1, "time": 0.5})"),
            "strictly increasing"));
  // node outside its cell: cells [0, 1], [1, 2] but t_0 = 1.5
  CHECK(has(errors("point",
                   R"({"uid": 1, "duration": 1, "time": 1.5},
                      {"uid": 2, "duration": 1, "time": 1.8})"),
            "outside its cell"));
  // any weights whose cells hold their nodes are valid (t = 0, 2 with
  // weights 0.2, 1.8 ⇒ cells [0, .2], [.2, 2])
  CHECK_FALSE(has(errors("point",
                         R"({"uid": 1, "duration": 0.2, "time": 0},
                            {"uid": 2, "duration": 1.8, "time": 2})"),
                  "outside"));
}

TEST_CASE(  // NOLINT
    "Point sampling - central2 rows are the paper's diffd_op stencil")
{
  // Reference: tcheby ChebyLib::diffd_op (the code of Matus et al.,
  // IEEE PES GM 2015): on nodes x, interior rows
  //   D(i, i−1) = −h₊ / (h₋ (h₊ + h₋)),  D(i, i) = (h₊ − h₋) / (h₊ h₋),
  //   D(i, i+1) =  h₋ / (h₊ (h₊ + h₋)),
  // h₊ = x_{i+1} − x_i, h₋ = x_i − x_{i−1}; first / last rows the forward
  // / backward difference.  gtopt writes each ramp row scaled by h_k
  // (h₋, h₊ at the first node), so the P coefficients must be h_k·D(k, ·).
  // Chebyshev–Lobatto nodes over 24 h, N = 8 (9 nodes, non-periodic).
  constexpr int n = 8;
  constexpr double period = 24.0;
  std::vector<double> t;
  for (int k = 0; k <= n; ++k) {
    t.push_back(0.5 * period * (1.0 - std::cos(std::numbers::pi * k / n)));
  }
  const auto m = t.size();
  // paper's diffd_op
  std::vector<std::vector<double>> d(m, std::vector<double>(m, 0.0));
  for (std::size_t i = 1; i + 1 < m; ++i) {
    const double h1 = t[i + 1] - t[i];
    const double h2 = t[i] - t[i - 1];
    d[i][i - 1] = -h1 / (h2 * (h1 + h2));
    d[i][i] = (h1 - h2) / (h1 * h2);
    d[i][i + 1] = h2 / (h1 * (h1 + h2));
  }
  d[0][0] = -1.0 / (t[1] - t[0]);
  d[0][1] = 1.0 / (t[1] - t[0]);
  d[m - 1][m - 2] = -1.0 / (t[m - 1] - t[m - 2]);
  d[m - 1][m - 1] = 1.0 / (t[m - 1] - t[m - 2]);
  // the interior stencil differentiates quadratics exactly
  for (std::size_t i = 1; i + 1 < m; ++i) {
    double dq = 0.0;
    for (std::size_t j = 0; j < m; ++j) {
      dq += d[i][j] * t[j] * t[j];
    }
    CHECK(dq == doctest::Approx(2.0 * t[i]));
  }

  std::string blocks;
  std::string load;
  for (std::size_t k = 0; k < m; ++k) {
    blocks += std::format(R"({}{{"uid": {}, "duration": 1, "time": {:.17g}}})",
                          k == 0 ? "" : ", ",
                          k + 1,
                          t[k]);
    load += std::format("{}{}", k == 0 ? "" : ", ", 50 + (5 * k));
  }
  const auto json = std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true, "sampling": "point"}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "central2_paper",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "ramp_up": 10, "ramp_down": 10,
                           "initial_status": 1, "initial_power": 50,
                           "relax": true}}]}}
}})",
      point_sampling_test::options(R"(, "ramp_formula": "central2")"),
      blocks,
      m,
      load);
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  REQUIRE(plp.resolve().value_or(0) == 1);
  auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto& gen = sys.elements<GeneratorLP>().front();
  const auto& pcols = gen.generation_cols_at(scenario, stage);
  const auto* rows =
      sys.elements<CommitmentLP>().front().find_ramp_up_rows(scenario, stage);
  REQUIRE(rows != nullptr);
  REQUIRE(rows->size() == m);
  std::vector<ColIndex> pcol;
  for (const auto& [buid, col] : pcols) {
    pcol.push_back(col);
  }
  REQUIRE(pcol.size() == m);
  std::size_t k = 0;
  for (const auto& [buid, row] : *rows) {
    const double h_k = k == 0 ? t[1] - t[0] : t[k] - t[k - 1];
    for (std::size_t j = 0; j < m; ++j) {
      CAPTURE(k);
      CAPTURE(j);
      CHECK(li.get_coeff(row, pcol[j])
            == doctest::Approx(h_k * d[k][j]).epsilon(1e-9).scale(1.0));
    }
    ++k;
  }
}
