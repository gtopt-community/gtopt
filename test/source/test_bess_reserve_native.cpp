// SPDX-License-Identifier: BSD-3-Clause
//
// Native BESS reserve deliverability rows (Battery.reserve_sustain_hours)
// versus the CEN PLEXOS "SSCC" battery formulation.
//
// CEN's PCP model (category "BESS SSCC Constraints") couples every BESS
// that offers secondary/primary reserve to an auxiliary PLEXOS Battery
// (BAT_<x>_AUX: 99 999 MWh, ±1000 MW, η = 1, 50 % initial SoC, free end
// state) through two equalities:
//
//   BAT_<x>_CF_GEN_COMP:  Σ_z α_z·r_dn,z − g_aux = 0
//   BAT_<x>_CF_LOAD_COMP: Σ_z α_z·r_up,z − l_aux = 0
//
// so every MW of DOWN reserve injects α MWh of energy from a fictitious
// tank at the BESS bus.  When that injection cannot be disposed of, the
// bus price is pinned at (c_dn − π_dn)/α < 0 — the negative-CMg
// mechanism.  The native rows put no reserve term in any bus balance.
//
// Every expected value below is hand-computed (block = 1 h, probability
// 1, no discounting, scale_objective = 1, self-discharge off unless
// stated).
#include <cmath>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "lp_names_helpers.hpp"

using namespace gtopt;

namespace
{

struct BessCase
{
  // battery
  double pc_max {50.0};
  double pd_max {50.0};
  double emax {100.0};
  double eini {50.0};
  double eta_c {1.0};
  double eta_d {1.0};
  double annual_standing_loss {0.0};
  std::string native {"\"reserve_sustain_hours\": 1.0,"};
  // system
  double demand {50.0};
  double mustrun {50.0};  ///< non-disposable injection (pmin = pmax)
  double g1_cost {50.0};
  bool g1_down {true};  ///< g1 may offer down reserve (g1 − r ≥ 0)
  double urreq {0.0};
  double drreq {40.0};
  double shortage {1.0e4};
  // CEN AUX compensation
  bool cen_aux {false};
  double alpha {0.5};
  std::string rp_extra {};  ///< extra JSON fields of rp_bess
  std::string extra_provisions {};  ///< more provisions (",{...}")
};

std::string make_json(const BessCase& c)
{
  const std::string zone_req =
      std::format(R"("urreq": {}, "drreq": {}, "urcost": {}, "drcost": {})",
                  c.urreq,
                  c.drreq,
                  c.shortage,
                  c.shortage);
  std::string provisions = std::format(
      R"({{"uid": 1, "name": "rp_bess", "generator": "bess_gen",
          "reserve_zones": [1], "urmax": 1000, "drmax": 1000,
          "ur_provision_factor": {}, "dr_provision_factor": {}{}}})",
      c.urreq > 0.0 ? 1.0 : 0.0,
      c.drreq > 0.0 ? 1.0 : 0.0,
      c.rp_extra);
  provisions += c.extra_provisions;
  if (c.g1_down && c.drreq > 0.0) {
    provisions += R"(,
        {"uid": 2, "name": "rp_g1", "generator": "g1",
         "reserve_zones": [1], "drmax": 1000, "dr_provision_factor": 1.0})";
  }
  std::string aux_battery;
  std::string ucs;
  if (c.cen_aux) {
    aux_battery = R"(,
        {"uid": 2, "name": "aux", "bus": 1,
         "input_efficiency": 1.0, "output_efficiency": 1.0,
         "annual_standing_loss": 0.0, "emin": 0, "emax": 10000, "capacity": 10000,
         "eini": 5000, "pmax_charge": 1000, "pmax_discharge": 1000,
         "use_state_variable": true, "daily_cycle": false})";
    ucs = std::format(
        R"(, "user_constraint_array": [
        {{"uid": 1, "name": "BAT_BESS_CF_GEN_COMP",
          "expression": "battery('aux').discharge = {} * reserve_provision('rp_bess').dn"}},
        {{"uid": 2, "name": "BAT_BESS_CF_LOAD_COMP",
          "expression": "battery('aux').charge = 0"}}])",
        c.alpha);
  }
  return std::format(
      R"({{
  "options": {{
    "annual_discount_rate": 0.0,
    "model_options": {{
      "use_single_bus": true,
      "demand_fail_cost": 1.0e5,
      "scale_objective": 1.0
    }}
  }},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]
  }},
  "system": {{
    "name": "bess_reserve",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "capacity": {}}}],
    "generator_array": [
      {{"uid": 1, "name": "g0_mustrun", "bus": 1, "gcost": 0,
        "pmin": {}, "pmax": {}, "capacity": {}}},
      {{"uid": 2, "name": "g1", "bus": 1, "gcost": {}, "capacity": 200}}
    ],
    "battery_array": [
      {{"uid": 1, "name": "bess", "bus": 1,
        "input_efficiency": {}, "output_efficiency": {},
        "annual_standing_loss": {}, "emin": 0, "emax": {}, "capacity": {},
        "eini": {}, "pmax_charge": {}, "pmax_discharge": {},
        {}
        "use_state_variable": true, "daily_cycle": false}}{}
    ],
    "reserve_zone_array": [{{"uid": 1, "name": "rz", {}}}],
    "reserve_provision_array": [{}]{}
  }}
}})",
      c.demand,
      c.mustrun,
      c.mustrun,
      c.mustrun,
      c.g1_cost,
      c.eta_c,
      c.eta_d,
      c.annual_standing_loss,
      c.emax,
      c.emax,
      c.eini,
      c.pc_max,
      c.pd_max,
      c.native,
      aux_battery,
      zone_req,
      provisions,
      ucs);
}

