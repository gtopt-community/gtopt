// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_dlr.cpp — dynamic line rating through critical spans
// (`Line.dlr`, line_dlr.hpp / line_dlr_lp.hpp / LineLP::add_to_lp).
//
// 2-bus fixture: a cheap generator (10 $/MWh) at b1, an expensive one
// (100 $/MWh) and the demand at b2, one line b1→b2.  Whenever the DLR
// rating is below the demand the line is congested, so the flow equals
// the rating and the rating dual is the 90 $/MWh price spread.
//
// Covers:
//   * one representative span, block-varying weather → per-block IEEE 738
//     rating (exact MW from `dlr_span_rating_mw`);
//   * several spans: min applied, binding span (critical_span) recorded;
//   * min vs rows: same objective and same summed span duals for the
//     flow / sending / full thermal-limit modes and the none /
//     bidirectional / tangent_signed_flow / piecewise_direct loss modes,
//     plus a tie;
//   * emergency tier → overload band;
//   * static tmax cap; per-scenario weather; precomputed `rating`;
//   * η, v_pu and power-factor scaling; dominated-row dropping;
//   * validation errors / warnings; JSON round trip.

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <functional>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/json/json_line.hpp>
#include <gtopt/line_dlr_lp.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace test_line_dlr_ns
{

namespace
{

constexpr double kCheap = 10.0;
constexpr double kDear = 100.0;
constexpr double kV = 220.0;  // kV

// [scenario][stage][block] with one stage.
using Stb = std::vector<std::vector<std::vector<double>>>;

[[nodiscard]] Stb per_block(std::vector<double> v)
{
  return Stb {{std::move(v)}};
}

[[nodiscard]] Stb per_scenario(std::vector<std::vector<double>> v)
{
  Stb out;
  for (auto& s : v) {
    out.push_back({std::move(s)});
  }
  return out;
}

/// A span with a precomputed MW rating.
[[nodiscard]] CriticalSpan rated_span(Uid uid,
                                      std::string name,
                                      Stb rating,
                                      std::optional<Stb> emergency = {})
{
  CriticalSpan s {
      .uid = uid,
      .name = std::move(name),
      .rating = OptSTBRealFieldSched {std::move(rating)},
  };
  if (emergency) {
    s.rating_emergency = OptSTBRealFieldSched {std::move(*emergency)};
  }
  return s;
}

[[nodiscard]] DlrConductor drake()
{
  return {
      .diameter = 0.02814,
      .r25 = 7.283e-5,
      .r75 = 8.688e-5,
      .emissivity = 0.8,
      .absorptivity = 0.8,
      .subconductors = 1,
  };
}

struct Case
{
  LineDlr dlr {};
  std::string_view loss_mode {"none"};
  int segments {4};
  std::string_view limit {};  ///< line_thermal_limit_mode; empty → flow
  std::string_view allocation {};  ///< loss_allocation_mode
  double resistance {0.0};  ///< Ω; 0 → lossless
  std::optional<double> tmax {2000.0};  ///< static tmax_ab / tmax_ba
  double overload_penalty {0.0};
  double demand {1000.0};
  int nblocks {3};
  int nscen {1};
  OptBool use_line_dlr {};  ///< model_options.use_line_dlr; unset → default
  OptReal tmax_normal {};  ///< static tmax_normal_ab / _ba
  OptName slack_bound {};  ///< Line.overload_slack_bound
  OptReal slack_ratio {};  ///< Line.max_overload_ratio
  OptName global_slack_bound {};  ///< model_options.overload_slack_bound
  OptReal global_ratio {};  ///< model_options.max_overload_ratio
  /// Line drawn b2→b1 (bus_a = b2), so the physical flow runs B→A.
  bool reverse {false};
  /// model_options.line_thermal_limit_mode; empty → unset.
  std::string_view global_limit {};
  /// Line.dlr.limit_mode; empty → unset.
  std::string_view dlr_limit {};
  /// model_options.dlr_limit_mode; empty → unset.
  std::string_view global_dlr_limit {};
  /// Per-block static tmax_ab / tmax_ba (overrides `tmax`).
  std::vector<double> tmax_blocks {};
  /// Extra Line settings (PLEXOS limit fields, classification, …),
  /// applied last.
  std::function<void(Line&)> line_fn {};
};

struct Fixture
{
  System system;
  Simulation simulation;
  PlanningOptions opts;
  PlanningOptionsLP options;
  SimulationLP sim_lp;
  SystemLP sys_lp;

  explicit Fixture(const Case& c)
      : system {make_system(c)}
      , simulation {make_simulation(c)}
      , options(make_options(opts, c))
      , sim_lp(simulation, options)
      , sys_lp(system, sim_lp, build_opts())
  {
  }

  [[nodiscard]] auto& lp() { return sys_lp.linear_interface(); }

  [[nodiscard]] const LineLP& line() const
  {
    return sys_lp.elements<LineLP>().front();
  }

  static System make_system(const Case& c)
  {
    Line ln {
        .uid = Uid {1},
        .name = "l1",
        .bus_a = Uid {c.reverse ? 2 : 1},
        .bus_b = Uid {c.reverse ? 1 : 2},
        .voltage = kV,
        .line_losses_mode = OptName {std::string(c.loss_mode)},
        .loss_segments = c.segments,
    };
    if (c.resistance > 0.0) {
      ln.resistance = c.resistance;
    }
    if (c.tmax) {
      ln.tmax_ab = *c.tmax;
      ln.tmax_ba = *c.tmax;
    }
    if (!c.tmax_blocks.empty()) {
      const std::vector<std::vector<double>> tb {c.tmax_blocks};
      ln.tmax_ab = tb;
      ln.tmax_ba = tb;
    }
    if (!c.limit.empty()) {
      ln.line_thermal_limit_mode = std::string(c.limit);
    }
    if (!c.allocation.empty()) {
      ln.loss_allocation_mode = std::string(c.allocation);
    }
    if (c.overload_penalty > 0.0) {
      ln.overload_penalty = c.overload_penalty;
    }
    if (c.tmax_normal) {
      ln.tmax_normal_ab = *c.tmax_normal;
      ln.tmax_normal_ba = *c.tmax_normal;
    }
    ln.overload_slack_bound = c.slack_bound;
    ln.max_overload_ratio = c.slack_ratio;
    ln.dlr = c.dlr;
    if (!c.dlr_limit.empty()) {
      ln.dlr->limit_mode = std::string(c.dlr_limit);
    }
    if (c.line_fn) {
      c.line_fn(ln);
    }
    return {
        .name = "Dlr2Bus",
        .bus_array =
            {
                {
                    .uid = Uid {1},
                    .name = "b1",
                },
                {
                    .uid = Uid {2},
                    .name = "b2",
                },
            },
        .demand_array =
            {
                {
                    .uid = Uid {1},
                    .name = "d2",
                    .bus = Uid {2},
                    .capacity = c.demand,
                },
            },
        .generator_array =
            {
                {
                    .uid = Uid {1},
                    .name = "g1",
                    .bus = Uid {1},
                    .gcost = kCheap,
                    .capacity = 5000.0,
                },
                {
                    .uid = Uid {2},
                    .name = "g2",
                    .bus = Uid {2},
                    .gcost = kDear,
                    .capacity = 5000.0,
                },
            },
        .line_array = {ln},
    };
  }

  static Simulation make_simulation(const Case& c)
  {
    Simulation sim;
    for (int b = 0; b < c.nblocks; ++b) {
      sim.block_array.push_back({
          .uid = Uid {b + 1},
          .duration = 1,
      });
    }
    sim.stage_array = {
        {
            .uid = Uid {1},
            .first_block = 0,
            .count_block = static_cast<Size>(c.nblocks),
        },
    };
    for (int s = 0; s < c.nscen; ++s) {
      sim.scenario_array.push_back({
          .uid = Uid {s + 1},
          .probability_factor = 1.0 / c.nscen,
      });
    }
    return sim;
  }

  static PlanningOptionsLP make_options(PlanningOptions& opts, const Case& c)
  {
    opts.model_options.use_single_bus = false;
    opts.model_options.use_kirchhoff = false;
    opts.model_options.scale_objective = 1.0;
    opts.model_options.demand_fail_cost = 1000.0;
    opts.model_options.use_line_dlr = c.use_line_dlr;
    opts.model_options.overload_slack_bound = c.global_slack_bound;
    opts.model_options.max_overload_ratio = c.global_ratio;
    if (!c.global_dlr_limit.empty()) {
      opts.model_options.dlr_limit_mode = std::string(c.global_dlr_limit);
    }
    if (!c.global_limit.empty()) {
      opts.model_options.line_thermal_limit_mode = std::string(c.global_limit);
    }
    opts.lp_matrix_options.col_with_names = true;
    opts.lp_matrix_options.row_with_names = true;
    opts.lp_matrix_options.col_with_name_map = true;
    opts.lp_matrix_options.row_with_name_map = true;
    return PlanningOptionsLP(opts);
  }

  static LpMatrixOptions build_opts()
  {
    LpMatrixOptions bo;
    bo.col_with_names = true;
    bo.col_with_name_map = true;
    bo.row_with_names = true;
    bo.row_with_name_map = true;
    return bo;
  }
};

struct Solved
{
  double obj {};
  /// [scenario][block] signed line flow.
  std::vector<std::vector<double>> flow;
  /// [scenario][block] Σ_k span dual (∂obj/∂T_k).
  std::vector<std::vector<double>> dual_sum;
  /// [scenario][block][span] span duals.
  std::vector<std::vector<std::vector<double>>> duals;
  /// [scenario][block] critical span uid.
  std::vector<std::vector<double>> critical;
  /// [scenario][block] dlr rating.
  std::vector<std::vector<double>> rating;
  /// [scenario][block] line loss = Σ generation − Σ served load (the
  /// fixture's only lossy element is the line), whatever the loss mode.
  std::vector<std::vector<double>> loss;
  /// [scenario][block] bus-balance duals (LMP) of b1 / b2.
  std::vector<std::vector<double>> lmp1;
  std::vector<std::vector<double>> lmp2;
  /// [scenario][block] binding limit part (`LineLP::LimitPart` as int,
  /// −1 = none reported) and its dual ∂obj/∂L, A→B / B→A.
  std::vector<std::vector<int>> part_p;
  std::vector<std::vector<int>> part_n;
  std::vector<std::vector<double>> lim_p;
  std::vector<std::vector<double>> lim_n;
};

[[nodiscard]] Solved solve(Fixture& fx)
{
  auto& li = fx.lp();
  const auto r = li.resolve();
  REQUIRE(r.has_value());
  REQUIRE(r.value() == 0);
  li.ensure_duals();
  const auto sol = li.get_col_sol();
  const auto rc = li.get_col_cost();
  const auto dual = li.get_row_dual();

  const auto& line = fx.line();
  const auto& stage = fx.sim_lp.stages().front();
  Solved out;
  out.obj = li.get_obj_value();
  for (const auto& scen : fx.sim_lp.scenarios()) {
    auto& fl = out.flow.emplace_back();
    auto& ds = out.dual_sum.emplace_back();
    auto& dd = out.duals.emplace_back();
    auto& cr = out.critical.emplace_back();
    auto& ra = out.rating.emplace_back();
    auto& lo = out.loss.emplace_back();
    auto& l1 = out.lmp1.emplace_back();
    auto& l2 = out.lmp2.emplace_back();
    const auto& limp = line.limitp_refs_at(scen, stage);
    const auto& limn = line.limitn_refs_at(scen, stage);
    auto& pp = out.part_p.emplace_back();
    auto& pn = out.part_n.emplace_back();
    auto& dp = out.lim_p.emplace_back();
    auto& dn = out.lim_n.emplace_back();
    const auto& gens = fx.sys_lp.elements<GeneratorLP>();
    const auto& dems = fx.sys_lp.elements<DemandLP>();
    const auto& buses = fx.sys_lp.elements<BusLP>();
    const auto sd = line.span_duals(
        {scen.uid(), stage.uid()},
        [&](ColIndex c) { return sol[c]; },
        [&](ColIndex c) { return rc[c]; },
        [&](RowIndex rr) { return dual[rr]; });
    for (const auto& block : stage.blocks()) {
      const auto b = block.uid();
      double f = 0.0;
      const auto add = [&](const auto& m, double sign)
      {
        if (const auto it = m.find(b); it != m.end()) {
          f += sign * sol[it->second];
        }
      };
      const auto add_segs = [&](const auto& m, double sign)
      {
        if (const auto it = m.find(b); it != m.end()) {
          for (const auto& c : it->second) {
            f += sign * sol[c];
          }
        }
      };
      add(line.flowp_cols_at(scen, stage), +1.0);
      add(line.flown_cols_at(scen, stage), -1.0);
      add(line.flows_cols_at(scen, stage), +1.0);
      add_segs(line.flowp_seg_cols_at(scen, stage), +1.0);
      add_segs(line.flown_seg_cols_at(scen, stage), -1.0);
      fl.push_back(f);

      double s = 0.0;
      std::vector<double> v;
      if (const auto it = sd.find(b); it != sd.end()) {
        v = it->second;
        for (const double x : v) {
          s += x;
        }
      }
      ds.push_back(s);
      dd.push_back(v);
      const auto& cs = line.critical_span_at(scen, stage);
      cr.push_back(cs.contains(b) ? cs.at(b) : -99.0);
      const auto& rt = line.dlr_rating_at(scen, stage);
      ra.push_back(rt.contains(b) ? rt.at(b) : -99.0);
      double gl = 0.0;
      for (const auto& g : gens) {
        gl += sol[g.generation_cols_at(scen, stage).at(b)];
      }
      for (const auto& d : dems) {
        gl -= sol[d.load_cols_at(scen, stage).at(b)];
      }
      lo.push_back(gl);
      double span = 0.0;
      if (const auto it = sd.find(b); it != sd.end()) {
        for (const double x : it->second) {
          span += x;
        }
      }
      const auto push_part = [&](const auto& refs, auto& parts, auto& duals)
      {
        if (const auto it = refs.find(b); it != refs.end()) {
          parts.push_back(static_cast<int>(it->second.part));
          duals.push_back(LineLP::limit_dual(
              it->second,
              span,
              [&](ColIndex c) { return sol[c]; },
              [&](ColIndex c) { return rc[c]; },
              [&](RowIndex rr) { return dual[rr]; }));
        } else {
          parts.push_back(-1);
          duals.push_back(0.0);
        }
      };
      push_part(limp, pp, dp);
      push_part(limn, pn, dn);
      for (const auto& bus : buses) {
        const double lmp = dual[bus.balance_rows_at(scen, stage).at(b)];
        (bus.uid() == Uid {1} ? l1 : l2).push_back(lmp);
      }
    }
  }
  return out;
}

[[nodiscard]] int count_rows(const LinearInterface& li, std::string_view sub)
{
  int n = 0;
  for (const auto& [name, idx] : li.row_name_map()) {
    if (name.contains(sub)) {
      ++n;
    }
  }
  return n;
}

[[nodiscard]] LineDlr three_span_dlr(std::string mode)
{
  // Per-block minima: b1 → s3 (90), b2 → s2 (120), b3 → s1 (300).
  LineDlr dlr {
      .span_mode = std::move(mode),
  };
  dlr.critical_spans = {
      rated_span(11, "s1", per_block({100.0, 200.0, 300.0})),
      rated_span(12, "s2", per_block({150.0, 120.0, 310.0})),
      rated_span(13, "s3", per_block({90.0, 250.0, 320.0})),
  };
  return dlr;
}

[[nodiscard]] bool has_error(const ValidationResult& r, std::string_view sub)
{
  return std::ranges::any_of(r.errors,
                             [sub](const auto& e) { return e.contains(sub); });
}

[[nodiscard]] bool has_warning(const ValidationResult& r, std::string_view sub)
{
  return std::ranges::any_of(r.warnings,
                             [sub](const auto& e) { return e.contains(sub); });
}

[[nodiscard]] Planning planning_with(LineDlr dlr)
{
  const Case c {
      .dlr = std::move(dlr),
  };
  return Planning {
      .simulation = Fixture::make_simulation(c),
      .system = Fixture::make_system(c),
  };
}

}  // namespace

TEST_CASE("LineDlrSpanMode parses")  // NOLINT
{
  CHECK(enum_from_name<LineDlrSpanMode>("min").value_or(LineDlrSpanMode::rows)
        == LineDlrSpanMode::min);
  CHECK(enum_from_name<LineDlrSpanMode>("rows").value_or(LineDlrSpanMode::min)
        == LineDlrSpanMode::rows);
  CHECK_FALSE(enum_from_name<LineDlrSpanMode>("bogus").has_value());
  LineDlr d;
  CHECK(d.span_mode_enum() == LineDlrSpanMode::min);
  d.span_mode = "rows";
  CHECK(d.span_mode_enum() == LineDlrSpanMode::rows);
  CHECK(dlr_default_span_uid(7, 2) == 7002);
}

TEST_CASE("DLR single span limits the flow per block (IEEE 738)")  // NOLINT
{
  // Block-varying wind: 0.61, 1.0 and 3.0 m/s perpendicular, Ta 40 °C,
  // sun 997.7 W/m², Tmax 75 °C, 220 kV.
  const std::vector<double> wind {0.61, 1.0, 3.0};
  LineDlr dlr {
      .conductor = drake(),
      .tmax_conductor = 75.0,
  };
  dlr.critical_spans = {
      CriticalSpan {
          .uid = 21,
          .name = "rep",
          .azimuth = 0.0,
          .ambient_temperature = 40.0,
          .wind_speed = OptSTBRealFieldSched {per_block(wind)},
          .wind_direction = 90.0,
          .solar_irradiance = 997.7005431224591,
      },
  };
  Fixture fx({
      .dlr = dlr,
      .demand = 2000.0,
  });
  const auto res = solve(fx);
  const ieee738::ConductorParams cond {
      .diameter = 0.02814,
      .r25 = 7.283e-5,
      .r75 = 8.688e-5,
      .emissivity = 0.8,
      .absorptivity = 0.8,
  };
  for (std::size_t b = 0; b < wind.size(); ++b) {
    CAPTURE(b);
    const double mw =
        dlr_span_rating_mw(cond,
                           75.0,
                           {
                               .ambient_temperature = 40.0,
                               .wind_speed = wind[b],
                               .attack_angle = 90.0,
                               .solar_irradiance = 997.7005431224591,
                           },
                           kV,
                           1.0,
                           1.0,
                           1,
                           1.0);
    CHECK(res.flow[0][b] == doctest::Approx(mw).epsilon(1e-7));
    CHECK(res.rating[0][b] == doctest::Approx(mw).epsilon(1e-9));
    CHECK(res.critical[0][b] == doctest::Approx(21.0));
    // Congested: ∂obj/∂T = −(100 − 10).
    CHECK(res.dual_sum[0][b] == doctest::Approx(-(kDear - kCheap)));
  }
  // Block 0 is the reference 726 A case: √3·220·725.90/1000 = 276.6 MW.
  CHECK(res.flow[0][0] == doctest::Approx(276.6).epsilon(1e-3));
  CHECK(res.flow[0][0] < res.flow[0][1]);
  CHECK(res.flow[0][1] < res.flow[0][2]);
}

TEST_CASE("DLR multiple spans: min applied, critical span recorded")  // NOLINT
{
  Fixture fx({
      .dlr = three_span_dlr("min"),
  });
  const auto res = solve(fx);
  CHECK(res.flow[0][0] == doctest::Approx(90.0));
  CHECK(res.flow[0][1] == doctest::Approx(120.0));
  CHECK(res.flow[0][2] == doctest::Approx(300.0));
  CHECK(res.critical[0][0] == doctest::Approx(13.0));
  CHECK(res.critical[0][1] == doctest::Approx(12.0));
  CHECK(res.critical[0][2] == doctest::Approx(11.0));
  CHECK(res.rating[0][0] == doctest::Approx(90.0));
  // The dual goes to the argmin span only.
  CHECK(res.duals[0][0][2] == doctest::Approx(-90.0));
  CHECK(res.duals[0][0][0] == doctest::Approx(0.0));
  CHECK(res.duals[0][1][1] == doctest::Approx(-90.0));
  CHECK(res.duals[0][2][0] == doctest::Approx(-90.0));
  // min mode adds no span rows.
  CHECK(count_rows(fx.lp(), "span_thermal") == 0);
  // Objective: Σ_b 10·T_b + 100·(1000 − T_b).
  double expected = 0.0;
  for (const double t : {90.0, 120.0, 300.0}) {
    expected += (kCheap * t) + (kDear * (1000.0 - t));
  }
  CHECK(res.obj == doctest::Approx(expected));
}

TEST_CASE("DLR rows mode drops dominated rows")  // NOLINT
{
  Fixture fx({
      .dlr = three_span_dlr("rows"),
  });
  // One binding span per block and direction: 3 blocks × 2 directions.
  CHECK(count_rows(fx.lp(), "span_thermalp") == 3);
  CHECK(count_rows(fx.lp(), "span_thermaln") == 3);
  const auto res = solve(fx);
  CHECK(res.flow[0][0] == doctest::Approx(90.0));
  CHECK(res.duals[0][0][2] == doctest::Approx(-90.0));
  CHECK(res.duals[0][1][1] == doctest::Approx(-90.0));
  CHECK(res.duals[0][2][0] == doctest::Approx(-90.0));

  SUBCASE("a lower static tmax dominates every span")
  {
    auto dlr = three_span_dlr("rows");
    Fixture fx2({
        .dlr = dlr,
        .tmax = 80.0,
        .dlr_limit = "min",
    });
    CHECK(count_rows(fx2.lp(), "span_thermal") == 0);
    const auto r2 = solve(fx2);
    CHECK(r2.flow[0][0] == doctest::Approx(80.0));
    CHECK(r2.critical[0][0] == doctest::Approx(-1.0));
    CHECK(r2.dual_sum[0][0] == doctest::Approx(0.0));
  }
}

TEST_CASE("DLR min vs rows: same objective and summed span duals")  // NOLINT
{
  struct Mode
  {
    std::string_view loss;
    std::string_view limit;
    std::string_view allocation;
  };
  const auto modes = std::to_array<Mode>({
      {
          .loss = "none",
          .limit = "flow",
          .allocation = "",
      },
      {
          .loss = "none",
          .limit = "full",
          .allocation = "",
      },
      {
          .loss = "bidirectional",
          .limit = "flow",
          .allocation = "",
      },
      {
          .loss = "bidirectional",
          .limit = "sending",
          .allocation = "split",
      },
      {
          .loss = "bidirectional",
          .limit = "full",
          .allocation = "",
      },
      {
          .loss = "tangent_signed_flow",
          .limit = "flow",
          .allocation = "",
      },
      {
          .loss = "tangent_signed_flow",
          .limit = "full",
          .allocation = "",
      },
      {
          .loss = "piecewise_direct",
          .limit = "flow",
          .allocation = "",
      },
      {
          .loss = "piecewise_direct",
          .limit = "sending",
          .allocation = "sender",
      },
      {
          .loss = "piecewise_direct",
          .limit = "full",
          .allocation = "",
      },
      {
          .loss = "linear",
          .limit = "full",
          .allocation = "",
      },
  });
  for (const auto& m : modes) {
    CAPTURE(m.loss);
    CAPTURE(m.limit);
    CAPTURE(m.allocation);
    const auto make = [&](std::string mode)
    {
      return Case {
          .dlr = three_span_dlr(std::move(mode)),
          .loss_mode = m.loss,
          .limit = m.limit,
          .allocation = m.allocation,
          .resistance = 20.0,  // ℓ(300 MW) ≈ 37 MW
      };
    };
    Fixture fmin(make("min"));
    Fixture frows(make("rows"));
    const auto rmin = solve(fmin);
    const auto rrows = solve(frows);
    CHECK(rmin.obj == doctest::Approx(rrows.obj).epsilon(1e-8));
    for (std::size_t b = 0; b < 3; ++b) {
      CAPTURE(b);
      CHECK(rmin.flow[0][b] == doctest::Approx(rrows.flow[0][b]).epsilon(1e-7));
      CHECK(rmin.dual_sum[0][b]
            == doctest::Approx(rrows.dual_sum[0][b]).epsilon(1e-6));
      // Congested: the rating carries a strictly negative dual.
      CHECK(rmin.dual_sum[0][b] < -1.0);
    }
  }
}

TEST_CASE("DLR min vs rows: tie between two spans")  // NOLINT
{
  const auto make = [](std::string mode)
  {
    LineDlr dlr {
        .span_mode = std::move(mode),
    };
    dlr.critical_spans = {
        rated_span(1, "a", per_block({150.0, 150.0, 400.0})),
        rated_span(2, "b", per_block({150.0, 200.0, 400.0})),
    };
    return Case {
        .dlr = dlr,
    };
  };
  Fixture fmin(make("min"));
  Fixture frows(make("rows"));
  // Ties keep both rows: block 1 and 3 have two rows per direction.
  CHECK(count_rows(frows.lp(), "span_thermalp") == 5);
  const auto rmin = solve(fmin);
  const auto rrows = solve(frows);
  CHECK(rmin.obj == doctest::Approx(rrows.obj));
  for (std::size_t b = 0; b < 3; ++b) {
    CAPTURE(b);
    CHECK(rmin.dual_sum[0][b] == doctest::Approx(rrows.dual_sum[0][b]));
    CHECK(rmin.dual_sum[0][b] == doctest::Approx(-90.0));
  }
  // min attributes the tie to the first argmin.
  CHECK(rmin.critical[0][0] == doctest::Approx(1.0));
  CHECK(rmin.duals[0][0][0] == doctest::Approx(-90.0));
}

TEST_CASE("DLR emergency tier drives the overload band")  // NOLINT
{
  const auto make = [](std::string mode, double penalty)
  {
    LineDlr dlr {
        .span_mode = std::move(mode),
    };
    dlr.critical_spans = {
        rated_span(1,
                   "a",
                   per_block({100.0, 100.0, 100.0}),
                   per_block({150.0, 150.0, 150.0})),
        rated_span(2,
                   "b",
                   per_block({120.0, 90.0, 120.0}),
                   per_block({140.0, 160.0, 140.0})),
    };
    return Case {
        .dlr = dlr,
        .overload_penalty = penalty,
    };
  };
  SUBCASE("cheap penalty: flow up to the emergency min")
  {
    for (const std::string mode : {"min", "rows"}) {
      CAPTURE(mode);
      Fixture fx(make(mode, 50.0));
      const auto res = solve(fx);
      // Emergency min: 140, 150, 140.  Normal min: 100, 90, 100.
      CHECK(res.flow[0][0] == doctest::Approx(140.0));
      CHECK(res.flow[0][1] == doctest::Approx(150.0));
      CHECK(res.flow[0][2] == doctest::Approx(140.0));
      // Obj: energy + 50 $/MWh on the excursion above the normal min.
      double expected = 0.0;
      const std::array fl {140.0, 150.0, 140.0};
      const std::array nm {100.0, 90.0, 100.0};
      for (std::size_t b = 0; b < 3; ++b) {
        expected += (kCheap * fl[b]) + (kDear * (1000.0 - fl[b]))
            + (50.0 * (fl[b] - nm[b]));
      }
      CHECK(res.obj == doctest::Approx(expected));
      // Total rating dual = hard (−40) + band (−50) = −90.
      for (std::size_t b = 0; b < 3; ++b) {
        CHECK(res.dual_sum[0][b] == doctest::Approx(-90.0));
      }
      // Block 1: hard cap from span b (140) → −40; band from span a
      // (normal 100) → −50.
      CHECK(res.duals[0][0][1] == doctest::Approx(-40.0));
      CHECK(res.duals[0][0][0] == doctest::Approx(-50.0));
    }
  }
  SUBCASE("expensive penalty: flow stays at the normal min")
  {
    for (const std::string mode : {"min", "rows"}) {
      CAPTURE(mode);
      Fixture fx(make(mode, 500.0));
      const auto res = solve(fx);
      CHECK(res.flow[0][0] == doctest::Approx(100.0));
      CHECK(res.flow[0][1] == doctest::Approx(90.0));
      CHECK(res.critical[0][1] == doctest::Approx(2.0));
      CHECK(res.dual_sum[0][1] == doctest::Approx(-90.0));
      CHECK(res.duals[0][1][1] == doctest::Approx(-90.0));
    }
  }
  SUBCASE("no overload_penalty: the normal rating is the hard cap")
  {
    Fixture fx(make("min", 0.0));
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(100.0));
    CHECK(res.flow[0][1] == doctest::Approx(90.0));
  }
}

