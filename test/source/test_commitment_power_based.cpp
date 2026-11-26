// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_power_based.cpp
 * @brief     `uc_formulation = "power_based"` (Morales-España et al.)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * PB1–PB6 of docs/unit-commitment.md §4.6: boundary powers P̂_b, block
 * energy = trapezoid, exact instantaneous ramps, start-up / shut-down
 * trajectories, power-capacity and ramp-capability reserves, and the
 * commitment grid kept apart from (coarser than) the power grid.
 */

#include <cmath>
#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/commitment_power_based.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace power_based_uc_test
{
namespace
{

struct Case
{
  std::vector<double> dur;  ///< block durations [h]
  std::vector<double> load;  ///< demand per block [MW]
  std::string model_extra {};  ///< extra model_options members
  std::string stage_extra {};
  std::string gen_extra {};  ///< extra members of generator g1
  std::string uc_extra {};  ///< extra members of commitment g1_uc
  std::string more_units {};  ///< extra generators (leading comma)
  std::string more_ucs {};  ///< extra commitments (leading comma)
  std::string system_extra {};  ///< extra system arrays (leading comma)
  double g1_cap {100.0};
  double peak_cost {1000.0};
  std::vector<int> cell {};  ///< Block.commitment_cell per block (opt.)
};

[[nodiscard]] std::string list(const std::vector<double>& v)
{
  std::string s;
  for (std::size_t i = 0; i < v.size(); ++i) {
    s += std::format("{}{:.17g}", i == 0 ? "" : ", ", v[i]);
  }
  return s;
}

[[nodiscard]] std::string make_json(const Case& c)
{
  std::string blocks;
  for (std::size_t k = 0; k < c.dur.size(); ++k) {
    blocks += std::format(
        R"({}{{"uid": {}, "duration": {:.17g}{}}})",
        k == 0 ? "" : ", ",
        k + 1,
        c.dur[k],
        c.cell.empty() ? std::string {}
                       : std::format(R"(, "commitment_cell": {})", c.cell[k]));
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 100000,
                                  "scale_objective": 1 {}}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "power_based_uc",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10,
        "capacity": {:.17g} {}}},
      {{"uid": 2, "name": "peak", "bus": "b1", "gcost": {:.17g},
        "capacity": 1000}} {}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1
                           {}}} {}] {}}}
}})",
      c.model_extra,
      blocks,
      c.dur.size(),
      c.stage_extra,
      list(c.load),
      c.g1_cap,
      c.gen_extra,
      c.peak_cost,
      c.more_units,
      c.uc_extra,
      c.more_ucs,
      c.system_extra);
}

constexpr std::string_view kPowerBased = R"(, "uc_formulation": "power_based")";

/// A solved case with typed accessors.
struct Solved
{
  PlanningLP plp;

  /// Integer columns of the LP as built (before the MIP solve, whose
  /// polish fixes and relaxes them).
  Index n_integer {0};

  explicit Solved(const Case& c)
      : plp(parse_planning_json(make_json(c)))
  {
    const auto& lp = plp.systems().front().front().linear_interface();
    for (Index col = 0; col < lp.get_numcols(); ++col) {
      if (lp.is_integer(ColIndex {col})) {
        ++n_integer;
      }
    }
    const auto res = plp.resolve();
    REQUIRE(res.has_value());
  }

  [[nodiscard]] auto& sys() { return plp.systems().front().front(); }
  [[nodiscard]] const auto& li() { return sys().linear_interface(); }
  [[nodiscard]] const auto& scenario()
  {
    return sys().scene().scenarios().front();
  }
  [[nodiscard]] const auto& stage() { return sys().phase().stages().front(); }
  [[nodiscard]] double obj() { return li().get_obj_value(); }

  [[nodiscard]] double sol(ColIndex c) { return li().get_col_sol()[c]; }

  /// Generation (block-average power) of generator @p gi per block.
  [[nodiscard]] std::vector<double> gen(std::size_t gi)
  {
    std::vector<double> out;
    for (const auto& b : stage().blocks()) {
      const auto col =
          sys().elements<GeneratorLP>().at(gi).lookup_generation_col(
              scenario(), stage(), b.uid());
      out.push_back(col ? sol(*col) : 0.0);
    }
    return out;
  }