struct Result
{
  double obj {0.0};
  double lambda {0.0};  ///< bus balance dual [$/MWh]
  double g1 {0.0};
  double pc {0.0};
  double pd {0.0};
  double soc {0.0};
  double r_up {0.0};
  double r_dn {0.0};
  int native_rows {0};
  int generic_rows {0};
  double r_up_dis {0.0};  ///< per-side split parts (caps active)
  double r_up_chg {0.0};
  int split_cols {0};
};

Result solve(const BessCase& c)
{
  auto planning = parse_planning_json(make_json(c));
  PlanningLP planning_lp(std::move(planning),
                         test_support::names_on_flat_opts());
  auto status = planning_lp.resolve();
  REQUIRE(status.has_value());

  auto& sys = planning_lp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();

  const auto sol = li.get_col_sol();
  const auto dual = li.get_row_dual();

  Result r;
  // The reported objective is "operating cost + shortage penalty" — the
  // hand-computed quantity: the met-requirement valuation (−rcost · req)
  // is cancelled by ReserveZoneLP's objective constant.
  r.obj = li.get_obj_value();
  const auto& bus = sys.elements<BusLP>().front();
  r.lambda = dual[bus.balance_rows_at(scenario, stage).at(buid)];
  for (const auto& g : sys.elements<GeneratorLP>()) {
    const auto& cols = g.lookup_generation_cols(scenario, stage);
    const auto it = cols.find(buid);
    if (it == cols.end()) {
      continue;
    }
    if (g.object().name == "g1") {
      r.g1 = sol[it->second];
    }
  }
  for (const auto& b : sys.elements<BatteryLP>()) {
    if (b.battery().name != "bess") {
      continue;
    }
    r.pc = sol[b.finp_cols_at(scenario, stage).at(buid)];
    r.pd = sol[b.fout_cols_at(scenario, stage).at(buid)];
    r.soc = sol[b.energy_cols_at(scenario, stage).at(buid)];
  }
  for (const auto& rp : sys.elements<ReserveProvisionLP>()) {
    if (rp.object().name != "rp_bess") {
      continue;
    }
    if (const auto col = rp.lookup_up_provision_col(scenario, stage, buid)) {
      r.r_up = sol[*col];
    }
    if (const auto col = rp.lookup_dn_provision_col(scenario, stage, buid)) {
      r.r_dn = sol[*col];
    }
    if (rp.lookup_up_provision_row(scenario, stage, buid)
        || rp.lookup_dn_provision_row(scenario, stage, buid))
    {
      ++r.generic_rows;
    }
  }
  for (Index i = 0; i < li.get_numcols(); ++i) {
    const auto* lbl = li.col_label_at(ColIndex {i});
    if (lbl == nullptr) {
      continue;
    }
    if (lbl->variable_name == ConverterLP::ReserveUpDischargeName) {
      r.r_up_dis += sol[ColIndex {i}];
      ++r.split_cols;
    } else if (lbl->variable_name == ConverterLP::ReserveUpChargeName) {
      r.r_up_chg += sol[ColIndex {i}];
      ++r.split_cols;
    }
  }
  for (Index i = 0; i < li.get_numrows(); ++i) {
    const auto* lbl = li.row_label_at(RowIndex {i});
    if (lbl != nullptr && lbl->constraint_name.starts_with("reserve_")) {
      ++r.native_rows;
    }
  }
  return r;
}

}  // namespace

