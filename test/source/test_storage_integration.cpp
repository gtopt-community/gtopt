// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_storage_integration.cpp
 * @brief     Banded (local Lagrange) storage integration on Chebyshev grids
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Point-sampled stages (`Stage.sampling = "point"`) with
 * `model_options.storage_integration = trapezoid | lg4 | lg6 [_pos]`:
 * the storage level is the state AT the Chebyshev–Lobatto node and the
 * increment over [t_{k−1}, t_k] integrates the local cubic / quintic
 * interpolant of the flows.  Columns are corrected to the Clenshaw–Curtis
 * block weights, so the period total equals the CC quadrature.  The
 * Chebyshev typical-week construction follows the user's paper code.
 * Also: the daily-cycle eini check of validate_planning.
 */

#include <algorithm>
#include <cmath>
#include <format>
#include <numbers>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_battery.hpp>
#include <gtopt/json/json_model_options.hpp>
#include <gtopt/json/json_reservoir.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/storage_integration.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace storage_integration_test
{
namespace
{

/// Chebyshev–Lobatto nodes on [0, T] (ascending) and their Clenshaw–Curtis
/// weights (sum T).
struct Grid
{
  std::vector<double> t;
  std::vector<double> w;
};

[[nodiscard]] Grid lobatto_cc(int n, double period)
{
  Grid g;
  const double pi = std::numbers::pi;
  for (int k = 0; k <= n; ++k) {
    const double theta = pi * k / n;
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

/// Levels at the nodes from the integration rows (start 0).
[[nodiscard]] std::vector<double> levels(const IntegrationRows& rows,
                                         const std::vector<double>& f)
{
  std::vector<double> e;
  double acc = 0.0;
  for (const auto& row : rows) {
    for (const auto& term : row) {
      acc += term.weight * f[term.node];
    }
    e.push_back(acc);
  }
  return e;
}

/// Exact cumulative integral of the global node interpolant at the nodes
/// (the dense S = V J V⁻¹).
[[nodiscard]] std::vector<double> dense_levels(const Grid& g,
                                               const std::vector<double>& f)
{
  const auto n = static_cast<int>(g.t.size());
  const auto rows = local_integration_rows(
      g.t, g.w, 0.0, g.t.back(), {.points = n}, /*match_weights=*/false);
  return levels(rows, f);
}

[[nodiscard]] std::string blocks_json(const Grid& g, double offset)
{
  std::string out;
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    out +=
        std::format(R"({}{{"uid": {}, "duration": {:.17g}, "time": {:.17g}}})",
                    k == 0 ? "" : ", ",
                    static_cast<double>(k) + 1 + offset,
                    g.w[k],
                    g.t[k]);
  }
  return out;
}

/// One day on a Lobatto grid: cheap energy (10 $/MWh, 100 MW) in the
/// first half, a 50 MW load served by `dear` (100 $/MWh) otherwise.  A
/// battery with emax and efin = eini = 0.  The battery's
/// best use is to fill up to emax and discharge into the evening load —
/// any apparent energy above emax would come from exploiting the weights.
[[nodiscard]] std::string day_case(const Grid& g,
                                   std::string_view integration,
                                   double emax)
{
  std::string lmax;
  std::string pmax;
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    const bool morning = g.t[k] < 12.0;
    lmax += std::format("{}{}", k == 0 ? "" : ", ", morning ? 0 : 50);
    pmax += std::format("{}{}", k == 0 ? "" : ", ", morning ? 100 : 0);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1,
                                  "storage_integration": "{}"}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "sampling": "point"}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "storage_integration",
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
      integration,
      blocks_json(g, 0.0),
      g.t.size(),
      lmax,
      pmax,
      emax,
      emax);
}

struct DaySolution
{
  double obj {0.0};
  std::vector<double> soc;  ///< LP levels per block
  std::vector<double> net;  ///< charge − discharge per block [MW]
};

[[nodiscard]] DaySolution solve_day(const std::string& json)
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
  DaySolution out;
  out.obj = li.get_obj_value();
  const auto sol = li.get_col_sol();
  const auto& bat = sys.elements<BatteryLP>().front();
  for (const auto& [buid, col] : bat.energy_cols_at(scenario, stage)) {
    out.soc.push_back(bat.to_physical(sol[col]));
  }
  const auto& finp = bat.finp_cols_at(scenario, stage);
  const auto& fout = bat.fout_cols_at(scenario, stage);
  for (const auto& [buid, col] : finp) {
    const auto it = fout.find(buid);
    const double out_v = it == fout.end() ? 0.0 : sol[it->second];
    out.net.push_back(sol[col] - out_v);
  }
  return out;
}

