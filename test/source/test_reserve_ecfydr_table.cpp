// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_reserve_ecfydr_table.cpp
 * @brief     The 12-plane 2026 CPF@10s requirement against CEN's ECFyDR
 *            reference tables
 * @date      2026-11-23
 *
 * Data (not committed; operator data is linked, not embedded): run
 * `python tools/fetch_cen_sscc_cpf.py`, which downloads and extracts
 *  - the (B.2) planes of the Minuta GM-DPRO Nº05-2026 (CPF@10s, 12 planes),
 *  - Tablas 5-3..5-5 of the Informe de Servicios Complementarios 2026
 *    ("Reservas requeridas CPF+ para CPF@10s", PotDesc 400 / 350 / 300 MW;
 *    inertia 20..60 GVAs × Gen 7.5..12.5 GW = 297 points),
 * into the cache read by cen_sscc_data.hpp.  Without it the test reports
 * the missing file and returns.
 *
 * Fit statistics documented for these planes: the max of the 12 planes
 * OVERestimates the tables by 10–12 MW on average (max 17) and never
 * UNDERestimates by more than 5 MW.
 *
 * gtopt side: ONE LP, one block per grid point.  Gen is the exogenous
 * `system_load` schedule, I the `system_inertia` schedule (inertia_term
 * "exogenous", I [GVAs] · 1 000 MWs), PotDesc a must-run contingency
 * unit, and a free provider makes R bind at max(0, max_k plane_k).  The
 * test checks R against the arithmetic max of the planes (exact) and then
 * the documented tolerance band against the published table.
 */

#include <algorithm>
#include <array>
#include <cstddef>
#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "cen_sscc_data.hpp"

using namespace gtopt;
using namespace gtopt::test_cen;

namespace
{

constexpr std::array<double, 3> kEcfPotDesc {400.0, 350.0, 300.0};

[[nodiscard]] std::size_t ecf_table_idx(double pot_desc)
{
  for (std::size_t t = 0; t < kEcfPotDesc.size(); ++t) {
    if (kEcfPotDesc.at(t) == pot_desc) {
      return t;
    }
  }
  FAIL("unexpected PotDesc " << pot_desc);
  return 0;
}

[[nodiscard]] double ecf_max_planes(const std::vector<CenPlane>& planes,
                                    const CenEcfPoint& p)
{
  double r = 0.0;  // the requirement is floored at 0
  for (const auto& [a, b, g, d] : planes) {
    r = std::max(r,
                 a - (b * p.gen_mw / 1000.0) - (g * p.inertia_gvas)
                     + (d * p.pot_desc_mw));
  }
  return r;
}

[[nodiscard]] std::string ecf_case_json(const std::vector<CenPlane>& cplanes,
                                        const std::vector<CenEcfPoint>& pts)
{
  std::string blocks;
  std::string load;
  std::string inertia;
  std::string big;
  std::string dem;
  for (std::size_t i = 0; i < pts.size(); ++i) {
    const auto* const sep = i > 0 ? "," : "";
    const auto& p = pts[i];
    blocks += std::format(R"({}{{"uid": {}, "duration": 1}})", sep, i + 1);
    load += std::format("{}{}", sep, p.gen_mw);
    inertia += std::format("{}{}", sep, p.inertia_gvas * 1000.0);
    big += std::format("{}{}", sep, p.pot_desc_mw);
    dem += std::format("{}{}", sep, p.pot_desc_mw);
  }
  std::string planes = "[";
  for (const auto& [a, b, g, d] : cplanes) {
    planes +=
        std::format(R"({}{{"alpha": {}, "demand_coef": {}, "inertia_coef": {},)"
                    R"( "contingency_coef": {}}})",
                    planes.size() > 1 ? "," : "",
                    a,
                    -b / 1000.0,
                    -g / 1000.0,
                    d);
  }
  planes += "]";
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": true, "demand_fail_cost": 1.0e5,
                      "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{0}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {1}}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "ecfydr",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "lmax": [[{2}]],
                      "capacity": 1000}}],
    "generator_array": [
      {{"uid": 2, "name": "big", "bus": 1, "gcost": 0, "pmin": [[{4}]],
        "pmax": [[{4}]], "capacity": 500}},
      {{"uid": 3, "name": "res", "bus": 1, "gcost": 0, "capacity": 2000}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf10s", "urcost": 5000,
      "urreq_planes": {5}, "inertia_term": "exogenous",
      "system_inertia": [[{3}]],
      "contingency_generators": ["big"], "load_term": "exogenous",
      "system_load": [[{6}]]}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 2000,
      "ur_provision_factor": 1, "urcost": 1}}]
  }}
}})",
      blocks,
      pts.size(),
      dem,
      inertia,
      big,
      planes,
      load);
}

}  // namespace

TEST_CASE("CPF@10s 2026 planes vs the ECFyDR tables 5-3..5-5")  // NOLINT
{
  const auto planes = cen_planes("2026");
  const auto pts = cen_ecfydr("2026");
  if (!planes || !pts) {
    MESSAGE("SKIPPED: CEN SSCC 2026 data not found; " << kCenFetchHint);
    return;
  }
  REQUIRE(planes->size() == 12);
  REQUIRE(pts->size() == 297);

  PlanningLP plp(parse_planning_json(ecf_case_json(*planes, *pts)));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  const auto sol = sys.linear_interface().get_col_sol();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto& zone = sys.elements<ReserveZoneLP>().front();
  REQUIRE(stage.blocks().size() == pts->size());

  std::array<double, 3> sum {};
  std::array<double, 3> lo {1e30, 1e30, 1e30};
  std::array<double, 3> hi {-1e30, -1e30, -1e30};
  std::array<int, 3> n {};
  for (std::size_t i = 0; i < pts->size(); ++i) {
    const auto& p = (*pts)[i];
    const auto t = ecf_table_idx(p.pot_desc_mw);
    const auto col =
        zone.lookup_urequirement_col(scenario, stage, stage.blocks()[i].uid());
    REQUIRE(col.has_value());
    const double r = sol[col.value_or(ColIndex {0})];
    CAPTURE(i);
    CAPTURE(p.gen_mw);
    CAPTURE(p.inertia_gvas);
    CAPTURE(p.pot_desc_mw);
    // The LP requirement is exactly the max of the planes.
    CHECK(r == doctest::Approx(ecf_max_planes(*planes, p)).epsilon(1e-9));
    const double err = r - p.req_mw;
    sum.at(t) += err;
    lo.at(t) = std::min(lo.at(t), err);
    hi.at(t) = std::max(hi.at(t), err);
    ++n.at(t);
  }

  for (std::size_t t = 0; t < 3; ++t) {
    const double mean = sum.at(t) / n.at(t);
    CAPTURE(kEcfPotDesc.at(t));
    CAPTURE(mean);
    CAPTURE(lo.at(t));
    CAPTURE(hi.at(t));
    CHECK(n.at(t) == 99);
    // Documented: overestimate 10–12 MW mean (measured 10.4 / 11.8 /
    // 10.1), at most 17 MW, never underestimate by more than 5 MW.
    CHECK(mean >= 10.0);
    CHECK(mean <= 12.0);
    CHECK(hi.at(t) <= 17.0);
    CHECK(lo.at(t) >= -5.0);
  }
}
