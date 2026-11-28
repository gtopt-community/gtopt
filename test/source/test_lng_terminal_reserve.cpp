// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_lng_terminal_reserve.cpp
 * @brief     Gas backing of the reserves of LngTerminal-fed generators
 *            (LngTerminal.reserve_sustain_hours / reserve_gas_usage)
 *
 * One bus, demand `demand` MW; `gas` (VOM 1 $/MWh, heat rate 1 m³/MWh,
 * fed from the tank) and `backup` (1000 $/MWh, no reserve).  A reserve
 * zone with up / down requirements; `gas` and an optional `peaker`
 * (up reserve only, bid 50 $/MW, its own capacity) provide.  Blocks of
 * 1 h, scale_objective 1, no discounting: every value is hand-computed.
 */
#include <cmath>
#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct GasCase
{
  int nstages {1};
  double demand {100.0};
  double eini {0.0};
  std::string delivery {"0"};  ///< scalar or per-stage array
  double urreq {50.0};
  double drreq {0.0};
  bool peaker {false};
  std::string tank_extra {R"(, "reserve_sustain_hours": 1.0)"};
};

std::string make_gas_json(const GasCase& c)
{
  std::string blocks;
  std::string stages;
  for (int t = 0; t < c.nstages; ++t) {
    blocks += std::format(
        R"({}{{"uid": {}, "duration": 1}})", t == 0 ? "" : ", ", t + 1);
    stages +=
        std::format(R"({}{{"uid": {}, "first_block": {}, "count_block": 1}})",
                    t == 0 ? "" : ", ",
                    t + 1,
                    t);
  }
  std::string gens =
      R"({"uid": 1, "name": "gas", "bus": 1, "gcost": 1, "capacity": 500},
      {"uid": 2, "name": "backup", "bus": 1, "gcost": 1000, "capacity": 500})";
  std::string provs = R"({"uid": 1, "name": "rp_gas", "generator": "gas",
       "reserve_zones": [1], "urmax": 200, "drmax": 200,
       "ur_provision_factor": 1, "dr_provision_factor": 1})";
  if (c.peaker) {
    gens += R"(,
      {"uid": 3, "name": "peaker", "bus": 1, "gcost": 2000, "capacity": 100})";
    provs += R"(,
      {"uid": 2, "name": "rp_peaker", "generator": "peaker",
       "reserve_zones": [1], "urmax": 100, "urcost": 50,
       "ur_provision_factor": 1})";
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
    "block_array": [{}],
    "stage_array": [{}],
    "scenario_array": [{{"uid": 1}}]
  }},
  "system": {{
    "name": "lng_reserve",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "lmax": {}}}],
    "generator_array": [{}],
    "lng_terminal_array": [{{
      "uid": 1, "name": "tank", "emin": 0, "emax": 10000, "eini": {},
      "annual_standing_loss": 0, "use_state_variable": true,
      "delivery": {},
      "generators": [{{"generator": 1, "heat_rate": 1.0}}]{}
    }}],
    "reserve_zone_array": [{{"uid": 1, "name": "rz",
      "urreq": {}, "drreq": {}, "urcost": 1.0e4, "drcost": 1.0e4}}],
    "reserve_provision_array": [{}]
  }}
}})",
      blocks,
      stages,
      c.demand,
      gens,
      c.eini,
      c.delivery,
      c.tank_extra,
      c.urreq,
      c.drreq,
      provs);
}

struct GasStageSol
{
  double gas {0.0};
  double r_up {0.0};
  double r_dn {0.0};
  double volume {0.0};
  double reserve_price {0.0};  ///< up requirement dual [$/MW]
};

struct GasResult
{
  double obj {0.0};
  std::vector<GasStageSol> stages;
  int reserve_rows {0};
};