/// Battery + reservoir on one point-sampled day.  `opts` / `bat_mode` /
/// `rsv_mode` are extra JSON fragments (model options, element fields).
[[nodiscard]] std::string mixed_case(const Grid& g,
                                     std::string_view opts,
                                     std::string_view bat_mode,
                                     std::string_view rsv_mode)
{
  std::string lmax;
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    lmax += std::format("{}{}", k == 0 ? "" : ", ", g.t[k] < 12.0 ? 10 : 60);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1{}}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "sampling": "point"}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "mixed_integration",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "hyd", "bus": "b1", "gcost": 0, "capacity": 1000}},
      {{"uid": 2, "name": "cheap", "bus": "b1", "gcost": 10,
        "capacity": 40}},
      {{"uid": 3, "name": "dear", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "junction_array": [{{"uid": 1, "name": "j1"}},
                       {{"uid": 2, "name": "j2", "drain": true}}],
    "waterway_array": [{{"uid": 1, "name": "ww1", "junction_a": 1,
                         "junction_b": 2, "fmin": 0, "fmax": 500}}],
    "flow_array": [{{"uid": 1, "name": "q_in", "direction": 1,
                     "junction": 1, "discharge": 10}}],
    "reservoir_array": [{{"uid": 1, "name": "rsv", "junction": 1,
                          "flow_conversion_rate": 1, "capacity": 1000,
                          "emin": 0, "emax": 1000, "eini": 100,
                          "efin": 100{}}}],
    "turbine_array": [{{"uid": 1, "name": "tur", "waterway": 1,
                        "generator": 1, "production_factor": 1.0}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "input_efficiency": 1, "output_efficiency": 1,
                        "emin": 0, "emax": 100, "eini": 0, "efin": 0,
                        "daily_cycle": false, "use_state_variable": true,
                        "pmax_charge": 50, "pmax_discharge": 50,
                        "capacity": 100{}}}]}}
}})",
      opts,
      blocks_json(g, 0.0),
      g.t.size(),
      lmax,
      rsv_mode,
      bat_mode);
}

/// Flow coefficients D_kj of an element's energy-balance rows (physical).
struct BandMatrix
{
  std::vector<std::vector<double>> d;  ///< [row k][block j]

  [[nodiscard]] std::size_t max_row_nnz() const
  {
    std::size_t out = 0;
    for (const auto& row : d) {
      out = std::max(out,
                     static_cast<std::size_t>(std::ranges::count_if(
                         row, [](double v) { return std::abs(v) > 1e-12; })));
    }
    return out;
  }
  [[nodiscard]] double col_sum(std::size_t j) const
  {
    double s = 0.0;
    for (const auto& row : d) {
      s += row[j];
    }
    return s;
  }
  [[nodiscard]] double min_weight() const
  {
    double m = 0.0;
    for (const auto& row : d) {
      for (const double v : row) {
        m = std::min(m, v);
      }
    }
    return m;
  }
};

struct MixedSolution
{
  double obj {0.0};
  BandMatrix battery;
  BandMatrix reservoir;
};

template<typename Cols>
[[nodiscard]] BandMatrix band_of(const LinearInterface& li,
                                 const auto& rows,
                                 const Cols& cols)
{
  BandMatrix out;
  for (const auto& [ruid, row] : rows) {
    auto& line = out.d.emplace_back();
    for (const auto& [cuid, col] : cols) {
      line.push_back(li.get_coeff(row, col));
    }
  }
  return out;
}

[[nodiscard]] MixedSolution solve_mixed(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  MixedSolution out;
  out.obj = li.get_obj_value();
  const auto& bat = sys.elements<BatteryLP>().front();
  out.battery = band_of(li,
                        bat.energy_rows_at(scenario, stage),
                        bat.fout_cols_at(scenario, stage));
  const auto& rsv = sys.elements<ReservoirLP>().front();
  out.reservoir = band_of(li,
                          rsv.energy_rows_at(scenario, stage),
                          rsv.extraction_cols_at(scenario, stage));
  return out;
}

}  // namespace
}  // namespace storage_integration_test

