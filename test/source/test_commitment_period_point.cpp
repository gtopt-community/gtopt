// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_period_point.cpp
 * @brief     `Commitment.commitment_period` on point-sampled stages
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Every stage is a partition into cells [τ_{k−1}, τ_k], τ_k = Σ_{j≤k} w_j
 * (blocks on block stages, the quadrature cells around the nodes on
 * `Stage.sampling = "point"` stages).  UC periods are unions of whole
 * cells: cell k → period ⌊mid_k / P⌋ (a nominal boundary snaps to the
 * nearer cell end), so a period lasts exactly Σ w of its cells — the
 * time-based UC-interval idea of `calc_uc_map` in the tcheby
 * scheduling code, made consistent with the quadrature.
 * `commitment_period = "auto"` picks P = max(resolution, ½·min(MUT, MDT)).
 */

#include <cmath>
#include <format>
#include <numbers>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/cell_partition.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace commitment_period_point_test
{
namespace
{

/// Clenshaw–Curtis weights of the Lobatto nodes t_k = 12(1 − cos(πk/24))
/// on [0, 24] h.
[[nodiscard]] std::vector<double> cc_weights()
{
  constexpr int n = 24;
  std::vector<double> w;
  for (int k = 0; k <= n; ++k) {
    const double theta = std::numbers::pi * k / n;
    double s = 0.0;
    for (int j = 1; j <= n / 2; ++j) {
      const double b = (2 * j == n) ? 1.0 : 2.0;
      s += b / ((4.0 * j * j) - 1.0) * std::cos(2.0 * j * theta);
    }
    const double c = (k == 0 || k == n) ? 1.0 : 2.0;
    w.push_back(c / n * (1.0 - s) * 12.0);
  }
  return w;
}

/// Lobatto nodes on [0, 24] (N = 24, CC weights) as point-stage blocks,
/// or 24 hourly blocks when @p point is false.
[[nodiscard]] std::string day_case(bool point, std::string_view period)
{
  constexpr int n = 24;
  const auto cc = cc_weights();
  std::string blocks;
  std::string lmax;
  const int count = point ? n + 1 : n;
  for (int k = 0; k < count; ++k) {
    const double t = point ? 12.0 * (1.0 - std::cos(std::numbers::pi * k / n))
                           : static_cast<double>(k) + 0.5;
    const double w = point ? cc[static_cast<std::size_t>(k)] : 1.0;
    blocks += std::format(
        R"({}{{"uid": {}, "duration": {:.17g}{}}})",
        k == 0 ? "" : ", ",
        k + 1,
        w,
        point ? std::format(R"(, "time": {:.17g})", t) : std::string {});
    lmax += std::format("{}{}", k == 0 ? "" : ", ", 50);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true{}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "uc_period_point",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "min_up_time": 4, "min_down_time": 6,
                           "relax": true,
                           "commitment_period": {}}}]}}
}})",
      blocks,
      count,
      point ? R"(, "sampling": "point")" : "",
      lmax,
      period);
}

/// First-block indices (0-based) of the UC periods, from the status
/// columns (one per period, keyed by the period's first block uid).
[[nodiscard]] std::vector<int> period_starts(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto& sys = plp.systems().front().front();
  const auto& uc = sys.elements<CommitmentLP>().front();
  const auto* cols = uc.find_status_cols(sys.scene().scenarios().front(),
                                         sys.phase().stages().front());
  REQUIRE(cols != nullptr);
  std::vector<int> out;
  for (const auto& [buid, col] : *cols) {
    out.push_back(static_cast<int>(value_of(buid)) - 1);
  }
  std::ranges::sort(out);
  return out;
}

}  // namespace
}  // namespace commitment_period_point_test

TEST_CASE(  // NOLINT
    "commitment_period - point stage: uniform cells by node time")
{
  using namespace commitment_period_point_test;
  // Lobatto N = 24 on [0, 24] h, P = 2 h: node t_k = 12(1 − cos(πk/24))
  // joins cell ⌊t_k / 2⌋ (aligned to the period start); a node on a cell
  // boundary (t_12 = 12 h) opens the later cell, t_24 = 24 h joins the
  // last cell.  Never one status per node.
  const std::vector<int> expected {0, 5, 7, 8, 10, 11, 12, 14, 15, 16, 18, 20};
  CHECK(period_starts(day_case(/*point=*/true, "2")) == expected);
}

