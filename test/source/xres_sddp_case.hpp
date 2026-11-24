// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      xres_sddp_case.hpp
 * @brief     Hydro-thermal case with the extended reserve model, for the
 *            SDDP / cascade / global-DecisionVariable tests
 * @date      2026-11-23
 *
 * One bus, 3 stages × 4 one-hour blocks, probability 1, no discounting,
 * `scale_objective = 1`.  Built either as ONE phase (the monolithic
 * reference: one LP over the whole horizon) or as THREE phases (one stage
 * each — SDDP / cascade).  The same JSON is used for both, only the
 * `phase_array` differs, so the one-phase LP optimum is the value SDDP must
 * converge to.
 *
 * Elements:
 *  - hydro: reservoir `rsv` (eini 200, emax 400, inflow 15 /h) feeding
 *    `hydro` (0 $/MWh, 120 MW) — the cross-phase state SDDP must price;
 *  - thermal: `tha` (40 $/MWh), `thb` (60 $/MWh), both with a relaxed
 *    SimpleCommitment (dispatch_pmin 20), and `peaker` (150 $/MWh);
 *  - BESS `bess` (40/40 MW, 100 MWh, eini 50, η 0.95, deployment energy
 *    0.1 MWh/MW/h) — a second cross-phase state;
 *  - InertiaZone `sen`: 6 000 MWs, relieved by a 2 000 MWs credit once both
 *    `tha` and `thb` are committed (continuous selector, PLEXOS form);
 *    providers `hydro` (50 MWs/MW) and `tha` (80 MWs/MW);
 *  - ReserveZone `cpf` (up, dynamic): R ≥ max(40 + 0.1·D,
 *    80 + 0.05·D − 0.002·I, C) with D exogenous (factor 1), I the `sen`
 *    inertia and C ≥ p_thb (contingency candidate);
 *  - ReserveZone `cpf_dn` (down, fixed 20 MW);
 *  - providers: `rp_tha` (up 60 / down 40 MW at 3 / 2 $/MW), `rp_peaker`
 *    (up 200 MW at 8 $/MW) and `rp_bess` — the BESS offering BOTH sides
 *    with per-side response caps (up: 15 discharge + 10 charge, down: 10
 *    discharge + 15 charge) → split columns + split rows.
 *
 * Options (XresCase):
 *  - `phases`: 3-phase (SDDP) or 1-phase (monolithic reference);
 *  - `with_dv`: a `scope: "global"` DecisionVariable `contract` caps the
 *    peaker's up provision in EVERY block through a block-level
 *    UserConstraint (one value for the whole horizon, 2 $/MW charged once);
 *  - `with_ctf`: an extra zone `ctf` (fixed 150 MW up, NO urcost, so its
 *    shortage price is `model_options.reserve_shortage_cost`) served only
 *    by `rp_tha_ctf` (≤ 50 MW): a permanent 100 MW shortage per block, i.e.
 *    the objective moves by 1 200 × Δ(reserve_shortage_cost);
 *  - `model_options_extra`: extra `model_options` keys (leading comma);
 *  - `inertia_term` of `cpf` (default "endogenous", the PLEXOS-compat
 *    dispatch inertia these tests were written for) and `cpf_extra`, extra
 *    `cpf` keys (leading comma, e.g. a `system_inertia` schedule).
 */

#pragma once

#include <array>
#include <cmath>
#include <cstddef>
#include <format>
#include <map>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/decision_variable_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