  /// Boundary powers P̂_1..P̂_B of commitment @p ci (empty: none).
  [[nodiscard]] std::vector<double> power(std::size_t ci)
  {
    std::vector<double> out;
    const auto* cols = sys().elements<CommitmentLP>().at(ci).find_power_cols(
        scenario(), stage());
    if (cols == nullptr) {
      return out;
    }
    for (const auto& b : stage().blocks()) {
      out.push_back(sol(cols->at(b.uid())));
    }
    return out;
  }

  /// Status u per block of commitment @p ci.
  [[nodiscard]] std::vector<double> status(std::size_t ci)
  {
    std::vector<double> out;
    const auto& uc = sys().elements<CommitmentLP>().at(ci);
    for (const auto& b : stage().blocks()) {
      const auto col = uc.lookup_status_col(scenario(), stage(), b.uid());
      out.push_back(col ? sol(*col) : -1.0);
    }
    return out;
  }

  [[nodiscard]] Index integer_cols() const { return n_integer; }
};

[[nodiscard]] std::vector<double> hours(std::size_t n, double h = 1.0)
{
  return std::vector<double>(n, h);
}

/// Full LP equality (sizes, bounds, objective, every coefficient).
void check_same_lp(Solved& a, Solved& b)
{
  const auto& la = a.li();
  const auto& lb = b.li();
  REQUIRE(la.get_numcols() == lb.get_numcols());
  REQUIRE(la.get_numrows() == lb.get_numrows());
  const auto ncols = la.get_numcols();
  const auto nrows = la.get_numrows();
  const auto oa = la.get_obj_coeff();
  const auto ob = lb.get_obj_coeff();
  for (Index c = 0; c < ncols; ++c) {
    const ColIndex ci {c};
    CHECK(la.get_col_low()[ci] == lb.get_col_low()[ci]);
    CHECK(la.get_col_upp()[ci] == lb.get_col_upp()[ci]);
    CHECK(oa[static_cast<std::size_t>(c)] == ob[static_cast<std::size_t>(c)]);
    CHECK(la.is_integer(ci) == lb.is_integer(ci));
  }
  for (Index r = 0; r < nrows; ++r) {
    const RowIndex ri {r};
    CHECK(la.get_row_low()[ri] == lb.get_row_low()[ri]);
    CHECK(la.get_row_upp()[ri] == lb.get_row_upp()[ri]);
    for (Index c = 0; c < ncols; ++c) {
      const auto ca = la.get_coeff_raw(ri, ColIndex {c});
      const auto cb = lb.get_coeff_raw(ri, ColIndex {c});
      if (ca != cb) {
        FAIL_CHECK(std::format("row {} col {}: {} vs {}", r, c, ca, cb));
      }
    }
  }
}

/// A UC case with ramps, start-up ramps, min up / down and an up / down
/// reserve, i.e. every energy-block row the power-based option replaces.
[[nodiscard]] Case rich_case()
{
  return {
      .dur = {1, 1, 2, 1, 1, 0.5},
      .load = {40, 90, 120, 60, 20, 70},
      .uc_extra = R"(, "pmin": 20, "ramp_up": 40, "ramp_down": 40,
                     "startup_ramp": 30, "shutdown_ramp": 30,
                     "min_up_time": 2, "min_down_time": 1,
                     "startup_cost": 100, "noload_cost": 5,
                     "initial_status": 1, "initial_power": 40)",
      .system_extra = R"(,
    "reserve_zone_array": [{"uid": 1, "name": "z", "urreq": 10,
                            "drreq": 5, "urcost": 500, "drcost": 500}],
    "reserve_provision_array": [{"uid": 1, "name": "rp", "generator": "g1",
                                 "reserve_zones": [1], "urmax": 50,
                                 "drmax": 50, "ur_provision_factor": 1,
                                 "dr_provision_factor": 1}])",
  };
}

}  // namespace
}  // namespace power_based_uc_test