TEST_CASE(  // NOLINT
    "commitment_period - auto = max(resolution, ½·min(MUT, MDT))")
{
  using namespace commitment_period_point_test;
  // MUT 4, MDT 6 ⇒ ½·min = 2 h; resolution = smallest cell (0.021 h)
  // ⇒ P = 2 h, same map as the explicit 2 h period.
  const std::vector<int> expected {0, 5, 7, 8, 10, 11, 12, 14, 15, 16, 18, 20};
  CHECK(period_starts(day_case(/*point=*/true, R"("auto")")) == expected);
  // block stage, 1 h blocks: P = max(1, 2) = 2 h ⇒ pairs of blocks
  std::vector<int> pairs;
  for (int k = 0; k < 24; k += 2) {
    pairs.push_back(k);
  }
  CHECK(period_starts(day_case(false, R"("auto")")) == pairs);
}

TEST_CASE(  // NOLINT
    "commitment_period - block stage grouping unchanged")
{
  using namespace commitment_period_point_test;
  // 24 hourly blocks, P = 3 h ⇒ 8 periods of 3 blocks
  std::vector<int> triples;
  for (int k = 0; k < 24; k += 3) {
    triples.push_back(k);
  }
  CHECK(period_starts(day_case(false, "3")) == triples);
  // unset (null) ⇒ one period per block
  CHECK(period_starts(day_case(false, "null")).size() == 24);
  // point stage without a period: the default uniform 1 h grid, never
  // one status per node (N = 24 Lobatto: the sparse middle leaves five
  // 1 h cells without a node, which continue the previous cell's status)
  const std::vector<int> hourly {
      0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21};
  CHECK(period_starts(day_case(/*point=*/true, "null")) == hourly);
}

TEST_CASE(  // NOLINT
    "Commitment::commitment_period_hours resolution")
{
  Commitment c;
  CHECK(c.commitment_period_hours(1.0) == doctest::Approx(0.0));
  c.commitment_period = 2.5;
  CHECK(c.commitment_period_hours(1.0) == doctest::Approx(2.5));
  c.commitment_period = Name {"auto"};
  // no MUT / MDT ⇒ per-block
  CHECK(c.commitment_period_hours(1.0) == doctest::Approx(0.0));
  c.min_up_time = 8.0;
  CHECK(c.commitment_period_hours(1.0) == doctest::Approx(4.0));
  c.min_down_time = 1.0;
  // ½·min(8, 1) = 0.5 < resolution 1 ⇒ 1
  CHECK(c.commitment_period_hours(1.0) == doctest::Approx(1.0));
  c.commitment_period = Name {"weekly"};  // unknown name ⇒ per-block
  CHECK(c.commitment_period_hours(1.0) == doctest::Approx(0.0));
}

TEST_CASE(  // NOLINT
    "commitment_period - JSON number and \"auto\" round trip")
{
  using namespace commitment_period_point_test;
  Planning base;
  base.merge(parse_planning_json(day_case(/*point=*/true, R"("auto")")));
  const auto& c = base.system.commitment_array.front();
  REQUIRE(c.commitment_period.has_value());
  CHECK(std::get<Name>(c.commitment_period.value_or(Real {0.0})) == "auto");
  Planning num;
  num.merge(parse_planning_json(day_case(/*point=*/true, "2")));
  const auto& cn = num.system.commitment_array.front();
  CHECK(std::get<Real>(cn.commitment_period.value_or(Name {})) == 2.0);
}

