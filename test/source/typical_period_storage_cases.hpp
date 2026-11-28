// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      typical_period_storage_cases.hpp
 * @brief     Two-stage typical-period storage fixtures (battery,
 *            reservoir, LNG terminal) shared by the V2 / V3 tests
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Two stages, each a typical period of two real 1 h blocks repeated
 * `n` times (weighted durations n, n; `period_hours` 2).  Stage 1 is
 * cheap (thermal 1 $/MWh), stage 2 dear (100 $/MWh): a STATE storage
 * carries energy from stage 1 to stage 2; a cyclic one cannot.
 * `model_options.periodic_storage` is on unless the case turns it off.
 */

#pragma once

#include <cmath>
#include <format>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/reservoir_lp.hpp>

#include "typical_period_helpers.hpp"

namespace typical_period_test
{

/// Case knobs.
struct TwoStageCase
{
  double n {10.0};  ///< repetitions of the 2 h period per stage
  bool point {false};  ///< point-sampled stages (nodes 0.5 h, 1.5 h)
  bool phases {false};  ///< one phase per stage (SDDP)
  std::string_view method {"monolithic"};
  /// `model_options.periodic_storage` (off: the legacy rules)
  bool periodic_storage {true};
};

/// Extra `model_options` members of a case.
[[nodiscard]] inline std::string_view model_extra(const TwoStageCase& c)
{
  return c.periodic_storage ? R"(, "periodic_storage": true)" : "";
}

/// Planning JSON: `system_body` holds the element arrays (after the bus
/// and the demand / thermal entries built here from @p load2).
[[nodiscard]] inline std::string two_stage_json(const TwoStageCase& c,
                                                double load2,
                                                std::string_view gens_extra,
                                                std::string_view system_body)
{
  const auto blk = [&](int uid, double t)
  {
    return c.point
        ? std::format(
              R"({{"uid": {}, "duration": {}, "time": {}}})", uid, c.n, t)
        : std::format(R"({{"uid": {}, "duration": {}}})", uid, c.n);
  };
  const std::string_view sampling = c.point ? R"(, "sampling": "point")" : "";
  const std::string phases = c.phases
      ? R"(, "phase_array": [{"uid": 1, "first_stage": 0, "count_stage": 1},
                             {"uid": 2, "first_stage": 1, "count_stage": 1}])"
      : "";
  return std::format(
      R"({{ {},
  "simulation": {{
    "block_array": [{}, {}, {}, {}],
    "stage_array": [
      {{"uid": 1, "first_block": 0, "count_block": 2, "period_hours": 2 {}}},
      {{"uid": 2, "first_block": 2, "count_block": 2, "period_hours": 2 {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}] {}}},
  "system": {{
    "name": "tp_two_stage",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[10, 10], [{}, {}]]}}],
    "generator_array": [
      {{"uid": 9, "name": "thermal", "bus": "b1",
        "gcost": [[1, 1], [100, 100]], "capacity": 1000}} {}],
    {}
  }}
}})",
      options(model_extra(c), c.method),
      blk(1, 0.5),
      blk(2, 1.5),
      blk(3, 0.5),
      blk(4, 1.5),
      sampling,
      sampling,
      phases,
      load2,
      load2,
      gens_extra,
      system_body);
}

/// 100 MWh battery, η = 1, eini 0, load 10 / 10 MW.
[[nodiscard]] inline std::string battery_json(const TwoStageCase& c,
                                              std::string_view flags)
{
  return two_stage_json(
      c,
      10.0,
      "",
      std::format(
          R"("battery_array": [{{"uid": 1, "name": "bess", "bus": 1,
          "input_efficiency": 1, "output_efficiency": 1,
          "annual_standing_loss": 0,
          "emin": 0, "emax": 100, "eini": 0,
          "pmax_charge": 100, "pmax_discharge": 100,
          "capacity": 100 {}}}])",
          flags));
}

/// 150-unit reservoir, inflow 10 /h (fcr 1), turbine 1 MW per unit flow;
/// load 10 / 30 MW.
[[nodiscard]] inline std::string reservoir_json(const TwoStageCase& c,
                                                std::string_view flags)
{
  return two_stage_json(c,
                        30.0,
                        R"(, {"uid": 1, "name": "hyd", "bus": "b1", "gcost": 0,
            "capacity": 1000})",
                        std::format(
                            R"("junction_array": [{{"uid": 1, "name": "j1"}},
                       {{"uid": 2, "name": "j2", "drain": true}}],
    "waterway_array": [{{"uid": 1, "name": "ww1", "junction_a": 1,
                         "junction_b": 2, "fmin": 0, "fmax": 500}}],
    "flow_array": [{{"uid": 1, "name": "q_in", "direction": 1,
                     "junction": 1, "discharge": 10}}],
    "reservoir_array": [{{"uid": 1, "name": "rsv", "junction": 1,
                          "flow_conversion_rate": 1, "capacity": 150,
                          "emin": 0, "emax": 150, "eini": 0 {}}}],
    "turbine_array": [{{"uid": 1, "name": "tur", "waterway": 1,
                        "generator": 1, "production_factor": 1.0}}])",
                            flags));
}