GasResult solve_gas(const GasCase& c)
{
  auto planning = parse_planning_json(make_gas_json(c));
  PlanningLP planning_lp(std::move(planning));
  auto status = planning_lp.resolve();
  REQUIRE(status.has_value());

  auto& sys = planning_lp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto sol = li.get_col_sol();
  const auto dual = li.get_row_dual();

  GasResult r;
  r.obj = li.get_obj_value();
  const auto& tank = sys.elements<LngTerminalLP>().front();
  for (const auto& stage : sys.phase().stages()) {
    const auto buid = stage.blocks().front().uid();
    GasStageSol s;
    for (const auto& g : sys.elements<GeneratorLP>()) {
      if (g.object().name != "gas") {
        continue;
      }
      const auto& cols = g.lookup_generation_cols(scenario, stage);
      if (const auto it = cols.find(buid); it != cols.end()) {
        s.gas = sol[it->second];
      }
    }
    for (const auto& rp : sys.elements<ReserveProvisionLP>()) {
      if (rp.object().name != "rp_gas") {
        continue;
      }
      if (const auto col = rp.lookup_up_provision_col(scenario, stage, buid)) {
        s.r_up = sol[*col];
      }
      if (const auto col = rp.lookup_dn_provision_col(scenario, stage, buid)) {
        s.r_dn = sol[*col];
      }
    }
    s.volume = sol[tank.energy_cols_at(scenario, stage).at(buid)];
    const auto& zone = sys.elements<ReserveZoneLP>().front();
    const auto& rows = zone.urequirement_rows();
    if (const auto it = rows.find({scenario.uid(), stage.uid()});
        it != rows.end())
    {
      if (const auto jt = it->second.find(buid); jt != it->second.end()) {
        s.reserve_price = std::abs(dual[jt->second]);
      }
    }
    r.stages.push_back(s);
  }
  const auto count = [](const auto& holder)
  {
    int n = 0;
    for (const auto& [key, rows] : holder) {
      n += static_cast<int>(rows.size());
    }
    return n;
  };
  r.reserve_rows = count(tank.reserve_up_energy_rows())
      + count(tank.reserve_up_sendout_rows());
  return r;
}

}  // namespace

TEST_CASE("LngTerminal reserve fields default to unset")  // NOLINT
{
  const LngTerminal t;
  CHECK_FALSE(t.reserve_sustain_hours.has_value());
  CHECK_FALSE(t.reserve_gas_usage.has_value());
}

TEST_CASE("LngTerminal reserve: empty tank offers no reserve")  // NOLINT
{
  // Empty tank, no delivery: gas cannot run (P = 0), backup serves the
  // 100 MW demand.  Requirements up 50, down 50.
  GasCase c;
  c.drreq = 50.0;

  SUBCASE("unset (default): the empty tank still sells up reserve")
  {
    c.tank_extra = "";
    const auto r = solve_gas(c);
    REQUIRE(r.stages.size() == 1);
    CHECK(r.reserve_rows == 0);
    CHECK(r.stages[0].gas == doctest::Approx(0.0));
    CHECK(r.stages[0].r_up >= 50.0 - 1e-6);  // gas-less headroom
    CHECK(r.stages[0].r_dn == doctest::Approx(0.0));  // r_dn <= P = 0
    // backup 100·1000 + down shortage 50·1e4
    CHECK(r.obj == doctest::Approx(1.0e5 + 5.0e5));
  }
  SUBCASE("reserve_sustain_hours: r_up = r_dn = 0")
  {
    const auto r = solve_gas(c);
    REQUIRE(r.stages.size() == 1);
    CHECK(r.reserve_rows == 1);
    CHECK(r.stages[0].gas == doctest::Approx(0.0));
    CHECK(r.stages[0].r_up == doctest::Approx(0.0));
    CHECK(r.stages[0].r_dn == doctest::Approx(0.0));
    CHECK(r.obj == doctest::Approx(1.0e5 + 5.0e5 + 5.0e5));
  }
}