TEST_CASE(  // NOLINT
    "cell partition - variable-duration blocks and long cells")
{
  // blocks 0.5 / 1 / 2 / 3.5 h (τ = 0.5, 1.5, 3.5, 7), P = 2 h: mids
  // 0.25, 1, 2.5, 5.25 ⇒ raw periods 0, 0, 1, 2
  const std::vector<double> cells {0.5, 1.0, 2.0, 3.5};
  CHECK(cell_groups(cells, 2.0) == std::vector<std::size_t> {0, 0, 1, 2});
  // the 2 h boundary falls inside the 2 h block [1.5, 3.5] (mid 2.5 ⇒
  // later period): snapping error |1.5 − 2| = 0.5 ≤ half the block;
  // the 4 h and 6 h boundaries inside the 3.5 h block leave no empty
  // period (renumbered)
  CHECK(snap_period_boundaries(cells, 2.0) == std::vector<std::size_t> {1, 2});
  // a day boundary inside a 4 h block: blocks 21 / 4 / 23 h (the 4 h
  // block [21, 25] has its midpoint 23 < 24 ⇒ day 0); 22 / 4 / 22 puts
  // the midpoint on 24 ⇒ day 1
  CHECK(cell_groups(std::vector<double> {21.0, 4.0, 23.0}, 24.0)
        == std::vector<std::size_t> {0, 0, 1});
  CHECK(cell_groups(std::vector<double> {22.0, 4.0, 22.0}, 24.0)
        == std::vector<std::size_t> {0, 1, 1});
  // uniform hourly cells with P = 2 h: pairs (unchanged block grouping)
  const std::vector<double> hourly(6, 1.0);
  CHECK(cell_groups(hourly, 2.0)
        == std::vector<std::size_t> {0, 0, 1, 1, 2, 2});
  CHECK(cell_groups(hourly, 0.0) == std::vector<std::size_t>(6, 0));
  // uniform block stages keep the legacy tumbling accumulator (a group
  // closes once Σw ≥ P); it differs from the midpoint rule only when
  // the duration does not divide P (1.5 h blocks, P = 2 h)
  const std::vector<double> b15(4, 1.5);
  CHECK(duration_groups(b15, 2.0, false)
        == std::vector<std::size_t> {0, 0, 1, 1});
  CHECK(duration_groups(b15, 2.0, true)
        == std::vector<std::size_t> {0, 1, 1, 2});
  CHECK(duration_groups(hourly, 2.0, false) == cell_groups(hourly, 2.0));
  // variable durations snap by midpoint on block stages too
  CHECK(duration_groups(cells, 2.0, false) == cell_groups(cells, 2.0));
  CHECK(cell_groups(std::vector<double> {}, 2.0).empty());
}

TEST_CASE(  // NOLINT
    "commitment_period - variable-duration block stage")
{
  using namespace commitment_period_point_test;
  const auto json = std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 0.5}}, {{"uid": 2, "duration": 1}},
                    {{"uid": 3, "duration": 2}}, {{"uid": 4, "duration": 3.5}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "uc_period_var",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[50, 50, 50, 50]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "min_up_time": 3, "relax": true,
                           "commitment_period": 2}}]}}
}})");
  // periods {0.5, 1}, {2}, {3.5} h
  CHECK(period_starts(json) == std::vector<int> {0, 2, 3});
}

TEST_CASE(  // NOLINT
    "commitment_period - point stages floor the period at 1 h")
{
  using namespace commitment_period_point_test;
  // a sub-hour period on a point stage would split the CC cells into
  // near-per-node periods: it is floored at 1 h (the same map as P = 1)
  const auto one = period_starts(day_case(/*point=*/true, "1"));
  CHECK(one.size() < 25);
  CHECK(period_starts(day_case(/*point=*/true, "0.25")) == one);
  // block stages keep the requested grouping (1 h blocks ⇒ per block)
  CHECK(period_starts(day_case(false, "0.25")).size() == 24);
}

