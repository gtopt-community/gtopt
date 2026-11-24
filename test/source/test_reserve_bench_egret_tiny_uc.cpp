// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_bench_egret_tiny_uc.cpp
 * @brief     Benchmark: Egret `tiny_uc_1` unit commitment with reserve,
 *            the subset gtopt represents, against Egret's own optimum
 * @date      2026-11-23
 *
 * Source: Egret (Sandia / grid-parity-exchange, Revised BSD licence),
 * https://github.com/grid-parity-exchange/Egret/tree/main/egret/models/
 * tests/uc_test_instances — `tiny_uc_1.json`: one bus, 7 thermal units,
 * 24 h, reference total_cost 487 389.24464 (reproduced: Egret 0.6.2 +
 * HiGHS, 487 389.24469).  The unit data and the load / reserve series
 * below are copied from that file (BSD, attribution above).
 *
 * What gtopt does NOT represent (dropped from the subset on BOTH sides):
 *  - the ancillary-service family: regulation up/down with AGC ranges
 *    (p_min_agc / p_max_agc / ramp_agc), spinning, non-spinning (offline
 *    fast-start capacity), supplemental and flexible-ramp up/down
 *    requirements, system and Area1;
 *  - reserve SUBSTITUTION (Egret lets a higher-quality product count for
 *    a lower one: regulation ⊂ spinning ⊂ non-spin ⊂ supplemental); a
 *    gtopt provision serving two zones counts the SAME MW in both;
 *  - flexible ramp products (inter-temporal ramp deliverability);
 *  - ramp-limited reserve deliverability: Egret's available power p̄_t is
 *    bounded by p_{t−1} + ramp / startup capacity, gtopt's headroom
 *    reserve only by g + r ≤ Pmax·u;
 *  - quadratic cost: gtopt is an LP/MIP, so BOTH models get the same
 *    piecewise-linear curve (4 secant segments pmin → pmax; [0, pmin] is
 *    one secant segment in gtopt with c0 as Commitment.noload_cost, Egret
 *    gets the same points from pmin as a `piecewise` cost curve);
 *  - over-generation penalty (Egret load_mismatch_cost is two-sided;
 *    gtopt's demand_fail_cost only prices unserved load — never active
 *    here).
 *
 * Subsets (the Egret references were computed with Egret 0.6.2 + HiGHS,
 * mipgap 1e-9, on the SAME subset; script in the commit message):
 *  s1  commitment (min up/down, initial state, fixed commitment →
 *      must_run), no-load, single (hot) startup cost, PWL cost, the system
 *      `reserve_requirement` as a headroom reserve (shortfall 100 $/MW),
 *      ramps and startup/shutdown capacity neutralised (= p_max)
 *        Egret 481 614.21934
 *  s2  s1 + ramp_up/down + startup/shutdown capacity, NO reserve
 *        Egret 481 834.78119
 *  s3  s2 + reserve (the reserve does not bind once ramps are active)
 *        Egret 481 834.78119
 *  s4  s1 + lag-dependent startup cost (hot if offline < L h, else cold;
 *      gtopt hot_start_time = cold_start_time = L − 1)
 *        Egret 481 614.21934
 * Full case (all products): 487 389.24 — 1.2 % above s1; that gap is
 * the cost of the unrepresented products and of the quadratic → PWL change.
 *
 * MIP: an exact MIP solver (solver_test::first_mip_solver, never CLP);
 * skipped when none is loaded.  Tolerance relative 1e-6 (both solves at
 * mipgap ≤ 1e-9 / solver default).
 */

#include <array>
#include <cstddef>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

struct EgUnit
{
  std::string_view name;
  double pmin;
  double pmax;
  double ramp;  ///< ramp up = ramp down [MW/h]
  double su_sd;  ///< startup = shutdown capacity [MW]
  double min_up_down;  ///< [h]
  double init_status;  ///< > 0 hours on, < 0 hours off
  double init_p;
  double hot_cost;  ///< startup cost for lag < cold_lag
  double cold_lag;
  double cold_cost;
  double c0;
  double c1;
  double c2;
  bool fixed_on;
};