TEST_CASE("DLR limit_mode min: static tmax still caps the flow")  // NOLINT
{
  for (const std::string mode : {"min", "rows"}) {
    CAPTURE(mode);
    Fixture fx({
        .dlr = three_span_dlr(mode),
        .tmax = 110.0,
        .dlr_limit = "min",
    });
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(90.0));  // span s3
    CHECK(res.flow[0][1] == doctest::Approx(110.0));  // static cap
    CHECK(res.flow[0][2] == doctest::Approx(110.0));  // static cap
    CHECK(res.critical[0][0] == doctest::Approx(13.0));
    CHECK(res.critical[0][1] == doctest::Approx(-1.0));
    CHECK(res.rating[0][1] == doctest::Approx(110.0));
    CHECK(res.dual_sum[0][0] == doctest::Approx(-90.0));
    // The static cap's dual is not a span dual.
    CHECK(res.dual_sum[0][1] == doctest::Approx(0.0));
  }
}

TEST_CASE("DLR: no static tmax uses the DLR envelope")  // NOLINT
{
  for (const std::string mode : {"min", "rows"}) {
    CAPTURE(mode);
    Fixture fx({
        .dlr = three_span_dlr(mode),
        .tmax = std::nullopt,
    });
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(90.0));
    CHECK(res.flow[0][2] == doctest::Approx(300.0));
    CHECK(res.dual_sum[0][2] == doctest::Approx(-90.0));
  }
}