namespace commitment_cell_test
{
namespace
{

struct CellCase
{
  int ncols {};
  std::size_t nstatus {};
  double obj {};
  bool pmin_held {true};
};

/// 24 hourly blocks; with @p refine the hours 6 and 18 (sunrise /
/// sunset) are split into four 15-min blocks, every block labelled with
/// its hour as `commitment_cell`.  g1 is a binary UC unit (pmin 40).
[[nodiscard]] CellCase solve_cells(bool refine)
{
  std::string blocks;
  std::string lmax;
  int uid = 0;
  for (int h = 0; h < 24; ++h) {
    const int parts = (refine && (h == 6 || h == 18)) ? 4 : 1;
    for (int q = 0; q < parts; ++q) {
      const double t0 = h + (static_cast<double>(q) / parts);
      const double load =
          60.0 + (40.0 * std::sin(std::numbers::pi * t0 / 12.0));
      blocks += std::format(
          R"({}{{"uid": {}, "duration": {:.17g}, "commitment_cell": {}}})",
          uid == 0 ? "" : ", ",
          uid + 1,
          1.0 / parts,
          h);
      lmax += std::format("{}{:.6f}", uid == 0 ? "" : ", ", load);
      ++uid;
    }
  }
  const auto json = std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "uc_cells",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 50,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "pmin": 40, "startup_cost": 500,
                           "min_up_time": 2}}]}}
}})",
      blocks,
      uid,
      lmax);
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  CellCase out {.ncols = static_cast<int>(li.get_numcols()),
                .obj = li.get_obj_value()};
  const auto& scen = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto& uc = sys.elements<CommitmentLP>().front();
  const auto* status = uc.find_status_cols(scen, stage);
  REQUIRE(status != nullptr);
  out.nstatus = status->size();
  // every block inherits the status of its cell: p ≥ pmin·u there
  const auto sol = li.get_col_sol();
  const auto& gen = sys.elements<GeneratorLP>().front();
  const auto& pcols = gen.lookup_generation_cols(scen, stage);
  std::size_t first = 0;
  for (const auto& blk : stage.blocks()) {
    if (!blk.commitment_cell()) {
      continue;
    }
    // status column of the cell: keyed by the cell's first block uid
    const auto cell = *blk.commitment_cell();
    const auto it = std::ranges::find_if(
        stage.blocks(),
        [&](const auto& b) { return b.commitment_cell() == cell; });
    const auto ucol = status->at(it->uid());
    const auto u = sol[static_cast<std::size_t>(value_of(ucol))];
    const auto p = sol[static_cast<std::size_t>(value_of(pcols.at(blk.uid())))];
    if (p < (40.0 * u) - 1e-6) {
      out.pmin_held = false;
    }
    ++first;
  }
  CHECK(first == stage.blocks().size());
  return out;
}

}  // namespace
}  // namespace commitment_cell_test

TEST_CASE(  // NOLINT
    "commitment_cell - refined blocks add continuous columns, not binaries")
{
  using namespace commitment_cell_test;
  const auto coarse = solve_cells(/*refine=*/false);
  const auto fine = solve_cells(/*refine=*/true);
  CAPTURE(coarse.ncols);
  CAPTURE(fine.ncols);
  // one status per hourly cell in both: the 15-min blocks inherit it
  CHECK(coarse.nstatus == 24);
  CHECK(fine.nstatus == 24);
  // u is the only integer column of a commitment (v / w are continuous
  // in the tight 3-bin formulation): integer count = status count
  CHECK(fine.ncols > coarse.ncols);
  CHECK(coarse.pmin_held);
  CHECK(fine.pmin_held);
}

namespace commitment_unit_grid_test
{
namespace
{

/// Chebyshev–Lobatto week (N = 288 over 168 h: node spacing < 1 h, so
/// every 1 h cell holds a node), one commitment per entry of @p periods
/// (hours, its own overlay grid), demand @p load(t).
template<typename Load>
[[nodiscard]] std::string week_case(const std::vector<double>& periods,
                                    Load load,
                                    std::string_view uc_extra = "")
{
  constexpr int n = 288;
  constexpr double horizon = 168.0;
  std::vector<double> t(n + 1);
  for (int k = 0; k <= n; ++k) {
    t[static_cast<std::size_t>(k)] =
        0.5 * horizon * (1.0 - std::cos(std::numbers::pi * k / n));
  }
  std::string blocks;
  std::string lmax;
  for (int k = 0; k <= n; ++k) {
    const auto i = static_cast<std::size_t>(k);
    // trapezoid cells (positive, sum = horizon): enough for a UC test
    const double lo = k == 0 ? t[0] : 0.5 * (t[i] + t[i - 1]);
    const double hi = k == n ? t[i] : 0.5 * (t[i] + t[i + 1]);
    blocks +=
        std::format(R"({}{{"uid": {}, "duration": {:.17g}, "time": {:.17g}}})",
                    k == 0 ? "" : ", ",
                    k + 1,
                    hi - lo,
                    t[i]);
    lmax += std::format("{}{:.6f}", k == 0 ? "" : ", ", load(t[i]));
  }
  std::string gens;
  std::string ucs;
  for (std::size_t u = 0; u < periods.size(); ++u) {
    gens += std::format(
        R"(, {{"uid": {}, "name": "g{}", "bus": "b1", "gcost": {},
             "capacity": 100}})",
        u + 2,
        u + 2,
        1 + u);
    ucs += std::format(
        R"({}{{"uid": {}, "name": "uc{}", "generator": {}, "noload_cost": 1,
             "commitment_period": {}{}}})",
        u == 0 ? "" : ", ",
        u + 1,
        u + 1,
        u + 2,
        periods[u],
        uc_extra);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true, "sampling": "point"}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "uc_unit_grid",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1000,
        "capacity": 1000}}{}],
    "commitment_array": [{}]}}
}})",
      blocks,
      n + 1,
      lmax,
      gens,
      ucs);
}