TEST_CASE("power_based - trajectory helpers")  // NOLINT
{
  // start-up [P^SU_1, P^SU_2] = [0, 25], hourly, P = 50:
  // f(0) = 50 (excluded: the P·v term), f(1) = 25, f(2) = 0, f(>2) = 0
  const std::vector<double> su {0.0, 25.0};
  CHECK(startup_trajectory_power(su, 1.0, 50.0, 0.0) == doctest::Approx(0.0));
  CHECK(startup_trajectory_power(su, 1.0, 50.0, 0.5) == doctest::Approx(37.5));
  CHECK(startup_trajectory_power(su, 1.0, 50.0, 1.0) == doctest::Approx(25.0));
  CHECK(startup_trajectory_power(su, 1.0, 50.0, 1.5) == doctest::Approx(12.5));
  CHECK(startup_trajectory_power(su, 1.0, 50.0, 2.0) == doctest::Approx(0.0));
  CHECK(startup_trajectory_power(su, 1.0, 50.0, 3.0) == doctest::Approx(0.0));
  // shut-down [P^SD_2, P^SD_3] = [25, 0]
  const std::vector<double> sd {25.0, 0.0};
  CHECK(shutdown_trajectory_power(sd, 1.0, 50.0, 1.0) == doctest::Approx(25.0));
  CHECK(shutdown_trajectory_power(sd, 1.0, 50.0, 0.25)
        == doctest::Approx(43.75));
  CHECK(shutdown_trajectory_power(sd, 1.0, 50.0, 2.0) == doctest::Approx(0.0));
  // 30-min samples: [10, 20, 30] before the start
  const std::vector<double> su2 {10.0, 20.0, 30.0};
  CHECK(startup_trajectory_power(su2, 0.5, 40.0, 0.5) == doctest::Approx(30.0));
  CHECK(startup_trajectory_power(su2, 0.5, 40.0, 1.5) == doctest::Approx(10.0));
  CHECK(startup_trajectory_power(su2, 0.5, 40.0, 1.6) == doctest::Approx(0.0));
  CHECK(startup_trajectory_power({}, 1.0, 40.0, 0.5) == doctest::Approx(0.0));
}

TEST_CASE("power_based - energy_block default LP is unchanged")  // NOLINT
{
  using namespace power_based_uc_test;
  // The default, the explicit global "energy_block" and the per-unit
  // "energy_block" build the identical LP (the power-based code path is
  // never entered).
  auto c = rich_case();
  Solved dflt(c);
  c.model_extra = R"(, "uc_formulation": "energy_block")";
  Solved global(c);
  c.model_extra = kPowerBased;
  c.uc_extra += R"(, "uc_formulation": "energy_block")";
  Solved per_unit(c);
  check_same_lp(dflt, global);
  check_same_lp(dflt, per_unit);
  CHECK(dflt.power(0).empty());
  CHECK(dflt.obj() == doctest::Approx(global.obj()));
  // and power_based does change it
  c = rich_case();
  c.model_extra = kPowerBased;
  Solved pb(c);
  CHECK(pb.li().get_numcols() > dflt.li().get_numcols());
  CHECK(pb.power(0).size() == 6);
}

TEST_CASE(
    "power_based - block energy is the trapezoid of the powers")  // NOLINT
{
  using namespace power_based_uc_test;
  auto c = rich_case();
  c.model_extra = kPowerBased;
  Solved s(c);
  const auto g = s.gen(0);
  const auto peak = s.gen(1);
  const auto p = s.power(0);
  REQUIRE(p.size() == c.dur.size());
  // P̂_0 = initial_power = 40
  double prev = 40.0;
  double gen_energy = 0.0;
  for (std::size_t b = 0; b < p.size(); ++b) {
    CHECK(g[b] == doctest::Approx(0.5 * (prev + p[b])));
    // the same energy is what the bus balance sees
    CHECK(g[b] + peak[b] == doctest::Approx(c.load[b]));
    gen_energy += 0.5 * (prev + p[b]) * c.dur[b];
    prev = p[b];
  }
  // the energy cost is the trapezoid energy at 10 $/MWh
  double g_energy = 0.0;
  for (std::size_t b = 0; b < g.size(); ++b) {
    g_energy += g[b] * c.dur[b];
  }
  CHECK(g_energy == doctest::Approx(gen_energy));
}

