// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_cpf_slopes_regression.cpp
 * @brief     Exogenous vs endogenous load term on a multi-bus case with the
 *            CEN CPF@10s plane sets of 2024, 2025 and 2026
 * @date      2026-11-23
 *
 * Plane sets (α, β [/GW], γ [/GVAs], δ), relation (B.2) of the CEN
 * minutas (operator data, not committed: `tools/fetch_cen_sscc_cpf.py`
 * downloads and extracts them; read via cen_sscc_data.hpp, the test
 * reports the missing file and returns without it):
 *   2024   8 planes  (minuta GM-DPRO Nº07-2024)
 *   2025  12 planes  (minuta GM-DPRO Nº02-2025)
 *   2026  12 planes  (minuta GM-DPRO Nº05-2026)
 * PLEXOS form REQ + β·Gen + γ·I − δ·MaxInj ≥ α  ⇒  gtopt plane
 * {alpha: α, demand_coef: −β/1000, inertia_coef: −γ/1000,
 *  contingency_coef: δ}.
 *
 * Network: N —(2 000 MW)— C —(6 000 MW)— S, DC Kirchhoff, lossless.
 *   N: solar (0 $/MWh; 1 800 / 4 000 / 0 / 0 MW available per hour);
 *   C: gas (60 $/MWh, 6 000 MW) and the variable load;
 *   S: must-run `sync` (6 000 MW; I = 24 GVAs given as `system_inertia`,
 *      inertia_term "exogenous"), must-run `big`
 *      (400 MW, the contingency C), coal (90 $/MWh), the 6 000 MW load and
 *      the only CPF provider (20 $/MW → μ = 20).
 * Hours (Gen = load, GW): 8.0 surplus (solar curtailed, λ = 0 everywhere),
 * 9.5 congested N–C (λ_N = 0, λ_C = λ_S = 60), 11.0 (60), 12.5 (90).
 *
 * Exogenous (`load_term: exogenous`, D = Σ lmax): no reserve term reaches
 * any bus row: λ(b, h) equals the energy-only price, never negative.
 * Endogenous (`endogenous_generation`, D = Σ gen): every MWh earns
 * β_k*·μ/1000 through the binding plane k*: λ(b, h) = λ_energy(b, h) −
 * β_k*·μ/1000, negative at the surplus/curtailed buses.  The (I, C) point
 * (24 GVAs, 400 MW) keeps a single binding plane per hour (margin > 3 MW
 * over the runner-up in every set).
 */

#include <array>
#include <cmath>
#include <cstddef>
#include <format>
#include <limits>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "cen_sscc_data.hpp"

using namespace gtopt;

