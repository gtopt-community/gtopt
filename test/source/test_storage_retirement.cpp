// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Retirement of state-carrying storage: an element with a state variable
// may be commissioned once and retired once (`active` = off* on* off*).
// At retirement its state is dropped — no state link, `efin` row or SDDP
// dependent column is created in the inactive stages / phases — and the
// LP (monolithic and SDDP) must build and solve.

#include <algorithm>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/validate_planning.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

[[nodiscard]] bool retire_has_state_error(const ValidationResult& r)
{
  return std::ranges::any_of(r.errors,
                             [](const auto& e)
                             { return e.contains("state-carrying storage"); });
}

/// `make_3phase_hydro_planning` (reservoir `rsv1`, 3 one-day stages)
/// with the reservoir active in stages 1-2 only and an `efin` target
/// that would bind in the (inactive) last stage.
[[nodiscard]] Planning retire_hydro_planning(bool single_phase)
{
  auto planning = make_3phase_hydro_planning();
  if (single_phase) {
    planning.simulation.phase_array.clear();
  }
  auto& rsv = planning.system.reservoir_array.front();
  rsv.active = std::vector<IntBool> {True, True, False};
  rsv.efin = 400.0;
  return planning;
}

// Stages 1-2: 4800 MWh of load; the reservoir's 250 + 2 × 240 inflow =
// 730 units are all turbined (the water left at retirement is worth
// nothing) at 5 $/MWh, the rest is thermal at 50.  Stage 3: run of river
// (10 MW) + 90 MW thermal for 24 h.  The ignored `efin = 400` would
// otherwise force storing water.
constexpr double kRetireHydroCost = (730.0 * 5.0) + ((4800.0 - 730.0) * 50.0)
    + (24.0 * ((10.0 * 5.0) + (90.0 * 50.0)));

}  // namespace

TEST_CASE("Battery with state retired in the last stage solves")  // NOLINT
{
  // Stage 1: the battery shifts 10 MWh from the cheap block to the
  // block without cheap energy (cost 20 MWh × 10).  Stage 2: retired —
  // no efin row, so `efin = 20` does not apply; block 4 is served by
  // the peaker.
  constexpr std::string_view json = R"({
    "options": {"annual_discount_rate": 0.0, "output_format": "csv",
                "model_options": {"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1}},
    "simulation": {
      "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1},
                      {"uid": 3, "duration": 1}, {"uid": 4, "duration": 1}],
      "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2},
                      {"uid": 2, "first_block": 2, "count_block": 2}],
      "scenario_array": [{"uid": 1, "probability_factor": 1}]},
    "system": {
      "name": "retired_battery",
      "bus_array": [{"uid": 1, "name": "b1"}],
      "demand_array": [{"uid": 1, "name": "d1", "bus": "b1",
                        "lmax": [[10, 10], [10, 10]]}],
      "generator_array": [
        {"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10,
         "pmax": [[100, 0], [100, 0]], "capacity": 100},
        {"uid": 2, "name": "peaker", "bus": "b1", "gcost": 100,
         "capacity": 100}],
      "battery_array": [{"uid": 1, "name": "bat", "bus": "b1",
        "active": [1, 0], "daily_cycle": false, "use_state_variable": true,
        "annual_standing_loss": 0, "eini": 0, "efin": 20,
        "input_efficiency": 1, "output_efficiency": 1,
        "emin": 0, "emax": 50, "capacity": 50,
        "pmax_charge": 50, "pmax_discharge": 50}]}
  })";
  Planning base;
  base.merge(parse_planning_json(json));
  CHECK_FALSE(retire_has_state_error(validate_planning(base)));

  PlanningLP planning_lp(std::move(base));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 1);
  CHECK(planning_lp.systems().front().front().linear_interface().get_obj_value()
        == doctest::Approx(200.0 + 100.0 + 1000.0));
}

TEST_CASE("Reservoir retired mid-horizon: monolithic")  // NOLINT
{
  SUBCASE("on -> off: water left at retirement has no value")
  {
    auto planning = retire_hydro_planning(/*single_phase=*/true);
    CHECK_FALSE(retire_has_state_error(validate_planning(planning)));
    PlanningLP planning_lp(std::move(planning));
    auto result = planning_lp.resolve();
    REQUIRE(result.has_value());
    CHECK(
        planning_lp.systems().front().front().linear_interface().get_obj_value()
        == doctest::Approx(kRetireHydroCost));
  }
  SUBCASE("off -> on -> off: commissioned and retired")
  {
    auto planning = retire_hydro_planning(/*single_phase=*/true);
    planning.system.reservoir_array.front().active =
        std::vector<IntBool> {False, True, False};
    CHECK_FALSE(retire_has_state_error(validate_planning(planning)));
    PlanningLP planning_lp(std::move(planning));
    auto result = planning_lp.resolve();
    REQUIRE(result.has_value());
  }
}

TEST_CASE("Reservoir retired at an SDDP phase boundary")  // NOLINT
{
  // Phase 2's efin StateVariable has no dependent in phase 3 (the
  // reservoir is inactive there): the forward pass propagates nothing,
  // the cut on phase 2 carries no reservoir coefficient, and SDDP
  // converges to the monolithic optimum.
  auto planning = retire_hydro_planning(/*single_phase=*/false);
  CHECK_FALSE(retire_has_state_error(validate_planning(planning)));
  PlanningLP planning_lp(std::move(planning));

  SDDPOptions sddp_opts;
  sddp_opts.max_iterations = 20;
  sddp_opts.convergence_tol = 1e-6;
  sddp_opts.enable_api = false;
  SDDPMethod sddp(planning_lp, sddp_opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  const auto& last = results->back();
  CHECK(last.upper_bound == doctest::Approx(kRetireHydroCost).epsilon(1e-6));
  CHECK(last.lower_bound == doctest::Approx(kRetireHydroCost).epsilon(1e-6));

  // No dependent of any phase-2 reservoir state lands in phase 3.
  const auto& sim = planning_lp.simulation();
  for (const auto& [key, svar] :
       sim.state_variables(first_scene_index(), PhaseIndex {1}))
  {
    if (key.class_name == "Reservoir") {
      CHECK(svar.dependent_variables().empty());
    }
  }
}