TEST_CASE("power_based - ramps bound the instantaneous power")  // NOLINT
{
  using namespace power_based_uc_test;
  // OR Spectrum 2015, Fig. 1: a 100–300 MW unit ramping 200 MW/h, at
  // 100 MW, asked for 100 then 300 MW.  The energy-block schedule
  // delivers 300 MW in hour 2; the power trajectory reaches 300 MW only
  // at its end, so hour 2 averages 200 MW and 100 MWh come from peak.
  Case c {
      .dur = {1, 1},
      .load = {100, 300},
      .uc_extra = R"(, "pmin": 100, "ramp_up": 200, "ramp_down": 200,
                     "must_run": true, "initial_status": 1,
                     "initial_power": 100)",
      .g1_cap = 300,
  };
  Solved eb(c);
  CHECK(eb.gen(0)[1] == doctest::Approx(300.0));
  CHECK(eb.obj() == doctest::Approx(4000.0));

  c.model_extra = kPowerBased;
  Solved pb(c);
  const auto p = pb.power(0);
  REQUIRE(p.size() == 2);
  CHECK(p[0] == doctest::Approx(100.0));
  CHECK(p[1] == doctest::Approx(300.0));
  CHECK(pb.gen(0)[1] == doctest::Approx(200.0));
  CHECK(pb.gen(1)[1] == doctest::Approx(100.0));
  CHECK(pb.obj() == doctest::Approx((10.0 * 300.0) + (1000.0 * 100.0)));

  // sub-hourly power grid: 4 × 15 min per hour, 1 h commitment grid.
  // The ramp holds on every 15-min boundary (≤ 50 MW each), the hourly
  // energies are unchanged.
  c.dur = hours(8, 0.25);
  c.load = {100, 100, 100, 100, 300, 300, 300, 300};
  Solved fine(c);
  const auto pf = fine.power(0);
  REQUIRE(pf.size() == 8);
  double prev = 100.0;
  for (const double x : pf) {
    CHECK(x - prev <= 50.0 + 1e-6);
    CHECK(prev - x <= 50.0 + 1e-6);
    prev = x;
  }
  CHECK(fine.integer_cols() == 6);  // u, v, w of 2 cells

  // Unknown initial power: P̂_0 is free in [P, P̄] for an on unit, so a
  // flat 300 MW load is met from the first hour.  The known 100 MW start
  // above caps hour 1 at (100 + 300) / 2 = 200 MW.
  c.dur = {1, 1};
  c.load = {300, 300};
  c.uc_extra = R"(, "pmin": 100, "ramp_up": 200, "ramp_down": 200,
                   "must_run": true, "initial_status": 1)";
  Solved free(c);
  CHECK(free.gen(0)[0] == doctest::Approx(300.0));
  CHECK(free.obj() == doctest::Approx(10.0 * 600.0));
}