TEST_CASE("DLR: per-scenario weather gives a per-scenario rating")  // NOLINT
{
  LineDlr dlr {};
  dlr.critical_spans = {
      rated_span(
          5, "s", per_scenario({{100.0, 200.0, 300.0}, {50.0, 60.0, 70.0}})),
  };
  Fixture fx({
      .dlr = dlr,
      .nscen = 2,
  });
  const auto res = solve(fx);
  REQUIRE(res.flow.size() == 2);
  CHECK(res.flow[0][0] == doctest::Approx(100.0));
  CHECK(res.flow[0][2] == doctest::Approx(300.0));
  CHECK(res.flow[1][0] == doctest::Approx(50.0));
  CHECK(res.flow[1][2] == doctest::Approx(70.0));
  // Weighted by the 0.5 scenario probability.
  CHECK(res.dual_sum[0][0] == doctest::Approx(-45.0));
  CHECK(res.dual_sum[1][1] == doctest::Approx(-45.0));

  SUBCASE("weather fields per scenario")
  {
    LineDlr wdlr {
        .conductor = drake(),
        .voltage = 220.0,
    };
    wdlr.critical_spans = {
        CriticalSpan {
            .uid = 9,
            .ambient_temperature = OptSTBRealFieldSched {per_scenario(
                {{40.0, 40.0, 40.0}, {10.0, 10.0, 10.0}})},
            .wind_speed = 0.61,
            .wind_direction = 90.0,
            .solar_irradiance = 0.0,
        },
    };
    Fixture fw({
        .dlr = wdlr,
        .nscen = 2,
    });
    const auto rw = solve(fw);
    // Colder scenario → higher rating.
    CHECK(rw.flow[1][0] > rw.flow[0][0]);
    // Night, 40 °C, 0.61 m/s perpendicular: 886.26 A → 337.7 MW.
    CHECK(rw.flow[0][0]
          == doctest::Approx(ieee738::ampacity_to_mw(
                                 886.2589246743955, 220.0, 1.0, 1.0, 1, 1.0))
                 .epsilon(1e-7));
  }
}

TEST_CASE("DLR: safety factor, v_pu, power factor, subconductors")  // NOLINT
{
  const auto make = [](double eta, double vpu, double pf, int nsub)
  {
    auto cond = drake();
    cond.subconductors = nsub;
    LineDlr dlr {
        .conductor = cond,
        .v_pu = vpu,
        .power_factor = pf,
        .safety_factor = eta,
    };
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 3,
            .ambient_temperature = 40.0,
            .wind_speed = 0.61,
            .wind_direction = 90.0,
            .solar_irradiance = 997.7005431224591,
        },
    };
    return Case {
        .dlr = dlr,
        .demand = 3000.0,
    };
  };
  Fixture base(make(1.0, 1.0, 1.0, 1));
  Fixture scaled(make(0.9, 0.95, 0.98, 1));
  Fixture bundle(make(1.0, 1.0, 1.0, 2));
  const auto rb = solve(base);
  const auto rs = solve(scaled);
  const auto rn = solve(bundle);
  CHECK(rb.flow[0][0] == doctest::Approx(276.6).epsilon(1e-3));
  CHECK(rs.flow[0][0] == doctest::Approx(rb.flow[0][0] * 0.9 * 0.95 * 0.98));
  CHECK(rn.flow[0][0] == doctest::Approx(2.0 * rb.flow[0][0]));
}

TEST_CASE("DLR: per-span tmax_conductor override")  // NOLINT
{
  LineDlr dlr {
      .conductor = drake(),
      .tmax_conductor = 75.0,
      .voltage = 230.0,
  };
  dlr.critical_spans = {
      CriticalSpan {
          .uid = 1,
          .ambient_temperature = 40.0,
          .wind_speed = 0.61,
          .wind_direction = 90.0,
          .solar_irradiance = 997.7005431224591,
          .tmax_conductor = 100.0,
      },
  };
  Fixture fx({
      .dlr = dlr,
      .demand = 3000.0,
  });
  const auto res = solve(fx);
  // The IEEE 738 Drake example: 1025.36 A at 230 kV.
  CHECK(res.flow[0][0]
        == doctest::Approx(ieee738::ampacity_to_mw(
                               1025.3603200725527, 230.0, 1.0, 1.0, 1, 1.0))
               .epsilon(1e-7));
}

