// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_bench_matpower_case30.cpp
 * @brief     Benchmark: MATPOWER `t_case30_userfcns` fixed zonal reserves (DC)
 * @date      2026-11-23
 *
 * Source (MATPOWER, 3-clause BSD; data and expected values reproduced
 * with attribution, Copyright (c) 1996-2024, Power Systems Engineering
 * Research Center (PSERC) and individual contributors):
 *  - case
 * <https://github.com/MATPOWER/matpower/blob/master/lib/t/t_case30_userfcns.m>
 *  - values
 * <https://github.com/MATPOWER/matpower/blob/master/lib/t/t_opf_userfcns.m>
 *    section "reserves + interface flow lims (DC)".
 *  - model  `lib/toggle_reserves.m`: per unit R in [0, qty], Pg + R ≤ Pmax,
 *           Σ_{g in zone} R_g ≥ req_zone, cost Σ c_g·R_g.
 *
 * Mapping: buses / branches (x in p.u., rateA → tmax) / loads one to one;
 * the quadratic gencost c2·P² + c1·P becomes a convex PWL with 0.1 MW
 * secant segments (exact at every breakpoint) through a unit-price fuel
 * and `heat_rate_segments`; each unit has ONE ReserveProvision listing
 * every zone it belongs to (overlapping zones: zone 1 = all, zone 2 =
 * units 5, 6), `urmax` = qty = 25 MW, `urcost` = offer; the two
 * interface limits are UserConstraints on signed branch flows.
 *
 * Expected (DC + reserves + interface limits):
 *   R    = [25 15 0 0 16.9 3.1] MW
 *   prc  = [2 2 2 2 5.5 5.5] $/MW  → zone duals 2 and 3.5
 *   reserve total cost 179.05 $, interface flows [-15, 20] MW.
 */

#include <array>
#include <cmath>
#include <cstddef>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

using Mp30Bus = std::array<double, 2>;  // BUS_I, PD
using Mp30Branch = std::array<double, 4>;  // F_BUS, T_BUS, BR_X, RATE_A
struct Mp30Gen
{
  int bus;
  double pmax;
  double c2;
  double c1;
  double rcost;
  bool zone2;
};

// mpc.bus (BUS_I, PD)
constexpr std::array<Mp30Bus, 30> kMp30Bus {{
    {1, 0.0},  {2, 21.7}, {300, 2.4}, {4, 7.6},  {5, 0.0},  {6, 0.0},
    {7, 22.8}, {8, 30.0}, {9, 0.0},   {10, 5.8}, {11, 0.0}, {12, 11.2},
    {13, 0.0}, {14, 6.2}, {15, 8.2},  {16, 3.5}, {17, 9.0}, {18, 3.2},
    {19, 9.5}, {20, 2.2}, {21, 17.5}, {22, 0.0}, {23, 3.2}, {24, 8.7},
    {25, 0.0}, {26, 3.5}, {27, 0.0},  {28, 0.0}, {29, 2.4}, {30, 10.6},
}};

// mpc.branch (F_BUS, T_BUS, BR_X, RATE_A)
constexpr std::array<Mp30Branch, 41> kMp30Branch {{
    {1, 2, 0.06, 130},   {1, 300, 0.19, 130}, {2, 4, 0.17, 65},
    {300, 4, 0.04, 130}, {2, 5, 0.2, 130},    {2, 6, 0.18, 65},
    {4, 6, 0.04, 90},    {5, 7, 0.12, 70},    {6, 7, 0.08, 130},
    {6, 8, 0.04, 32},    {6, 9, 0.21, 65},    {6, 10, 0.56, 32},
    {9, 11, 0.21, 65},   {9, 10, 0.11, 65},   {4, 12, 0.26, 65},
    {12, 13, 0.14, 65},  {12, 14, 0.26, 32},  {12, 15, 0.13, 32},
    {12, 16, 0.2, 32},   {14, 15, 0.2, 16},   {16, 17, 0.19, 16},
    {15, 18, 0.22, 16},  {18, 19, 0.13, 16},  {19, 20, 0.07, 32},
    {10, 20, 0.21, 32},  {10, 17, 0.08, 32},  {10, 21, 0.07, 32},
    {10, 22, 0.15, 32},  {21, 22, 0.02, 32},  {15, 23, 0.2, 16},
    {22, 24, 0.18, 16},  {23, 24, 0.27, 16},  {24, 25, 0.33, 16},
    {25, 26, 0.38, 16},  {25, 27, 0.21, 16},  {28, 27, 0.4, 65},
    {27, 29, 0.42, 16},  {27, 30, 0.6, 16},   {29, 30, 0.45, 16},
    {8, 28, 0.2, 32},    {6, 28, 0.06, 32},
}};

