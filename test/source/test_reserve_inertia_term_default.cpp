// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Default CPF plane inertia term (`ReserveZone.inertia_term`) and its known
// risk.
//
// DECISION (user, 2026-11-23): the default is the CEN form `"commitment"`
// (minuta Anexo B.1, I = Σ I_c·u_c, I_c = pf·min(provision_max, pmax)).
// Evidence, CEN PCP 2026-11-07 (relaxed Commitments): commitment vs
// exogenous gives 515 vs 509 negative bus-hours, none below −0.1 $/MWh, and
// a realistic requirement — the must-run-only `"exogenous"` fallback
// inflates it to 505 MW against 195 MW in PLEXOS; a decomposition run put
// the whole negative-price credit on the load term, not on the inertia.
//
// KNOWN RISK, documented by this 3-bus, 24-hour case (must-take solar in
// the north, a cheap north hydro with a Commitment behind a congested
// line, committed coal / CCGT, a CEN-shaped CPF plane), 72 bus-hours:
//
//   relaxed commitment     negative   < -0.1   min LMP   mean south LMP
//     commitment                  6        6    -7.05          42.77
//     exogenous                   0        0     0.50          45.66
//     endogenous                 10       10    -2.76          43.08
//   integer commitment
//     commitment                  0        0     0.50          41.69
//     exogenous                   0        0     0.50          53.23
//     endogenous                 25       25    -1.90          40.99
//
// With a RELAXED Commitment u is an LP variable bounded by pmin·u <= p, so
// the hydro raises u to p/pmin and earns I_c/pmin = 20 MWs per MW of
// inertia credit (more than its pf = 8 under "endogenous"): its bus clears
// at -7.05 $/MWh.  validate_planning reports that coupling channel and
// names `inertia_term: "exogenous"` as the remedy; with integer commitment
// (duals of the fixed-u LP) the commitment form is clean.

#include <algorithm>
#include <array>
#include <cmath>
#include <format>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

struct LmpStats
{
  int negative_hours {0};  ///< bus-hours with λ < −1e-6
  int below_tenth {0};  ///< bus-hours with λ < −0.1 $/MWh
  double min_lmp {0.0};
  double mean_ref {0.0};  ///< mean λ at the reference (south) bus
};

[[nodiscard]] std::string itd_json(std::string_view inertia_term, bool relax)
{
  // Solar profile (MW) and load (MW) over 24 h.
  constexpr std::array<double, 24> solar {
      0,   0,   0,   0,   0,   20,  120, 260, 380, 460, 500, 520,
      520, 500, 460, 380, 260, 120, 20,  0,   0,   0,   0,   0,
  };
  constexpr std::array<double, 24> load {
      300, 290, 285, 285, 290, 310, 330, 350, 360, 370, 375, 380,
      380, 378, 375, 372, 380, 410, 440, 450, 430, 400, 360, 320,
  };
  std::string blocks;
  std::string solar_pmax;
  std::string solar_pmin;
  std::string lmax;
  for (std::size_t h = 0; h < 24; ++h) {
    const auto* sep = h == 0 ? "" : ", ";
    blocks += std::format(R"({}{{"uid": {}, "duration": 1}})", sep, h + 1);
    solar_pmax += std::format("{}{}", sep, 0.44 * solar.at(h));
    // Must-take solar (PMGD-like, not curtailable): pmin = pmax.
    solar_pmin += std::format("{}{}", sep, 0.44 * solar.at(h));
    lmax += std::format("{}{}", sep, load.at(h));
  }
  return std::format(
      R"({{
  "options": {{
    "annual_discount_rate": 0.0,
    "model_options": {{ "use_single_bus": false, "use_kirchhoff": true,
                        "demand_fail_cost": 5000, "scale_objective": 1 }}
  }},
  "simulation": {{
    "block_array": [{0}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 24,
                      "chronological": true}}],
    "scenario_array": [{{"uid": 1}}]
  }},
  "system": {{
    "name": "inertia_default",
    "bus_array": [{{"uid": 1, "name": "north"}}, {{"uid": 2, "name": "center"}},
                  {{"uid": 3, "name": "south"}}],
    "line_array": [
      {{"uid": 1, "name": "nc", "bus_a": 1, "bus_b": 2, "reactance": 0.05,
        "tmax_ab": 250, "tmax_ba": 250, "capacity": 250}},
      {{"uid": 2, "name": "cs", "bus_a": 2, "bus_b": 3, "reactance": 0.05,
        "tmax_ab": 400, "tmax_ba": 400, "capacity": 400}}],
    "demand_array": [{{"uid": 1, "name": "d_c", "bus": 2, "capacity": 500,
                       "lmax": [[{3}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "solar", "bus": 1, "gcost": 0, "capacity": 600,
        "pmax": [[{1}]], "pmin": [[{2}]]}},
      {{"uid": 2, "name": "coal", "bus": 3, "gcost": 30, "capacity": 200}},
      {{"uid": 3, "name": "ccgt", "bus": 3, "gcost": 60, "capacity": 250}},
      {{"uid": 4, "name": "ocgt", "bus": 2, "gcost": 120, "capacity": 150}},
      {{"uid": 5, "name": "hydro_n", "bus": 1, "gcost": 0.5,
        "capacity": 150}}],
    "commitment_array": [
      {{"uid": 1, "name": "uc_coal", "generator": "coal", "pmin": 100,
        "noload_cost": 200, "initial_status": 1, "relax": {4}}},
      {{"uid": 2, "name": "uc_ccgt", "generator": "ccgt", "pmin": 90,
        "noload_cost": 400, "initial_status": 1, "relax": {4}}},
      {{"uid": 3, "name": "uc_hydro_n", "generator": "hydro_n", "pmin": 60,
        "initial_status": 1, "relax": {4}}}],
    "inertia_zone_array": [{{"uid": 1, "name": "sen"}}],
    "inertia_provision_array": [
      {{"uid": 1, "name": "ip_coal", "generator": "coal",
        "inertia_zones": ["sen"], "provision_factor": 12,
        "provision_max": 100}},
      {{"uid": 2, "name": "ip_ccgt", "generator": "ccgt",
        "inertia_zones": ["sen"], "provision_factor": 15,
        "provision_max": 90}},
      {{"uid": 3, "name": "ip_hydro_n", "generator": "hydro_n",
        "inertia_zones": ["sen"], "provision_factor": 8,
        "provision_max": 150}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf", "urcost": 3000,
      "urreq_planes": [{{"alpha": 120, "demand_coef": -0.05,
                         "inertia_coef": -0.02, "contingency_coef": 0.5}}],
      "contingency_generators": ["coal", "ccgt"],
      "inertia_zone": "sen"{5}}}],
    "reserve_provision_array": [
      {{"uid": 1, "name": "rp_ccgt", "generator": "ccgt",
        "reserve_zones": [1], "urmax": 80, "ur_provision_factor": 1,
        "urcost": 8}},
      {{"uid": 2, "name": "rp_ocgt", "generator": "ocgt",
        "reserve_zones": [1], "urmax": 100, "ur_provision_factor": 1,
        "urcost": 15}}]
  }}
}})",
      blocks,
      solar_pmax,
      solar_pmin,
      lmax,
      relax,
      inertia_term.empty()
          ? std::string {}
          : std::format(R"(, "inertia_term": "{}")", inertia_term));
}