TEST_CASE("DLR validation errors")  // NOLINT
{
  SUBCASE("empty critical_spans")
  {
    auto p = planning_with(LineDlr {});
    CHECK(has_error(validate_planning(p), "critical_spans is empty"));
  }
  SUBCASE("missing weather without rating")
  {
    LineDlr dlr {
        .conductor = drake(),
        .voltage = 220.0,
    };
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1,
            .ambient_temperature = 30.0,
            .wind_speed = 1.0,
        },
    };
    auto p = planning_with(dlr);
    const auto r = validate_planning(p);
    CHECK(has_error(r, "missing weather field(s) wind_direction"));
    CHECK(has_error(r, "solar_irradiance"));
  }
  SUBCASE("weather without conductor or voltage")
  {
    LineDlr dlr {};
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1,
            .ambient_temperature = 30.0,
            .wind_speed = 1.0,
            .wind_direction = 0.0,
            .solar_irradiance = 0.0,
        },
    };
    auto p = planning_with(dlr);
    p.system.line_array.front().voltage.reset();
    const auto r = validate_planning(p);
    CHECK(has_error(r, "conductor is required"));
    CHECK(has_error(r, "voltage in kV"));
  }
  SUBCASE("negative / invalid parameters")
  {
    auto cond = drake();
    cond.diameter = -0.01;
    cond.emissivity = 1.5;
    cond.subconductors = 0;
    LineDlr dlr {
        .conductor = cond,
        .tmax_conductor = 75.0,
        .tmax_conductor_emergency = 60.0,
        .voltage = -1.0,
        .power_factor = 1.2,
        .safety_factor = 0.0,
        .span_mode = "maximum",
    };
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1,
            .ambient_temperature = 30.0,
            .wind_speed = -1.0,
            .wind_direction = 0.0,
            .solar_irradiance = -5.0,
        },
    };
    auto p = planning_with(dlr);
    const auto r = validate_planning(p);
    CHECK(has_error(r, "conductor.diameter"));
    CHECK(has_error(r, "conductor.emissivity"));
    CHECK(has_error(r, "conductor.subconductors"));
    CHECK(has_error(r, "tmax_conductor_emergency"));
    CHECK(has_error(r, "voltage = -1"));
    CHECK(has_error(r, "power_factor = 1.2"));
    CHECK(has_error(r, "safety_factor"));
    CHECK(has_error(r, "unknown span_mode 'maximum'"));
    CHECK(has_error(r, "wind_speed has a value below 0"));
    CHECK(has_error(r, "solar_irradiance has a value below 0"));
  }
  SUBCASE("mismatched inline dimensions")
  {
    LineDlr dlr {};
    dlr.critical_spans = {
        rated_span(1, "s", per_block({100.0, 200.0})),  // 2 of 3 blocks
    };
    auto p = planning_with(dlr);
    CHECK(has_error(validate_planning(p), "inline array must be"));
  }
  SUBCASE("duplicate span uid across lines")
  {
    LineDlr dlr {};
    dlr.critical_spans = {
        rated_span(1, "s", per_block({100.0, 200.0, 300.0})),
        rated_span(1, "t", per_block({100.0, 200.0, 300.0})),
    };
    auto p = planning_with(dlr);
    CHECK(has_error(validate_planning(p), "already used"));
  }
  SUBCASE("rating_emergency without rating")
  {
    LineDlr dlr {};
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1,
            .rating_emergency =
                OptSTBRealFieldSched {per_block({1.0, 2.0, 3.0})},
        },
    };
    auto p = planning_with(dlr);
    CHECK(has_error(validate_planning(p), "rating_emergency needs"));
  }
  SUBCASE("valid precomputed DLR passes")
  {
    auto p = planning_with(three_span_dlr("rows"));
    const auto r = validate_planning(p);
    CHECK_FALSE(std::ranges::any_of(
        r.errors, [](const auto& e) { return e.contains("dlr"); }));
  }
  SUBCASE("warning when the line is never in service")
  {
    auto p = planning_with(three_span_dlr("min"));
    p.system.line_array.front().in_service = OptTBIntBoolFieldSched {0};
    CHECK(has_warning(validate_planning(p), "DLR is inert"));
  }
}

TEST_CASE("DLR: missing file-free weather cell throws at build")  // NOLINT
{
  // Validation would flag this; build it directly to exercise the
  // LineDlrLP runtime guard.
  LineDlr dlr {
      .conductor = drake(),
      .voltage = 220.0,
  };
  dlr.critical_spans = {
      CriticalSpan {
          .uid = 1,
          .ambient_temperature = 30.0,
      },
  };
  CHECK_THROWS_WITH_AS(Fixture({.dlr = dlr}),
                       doctest::Contains("wind_speed"),
                       std::runtime_error);
}

TEST_CASE("DLR JSON round trip")  // NOLINT
{
  constexpr std::string_view json = R"({
    "uid": 1, "name": "l1", "bus_a": 1, "bus_b": 2, "voltage": 220,
    "tmax_ab": 500, "tmax_ba": 500,
    "dlr": {
      "conductor": {"diameter": 0.02814, "r25": 7.283e-5, "r75": 8.688e-5,
                    "emissivity": 0.7, "absorptivity": 0.9,
                    "subconductors": 2},
      "tmax_conductor": 75,
      "tmax_conductor_emergency": 100,
      "voltage": 230,
      "v_pu": 0.98, "power_factor": 0.95, "safety_factor": 0.9,
      "span_mode": "rows",
      "critical_spans": [
        {"uid": 101, "name": "s1", "azimuth": 35, "elevation": 420,
         "ambient_temperature": 25, "wind_speed": "dlr_wind_speed",
         "wind_direction": [[[90, 180]]], "solar_irradiance": 800,
         "tmax_conductor": 80},
        {"uid": 102, "rating": [[[120, 130]]],
         "rating_emergency": [[[150, 160]]]}
      ]
    }
  })";
  const auto line = daw::json::from_json<Line>(json);
  REQUIRE(line.dlr.has_value());
  const auto d = line.dlr.value_or(LineDlr {});
  REQUIRE(d.conductor.has_value());
  const auto cond = d.conductor.value_or(DlrConductor {});
  CHECK(cond.subconductors.value_or(0) == 2);
  CHECK(cond.emissivity.value_or(0.0) == doctest::Approx(0.7));
  CHECK(d.tmax_conductor_emergency.value_or(0.0) == doctest::Approx(100.0));
  CHECK(d.voltage.value_or(0.0) == doctest::Approx(230.0));
  CHECK(d.span_mode_enum() == LineDlrSpanMode::rows);
  REQUIRE(d.critical_spans.size() == 2);
  const auto& s1 = d.critical_spans[0];
  CHECK(s1.uid.value_or(0) == 101);
  CHECK(s1.name.value_or("") == "s1");
  CHECK(s1.azimuth.value_or(0.0) == doctest::Approx(35.0));
  CHECK(s1.elevation.value_or(0.0) == doctest::Approx(420.0));
  using Sched = OptSTBRealFieldSched::value_type;
  REQUIRE(s1.wind_speed.has_value());
  CHECK(std::holds_alternative<FileSched>(s1.wind_speed.value_or(Sched {})));
  REQUIRE(s1.wind_direction.has_value());
  CHECK(std::holds_alternative<std::vector<std::vector<std::vector<Real>>>>(
      s1.wind_direction.value_or(Sched {})));
  CHECK(s1.tmax_conductor.value_or(0.0) == doctest::Approx(80.0));
  CHECK(d.critical_spans[1].rating.has_value());
  CHECK(d.critical_spans[1].rating_emergency.has_value());

  // Serialise and parse back: identical JSON.
  const auto out = daw::json::to_json(line);
  const auto again = daw::json::from_json<Line>(out);
  CHECK(daw::json::to_json(again) == out);
  REQUIRE(again.dlr.has_value());
  const auto again_dlr = again.dlr.value_or(LineDlr {});
  CHECK(again_dlr.critical_spans.size() == 2);
  CHECK(again_dlr.safety_factor.value_or(0.0) == doctest::Approx(0.9));

  // A line without dlr serialises without the key.
  const auto plain = daw::json::from_json<Line>(
      R"({"uid": 2, "name": "l2", "bus_a": 1, "bus_b": 2})");
  CHECK_FALSE(plain.dlr.has_value());
  CHECK_FALSE(daw::json::to_json(plain).contains("dlr"));
}

TEST_CASE("DLR option: model_options.use_line_dlr")  // NOLINT
{
  CHECK(PlanningOptionsLP(PlanningOptions {}).use_line_dlr());
  for (const std::string mode : {"min", "rows"}) {
    CAPTURE(mode);
    {
      Fixture fx({
          .dlr = three_span_dlr(mode),
          .tmax = 250.0,
      });
      CHECK(solve(fx).flow[0][0] == doctest::Approx(90.0));
    }
    {
      Fixture fx({
          .dlr = three_span_dlr(mode),
          .tmax = 250.0,
          .use_line_dlr = true,
      });
      CHECK(solve(fx).flow[0][1] == doctest::Approx(120.0));
    }
    {
      Fixture fx({
          .dlr = three_span_dlr(mode),
          .tmax = 250.0,
          .use_line_dlr = false,
      });
      CHECK(count_rows(fx.lp(), "span_thermal") == 0);
      const auto res = solve(fx);
      for (std::size_t b = 0; b < 3; ++b) {
        CHECK(res.flow[0][b] == doctest::Approx(250.0));
        CHECK(res.critical[0][b] == doctest::Approx(-99.0));  // absent
        CHECK(res.dual_sum[0][b] == doctest::Approx(0.0));
      }
    }
  }
}

TEST_CASE("DLR option: use_emergency")  // NOLINT
{
  const auto make = [](OptBool use_emergency)
  {
    LineDlr dlr {
        .use_emergency = use_emergency,
    };
    dlr.critical_spans = {
        rated_span(1,
                   "a",
                   per_block({100.0, 100.0, 100.0}),
                   per_block({150.0, 150.0, 150.0})),
    };
    return Case {
        .dlr = dlr,
        .overload_penalty = 50.0,
    };
  };
  for (const auto& [opt, expected] : {std::pair {OptBool {}, 150.0},
                                      std::pair {OptBool {true}, 150.0},
                                      std::pair {OptBool {false}, 100.0}})
  {
    CAPTURE(opt.has_value());
    Fixture fx(make(opt));
    CHECK(solve(fx).flow[0][0] == doctest::Approx(expected));
  }
}

namespace
{

/// One weather-driven span (Drake, 220 kV) with the given options.
[[nodiscard]] double weather_flow(const std::function<void(LineDlr&)>& tweak,
                                  double wind_speed,
                                  double wind_direction,
                                  double sun)
{
  LineDlr dlr {
      .conductor = drake(),
      .tmax_conductor = 75.0,
      .voltage = 220.0,
  };
  dlr.critical_spans = {
      CriticalSpan {
          .uid = 1,
          .azimuth = 0.0,
          .ambient_temperature = 40.0,
          .wind_speed = wind_speed,
          .wind_direction = wind_direction,
          .solar_irradiance = sun,
      },
  };
  tweak(dlr);
  Fixture fx({
      .dlr = dlr,
      .demand = 3000.0,
  });
  return solve(fx).flow[0][0];
}

[[nodiscard]] double drake_mw(double phi, double wind, double sun)
{
  return dlr_span_rating_mw({.diameter = 0.02814,
                             .r25 = 7.283e-5,
                             .r75 = 8.688e-5,
                             .emissivity = 0.8,
                             .absorptivity = 0.8},
                            75.0,
                            {.ambient_temperature = 40.0,
                             .wind_speed = wind,
                             .attack_angle = phi,
                             .solar_irradiance = sun},
                            220.0,
                            1.0,
                            1.0,
                            1,
                            1.0);
}

}  // namespace

TEST_CASE("DLR option: low-wind rule")  // NOLINT
{
  // 0.3 m/s blowing ALONG the span (φ = 0 by direction).
  const auto none = [](LineDlr&) {};
  SUBCASE("default: below 0.5 m/s the angle is 45°")
  {
    CHECK(weather_flow(none, 0.3, 0.0, 0.0)
          == doctest::Approx(drake_mw(45.0, 0.3, 0.0)).epsilon(1e-7));
  }
  SUBCASE("low_wind_speed = 0 disables the rule (φ from direction)")
  {
    CHECK(
        weather_flow([](LineDlr& d) { d.low_wind_speed = 0.0; }, 0.3, 0.0, 0.0)
        == doctest::Approx(drake_mw(0.0, 0.3, 0.0)).epsilon(1e-7));
  }
  SUBCASE("custom low_wind_angle")
  {
    CHECK(
        weather_flow([](LineDlr& d) { d.low_wind_angle = 90.0; }, 0.3, 0.0, 0.0)
        == doctest::Approx(drake_mw(90.0, 0.3, 0.0)).epsilon(1e-7));
  }
  SUBCASE("custom low_wind_speed above the actual wind")
  {
    CHECK(
        weather_flow([](LineDlr& d) { d.low_wind_speed = 1.0; }, 0.61, 0.0, 0.0)
        == doctest::Approx(drake_mw(45.0, 0.61, 0.0)).epsilon(1e-7));
  }
}