TEST_CASE("BESS reserve: CEN AUX goes negative, native does not")  // NOLINT
{
  // 1 bus, demand 50 met by a 50 MW must-run (no disposal), g1 at
  // 50 $/MWh, BESS 50/50 MW, 100 MWh, SoC 50, down requirement 40.
  //
  // Native: the idle BESS holds up to 50 MW of down headroom (pc + r ≤ 50)
  // and 50 MWh of room (e + r ≤ 100) → r_dn ≥ 40 at zero cost, g1 = 0,
  // objective 0, λ ∈ [0, 50] (degenerate, never negative).
  //
  // CEN (α = 0.5): x MW of BESS down reserve injects 0.5x from the AUX.
  // The surplus must be stored (pc = 0.5x + g1) and pc + x ≤ 50; the rest
  // of the requirement comes from g1, which must RUN to offer it
  // (g1 ≥ y).  Solving: x = 20, y = g1 = 20, pc = 30, aux = 10,
  // objective 20·50 = 1000 and λ = −100 $/MWh.
  BessCase base;
  base.cen_aux = false;
  const auto nat = solve(base);
  CHECK(nat.obj == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(nat.r_dn >= 40.0 - 1e-6);
  CHECK(nat.g1 == doctest::Approx(0.0));
  CHECK(nat.lambda >= -1e-6);
  CHECK(nat.native_rows == 2);  // dn headroom + dn energy
  CHECK(nat.generic_rows == 0);

  BessCase cen = base;
  cen.cen_aux = true;
  const auto res = solve(cen);
  CHECK(res.obj == doctest::Approx(1000.0));
  CHECK(res.r_dn == doctest::Approx(20.0));
  CHECK(res.g1 == doctest::Approx(20.0));
  CHECK(res.pc == doctest::Approx(30.0));
  CHECK(res.lambda == doctest::Approx(-100.0));
}

TEST_CASE("BESS reserve: legacy rows unchanged when sustain unset")  // NOLINT
{
  // Without reserve_sustain_hours the generic per-provision generator
  // rows apply (g + r_up ≤ Pd, g − r_dn ≥ 0) and no converter reserve
  // row is emitted — existing cases are untouched.
  BessCase c;
  c.native.clear();
  c.mustrun = 0.0;
  c.demand = 100.0;
  const auto r = solve(c);
  CHECK(r.native_rows == 0);
  CHECK(r.generic_rows == 1);
  // Generic down row: r_dn ≤ pd.
  CHECK(r.r_dn <= r.pd + 1e-6);
}

TEST_CASE("BESS reserve: up headroom credits curtailing the charge")  // NOLINT
{
  // Demand 100 served by g1 (50 $/MWh); BESS 40/40 MW, SoC 100/200,
  // headroom only (T = 0); up requirement 60, shortage 1e4.
  //   Native: pd − pc + r_up ≤ 40 → r_up = 60 needs pc = 20 (stopping a
  //   20 MW charge is up-regulation).  g1 = 120, obj = 6000, λ = 50.
  //   Legacy: g + r_up ≤ 40 → r_up = 40, shortage 20 → 5000 + 2e5.
  BessCase c;
  c.pc_max = 40.0;
  c.pd_max = 40.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.mustrun = 0.0;
  c.demand = 100.0;
  c.urreq = 60.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 0.0,)";
  const auto nat = solve(c);
  CHECK(nat.r_up == doctest::Approx(60.0));
  // Only the net charge is pinned: simultaneous charge/discharge with
  // unit efficiencies is degenerate (CLP may return pc = 20 + x, pd = x).
  CHECK((nat.pc - nat.pd) == doctest::Approx(20.0));
  CHECK(nat.g1 == doctest::Approx(120.0));
  CHECK(nat.obj == doctest::Approx(6000.0));
  CHECK(nat.lambda == doctest::Approx(50.0));
  CHECK(nat.native_rows == 1);  // up headroom only (T = 0)

  c.native.clear();
  const auto leg = solve(c);
  CHECK(leg.r_up == doctest::Approx(40.0));
  CHECK(leg.obj == doctest::Approx(5000.0 + (20.0 * 1.0e4)));
}