namespace gtopt::test_xres
{

struct XresCase
{
  bool phases {true};
  bool with_dv {false};
  bool with_ctf {false};
  std::string model_options_extra {};
  std::string options_extra {};  ///< extra top-level `options` keys
  std::string inertia_term {"endogenous"};  ///< `cpf` inertia_term
  std::string cpf_extra {};  ///< extra `cpf` zone keys (leading comma)
};

/// Demand per stage × block [MW] (4 blocks per stage).
inline constexpr std::array<std::array<double, 4>, 3> kXresLoad {{
    {120.0, 160.0, 200.0, 150.0},
    {130.0, 170.0, 210.0, 160.0},
    {110.0, 150.0, 190.0, 140.0},
}};

[[nodiscard]] inline std::string xres_case_json(const XresCase& c)
{
  std::string blocks;
  for (int b = 1; b <= 12; ++b) {
    blocks +=
        std::format(R"({}{{"uid": {}, "duration": 1}})", b > 1 ? "," : "", b);
  }
  std::string lmax = "[";
  for (std::size_t s = 0; s < kXresLoad.size(); ++s) {
    const auto& l = kXresLoad.at(s);
    lmax += std::format(
        "{}[{}, {}, {}, {}]", s > 0 ? "," : "", l[0], l[1], l[2], l[3]);
  }
  lmax += "]";

  const std::string phases = c.phases ? R"(, "phase_array": [
        {"uid": 1, "first_stage": 0, "count_stage": 1},
        {"uid": 2, "first_stage": 1, "count_stage": 1},
        {"uid": 3, "first_stage": 2, "count_stage": 1}])"
                                      : "";

  const std::string ctf_zone =
      c.with_ctf ? R"(, {"uid": 3, "name": "ctf", "urreq": 150})" : "";
  const std::string ctf_prov = c.with_ctf
      ? R"(, {"uid": 4, "name": "rp_tha_ctf", "generator": "tha",
             "reserve_zones": [3], "urmax": 50, "ur_provision_factor": 1,
             "urcost": 1})"
      : "";

  const std::string dv = c.with_dv ? R"(, "decision_variable_array": [
        {"uid": 1, "name": "contract", "lower_bound": 0,
         "upper_bound": 200, "cost": 2.0, "scope": "global"}],
      "user_constraint_array": [
        {"uid": 1, "name": "peaker_contract",
         "expression":
           "reserve_provision('rp_peaker').up <= decision_variable('contract').value"}])"
                                   : "";

  return std::format(
      R"({{
  "options": {{
    "annual_discount_rate": 0.0,
    "output_format": "csv",
    "output_compression": "uncompressed"{7},
    "model_options": {{
      "use_single_bus": true,
      "demand_fail_cost": 1.0e4,
      "scale_objective": 1.0{0}
    }}
  }},
  "simulation": {{
    "block_array": [{1}],
    "stage_array": [
      {{"uid": 1, "first_block": 0, "count_block": 4}},
      {{"uid": 2, "first_block": 4, "count_block": 4}},
      {{"uid": 3, "first_block": 8, "count_block": 4}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1.0}}]{2}
  }},
  "system": {{
    "name": "xres_sddp",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "lmax": {3}}}],
    "generator_array": [
      {{"uid": 1, "name": "hydro", "bus": 1, "gcost": 0, "capacity": 120}},
      {{"uid": 2, "name": "tha", "bus": 1, "gcost": 40, "capacity": 150}},
      {{"uid": 3, "name": "thb", "bus": 1, "gcost": 60, "capacity": 150}},
      {{"uid": 4, "name": "peaker", "bus": 1, "gcost": 150,
        "capacity": 200}}],
    "simple_commitment_array": [
      {{"uid": 1, "name": "sc_tha", "generator": "tha",
        "dispatch_pmin": 20, "relax": true}},
      {{"uid": 2, "name": "sc_thb", "generator": "thb",
        "dispatch_pmin": 20, "relax": true}}],
    "junction_array": [
      {{"uid": 1, "name": "j_up"}},
      {{"uid": 2, "name": "j_down", "drain": true}}],
    "waterway_array": [
      {{"uid": 1, "name": "ww1", "junction_a": 1, "junction_b": 2,
        "fmin": 0, "fmax": 200}}],
    "reservoir_array": [
      {{"uid": 1, "name": "rsv", "junction": 1, "capacity": 400,
        "emin": 0, "emax": 400, "eini": 200, "fmin": -1000, "fmax": 1000,
        "flow_conversion_rate": 1.0}}],
    "flow_array": [
      {{"uid": 1, "name": "inflow", "direction": 1, "junction": 1,
        "discharge": 15}}],
    "turbine_array": [
      {{"uid": 1, "name": "tur", "waterway": 1, "generator": 1,
        "production_factor": 1.0}}],
    "battery_array": [
      {{"uid": 1, "name": "bess", "bus": 1, "input_efficiency": 0.95,
        "output_efficiency": 0.95, "emin": 0, "emax": 100, "capacity": 100,
        "eini": 50, "pmax_charge": 40, "pmax_discharge": 40,
        "reserve_sustain_hours": 0.0, "reserve_energy_usage_up": 0.1,
        "reserve_energy_usage_down": 0.1,
        "use_state_variable": true, "daily_cycle": false}}],
    "inertia_zone_array": [
      {{"uid": 1, "name": "sen", "requirement": 6000, "cost": 50,
        "requirement_credits": [
          {{"name": "both_thermals", "generators": ["tha", "thb"],
            "units": 2, "credit": 2000, "integer": false}}]}}],
    "inertia_provision_array": [
      {{"uid": 1, "name": "ip_hydro", "generator": "hydro",
        "inertia_zones": ["sen"], "provision_factor": 50,
        "provision_max": 6000}},
      {{"uid": 2, "name": "ip_tha", "generator": "tha",
        "inertia_zones": ["sen"], "provision_factor": 80,
        "provision_max": 12000}}],
    "reserve_zone_array": [
      {{"uid": 1, "name": "cpf", "urcost": 5000,
        "urreq_planes": [
          {{"alpha": 40, "demand_coef": 0.1}},
          {{"alpha": 80, "demand_coef": 0.05, "inertia_coef": -0.002}},
          {{"contingency_coef": 1.0}}],
        "load_term": "exogenous", "demand_load_factor": 1.0,
        "inertia_zone": "sen", "inertia_term": "{8}",
        "contingency_generators": ["thb"]{9}}},
      {{"uid": 2, "name": "cpf_dn", "drreq": 20, "drcost": 5000}}{4}],
    "reserve_provision_array": [
      {{"uid": 1, "name": "rp_tha", "generator": "tha",
        "reserve_zones": [1, 2], "urmax": 60, "drmax": 40,
        "ur_provision_factor": 1, "dr_provision_factor": 1,
        "urcost": 3, "drcost": 2}},
      {{"uid": 2, "name": "rp_peaker", "generator": "peaker",
        "reserve_zones": [1], "urmax": 200, "ur_provision_factor": 1,
        "urcost": 8}},
      {{"uid": 3, "name": "rp_bess", "generator": "bess_gen",
        "reserve_zones": [1, 2], "urmax": 50, "drmax": 50,
        "ur_provision_factor": 1, "dr_provision_factor": 1,
        "urcost": 1, "drcost": 1,
        "urmax_response": 15, "urmax_charge_response": 10,
        "drmax_response": 10, "drmax_charge_response": 15}}{5}]{6}
  }}
}})",
      c.model_options_extra,
      blocks,
      phases,
      lmax,
      ctf_zone,
      ctf_prov,
      dv,
      c.options_extra,
      c.inertia_term,
      c.cpf_extra);
}