constexpr std::array<EgUnit, 7> kEgUnits {{
    {
        .name = "GEN1_0_t",
        .pmin = 150,
        .pmax = 455,
        .ramp = 225,
        .su_sd = 150,
        .min_up_down = 8,
        .init_status = 8,
        .init_p = 455,
        .hot_cost = 450,
        .cold_lag = 14,
        .cold_cost = 900,
        .c0 = 100,
        .c1 = 16.19,
        .c2 = 0.00048,
        .fixed_on = true,
    },
    {
        .name = "GEN1_1_t",
        .pmin = 150,
        .pmax = 455,
        .ramp = 225,
        .su_sd = 150,
        .min_up_down = 8,
        .init_status = 8,
        .init_p = 455,
        .hot_cost = 450,
        .cold_lag = 14,
        .cold_cost = 900,
        .c0 = 100,
        .c1 = 17.19,
        .c2 = 0.00048,
        .fixed_on = false,
    },
    {
        .name = "GEN2_0_t",
        .pmin = 150,
        .pmax = 455,
        .ramp = 225,
        .su_sd = 150,
        .min_up_down = 8,
        .init_status = 8,
        .init_p = 245,
        .hot_cost = 500,
        .cold_lag = 14,
        .cold_cost = 1000,
        .c0 = 90,
        .c1 = 17.26,
        .c2 = 0.00031,
        .fixed_on = false,
    },
    {
        .name = "GEN2_1_t",
        .pmin = 150,
        .pmax = 455,
        .ramp = 225,
        .su_sd = 150,
        .min_up_down = 8,
        .init_status = 8,
        .init_p = 245,
        .hot_cost = 500,
        .cold_lag = 14,
        .cold_cost = 1000,
        .c0 = 90,
        .c1 = 18.26,
        .c2 = 0.00031,
        .fixed_on = false,
    },
    {
        .name = "GEN5_0_t",
        .pmin = 25,
        .pmax = 162,
        .ramp = 60,
        .su_sd = 25,
        .min_up_down = 6,
        .init_status = -6,
        .init_p = 0,
        .hot_cost = 90,
        .cold_lag = 11,
        .cold_cost = 180,
        .c0 = 45,
        .c1 = 19.7,
        .c2 = 0.00398,
        .fixed_on = false,
    },
    {
        .name = "GEN6_0_t",
        .pmin = 20,
        .pmax = 80,
        .ramp = 60,
        .su_sd = 20,
        .min_up_down = 3,
        .init_status = -3,
        .init_p = 0,
        .hot_cost = 17,
        .cold_lag = 8,
        .cold_cost = 34,
        .c0 = 37,
        .c1 = 22.26,
        .c2 = 0.00712,
        .fixed_on = true,
    },
    {
        .name = "GEN6_1_t",
        .pmin = 20,
        .pmax = 80,
        .ramp = 60,
        .su_sd = 20,
        .min_up_down = 3,
        .init_status = -3,
        .init_p = 0,
        .hot_cost = 17,
        .cold_lag = 8,
        .cold_cost = 34,
        .c0 = 37,
        .c1 = 23.26,
        .c2 = 0.00712,
        .fixed_on = false,
    },
}};

constexpr std::array<double, 24> kEgLoad {
    1088.1318, 996.177,   950.1996,  919.548,   888.8964,  888.8964,
    919.548,   980.8512,  1118.7834, 1226.064,  1256.7156, 1272.0414,
    1256.7156, 1226.064,  1210.7382, 1210.7382, 1272.0414, 1394.6478,
    1379.322,  1348.6704, 1302.693,  1287.3672, 1210.7382, 1134.1092,
};

constexpr std::array<double, 24> kEgReserve {
    32.643954, 29.88531,  28.505988, 27.58644,  26.666892, 26.666892,
    27.58644,  29.425536, 33.563502, 36.78192,  37.701468, 38.161242,
    37.701468, 36.78192,  36.322146, 36.322146, 38.161242, 41.839434,
    41.37966,  40.460112, 39.08079,  38.621016, 36.322146, 34.023276,
};

constexpr int kEgSegments = 4;

struct EgSubset
{
  bool reserve {true};
  bool ramps {false};
  bool hot_cold {false};
};

