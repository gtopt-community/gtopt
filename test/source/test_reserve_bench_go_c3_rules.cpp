// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_bench_go_c3_rules.cpp
 * @brief     ARPA-E GO Competition Challenge 3: zonal reserve requirement
 *            rules (σ·load, σ·largest producer) as ReserveZone planes
 *
 * GO C3 (ARPA-E Grid Optimization Competition Challenge 3, Sandbox 0
 * release C3S0.1_20230804, CC BY 4.0,
 * https://data.openei.org/files/5997/C3S0.1_20230804.zip) sets, per active
 * reserve zone n and interval t (formulation §4.6):
 *
 *   regulation up / down   p_req = σ_rgu / σ_rgd · Σ_{consumers in n} p_jt
 *   synchronized (SYN)     p_req = σ_syn  · max_{producers in n} p_jt
 *   non-synchronized       p_req = σ_nsyn · max_{producers in n} p_jt
 *
 * gtopt maps them onto requirement planes (no new element):
 *   σ·load            → `{"demand_coef": σ}`, `demand_load_factor: 1`
 *                        (D = Σ lmax, exogenous: the consumption is data)
 *   σ·largest producer → `{"contingency_coef": σ}`,
 *                        `contingency_generators` = the zone's producers
 *                        (C ≥ p_g, minimal C = max_g p_g).
 * Out of scope (not compared): the hierarchical substitution
 * (rgu ⊂ syn ⊂ nsyn), ramping reserves, the objective.
 *
 * Data: the 3-bus (C3S0N00003D1) and 14-bus (C3S0N00014D1, 2 zones)
 * day-ahead scenario_003, consumer / producer dispatch of the published
 * prior operating point (`scenario_003.json.pop_solution.json`, p_on ×
 * on_status × 100 MVA), pinned in gtopt.  Expected: the per-interval rule
 * values, and their maxima equal the official evaluator's log
 * (`prz_t_p_{rgu,rgd,scr,nsc}_req max`, `scenario_003.json.popsolution.log`).
 */

#include <algorithm>
#include <array>
#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::size_t kGoT = 18;
using GoSeries = std::array<double, kGoT>;

constexpr GoSeries kGoDur {
    0.25,
    0.25,
    0.25,
    0.25,
    0.25,
    0.25,
    0.25,
    0.25,
    0.5,
    0.5,
    0.5,
    0.5,
    0.5,
    0.5,
    0.5,
    0.5,
    1.0,
    1.0,
};

struct GoZone
{
  const char* name;
  double sigma_rgu;
  double sigma_rgd;
  double sigma_syn;
  double sigma_nsyn;
  GoSeries consumption;  ///< Σ consumer p [MW]
  std::vector<GoSeries> producers;  ///< producer p [MW]
};

// clang-format off
const std::vector<GoZone>& go_zones()
{
  static const std::vector<GoZone> zones {
{.name = "C3S0N00003D1/prz_0",
 .sigma_rgu = 0.07, .sigma_rgd = 0.07,
 .sigma_syn = 0.05, .sigma_nsyn = 0.05,
 .consumption = {27.4963393, 27.5, 27.5, 27.5, 27.4844587, 27.4844591, 27.4844584, 27.4844581, 27.130783, 27.130783, 26.4804172, 26.4804172, 24.7471995, 24.7471995, 22.91504, 22.91504, 21.5106182, 20.3624331,},
 .producers = {
   {3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,},
   {24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5, 24.5,},
 },},
{.name = "C3S0N00014D1/prz_0",
 .sigma_rgu = 0.03, .sigma_rgd = 0.03,
 .sigma_syn = 0.3, .sigma_nsyn = 0.7,
 .consumption = {52.7990002, 52.7990002, 52.7990002, 52.7990002, 52.7990002, 52.7990002, 52.7990002, 52.7990002, 52.7990002, 52.8813004, 53.2413004, 53.3313004, 53.6038004, 55.0431004, 56.4281002, 58.5, 60.48, 63.09,},
 .producers = {
   {10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10,},
   {34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994, 34.9999994,},
   {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,},
 },},
{.name = "C3S0N00014D1/prz_1",
 .sigma_rgu = 0.03, .sigma_rgd = 0.03,
 .sigma_syn = 0.3, .sigma_nsyn = 0.7,
 .consumption = {33.5479002, 33.5479002, 33.5479002, 33.5479002, 33.5479002, 33.5479002, 33.5479002, 33.5479002, 33.5479002, 33.6889001, 33.9099, 34.0899, 34.2289001, 34.7199, 35.4880001, 36.54, 38.07, 40.5,},
 .producers = {
   {42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014, 42.2500014,},
   {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,},
   {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,},
 },},
  };
  return zones;
}
// clang-format on

[[nodiscard]] std::string go_series(const GoSeries& v, double add = 0.0)
{
  std::string s = "[[";
  for (std::size_t t = 0; t < v.size(); ++t) {
    s += std::format("{}{}", t > 0 ? "," : "", v[t] + add);
  }
  return s + "]]";
}

/// One single-bus gtopt case per C3 zone: consumption as a demand,
/// producers pinned at their prior-operating-point output (50 MW of
/// headroom above), four zones (rgu, rgd, syn, nsyn), each with its own
/// cheap provisions so that the requirement column sits on its plane.
[[nodiscard]] std::string go_case_json(const GoZone& z)
{
  std::string blocks;
  for (std::size_t t = 0; t < kGoT; ++t) {
    blocks += std::format(R"({}{{"uid": {}, "duration": {}}})",
                          t > 0 ? "," : "",
                          t + 1,
                          kGoDur[t]);
  }
  std::string gens;
  std::string names;
  for (std::size_t g = 0; g < z.producers.size(); ++g) {
    const auto* const sep = g > 0 ? "," : "";
    gens += std::format(
        R"({}{{"uid": {}, "name": "p{}", "bus": 1, "gcost": 10,
        "pmin": {}, "pmax": {}, "capacity": 1000}})",
        sep,
        g + 1,
        g + 1,
        go_series(z.producers[g]),
        go_series(z.producers[g], 50.0));
    names += std::format(R"({}"p{}")", sep, g + 1);
  }
  std::string provs;
  int puid = 0;
  for (int zone = 1; zone <= 4; ++zone) {
    const bool down = zone == 2;
    for (std::size_t g = 0; g < z.producers.size(); ++g) {
      provs += std::format(
          R"({}{{"uid": {}, "name": "r{}_{}", "generator": "p{}",
          "reserve_zones": [{}], "{}max": 100, "{}_provision_factor": 1,
          "{}cost": 0.01}})",
          puid > 0 ? "," : "",
          puid + 1,
          zone,
          g + 1,
          g + 1,
          zone,
          down ? "dr" : "ur",
          down ? "dr" : "ur",
          down ? "dr" : "ur");
      ++puid;
    }
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1000,
      "scale_objective": 1.0, "allow_oversupply": true}}}},
  "simulation": {{"block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {}}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "go_c3_rules",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "load", "bus": 1, "lmax": {}}}],
    "generator_array": [{}],
    "reserve_zone_array": [
      {{"uid": 1, "name": "rgu", "urcost": 1000, "demand_load_factor": 1.0,
        "urreq_planes": [{{"alpha": 0, "demand_coef": {}}}]}},
      {{"uid": 2, "name": "rgd", "drcost": 1000, "demand_load_factor": 1.0,
        "drreq_planes": [{{"alpha": 0, "demand_coef": {}}}]}},
      {{"uid": 3, "name": "syn", "urcost": 1000,
        "urreq_planes": [{{"alpha": 0, "contingency_coef": {}}}],
        "contingency_generators": [{}]}},
      {{"uid": 4, "name": "nsyn", "urcost": 1000,
        "urreq_planes": [{{"alpha": 0, "contingency_coef": {}}}],
        "contingency_generators": [{}]}}],
    "reserve_provision_array": [{}]
  }}
}})",
      blocks,
      kGoT,
      go_series(z.consumption),
      gens,
      z.sigma_rgu,
      z.sigma_rgd,
      z.sigma_syn,
      names,
      z.sigma_nsyn,
      names,
      provs);
}

