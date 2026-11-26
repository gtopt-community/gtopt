// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_cyclic.cpp
 * @brief     Cyclic unit commitment (`cycle = "stage"`) and the explicit
 *            commitment grid (`commitment_grid = block | overlay`)
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Common unit: `g1` 10 $/MWh, no-load 100 $/h, start 1500 $, 100 MW;
 * backup `g2` 100 $/MWh.  Serving a 50 MW load with g1 always beats the
 * backup, so the objectives below are hand computed from the on-hours
 * and the number of starts.
 */

#include <algorithm>
#include <cmath>
#include <format>
#include <numbers>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace cyclic_uc_test
{
namespace
{

struct Case
{
  std::vector<double> dur {};  ///< block durations (nominal)
  std::vector<double> load {};  ///< MW per block
  std::string uc_extra {};  ///< extra commitment fields (leading comma)
  std::string stage_extra {};  ///< extra stage fields (leading comma)
  std::vector<double> time {};  ///< node times (point stages)
  bool relax = false;
};

[[nodiscard]] std::string list(const std::vector<double>& v)
{
  std::string out;
  for (std::size_t k = 0; k < v.size(); ++k) {
    out += std::format("{}{:.17g}", k == 0 ? "" : ", ", v[k]);
  }
  return out;
}

[[nodiscard]] std::string make_json(const Case& c)
{
  std::string blocks;
  for (std::size_t k = 0; k < c.dur.size(); ++k) {
    blocks += std::format(R"({}{{"uid": {}, "duration": {:.17g}{}}})",
                          k == 0 ? "" : ", ",
                          k + 1,
                          c.dur[k],
                          c.time.empty()
                              ? std::string {}
                              : std::format(R"(, "time": {:.17g})", c.time[k]));
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 100000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "cyclic_uc",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "noload_cost": 100, "startup_cost": 1500,
                           "relax": {} {}}}]}}
}})",
      blocks,
      c.dur.size(),
      c.stage_extra,
      list(c.load),
      c.relax ? "true" : "false",
      c.uc_extra);
}

struct Solved
{
  double obj;
  Index ncols;
  Index nrows;
};

[[nodiscard]] Solved solve(const Case& c)
{
  Planning base;
  base.merge(parse_planning_json(make_json(c)));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  const auto& li = plp.systems().front().front().linear_interface();
  return {
      .obj = li.get_obj_value(),
      .ncols = li.get_numcols(),
      .nrows = li.get_numrows(),
  };
}

/// 24 hourly blocks, 50 MW in the listed hours.
[[nodiscard]] std::vector<double> day_load(std::initializer_list<int> hours)
{
  std::vector<double> l(24, 0.0);
  for (const int h : hours) {
    l[static_cast<std::size_t>(h)] = 50.0;
  }
  return l;
}

constexpr std::string_view kCyclic = R"(, "cycle": "stage")";

}  // namespace
}  // namespace cyclic_uc_test

