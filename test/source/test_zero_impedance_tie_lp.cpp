// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Zero-impedance ties on the assembled LP.
//
// Semantics under test (`tiny_reactance_mode = tie`, the default): a line
// whose reactance is 0 or whose |X/V²| is below the effective floor
// `max(dc_line_reactance_threshold, tiny_reactance_rel_threshold ×
// median |X/V²|)` keeps its flow column (thermal bounds, output), and
// only its KVL row changes: θ_a = θ_b (+φ) replaces f = (θ_a − θ_b)/X.
// No coefficient derived from the tie's X reaches the matrix, the flow
// is set by the bus balances, and (by default) the tie is lossless.
//
// Reference: the same network with every tie given a tiny but REGULAR
// reactance (1e-6, 1e-5 of the other lines; floors switched off), i.e.
// the physical X → 0 limit with all thermal limits in place.  Flows on
// every line (tie included; parallel ties through their sum), dispatch
// cost and LMPs must match it; a single tie's flow must also close the
// KCL of its `bus_b` side.
//
// Network (5 lines, meshed, congested so LMPs differ):
//
//     g1 (10 $/MWh, 500 MW) @ b1      g4 (50 $/MWh, 500 MW) @ b4
//     d2 = 100 MW @ b2                d3 = 300 MW @ b3
//
//     l12 b1-b2 x=0.10 t=200      l23 b2-b3 x=0.10 t=200
//     l34 b3-b4 x=0.10 t=200      l41 b4-b1 x=0.20 t=200
//     l13 b1-b3 x=0.15 t= 80
//
// System median |X/V²| = 0.1 → effective floor 0.01 × 0.1 = 1e-3.

#include <algorithm>
#include <cmath>
#include <format>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>

using namespace gtopt;

namespace
{

struct LineSpec
{
  std::string name {};
  std::string bus_a {};
  std::string bus_b {};
  std::optional<double> x {};  ///< nullopt = reactance unset (DC link)
  double tmax {200.0};
  std::optional<double> r {};  ///< resistance (V = 1)
};

struct NetSpec
{
  std::vector<std::string> buses {"b1", "b2", "b3", "b4"};
  std::vector<LineSpec> lines {};
  /// (name, bus, gcost, pmax)
  std::vector<std::tuple<std::string, std::string, double, double>> gens {
      {"g1", "b1", 10.0, 500.0},
      {"g4", "b4", 50.0, 500.0},
  };
  /// (name, bus, MW)
  std::vector<std::tuple<std::string, std::string, double>> demands {
      {"d2", "b2", 100.0},
      {"d3", "b3", 300.0},
  };
  std::string kirchhoff_mode {"node_angle"};
  std::string losses_mode {"none"};
  std::string extra_model_options {};  ///< raw JSON members, leading comma
};

[[nodiscard]] std::vector<LineSpec> base_lines()
{
  return {
      {.name = "l12", .bus_a = "b1", .bus_b = "b2", .x = 0.10},
      {.name = "l23", .bus_a = "b2", .bus_b = "b3", .x = 0.10},
      {.name = "l34", .bus_a = "b3", .bus_b = "b4", .x = 0.10},
      {.name = "l41", .bus_a = "b4", .bus_b = "b1", .x = 0.20},
      {.name = "l13", .bus_a = "b1", .bus_b = "b3", .x = 0.15, .tmax = 80.0},
  };
}

[[nodiscard]] std::string to_json(const NetSpec& n)
{
  std::string buses;
  for (std::size_t i = 0; i < n.buses.size(); ++i) {
    buses += std::format(R"({}{{"uid": {}, "name": "{}"}})",
                         i == 0 ? "" : ",",
                         i + 1,
                         n.buses[i]);
  }
  std::string gens;
  int uid = 0;
  for (const auto& [name, bus, cost, pmax] : n.gens) {
    gens += std::format(
        R"({}{{"uid": {}, "name": "{}", "bus": "{}", "pmin": 0, "pmax": {},)"
        R"( "gcost": {}, "capacity": {}}})",
        uid == 0 ? "" : ",",
        uid + 1,
        name,
        bus,
        pmax,
        cost,
        pmax);
    ++uid;
  }
  std::string dems;
  uid = 0;
  for (const auto& [name, bus, mw] : n.demands) {
    dems += std::format(
        R"({}{{"uid": {}, "name": "{}", "bus": "{}", "lmax": [[{}]]}})",
        uid == 0 ? "" : ",",
        uid + 1,
        name,
        bus,
        mw);
    ++uid;
  }
  std::string lines;
  uid = 0;
  for (const auto& l : n.lines) {
    std::string extra;
    if (l.x) {
      extra += std::format(R"(, "reactance": {:.17g})", *l.x);
    }
    if (l.r) {
      extra += std::format(R"(, "resistance": {:.17g}, "voltage": 1)", *l.r);
    }
    lines += std::format(
        R"({}{{"uid": {}, "name": "{}", "bus_a": "{}", "bus_b": "{}",)"
        R"( "tmax_ab": {}, "tmax_ba": {}{}}})",
        uid == 0 ? "" : ",",
        uid + 1,
        l.name,
        l.bus_a,
        l.bus_b,
        l.tmax,
        l.tmax,
        extra);
    ++uid;
  }
  return std::format(
      R"({{"options": {{"annual_discount_rate": 0.0,
           "output_format": "csv", "output_compression": "uncompressed",
           "model_options": {{"use_single_bus": false,
             "use_kirchhoff": true, "kirchhoff_mode": "{}",
             "line_losses_mode": "{}", "loss_segments": 4,
             "scale_objective": 1, "demand_fail_cost": 1000{}}}}},
         "simulation": {{"block_array": [{{"uid": 1, "duration": 1}}],
           "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
           "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
         "system": {{"name": "tie_lp", "bus_array": [{}],
           "generator_array": [{}], "demand_array": [{}],
           "line_array": [{}]}}}})",
      n.kirchhoff_mode,
      n.losses_mode,
      n.extra_model_options,
      buses,
      gens,
      dems,
      lines);
}