struct UnitGrid
{
  std::vector<std::size_t> nstatus;  ///< status columns per unit
  std::vector<std::vector<double>> u;  ///< status per cell, per unit
};

[[nodiscard]] UnitGrid solve_week(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  UnitGrid out;
  const auto sol = li.get_col_sol();
  for (const auto& uc : sys.elements<CommitmentLP>()) {
    const auto* cols = uc.find_status_cols(sys.scene().scenarios().front(),
                                           sys.phase().stages().front());
    REQUIRE(cols != nullptr);
    out.nstatus.push_back(cols->size());
    std::vector<std::pair<int, double>> cells;
    cells.reserve(cols->size());
    for (const auto& [buid, col] : *cols) {
      cells.emplace_back(static_cast<int>(value_of(buid)),
                         sol[static_cast<std::size_t>(value_of(col))]);
    }
    std::ranges::sort(cells);
    std::vector<double> u;
    u.reserve(cells.size());
    for (const auto& [b, v] : cells) {
      u.push_back(v);
    }
    out.u.push_back(std::move(u));
  }
  return out;
}

}  // namespace
}  // namespace commitment_unit_grid_test

TEST_CASE(  // NOLINT
    "commitment_period - per-unit 1/3/4 h grids on a Chebyshev week")
{
  using namespace commitment_unit_grid_test;
  // three units with their own uniform grids aligned to the week start:
  // integer columns per unit = 168 / P, never one per node (289 nodes)
  const auto sol = solve_week(week_case(
      {1.0, 3.0, 4.0}, [](double tk) { return 50.0 + (20.0 * std::sin(tk)); }));
  REQUIRE(sol.nstatus.size() == 3);
  CHECK(sol.nstatus[0] == 168);
  CHECK(sol.nstatus[1] == 56);
  CHECK(sol.nstatus[2] == 42);
  // u is the only integer column of a commitment: 168 + 56 + 42 binaries
  CHECK(sol.nstatus[0] + sol.nstatus[1] + sol.nstatus[2] == 266);
}

TEST_CASE(  // NOLINT
    "commitment_period - min up time in hours on a 4 h point-stage grid")
{
  using namespace commitment_unit_grid_test;
  // load only in [80, 82) h (inside the 4 h cell [80, 84)); the unit
  // pays no-load while on, so it stays on exactly max(MUT, 4 h)
  const auto pulse = [](double tk)
  { return (tk >= 80.0 && tk < 82.0) ? 50.0 : 0.0; };
  for (const double mut : {4.0, 12.0}) {
    CAPTURE(mut);
    const auto sol = solve_week(
        week_case({4.0}, pulse, std::format(R"(, "min_up_time": {})", mut)));
    REQUIRE(sol.u.size() == 1);
    double on_hours = 0.0;
    for (const double u : sol.u.front()) {
      on_hours += 4.0 * u;
    }
    CHECK(on_hours == doctest::Approx(mut));
    CHECK(sol.u.front()[20] == doctest::Approx(1.0));  // cell [80, 84)
  }
}

TEST_CASE(  // NOLINT
    "commitment_grid - previous point-stage grids stay reachable")
{
  using namespace commitment_period_point_test;
  const auto with_grid = [](std::string json, std::string_view grid)
  {
    const std::string key = R"("relax": true,)";
    const auto pos = json.find(key);
    REQUIRE(pos != std::string::npos);
    json.insert(pos + key.size(),
                std::format(R"( "commitment_grid": "{}",)", grid));
    return json;
  };
  // "block": one status per node (25 Lobatto nodes)
  CHECK(
      period_starts(with_grid(day_case(/*point=*/true, "null"), "block")).size()
      == 25);
  // "cells": periods are unions of the CC cells (midpoint rule): the
  // cell of t_12 = 12 h is centred on the boundary, cell 16 joins 17
  const std::vector<int> cells {0, 5, 7, 8, 10, 11, 12, 14, 15, 17, 18, 20};
  CHECK(period_starts(with_grid(day_case(/*point=*/true, "2"), "cells"))
        == cells);
  // and the same map from the pure partition helper
  const auto bounds = snap_period_boundaries(cc_weights(), 2.0);
  REQUIRE(bounds.size() == cells.size() - 1);
  for (std::size_t m = 0; m < bounds.size(); ++m) {
    CHECK(static_cast<int>(bounds[m]) + 1 == cells[m + 1]);
  }
}