TEST_CASE("power_based - ramps scale with the block duration")  // NOLINT
{
  using namespace power_based_uc_test;
  // Two blocks of 2 h and 3 h, RU = 10 MW/h, the unit runs flat out
  // against a 1000 $/MWh peak.  Hand bounds, with RU [MW/h] times the
  // block's own length Δt_b:
  //   power_based:  P̂_0 = 10, P̂_1 ≤ 10 + 10·2 = 30, P̂_2 ≤ 30 + 10·3 = 60
  //                 → g = (½(10+30), ½(30+60)) = (20, 45)
  //   energy_block: g_1 ≤ 10 + 10·2 = 30, g_2 ≤ 30 + 10·3 = 60
  // A ramp read per block instead of per hour (RU·1) would give 15 / 30.
  Case c {
      .dur = {2, 3},
      .load = {200, 200},
      .uc_extra = R"(, "pmin": 10, "ramp_up": 10, "ramp_down": 10,
                     "must_run": true, "initial_status": 1,
                     "initial_power": 10)",
  };
  Solved eb(c);
  CHECK(eb.gen(0)[0] == doctest::Approx(30.0));
  CHECK(eb.gen(0)[1] == doctest::Approx(60.0));

  c.model_extra = kPowerBased;
  Solved pb(c);
  const auto p = pb.power(0);
  REQUIRE(p.size() == 2);
  CHECK(p[0] == doctest::Approx(30.0));
  CHECK(p[1] == doctest::Approx(60.0));
  CHECK(pb.gen(0)[0] == doctest::Approx(20.0));
  CHECK(pb.gen(0)[1] == doctest::Approx(45.0));
  // energy 20·2 + 45·3 = 175 MWh at 10, the rest (825 MWh) from peak
  CHECK(pb.obj() == doctest::Approx((10.0 * 175.0) + (1000.0 * 825.0)));

  // Start-up: an initially off unit with SU = 40 MW.  The start-up
  // capability is a power jump at the start boundary, not a rate, so it
  // does not scale with Δt: P̂_0 ≤ SU = 40, then the ramp adds RU·Δt_b:
  //   P̂_1 ≤ 40 + 20 = 60, P̂_2 ≤ 60 + 30 = 90 → g = (50, 75)
  c.uc_extra = R"(, "pmin": 10, "ramp_up": 10, "ramp_down": 10,
                   "startup_ramp": 40, "shutdown_ramp": 40,
                   "initial_status": 0)";
  Solved su(c);
  const auto ps = su.power(0);
  REQUIRE(ps.size() == 2);
  CHECK(su.status(0)[0] == doctest::Approx(1.0));
  CHECK(ps[0] == doctest::Approx(60.0));
  CHECK(ps[1] == doctest::Approx(90.0));
  CHECK(su.gen(0)[0] == doctest::Approx(50.0));
  CHECK(su.gen(0)[1] == doctest::Approx(75.0));

  // energy_block reads the same values: g_1 ≤ SU = 40 (unscaled),
  // g_2 ≤ 40 + 10·3 = 70
  c.model_extra.clear();
  Solved eb_su(c);
  CHECK(eb_su.gen(0)[0] == doctest::Approx(40.0));
  CHECK(eb_su.gen(0)[1] == doctest::Approx(70.0));
}

TEST_CASE("power_based - a one-block outage passes through pmin")  // NOLINT
{
  using namespace power_based_uc_test;
  // A quick unit (SU = SD = P̄ by default) with a high no-load cost.
  // Without PB7 an "off" block would run from SD to SU and still
  // deliver 60 MW, so cycling off every other hour would save the
  // no-load cost for free.  With PB7 an outage block averages P = 30 MW
  // and the other 30 MWh cost 1000 $/MWh: the unit stays on.
  Case c {
      .dur = {1, 1, 1, 1},
      .load = {60, 60, 60, 60},
      .model_extra = std::string(kPowerBased),
      .uc_extra = R"(, "pmin": 30, "noload_cost": 500, "startup_cost": 1,
                     "initial_status": 1, "initial_power": 60)",
  };
  Solved pb(c);
  for (const double u : pb.status(0)) {
    CHECK(u == doctest::Approx(1.0));
  }
  for (const double g : pb.gen(0)) {
    CHECK(g == doctest::Approx(60.0));
  }
  CHECK(pb.obj() == doctest::Approx((4 * 60.0 * 10.0) + (4 * 500.0)));
}