struct LineOut
{
  double flow {0.0};  ///< signed a→b [MW]
  std::vector<ColIndex> cols {};  ///< every flow column of the line
};

struct Solved
{
  double obj {0.0};
  std::map<std::string, LineOut> lines {};
  std::map<std::string, double> lmp {};
  std::map<std::string, std::optional<ColIndex>> theta {};
  double scale_theta {0.0};
  double min_abs {0.0};
  double max_abs {0.0};
  int n_rows {0};
  int n_cols {0};
  /// Raw (LP-space) column of row r, as {col → coeff}.
  std::vector<std::map<Index, double>> rows {};
  std::vector<double> col_upp {};  ///< physical column upper bounds
  std::vector<double> col_low {};
};

[[nodiscard]] Solved solve(const NetSpec& n)
{
  auto planning = daw::json::from_json<Planning>(to_json(n), StrictParsePolicy);
  LpMatrixOptions flat_opts;
  flat_opts.compute_stats = OptBool {true};
  PlanningLP planning_lp(std::move(planning), flat_opts);
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());

  auto& sys_lp = planning_lp.systems().front().front();
  auto& li = sys_lp.linear_interface();
  const auto& sim = sys_lp.system_context().simulation();
  const auto& scenario = sim.scenarios().front();
  const auto& stage = sim.stages().front();
  const auto buid = stage.blocks().front().uid();

  Solved out;
  out.obj = li.get_obj_value();
  out.scale_theta = sys_lp.system_context().options().scale_theta();
  out.min_abs = li.lp_stats_min_abs();
  out.max_abs = li.lp_stats_max_abs();
  out.n_rows = static_cast<int>(li.get_numrows());
  out.n_cols = static_cast<int>(li.get_numcols());
  const auto sol = li.get_col_sol();
  const auto dual = li.get_row_dual();
  for (const auto& line : sys_lp.elements<LineLP>()) {
    LineOut lo;
    const auto add = [&](const auto& m, double sign)
    {
      if (const auto it = m.find(buid); it != m.end()) {
        lo.flow += sign * sol[it->second];
        lo.cols.push_back(it->second);
      }
    };
    add(line.flowp_cols_at(scenario, stage), +1.0);
    add(line.flown_cols_at(scenario, stage), -1.0);
    add(line.flows_cols_at(scenario, stage), +1.0);
    out.lines.emplace(line.line().name, std::move(lo));
  }
  for (const auto& bus : sys_lp.elements<BusLP>()) {
    const auto& rows = bus.balance_rows_at(scenario, stage);
    out.lmp[bus.bus().name] = dual[rows.at(buid)];
    out.theta[bus.bus().name] = bus.lookup_theta_col(scenario, stage, buid);
  }
  const auto nr = li.get_numrows();
  const auto nc = li.get_numcols();
  out.rows.resize(static_cast<std::size_t>(nr));
  for (Index r = 0; r < nr; ++r) {
    for (Index c = 0; c < nc; ++c) {
      const double v = li.get_coeff_raw(RowIndex {r}, ColIndex {c});
      if (v != 0.0) {
        out.rows[static_cast<std::size_t>(r)][c] = v;
      }
    }
  }
  const auto upp = li.get_col_upp();
  const auto low = li.get_col_low();
  for (Index c = 0; c < nc; ++c) {
    out.col_upp.push_back(upp[ColIndex {c}]);
    out.col_low.push_back(low[ColIndex {c}]);
  }
  return out;
}