TEST_CASE(  // NOLINT
    "storage_integration - option parsing")
{
  CHECK_FALSE(StorageIntegration::parse("block").banded());
  CHECK(StorageIntegration::parse("trapezoid").points == 2);
  CHECK(StorageIntegration::parse("lg4").points == 4);
  CHECK(StorageIntegration::parse("lg6").points == 6);
  CHECK(StorageIntegration::parse("lg4_pos").clip_negative);
  CHECK(StorageIntegration::is_valid("block"));
  CHECK_FALSE(StorageIntegration::is_valid("cc_local5"));
}

TEST_CASE(  // NOLINT
    "storage_integration - column sums equal the CC block weights")
{
  using namespace storage_integration_test;
  for (const int n : {16, 64, 96, 168}) {
    const auto g = lobatto_cc(n, 168.0);
    for (const auto* name : {"trapezoid", "lg4", "lg6", "lg4_pos", "lg6_pos"}) {
      const auto mode = StorageIntegration::parse(name);
      const auto rows = local_integration_rows(
          g.t, g.w, 0.0, 168.0, mode, /*match_weights=*/true);
      std::vector<double> colsum(g.t.size(), 0.0);
      std::size_t nnz = 0;
      for (const auto& row : rows) {
        for (const auto& term : row) {
          colsum[term.node] += term.weight;
          ++nnz;
          if (mode.clip_negative) {
            CHECK(term.weight >= 0.0);
          }
        }
      }
      for (std::size_t j = 0; j < g.t.size(); ++j) {
        CHECK(colsum[j] == doctest::Approx(g.w[j]).epsilon(1e-13));
      }
      // banded: ≤ points nonzeros per row (vs N+1 dense)
      CHECK(nnz <= g.t.size() * static_cast<std::size_t>(mode.points));
      // Lobatto: the first node is the period start (empty first row)
      CHECK(rows.front().empty());
    }
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - trapezoid: linear flows exact at every node")
{
  using namespace storage_integration_test;
  const auto g = lobatto_cc(32, 168.0);
  std::vector<double> f;
  f.reserve(g.t.size());
  for (const double t : g.t) {
    f.push_back(2.0 + (0.01 * t));
  }
  const auto prim = [](double t) { return (2.0 * t) + (0.005 * t * t); };
  const auto mode = StorageIntegration::parse("trapezoid");
  const auto raw = local_integration_rows(
      g.t, g.w, 0.0, 168.0, mode, /*match_weights=*/false);
  const auto e_raw = levels(raw, f);
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    CHECK(e_raw[k] == doctest::Approx(prim(g.t[k])).epsilon(1e-12));
    CHECK(raw[k].size() <= 2);
  }
  // corrected: weekly closure exact (CC), nodes first order
  const auto cor = local_integration_rows(g.t, g.w, 0.0, 168.0, mode);
  CHECK(levels(cor, f).back() == doctest::Approx(prim(168.0)).epsilon(1e-12));
}

TEST_CASE(  // NOLINT
    "storage_integration - cubic flows integrated exactly at every node")
{
  using namespace storage_integration_test;
  const auto g = lobatto_cc(32, 168.0);
  std::vector<double> f;
  const auto prim = [](double t)
  {
    const double x = t / 168.0;
    return 168.0 * ((1.5 * x) - (x * x) + (0.5 * x * x * x * x));
  };
  for (const double t : g.t) {
    const double x = t / 168.0;
    f.push_back(1.5 - (2.0 * x) + (2.0 * x * x * x));
  }
  // raw cubic Lagrange rule: exact at every node
  const auto raw = local_integration_rows(g.t,
                                          g.w,
                                          0.0,
                                          168.0,
                                          StorageIntegration::parse("lg4"),
                                          /*match_weights=*/false);
  const auto e_raw = levels(raw, f);
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    CHECK(e_raw[k] == doctest::Approx(prim(g.t[k])).epsilon(1e-11));
  }
  // with the CC column correction: total exact; the intermediate levels
  // move by the column corrections w_j − Σ_k D_kj (CC vs local rule,
  // ≈ 6.5e-4 of the total at N = 32) — first order, like the block rule
  const auto cor = local_integration_rows(
      g.t, g.w, 0.0, 168.0, StorageIntegration::parse("lg4"));
  const auto e_cor = levels(cor, f);
  CHECK(e_cor.back() == doctest::Approx(prim(168.0)).epsilon(1e-12));
  double worst = 0.0;
  for (std::size_t k = 0; k < g.t.size(); ++k) {
    worst = std::max(worst, std::abs(e_cor[k] - prim(g.t[k])));
  }
  CHECK(worst / prim(168.0) < 2e-3);
  // the dense reference is exact too
  const auto e_dense = dense_levels(g, f);
  CHECK(e_dense.back() == doctest::Approx(prim(168.0)).epsilon(1e-10));
}