[[nodiscard]] inline Planning xres_planning(const XresCase& c)
{
  return parse_planning_json(xres_case_json(c));
}

/// Row duals and selected primals of every (stage, block) of scene 0,
/// gathered over all phase LPs (one for the monolithic reference, three
/// for SDDP).  Keys are (stage uid, block uid).
struct XresSnapshot
{
  using Key = std::pair<int, int>;
  std::map<Key, double> lambda;  ///< bus balance dual [$/MWh]
  std::map<Key, double> mu_up;  ///< |dual| of the cpf provision balance
  std::map<Key, double> mu_dn;  ///< |dual| of the cpf_dn provision balance
  std::map<Key, double> mu_inertia;  ///< |dual| of the sen requirement row
  std::map<Key, std::vector<double>> plane_duals;  ///< |dual| per cpf plane
  std::map<Key, double> ureq;  ///< cpf requirement column R
  std::vector<double> contract;  ///< `contract` DV value per phase LP
  double objective {0.0};  ///< Σ phase-LP objective values
};

namespace detail
{
template<typename Zone>
[[nodiscard]] inline const Zone& xres_find(SystemLP& sys, std::string_view name)
{
  for (const auto& z : sys.template elements<Zone>()) {
    if (z.object().name == name) {
      return z;
    }
  }
  FAIL("element not found: " << name);
  return sys.template elements<Zone>().front();
}

template<typename Rows>
[[nodiscard]] inline double xres_row_dual(const Rows& rows,
                                          const ScenarioLP& scenario,
                                          const StageLP& stage,
                                          BlockUid buid,
                                          const auto& duals)
{
  const auto it = rows.find({scenario.uid(), stage.uid()});
  REQUIRE(it != rows.end());
  return std::abs(duals[it->second.at(buid)]);
}
}  // namespace detail