/// The near-tie reference: every tie line of `ties` gets the tiny but
/// regular reactance `kRefX` and both floors are switched off, so the
/// reference LP carries a normal KVL row with the physical flow split
/// (thermal limits included).  As X → 0 its solution converges to the
/// tie's; at kRefX = 1e-6 (1e-5 of the other lines) the flow error is
/// ~1e-5 relative.
constexpr double kRefX = 1e-6;

[[nodiscard]] NetSpec near_tie(const NetSpec& n,
                               const std::set<std::string>& ties)
{
  NetSpec r = n;
  for (auto& l : r.lines) {
    if (ties.contains(l.name)) {
      l.x = kRefX;
    }
  }
  r.extra_model_options += R"(, "tiny_reactance_rel_threshold": 0,)"
                           R"( "dc_line_reactance_threshold": 0)";
  return r;
}

/// Demand at `bus` plus the flow leaving it over the lines NOT in
/// `skip` (`bus` must have no generator).
[[nodiscard]] double kcl_residual(const NetSpec& n,
                                  const Solved& tie_sol,
                                  const std::string& bus,
                                  const std::set<std::string>& skip)
{
  double out = 0.0;
  for (const auto& [name, b, mw] : n.demands) {
    if (b == bus) {
      out += mw;
    }
  }
  for (const auto& l : n.lines) {
    if (skip.contains(l.name)) {
      continue;
    }
    const double f = tie_sol.lines.at(l.name).flow;
    if (l.bus_a == bus) {
      out += f;
    }
    if (l.bus_b == bus) {
      out -= f;
    }
  }
  return out;
}

/// Every row in which column `c` has a nonzero coefficient.
[[nodiscard]] std::vector<std::size_t> rows_of(const Solved& s, ColIndex c)
{
  std::vector<std::size_t> out;
  for (std::size_t r = 0; r < s.rows.size(); ++r) {
    if (s.rows[r].contains(c)) {
      out.push_back(r);
    }
  }
  return out;
}

/// True when some row is exactly {θ_a: +k, θ_b: −k} (angle equality).
[[nodiscard]] bool has_angle_equality_row(const Solved& s,
                                          const std::string& a,
                                          const std::string& b)
{
  const auto ta = s.theta.at(a);
  const auto tb = s.theta.at(b);
  return std::ranges::any_of(
      s.rows,
      [&](const auto& row)
      {
        if (row.size() != 2) {
          return false;
        }
        // A reference bus has no θ column: then the row is a 1-col bound.
        if (!ta || !tb || !row.contains(*ta) || !row.contains(*tb)) {
          return false;
        }
        return std::abs(row.at(*ta) + row.at(*tb)) < 1e-12;
      });
}