TEST_CASE(  // NOLINT
    "storage_integration - smooth flow: banded beats the block recursion")
{
  using namespace storage_integration_test;
  for (const int n : {64, 96, 168}) {
    const auto g = lobatto_cc(n, 168.0);
    std::vector<double> f;
    f.reserve(g.t.size());
    for (const double t : g.t) {
      f.push_back(std::sin(2 * std::numbers::pi * t / 24.0)
                  + (0.3 * std::cos(2 * std::numbers::pi * t / 12.0)) + 0.2);
    }
    const auto exact = [](double t)
    {
      const double pi = std::numbers::pi;
      return (-24.0 / (2 * pi) * (std::cos(2 * pi * t / 24.0) - 1.0))
          + (0.3 * 12.0 / (2 * pi) * std::sin(2 * pi * t / 12.0)) + (0.2 * t);
    };
    const auto local4 =
        levels(local_integration_rows(
                   g.t, g.w, 0.0, 168.0, StorageIntegration::parse("lg4")),
               f);
    double scale = 0.0;
    double err_block = 0.0;
    double err_local = 0.0;
    double acc = 0.0;
    for (std::size_t k = 0; k < g.t.size(); ++k) {
      acc += g.w[k] * f[k];  // block recursion level, compared at the node
      scale = std::max(scale, std::abs(exact(g.t[k])));
      err_block = std::max(err_block, std::abs(acc - exact(g.t[k])));
      err_local = std::max(err_local, std::abs(local4[k] - exact(g.t[k])));
    }
    MESSAGE(std::format("N={} block={:.2e} lg4={:.2e}",
                        n,
                        err_block / scale,
                        err_local / scale));
    CHECK(err_local < 0.2 * err_block);
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - LP: envelope rows stop intra-interval shifts")
{
  using namespace storage_integration_test;
  const auto g = lobatto_cc(24, 24.0);
  const double emax = 100.0;
  // block recursion: shift exactly emax (10 → 100 $/MWh)
  const auto block = solve_day(day_case(g, "block", emax));
  // Levels are bounded only AT the nodes; without the envelope rows a
  // charge at t_k and a discharge at t_{k+1} cancel inside one row and
  // the battery shifts ≈ ½·Δt·pmax beyond emax (39 MWh here, +3534 $).
  // With them the true level of the piecewise-linear flows stays within
  // emax and the shift equals the block value up to the interpolation
  // accuracy.
  for (const auto* name : {"trapezoid", "lg4", "lg4_pos", "lg6"}) {
    const auto sol = solve_day(day_case(g, name, emax));
    for (const double e : sol.soc) {
      CHECK(e <= emax + 1e-6);
    }
    // true peak of the piecewise-linear net flow inside each interval,
    // starting from the LP level at the left node
    double peak = 0.0;
    for (std::size_t k = 1; k < g.t.size(); ++k) {
      const double dt = g.t[k] - g.t[k - 1];
      const double fa = sol.net[k - 1];
      const double fb = sol.net[k];
      double best = std::max(0.0, 0.5 * dt * (fa + fb));
      if (fa > 0.0 && fb < 0.0) {  // interior maximum at the zero crossing
        best = std::max(best, 0.5 * dt * fa * fa / (fa - fb));
      }
      peak = std::max(peak, sol.soc[k - 1] + best);
    }
    const double extra = (block.obj - sol.obj) / (90.0 * emax);
    MESSAGE(
        std::format("{}: obj={:.4f} (block {:.4f}) shift vs block "
                    "{:+.3f}% of emax, PWL peak {:.3f}",
                    name,
                    sol.obj,
                    block.obj,
                    100.0 * extra,
                    peak));
    // The envelope rows bound the rule's own interpolant (D+ weights);
    // the piecewise-linear peak is only a proxy for it, and alternative
    // optima of the same objective differ by solver (HiGHS ≈ 1.1 %).
    CHECK(peak <= emax * 1.02);
    CHECK(extra < 0.02);
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - closed period conserves storage on every rule")
{
  // eini = 0, lossless conversion, no self-discharge: whatever the rule,
  // the net stored energy over the period, integrated with the quadrature
  // (block) weights, equals the last level (Σ_k w_k φ_k = e_N − e_0; the
  // balanced column sums = CC weights make the banded rules close the
  // same quadrature as the block recursion).  efin = 0 is a lower bound,
  // so a rule may leave a little energy in the battery at the end.
  using namespace storage_integration_test;
  for (const int n : {8, 24}) {
    const auto g = lobatto_cc(n, 24.0);
    double wsum = 0.0;
    for (const double w : g.w) {
      wsum += w;
    }
    CHECK(wsum == doctest::Approx(24.0));
    for (const std::string name :
         {"block", "trapezoid", "lg4", "lg4_pos", "lg6", "lg6_pos"})
    {
      CAPTURE(n);
      CAPTURE(name);
      const auto sol = solve_day(day_case(g, name, 100.0));
      REQUIRE(sol.net.size() == g.w.size());
      REQUIRE_FALSE(sol.soc.empty());
      double stored = 0.0;
      double charged = 0.0;
      for (std::size_t k = 0; k < g.w.size(); ++k) {
        stored += g.w[k] * sol.net[k];
        charged += g.w[k] * std::max(sol.net[k], 0.0);
      }
      CHECK(charged > 1.0);  // the battery is used
      CHECK(sol.soc.back() >= -1e-9);
      CHECK(stored
            == doctest::Approx(sol.soc.back()).epsilon(1e-9).scale(charged));
    }
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - lg4_pos on periodic N-node grids")
{
  // plexos2gtopt / tcheby2gtopt emit battery_storage_integration =
  // reservoir_storage_integration = lg4_pos on Chebyshev point stages
  // with --point-storage-integration lg4_pos (default cc, tested below);
  // their periodic Lobatto grids carry N nodes (node N ≡ node 0 dropped,
  // its weight added to node 0) and period_hours = the period.  Both
  // storages must be banded with nonnegative weights whose column sums
  // are the block weights (the period total is the quadrature), and the
  // battery must conserve energy over the closed period.
  using namespace storage_integration_test;
  constexpr std::string_view kOpts =
      R"(, "battery_storage_integration": "lg4_pos",
           "reservoir_storage_integration": "lg4_pos")";
  for (const int n : {8, 24}) {
    CAPTURE(n);
    auto g = lobatto_cc(n, 24.0);
    g.w.front() += g.w.back();
    g.w.pop_back();
    g.t.pop_back();
    double wsum = 0.0;
    for (const double w : g.w) {
      wsum += w;
    }
    CHECK(wsum == doctest::Approx(24.0));
    const auto periodic = [](std::string json)
    {
      const std::string key = R"("sampling": "point")";
      const auto pos = json.find(key);
      REQUIRE(pos != std::string::npos);
      json.insert(pos + key.size(), R"(, "period_hours": 24)");
      return json;
    };

    const auto mixed = solve_mixed(periodic(mixed_case(g, kOpts, "", "")));
    for (const auto* m : {&mixed.battery, &mixed.reservoir}) {
      CHECK(m->max_row_nnz() > 1);
      CHECK(m->max_row_nnz() <= 4);
      CHECK(m->min_weight() >= -1e-12);
      for (std::size_t j = 0; j < g.w.size(); ++j) {
        CHECK(m->col_sum(j) == doctest::Approx(g.w[j]).epsilon(1e-9));
      }
    }

    auto day = day_case(g, "block", 100.0);
    const std::string glob = R"("storage_integration": "block")";
    const auto gpos = day.find(glob);
    REQUIRE(gpos != std::string::npos);
    day.replace(
        gpos, glob.size(), R"("battery_storage_integration": "lg4_pos")");
    const auto sol = solve_day(periodic(day));
    REQUIRE(sol.net.size() == g.w.size());
    double stored = 0.0;
    double charged = 0.0;
    for (std::size_t k = 0; k < g.w.size(); ++k) {
      stored += g.w[k] * sol.net[k];
      charged += g.w[k] * std::max(sol.net[k], 0.0);
    }
    CHECK(charged > 1.0);
    CHECK(stored
          == doctest::Approx(sol.soc.back()).epsilon(1e-9).scale(charged));
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - SDDP state is the level at the last node")
{
  using namespace storage_integration_test;
  const auto g = lobatto_cc(16, 24.0);
  // two days, the battery may carry energy from day 1 to day 2: cheap
  // energy on day 1 only, load on day 2 only
  const auto make = [&](bool phases, std::string_view mode)
  {
    std::string lmax;
    std::string pmax;
    for (int d = 0; d < 2; ++d) {
      lmax += std::string(d == 0 ? "" : ", ") + "[";
      pmax += std::string(d == 0 ? "" : ", ") + "[";
      for (std::size_t k = 0; k < g.t.size(); ++k) {
        lmax += std::format("{}{}", k == 0 ? "" : ", ", d == 0 ? 0 : 20);
        pmax += std::format("{}{}", k == 0 ? "" : ", ", d == 0 ? 100 : 0);
      }
      lmax += "]";
      pmax += "]";
    }
    const auto n = g.t.size();
    return std::format(
        R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true, "battery_annual_standing_loss": 0,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1,
                                  "storage_integration": "{}"}}}},
  "simulation": {{
    "block_array": [{}, {}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "sampling": "point"}},
                    {{"uid": 2, "first_block": {}, "count_block": {},
                      "sampling": "point"}}],
    {}
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "storage_integration_sddp",
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
        mode,
        blocks_json(g, 0.0),
        blocks_json(g, static_cast<double>(n)),
        n,
        n,
        n,
        phases ? R"("phase_array": [{"uid": 1, "first_stage": 0,
                                     "count_stage": 1},
                                    {"uid": 2, "first_stage": 1,
                                     "count_stage": 1}],)"
               : "",
        lmax,
        pmax);
  };

  for (const auto* mode : {"block", "trapezoid", "lg4"}) {
    CAPTURE(mode);
    Planning mono_p;
    mono_p.merge(parse_planning_json(make(false, mode)));
    PlanningLP mono(std::move(mono_p));
    auto mres = mono.resolve();
    REQUIRE(mres.has_value());
    const double mono_obj =
        mono.systems().front().front().linear_interface().get_obj_value();
    // day 2 needs 20 MW × 24 h = 480 MWh; the battery carries 200 MWh
    CHECK(mono_obj == doctest::Approx((200.0 * 10.0) + (280.0 * 100.0)));

    Planning sddp_p;
    sddp_p.merge(parse_planning_json(make(true, mode)));
    PlanningLP plp(std::move(sddp_p));
    SDDPOptions opts;
    opts.max_iterations = 10;
    opts.convergence_tol = 1e-6;
    SDDPMethod sddp(plp, opts);
    auto results = sddp.solve();
    REQUIRE(results.has_value());
    REQUIRE_FALSE(results->empty());
    CHECK(results->back().upper_bound
          == doctest::Approx(mono_obj).epsilon(1e-6));
    CHECK(results->back().lower_bound
          == doctest::Approx(mono_obj).epsilon(1e-6));
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - daily-cycle eini above a retired stage's emax")
{
  const auto make = [](double eini)
  {
    return std::format(
        R"({{
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 24}}, {{"uid": 2, "duration": 24}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}},
                    {{"uid": 2, "first_block": 1, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{
    "name": "retire",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
                        "emin": 0, "emax": [[8.9], [0.0]], "eini": {},
                        "daily_cycle": true, "capacity": 8.9}}]}}
}})",
        eini);
  };
  Planning bad;
  bad.merge(parse_planning_json(make(4.46)));
  const auto r = validate_planning(bad);
  CHECK(std::ranges::any_of(
      r.errors,
      [](const auto& e)
      { return e.contains("Battery 'bess'") && e.contains("stage uid=2"); }));
  Planning good;
  good.merge(parse_planning_json(make(0.0)));
  const auto r2 = validate_planning(good);
  CHECK_FALSE(std::ranges::any_of(
      r2.errors,
      [](const auto& e) { return e.contains("daily_cycle chains"); }));
}