TEST_CASE("power_based - start-up / shut-down trajectory energy")  // NOLINT
{
  using namespace power_based_uc_test;
  // Slow-start unit, P = 50, S^U = S^D = 2 h:
  //   start-up [0, 25]  → blocks before the start: (0+25)/2, (25+50)/2
  //   shut-down [25, 0] → blocks after the stop:   (50+25)/2, (25+0)/2
  // Off at 0 with zero output, so the earliest start is period 2.  The
  // load drops to 37.5 / 12.5 / 0 at the end: the unit must stop at
  // T = 6 and its shut-down energy serves exactly the last load.
  Case c {
      .dur = hours(10),
      .load = {60, 60, 60, 60, 60, 60, 37.5, 12.5, 0, 0},
      .model_extra = std::string {kPowerBased},
      .uc_extra = R"(, "pmin": 50, "initial_status": 0,
                     "initial_power": 0,
                     "startup_trajectory": [0, 25],
                     "shutdown_trajectory": [25, 0])",
  };
  Solved s(c);
  const auto g = s.gen(0);
  const auto u = s.status(0);
  CHECK(u[0] == doctest::Approx(0.0));
  CHECK(u[1] == doctest::Approx(0.0));
  CHECK(u[2] == doctest::Approx(1.0));
  CHECK(u[5] == doctest::Approx(1.0));
  CHECK(u[6] == doctest::Approx(0.0));
  CHECK(g[0] == doctest::Approx(12.5));
  CHECK(g[1] == doctest::Approx(37.5));
  CHECK(g[2] == doctest::Approx(60.0));
  CHECK(g[6] == doctest::Approx(37.5));
  CHECK(g[7] == doctest::Approx(12.5));
  CHECK(g[8] == doctest::Approx(0.0));
  const auto p = s.power(0);
  CHECK(p[0] == doctest::Approx(25.0));
  CHECK(p[1] == doctest::Approx(50.0));  // P at the start instant
  CHECK(p[5] == doctest::Approx(50.0));  // P at the stop instant (SD = P)
  CHECK(p[6] == doctest::Approx(25.0));
  CHECK(p[7] == doctest::Approx(0.0));

  // quick-start unit (no trajectory): the block before the up period
  // ramps 0 → P (+ up to SU − P), energy P/2 at least
  c.uc_extra = R"(, "pmin": 50, "startup_ramp": 50, "initial_status": 0,
                   "initial_power": 0)";
  c.load = {0, 25, 60, 60, 60, 60, 60, 60, 60, 60};
  Solved q(c);
  const auto gq = q.gen(0);
  CHECK(gq[0] == doctest::Approx(0.0));
  CHECK(gq[1] == doctest::Approx(25.0));
  CHECK(q.status(0)[2] == doctest::Approx(1.0));
}

TEST_CASE("power_based - reserves are deliverable within the ramp")  // NOLINT
{
  using namespace power_based_uc_test;
  // Flat 50 MW, ramp 10 MW/h.  30 MW of up reserve is asked for; the
  // energy-block rows let g1 offer all 30 (headroom 50), the power-based
  // ramp-capability row only RU·Δ − (p_b − p_{b−1}) = 10 per hour.
  Case c {
      .dur = hours(4),
      .load = {50, 50, 50, 50},
      .uc_extra = R"(, "pmin": 20, "ramp_up": 10, "ramp_down": 10,
                     "must_run": true, "initial_status": 1,
                     "initial_power": 50)",
      .system_extra = R"(,
    "reserve_zone_array": [{"uid": 1, "name": "z", "urreq": 30,
                            "urcost": 1000}],
    "reserve_provision_array": [{"uid": 1, "name": "rp", "generator": "g1",
                                 "reserve_zones": [1], "urmax": 100,
                                 "ur_provision_factor": 1}])",
      .peak_cost = 5000,
  };
  const auto provision = [](Solved& s)
  {
    std::vector<double> out;
    const auto& rp = s.sys().elements<ReserveProvisionLP>().front();
    for (const auto& b : s.stage().blocks()) {
      const auto col =
          rp.lookup_up_provision_col(s.scenario(), s.stage(), b.uid());
      REQUIRE(col.has_value());
      out.push_back(s.sol(col.value_or(ColIndex {0})));
    }
    return out;
  };
  Solved eb(c);
  for (const double r : provision(eb)) {
    CHECK(r >= 30.0 - 1e-6);  // the whole requirement (headroom 50)
  }
  c.model_extra = kPowerBased;
  Solved pb(c);
  const auto p = pb.power(0);
  double prev = 50.0;
  const auto r = provision(pb);
  for (std::size_t b = 0; b < r.size(); ++b) {
    CHECK(r[b] == doctest::Approx(10.0));
    // deliverable: P̂_b + r ≤ P̂_{b−1} + RU·Δ and ≤ P̄
    CHECK(p[b] + r[b] <= prev + 10.0 + 1e-6);
    CHECK(p[b] + r[b] <= 100.0 + 1e-6);
    prev = p[b];
  }
}

