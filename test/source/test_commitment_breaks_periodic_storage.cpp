// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_breaks_periodic_storage.cpp
 * @brief     Daily commitment grid together with periodic storages on
 *            one periodic point week
 * @date      2026-11-28
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * One point-sampled typical week (168 hourly nodes from Wednesday 12:00,
 * `period_hours` 168, weight 4: one month) with
 *   * a cyclic (relaxed) unit commitment on the study
 *     `commitment_breaks` grid,
 *   * a cyclic battery (`daily_cycle`) and a state battery
 *     (`daily_cycle: false`, `use_state_variable`) under
 *     `model_options.periodic_storage`.
 * The two wraps are independent: the storage option must not change
 * the commitment cells, the cells must not change the storage rows,
 * and each wrap keeps its own invariant on the joint solution.
 */

#include <cmath>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace commitment_breaks_periodic_storage_test
{
namespace
{

constexpr int kNodes = 168;
constexpr double kWeight = 4.0;  ///< n = 672 h / 168 h

struct Options
{
  bool periodic_storage {true};
  bool breaks {true};
};

/// Peak 80 MW at the clock hours 18..23 of every day, 20 MW elsewhere;
/// node k sits at clock time (12 + k) mod 24.
[[nodiscard]] std::string make_json(const Options& o)
{
  std::string blocks;
  std::string lmax;
  for (int k = 0; k < kNodes; ++k) {
    const int clock = (12 + k) % 24;
    blocks += std::format(R"({}{{"uid": {}, "duration": {}, "time": {}}})",
                          k == 0 ? "" : ", ",
                          k + 1,
                          kWeight,
                          k);
    lmax += std::format("{}{}", k == 0 ? "" : ", ", clock >= 18 ? 80.0 : 20.0);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1{}{}}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": true, "sampling": "point",
                      "period_hours": {}, "start_hour": 12}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "uc_breaks_periodic_storage",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[{}]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 10,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "noload_cost": 300, "cycle": "stage",
                           "relax": true}}],
    "battery_array": [
      {{"uid": 1, "name": "bess", "bus": "b1",
        "input_efficiency": 1, "output_efficiency": 1,
        "annual_standing_loss": 0,
        "emin": 0, "emax": 100, "pmax_charge": 50, "pmax_discharge": 50,
        "capacity": 100}},
      {{"uid": 2, "name": "phes", "bus": "b1",
        "input_efficiency": 0.9, "output_efficiency": 0.9,
        "annual_standing_loss": 0,
        "emin": 0, "emax": 5000, "eini": 1000,
        "pmax_charge": 30, "pmax_discharge": 30, "capacity": 5000,
        "daily_cycle": false, "use_state_variable": true}}]}}
}})",
      o.periodic_storage ? R"(, "periodic_storage": true)" : "",
      o.breaks ? R"(, "commitment_breaks": [3.5, 7.5, 10.5, 14.5, 17.5, 23.5])"
               : "",
      blocks,
      kNodes,
      kNodes,
      lmax);
}

struct Result
{
  double obj {};
  std::size_t ucells {};  ///< status columns of the commitment
  double bess_s {};
  double bess_efin {};
  double phes_s {};
  double phes_last {};
  double phes_efin {};
  double n {};
};

[[nodiscard]] Result run(const Options& o)
{
  Planning base;
  base.merge(parse_planning_json(make_json(o)));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  const auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto& scen = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  Result r {.obj = li.get_obj_value(), .n = stage.repetitions()};
  const auto* ucols =
      sys.elements<CommitmentLP>().front().find_status_cols(scen, stage);
  REQUIRE(ucols != nullptr);
  r.ucells = ucols->size();
  for (const auto& bat : sys.elements<BatteryLP>()) {
    const double s = bat.to_physical(sol[bat.eini_col_at(scen, stage)]);
    const double efin = bat.to_physical(sol[bat.efin_col_at(scen, stage)]);
    double last = s;
    for (const auto& [buid, col] : bat.energy_cols_at(scen, stage)) {
      last = bat.to_physical(sol[col]);
    }
    if (bat.object().name == "bess") {
      r.bess_s = s;
      r.bess_efin = efin;
    } else {
      r.phes_s = s;
      r.phes_last = last;
      r.phes_efin = efin;
    }
  }
  return r;
}

}  // namespace
}  // namespace commitment_breaks_periodic_storage_test

TEST_CASE(  // NOLINT
    "commitment_breaks + periodic_storage - one periodic point week")
{
  using namespace commitment_breaks_periodic_storage_test;
  const auto on = run({});
  CHECK(on.n == doctest::Approx(kWeight));

  // the daily grid: 7 days x 6 cells from Wednesday 12:00 plus the
  // piece before the first break (the 10:30-14:30 cell straddles the
  // period start: its two pieces keep separate u columns, the first with
  // v / w fixed to 0): 43 status values for 168 nodes; the storage
  // option does not touch it
  const auto off = run({.periodic_storage = false});
  CHECK(on.ucells == 43);
  CHECK(on.ucells == off.ucells);
  const auto hourly = run({.breaks = false});
  CHECK(hourly.ucells == static_cast<std::size_t>(kNodes));

  // cyclic battery: closes on itself
  CHECK(on.bess_efin == doctest::Approx(on.bess_s).epsilon(1e-6));
  // state battery: one real week, moved n times
  CHECK(on.phes_s == doctest::Approx(1000.0));
  CHECK(on.phes_efin
        == doctest::Approx(on.phes_s + (on.n * (on.phes_last - on.phes_s)))
               .epsilon(1e-6));

  // the coarser grid is a restriction of the hourly one
  CHECK(hourly.obj <= on.obj + 1e-6);
  CHECK(std::isfinite(off.obj));
}