namespace
{

using CsrPlane = test_cen::CenPlane;  // α, β [/GW], γ [/GVAs], δ

constexpr double kCsrInertiaGVAs = 24.0;
constexpr double kCsrContingency = 400.0;
constexpr double kCsrMu = 20.0;
constexpr std::array<double, 4> kCsrLoadC {2000.0, 3500.0, 5000.0, 6500.0};
constexpr double kCsrLoadS = 6000.0;
constexpr std::array<double, 4> kCsrSolar {1800.0, 4000.0, 0.0, 0.0};
/// Energy-only bus prices λ(bus N, C, S) per hour.
constexpr std::array<std::array<double, 3>, 4> kCsrLambdaEnergy {{
    {0.0, 0.0, 0.0},
    {0.0, 60.0, 60.0},
    {60.0, 60.0, 60.0},
    {90.0, 90.0, 90.0},
}};

[[nodiscard]] std::string csr_planes_json(std::span<const CsrPlane> set)
{
  std::string out = "[";
  for (const auto& [a, b, g, d] : set) {
    out +=
        std::format(R"({}{{"alpha": {}, "demand_coef": {}, "inertia_coef": {},)"
                    R"( "contingency_coef": {}}})",
                    out.size() > 1 ? "," : "",
                    a,
                    -b / 1000.0,
                    -g / 1000.0,
                    d);
  }
  return out + "]";
}

[[nodiscard]] std::string csr_case_json(std::span<const CsrPlane> set,
                                        std::string_view load_term)
{
  const auto row4 = [](const std::array<double, 4>& v)
  { return std::format("[[{}, {}, {}, {}]]", v[0], v[1], v[2], v[3]); };
  const std::string zones = load_term.empty() ? std::string {}
                                              : std::format(
                                                    R"(,
    "reserve_zone_array": [{{"uid": 1, "name": "cpf10s", "urcost": 5000,
      "urreq_planes": {}, "inertia_term": "exogenous",
      "system_inertia": {},
      "contingency_generators": ["big"], "demand_load_factor": 1.0,
      "load_term": "{}"}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 3000,
      "ur_provision_factor": 1, "urcost": {}}}])",
                                                    csr_planes_json(set),
                                                    kCsrInertiaGVAs * 1000.0,
                                                    load_term,
                                                    kCsrMu);
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": false, "use_kirchhoff": true,
      "demand_fail_cost": 1.0e5, "scale_objective": 1.0}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}, {{"uid": 2, "duration": 1}},
                    {{"uid": 3, "duration": 1}}, {{"uid": 4, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 4}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "cpf_slopes",
    "bus_array": [{{"uid": 1, "name": "N"}}, {{"uid": 2, "name": "C"}},
                  {{"uid": 3, "name": "S"}}],
    "line_array": [
      {{"uid": 1, "name": "nc", "bus_a": 1, "bus_b": 2, "reactance": 0.01,
        "tmax_ab": 2000, "tmax_ba": 2000, "capacity": 2000}},
      {{"uid": 2, "name": "cs", "bus_a": 2, "bus_b": 3, "reactance": 0.01,
        "tmax_ab": 6000, "tmax_ba": 6000, "capacity": 6000}}],
    "demand_array": [
      {{"uid": 1, "name": "dem_c", "bus": 2, "lmax": {}}},
      {{"uid": 2, "name": "dem_s", "bus": 3, "lmax": {}}}],
    "generator_array": [
      {{"uid": 1, "name": "solar", "bus": 1, "gcost": 0, "pmax": {},
        "capacity": 4000}},
      {{"uid": 2, "name": "gas", "bus": 2, "gcost": 60, "capacity": 6000}},
      {{"uid": 3, "name": "sync", "bus": 3, "gcost": 0, "pmin": 6000,
        "pmax": 6000, "capacity": 6000}},
      {{"uid": 4, "name": "big", "bus": 3, "gcost": 0, "pmin": 400,
        "pmax": 400, "capacity": 400}},
      {{"uid": 5, "name": "coal", "bus": 3, "gcost": 90, "capacity": 3000}},
      {{"uid": 6, "name": "res", "bus": 3, "gcost": 200,
        "capacity": 3000}}]{}
  }}
}})",
      row4(kCsrLoadC),
      row4({kCsrLoadS, kCsrLoadS, kCsrLoadS, kCsrLoadS}),
      row4(kCsrSolar),
      zones);
}

struct CsrSolved
{
  std::vector<std::array<double, 3>> lambda;  ///< per hour, buses N, C, S
  std::vector<double> mu;  ///< |provision-balance dual| per hour
};

[[nodiscard]] CsrSolved csr_solve(const std::string& json, bool has_zone)
{
  PlanningLP plp(parse_planning_json(json));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto duals = li.get_row_dual();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto& buses = sys.elements<BusLP>();
  REQUIRE(buses.size() == 3);
  CsrSolved out;
  for (const auto& block : stage.blocks()) {
    std::array<double, 3> l {};
    for (std::size_t b = 0; b < 3; ++b) {
      l.at(b) =
          duals[buses[b].balance_rows_at(scenario, stage).at(block.uid())];
    }
    out.lambda.push_back(l);
    if (has_zone) {
      const auto& rows =
          sys.elements<ReserveZoneLP>().front().urequirement_rows();
      const auto it = rows.find({scenario.uid(), stage.uid()});
      REQUIRE(it != rows.end());
      out.mu.push_back(std::abs(duals[it->second.at(block.uid())]));
    }
  }
  return out;
}