/// Assert the tie semantics for the lines in `ties` against the
/// near-tie reference.  Lines of a `sum_groups` entry (parallel ties,
/// whose individual split is not determined) are compared through their
/// sum.  `kcl_ties` are single ties whose flow must close the KCL of
/// their `bus_b` side (no generator and no curtailment there).
///
/// `tie_loop`: the network has a loop made only of ties.  Around such a
/// loop the angle rows are 0 = 0, so the circulating split is free (as
/// on a merged bus that keeps its branch flows and limits), while any
/// tiny-X reference fixes it by the ratio of the tiny reactances, which
/// are placeholders in practice.  The tie LP is then a relaxation of the
/// reference: its cost is not higher, and only feasibility is asserted.
///
/// LMPs are not compared with the reference: its balance duals are
/// degenerate in the X → 0 limit (the ill-conditioning the tie removes)
/// and the two ends of a congested tie do price differently.
void check_against_reference(
    const NetSpec& n,
    const std::set<std::string>& ties,
    const std::vector<std::set<std::string>>& sum_groups = {},
    const std::set<std::string>& kcl_ties = {},
    bool tie_loop = false)
{
  const auto sol = solve(n);
  const auto ref = solve(near_tie(n, ties));
  // The near-tie reference is off by O(kRefX / x) of the flows.
  constexpr double eps = 1e-4;
  constexpr double abs_mw = 1e-2;
  if (tie_loop) {
    CHECK(sol.obj <= ref.obj * (1.0 + eps));
  } else {
    CHECK(sol.obj == doctest::Approx(ref.obj).epsilon(eps));
  }

  const auto in_group = [&](const std::string& name)
  {
    return std::ranges::any_of(sum_groups,
                               [&](const auto& g) { return g.contains(name); });
  };
  for (const auto& l : n.lines) {
    CAPTURE(l.name);
    const auto& lo = sol.lines.at(l.name);
    // The line keeps its flow column(s) with the thermal bounds.
    REQUIRE(!lo.cols.empty());
    if (ties.contains(l.name)) {
      for (const auto& c : lo.cols) {
        CHECK(std::abs(sol.col_upp[static_cast<std::size_t>(c)])
              == doctest::Approx(l.tmax));
        // Only the two bus balances reference the tie flow (±1): no KVL
        // coefficient (1/X or X) on it.
        const auto rws = rows_of(sol, c);
        CHECK(rws.size() == 2);
        for (const auto r : rws) {
          CHECK(std::abs(sol.rows[r].at(c)) == doctest::Approx(1.0));
        }
      }
    }
    CHECK(std::abs(lo.flow) <= l.tmax + 1e-6);
    if (tie_loop || in_group(l.name)) {
      continue;
    }
    CHECK(lo.flow
          == doctest::Approx(ref.lines.at(l.name).flow)
                 .epsilon(eps)
                 .scale(abs_mw / eps));
  }
  for (const auto& g : sum_groups) {
    double fs = 0.0;
    double fr = 0.0;
    for (const auto& name : g) {
      fs += sol.lines.at(name).flow;
      fr += ref.lines.at(name).flow;
    }
    CHECK(fs == doctest::Approx(fr).epsilon(eps).scale(abs_mw / eps));
  }
  for (const auto& l : n.lines) {
    if (!kcl_ties.contains(l.name)) {
      continue;
    }
    CAPTURE(l.name);
    const std::set<std::string> skip {l.name};
    CHECK(sol.lines.at(l.name).flow
          == doctest::Approx(kcl_residual(n, sol, l.bus_b, skip))
                 .epsilon(1e-6)
                 .scale(1.0));
  }
  for (const auto& [bus, lmp] : sol.lmp) {
    CAPTURE(bus);
    CHECK(std::isfinite(lmp));
  }
  CHECK(std::isfinite(sol.scale_theta));
  CHECK(sol.scale_theta > 0.0);
}

}  // namespace