TEST_CASE("DLR option: solar_mode")  // NOLINT
{
  constexpr double sun = 1000.0;
  SUBCASE("ghi (default): q_s = α·S·D")
  {
    CHECK(weather_flow([](LineDlr&) {}, 0.61, 90.0, sun)
          == doctest::Approx(drake_mw(90.0, 0.61, sun)).epsilon(1e-7));
    CHECK(
        weather_flow([](LineDlr& d) { d.solar_mode = "ghi"; }, 0.61, 90.0, sun)
        == doctest::Approx(drake_mw(90.0, 0.61, sun)).epsilon(1e-7));
  }
  SUBCASE("none: solar gain ignored, field optional")
  {
    CHECK(
        weather_flow([](LineDlr& d) { d.solar_mode = "none"; }, 0.61, 90.0, sun)
        == doctest::Approx(drake_mw(90.0, 0.61, 0.0)).epsilon(1e-7));
    LineDlr dlr {
        .conductor = drake(),
        .voltage = 220.0,
        .solar_mode = "none",
    };
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1,
            .ambient_temperature = 40.0,
            .wind_speed = 0.61,
            .wind_direction = 90.0,
        },
    };
    auto p = planning_with(dlr);
    CHECK_FALSE(std::ranges::any_of(validate_planning(p).errors,
                                    [](const auto& e)
                                    { return e.contains("dlr"); }));
    Fixture fx({
        .dlr = dlr,
        .demand = 3000.0,
    });
    CHECK(solve(fx).flow[0][0]
          == doctest::Approx(drake_mw(90.0, 0.61, 0.0)).epsilon(1e-7));
  }
  SUBCASE("unknown solar_mode is rejected")
  {
    LineDlr dlr {
        .solar_mode = "projected",
    };
    dlr.critical_spans = {
        rated_span(1, "s", per_block({1.0, 2.0, 3.0})),
    };
    auto p = planning_with(dlr);
    CHECK(has_error(validate_planning(p), "unknown solar_mode"));
  }
}

TEST_CASE("DLR option: conductor resistance from r20 + alpha_r")  // NOLINT
{
  // AAAC Flint (CEN, Los Vilos–Las Palmas 220 kV): D 25.17 mm,
  // R20 0.0889 Ω/km, α_R 0.0036 /K, ac factor 1.02, ε = α = 0.5, 55 °C.
  const auto flint = [](bool r20_form)
  {
    DlrConductor c {
        .diameter = 0.02517,
        .emissivity = 0.5,
        .absorptivity = 0.5,
    };
    if (r20_form) {
      c.r20 = 8.89e-5;
      c.alpha_r = 0.0036;
      c.ac_factor = 1.02;
    } else {
      const auto rp = ieee738::resistance_from_r20(8.89e-5, 0.0036, 1.02);
      c.r25 = rp.r25;
      c.r75 = rp.r75;
    }
    LineDlr dlr {
        .conductor = c,
        .tmax_conductor = 55.0,
        .voltage = 220.0,
    };
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1,
            .ambient_temperature = 25.0,
            .wind_speed = 0.61,
            .wind_direction = 90.0,
            .solar_irradiance = 1000.0,
        },
    };
    return dlr;
  };
  Fixture f20({
      .dlr = flint(true),
      .demand = 3000.0,
  });
  Fixture f2575({
      .dlr = flint(false),
      .demand = 3000.0,
  });
  const double mw20 = solve(f20).flow[0][0];
  CHECK(mw20 == doctest::Approx(solve(f2575).flow[0][0]).epsilon(1e-9));
  // 581.876 A (CEN static table: 587 A, within 1 %).
  CHECK(mw20
        == doctest::Approx(ieee738::ampacity_to_mw(
                               581.8759361999057, 220.0, 1.0, 1.0, 1, 1.0))
               .epsilon(1e-7));

  SUBCASE("validation of the r20 form")
  {
    auto both = flint(true);
    {
      auto c = both.conductor.value_or(DlrConductor {});
      c.r25 = 1e-4;
      both.conductor = c;
    }
    auto p1 = planning_with(both);
    CHECK(has_error(validate_planning(p1), "not both"));
    auto no_alpha = flint(true);
    {
      auto c = no_alpha.conductor.value_or(DlrConductor {});
      c.alpha_r.reset();
      no_alpha.conductor = c;
    }
    auto p2 = planning_with(no_alpha);
    CHECK(has_error(validate_planning(p2), "alpha_r is required"));
    auto bad_ac = flint(true);
    {
      auto c = bad_ac.conductor.value_or(DlrConductor {});
      c.ac_factor = 0.0;
      bad_ac.conductor = c;
    }
    auto p3 = planning_with(bad_ac);
    CHECK(has_error(validate_planning(p3), "ac_factor"));
    auto ok = flint(true);
    auto p4 = planning_with(ok);
    CHECK_FALSE(std::ranges::any_of(validate_planning(p4).errors,
                                    [](const auto& e)
                                    { return e.contains("dlr"); }));
  }
}

TEST_CASE("DLR option: low-wind validation")  // NOLINT
{
  LineDlr dlr {
      .low_wind_speed = -1.0,
      .low_wind_angle = 120.0,
  };
  dlr.critical_spans = {
      rated_span(1, "s", per_block({1.0, 2.0, 3.0})),
  };
  auto p = planning_with(dlr);
  const auto r = validate_planning(p);
  CHECK(has_error(r, "low_wind_speed"));
  CHECK(has_error(r, "low_wind_angle"));
}

TEST_CASE("DLR: precomputed rating is used verbatim")  // NOLINT
{
  // η, v_pu, power factor, subconductors and voltage scale only the
  // IEEE 738 path; a precomputed `rating` reaches the LP unchanged.
  for (const bool scaled : {false, true}) {
    CAPTURE(scaled);
    LineDlr dlr {};
    if (scaled) {
      auto cond = drake();
      cond.subconductors = 3;
      dlr.conductor = cond;
      dlr.voltage = 500.0;
      dlr.v_pu = 0.9;
      dlr.power_factor = 0.8;
      dlr.safety_factor = 0.5;
    }
    dlr.critical_spans = {
        rated_span(1, "s", per_block({100.0, 120.0, 140.0})),
    };
    Fixture fx({
        .dlr = dlr,
    });
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(100.0));
    CHECK(res.flow[0][1] == doctest::Approx(120.0));
    CHECK(res.flow[0][2] == doctest::Approx(140.0));
    CHECK(res.rating[0][1] == doctest::Approx(120.0));
  }
}

TEST_CASE("Overload slack bound modes: static line")  // NOLINT
{
  // tmax 100 (hard), tmax_normal 30, penalty 10: overloading is worth
  // 100 − 10 − 10 = 80 $/MWh, so the flow goes as far as the band allows.
  const auto flow = [](Case c)
  {
    c.tmax = 100.0;
    c.tmax_normal = 30.0;
    c.overload_penalty = 10.0;
    Fixture fx(c);
    return solve(fx).flow[0][0];
  };
  // Default thermal: min(100 − 30, 1.5 · 30) = 45 → 75.
  CHECK(flow({}) == doctest::Approx(75.0));
  CHECK(flow({.slack_bound = "thermal"}) == doctest::Approx(75.0));
  // Thermal with a large ratio: the thermal headroom 70 → 100.
  CHECK(flow({.slack_ratio = 5.0}) == doctest::Approx(100.0));
  // Ratio (global 1.0): 30 → 60; the per-line ratio 0.5 overrides: 45.
  CHECK(flow({.global_slack_bound = "ratio", .global_ratio = 1.0})
        == doctest::Approx(60.0));
  CHECK(flow({.slack_ratio = 0.5,
              .global_slack_bound = "ratio",
              .global_ratio = 1.0})
        == doctest::Approx(45.0));
  // Per-line mode overrides the global one.
  CHECK(flow({.slack_bound = "unbounded", .global_slack_bound = "ratio"})
        == doctest::Approx(100.0));
  // Unbounded: only the hard cap (100) limits the flow.
  CHECK(flow({.global_slack_bound = "unbounded"}) == doctest::Approx(100.0));
}

TEST_CASE("Overload slack bound modes: DLR emergency tier")  // NOLINT
{
  // Span a: normal 40, emergency 200.  Span b: normal 60, emergency 120.
  // Normal rating 40 (a), emergency rating 120 (b); penalty 10.
  const auto make = [](Case c)
  {
    LineDlr dlr {};
    dlr.critical_spans = {
        rated_span(1,
                   "a",
                   per_block({40.0, 40.0, 40.0}),
                   per_block({200.0, 200.0, 200.0})),
        rated_span(2,
                   "b",
                   per_block({60.0, 60.0, 60.0}),
                   per_block({120.0, 120.0, 120.0})),
    };
    c.dlr = dlr;
    c.overload_penalty = 10.0;
    return c;
  };

  SUBCASE("default thermal: min(E − N, 1.5·N) = 60 → flow 100")
  {
    Fixture fx(make({}));
    const auto r = solve(fx);
    for (std::size_t b = 0; b < 3; ++b) {
      CHECK(r.flow[0][b] == doctest::Approx(100.0));
      // The ratio bound binds: ∂obj/∂N = −80·2.5 − 10 = −210 on span a.
      CHECK(r.duals[0][b][0] == doctest::Approx(-210.0));
      CHECK(r.duals[0][b][1] == doctest::Approx(0.0));
    }
  }
  SUBCASE("ratio 1.0 → flow 80")
  {
    Fixture fx(make({.global_slack_bound = "ratio", .global_ratio = 1.0}));
    const auto r = solve(fx);
    CHECK(r.flow[0][0] == doctest::Approx(80.0));
    // ∂obj/∂N = −80·2 − 10 = −170.
    CHECK(r.duals[0][0][0] == doctest::Approx(-170.0));
  }

  // At the emergency rating the finite thermal bound (E − N = 80) ties
  // with the hard cap: the span duals must not depend on how the solver
  // splits that degenerate vertex, and must match the unbounded slack.
  for (const auto& [name, c] :
       {std::pair {"thermal at E", Case {.slack_ratio = 5.0}},
        std::pair {"unbounded", Case {.slack_bound = "unbounded"}}})
  {
    CAPTURE(name);
    for (const std::string mode : {"min", "rows"}) {
      CAPTURE(mode);
      auto cm = make(c);
      cm.dlr.span_mode = mode;
      Fixture fx(cm);
      const auto r = solve(fx);
      for (std::size_t b = 0; b < 3; ++b) {
        CHECK(r.flow[0][b] == doctest::Approx(120.0));
        // ∂obj/∂E_b = −80 (one more MW at 80 $/MWh net);
        // ∂obj/∂N_a = −10 (one less MWh priced by the band).
        CHECK(r.duals[0][b][1] == doctest::Approx(-80.0));
        CHECK(r.duals[0][b][0] == doctest::Approx(-10.0));
        CHECK(r.dual_sum[0][b] == doctest::Approx(-90.0));
      }
    }
  }

  SUBCASE("validation of the options")
  {
    LineDlr dlr {};
    dlr.critical_spans = {
        rated_span(1, "s", per_block({1.0, 2.0, 3.0})),
    };
    auto p = planning_with(dlr);
    p.options.model_options.overload_slack_bound = "elastic";
    p.options.model_options.max_overload_ratio = -1.0;
    REQUIRE_FALSE(p.system.line_array.empty());
    auto& ln = p.system.line_array.front();
    ln.overload_slack_bound = "bogus";
    ln.max_overload_ratio = -0.1;
    const auto res = validate_planning(p);
    CHECK(has_error(res, "unknown overload_slack_bound 'elastic'"));
    CHECK(has_error(res, "unknown overload_slack_bound 'bogus'"));
    CHECK(has_error(res, "model_options: max_overload_ratio"));
    CHECK(has_error(res, "Line 'l1': max_overload_ratio"));
  }
}

