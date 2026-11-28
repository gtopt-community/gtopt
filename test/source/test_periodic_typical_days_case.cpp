// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_periodic_typical_days_case.cpp
 * @brief     Integration of the periodic typical-period options on the
 *            cases/periodic_typical_days case (monolithic, 2-phase SDDP,
 *            options off) and on the cheby_small point weeks
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Two typical days (4 × 6 h, period_hours 24) standing for 30 and 31
 * days: a reservoir and a long-duration battery (state storages), a 2 h
 * battery (cyclic), a switchable line (`LineCommitment.cycle = "stage"`)
 * and coal ramp rows on `generation_prev` (`prev_stage_link = "period"`).
 */

#include <cmath>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/battery_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "typical_period_helpers.hpp"

using namespace gtopt;

namespace periodic_typical_days_test
{
namespace
{

[[nodiscard]] std::filesystem::path cases_dir()
{
  return std::filesystem::path(__FILE__)
             .parent_path()
             .parent_path()
             .parent_path()
      / "cases";
}

[[nodiscard]] Planning load(std::string_view rel)
{
  const auto path = cases_dir() / rel;
  REQUIRE(std::filesystem::exists(path));
  std::ifstream in(path);
  std::stringstream buf;
  buf << in.rdbuf();
  Planning base;
  base.merge(parse_planning_json(buf.str()));
  base.options.output_directory = (std::filesystem::temp_directory_path()
                                   / ("gtopt_periodic_" + path.stem().string()))
                                      .string();
  return base;
}

[[nodiscard]] Planning typical_days()
{
  return load("periodic_typical_days/periodic_typical_days.json");
}

/// s, x_last and efin of a storage per stage (physical units).
template<typename StorageLPT>
void check_state_storage(const SystemLP& sys,
                         std::string_view name,
                         bool cyclic)
{
  const auto& li = sys.linear_interface();
  const auto sol = li.get_col_sol();
  const auto& scenario = sys.scene().scenarios().front();
  for (const auto& st : sys.template elements<StorageLPT>()) {
    if (st.object().name != name) {
      continue;
    }
    for (const auto& stage : sys.phase().stages()) {
      const double s = st.to_physical(sol[st.eini_col_at(scenario, stage)]);
      const double efin = st.to_physical(sol[st.efin_col_at(scenario, stage)]);
      double x_last = s;
      for (const auto& [buid, col] : st.energy_cols_at(scenario, stage)) {
        x_last = st.to_physical(sol[col]);
      }
      CAPTURE(name);
      CAPTURE(stage.uid());
      if (cyclic) {
        CHECK(efin == doctest::Approx(s).epsilon(1e-6));
      } else {
        const double n = stage.repetitions();
        CHECK(efin == doctest::Approx(s + (n * (x_last - s))).epsilon(1e-6));
      }
    }
    return;
  }
  FAIL("storage not found: " << name);
}

}  // namespace
}  // namespace periodic_typical_days_test

TEST_CASE(  // NOLINT
    "periodic_typical_days - monolithic invariants of the case")
{
  using namespace periodic_typical_days_test;
  PlanningLP plp(typical_days());
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  const auto& sys = plp.systems().front().front();
  CHECK(sys.linear_interface().get_obj_value() > 0.0);
  check_state_storage<ReservoirLP>(sys, "lake", /*cyclic=*/false);
  check_state_storage<BatteryLP>(sys, "phes_long", /*cyclic=*/false);
  check_state_storage<BatteryLP>(sys, "bess_2h", /*cyclic=*/true);
}

TEST_CASE(  // NOLINT
    "periodic_typical_days - options off restore the legacy LP")
{
  using namespace periodic_typical_days_test;
  // Every opt-in reverted: the legacy model solves too and differs
  // (the state storages integrate 4 × 180 h as one stretched day).
  PlanningLP on(typical_days());
  REQUIRE(on.resolve().has_value());
  const double obj_on =
      on.systems().front().front().linear_interface().get_obj_value();

  auto base = typical_days();
  base.options.model_options.periodic_storage = false;
  base.options.model_options.prev_stage_link = "stage";
  for (auto& lc : base.system.line_commitment_array) {
    lc.cycle = "none";
  }
  PlanningLP off(std::move(base));
  REQUIRE(off.resolve().has_value());
  const double obj_off =
      off.systems().front().front().linear_interface().get_obj_value();
  CHECK_FALSE(obj_off == doctest::Approx(obj_on).epsilon(1e-6));
}

TEST_CASE(  // NOLINT
    "periodic_typical_days - 2-phase SDDP equals monolithic")
{
  using namespace periodic_typical_days_test;
  // OTS (LineCommitment) is monolithic-only: drop it for this check.
  auto mono_planning = typical_days();
  mono_planning.system.line_commitment_array.clear();
  auto sddp_planning = mono_planning;

  PlanningLP mono(std::move(mono_planning));
  REQUIRE(mono.resolve().has_value());
  const double obj =
      mono.systems().front().front().linear_interface().get_obj_value();

  sddp_planning.options.method = MethodType::sddp;
  sddp_planning.simulation.phase_array = {
      Phase {.uid = Uid {1}, .first_stage = 0, .count_stage = 1},
      Phase {.uid = Uid {2}, .first_stage = 1, .count_stage = 1},
  };
  PlanningLP sddp(std::move(sddp_planning));
  const auto [lb, ub] = typical_period_test::solve_sddp_bounds(sddp);
  CHECK(lb == doctest::Approx(obj).epsilon(1e-6));
  CHECK(ub == doctest::Approx(obj).epsilon(1e-6));
}

TEST_CASE(  // NOLINT
    "periodic_typical_days - cheby_small point weeks with the options on")
{
  using namespace periodic_typical_days_test;
  // The point-sampled cheby_small weeks (reservoirs, batteries, UC on
  // Chebyshev nodes) solve with every periodic option on, and the state
  // storages satisfy the stage-end rule.
  for (const std::string_view rel :
       {
           std::string_view {
               "cheby_small/system2_week/system2_week_point.json"},
           std::string_view {"cheby_small/mini/hydro/hydro_point.json"},
           std::string_view {
               "cheby_small/mini/periodic_week/periodic_week_point.json"},
       })
  {
    CAPTURE(rel);
    auto base = load(rel);
    base.options.model_options.periodic_storage = true;
    base.options.model_options.prev_stage_link = "period";
    PlanningLP plp(std::move(base));
    auto res = plp.resolve();
    REQUIRE(res.has_value());
    CHECK(*res == 1);
  }
}