TEST_CASE(  // NOLINT
    "storage_integration - balanced rows: interval lengths and CC weights")
{
  using namespace storage_integration_test;
  for (const int n : {16, 48, 96}) {
    const auto g = lobatto_cc(n, 168.0);
    for (const auto* name : {"trapezoid", "lg4", "lg6", "lg4_pos", "lg6_pos"}) {
      CAPTURE(name);
      const auto mode = StorageIntegration::parse(name);
      const auto rows = local_integration_rows(g.t, g.w, 0.0, 168.0, mode);
      std::vector<double> colsum(g.t.size(), 0.0);
      for (std::size_t k = 0; k < rows.size(); ++k) {
        double rs = 0.0;
        for (const auto& term : rows[k]) {
          rs += term.weight;
          colsum[term.node] += term.weight;
          if (mode.clip_negative) {
            CHECK(term.weight >= 0.0);
          }
        }
        // a constant flow integrates exactly over every interval
        const double len = k == 0 ? g.t[0] : g.t[k] - g.t[k - 1];
        CHECK(rs == doctest::Approx(len).epsilon(1e-9));
      }
      for (std::size_t j = 0; j < g.t.size(); ++j) {
        CHECK(colsum[j] == doctest::Approx(g.w[j]).epsilon(1e-12));
      }
    }
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - per-type defaults: reservoir lg4_pos, battery block")
{
  using namespace storage_integration_test;
  const auto g = lobatto_cc(16, 24.0);
  const auto check_totals = [&](const BandMatrix& m)
  {
    // weekly (period) totals exact: column sums = block weights
    for (std::size_t j = 0; j < g.w.size(); ++j) {
      CHECK(m.col_sum(j) == doctest::Approx(g.w[j]).epsilon(1e-9));
    }
  };

  SUBCASE("defaults")
  {
    const auto sol = solve_mixed(mixed_case(g, "", "", ""));
    CHECK(sol.battery.max_row_nnz() == 1);  // block recursion
    CHECK(sol.reservoir.max_row_nnz() > 1);  // banded lg4_pos
    CHECK(sol.reservoir.max_row_nnz() <= 4);
    CHECK(sol.reservoir.min_weight() >= -1e-12);  // no negative weights
    check_totals(sol.battery);
    check_totals(sol.reservoir);
  }

  SUBCASE("per-element override beats the per-type default")
  {
    const auto sol =
        solve_mixed(mixed_case(g,
                               "",
                               R"(, "storage_integration": "lg4_pos")",
                               R"(, "storage_integration": "block")"));
    CHECK(sol.battery.max_row_nnz() > 1);
    CHECK(sol.battery.min_weight() >= -1e-12);
    CHECK(sol.reservoir.max_row_nnz() == 1);
    check_totals(sol.battery);
    check_totals(sol.reservoir);
  }

  SUBCASE("per-type option beats the global option")
  {
    const auto sol =
        solve_mixed(mixed_case(g,
                               R"(, "storage_integration": "lg4_pos",
                       "reservoir_storage_integration": "trapezoid")",
                               "",
                               ""));
    CHECK(sol.reservoir.max_row_nnz() == 2);
    CHECK(sol.battery.max_row_nnz() > 2);  // global lg4_pos
    check_totals(sol.battery);
    check_totals(sol.reservoir);
  }

  SUBCASE("resolution order")
  {
    PlanningOptions po;
    po.model_options.storage_integration = "lg6";
    const PlanningOptionsLP lp(po);
    CHECK(lp.storage_integration(OptName {"trapezoid"}, true).points == 2);
    CHECK(lp.storage_integration(OptName {}, true).points == 6);
    const PlanningOptionsLP def {PlanningOptions {}};
    CHECK(def.storage_integration(OptName {}, true).clip_negative);
    CHECK(def.storage_integration(OptName {}, true).points == 4);
    CHECK_FALSE(def.storage_integration(OptName {}, false).banded());
    CHECK_FALSE(def.storage_integration().banded());
  }
}

TEST_CASE(  // NOLINT
    "storage_integration - JSON round trip of the per-element fields")
{
  const auto bat = daw::json::from_json<Battery>(
      R"({"uid": 1, "name": "b", "storage_integration": "lg4_pos"})");
  CHECK(bat.storage_integration.value_or("") == "lg4_pos");
  const auto bat2 = daw::json::from_json<Battery>(daw::json::to_json(bat));
  CHECK(bat2.storage_integration.value_or("") == "lg4_pos");

  const auto rsv = daw::json::from_json<Reservoir>(
      R"({"uid": 1, "name": "r", "junction": 1,
          "storage_integration": "block"})");
  const auto rsv2 = daw::json::from_json<Reservoir>(daw::json::to_json(rsv));
  CHECK(rsv2.storage_integration.value_or("") == "block");
  CHECK_FALSE(daw::json::from_json<Reservoir>(
                  R"({"uid": 1, "name": "r", "junction": 1})")
                  .storage_integration.has_value());

  const auto mo = daw::json::from_json<ModelOptions>(
      R"({"reservoir_storage_integration": "lg6_pos",
          "battery_storage_integration": "trapezoid"})");
  const auto mo2 = daw::json::from_json<ModelOptions>(daw::json::to_json(mo));
  CHECK(mo2.reservoir_storage_integration.value_or("") == "lg6_pos");
  CHECK(mo2.battery_storage_integration.value_or("") == "trapezoid");
}