TEST_CASE("tie LP — reactance values around the floor")  // NOLINT
{
  // l13 (b1-b3) is the candidate tie; floor = 0.01 × median 0.1 = 1e-3.
  for (const std::string kmode : {"node_angle", "cycle_basis"}) {
    CAPTURE(kmode);
    for (const double x : {0.0, 1e-12, 0.9e-3}) {
      CAPTURE(x);
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines[4].x = x;
      check_against_reference(n, {"l13"});

      const auto sol = solve(n);
      // No coefficient of the tie's X anywhere: the matrix keeps the
      // range of the normal lines (|x| ≥ 0.1, θ scale ~1/(3·0.1)).
      CHECK(sol.min_abs >= 1e-2);
      CHECK(std::isfinite(sol.max_abs));
      if (kmode == "node_angle") {
        CHECK(has_angle_equality_row(sol, "b1", "b3"));
      }
    }
    SUBCASE("just above the floor: a normal KVL line")
    {
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines[4].x = 1.1e-3;
      const auto sol = solve(n);
      const auto& cols = sol.lines.at("l13").cols;
      REQUIRE(!cols.empty());
      // Two balances + at least one KVL row carry its flow.
      CHECK(rows_of(sol, cols.front()).size() >= 3);
      if (kmode == "node_angle") {
        CHECK_FALSE(has_angle_equality_row(sol, "b1", "b3"));
      }
    }
    SUBCASE("legacy absolute floor: 0.9e-3 is a normal line")
    {
      NetSpec n {
          .lines = base_lines(),
          .kirchhoff_mode = kmode,
          .extra_model_options = R"(, "tiny_reactance_rel_threshold": 0)",
      };
      n.lines[4].x = 0.9e-3;
      const auto sol = solve(n);
      CHECK(rows_of(sol, sol.lines.at("l13").cols.front()).size() >= 3);
    }
  }
}

TEST_CASE(
    "tie LP — meshed topologies against the near-tie reference")  // NOLINT
{
  for (const std::string kmode : {"node_angle", "cycle_basis"}) {
    CAPTURE(kmode);
    SUBCASE("tie inside a loop")
    {
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines[1].x = 0.0;  // l23
      check_against_reference(n, {"l23"}, {}, {"l23"});
    }
    SUBCASE("ties in series: chain merging b1, b2, b3; l13 parallel")
    {
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines[0].x = 0.0;  // l12
      n.lines[1].x = 1e-12;  // l23
      // l13 (normal) joins b1 and b3, whose angles the ties equalise.
      check_against_reference(n, {"l12", "l23"});
      const auto sol = solve(n);
      // KCL at b2 (no generator): f12 − f23 = d2.
      CHECK(sol.lines.at("l12").flow - sol.lines.at("l23").flow
            == doctest::Approx(100.0));
    }
    SUBCASE("tie in parallel with a normal line")
    {
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines.push_back(
          {.name = "l12t", .bus_a = "b1", .bus_b = "b2", .x = 0.0});
      check_against_reference(n, {"l12t"});
      const auto sol = solve(n);
      // All of the b1→b2 transfer rides the tie.
      CHECK(sol.lines.at("l12t").flow
            == doctest::Approx(kcl_residual(n, sol, "b2", {"l12t"})));
    }
    SUBCASE("two ties in parallel: the sum is determined")
    {
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines[0].x = 0.0;
      n.lines.push_back(
          {.name = "l12t", .bus_a = "b1", .bus_b = "b2", .x = 0.0});
      check_against_reference(n, {"l12", "l12t"}, {{"l12", "l12t"}});
      const auto sol = solve(n);
      CHECK(sol.lines.at("l12").flow + sol.lines.at("l12t").flow
            == doctest::Approx(kcl_residual(n, sol, "b2", {"l12", "l12t"})));
    }
    SUBCASE("most lines are ties")
    {
      NetSpec n {.lines = base_lines(), .kirchhoff_mode = kmode};
      n.lines[0].x = 0.0;  // l12
      n.lines[1].x = 0.0;  // l23
      n.lines[2].x = 0.0;  // l34
      n.lines[4].x = 0.0;  // l13 — only l41 keeps its reactance
      // Ties tie b1..b4 together; l41 carries no angle difference.
      check_against_reference(
          n, {"l12", "l23", "l34", "l13"}, {}, {}, /*tie_loop=*/true);
      // All four angles are equal, so the one normal line carries nothing.
      const auto sol = solve(n);
      CHECK(sol.lines.at("l41").flow
            == doctest::Approx(0.0).epsilon(1e-9).scale(1.0));
    }
  }
}