TEST_CASE("BESS reserve: up SoC sufficiency with efficiency")  // NOLINT
{
  // BESS 150/150 MW, SoC 50/400, T = 2 h, ηd = 0.8, demand 50, up
  // requirement 60:
  //   e − (2/0.8)·60 ≥ 0 → e ≥ 150 → pc = 100, g1 = 150, obj = 7500.
  BessCase c;
  c.pc_max = 150.0;
  c.pd_max = 150.0;
  c.emax = 400.0;
  c.eini = 50.0;
  c.eta_d = 0.8;
  c.mustrun = 0.0;
  c.demand = 50.0;
  c.urreq = 60.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 2.0,)";
  const auto r = solve(c);
  CHECK(r.r_up == doctest::Approx(60.0));
  CHECK(r.soc == doctest::Approx(150.0));
  CHECK(r.pc == doctest::Approx(100.0));
  CHECK(r.obj == doctest::Approx(7500.0));
  CHECK(r.lambda == doctest::Approx(50.0));

  SUBCASE("self-discharge is drained before the sufficiency test")
  {
    // annual_standing_loss 0.48 → e = 50·(1 − 0.48/8760) + pc ≥ 150.
    c.annual_standing_loss = 0.48;
    const auto s = solve(c);
    const double pc = 100.0 + (50.0 * 0.48 / 8760.0);
    CHECK(s.soc == doctest::Approx(150.0));
    CHECK(s.pc == doctest::Approx(pc).epsilon(1e-9));
    CHECK(s.obj == doctest::Approx((50.0 + pc) * 50.0).epsilon(1e-9));
  }
}