TEST_CASE("LngTerminal reserve: stock sustains T hours of up reserve")
{
  // eini 200, T = 2, requirement 80: V = 200 − P and 2·r_up <= V.  A MW
  // of reserve needs 2 MWh less burn (backup 999 $/MWh more) against a
  // 1e4 $/MW shortage, so the LP burns P = 40 to keep V = 160 → r = 80.
  GasCase c;
  c.eini = 200.0;
  c.urreq = 80.0;
  c.tank_extra = R"(, "reserve_sustain_hours": 2.0)";
  const auto r = solve_gas(c);
  CHECK(r.stages[0].gas == doctest::Approx(40.0));
  CHECK(r.stages[0].volume == doctest::Approx(160.0));
  CHECK(r.stages[0].r_up == doctest::Approx(80.0));
  CHECK(r.obj == doctest::Approx(40.0 + (60.0 * 1000.0)));

  SUBCASE("T = 0: no energy row")
  {
    c.tank_extra = R"(, "reserve_sustain_hours": 0.0)";
    const auto r0 = solve_gas(c);
    CHECK(r0.reserve_rows == 0);  // no sendout_max either
    CHECK(r0.stages[0].r_up == doctest::Approx(80.0));
    CHECK(r0.stages[0].gas == doctest::Approx(100.0));
  }
  SUBCASE("deployment gas: reserve_gas_usage 0.5")
  {
    // V = 200 − P − 0.5·r and V − 2·r >= 0 → P <= 200 − 2.5·r: r = 80
    // takes all the gas (P = 0, V = 160).
    c.tank_extra =
        R"(, "reserve_sustain_hours": 2.0, "reserve_gas_usage": 0.5)";
    const auto ru = solve_gas(c);
    CHECK(ru.stages[0].r_up == doctest::Approx(80.0));
    CHECK(ru.stages[0].gas == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(ru.stages[0].volume == doctest::Approx(160.0));
  }
}

TEST_CASE("LngTerminal reserve: send-out headroom P + r_up")  // NOLINT
{
  // Large stock, sendout_max 120 m³/h, hr 1: P + r_up <= 120; reserve
  // (shortage 1e4) beats burn (999 saved per MWh): r_up 50, P 70.
  GasCase c;
  c.eini = 5000.0;
  c.urreq = 50.0;
  c.tank_extra = R"(, "sendout_max": 120, "reserve_sustain_hours": 1.0)";
  const auto r = solve_gas(c);
  CHECK(r.reserve_rows == 2);
  CHECK(r.stages[0].gas == doctest::Approx(70.0));
  CHECK(r.stages[0].r_up == doctest::Approx(50.0));

  SUBCASE("enforce_sendout = false drops the headroom row")
  {
    c.tank_extra = R"(, "sendout_max": 120, "enforce_sendout": false,
        "reserve_sustain_hours": 1.0)";
    const auto r1 = solve_gas(c);
    CHECK(r1.reserve_rows == 1);
    CHECK(r1.stages[0].r_up == doctest::Approx(50.0));
    CHECK(r1.stages[0].gas == doctest::Approx(100.0));
  }
}

TEST_CASE("LngTerminal reserve: the price rises when the tank empties")
{
  // Three stages, one cargo of 250 m³ in stage 1, demand 100, up
  // requirement 50.  Keeping 1 m³ for reserve costs 999 $ of backup and
  // saves at most the peaker bid 50 $/MW, so all 250 m³ are burnt and
  // the tank is empty at the end of stage 3: the peaker covers that
  // stage's requirement and the reserve price rises 0 → 50 $/MW.  (The
  // burn split is degenerate; stage 2 keeps V ≥ 50 at the optimum.)
  GasCase c;
  c.nstages = 3;
  c.delivery = "[250, 0, 0]";
  c.peaker = true;
  const auto r = solve_gas(c);
  REQUIRE(r.stages.size() == 3);
  // Reserve above the requirement is free, so r_up is degenerate above
  // 50 (CPLEX returns 150): only the requirement is pinned.
  CHECK(r.stages[0].r_up >= 50.0 - 1e-6);
  CHECK(r.stages[0].reserve_price == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(r.stages[2].volume == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(r.stages[2].r_up == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(r.stages[2].reserve_price == doctest::Approx(50.0));
  // gas 250 MWh·1 + backup 50·1000 + peaker reserve 50·50
  CHECK(r.obj == doctest::Approx(250.0 + 5.0e4 + 2500.0));

  SUBCASE("unset: the empty tank keeps selling reserve at price 0")
  {
    c.tank_extra = "";
    const auto r0 = solve_gas(c);
    CHECK(r0.stages[2].r_up >= 50.0 - 1e-6);  // free, degenerate above 50
    CHECK(r0.stages[2].reserve_price == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(r0.obj == doctest::Approx(250.0 + 5.0e4));
  }
}