TEST_CASE(  // NOLINT
    "validate_planning - storage_integration names")
{
  using namespace storage_integration_test;
  const auto g = lobatto_cc(8, 24.0);
  Planning bad;
  bad.merge(parse_planning_json(
      mixed_case(g,
                 R"(, "battery_storage_integration": "cc_local5")",
                 R"(, "storage_integration": "lg5")",
                 "")));
  const auto r = validate_planning(bad);
  CHECK(std::ranges::any_of(
      r.errors,
      [](const auto& e) { return e.contains("battery_storage_integration"); }));
  CHECK(std::ranges::any_of(
      r.errors, [](const auto& e) { return e.contains("Battery 'bess'"); }));
  Planning good;
  good.merge(parse_planning_json(mixed_case(g, "", "", "")));
  CHECK_FALSE(std::ranges::any_of(
      validate_planning(good).errors,
      [](const auto& e) { return e.contains("storage_integration"); }));
}

TEST_CASE(  // NOLINT
    "storage_integration - cc (converter default) is the Clenshaw-Curtis rule")
{
  // On a periodic Chebyshev-Lobatto grid the block weights are the CC
  // weights (node 0 carries w_0 + w_N): `cc` / `clenshaw_curtis` give one
  // flow node per row with weight w_k (as sparse as the block balance,
  // all weights > 0), the same LP and objective as `block`, and over the
  // closed period the level change is exactly the CC quadrature of the
  // net flow.
  using namespace storage_integration_test;
  CHECK(StorageIntegration::is_valid("cc"));
  CHECK(StorageIntegration::is_valid("clenshaw_curtis"));
  CHECK_FALSE(StorageIntegration::parse("cc").banded());
  CHECK_FALSE(StorageIntegration::is_valid("clenshaw"));
  for (const int n : {8, 24}) {
    CAPTURE(n);
    auto g = lobatto_cc(n, 24.0);
    g.w.front() += g.w.back();
    g.w.pop_back();
    g.t.pop_back();
    const auto periodic = [](std::string json)
    {
      const std::string key = R"("sampling": "point")";
      const auto pos = json.find(key);
      REQUIRE(pos != std::string::npos);
      json.insert(pos + key.size(), R"(, "period_hours": 24)");
      return json;
    };
    for (const std::string_view name : {"cc", "clenshaw_curtis"}) {
      CAPTURE(name);
      const auto opts = std::format(
          R"(, "battery_storage_integration": "{0}",
               "reservoir_storage_integration": "{0}")",
          name);
      const auto cc = solve_mixed(periodic(mixed_case(g, opts, "", "")));
      const auto blk = solve_mixed(
          periodic(mixed_case(g,
                              R"(, "battery_storage_integration": "block",
               "reservoir_storage_integration": "block")",
                              "",
                              "")));
      CHECK(cc.obj == doctest::Approx(blk.obj));
      for (const auto* m : {&cc.battery, &cc.reservoir}) {
        CHECK(m->max_row_nnz() == 1);
        CHECK(m->min_weight() >= 0.0);
        for (std::size_t j = 0; j < g.w.size(); ++j) {
          CHECK(g.w[j] > 0.0);
          CHECK(m->col_sum(j) == doctest::Approx(g.w[j]).epsilon(1e-9));
        }
      }

      const auto sol = solve_day(periodic(day_case(g, name, 100.0)));
      REQUIRE(sol.net.size() == g.w.size());
      double stored = 0.0;
      double charged = 0.0;
      for (std::size_t k = 0; k < g.w.size(); ++k) {
        stored += g.w[k] * sol.net[k];
        charged += g.w[k] * std::max(sol.net[k], 0.0);
      }
      CHECK(charged > 1.0);
      CHECK(stored
            == doctest::Approx(sol.soc.back()).epsilon(1e-9).scale(charged));
    }
  }
}