TEST_CASE("BESS reserve: down SoC room and discharge credit")  // NOLINT
{
  // BESS 100/100 MW, SoC 180/200, T = 1, demand 10 served by the BESS
  // (free stored energy), down requirement 50 (BESS only).
  //   Room: e + r ≤ 200 with e = 180 − 10 = 170 → r_dn = 30, shortage 20
  //   → objective 20·1e4 = 2e5.  With T = 0 (headroom only) the full 50
  //   is available → objective 0.
  BessCase c;
  c.pc_max = 100.0;
  c.pd_max = 100.0;
  c.emax = 200.0;
  c.eini = 180.0;
  c.mustrun = 0.0;
  c.demand = 10.0;
  c.drreq = 50.0;
  c.g1_down = false;
  const auto r = solve(c);
  CHECK(r.pd == doctest::Approx(10.0));
  CHECK(r.soc == doctest::Approx(170.0));
  CHECK(r.r_dn == doctest::Approx(30.0));
  CHECK(r.obj == doctest::Approx(20.0 * 1.0e4));
  CHECK(r.native_rows == 2);

  BessCase t0 = c;
  t0.native = R"("reserve_sustain_hours": 0.0,)";
  const auto h0 = solve(t0);
  CHECK(h0.r_dn >= 50.0 - 1e-6);  // degenerate: up to Pc
  CHECK(h0.obj == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(h0.native_rows == 1);

  SUBCASE("charge-only vs discharge-credit headroom")
  {
    // Pc = 20, T = 0, demand 100 served by the BESS (pd = 100, free
    // energy).  Charge-only: pc + r ≤ 20 → r = 20, shortage 30 → 3e5.
    // Credit (κ = 1): pc − pd + r ≤ 20 → r = 50 because pd ≥ 30 → 0.
    BessCase h = c;
    h.pc_max = 20.0;
    h.eini = 100.0;
    h.demand = 100.0;
    h.native = R"("reserve_sustain_hours": 0.0,)";
    const auto co = solve(h);
    CHECK(co.r_dn == doctest::Approx(20.0));
    CHECK(co.obj == doctest::Approx(30.0 * 1.0e4));

    h.native =
        R"("reserve_sustain_hours": 0.0, "reserve_discharge_credit": true,)";
    const auto cr = solve(h);
    // Degenerate above the requirement: provision is free, so any
    // r_dn in [50, Pc + pd - pc] = [50, 120] is optimal (HiGHS 1.12
    // returns 120, 1.15 returns 50).  The objective pins the credit.
    CHECK(cr.r_dn >= 50.0 - 1e-6);
    CHECK(cr.pd >= 30.0 - 1e-6);
    CHECK(cr.obj == doctest::Approx(0.0).epsilon(1e-9));
  }
}

TEST_CASE("BESS reserve: per-side response caps (PLEXOS Max Response)")
// NOLINT
{
  // Same system as "up headroom credits curtailing the charge": demand
  // 100 by g1 (50 $/MWh), BESS 40/40 MW, up requirement 60, T = 0.
  // The provision splits r_up = r_dis + r_chg with r_dis ≤ 40 − pd and
  // r_chg ≤ pc, each capped by the PLEXOS per-side schedule.
  BessCase c;
  c.pc_max = 40.0;
  c.pd_max = 40.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.mustrun = 0.0;
  c.demand = 100.0;
  c.urreq = 60.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 0.0,)";

  SUBCASE("charge-side cap 10: r_up = 40 + 10, shortage 10")
  {
    c.rp_extra = R"(, "urmax_charge_response": 10)";
    const auto r = solve(c);
    CHECK(r.r_up == doctest::Approx(50.0));
    CHECK(r.r_up_chg == doctest::Approx(10.0));
    CHECK(r.r_up_dis == doctest::Approx(40.0));
    CHECK(r.obj == doctest::Approx((110.0 * 50.0) + (10.0 * 1.0e4)));
    CHECK(r.lambda == doctest::Approx(50.0));
  }
  SUBCASE("discharge-side cap 25: the charge side covers 35")
  {
    // r_chg = 35 needs a 35 MW gross charge; the power envelope
    // pc/40 + pd/40 ≤ 1 then allows pd ≤ 5, so the cheapest point is
    // pc = 35, pd = 5 (net charge 30, g1 = 130).
    c.rp_extra = R"(, "urmax_response": 25)";
    const auto r = solve(c);
    CHECK(r.split_cols == 2);
    CHECK(r.r_up == doctest::Approx(60.0));
    CHECK(r.r_up_dis == doctest::Approx(25.0));
    CHECK(r.r_up_chg == doctest::Approx(35.0));
    CHECK(r.pc == doctest::Approx(35.0));
    CHECK((r.pc - r.pd) == doctest::Approx(30.0));
    CHECK(r.obj == doctest::Approx(130.0 * 50.0));
  }
  SUBCASE("no cap: unchanged native result (pc = 20)")
  {
    const auto r = solve(c);
    CHECK(r.split_cols == 0);
    CHECK(r.r_up == doctest::Approx(60.0));
    CHECK(r.obj == doctest::Approx(120.0 * 50.0));
  }
}