[[nodiscard]] std::string eg_series(const std::array<double, 24>& v)
{
  std::string out = "[[";
  for (std::size_t t = 0; t < v.size(); ++t) {
    out += std::format("{}{}", t > 0 ? "," : "", v.at(t));
  }
  return out + "]]";
}

[[nodiscard]] std::string eg_case_json(const EgSubset& s)
{
  std::string blocks;
  for (int t = 1; t <= 24; ++t) {
    blocks +=
        std::format(R"({}{{"uid": {}, "duration": 1}})", t > 1 ? "," : "", t);
  }
  std::string gens;
  std::string ucs;
  std::string provs;
  int uid = 0;
  for (const auto& u : kEgUnits) {
    ++uid;
    const auto* const sep = uid > 1 ? "," : "";
    const auto cost = [&](double x)
    { return u.c0 + (u.c1 * x) + (u.c2 * x * x); };
    // Breakpoints: [0, pmin] one secant (c0 is the no-load cost), then
    // kEgSegments equal secants up to pmax — the same points Egret gets.
    std::string bps = std::format("{}", u.pmin);
    std::string slopes = std::format("{}", (cost(u.pmin) - u.c0) / u.pmin);
    for (int k = 0; k < kEgSegments; ++k) {
      const double x0 = u.pmin + ((u.pmax - u.pmin) * k / kEgSegments);
      const double x1 = u.pmin + ((u.pmax - u.pmin) * (k + 1) / kEgSegments);
      bps += std::format(",{}", x1);
      slopes += std::format(",{}", (cost(x1) - cost(x0)) / (x1 - x0));
    }
    gens += std::format(
        R"({}{{"uid": {}, "name": "{}", "bus": 1, "gcost": 0, "pmin": 0,
        "pmax": {}, "capacity": {}, "fuel": "unit",
        "pmax_segments": [{}], "heat_rate_segments": [{}]}})",
        sep,
        uid,
        u.name,
        u.pmax,
        u.pmax,
        bps,
        slopes);
    std::string start = s.hot_cold
        // Egret: hot iff offline < cold_lag.  gtopt: hot iff a shutdown
        // within hot_start_time h; hot = cold time leaves no warm window
        // (all five tier fields are required, else NO start cost at all).
        ? std::format(R"("hot_start_cost": {0}, "warm_start_cost": {1},
          "cold_start_cost": {1}, "hot_start_time": {2},
          "cold_start_time": {2})",
                      u.hot_cost,
                      u.cold_cost,
                      u.cold_lag - 1)
        : std::format(R"("startup_cost": {})", u.hot_cost);
    const double ramp = s.ramps ? u.ramp : u.pmax;
    const double su_sd = s.ramps ? u.su_sd : u.pmax;
    ucs += std::format(
        R"({}{{"uid": {}, "name": "uc_{}", "generator": "{}", "pmin": {},
        "noload_cost": {}, "min_up_time": {}, "min_down_time": {},
        "initial_status": {}, "initial_hours": {}, "initial_power": {},
        "relax": false, "must_run": {}, {}{}}})",
        sep,
        uid,
        u.name,
        u.name,
        u.pmin,
        u.c0,
        u.min_up_down,
        u.min_up_down,
        u.init_status > 0 ? 1.0 : 0.0,
        u.init_status > 0 ? u.init_status : -u.init_status,
        u.init_p,
        u.fixed_on ? "true" : "false",
        start,
        s.ramps ? std::format(R"(, "ramp_up": {0}, "ramp_down": {0},
          "startup_ramp": {1}, "shutdown_ramp": {1})",
                              ramp,
                              su_sd)
                : std::string {});
    provs += std::format(
        R"({}{{"uid": {}, "name": "r_{}", "generator": "{}",
        "reserve_zones": [1], "urmax": {}, "ur_provision_factor": 1,
        "urcost": 0}})",
        sep,
        uid,
        u.name,
        u.name,
        u.pmax);
  }
  const std::string reserve = s.reserve ? std::format(R"(,
    "reserve_zone_array": [{{"uid": 1, "name": "sys_reserve",
      "urreq": {}, "urcost": 100}}],
    "reserve_provision_array": [{}])",
                                                      eg_series(kEgReserve),
                                                      provs)
                                        : std::string {};
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1000,
      "scale_objective": 1.0, "continuous_phases": "none"}}}},
  "simulation": {{"block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 24,
                     "chronological": true}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "egret_tiny_uc_1",
    "bus_array": [{{"uid": 1, "name": "Bus1"}}],
    "demand_array": [{{"uid": 1, "name": "load", "bus": 1, "lmax": {},
                      "capacity": 5000}}],
    "fuel_array": [{{"uid": 1, "name": "unit", "price": 1.0}}],
    "generator_array": [{}],
    "commitment_array": [{}]{}
  }}
}})",
      blocks,
      eg_series(kEgLoad),
      gens,
      ucs,
      reserve);
}