[[nodiscard]] LmpStats itd_stats(std::string_view inertia_term, bool relax)
{
  PlanningLP plp(solver_test::with_mip_solver(
      parse_planning_json(itd_json(inertia_term, relax))));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto duals = li.get_row_dual();
  LmpStats st;
  st.min_lmp = 1e30;
  double ref_sum = 0.0;
  int ref_n = 0;
  for (const auto& bus : sys.elements<BusLP>()) {
    const auto& rows = bus.balance_rows_at(scenario, stage);
    for (const auto& block : stage.blocks()) {
      const double lmp = duals[rows.at(block.uid())];
      st.min_lmp = std::min(st.min_lmp, lmp);
      if (lmp < -1e-6) {
        ++st.negative_hours;
      }
      if (lmp < -0.1) {
        ++st.below_tenth;
      }
      if (bus.object().name == "south") {
        ref_sum += lmp;
        ++ref_n;
      }
    }
  }
  st.mean_ref = ref_n > 0 ? ref_sum / ref_n : 0.0;
  MESSAGE(std::format(
      "inertia_term={} relax={}: negative bus-hours={} (< -0.1: {}), "
      "min LMP={:.4f}, mean south LMP={:.4f}",
      inertia_term,
      relax,
      st.negative_hours,
      st.below_tenth,
      st.min_lmp,
      st.mean_ref));
  return st;
}

}  // namespace

TEST_CASE("CPF inertia term default: commitment, and its relaxed-UC risk")
// NOLINT
{
  for (const bool relax : {true, false}) {
    CAPTURE(relax);
    const auto com = itd_stats("commitment", relax);
    const auto exo = itd_stats("exogenous", relax);
    const auto endo = itd_stats("endogenous", relax);
    const auto dflt = itd_stats("", relax);
    // The default is the CEN commitment form.
    CHECK(dflt.negative_hours == com.negative_hours);
    CHECK(dflt.mean_ref == doctest::Approx(com.mean_ref));
    // The exogenous data term (the remedy) never prices a bus negative.
    CHECK(exo.negative_hours == 0);
    CHECK(exo.min_lmp >= -1e-6);
    // The dispatch-based PLEXOS form does.
    CHECK(endo.negative_hours > 0);
    if (relax) {
      // Known risk: relaxed commitment lets the commitment form price the
      // congested hydro bus negative.
      CHECK(com.negative_hours > exo.negative_hours);
      CHECK(com.min_lmp < -0.1);
    } else {
      // Integer commitment: the duals come from the fixed-u LP.
      CHECK(com.negative_hours == 0);
    }
  }
}

TEST_CASE("CPF inertia term default: validator flags the relaxed-UC risk")
// NOLINT
{
  const auto warnings = [](std::string_view term, bool relax)
  {
    auto planning = parse_planning_json(itd_json(term, relax));
    return validate_planning(planning).warnings;
  };
  const auto flagged = [](const std::vector<std::string>& w)
  {
    return std::ranges::any_of(w,
                               [](const std::string& m)
                               {
                                 return m.contains("inertia_term 'commitment'")
                                     && m.contains("relaxed")
                                     && m.contains("\"exogenous\"");
                               });
  };
  CHECK(flagged(warnings("", /*relax=*/true)));  // the default
  CHECK(flagged(warnings("commitment", /*relax=*/true)));
  CHECK_FALSE(flagged(warnings("commitment", /*relax=*/false)));
  CHECK_FALSE(flagged(warnings("exogenous", /*relax=*/true)));
}