TEST_CASE("power_based - storage conservation with a trapezoid unit")  // NOLINT
{
  using namespace power_based_uc_test;
  // A ramp-limited power-based unit and a battery: the battery balance
  // integrates its block flows; the bus balance meets every block load
  // with the unit's trapezoid energy; the battery level closes exactly.
  Case c {
      .dur = {1, 1, 1, 1, 1, 1},
      .load = {40, 120, 40, 120, 40, 120},
      .model_extra = std::string {kPowerBased},
      .uc_extra = R"(, "pmin": 20, "ramp_up": 30, "ramp_down": 30,
                     "initial_status": 1, "initial_power": 60)",
      .system_extra = R"(,
    "battery_array": [{"uid": 1, "name": "bat", "bus": "b1", "emin": 0,
                       "emax": 200, "eini": 50, "capacity": 200,
                       "pmax_charge": 100, "pmax_discharge": 100,
                       "input_efficiency": 0.9, "output_efficiency": 0.9,
                       "annual_standing_loss": 0,
                       "use_state_variable": false,
                       "daily_cycle": false}])",
  };
  Solved s(c);
  const auto g = s.gen(0);
  const auto peak = s.gen(1);
  const auto& bat = s.sys().elements<BatteryLP>().front();
  const auto& fin = bat.finp_cols_at(s.scenario(), s.stage());
  const auto& fout = bat.fout_cols_at(s.scenario(), s.stage());
  const auto& ecols = bat.energy_cols_at(s.scenario(), s.stage());
  double level = 50.0;
  for (std::size_t b = 0; b < g.size(); ++b) {
    const auto buid = s.stage().blocks()[b].uid();
    const double ch = s.sol(fin.at(buid));
    const double dis = s.sol(fout.at(buid));
    CHECK(g[b] + peak[b] + dis - ch == doctest::Approx(c.load[b]));
    level += ((0.9 * ch) - (dis / 0.9)) * c.dur[b];
    CHECK(s.sol(ecols.at(buid)) == doctest::Approx(level));
  }
}

TEST_CASE(  // NOLINT
    "power_based - integer columns do not depend on the power grid")
{
  using namespace power_based_uc_test;
  const std::vector<double> day {40, 40, 40, 40, 40, 60, 80, 90,
                                 90, 80, 70, 70, 70, 70, 80, 90,
                                 95, 95, 90, 80, 60, 50, 45, 40};
  const auto refine = [](const std::vector<double>& v, int n)
  {
    std::vector<double> out;
    for (const double x : v) {
      out.insert(out.end(), static_cast<std::size_t>(n), x);
    }
    return out;
  };
  Case c {
      .dur = hours(24),
      .load = day,
      .model_extra = std::string {kPowerBased},
      .uc_extra = R"(, "pmin": 30, "ramp_up": 40, "ramp_down": 40,
                     "min_up_time": 3, "min_down_time": 2,
                     "startup_cost": 200, "relax": false,
                     "initial_status": 1, "initial_power": 40)",
  };
  Solved hourly(c);
  c.dur = hours(96, 0.25);
  c.load = refine(day, 4);
  Solved quarter(c);
  // 24 u, v, w per unit (all binary); no default 15-min binaries
  CHECK(hourly.integer_cols() == 3 * 24);
  CHECK(quarter.integer_cols() == 3 * 24);
  // refined blocks labelled with 2 h commitment cells: 12 binaries
  for (int b = 0; b < 96; ++b) {
    c.cell.push_back(b / 8);
  }
  Solved cells(c);
  CHECK(cells.integer_cols() == 3 * 12);
  c.cell.clear();
  // explicit 3 h grid: 8 binaries on both power grids
  c.uc_extra += R"(, "commitment_period": 3)";
  Solved q3(c);
  c.dur = hours(24);
  c.load = day;
  Solved h3(c);
  CHECK(q3.integer_cols() == 3 * 8);
  CHECK(h3.integer_cols() == 3 * 8);
  // the energy-block model on 15-min blocks has one u, v, w per block
  c.model_extra.clear();
  c.uc_extra = R"(, "pmin": 30, "initial_status": 1)";
  c.dur = hours(96, 0.25);
  c.load = refine(day, 4);
  Solved eb(c);
  CHECK(eb.integer_cols() == 3 * 96);
}