[[nodiscard]] double eg_solve(const EgSubset& s, const std::string& solver)
{
  auto planning = parse_planning_json(eg_case_json(s));
  planning.options.lp_matrix_options.solver_name = solver;
  PlanningLP plp(std::move(planning));
  REQUIRE(plp.resolve().has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

}  // namespace

TEST_CASE("Egret tiny_uc_1 subsets match Egret's MIP optimum")  // NOLINT
{
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("SKIPPED: no MIP solver loaded (never CLP for unit commitment)");
    return;
  }
  CAPTURE(solver);
  constexpr double kTol = 1.0e-6;

  SUBCASE("s1: commitment + PWL cost + headroom reserve")
  {
    CHECK(eg_solve({}, solver) == doctest::Approx(481614.21934).epsilon(kTol));
  }
  SUBCASE("s2: + ramps and startup/shutdown capacity, no reserve")
  {
    CHECK(eg_solve({.reserve = false, .ramps = true}, solver)
          == doctest::Approx(481834.78119).epsilon(kTol));
  }
  SUBCASE("s3: + ramps with reserve")
  {
    CHECK(eg_solve({.ramps = true}, solver)
          == doctest::Approx(481834.78119).epsilon(kTol));
  }
}

TEST_CASE(  // NOLINT
    "Egret tiny_uc_1 s4: hot / cold startup cost tiers")
{
  // GEN6_0 (must-run, 3 h offline at t = 0, hot window 7 h) starts in the
  // FIRST block: the initial offline time makes it a hot start (17), not
  // cold (34).  Before the fix gtopt gave 481 631.22 vs Egret 481 614.22.
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("SKIPPED: no MIP solver loaded");
    return;
  }
  CHECK(eg_solve({.hot_cold = true}, solver)
        == doctest::Approx(481614.21934).epsilon(1.0e-6));
}

TEST_CASE(  // NOLINT
    "Commitment startup tiers: a first-block start after a short initial "
    "offline time is hot")
{
  // Regression: hot_periods counts in-horizon periods back from p, so it
  // is 0 at p = 0 and the old `p < hot_periods && initial_hours_offline <
  // hot_time` never let the initial offline time make the first start hot
  // (or warm).  The tier is now chosen from the in-horizon time before p
  // plus the initial offline hours.
  // Unit off 3 h at t = 0, must run from block 1, hot ≤ 7 h: 17, not 34.
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("SKIPPED: no MIP solver loaded");
    return;
  }
  auto planning = parse_planning_json(R"({
  "options": {"annual_discount_rate": 0.0,
    "model_options": {"use_single_bus": true, "demand_fail_cost": 1000,
      "scale_objective": 1.0, "continuous_phases": "none"}},
  "simulation": {"block_array": [{"uid": 1, "duration": 1},
                                 {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2,
                     "chronological": true}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "tiers",
    "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "lmax": 10,
                      "capacity": 10}],
    "generator_array": [{"uid": 1, "name": "g1", "bus": 1, "gcost": 0,
                         "capacity": 50}],
    "commitment_array": [{"uid": 1, "name": "uc_g1", "generator": "g1",
      "pmin": 5, "must_run": true, "initial_status": 0,
      "initial_hours": 3, "relax": false,
      "hot_start_cost": 17, "warm_start_cost": 34, "cold_start_cost": 34,
      "hot_start_time": 7, "cold_start_time": 7}]
  }
})");
  planning.options.lp_matrix_options.solver_name = solver;
  PlanningLP plp(std::move(planning));
  REQUIRE(plp.resolve().has_value());
  CHECK(plp.systems().front().front().linear_interface().get_obj_value()
        == doctest::Approx(17.0));
}