TEST_CASE(  // NOLINT
    "ramp_allowance - 'boundary' gives SU only at the period boundary")
{
  // 4 hourly blocks, 2 h commitment periods {0, 1} and {2, 3}; g1 (1 $,
  // RU 10 MW/h, SU 50 MW) starts in period 2 to serve 50 / 100 MW at
  // blocks 2 / 3.  Boundary allowance: p2 ≤ 50, p3 ≤ p2 + 10 = 60, g2
  // (100 $) covers 40 MW: 50 + 60 + 4000 = 4110 (the default).  The
  // "period" allowance also lets block 3 use SU: p3 = 100, cost 150.
  const auto solve = [](std::string_view allowance)
  {
    const auto json = std::format(
        R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}, {{"uid": 2, "duration": 1}},
                    {{"uid": 3, "duration": 1}}, {{"uid": 4, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4,
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "ramp_allowance",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[0, 0, 50, 100]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "ramp_up": 10, "startup_ramp": 50,
                           "initial_status": 0, "initial_hours": 10,
                           "initial_power": 0, "relax": false,
                           "commitment_period": 2{}}}]}}
}})",
        allowance);
    Planning base;
    base.merge(parse_planning_json(json));
    PlanningLP plp(std::move(base));
    auto res = plp.resolve();
    REQUIRE(res.has_value());
    return plp.systems().front().front().linear_interface().get_obj_value();
  };
  CHECK(solve(R"(, "ramp_allowance": "boundary")") == doctest::Approx(4110.0));
  CHECK(solve(R"(, "ramp_allowance": "period")") == doctest::Approx(150.0));
  // the default is master's boundary-only allowance
  CHECK(solve("") == doctest::Approx(4110.0));
}

TEST_CASE(  // NOLINT
    "commitment_cell - non-chronological stages share the cell status")
{
  // A refined block week (plexos2gtopt --ramp-refine) labels its blocks
  // with `commitment_cell`; several blocks share a cell.  On a
  // non-chronological stage the status u exists once per cell and every
  // block of the cell must resolve to it (the battery's synthesised
  // `uc_<bat>_gen` commitment is enough to exercise it).  Its relaxed,
  // costless u leaves the optimum of the unlabelled case unchanged.
  const auto solve = [](bool cells)
  {
    const auto blk = [&](int uid, double dur, int cell)
    {
      return cells
          ? std::format(
                R"({{"uid": {}, "duration": {}, "commitment_cell": {}}})",
                uid,
                dur,
                cell)
          : std::format(R"({{"uid": {}, "duration": {}}})", uid, dur);
    };
    std::string blocks;
    int uid = 1;
    for (int st = 0; st < 2; ++st) {
      for (const auto& [dur, cell] :
           std::vector<std::pair<double, int>> {
               {1.0, 0},
               {0.5, 1},
               {0.5, 1},
               {1.0, 2},
           })
      {
        if (uid > 1) {
          blocks += ", ";
        }
        blocks += blk(uid, dur, cell);
        ++uid;
      }
    }
    const auto json = std::format(
        R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 1000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4}},
                    {{"uid": 2, "first_block": 4, "count_block": 4}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "cells_nonchrono",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1", "lmax": 50}}],
    "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
                          "gcost": 10, "capacity": 100}}],
    "battery_array": [{{"uid": 1, "name": "bat", "bus": "b1",
                        "input_efficiency": 0.9, "output_efficiency": 0.9,
                        "emin": 0, "emax": 100, "eini": 10,
                        "pmax_charge": 20, "pmax_discharge": 20,
                        "capacity": 100}}]}}
}})",
        blocks);
    Planning base;
    base.merge(parse_planning_json(json));
    PlanningLP plp(std::move(base));
    auto res = plp.resolve();
    REQUIRE(res.has_value());
    return plp.systems().front().front().linear_interface().get_obj_value();
  };
  CHECK(solve(true) == doctest::Approx(solve(false)));
}