// mpc.gen (GEN_BUS, PMAX), mpc.gencost (c2, c1), mpc.reserves.cost and
// zone-2 membership.
constexpr std::array<Mp30Gen, 6> kMp30Gen {{
    {
        .bus = 1,
        .pmax = 80.0,
        .c2 = 0.02,
        .c1 = 2.0,
        .rcost = 1.9,
        .zone2 = false,
    },
    {
        .bus = 2,
        .pmax = 80.0,
        .c2 = 0.0175,
        .c1 = 1.75,
        .rcost = 2.0,
        .zone2 = false,
    },
    {
        .bus = 22,
        .pmax = 50.0,
        .c2 = 0.0625,
        .c1 = 1.0,
        .rcost = 3.0,
        .zone2 = false,
    },
    {
        .bus = 27,
        .pmax = 55.0,
        .c2 = 0.00834,
        .c1 = 3.25,
        .rcost = 4.0,
        .zone2 = false,
    },
    {
        .bus = 23,
        .pmax = 30.0,
        .c2 = 0.025,
        .c1 = 3.0,
        .rcost = 5.0,
        .zone2 = true,
    },
    {
        .bus = 13,
        .pmax = 40.0,
        .c2 = 0.025,
        .c1 = 3.0,
        .rcost = 5.5,
        .zone2 = true,
    },
}};

constexpr double kMp30Qty = 25.0;
constexpr double kMp30Step = 0.1;

// mpc.if.map: (interface, signed 1-based branch index)
constexpr std::array<std::array<int, 2>, 8> kMp30IfMap {{
    {1, -12},
    {1, -14},
    {1, -15},
    {1, -36},
    {2, 15},
    {2, 25},
    {2, 26},
    {2, -32},
}};

[[nodiscard]] std::string mp30_if_expr(int iface)
{
  std::string e;
  for (const auto& [i, br] : kMp30IfMap) {
    if (i != iface) {
      continue;
    }
    e += std::format("{} line('l{}').flow ", br < 0 ? "-" : "+", std::abs(br));
  }
  return e;
}

[[nodiscard]] std::string mp30_json()
{
  std::string buses;
  std::string demands;
  for (const auto& [bid, pd] : kMp30Bus) {
    const auto id = static_cast<int>(bid);
    buses += std::format(
        R"({}{{"uid": {}, "name": "b{}"}})", buses.empty() ? "" : ",", id, id);
    if (pd > 0.0) {
      demands += std::format(
          R"({}{{"uid": {}, "name": "d{}", "bus": "b{}", "lmax": {}}})",
          demands.empty() ? "" : ",",
          id,
          id,
          id,
          pd);
    }
  }
  std::string lines;
  for (std::size_t k = 0; k < kMp30Branch.size(); ++k) {
    const auto& [f, t, x, rate] = kMp30Branch.at(k);
    lines += std::format(
        R"({}{{"uid": {}, "name": "l{}", "bus_a": "b{}", "bus_b": "b{}",)"
        R"( "reactance": {}, "tmax_ab": {}, "tmax_ba": {}}})",
        k > 0 ? "," : "",
        k + 1,
        k + 1,
        static_cast<int>(f),
        static_cast<int>(t),
        x,
        rate,
        rate);
  }
  std::string gens;
  std::string provs;
  for (std::size_t g = 0; g < kMp30Gen.size(); ++g) {
    const auto& u = kMp30Gen.at(g);
    std::string pseg;
    std::string hseg;
    const auto n = static_cast<int>(std::lround(u.pmax / kMp30Step));
    for (int k = 0; k < n; ++k) {
      const double x0 = k * kMp30Step;
      const double x1 = (k + 1) * kMp30Step;
      // Secant slope of c2·P² + c1·P on [x0, x1].
      const double slope = (u.c2 * (x0 + x1)) + u.c1;
      pseg += std::format("{}{:.6g}", k > 0 ? "," : "", x1);
      hseg += std::format("{}{:.12g}", k > 0 ? "," : "", slope);
    }
    gens += std::format(
        R"({}{{"uid": {}, "name": "g{}", "bus": "b{}", "pmin": 0,)"
        R"( "pmax": {}, "capacity": {}, "gcost": 0, "fuel": "unit",)"
        R"( "pmax_segments": [{}], "heat_rate_segments": [{}]}})",
        g > 0 ? "," : "",
        g + 1,
        g + 1,
        u.bus,
        u.pmax,
        u.pmax,
        pseg,
        hseg);
    provs += std::format(
        R"({}{{"uid": {}, "name": "r{}", "generator": "g{}",)"
        R"( "reserve_zones": [{}], "urmax": {}, "ur_provision_factor": 1,)"
        R"( "urcost": {}}})",
        g > 0 ? "," : "",
        g + 1,
        g + 1,
        g + 1,
        u.zone2 ? R"("z1", "z2")" : R"("z1")",
        kMp30Qty,
        u.rcost);
  }
  return std::format(
      R"({{
  "options": {{
    "output_format": "csv", "output_compression": "uncompressed",
    "model_options": {{"use_single_bus": false, "use_kirchhoff": true,
                      "demand_fail_cost": 1000}}
  }},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]
  }},
  "system": {{
    "name": "matpower_case30_reserves",
    "bus_array": [{0}],
    "demand_array": [{1}],
    "line_array": [{2}],
    "fuel_array": [{{"uid": 1, "name": "unit", "price": 1.0}}],
    "generator_array": [{3}],
    "reserve_zone_array": [
      {{"uid": 1, "name": "z1", "urreq": 60, "urcost": 1000}},
      {{"uid": 2, "name": "z2", "urreq": 20, "urcost": 1000}}
    ],
    "reserve_provision_array": [{4}],
    "user_constraint_array": [
      {{"uid": 1, "name": "if1_lo", "expression": "{5} >= -15"}},
      {{"uid": 2, "name": "if1_up", "expression": "{5} <= 25"}},
      {{"uid": 3, "name": "if2_lo", "expression": "{6} >= -10"}},
      {{"uid": 4, "name": "if2_up", "expression": "{6} <= 20"}}
    ]
  }}
}})",
      buses,
      demands,
      lines,
      gens,
      provs,
      mp30_if_expr(1),
      mp30_if_expr(2));
}