// ── DLR × line_thermal_limit_mode ─────────────────────────────────────
//
// The DLR rating T is a thermal limit like the static ``tmax``: it caps
// the same loaded quantity ``|f| + a·ℓ`` with ``a`` from
// ``line_thermal_limit_mode`` (flow → 0, sending → the loss share booked
// at the sending bus, full → 1).  The fixture is always congested (cheap
// generation upstream, dear downstream), so the rating binds:
// ``|f| + a·ℓ = T`` exactly, the span duals are negative and the bus
// LMPs are the two marginal costs.

namespace
{

/// Expected loss share ``a`` of the thermal limit (mirrors
/// ``line_losses::thermal_loss_share``).
[[nodiscard]] double expected_share(std::string_view loss,
                                    std::string_view limit,
                                    std::string_view allocation)
{
  if (limit == "full") {
    return 1.0;
  }
  if (limit == "sending") {
    if (loss == "tangent_signed_flow") {
      return 0.5;
    }
    if (allocation == "sender") {
      return 1.0;
    }
    if (allocation == "split") {
      return 0.5;
    }
  }
  return 0.0;
}

}  // namespace

TEST_CASE("DLR rating follows line_thermal_limit_mode")  // NOLINT
{
  struct Limit
  {
    std::string_view limit;
    std::string_view allocation;
  };
  const auto limits = std::to_array<Limit>({
      {
          .limit = "flow",
          .allocation = "split",
      },
      {
          .limit = "sending",
          .allocation = "split",
      },
      {
          .limit = "sending",
          .allocation = "sender",
      },
      {
          .limit = "sending",
          .allocation = "receiver",
      },
      {
          .limit = "full",
          .allocation = "split",
      },
  });
  const auto loss_modes = std::to_array<std::string_view>({
      "piecewise",
      "bidirectional",
      "linear",
      "tangent_signed_flow",
      "piecewise_direct",
  });
  const std::array ratings {90.0, 120.0, 300.0};

  for (const auto loss : loss_modes) {
    for (const auto& lm : limits) {
      for (const std::string span_mode : {"min", "rows"}) {
        for (const bool reverse : {false, true}) {
          for (const bool global : {false, true}) {
            CAPTURE(loss);
            CAPTURE(lm.limit);
            CAPTURE(lm.allocation);
            CAPTURE(span_mode);
            CAPTURE(reverse);
            CAPTURE(global);
            Fixture fx(Case {
                .dlr = three_span_dlr(span_mode),
                .loss_mode = loss,
                .limit = global ? std::string_view {} : lm.limit,
                .allocation = lm.allocation,
                .resistance = 20.0,
                .reverse = reverse,
                .global_limit = global ? lm.limit : std::string_view {},
            });
            const auto res = solve(fx);
            const double a = expected_share(loss, lm.limit, lm.allocation);
            for (std::size_t b = 0; b < ratings.size(); ++b) {
              CAPTURE(b);
              const double f = res.flow[0][b];
              const double l = res.loss[0][b];
              // Physical flow b1 → b2: A→B, or B→A on the reversed line.
              CHECK((reverse ? -f : f) > 0.0);
              // Lossy enough to tell the modes apart (the
              // ``tangent_signed_flow`` lower envelope is 0 well below
              // the 2000 MW chord anchor: only the 300 MW block).
              if (loss != "tangent_signed_flow" || b == 2) {
                CHECK(l > 1.0);
              }
              // The rating binds on |f| + a·ℓ.
              CHECK(std::abs(f) + (a * l)
                    == doctest::Approx(ratings[b]).epsilon(1e-7));
              CHECK(res.rating[0][b] == doctest::Approx(ratings[b]));
              // Dual / CMg sanity: ∂obj/∂T < 0, LMPs = marginal costs.
              CHECK(res.dual_sum[0][b] < -1.0);
              CHECK(res.lmp1[0][b] == doctest::Approx(kCheap));
              CHECK(res.lmp2[0][b] == doctest::Approx(kDear));
            }
          }
        }
      }
    }
  }
}

TEST_CASE(
    "DLR min mode adds no rows beyond the static thermal limit")  // NOLINT
{
  // flow: the DLR tightens the flow-column bound — no row at all.
  // sending / full: the DLR lowers the RHS of the existing thermal row
  // (one per block and direction) — no extra row.
  for (const std::string_view loss : {"piecewise", "bidirectional"}) {
    CAPTURE(loss);
    Fixture flow(Case {
        .dlr = three_span_dlr("min"),
        .loss_mode = loss,
        .limit = "flow",
        .resistance = 20.0,
    });
    CHECK(count_rows(flow.lp(), "thermal") == 0);
    Fixture full(Case {
        .dlr = three_span_dlr("min"),
        .loss_mode = loss,
        .limit = "full",
        .resistance = 20.0,
    });
    CHECK(count_rows(full.lp(), "thermalp") == 3);
    CHECK(count_rows(full.lp(), "thermaln") == 3);
    CHECK(count_rows(full.lp(), "span_thermal") == 0);
  }
}

TEST_CASE("DLR emergency tier follows line_thermal_limit_mode")  // NOLINT
{
  // Normal min 100 / 90 / 100, emergency min 140 / 150 / 140; a cheap
  // overload penalty pushes the loaded quantity to the emergency rating.
  const std::array emergency {140.0, 150.0, 140.0};
  for (const std::string_view limit : {"flow", "sending", "full"}) {
    for (const std::string span_mode : {"min", "rows"}) {
      for (const bool reverse : {false, true}) {
        CAPTURE(limit);
        CAPTURE(span_mode);
        CAPTURE(reverse);
        LineDlr dlr {
            .span_mode = span_mode,
        };
        dlr.critical_spans = {
            rated_span(1,
                       "a",
                       per_block({100.0, 100.0, 100.0}),
                       per_block({150.0, 150.0, 150.0})),
            rated_span(2,
                       "b",
                       per_block({120.0, 90.0, 120.0}),
                       per_block({140.0, 160.0, 140.0})),
        };
        Fixture fx(Case {
            .dlr = dlr,
            .loss_mode = "piecewise",
            .allocation = "split",
            .resistance = 20.0,
            .overload_penalty = 20.0,
            .reverse = reverse,
            .global_limit = limit,
        });
        const auto res = solve(fx);
        const double a = expected_share("piecewise", limit, "split");
        for (std::size_t b = 0; b < emergency.size(); ++b) {
          CAPTURE(b);
          const double f = std::abs(res.flow[0][b]);
          const double l = res.loss[0][b];
          CHECK(l > 1.0);
          CHECK(f + (a * l) == doctest::Approx(emergency[b]).epsilon(1e-7));
          CHECK(res.dual_sum[0][b] < -1.0);
          CHECK(res.lmp2[0][b] == doctest::Approx(kDear));
        }
      }
    }
  }
}

TEST_CASE("DLR full mode regression: B→A flow, |f| + loss = rating")  // NOLINT
{
  // Reproduces the CEN PCP report (piecewise losses, 3 segments, flow
  // B→A, ``model_options.line_thermal_limit_mode = full``, span_mode
  // min).  The binding quantity is |f| + ℓ (full), NOT |f| + ℓ/2.  The
  // B→A loss lives in ``lossn`` only: ``Line/loss_sol`` (= lossp +
  // lossn) already holds it, so adding ``Line/lossn_sol`` on top counts
  // the reverse-direction loss twice.
  Fixture fx(Case {
      .dlr = three_span_dlr("min"),
      .loss_mode = "piecewise",
      .segments = 3,
      .allocation = "split",
      .resistance = 20.0,
      .reverse = true,
      .global_limit = "full",
  });
  const auto res = solve(fx);
  const auto sol = fx.lp().get_col_sol();
  const auto& line = fx.line();
  const auto& scen = fx.sim_lp.scenarios().front();
  const auto& stage = fx.sim_lp.stages().front();
  const auto& lp_cols = line.lossp_cols_at(scen, stage);
  const auto& ln_cols = line.lossn_cols_at(scen, stage);
  const std::array ratings {90.0, 120.0, 300.0};
  for (std::size_t b = 0; b < ratings.size(); ++b) {
    CAPTURE(b);
    const auto buid = fx.sim_lp.stages().front().blocks()[b].uid();
    const double f = res.flow[0][b];
    const double l = res.loss[0][b];
    CHECK(f < 0.0);
    CHECK(std::abs(f) + l == doctest::Approx(ratings[b]).epsilon(1e-7));
    CHECK(std::abs(f) + (0.5 * l) < ratings[b] - (0.25 * l));
    const double lossp = lp_cols.contains(buid) ? sol[lp_cols.at(buid)] : 0.0;
    const double lossn = ln_cols.contains(buid) ? sol[ln_cols.at(buid)] : 0.0;
    CHECK(lossp == doctest::Approx(0.0));
    CHECK(lossn == doctest::Approx(l).epsilon(1e-7));
  }
}

// ── PLEXOS line limits, limit classification and DLR limit_mode ──────
//
// Every limit field is optional: unset (or a NaN cell) = no limit from
// that field.  The effective limit per direction and block is the min
// over the SET fields; one bound / row carries it and its dual is
// attributed to the binding part.  Ratings of ``three_span_dlr``:
// 90 / 120 / 300.  Without any limit the line carries the whole 1000 MW
// demand.

namespace
{

using Part = LineLP::LimitPart;
using TB = std::vector<std::vector<double>>;

[[nodiscard]] TB tb(std::vector<double> v)
{
  return TB {std::move(v)};
}

[[nodiscard]] int pi(Part p)
{
  return static_cast<int>(p);
}

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
constexpr double kSpread = kDear - kCheap;

/// Lossless, no-DLR fixture with only `fn` limits (no static tmax).
[[nodiscard]] Case limits_case(std::function<void(Line&)> fn,
                               bool reverse = false)
{
  return Case {
      .tmax = std::nullopt,
      .use_line_dlr = false,
      .reverse = reverse,
      .line_fn = std::move(fn),
  };
}

}  // namespace

TEST_CASE("Line limits: each field alone")  // NOLINT
{
  struct One
  {
    std::string_view name;
    std::function<void(Line&)> fn;
    bool reverse;
    double flow;  ///< |flow| in every block
    int part;  ///< reported part (−1: none)
  };
  const auto ones = std::to_array<One>({
      {
          .name = "max_flow",
          .fn = [](Line& l) { l.max_flow = 100.0; },
          .reverse = false,
          .flow = 100.0,
          .part = -1,
      },
      {
          .name = "min_flow (signed)",
          .fn = [](Line& l) { l.min_flow = -100.0; },
          .reverse = true,
          .flow = 100.0,
          .part = -1,
      },
      {
          .name = "max_rating",
          .fn = [](Line& l) { l.max_rating = 100.0; },
          .reverse = false,
          .flow = 100.0,
          .part = pi(Part::rating),
      },
      {
          .name = "min_rating (signed)",
          .fn = [](Line& l) { l.min_rating = -100.0; },
          .reverse = true,
          .flow = 100.0,
          .part = pi(Part::rating),
      },
      {
          .name = "outage_max_rating with units_out",
          .fn =
              [](Line& l)
          {
            l.outage_max_rating = 100.0;
            l.units_out = 1.0;
          },
          .reverse = false,
          .flow = 100.0,
          .part = pi(Part::outage),
      },
      {
          .name = "outage_max_rating without units out: no derate",
          .fn = [](Line& l) { l.outage_max_rating = 100.0; },
          .reverse = false,
          .flow = 1000.0,
          .part = -1,
      },
      {
          .name = "overload_max_rating without a penalty: unused",
          .fn = [](Line& l) { l.overload_max_rating = 100.0; },
          .reverse = false,
          .flow = 1000.0,
          .part = -1,
      },
      {
          .name = "conductor_rating_ab",
          .fn = [](Line& l) { l.conductor_rating_ab = 100.0; },
          .reverse = false,
          .flow = 100.0,
          .part = pi(Part::conductor),
      },
      {
          .name = "equipment_rating_ab",
          .fn = [](Line& l) { l.equipment_rating_ab = 100.0; },
          .reverse = false,
          .flow = 100.0,
          .part = pi(Part::equipment),
      },
      {
          .name = "security_limit_ab",
          .fn = [](Line& l) { l.security_limit_ab = 100.0; },
          .reverse = false,
          .flow = 100.0,
          .part = pi(Part::security),
      },
      {
          .name = "security_limit_ba",
          .fn = [](Line& l) { l.security_limit_ba = 100.0; },
          .reverse = true,
          .flow = 100.0,
          .part = pi(Part::security),
      },
      // One direction only: the other side is NOT mirrored.
      {
          .name = "max_rating does not limit B→A",
          .fn = [](Line& l) { l.max_rating = 100.0; },
          .reverse = true,
          .flow = 1000.0,
          .part = -1,
      },
      {
          .name = "security_limit_ab does not limit B→A",
          .fn = [](Line& l) { l.security_limit_ab = 100.0; },
          .reverse = true,
          .flow = 1000.0,
          .part = -1,
      },
  });
  for (const auto& o : ones) {
    CAPTURE(o.name);
    Fixture fx(limits_case(o.fn, o.reverse));
    const auto res = solve(fx);
    const auto& part = o.reverse ? res.part_n : res.part_p;
    const auto& lim = o.reverse ? res.lim_n : res.lim_p;
    for (std::size_t b = 0; b < 3; ++b) {
      CAPTURE(b);
      CHECK(std::abs(res.flow[0][b]) == doctest::Approx(o.flow));
      CHECK(part[0][b] == o.part);
      if (o.part >= 0) {
        CHECK(lim[0][b] == doctest::Approx(-kSpread));
      }
    }
  }
}