TEST_CASE("BESS reserve: expected deployment energy in the SoC")  // NOLINT
{
  // Replaces the CEN AUX compensation (CF_GEN/LOAD_COMP, α = 0.3–0.5)
  // by a SoC term: no bus injection.  Idle BESS, no load, SoC 100/200.
  BessCase c;
  c.pc_max = 100.0;
  c.pd_max = 100.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.mustrun = 0.0;
  c.demand = 0.0;
  c.g1_down = false;
  c.rp_extra = R"(, "urcost": 1, "drcost": 1)";

  SUBCASE("up: e = 100 − (0.5/ηd)·60")
  {
    c.urreq = 60.0;
    c.drreq = 0.0;
    c.eta_d = 0.8;
    c.native =
        R"("reserve_sustain_hours": 0.0, "reserve_energy_usage_up": 0.5,)";
    const auto r = solve(c);
    CHECK(r.r_up == doctest::Approx(60.0));
    CHECK(r.soc == doctest::Approx(100.0 - (0.5 * 60.0 / 0.8)));
  }
  SUBCASE("down: e = 100 + 0.5·ηc·40")
  {
    c.urreq = 0.0;
    c.drreq = 40.0;
    c.eta_c = 0.9;
    c.native =
        R"("reserve_sustain_hours": 0.0, "reserve_energy_usage_down": 0.5,)";
    const auto r = solve(c);
    CHECK(r.r_dn == doctest::Approx(40.0));
    CHECK(r.soc == doctest::Approx(100.0 + (0.5 * 0.9 * 40.0)));
    CHECK(r.lambda >= -1e-6);  // no forced injection
  }
  SUBCASE("unset: SoC untouched")
  {
    c.urreq = 60.0;
    c.drreq = 0.0;
    c.native = R"("reserve_sustain_hours": 0.0,)";
    const auto r = solve(c);
    CHECK(r.soc == doctest::Approx(100.0));
  }
}

TEST_CASE("BESS reserve: two capped provisions on one battery (CPF + CTF)")
// NOLINT
{
  // Regression: each provision gets its own split columns and row
  // (labelled ReserveProvision/<uid>), so two capped provisions in the
  // same direction no longer collide.  Charge caps 10 + 5 → charge side
  // ≤ 15, discharge side ≤ 40 → 55 of the 60 MW requirement, shortage 5,
  // g1 = 100 + 15.
  BessCase c;
  c.pc_max = 40.0;
  c.pd_max = 40.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.mustrun = 0.0;
  c.demand = 100.0;
  c.urreq = 60.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 0.0,)";
  c.rp_extra = R"(, "urmax_charge_response": 10)";
  c.extra_provisions = R"(,
      {"uid": 3, "name": "rp_bess_ctf", "generator": "bess_gen",
       "reserve_zones": [1], "urmax": 1000, "ur_provision_factor": 1,
       "urmax_charge_response": 5})";
  const auto r = solve(c);
  CHECK(r.split_cols == 4);
  CHECK(r.r_up_chg == doctest::Approx(15.0));
  CHECK(r.r_up_dis == doctest::Approx(40.0));
  CHECK(r.obj == doctest::Approx((115.0 * 50.0) + (5.0 * 1.0e4)));
}

TEST_CASE("BESS reserve: per-provision deployment energy overrides")
// NOLINT
{
  // Battery default a_up = 0.5.  rp_cpf (1 $/MW, ≤ 20 MW) inherits it;
  // rp_bess (2 $/MW) takes the other 40 MW.  Idle BESS, SoC 100, η = 1.
  BessCase c;
  c.pc_max = 100.0;
  c.pd_max = 100.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.mustrun = 0.0;
  c.demand = 0.0;
  c.g1_down = false;
  c.urreq = 60.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 0.0, "reserve_energy_usage_up": 0.5,)";
  c.extra_provisions = R"(,
      {"uid": 3, "name": "rp_cpf", "generator": "bess_gen",
       "reserve_zones": [1], "urmax": 20, "ur_provision_factor": 1,
       "urcost": 1})";
  SUBCASE("rp_bess overrides to 0 (PLEXOS CPF/CTF): only rp_cpf uses SoC")
  {
    c.rp_extra = R"(, "urcost": 2, "ur_energy_usage": 0)";
    const auto r = solve(c);
    CHECK(r.r_up == doctest::Approx(40.0));
    CHECK(r.soc == doctest::Approx(100.0 - (0.5 * 20.0)));
  }
  SUBCASE("no override: both use the battery default")
  {
    c.rp_extra = R"(, "urcost": 2)";
    const auto r = solve(c);
    CHECK(r.soc == doctest::Approx(100.0 - (0.5 * 60.0)));
  }
  SUBCASE("override 0.3 on rp_bess (PLEXOS CSF)")
  {
    c.rp_extra = R"(, "urcost": 2, "ur_energy_usage": 0.3)";
    const auto r = solve(c);
    CHECK(r.soc == doctest::Approx(100.0 - (0.5 * 20.0) - (0.3 * 40.0)));
  }
}