/// 150 m³ LNG tank, 200 m³ delivered in stage 1, gas unit (heat rate
/// 1 m³/MWh, 0 $); load 10 / 30 MW.
[[nodiscard]] inline std::string lng_json(const TwoStageCase& c,
                                          std::string_view flags)
{
  return two_stage_json(c,
                        30.0,
                        R"(, {"uid": 1, "name": "gas", "bus": "b1", "gcost": 0,
            "capacity": 1000})",
                        std::format(
                            R"("lng_terminal_array": [{{"uid": 1, "name": "lng",
          "emin": 0, "emax": 150, "eini": 0, "delivery": [200, 0],
          "generators": [{{"generator": 1, "heat_rate": 1}}] {}}}])",
                            flags));
}

/// Stage-level solution of the storage element of type @p StorageLPT.
struct StageLevels
{
  double eini {};  ///< start level s
  double efin {};  ///< stage-end level
  std::vector<double> x;  ///< one-cycle levels per block
};

struct TwoStageSolution
{
  double obj {};
  std::vector<StageLevels> stages;
  bool efin_is_state {};  ///< an `efin` state variable is registered
};

template<typename StorageLPT>
[[nodiscard]] TwoStageSolution solve_two_stage(const std::string& json)
{
  gtopt::Planning base;
  base.merge(gtopt::parse_planning_json(json));
  gtopt::PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& st = sys.template elements<StorageLPT>().front();
  TwoStageSolution out;
  out.obj = li.get_obj_value();
  for (const auto& stage : sys.phase().stages()) {
    StageLevels lv;
    lv.eini = st.to_physical(sol[st.eini_col_at(scenario, stage)]);
    lv.efin = st.to_physical(sol[st.efin_col_at(scenario, stage)]);
    for (const auto& [buid, col] : st.energy_cols_at(scenario, stage)) {
      lv.x.push_back(st.to_physical(sol[col]));
    }
    out.stages.push_back(std::move(lv));
  }
  for (const auto& [key, svar] : plp.simulation().state_variables(
           gtopt::first_scene_index(), gtopt::PhaseIndex {0}))
  {
    if (key.col_name == "efin") {
      out.efin_is_state = true;
    }
  }
  return out;
}

/// State-storage invariants on one stage: efin = s + n·(x_{N−1} − s)
/// and every repetition j ∈ {0, ⌈n⌉ − 1} within [emin, emax].
inline void check_state_stage(const StageLevels& lv,
                              double n,
                              double emin,
                              double emax)
{
  REQUIRE_FALSE(lv.x.empty());
  const double delta = lv.x.back() - lv.eini;
  CHECK(lv.efin == doctest::Approx(lv.eini + (n * delta)));
  const double last_rep = std::ceil(n - 1e-9) - 1.0;
  for (const double x : lv.x) {
    CHECK(x >= emin - 1e-6);
    CHECK(x <= emax + 1e-6);
    CHECK(x + (last_rep * delta) >= emin - 1e-6);
    CHECK(x + (last_rep * delta) <= emax + 1e-6);
  }
  CHECK(lv.efin >= emin - 1e-6);
  CHECK(lv.efin <= emax + 1e-6);
}

/// 2-phase SDDP of @p json (built with `phases = true`, method sddp):
/// LB = UB = the monolithic objective, and the phase-0 `efin` state
/// variable of the storage holds the stage-END level s + n·Δ (the
/// monolithic stage-1 `efin`), not the last block's one-cycle level.
template<typename StorageLPT>
inline void check_sddp_matches(const std::string& json,
                               const TwoStageSolution& mono,
                               std::string_view class_name)
{
  REQUIRE(mono.stages.size() == 2);
  const auto& stage1 = mono.stages.front();
  REQUIRE_FALSE(stage1.x.empty());
  gtopt::Planning base;
  base.merge(gtopt::parse_planning_json(json));
  gtopt::PlanningLP plp(std::move(base));
  const auto [lb, ub] = solve_sddp_bounds(plp);
  CHECK(lb == doctest::Approx(mono.obj).epsilon(1e-6));
  CHECK(ub == doctest::Approx(mono.obj).epsilon(1e-6));

  bool found = false;
  for (const auto& [key, svar] : plp.simulation().state_variables(
           gtopt::first_scene_index(), gtopt::PhaseIndex {0}))
  {
    if (key.col_name == "efin" && key.class_name == class_name) {
      found = true;
      CHECK(svar.col_sol_physical()
            == doctest::Approx(stage1.efin).epsilon(1e-6));
      CHECK_FALSE(svar.col_sol_physical()
                  == doctest::Approx(stage1.x.back()).epsilon(1e-6));
    }
  }
  CHECK(found);
}

}  // namespace typical_period_test