TEST_CASE("Line limits: every combination of set and unset")  // NOLINT
{
  // Distinct values so the binding part is unique.
  struct F
  {
    Part part;
    double value;
    std::function<void(Line&, double)> set;
  };
  const std::array<F, 6> fields {{
      {
          .part = Part::flowlimit,
          .value = 260.0,
          .set = [](Line& l, double v) { l.max_flow = v; },
      },
      {
          .part = Part::rating,
          .value = 240.0,
          .set = [](Line& l, double v) { l.max_rating = v; },
      },
      {
          .part = Part::outage,
          .value = 220.0,
          .set =
              [](Line& l, double v)
          {
            l.outage_max_rating = v;
            l.units_out = 2.0;
          },
      },
      {
          .part = Part::conductor,
          .value = 200.0,
          .set = [](Line& l, double v) { l.conductor_rating_ab = v; },
      },
      {
          .part = Part::equipment,
          .value = 180.0,
          .set = [](Line& l, double v) { l.equipment_rating_ab = v; },
      },
      {
          .part = Part::security,
          .value = 160.0,
          .set = [](Line& l, double v) { l.security_limit_ab = v; },
      },
  }};
  for (unsigned mask = 0; mask < (1U << fields.size()); ++mask) {
    CAPTURE(mask);
    double expected = 1000.0;  // demand: no limit set
    int part = -1;
    bool rating_line = false;
    for (const auto& [k, f] : enumerate(fields)) {
      if ((mask & (1U << k)) != 0U) {
        if (f.value < expected) {
          expected = f.value;
          part = pi(f.part);
        }
        rating_line = rating_line || f.part != Part::flowlimit;
      }
    }
    Fixture fx(limits_case(
        [&](Line& l)
        {
          for (const auto& [k, f] : enumerate(fields)) {
            if ((mask & (1U << k)) != 0U) {
              f.set(l, f.value);
            }
          }
        }));
    const auto res = solve(fx);
    for (std::size_t b = 0; b < 3; ++b) {
      CHECK(res.flow[0][b] == doctest::Approx(expected));
      CHECK(res.flow[0][b] > 1.0);  // an unset field never limits to 0
      CHECK(res.part_p[0][b] == (rating_line ? part : -1));
      if (rating_line && part >= 0) {
        CHECK(res.lim_p[0][b] == doctest::Approx(-kSpread));
      }
    }
  }
}

TEST_CASE("Line limits: NaN / missing cell vs explicit 0")  // NOLINT
{
  SUBCASE("max_rating {NaN, 0, 100}")
  {
    Fixture fx(
        limits_case([](Line& l) { l.max_rating = tb({kNaN, 0.0, 100.0}); }));
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(1000.0));  // NaN: no limit
    CHECK(res.flow[0][1] == doctest::Approx(0.0));  // explicit 0
    CHECK(res.flow[0][2] == doctest::Approx(100.0));
    CHECK(res.part_p[0][0] == -1);
    CHECK(res.part_p[0][1] == pi(Part::rating));
  }
  SUBCASE("tmax {NaN, 50, 0} and security_limit_ab {NaN, NaN, 70}")
  {
    Fixture fx(limits_case(
        [](Line& l)
        {
          l.tmax_ab = tb({kNaN, 50.0, 0.0});
          l.security_limit_ab = tb({kNaN, kNaN, 70.0});
        }));
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(1000.0));
    CHECK(res.flow[0][1] == doctest::Approx(50.0));
    CHECK(res.flow[0][2] == doctest::Approx(0.0));
    CHECK(res.part_p[0][1] == pi(Part::flowlimit));
    CHECK(res.part_p[0][2] == pi(Part::flowlimit));
  }
  SUBCASE("units_out {0, NaN, 1}: outage only where units are out")
  {
    Fixture fx(limits_case(
        [](Line& l)
        {
          l.max_rating = 300.0;
          l.outage_max_rating = 120.0;
          l.units_out = tb({0.0, kNaN, 1.0});
        }));
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(300.0));
    CHECK(res.flow[0][1] == doctest::Approx(300.0));
    CHECK(res.flow[0][2] == doctest::Approx(120.0));
    CHECK(res.part_p[0][2] == pi(Part::outage));
  }
}

TEST_CASE("Line limits: overload rating and limit_penalty")  // NOLINT
{
  const auto run = [](double penalty)
  {
    Fixture fx(Case {
        .tmax = std::nullopt,
        .use_line_dlr = false,
        .line_fn =
            [penalty](Line& l)
        {
          l.max_rating = 100.0;
          l.overload_max_rating = 150.0;
          if (penalty > 0.0) {
            l.limit_penalty = penalty;
          }
        },
    });
    return solve(fx);
  };
  SUBCASE("priced band: up to the overload rating")
  {
    const auto res = run(20.0);
    double expected = 0.0;
    for (std::size_t b = 0; b < 3; ++b) {
      CHECK(res.flow[0][b] == doctest::Approx(150.0));
      CHECK(res.part_p[0][b] == pi(Part::rating));
      expected += (kCheap * 150.0) + (kDear * 850.0) + (20.0 * 50.0);
    }
    CHECK(res.obj == doctest::Approx(expected));
  }
  SUBCASE("no penalty: the normal rating is the hard cap")
  {
    const auto res = run(0.0);
    CHECK(res.flow[0][0] == doctest::Approx(100.0));
  }
}

TEST_CASE("DLR limit_mode: defaults and targets")  // NOLINT
{
  for (const std::string mode : {"min", "rows"}) {
    CAPTURE(mode);
    SUBCASE("plain tmax: min (historical), replace has no target")
    {
      for (const std::string_view lim : {"", "replace"}) {
        CAPTURE(lim);
        Fixture fx({
            .dlr = three_span_dlr(mode),
            .tmax = 110.0,
            .dlr_limit = lim,
        });
        const auto res = solve(fx);
        CHECK(res.flow[0][0] == doctest::Approx(90.0));
        CHECK(res.flow[0][1] == doctest::Approx(110.0));
        CHECK(res.flow[0][2] == doctest::Approx(110.0));
      }
    }
    SUBCASE("conductor rating: replace by default")
    {
      Fixture fx({
          .dlr = three_span_dlr(mode),
          .tmax = std::nullopt,
          .line_fn =
              [](Line& l)
          {
            l.conductor_rating_ab = 110.0;
            l.conductor_rating_ba = 110.0;
          },
      });
      const auto res = solve(fx);
      CHECK(res.flow[0][0] == doctest::Approx(90.0));
      CHECK(res.flow[0][1] == doctest::Approx(120.0));
      CHECK(res.flow[0][2] == doctest::Approx(300.0));
      for (std::size_t b = 0; b < 3; ++b) {
        CHECK(res.part_p[0][b] == pi(Part::conductor));
        CHECK(res.lim_p[0][b] == doctest::Approx(-kSpread));
        CHECK(res.dual_sum[0][b] == doctest::Approx(-kSpread));
      }
    }
    SUBCASE("conductor rating with explicit min (per line or global)")
    {
      for (const bool global : {false, true}) {
        CAPTURE(global);
        Fixture fx(Case {
            .dlr = three_span_dlr(mode),
            .tmax = std::nullopt,
            .dlr_limit = global ? std::string_view {} : "min",
            .global_dlr_limit = global ? "min" : std::string_view {},
            .line_fn = [](Line& l) { l.conductor_rating_ab = 110.0; },
        });
        const auto res = solve(fx);
        CHECK(res.flow[0][1] == doctest::Approx(110.0));
        CHECK(res.flow[0][2] == doctest::Approx(110.0));
        // Static conductor binds: conductor part, not a span dual.
        CHECK(res.part_p[0][2] == pi(Part::conductor));
        CHECK(res.lim_p[0][2] == doctest::Approx(-kSpread));
        CHECK(res.dual_sum[0][2] == doctest::Approx(0.0));
      }
    }
    SUBCASE("per-line replace overrides a global min")
    {
      Fixture fx({
          .dlr = three_span_dlr(mode),
          .tmax = std::nullopt,
          .dlr_limit = "replace",
          .global_dlr_limit = "min",
          .line_fn = [](Line& l) { l.conductor_rating_ab = 110.0; },
      });
      CHECK(solve(fx).flow[0][2] == doctest::Approx(300.0));
    }
    SUBCASE("no conductor: explicit replace acts on max_rating")
    {
      for (const std::string_view lim : {"", "replace"}) {
        CAPTURE(lim);
        Fixture fx({
            .dlr = three_span_dlr(mode),
            .tmax = std::nullopt,
            .dlr_limit = lim,
            .line_fn = [](Line& l) { l.max_rating = 110.0; },
        });
        const auto res = solve(fx);
        CHECK(res.flow[0][2] == doctest::Approx(lim.empty() ? 110.0 : 300.0));
        CHECK(res.part_p[0][2]
              == pi(lim.empty() ? Part::rating : Part::conductor));
      }
    }
    SUBCASE("max_flow is never replaced")
    {
      Fixture fx({
          .dlr = three_span_dlr(mode),
          .tmax = std::nullopt,
          .line_fn =
              [](Line& l)
          {
            l.conductor_rating_ab = 110.0;
            l.max_flow = 250.0;
          },
      });
      const auto res = solve(fx);
      CHECK(res.flow[0][2] == doctest::Approx(250.0));
      CHECK(res.part_p[0][2] == pi(Part::flowlimit));
    }
  }
}

TEST_CASE("DLR replace with a binding security limit")  // NOLINT
{
  // Conductor 110 replaced; security 1000 / 100 / 250: limit 90 / 100 /
  // 250 — conductor (DLR) in block 1, security in blocks 2-3.
  for (const std::string mode : {"min", "rows"}) {
    CAPTURE(mode);
    Fixture fx({
        .dlr = three_span_dlr(mode),
        .tmax = std::nullopt,
        .line_fn =
            [](Line& l)
        {
          l.conductor_rating_ab = 110.0;
          l.security_limit_ab = tb({1000.0, 100.0, 250.0});
        },
    });
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(90.0));
    CHECK(res.flow[0][1] == doctest::Approx(100.0));
    CHECK(res.flow[0][2] == doctest::Approx(250.0));
    CHECK(res.part_p[0][0] == pi(Part::conductor));
    CHECK(res.part_p[0][1] == pi(Part::security));
    CHECK(res.part_p[0][2] == pi(Part::security));
    for (std::size_t b = 0; b < 3; ++b) {
      CHECK(res.lim_p[0][b] == doctest::Approx(-kSpread));
    }
    // Separate duals: the span duals carry only the DLR block.
    CHECK(res.dual_sum[0][0] == doctest::Approx(-kSpread));
    CHECK(res.dual_sum[0][1] == doctest::Approx(0.0));
    CHECK(res.critical[0][1] == doctest::Approx(-1.0));
    // flow mode: no row for any part (``rows`` adds only span rows).
    if (mode == "min") {
      CHECK(count_rows(fx.lp(), "thermal") == 0);
    }
    CHECK(count_rows(fx.lp(), "security") == 0);
  }
}