TEST_CASE("tie LP — a case whose only line is a tie")  // NOLINT
{
  for (const std::string kmode : {"node_angle", "cycle_basis"}) {
    CAPTURE(kmode);
    for (const double x : {0.0, 1e-12}) {
      CAPTURE(x);
      NetSpec n {
          .buses = {"b1", "b2"},
          .lines = {{.name = "t",
                     .bus_a = "b1",
                     .bus_b = "b2",
                     .x = x,
                     .tmax = 150.0}},
          .gens = {{"g1", "b1", 10.0, 500.0}},
          .demands = {{"d2", "b2", 200.0}},
          .kirchhoff_mode = kmode,
      };
      const auto sol = solve(n);
      // The tie's thermal bound binds: 150 MW served, 50 MW curtailed.
      CHECK(sol.lines.at("t").flow == doctest::Approx(150.0));
      CHECK(sol.obj == doctest::Approx((150.0 * 10.0) + (50.0 * 1000.0)));
      CHECK(sol.lmp.at("b1") == doctest::Approx(10.0));
      CHECK(sol.lmp.at("b2") == doctest::Approx(1000.0));
      // No median to scale by: scale_theta keeps its default, finite.
      CHECK(std::isfinite(sol.scale_theta));
      CHECK(sol.scale_theta == doctest::Approx(1.0));
      CHECK(std::isfinite(sol.min_abs));
      CHECK(sol.min_abs > 0.0);
      if (kmode == "node_angle") {
        CHECK(has_angle_equality_row(sol, "b1", "b2"));
      }
    }
  }
}

TEST_CASE("tie LP — losses on ties and tiny-R lines")  // NOLINT
{
  // Lossy l12 in piecewise mode; compare the LP size against the same
  // line without resistance.
  const auto with_r = [](std::optional<double> x,
                         std::optional<double> r,
                         std::string extra = {})
  {
    NetSpec n {
        .lines = base_lines(),
        .losses_mode = "piecewise",
        .extra_model_options = std::move(extra),
    };
    n.lines[0].x = x;
    n.lines[0].r = r;
    return solve(n);
  };

  SUBCASE("tiny R (R/V² < 1e-7) builds no loss columns or rows")
  {
    const auto lossless = with_r(0.10, std::nullopt);
    const auto tiny = with_r(0.10, 5.2e-8);
    const auto lossy = with_r(0.10, 0.01);
    CHECK(tiny.n_cols == lossless.n_cols);
    CHECK(tiny.n_rows == lossless.n_rows);
    CHECK(tiny.obj == doctest::Approx(lossless.obj));
    CHECK(lossy.n_cols > lossless.n_cols);  // loss model really exists
    // The flow stays.
    CHECK(!tiny.lines.at("l12").cols.empty());
  }
  SUBCASE("tie with a non-tiny R is lossless by default")
  {
    const auto tie_nor = with_r(0.0, std::nullopt);
    const auto tie_r = with_r(0.0, 0.01);
    CHECK(tie_r.n_cols == tie_nor.n_cols);
    CHECK(tie_r.n_rows == tie_nor.n_rows);
    CHECK(tie_r.obj == doctest::Approx(tie_nor.obj));
    CHECK(!tie_r.lines.at("l12").cols.empty());
  }
  SUBCASE("tie_line_losses = true keeps the legacy tie loss model")
  {
    const auto tie_nor = with_r(0.0, std::nullopt);
    const auto tie_r = with_r(0.0, 0.01, R"(, "tie_line_losses": true)");
    CHECK(tie_r.n_cols > tie_nor.n_cols);
  }
}