template<typename E>
[[nodiscard]] const E& mp30_find(SystemLP& sys, std::string_view name)
{
  for (const auto& e : sys.template elements<E>()) {
    if (e.object().name == name) {
      return e;
    }
  }
  FAIL("element not found: " << name);
  return sys.template elements<E>().front();
}

}  // namespace

TEST_CASE("Benchmark MATPOWER case30 fixed zonal reserves (DC)")  // NOLINT
{
  PlanningLP plp(parse_planning_json(mp30_json()));
  REQUIRE(plp.resolve().has_value());

  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto duals = li.get_row_dual();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();

  // Reserve per unit and its total cost.
  constexpr std::array<double, 6> kR {25.0, 15.0, 0.0, 0.0, 16.9, 3.1};
  double rcost = 0.0;
  for (std::size_t g = 0; g < kMp30Gen.size(); ++g) {
    const auto& rp =
        mp30_find<ReserveProvisionLP>(sys, std::format("r{}", g + 1));
    const auto col = rp.lookup_up_provision_col(scenario, stage, buid);
    REQUIRE(col.has_value());
    const double r = sol[col.value_or(ColIndex {0})];
    CAPTURE(g + 1);
    // The PWL energy cost is exact on its 0.1 MW grid and the reference
    // P5 = Pmax5 − R5 = 13.1 MW lies on it: measured |error| < 1e-14.
    CAPTURE(r);
    CHECK(std::abs(r - kR.at(g)) <= 1.0e-6);
    rcost += kMp30Gen.at(g).rcost * r;
  }
  CHECK(rcost == doctest::Approx(179.05).epsilon(1.0e-8));

  // Zone prices: zone 1 = 2 (unit 2 marginal), zone 2 = 5.5 − 2 = 3.5.
  const auto zone_dual = [&](std::string_view z)
  {
    const auto& zone = mp30_find<ReserveZoneLP>(sys, z);
    const auto it =
        zone.urequirement_rows().find({scenario.uid(), stage.uid()});
    REQUIRE(it != zone.urequirement_rows().end());
    return std::abs(duals[it->second.at(buid)]);
  };
  CHECK(zone_dual("z1") == doctest::Approx(2.0).epsilon(1.0e-6));
  CHECK(zone_dual("z2") == doctest::Approx(3.5).epsilon(1.0e-6));

  // Interface flows: area-1 imports at its lower limit, area-2 at upper.
  const auto flow = [&](int k)
  {
    const auto& line = mp30_find<LineLP>(sys, std::format("l{}", k));
    double f = 0.0;
    const auto& fp = line.flowp_cols_at(scenario, stage);
    if (const auto it = fp.find(buid); it != fp.end()) {
      f += sol[it->second];
    }
    const auto& fn = line.flown_cols_at(scenario, stage);
    if (const auto it = fn.find(buid); it != fn.end()) {
      f -= sol[it->second];
    }
    return f;
  };
  const auto iface = [&](int i)
  {
    double s = 0.0;
    for (const auto& [ii, br] : kMp30IfMap) {
      if (ii == i) {
        s += (br < 0 ? -1.0 : 1.0) * flow(std::abs(br));
      }
    }
    return s;
  };
  CHECK(iface(1) == doctest::Approx(-15.0).epsilon(1.0e-6));
  CHECK(iface(2) == doctest::Approx(20.0).epsilon(1.0e-6));
}
