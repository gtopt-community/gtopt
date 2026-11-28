// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_lng_terminal_fuel_curve.cpp
 * @brief     LngTerminal.use_generator_fuel_curve: the tank draw follows
 *            the linked generator's heat-rate curve (segments + no-load)
 *
 * One bus, demand 100 MW, 1-h block, scale_objective 1.  `gas` burns
 * from `tank` (link heat_rate 1 = the curve is in m³/MWh); `backup`
 * costs 1000 $/MWh.  Hand-computed expectations.
 */
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct CurveCase
{
  std::string gas_extra {
      R"(, "pmax_segments": [50, 100], "heat_rate_segments": [1.0, 2.0])"};
  std::string tank_extra {R"(, "use_generator_fuel_curve": true)"};
  double eini {120.0};
  double urreq {0.0};
  std::string commitment {};  ///< commitment_array JSON (with key) or ""
};

std::string make_curve_json(const CurveCase& c)
{
  const std::string reserves = c.urreq > 0.0 ? std::format(
                                                   R"(,
    "reserve_zone_array": [{{"uid": 1, "name": "rz", "urreq": {},
      "urcost": 1.0e5}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_gas",
      "generator": "gas", "reserve_zones": [1], "urmax": 100,
      "ur_provision_factor": 1}}])",
                                                   c.urreq)
                                             : std::string {};
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
    "name": "lng_curve",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": 1, "lmax": 100}}],
    "generator_array": [
      {{"uid": 1, "name": "gas", "bus": 1, "gcost": 1, "capacity": 100,
        "pmax": 100{}}},
      {{"uid": 2, "name": "backup", "bus": 1, "gcost": 1000,
        "capacity": 500}}
    ],
    "lng_terminal_array": [{{
      "uid": 1, "name": "tank", "emin": 0, "emax": 10000, "eini": {},
      "annual_standing_loss": 0, "use_state_variable": true,
      "generators": [{{"generator": 1, "heat_rate": 1.0}}]{}
    }}]{}{}
  }}
}})",
      c.gas_extra,
      c.eini,
      c.tank_extra,
      c.commitment,
      reserves);
}

struct CurveResult
{
  double gas {0.0};
  double r_up {0.0};
  double volume {0.0};
  double obj {0.0};
};

CurveResult solve_curve(const CurveCase& c)
{
  auto planning = parse_planning_json(make_curve_json(c));
  PlanningLP planning_lp(std::move(planning));
  auto status = planning_lp.resolve();
  REQUIRE(status.has_value());

  auto& sys = planning_lp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto sol = li.get_col_sol();

  CurveResult r;
  r.obj = li.get_obj_value();
  for (const auto& g : sys.elements<GeneratorLP>()) {
    if (g.object().name == "gas") {
      const auto& cols = g.lookup_generation_cols(scenario, stage);
      if (const auto it = cols.find(buid); it != cols.end()) {
        r.gas = sol[it->second];
      }
    }
  }
  for (const auto& rp : sys.elements<ReserveProvisionLP>()) {
    if (const auto col = rp.lookup_up_provision_col(scenario, stage, buid)) {
      r.r_up = sol[*col];
    }
  }
  const auto& tank = sys.elements<LngTerminalLP>().front();
  r.volume = sol[tank.energy_cols_at(scenario, stage).at(buid)];
  return r;
}

/// Committed no-load burn 40 m³/h; segments 60 MW @ 1.0, then 1.2.
/// The tank gas is worth 10 $/m³ (efin = eini, efin_cost 10).
CurveCase noload_case()
{
  CurveCase c;
  c.gas_extra =
      R"(, "pmax_segments": [60, 100], "heat_rate_segments": [1.0, 1.2])";
  c.eini = 1000.0;
  c.tank_extra =
      R"(, "use_generator_fuel_curve": true, "efin": 1000, "efin_cost": 10)";
  c.commitment = R"(,
    "commitment_array": [{"uid": 1, "name": "c_gas", "generator": "gas",
      "noload_fuel": 40}])";
  return c;
}

}  // namespace

TEST_CASE("LngTerminal fuel curve: field defaults to unset")  // NOLINT
{
  const LngTerminal t;
  CHECK_FALSE(t.use_generator_fuel_curve.has_value());
}

