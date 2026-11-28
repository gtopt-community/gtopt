// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_line_losses_unrated.cpp
 * @brief     PWL line losses on a line with no flow rating
 *
 * A line with resistance but no `tmax` / capacity / `loss_envelope` has
 * no finite range for the loss PWL.  Spreading K segments over the
 * `DblMax` block cap overflowed the slopes to `inf` and the row scale
 * to 0, stamping NaN coefficients: CPLEX and HiGHS then returned
 * different "optimal" objectives for the same case (ldf_tiny, 0.15 %).
 * Such a line is now modelled lossless (with a warning); a rated line
 * keeps its losses.
 */

#include <cmath>
#include <format>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct Outcome
{
  bool optimal {};
  bool finite {true};
  double obj {};
};

Outcome solve_two_bus(std::string_view mode, std::string_view line_extra)
{
  const auto json = std::format(
      R"({{
  "options": {{
    "annual_discount_rate": 0.0,
    "model_options": {{
      "use_single_bus": false, "use_kirchhoff": false,
      "use_line_losses": true, "line_losses_mode": "{}",
      "loss_segments": 8, "loss_cost_eps": 0.0,
      "scale_objective": 1, "demand_fail_cost": 1000
    }}
  }},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]
  }},
  "system": {{
    "name": "unrated",
    "bus_array": [{{"uid": 1, "name": "a"}}, {{"uid": 2, "name": "b"}}],
    "generator_array": [
      {{"uid": 1, "name": "g", "bus": 1, "gcost": 50, "capacity": 100}}
    ],
    "demand_array": [{{"uid": 1, "name": "d", "bus": 2, "lmax": 5}}],
    "line_array": [
      {{"uid": 1, "name": "l", "bus_a": 1, "bus_b": 2, "voltage": 10,
        "resistance": 1.0, "reactance": 2.0{}}}
    ]
  }}
}})",
      mode,
      line_extra);
  auto planning = daw::json::from_json<Planning>(json, StrictParsePolicy);
  const PlanningOptionsLP options_lp(planning.options);
  SimulationLP simulation_lp(planning.simulation, options_lp);
  SystemLP system_lp(planning.system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  Outcome out;
  const auto nrows = static_cast<int>(lp.get_numrows());
  const auto ncols = static_cast<int>(lp.get_numcols());
  for (int i = 0; i < nrows; ++i) {
    for (int j = 0; j < ncols; ++j) {
      if (!std::isfinite(lp.get_coeff_raw(RowIndex {i}, ColIndex {j}))) {
        out.finite = false;
      }
    }
  }
  REQUIRE(lp.resolve().has_value());
  out.optimal = lp.is_optimal();
  out.obj = lp.get_obj_value_raw();
  return out;
}

}  // namespace

TEST_CASE("PWL losses on an unrated line stay finite")  // NOLINT
{
  for (const std::string_view mode :
       {
           "piecewise",
           "bidirectional",
           "tangent_signed_flow",
       })
  {
    CAPTURE(mode);
    {  // unrated: lossless, finite matrix
      const auto r = solve_two_bus(mode, "");
      CHECK(r.finite);
      REQUIRE(r.optimal);
      CHECK(r.obj == doctest::Approx(50.0 * 5.0));
    }
    {  // rated: losses kept
      const auto r = solve_two_bus(mode, R"(, "tmax_ab": 10, "tmax_ba": 10)");
      CHECK(r.finite);
      REQUIRE(r.optimal);
      // R·f²/V² = 1·25/100 = 0.25 MW of loss on top of the 5 MW load
      CHECK(r.obj > 50.0 * 5.2);
    }
    {  // unrated with loss_envelope: losses kept
      const auto r = solve_two_bus(mode, R"(, "loss_envelope": 10)");
      CHECK(r.finite);
      REQUIRE(r.optimal);
      CHECK(r.obj > 50.0 * 5.2);
    }
  }
}