TEST_CASE("BESS reserve: split replaces the aggregate headroom row")
// NOLINT
{
  // With per-side caps the side rows imply the aggregate up headroom row;
  // emitting both made them linearly dependent (review P2-1).
  BessCase c;
  c.pc_max = 40.0;
  c.pd_max = 40.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.mustrun = 0.0;
  c.demand = 100.0;
  c.urreq = 60.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 0.0,)";
  const auto headroom_rows = [](const BessCase& bc)
  {
    PlanningLP plp(parse_planning_json(make_json(bc)),
                   test_support::names_on_flat_opts());
    REQUIRE(plp.resolve().has_value());
    const auto& li = plp.systems().front().front().linear_interface();
    int n = 0;
    for (Index i = 0; i < li.get_numrows(); ++i) {
      const auto* lbl = li.row_label_at(RowIndex {i});
      if (lbl != nullptr
          && lbl->constraint_name == ConverterLP::ReserveUpHeadroomName)
      {
        ++n;
      }
    }
    return n;
  };
  CHECK(headroom_rows(c) == 1);
  c.rp_extra = R"(, "urmax_charge_response": 10)";
  CHECK(headroom_rows(c) == 0);
  // Same optimum as before the change (side rows carry the headroom).
  const auto r = solve(c);
  CHECK(r.r_up == doctest::Approx(50.0));
  CHECK(r.obj == doctest::Approx((110.0 * 50.0) + (10.0 * 1.0e4)));
}

TEST_CASE("BESS reserve: charge-side deployment energy uses eta_c")
// NOLINT
{
  // Up reserve served entirely from the charge side (discharge cap 0):
  // deploying it means charging less, which saves ηc·Δ of SoC per MW —
  // not Δ/ηd as for the discharge side (review P2-2).
  //   r_chg = 30, a = 0.5, ηc = 0.9, ηd = 0.8: the deployment term is
  //   −0.5·0.9·30 = −13.5  (was −0.5·30/0.8 = −18.75).
  BessCase c;
  c.pc_max = 100.0;
  c.pd_max = 100.0;
  c.emax = 200.0;
  c.eini = 100.0;
  c.eta_c = 0.9;
  c.eta_d = 0.8;
  c.mustrun = 0.0;
  c.demand = 0.0;
  c.g1_down = false;
  c.g1_cost = 1.0;
  c.urreq = 30.0;
  c.drreq = 0.0;
  c.native = R"("reserve_sustain_hours": 0.0, "reserve_energy_usage_up": 0.5,)";
  c.rp_extra = R"(, "urmax_response": 0)";
  const auto r = solve(c);
  CHECK(r.r_up == doctest::Approx(30.0));
  CHECK(r.r_up_chg == doctest::Approx(30.0));
  CHECK(r.pc == doctest::Approx(30.0));
  // The LP may also discharge (pd) to displace g1; the deployment term is
  // the ηc·Δ part only:  e = 100 + ηc·pc − pd/ηd − a·ηc·r_chg.
  CHECK(r.soc
        == doctest::Approx(100.0 + (0.9 * r.pc) - (r.pd / 0.8)
                           - (0.5 * 0.9 * 30.0)));
}