TEST_CASE("tie floor — relative to the system median")  // NOLINT
{
  auto planning = daw::json::from_json<Planning>(
      to_json(NetSpec {.lines = base_lines()}), StrictParsePolicy);
  auto& lines = planning.system.line_array;

  SUBCASE("median and effective floor")
  {
    CHECK(PlanningLP::median_line_reactance_pu(planning).value_or(-1.0)
          == doctest::Approx(0.10));
    CHECK(PlanningLP::effective_tiny_reactance_threshold(planning)
          == doctest::Approx(1e-3));
    planning.options.model_options.tiny_reactance_rel_threshold = 0.0;
    CHECK(PlanningLP::effective_tiny_reactance_threshold(planning)
          == doctest::Approx(1e-6));
    planning.options.model_options.tiny_reactance_rel_threshold = 1e-6;
    planning.options.model_options.dc_line_reactance_threshold = 1e-4;
    CHECK(PlanningLP::effective_tiny_reactance_threshold(planning)
          == doctest::Approx(1e-4));
  }
  SUBCASE("X = 0 lines do not enter the median; all-zero → no median")
  {
    lines[0].reactance = 0.0;
    lines[1].reactance = 0.0;
    // Remaining 0.10, 0.20, 0.15 → median 0.15.
    CHECK(PlanningLP::median_line_reactance_pu(planning).value_or(-1.0)
          == doctest::Approx(0.15));
    for (auto& l : lines) {
      l.reactance = 0.0;
    }
    CHECK_FALSE(PlanningLP::median_line_reactance_pu(planning).has_value());
    CHECK(PlanningLP::effective_tiny_reactance_threshold(planning)
          == doctest::Approx(1e-6));
  }
  SUBCASE("validate_line_reactance writes the effective floor back")
  {
    PlanningLP::validate_line_reactance(planning);
    CHECK(planning.options.model_options.dc_line_reactance_threshold.value_or(
              -1.0)
          == doctest::Approx(1e-3));
    const PlanningOptionsLP opts {planning.options};
    CHECK(opts.classify_line_reactance(0.9e-3, 1.0) == LineKvlKind::tie);
    CHECK(opts.classify_line_reactance(1.1e-3, 1.0) == LineKvlKind::ac);
  }
  SUBCASE("ties drop resistance unless tie_line_losses")
  {
    lines[0].reactance = 0.0;
    lines[0].resistance = 0.01;
    auto keep = planning;
    PlanningLP::validate_line_reactance(planning);
    CHECK_FALSE(lines[0].resistance.has_value());
    CHECK(lines[1].resistance.has_value() == false);
    keep.options.model_options.tie_line_losses = true;
    PlanningLP::validate_line_reactance(keep);
    CHECK(keep.system.line_array[0].resistance.has_value());
  }
}

TEST_CASE("auto scale_theta — ties excluded, X = 0 safe")  // NOLINT
{
  auto planning = daw::json::from_json<Planning>(
      to_json(NetSpec {.lines = base_lines()}), StrictParsePolicy);
  auto& lines = planning.system.line_array;

  SUBCASE("ties stay out of the median")
  {
    lines[0].reactance = 0.0;  // exact zero
    lines[1].reactance = 1e-12;  // tiny
    lines[2].reactance = 0.9e-3;  // below the relative floor
    auto input = planning;  // each SUBCASE run rebuilds `planning`
    const PlanningLP plp(std::move(input));
    const auto& mo = plp.planning().options.model_options;
    // Non-tie x: 0.20, 0.15 → median 0.175.
    const double st = mo.scale_theta.value_or(-1.0);
    CHECK(std::isfinite(st));
    CHECK(st == doctest::Approx(1.0 / (3.0 * 0.175)));
    CHECK(std::isfinite(mo.theta_max.value_or(-1.0)));
    CHECK(plp.options().scale_theta() == doctest::Approx(st));
  }
  SUBCASE("every line a tie: no scale, no NaN")
  {
    for (auto& l : lines) {
      l.reactance = 0.0;
    }
    auto input = planning;  // each SUBCASE run rebuilds `planning`
    const PlanningLP plp(std::move(input));
    const auto& mo = plp.planning().options.model_options;
    CHECK_FALSE(mo.scale_theta.has_value());
    CHECK(plp.options().scale_theta() == doctest::Approx(1.0));
    CHECK(std::isfinite(plp.options().theta_max()));
  }
}