TEST_CASE(  // NOLINT
    "power_based - per-unit commitment grids of 1, 3 and 4 h")
{
  using namespace power_based_uc_test;
  // Three units on a 15-min power grid with overlay grids of 1 h (fast),
  // 3 h (medium) and 4 h (slow): 24 + 8 + 6 binaries, independent of the
  // power grid.  Every power point inherits its own unit's cell state.
  const std::vector<double> day {60,  60,  60,  60,  70,  80,  120, 150,
                                 160, 150, 130, 120, 120, 130, 150, 170,
                                 180, 170, 150, 120, 90,  60,  50,  50};
  const auto refine = [](const std::vector<double>& v, int n)
  {
    std::vector<double> out;
    for (const double x : v) {
      out.insert(out.end(), static_cast<std::size_t>(n), x);
    }
    return out;
  };
  Case c {
      .dur = hours(96, 0.25),
      .load = refine(day, 4),
      .model_extra = std::string {kPowerBased},
      .uc_extra = R"(, "pmin": 20, "ramp_up": 60, "ramp_down": 60,
                     "commitment_period": 1, "startup_cost": 50,
                     "initial_status": 0, "initial_power": 0)",
      .more_units = R"(,
      {"uid": 3, "name": "g3", "bus": "b1", "gcost": 8, "capacity": 80},
      {"uid": 4, "name": "g4", "bus": "b1", "gcost": 5, "capacity": 80})",
      .more_ucs = R"(,
      {"uid": 2, "name": "g3_uc", "generator": 3, "pmin": 30,
       "ramp_up": 30, "ramp_down": 30, "commitment_period": 3,
       "startup_cost": 500, "initial_status": 0, "initial_power": 0},
      {"uid": 3, "name": "g4_uc", "generator": 4, "pmin": 40,
       "ramp_up": 20, "ramp_down": 20, "commitment_period": 4,
       "startup_cost": 2000, "initial_status": 1, "initial_power": 40,
       "startup_trajectory": [0, 20], "shutdown_trajectory": [20, 0]})",
  };
  Solved q(c);
  CHECK(q.integer_cols() == 3 * (24 + 8 + 6));
  c.dur = hours(24);
  c.load = day;
  Solved h(c);
  CHECK(h.integer_cols() == 3 * (24 + 8 + 6));

  // state inheritance on the 15-min solution: each unit's status is
  // constant over its own cell, and a boundary inside an OFF cell
  // carries no power unless an event (the next cell's start) or a
  // trajectory touches it.
  const std::vector<std::size_t> cell {4, 12, 16};  // 15-min blocks per cell
  const std::vector<double> pmax {100, 80, 80};
  for (std::size_t ci = 0; ci < 3; ++ci) {
    const auto u = q.status(ci);
    const auto p = q.power(ci);
    REQUIRE(p.size() == 96);
    for (std::size_t b = 0; b < 96; ++b) {
      const auto first = b - (b % cell[ci]);
      CHECK(u[b] == doctest::Approx(u[first]));
      CHECK(p[b] <= (pmax[ci] * 1.0) + 1e-6);
      const bool cell_end = (b + 1) % cell[ci] == 0;
      if (ci < 2 && u[b] < 0.5 && !cell_end) {
        CHECK(p[b] == doctest::Approx(0.0));  // quick start: no events
      }
    }
  }
}

TEST_CASE("power_based - cyclic stage wraps the first boundary")  // NOLINT
{
  using namespace power_based_uc_test;
  Case c {
      .dur = hours(6),
      .load = {40, 80, 100, 100, 60, 40},
      .model_extra = std::string {kPowerBased},
      .uc_extra = R"(, "pmin": 20, "ramp_up": 30, "ramp_down": 30,
                     "must_run": true, "cycle": "stage")",
  };
  Solved s(c);
  const auto g = s.gen(0);
  const auto p = s.power(0);
  REQUIRE(p.size() == 6);
  CHECK(g[0] == doctest::Approx(0.5 * (p[5] + p[0])));
  for (std::size_t b = 1; b < 6; ++b) {
    CHECK(g[b] == doctest::Approx(0.5 * (p[b - 1] + p[b])));
    CHECK(std::abs(p[b] - p[b - 1]) <= 30.0 + 1e-6);
  }
  CHECK(std::abs(p[0] - p[5]) <= 30.0 + 1e-6);
}