/// β [/GW] of the plane binding at hour @p h (unique by construction).
[[nodiscard]] double csr_binding_beta(std::span<const CsrPlane> set,
                                      std::size_t h)
{
  const double gen_gw = (kCsrLoadC.at(h) + kCsrLoadS) / 1000.0;
  double best = -std::numeric_limits<double>::infinity();
  double second = best;
  double beta = 0.0;
  for (const auto& [a, b, g, d] : set) {
    const double v =
        a - (b * gen_gw) - (g * kCsrInertiaGVAs) + (d * kCsrContingency);
    if (v > best) {
      second = best;
      best = v;
      beta = b;
    } else if (v > second) {
      second = v;
    }
  }
  REQUIRE(best - second > 1.0);  // a single binding plane
  return beta;
}

void csr_check_set(std::span<const CsrPlane> set)
{
  const auto energy = csr_solve(csr_case_json(set, ""), /*has_zone=*/false);
  for (std::size_t h = 0; h < 4; ++h) {
    for (std::size_t b = 0; b < 3; ++b) {
      CAPTURE(h);
      CAPTURE(b);
      REQUIRE(energy.lambda[h][b]
              == doctest::Approx(kCsrLambdaEnergy.at(h).at(b)));
    }
  }

  SUBCASE("exogenous: LMP = energy price, never negative")
  {
    const auto s =
        csr_solve(csr_case_json(set, "exogenous"), /*has_zone=*/true);
    for (std::size_t h = 0; h < 4; ++h) {
      CAPTURE(h);
      CHECK(s.mu[h] == doctest::Approx(kCsrMu));
      for (std::size_t b = 0; b < 3; ++b) {
        CAPTURE(b);
        CHECK(s.lambda[h][b] == doctest::Approx(energy.lambda[h][b]));
        CHECK(s.lambda[h][b] >= -1e-9);
      }
    }
  }

  SUBCASE("endogenous: LMP carries the -beta*mu/1000 credit")
  {
    const auto s = csr_solve(csr_case_json(set, "endogenous_generation"),
                             /*has_zone=*/true);
    for (std::size_t h = 0; h < 4; ++h) {
      CAPTURE(h);
      CHECK(s.mu[h] == doctest::Approx(kCsrMu));
      const double credit = csr_binding_beta(set, h) * kCsrMu / 1000.0;
      for (std::size_t b = 0; b < 3; ++b) {
        CAPTURE(b);
        CHECK(s.lambda[h][b] == doctest::Approx(energy.lambda[h][b] - credit));
      }
    }
    // The surplus hour prices every bus negative; the congested hour the
    // exporting (curtailed) bus.
    for (std::size_t b = 0; b < 3; ++b) {
      CHECK(s.lambda[0][b] < 0.0);
    }
    CHECK(s.lambda[1][0] < 0.0);
  }
}

}  // namespace

namespace
{
void csr_check_year(std::string_view year, std::size_t nplanes)
{
  const auto set = test_cen::cen_planes(year);
  if (!set) {
    MESSAGE("SKIPPED: CEN CPF@10s planes " << year << " not found; "
                                           << test_cen::kCenFetchHint);
    return;
  }
  REQUIRE(set->size() == nplanes);
  csr_check_set(*set);
}
}  // namespace

TEST_CASE("CPF slopes regression: 2024 set (8 planes)")  // NOLINT
{
  csr_check_year("2024", 8);
}

TEST_CASE("CPF slopes regression: 2025 set (12 planes)")  // NOLINT
{
  csr_check_year("2025", 12);
}

TEST_CASE("CPF slopes regression: 2026 set (12 planes)")  // NOLINT
{
  csr_check_year("2026", 12);
}