TEST_CASE("LngTerminal fuel curve: the draw follows the segments")  // NOLINT
{
  // 120 m³ of gas (scarce: backup 1000 $/MWh).  Curve: burn(P) = P up to
  // 50 MW, then 50 + 2·(P − 50) → P = 85, backup 15.
  CurveCase c;
  const auto r = solve_curve(c);
  CHECK(r.gas == doctest::Approx(85.0));
  CHECK(r.volume == doctest::Approx(0.0).epsilon(1e-9));

  SUBCASE("off (default): the link heat rate 1 × P")
  {
    c.tank_extra = "";
    const auto r0 = solve_curve(c);
    CHECK(r0.gas == doctest::Approx(100.0));
    CHECK(r0.volume == doctest::Approx(20.0));
  }
  SUBCASE("scalar Generator.heat_rate is a curve too")
  {
    c.gas_extra = R"(, "heat_rate": 1.5)";
    const auto r1 = solve_curve(c);
    CHECK(r1.gas == doctest::Approx(80.0));  // 120 / 1.5
  }
  SUBCASE("link heat_rate converts the curve's fuel unit to m³")
  {
    c.tank_extra = R"(, "use_generator_fuel_curve": true)";
    c.gas_extra = R"(, "heat_rate": 3.0)";  // GJ/MWh
    c.eini = 120.0;
    // link 0.5 m³/GJ → 1.5 m³/MWh → 80 MW
    auto json = make_curve_json(c);
    constexpr std::string_view link_hr {R"("heat_rate": 1.0)"};
    const auto pos = json.find(link_hr);
    REQUIRE(pos != std::string::npos);
    json.replace(pos, link_hr.size(), R"("heat_rate": 0.5)");
    auto planning = parse_planning_json(json);
    PlanningLP planning_lp(std::move(planning));
    REQUIRE(planning_lp.resolve().has_value());
    auto& sys = planning_lp.systems().front().front();
    const auto sol = sys.linear_interface().get_col_sol();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().front();
    const auto& gas = sys.elements<GeneratorLP>().front();
    const auto col = gas.lookup_generation_cols(scenario, stage)
                         .at(stage.blocks().front().uid());
    CHECK(sol[col] == doctest::Approx(80.0));
  }
}

TEST_CASE("LngTerminal fuel curve: reserve at part load costs gas")  // NOLINT
{
  // No reserve: P = 100, burn = 40 + 60 + 1.2·40 = 148 m³ (1.48 m³/MWh).
  auto c = noload_case();
  const auto full = solve_curve(c);
  CHECK(full.gas == doctest::Approx(100.0));
  CHECK(1000.0 - full.volume == doctest::Approx(148.0));

  // Up requirement 50 (gas only): P = 50, r = 50, burn = 40 + 50 = 90
  // m³ for 50 MWh (1.8 m³/MWh): holding reserve at part load raises the
  // specific consumption, and the backup covers the other 50 MW.
  c.urreq = 50.0;
  const auto part = solve_curve(c);
  CHECK(part.gas == doctest::Approx(50.0));
  CHECK(part.r_up == doctest::Approx(50.0));
  const double burn = 1000.0 - part.volume;
  CHECK(burn == doctest::Approx(90.0));
  CHECK(burn / part.gas > (1000.0 - full.volume) / full.gas);
  // cost: VOM 50 + backup 50·1000 + gas 90·10 vs VOM 100 + gas 148·10
  CHECK(part.obj == doctest::Approx(50.0 + 50'000.0 + 900.0));
  CHECK(full.obj == doctest::Approx(100.0 + 1480.0));
}

TEST_CASE("LngTerminal fuel curve: reserve backed at the top slope")  // NOLINT
{
  // 150 m³, T = 1, requirement 50 (gas only, shortage 1e5): V = 150 −
  // burn(P) ≥ 1.2·r_up (the top segment).  burn(P) = 40 + P for P ≤ 60,
  // so r = 50 needs burn ≤ 90 → P = 50, V = 60.
  auto c = noload_case();
  c.eini = 150.0;
  c.tank_extra =
      R"(, "use_generator_fuel_curve": true, "reserve_sustain_hours": 1)";
  c.urreq = 50.0;
  const auto r = solve_curve(c);
  CHECK(r.r_up == doctest::Approx(50.0));
  CHECK(r.gas == doctest::Approx(50.0));
  CHECK(r.volume == doctest::Approx(60.0));
}