TEST_CASE(  // NOLINT
    "Cyclic UC - a peaker starts exactly once per cycle")
{
  using namespace cyclic_uc_test;
  // load 50 MW at 10–13 h: energy 200 MWh · 10 = 2000
  //   one start: 2000 + 4·100 + 1500 = 3900  (< always on: 2000 + 2400)
  const auto load = day_load({10, 11, 12, 13});
  const Case cyc {.dur = std::vector<double>(24, 1.0),
                  .load = load,
                  .uc_extra = std::string(kCyclic)};
  CHECK(solve(cyc).obj == doctest::Approx(3900.0));
  // relaxed: u = v = w = ½ suffice (p ≤ 100·u, no pmin):
  //   2000 + 4·50 + ½·1500 = 2950
  Case rel = cyc;
  rel.relax = true;
  CHECK(solve(rel).obj == doctest::Approx(2950.0));
  // non-cyclic, initially on: stays on 0–13 h without a start
  //   2000 + 14·100 = 3400
  const Case on0 {.dur = std::vector<double>(24, 1.0),
                  .load = load,
                  .uc_extra = R"(, "initial_status": 1)"};
  CHECK(solve(on0).obj == doctest::Approx(3400.0));
  // cyclic ignores initial_status (no free initial state)
  Case cyc_on0 = on0;
  cyc_on0.uc_extra += kCyclic;
  CHECK(solve(cyc_on0).obj == doctest::Approx(3900.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - a run across the cycle boundary is one start")
{
  using namespace cyclic_uc_test;
  // load at 22, 23, 0, 1 h: cyclic run 22→1 = one start:
  //   2000 + 400 + 1500 = 3900
  // chronological from off: two runs (0–1, 22–23) = two starts:
  //   2000 + 400 + 3000 = 5400  (bridging 2–21 costs 2000 > 1500)
  const auto load = day_load({22, 23, 0, 1});
  const Case cyc {.dur = std::vector<double>(24, 1.0),
                  .load = load,
                  .uc_extra = std::string(kCyclic)};
  CHECK(solve(cyc).obj == doctest::Approx(3900.0));
  const Case chrono {.dur = std::vector<double>(24, 1.0),
                     .load = load,
                     .uc_extra = R"(, "initial_status": 0)"};
  CHECK(solve(chrono).obj == doctest::Approx(5400.0));
  // load all day: on through the whole cycle, 0 starts
  const Case all {.dur = std::vector<double>(24, 1.0),
                  .load = std::vector<double>(24, 50.0),
                  .uc_extra = std::string(kCyclic)};
  CHECK(solve(all).obj == doctest::Approx((1200.0 * 10.0) + 2400.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - min up time wraps around the cycle")
{
  using namespace cyclic_uc_test;
  // on 22:00–03:00 (load at 22, 23, 0, 1, 2): 5 h = MUT 5 satisfied:
  //   250·10 + 5·100 + 1500 = 4500
  // MUT 6: one more hour on: 4600 (a window that did not wrap would
  // truncate at 24 h and miss it)
  const auto load = day_load({22, 23, 0, 1, 2});
  Case c {.dur = std::vector<double>(24, 1.0),
          .load = load,
          .uc_extra = std::string(kCyclic) + R"(, "min_up_time": 5)"};
  CHECK(solve(c).obj == doctest::Approx(4500.0));
  c.uc_extra = std::string(kCyclic) + R"(, "min_up_time": 6)";
  CHECK(solve(c).obj == doctest::Approx(4600.0));
  // min down time wraps too: MDT 20 h > the 19 h off gap ⇒ stay on
  // all day (2400 noload, no start): 2500 + 2400 = 4900 < shutdown
  // impossible
  c.uc_extra = std::string(kCyclic) + R"(, "min_down_time": 20)";
  CHECK(solve(c).obj == doctest::Approx(4900.0));
  c.uc_extra = std::string(kCyclic) + R"(, "min_down_time": 19)";
  CHECK(solve(c).obj == doctest::Approx(4500.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - start cost scaled by the stage weight")
{
  using namespace cyclic_uc_test;
  // typical day standing for 30 days: durations 30 h per block,
  // period_hours 24 ⇒ every cost ×30: 30 · 3900
  const Case c {.dur = std::vector<double>(24, 30.0),
                .load = day_load({10, 11, 12, 13}),
                .uc_extra = std::string(kCyclic),
                .stage_extra = R"(, "period_hours": 24)"};
  CHECK(solve(c).obj == doctest::Approx(30.0 * 3900.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - periodic point stage on N nodes, no phantom start")
{
  using namespace cyclic_uc_test;
  // Lobatto N = 8 on a 24 h day as a periodic grid: node N (≡ node 0 of
  // the next cycle) is dropped and its weight added to node 0 — 8
  // nodes, one status each, the wrap closes on node 0.  Load at all
  // nodes ⇒ always on, no start: 50·24·10 + 24·100
  constexpr int n = 8;
  std::vector<double> t;
  std::vector<double> w;
  for (int k = 0; k <= n; ++k) {
    const double theta = std::numbers::pi * k / n;
    t.push_back(12.0 * (1.0 - std::cos(theta)));
    double s = 0.0;
    for (int j = 1; j <= n / 2; ++j) {
      const double b = (2 * j == n) ? 1.0 : 2.0;
      s += b / ((4.0 * j * j) - 1.0) * std::cos(2.0 * j * theta);
    }
    const double c = (k == 0 || k == n) ? 1.0 : 2.0;
    w.push_back(c / n * (1.0 - s) * 12.0);
  }
  w.front() += w.back();
  w.pop_back();
  t.pop_back();
  Case cyc {.dur = w,
            .load = std::vector<double>(t.size(), 50.0),
            .uc_extra = std::string(kCyclic),
            .stage_extra = R"(, "sampling": "point", "period_hours": 24)",
            .time = t};
  const auto a = solve(cyc);
  CHECK(a.obj == doctest::Approx((50.0 * 24.0 * 10.0) + 2400.0));
  Case chrono = cyc;
  chrono.uc_extra = R"(, "initial_status": 1)";
  const auto b = solve(chrono);
  CHECK(b.obj == doctest::Approx(a.obj));
  // the same status columns (one per node) in both
  CHECK(b.ncols == a.ncols);
}

TEST_CASE(  // NOLINT
    "Commitment grid - block vs overlay on variable blocks")
{
  using namespace cyclic_uc_test;
  // blocks 0.5 / 1 / 2 / 3.5 / 1 / 20 h, load 50 MW in block 1 (1 h)
  // only (always on would cost 28·100 + 500 = 3300).
  //   block grid: on in block 1 only: 50·10 + 100·1 + 1500 = 2100
  //   overlay P = 2 h (midpoints 0.25, 1, 2.5, 5.25, 7.5, 18 ⇒ periods
  //   {0, 1}, {2}, {3}, {4}, {5}): the status of block 1 is shared with
  //   block 0: 500 + 100·1.5 + 1500 = 2150
  const std::vector<double> dur {0.5, 1.0, 2.0, 3.5, 1.0, 20.0};
  const std::vector<double> load {0.0, 50.0, 0.0, 0.0, 0.0, 0.0};
  Case blk {.dur = dur,
            .load = load,
            .uc_extra = R"(, "initial_status": 0, "commitment_grid": "block")"};
  CHECK(solve(blk).obj == doctest::Approx(2100.0));
  Case ovl = blk;
  ovl.uc_extra =
      R"(, "initial_status": 0, "commitment_grid": "overlay", "commitment_period": 2)";
  CHECK(solve(ovl).obj == doctest::Approx(2150.0));
  // default grid: overlay iff commitment_period is set
  Case dflt = ovl;
  dflt.uc_extra = R"(, "initial_status": 0, "commitment_period": 2)";
  CHECK(solve(dflt).obj == doctest::Approx(2150.0));
  // MUT 3 h counts real durations: block grid from block 1 needs
  // 1 + 2 ≥ 3 ⇒ blocks 1, 2 on: 500 + 300 + 1500 = 2300
  blk.uc_extra += R"(, "min_up_time": 3)";
  CHECK(solve(blk).obj == doctest::Approx(2300.0));
  // overlay: periods {0,1} (1.5 h) + {2} (2 h) ⇒ 500 + 350 + 1500
  ovl.uc_extra += R"(, "min_up_time": 3)";
  CHECK(solve(ovl).obj == doctest::Approx(2350.0));
  // cyclic + overlay: blocks 0.5 / 1 / 20 / 1 h (midpoints 0.25, 1,
  // 11.5, 21.5 ⇒ periods {0, 1}, {2}, {3}); load in the last block (1 h)
  // and the first (0.5 h) is one wrapped run over periods {3}, {0, 1}:
  //   75·10 + 100·(1 + 1.5) + 1500 = 2500   (always on: 2250 + 750)
  Case wrap {.dur = {0.5, 1.0, 20.0, 1.0},
             .load = {50.0, 0.0, 0.0, 50.0},
             .uc_extra = std::string(kCyclic) + R"(, "commitment_period": 2)"};
  CHECK(solve(wrap).obj == doctest::Approx(2500.0));
}

TEST_CASE(  // NOLINT
    "Commitment grid - validation of grid and cycle options")
{
  using namespace cyclic_uc_test;
  const auto msgs = [](std::string_view extra)
  {
    Case c {
        .dur = {1.0, 1.0}, .load = {0.0, 0.0}, .uc_extra = std::string(extra)};
    Planning p;
    p.merge(parse_planning_json(make_json(c)));
    const auto r = validate_planning(p);
    std::string all;
    for (const auto& e : r.errors) {
      all += "E:" + e + "\n";
    }
    for (const auto& w : r.warnings) {
      all += "W:" + w + "\n";
    }
    return all;
  };
  CHECK(msgs(R"(, "commitment_grid": "nodes")").find("must be 'block'")
        != std::string::npos);
  CHECK(msgs(R"(, "commitment_grid": "block", "commitment_period": 2)")
            .find("conflicts")
        != std::string::npos);
  CHECK(msgs(R"(, "commitment_grid": "overlay")").find("needs")
        != std::string::npos);
  CHECK(msgs(R"(, "cycle": "week")").find("'none' or 'stage'")
        != std::string::npos);
  CHECK(msgs(R"(, "cycle": "stage", "initial_status": 1)").find("W:")
        != std::string::npos);
  CHECK(msgs(R"(, "cycle": "stage")").find("Commitment") == std::string::npos);
}

TEST_CASE(  // NOLINT
    "Cyclic UC - start tiers count the off time across the boundary")
{
  using namespace cyclic_uc_test;
  // on 0–17 h, off 18–23 h: the wrapped off time before the start at
  // 0 h is 6 h ⇒ warm (hot < 4 h ≤ warm < 10 h ≤ cold).  Options per
  // cycle: warm start 500 < staying on 2 h more for a hot start
  // (200 + 450) < bridging 6·100 ⇒ 18·50·10 + 18·100 + 500 = 11300
  //   (cold, or a start without the wrapped off time, stays on: 11400)
  std::vector<double> load(24, 0.0);
  std::fill_n(load.begin(), 18, 50.0);
  constexpr std::string_view tiers =
      R"(, "cycle": "stage", "hot_start_cost": 450, "warm_start_cost": 500,
         "cold_start_cost": 3000, "hot_start_time": 4,
         "cold_start_time": 10)";
  const Case c {.dur = std::vector<double>(24, 1.0),
                .load = load,
                .uc_extra = std::string(tiers)};
  CHECK(solve(c).obj == doctest::Approx(11300.0));
  // the same typical day standing for 30 days: tiers ×30 like every
  // other cost (the off time is measured in typical-period hours)
  Case w = c;
  w.dur = std::vector<double>(24, 30.0);
  w.stage_extra = R"(, "period_hours": 24)";
  CHECK(solve(w).obj == doctest::Approx(30.0 * 11300.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - ramps wrap from the last block to the first")
{
  using namespace cyclic_uc_test;
  // load 80 MW 0–11 h, 20 MW 12–23 h; ramp 30 MW/h; always on (no
  // start; startup / shutdown ramps 0 so that a fractional v = w
  // cannot buy ramp room).  Each 60 MW step needs one block at 50 MW with the
  // backup covering 30 MWh at +90 $/MWh: hour 11 (down) and — only when the
  // ramp wraps 23 h → 0 h — hour 0 (up).
  //   energy 1200·10 + noload 2400 + 2700 per limited step
  std::vector<double> load(24, 20.0);
  std::fill_n(load.begin(), 12, 80.0);
  constexpr std::string_view ramps =
      R"(, "ramp_up": 30, "ramp_down": 30, "startup_ramp": 0,
         "shutdown_ramp": 0)";
  const Case cyc {.dur = std::vector<double>(24, 1.0),
                  .load = load,
                  .uc_extra = std::string(kCyclic) + std::string(ramps)};
  CHECK(solve(cyc).obj == doctest::Approx(12000.0 + 2400.0 + 5400.0));
  // chronological, initially on at 80 MW: only the 11 → 12 h step is
  // limited
  const Case chrono {.dur = std::vector<double>(24, 1.0),
                     .load = load,
                     .uc_extra = R"(, "initial_status": 1, "initial_power": 80)"
                         + std::string(ramps)};
  CHECK(solve(chrono).obj == doctest::Approx(12000.0 + 2400.0 + 2700.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - max_starts counts the wrapped start")
{
  using namespace cyclic_uc_test;
  // 48 h typical period, 50 MW at 0 h and 24 h (gaps of 23 h).
  //   free: two starts 1000 + 200 + 3000 = 4200
  //   max_starts 1: one start, bridge 23 h: 1000 + 2500 + 1500 = 5000
  std::vector<double> load(48, 0.0);
  load[0] = 50.0;
  load[24] = 50.0;
  const Case free_ {.dur = std::vector<double>(48, 1.0),
                    .load = load,
                    .uc_extra = std::string(kCyclic)};
  CHECK(solve(free_).obj == doctest::Approx(4200.0));
  Case capped = free_;
  capped.uc_extra += R"(, "max_starts": 1, "starts_scope": "horizon")";
  CHECK(solve(capped).obj == doctest::Approx(5000.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - a starts window longer than the cycle needs a declaration")
{
  using namespace cyclic_uc_test;
  Case c {.dur = std::vector<double>(24, 1.0),
          .load = day_load({10}),
          .uc_extra = std::string(kCyclic)
              + R"(, "max_starts": 1, "starts_scope": 48)"};
  Planning p;
  p.merge(parse_planning_json(make_json(c)));
  const auto r = validate_planning(p);
  CHECK(std::ranges::any_of(
      r.errors,
      [](const auto& e)
      { return e.contains("starts_scope = 48 h > typical period 24 h"); }));
  // declared "on": no starts row, committed all day
  //   50·10 + 24·100 = 2900
  c.uc_extra += R"(, "long_transition": "on")";
  CHECK(solve(c).obj == doctest::Approx(2900.0));
}

TEST_CASE(  // NOLINT
    "Cyclic UC - point stage standing for 30 days, no phantom start")
{
  using namespace cyclic_uc_test;
  // Lobatto N = 8 day weighted ×30 (Σw = 720 h, period_hours 24) as a
  // periodic grid: node N (≡ node 0 of the next cycle) is dropped and
  // its weight added to node 0 (N blocks).  Load at every node ⇒ always
  // on, the cyclic wrap closes on node 0 with no start:
  //   30 · (50·24·10 + 24·100)
  constexpr int n = 8;
  std::vector<double> t;
  std::vector<double> w;
  for (int k = 0; k <= n; ++k) {
    const double theta = std::numbers::pi * k / n;
    t.push_back(12.0 * (1.0 - std::cos(theta)));
    double s = 0.0;
    for (int j = 1; j <= n / 2; ++j) {
      const double b = (2 * j == n) ? 1.0 : 2.0;
      s += b / ((4.0 * j * j) - 1.0) * std::cos(2.0 * j * theta);
    }
    const double c = (k == 0 || k == n) ? 1.0 : 2.0;
    w.push_back(30.0 * c / n * (1.0 - s) * 12.0);
  }
  w.front() += w.back();
  w.pop_back();
  t.pop_back();
  const Case cyc {.dur = w,
                  .load = std::vector<double>(t.size(), 50.0),
                  .uc_extra = std::string(kCyclic),
                  .stage_extra = R"(, "sampling": "point", "period_hours": 24)",
                  .time = t};
  CHECK(solve(cyc).obj == doctest::Approx(30.0 * 14400.0));
  // one start per cycle (load only at the midday node): the weighted
  // stage costs exactly 30 × the same typical day unweighted
  std::vector<double> mid(t.size(), 0.0);
  mid[4] = 50.0;
  Case peak = cyc;
  peak.load = mid;
  Case one = peak;
  for (auto& d : one.dur) {
    d /= 30.0;
  }
  one.stage_extra = R"(, "sampling": "point")";
  const auto day = solve(one).obj;
  CHECK(day > 1500.0);  // the start is paid
  CHECK(solve(peak).obj == doctest::Approx(30.0 * day));
}

TEST_CASE(  // NOLINT
    "Commitment ramps - start / stop tied to the status where SU / SD enter")
{
  using namespace cyclic_uc_test;
  // The 80 → 20 MW case with the DEFAULT startup / shutdown ramps
  // (pmax): with continuous v / w a fractional v = w with u unchanged
  // bought ramp room (cyclic 15300).  Binary v / w: ramp_down binds:
  // 12000 + 2400 + 2·2700 = 19800.
  std::vector<double> load(24, 20.0);
  std::fill_n(load.begin(), 12, 80.0);
  constexpr std::string_view ramps = R"(, "ramp_up": 30, "ramp_down": 30)";
  const Case cyc {.dur = std::vector<double>(24, 1.0),
                  .load = load,
                  .uc_extra = std::string(kCyclic) + std::string(ramps)};
  const auto mip = solve(cyc);
  CHECK(mip.obj == doctest::Approx(19800.0));
  // chronological from 80 MW: only the 11 → 12 h step: 17100
  const Case chrono {.dur = std::vector<double>(24, 1.0),
                     .load = load,
                     .uc_extra = R"(, "initial_status": 1, "initial_power": 80)"
                         + std::string(ramps)};
  CHECK(solve(chrono).obj == doctest::Approx(17100.0));
  // relaxed: fractional u / v / w may trade no-load for ramp room,
  // a weaker but valid relaxation: between
  // the relaxed no-ramp optimum and the MIP (14074.3 with HiGHS/CPLEX)
  Case rel = cyc;
  rel.relax = true;
  const auto r = solve(rel);
  Case rel_flat = rel;
  rel_flat.uc_extra = std::string(kCyclic);
  CHECK(r.obj <= mip.obj + 1e-6);
  CHECK(r.obj >= solve(rel_flat).obj - 1e-6);

  // the ramps add one ramp-up and one ramp-down row per block
  Case flat = cyc;
  flat.uc_extra = std::string(kCyclic);
  const auto none = solve(flat);
  CHECK(mip.nrows - none.nrows == 2 * 24);
  Case up_only = cyc;
  up_only.uc_extra = std::string(kCyclic) + R"(, "ramp_up": 30)";
  CHECK(solve(up_only).nrows - none.nrows == 24);
  // the removed transition_status_rows key is ignored: same LP
  Case untied = flat;
  untied.uc_extra += R"(, "transition_status_rows": false)";
  CHECK(solve(untied).nrows == none.nrows);
}

TEST_CASE(  // NOLINT
    "Point-stage ramps - backward interval slopes with the cyclic wrap")
{
  using namespace cyclic_uc_test;
  // Point stage, nodes t = 0, 4, 10, 16 in a 24 h cycle (weights 6 h
  // each), must-run g1, RU = RD = 5 MW/h.  Interval slope rows scaled by
  // h_k = t_k − t_{k−1}, wrap h_0 = t_0 + 24 − t_3 = 8:
  //   P0 − P3 ≤ 5·8 = 40,  P1 − P0 ≥ −20,  P2 − P1 ≥ −30
  // load 90, 90, 20, 20 ⇒ P3 ≤ 20 ⇒ P0 ≤ 60;  P2 ≤ 20 ⇒ P1 ≤ 50:
  //   g1 = 60, 50, 20, 20;  cost = 10·6·150 + 100·6·(30 + 40) + 100·24
  //      = 9000 + 42000 + 2400 = 53400
  // (a wrap over the full 24 h would allow P0 = 70; central2 is different)
  const Case c {.dur = {6.0, 6.0, 6.0, 6.0},
                .load = {90.0, 90.0, 20.0, 20.0},
                .uc_extra = std::string(kCyclic)
                    + R"(, "must_run": true, "ramp_up": 5, "ramp_down": 5)",
                .stage_extra = R"(, "sampling": "point", "period_hours": 24)",
                .time = {0.0, 4.0, 10.0, 16.0}};
  CHECK(solve(c).obj == doctest::Approx(53400.0));
}

TEST_CASE(  // NOLINT
    "validate_planning - central2 warning and duplicated periodic end node")
{
  using namespace cyclic_uc_test;
  Case c {.dur = {6.0, 6.0, 6.0, 6.0},
          .load = {10.0, 10.0, 10.0, 10.0},
          .stage_extra = R"(, "sampling": "point", "period_hours": 24)",
          .time = {0.0, 8.0, 16.0, 24.0}};
  Planning dup;
  dup.merge(parse_planning_json(make_json(c)));
  const auto r = validate_planning(dup);
  CHECK(std::ranges::any_of(
      r.warnings, [](const auto& w) { return w.contains("period_hours"); }));
  c.time = {0.0, 6.0, 12.0, 18.0};
  Planning ok;
  ok.merge(parse_planning_json(make_json(c)));
  CHECK_FALSE(std::ranges::any_of(validate_planning(ok).warnings,
                                  [](const auto& w)
                                  { return w.contains("period_hours"); }));
  auto json = make_json(c);
  const auto pos = json.find(R"("scale_objective": 1)");
  REQUIRE(pos != std::string::npos);
  json.insert(pos, R"("ramp_formula": "central2", )");
  Planning cen;
  cen.merge(parse_planning_json(json));
  CHECK(std::ranges::any_of(validate_planning(cen).warnings,
                            [](const auto& w)
                            { return w.contains("central2"); }));
}