TEST_CASE("DLR replace: outage rating is the maintenance derate")  // NOLINT
{
  for (const std::string mode : {"min", "rows"}) {
    CAPTURE(mode);
    Fixture fx({
        .dlr = three_span_dlr(mode),
        .tmax = std::nullopt,
        .line_fn =
            [](Line& l)
        {
          l.conductor_rating_ab = 110.0;
          l.outage_max_rating = 100.0;
          l.units_out = tb({0.0, 1.0, 0.0});
        },
    });
    const auto res = solve(fx);
    CHECK(res.flow[0][0] == doctest::Approx(90.0));
    CHECK(res.flow[0][1] == doctest::Approx(100.0));  // derate
    CHECK(res.flow[0][2] == doctest::Approx(300.0));  // DLR
    CHECK(res.part_p[0][1] == pi(Part::outage));
    CHECK(res.critical[0][1] == doctest::Approx(-1.0));
  }
}

TEST_CASE("DLR replace + security with each thermal-limit mode")  // NOLINT
{
  // Limit 90 (DLR) / 100 (security) / 250 (security) on |f| + a·ℓ.
  const std::array limit_b {90.0, 100.0, 250.0};
  for (const std::string_view loss :
       {"piecewise", "tangent_signed_flow", "linear", "bidirectional"})
  {
    for (const std::string_view limit : {"flow", "sending", "full"}) {
      for (const std::string mode : {"min", "rows"}) {
        for (const bool reverse : {false, true}) {
          CAPTURE(loss);
          CAPTURE(limit);
          CAPTURE(mode);
          CAPTURE(reverse);
          Fixture fx(Case {
              .dlr = three_span_dlr(mode),
              .loss_mode = loss,
              .allocation = "split",
              .resistance = 20.0,
              .tmax = std::nullopt,
              .reverse = reverse,
              .global_limit = limit,
              .line_fn =
                  [](Line& l)
              {
                for (auto* f :
                     {&l.conductor_rating_ab, &l.conductor_rating_ba}) {
                  *f = 110.0;
                }
                l.security_limit_ab = tb({1000.0, 100.0, 250.0});
                l.security_limit_ba = tb({1000.0, 100.0, 250.0});
              },
          });
          const auto res = solve(fx);
          const double a = expected_share(loss, limit, "split");
          const auto& part = reverse ? res.part_n : res.part_p;
          const auto& lim = reverse ? res.lim_n : res.lim_p;
          for (std::size_t b = 0; b < limit_b.size(); ++b) {
            CAPTURE(b);
            const double l = res.loss[0][b];
            // ``tangent_signed_flow``: the loss lower envelope is 0 far
            // below the chord anchor (the 1000 MW security envelope).
            if (loss != "tangent_signed_flow") {
              CHECK(l > 0.5);
            }
            CHECK(std::abs(res.flow[0][b]) + (a * l)
                  == doctest::Approx(limit_b[b]).epsilon(1e-7));
            CHECK(res.lmp1[0][b] == doctest::Approx(kCheap));
            CHECK(res.lmp2[0][b] == doctest::Approx(kDear));
            CHECK(part[0][b] == pi(b == 0 ? Part::conductor : Part::security));
            CHECK(lim[0][b] < -1.0);
            if (b > 0) {
              CHECK(res.dual_sum[0][b] == doctest::Approx(0.0));
            }
          }
        }
      }
    }
  }
}

TEST_CASE("Line limits: full mode keeps one row per direction")  // NOLINT
{
  Fixture fx({
      .dlr = three_span_dlr("min"),
      .loss_mode = "piecewise",
      .limit = "full",
      .resistance = 20.0,
      .tmax = std::nullopt,
      .line_fn =
          [](Line& l)
      {
        l.conductor_rating_ab = 110.0;
        l.equipment_rating_ab = 95.0;
        l.security_limit_ab = tb({1000.0, 100.0, 250.0});
      },
  });
  // One thermal row per block and direction (A→B: equipment/security
  // RHS; B→A: the DLR envelope) and no part rows.
  CHECK(count_rows(fx.lp(), "thermalp") == 3);
  CHECK(count_rows(fx.lp(), "equipment") == 0);
  CHECK(count_rows(fx.lp(), "security") == 0);
  const auto res = solve(fx);
  CHECK(res.part_p[0][0] == pi(Part::conductor));  // DLR 90 < 95
  CHECK(res.part_p[0][1] == pi(Part::equipment));  // 95
  CHECK(res.part_p[0][2] == pi(Part::equipment));
  CHECK(res.lim_p[0][1] < -1.0);
}

TEST_CASE("DLR replace: emergency tier above the static conductor")  // NOLINT
{
  // Normal min 100 / 90 / 100, emergency min 140 / 150 / 140, static
  // conductor 120 and tmax_normal 50 (both replaced).
  for (const std::string mode : {"min", "rows"}) {
    for (const std::string_view lim : {"replace", "min"}) {
      CAPTURE(mode);
      CAPTURE(lim);
      LineDlr dlr {
          .span_mode = mode,
      };
      dlr.critical_spans = {
          rated_span(1,
                     "a",
                     per_block({100.0, 100.0, 100.0}),
                     per_block({150.0, 150.0, 150.0})),
          rated_span(2,
                     "b",
                     per_block({120.0, 90.0, 120.0}),
                     per_block({140.0, 160.0, 140.0})),
      };
      Fixture fx(Case {
          .dlr = dlr,
          .tmax = std::nullopt,
          .overload_penalty = 20.0,
          .tmax_normal = 50.0,
          .dlr_limit = lim,
          .line_fn = [](Line& l) { l.conductor_rating_ab = 120.0; },
      });
      const auto res = solve(fx);
      if (lim == "replace") {
        CHECK(res.flow[0][0] == doctest::Approx(140.0));
        CHECK(res.flow[0][1] == doctest::Approx(150.0));
        double expected = 0.0;
        const std::array fl {140.0, 150.0, 140.0};
        const std::array nm {100.0, 90.0, 100.0};
        for (std::size_t b = 0; b < 3; ++b) {
          expected += (kCheap * fl[b]) + (kDear * (1000.0 - fl[b]))
              + (20.0 * (fl[b] - nm[b]));
        }
        CHECK(res.obj == doctest::Approx(expected));
      } else {
        CHECK(res.flow[0][0] == doctest::Approx(120.0));
        CHECK(res.flow[0][1] == doctest::Approx(120.0));
      }
    }
  }
}

TEST_CASE("Line limits: validation and JSON")  // NOLINT
{
  SUBCASE("aliases and signs")
  {
    auto p = planning_with(three_span_dlr("min"));
    auto& ln = p.system.line_array.front();
    ln.max_flow = 100.0;  // tmax_ab is also set (2000)
    ln.min_flow = 50.0;  // positive: wrong sign, and tmax_ba is set
    ln.limit_penalty = 10.0;
    ln.overload_penalty = 10.0;
    ln.max_rating = tb({kNaN, -1.0});
    ln.min_rating = tb({kNaN, -1.0});  // NaN ok, -1 ok
    const auto r = validate_planning(p);
    CHECK(has_error(r, "tmax_ab and max_flow are aliases"));
    CHECK(has_error(r, "tmax_ba and min_flow"));
    CHECK(has_error(r, "overload_penalty and limit_penalty"));
    CHECK(has_error(r, "min_flow"));
    CHECK(has_error(r, "max_rating"));
    CHECK_FALSE(has_error(r, "min_rating"));
  }
  SUBCASE("unknown limit modes")
  {
    auto dlr = three_span_dlr("min");
    dlr.limit_mode = "maximum";
    auto p = planning_with(dlr);
    p.options.model_options.dlr_limit_mode = "bogus";
    const auto r = validate_planning(p);
    CHECK(has_error(r, "unknown limit_mode 'maximum'"));
    CHECK(has_error(r, "unknown dlr_limit_mode 'bogus'"));
  }
  SUBCASE("option default")
  {
    CHECK_FALSE(
        PlanningOptionsLP(PlanningOptions {}).dlr_limit_mode().has_value());
    PlanningOptions po;
    po.model_options.dlr_limit_mode = "replace";
    CHECK(PlanningOptionsLP(po).dlr_limit_mode().value_or(LineDlrLimitMode::min)
          == LineDlrLimitMode::replace);
  }
  SUBCASE("JSON round trip")
  {
    constexpr std::string_view json = R"({
      "uid": 1, "name": "l1", "bus_a": 1, "bus_b": 2,
      "max_flow": 1500, "min_flow": -1500,
      "max_rating": [[490, 480]], "min_rating": -490,
      "overload_max_rating": 600, "overload_min_rating": -600,
      "outage_max_rating": 300, "outage_min_rating": -300,
      "units_out": [[0, 1]], "limit_penalty": 50,
      "conductor_rating_ab": "cond_ab", "conductor_rating_ba": 700,
      "equipment_rating_ab": 650, "equipment_rating_ba": 650,
      "security_limit_ab": 520, "security_limit_ba": 510,
      "dlr": {"limit_mode": "min",
              "critical_spans": [{"uid": 7, "rating": 100}]}
    })";
    const auto line = daw::json::from_json<Line>(json);
    CHECK(line.has_rating_limits());
    CHECK(line.max_flow.has_value());
    CHECK(line.min_flow.has_value());
    CHECK(line.units_out.has_value());
    CHECK(line.limit_penalty.has_value());
    REQUIRE(line.conductor_rating_ab.has_value());
    CHECK(std::holds_alternative<FileSched>(
        line.conductor_rating_ab.value_or(OptTBRealFieldSched::value_type {})));
    CHECK(line.dlr.value_or(LineDlr {}).limit_mode.value_or("") == "min");
    const auto out = daw::json::to_json(line);
    const auto back = daw::json::from_json<Line>(out);
    CHECK(daw::json::to_json(back) == out);
    CHECK(back.security_limit_ba.has_value());
    CHECK(back.outage_min_rating.has_value());
    // Plain lines have no rating limits.
    CHECK_FALSE(Line {}.has_rating_limits());
  }
  SUBCASE("PAMPL parameter accessors")
  {
    Fixture fx(limits_case(
        [](Line& l)
        {
          l.min_flow = -400.0;
          l.max_rating = 300.0;
          l.min_rating = -250.0;
          l.security_limit_ba = 200.0;
        }));
    const auto& line = fx.line();
    const auto s = make_uid<Stage>(1);
    const auto b = make_uid<Block>(1);
    CHECK(line.param_tmax_ba(s, b).value_or(-1.0) == doctest::Approx(400.0));
    CHECK(line.param_min_flow(s, b).value_or(0.0) == doctest::Approx(-400.0));
    CHECK_FALSE(line.param_tmax_ab(s, b).has_value());
    CHECK(line.param_max_rating(s, b).value_or(-1.0) == doctest::Approx(300.0));
    CHECK(line.param_min_rating(s, b).value_or(0.0) == doctest::Approx(-250.0));
    CHECK(line.param_security_limit_ba(s, b).value_or(-1.0)
          == doctest::Approx(200.0));
    CHECK_FALSE(line.param_conductor_rating_ab(s, b).has_value());
  }
}

}  // namespace test_line_dlr_ns