[[nodiscard]] inline XresSnapshot xres_snapshot(PlanningLP& plp)
{
  XresSnapshot snap;
  for (auto& sys : plp.systems().front()) {
    auto& li = sys.linear_interface();
    snap.objective += li.get_obj_value();
    const auto duals = li.get_row_dual();
    const auto sol = li.get_col_sol();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& bus = sys.elements<BusLP>().front();
    const auto& cpf = detail::xres_find<ReserveZoneLP>(sys, "cpf");
    const auto& cpf_dn = detail::xres_find<ReserveZoneLP>(sys, "cpf_dn");
    const auto& sen = detail::xres_find<InertiaZoneLP>(sys, "sen");
    for (const auto& stage : sys.phase().stages()) {
      for (const auto& block : stage.blocks()) {
        const auto buid = block.uid();
        const XresSnapshot::Key key {static_cast<int>(stage.uid().value()),
                                     static_cast<int>(buid.value())};
        snap.lambda[key] = duals[bus.balance_rows_at(scenario, stage).at(buid)];
        snap.mu_up[key] = detail::xres_row_dual(
            cpf.urequirement_rows(), scenario, stage, buid, duals);
        snap.mu_dn[key] = detail::xres_row_dual(
            cpf_dn.drequirement_rows(), scenario, stage, buid, duals);
        snap.mu_inertia[key] = detail::xres_row_dual(
            sen.requirement_rows(), scenario, stage, buid, duals);
        auto& planes = snap.plane_duals[key];
        for (const auto r : cpf.urplane_rows(scenario, stage, buid)) {
          planes.push_back(std::abs(duals[r]));
        }
        const auto rcol = cpf.lookup_urequirement_col(scenario, stage, buid);
        REQUIRE(rcol.has_value());
        snap.ureq[key] = sol[rcol.value_or(ColIndex {0})];
      }
    }
    for (const auto& dv : sys.elements<DecisionVariableLP>()) {
      const auto& holder = dv.value_cols_holder();
      REQUIRE_FALSE(holder.empty());
      const auto& bcols = holder.begin()->second;
      REQUIRE_FALSE(bcols.empty());
      snap.contract.push_back(sol[bcols.begin()->second]);
    }
  }
  return snap;
}

/// CHECK two per-(stage, block) maps agree within @p eps (absolute floor
/// 1e-6 for values near zero).
inline void xres_check_same(const std::map<XresSnapshot::Key, double>& a,
                            const std::map<XresSnapshot::Key, double>& b,
                            std::string_view what,
                            double eps = 1.0e-6)
{
  REQUIRE(a.size() == b.size());
  for (const auto& [key, va] : a) {
    const auto it = b.find(key);
    REQUIRE(it != b.end());
    CAPTURE(what);
    CAPTURE(key.first);
    CAPTURE(key.second);
    CHECK(it->second == doctest::Approx(va).epsilon(eps));
  }
}

/// CHECK the cpf requirement R covers its exogenous plane 40 + 0.1·D in
/// every block.  R itself is NOT unique across solves: its other planes
/// read the endogenous inertia / contingency injection, whose dispatch
/// has alternative optima, so two equally optimal runs may report
/// different R at the same prices.
inline void xres_check_requirement_floor(
    const std::map<XresSnapshot::Key, double>& ureq)
{
  REQUIRE(ureq.size() == 12);
  for (const auto& [key, r] : ureq) {
    const auto s = static_cast<std::size_t>(key.first - 1);
    const auto b = static_cast<std::size_t>((key.second - 1) % 4);
    const double floor = 40.0 + (0.1 * kXresLoad.at(s).at(b));
    CAPTURE(key.first);
    CAPTURE(key.second);
    CHECK(r >= floor - 1.0e-6);
  }
}

}  // namespace gtopt::test_xres