/// Requirement column R per (zone 1..4, block) of the solved case.
[[nodiscard]] std::array<GoSeries, 4> go_solve(const GoZone& z)
{
  PlanningLP plp(parse_planning_json(go_case_json(z)));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  const auto sol = sys.linear_interface().get_col_sol();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto& zones = sys.elements<ReserveZoneLP>();
  REQUIRE(zones.size() == 4);
  std::array<GoSeries, 4> out {};
  for (std::size_t k = 0; k < 4; ++k) {
    for (std::size_t t = 0; t < kGoT; ++t) {
      const auto buid = stage.blocks()[t].uid();
      const auto col = k == 1
          ? zones[k].lookup_drequirement_col(scenario, stage, buid)
          : zones[k].lookup_urequirement_col(scenario, stage, buid);
      REQUIRE(col.has_value());
      out[k][t] = sol[col.value_or(ColIndex {0})];
    }
  }
  return out;
}

[[nodiscard]] double go_max_producer(const GoZone& z, std::size_t t)
{
  double m = 0.0;
  for (const auto& p : z.producers) {
    m = std::max(m, p[t]);
  }
  return m;
}

}  // namespace

TEST_CASE("GO C3 zonal reserve rules as ReserveZone planes")  // NOLINT
{
  // Evaluator maxima [MW] (log value × 100 MVA): 3-bus, and 14-bus over
  // its two zones.
  constexpr double kN3Rgu = 1.9249999987851964;
  constexpr double kN3Scr = 1.2250000000070364;
  constexpr double kN14Rgu = 1.8927;
  constexpr double kN14Scr = 12.675000430561360;
  constexpr double kN14Nsc = 29.575001004643170;

  std::array<double, 4> n14_max {};
  for (const auto& z : go_zones()) {
    CAPTURE(z.name);
    const auto req = go_solve(z);
    std::array<double, 4> zmax {};
    for (std::size_t t = 0; t < kGoT; ++t) {
      CAPTURE(t);
      const double load = z.consumption[t];
      const double largest = go_max_producer(z, t);
      CHECK(req[0][t] == doctest::Approx(z.sigma_rgu * load));
      CHECK(req[1][t] == doctest::Approx(z.sigma_rgd * load));
      CHECK(req[2][t] == doctest::Approx(z.sigma_syn * largest));
      CHECK(req[3][t] == doctest::Approx(z.sigma_nsyn * largest));
      for (std::size_t k = 0; k < 4; ++k) {
        zmax[k] = std::max(zmax[k], req[k][t]);
      }
    }
    if (std::string {z.name}.starts_with("C3S0N00003")) {
      CHECK(zmax[0] == doctest::Approx(kN3Rgu).epsilon(1e-8));
      CHECK(zmax[1] == doctest::Approx(kN3Rgu).epsilon(1e-8));
      CHECK(zmax[2] == doctest::Approx(kN3Scr).epsilon(1e-8));
      CHECK(zmax[3] == doctest::Approx(kN3Scr).epsilon(1e-8));
    } else {
      for (std::size_t k = 0; k < 4; ++k) {
        n14_max[k] = std::max(n14_max[k], zmax[k]);
      }
    }
  }
  CHECK(n14_max[0] == doctest::Approx(kN14Rgu).epsilon(1e-8));
  CHECK(n14_max[1] == doctest::Approx(kN14Rgu).epsilon(1e-8));
  CHECK(n14_max[2] == doctest::Approx(kN14Scr).epsilon(1e-8));
  CHECK(n14_max[3] == doctest::Approx(kN14Nsc).epsilon(1e-8));
}
