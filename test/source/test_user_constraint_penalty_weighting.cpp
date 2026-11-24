// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// UserConstraint soft-penalty weighting (add_to_lp audit, P0-16).
//
// The slack of a soft UserConstraint (`penalty` set) is priced like every
// other cost in the objective: `penalty` is $/unit per hour of violation,
// weighted by scenario probability × stage discount × block duration
// (`CostHelper::block_ecost`).  Rows that span more than one block (a
// `daily_sum` day row, a `stage` / `phase` / `global` scope row) are
// already span totals and carry probability × discount
// (`CostHelper::cost_factor`).  Before the fix the slack carried the raw
// `penalty` (no probability, no discount, no duration), so with S
// equiprobable scenarios a violation weighed S times more than in a
// single-scenario run, and a 4 h block weighed it 4 times less than the
// energy cost next to it.

#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_system.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/user_constraint_lp.hpp>

using namespace gtopt;

namespace
{

// g1 ($10) must serve 100 MW; `g1 ≤ 60` is soft at $50/MW·h; g2 costs
// $200, so the cap is violated by 40 MW in every scenario.
constexpr std::string_view ucpw_uc_soft_json = R"({
   "name":"uc_soft","bus_array":[{"uid":1,"name":"b1"}],
   "generator_array":[
    {"uid":1,"name":"g1","bus":"b1","pmax":200,"capacity":200,"gcost":10},
    {"uid":2,"name":"g2","bus":"b1","pmax":200,"capacity":200,
     "gcost":200}],
   "demand_array":[{"uid":1,"name":"d1","bus":"b1","lmax":100}],
   "user_constraint_array":[{"uid":1,"name":"cap",
     "expression":"generator('g1').generation <= 60","penalty":50}]})";

struct UcpwCase
{
  int nscenarios {1};
  double duration {1.0};
  double discount {1.0};
};

Simulation ucpw_make_simulation(const UcpwCase& c)
{
  Simulation sim {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = c.duration,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
                  .discount_factor = c.discount,
              },
          },
  };
  for (int s = 0; s < c.nscenarios; ++s) {
    sim.scenario_array.push_back({
        .uid = Uid {s + 1},
        .probability_factor = 1.0 / c.nscenarios,
    });
  }
  return sim;
}

PlanningOptionsLP ucpw_make_options()
{
  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.use_single_bus = true;
  return PlanningOptionsLP {popts};
}

struct UcpwResult
{
  double obj {0.0};
  std::vector<double> duals;  ///< UserConstraint `constraint` dual readback
  std::vector<double> slack;  ///< UserConstraint `slack` primal readback
};

std::vector<double> ucpw_field_values(const OutputContext& oc,
                                      std::string_view field,
                                      std::string_view kind)
{
  const OutputContext::ClassFieldName key {"UserConstraint", field, kind};
  std::vector<double> out;
  const auto it = oc.fields().find(key);
  if (it == oc.fields().end()) {
    return out;
  }
  for (const auto& entry : it->second) {
    for (const auto& [slot, value] : entry.entries) {
      out.push_back(value);
    }
  }
  return out;
}

UcpwResult ucpw_solve(const UcpwCase& c)
{
  const auto simulation = ucpw_make_simulation(c);
  const auto options = ucpw_make_options();
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(daw::json::from_json<System>(ucpw_uc_soft_json),
                     simulation_lp);
  auto&& li = system_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  OutputContext oc(system_lp.system_context(),
                   li,
                   system_lp.scene().uid(),
                   system_lp.phase().uid(),
                   system_lp.phase().is_continuous());
  REQUIRE(system_lp.elements<UserConstraintLP>().front().add_to_output(oc));
  return {
      .obj = li.get_obj_value(),
      .duals = ucpw_field_values(oc, UserConstraintLP::ConstraintName, "dual"),
      .slack = ucpw_field_values(oc, UserConstraintLP::SlackName, "sol"),
  };
}

/// Hand value: Σ_s p_s · disc · Δt · (10·100 + 50·40) = disc · Δt · 3000.
double ucpw_expected_obj(const UcpwCase& c)
{
  return c.discount * c.duration * 3000.0;
}

}  // namespace

TEST_CASE("UserConstraint soft penalty is weighted like every cost")  // NOLINT
{
  SUBCASE("single scenario, 1 h, undiscounted: unchanged (PCP-like)")
  {
    // Raw penalty and weighted penalty coincide: 1000 + 2000.
    const UcpwCase c {};
    CHECK(ucpw_solve(c).obj == doctest::Approx(3000.0));
  }

  SUBCASE("two equiprobable scenarios")
  {
    // Weighted: Σ_s 0.5 · 3000 = 3000.  Raw (before): 1000 + 2·2000.
    const UcpwCase c {.nscenarios = 2};
    CHECK(ucpw_solve(c).obj == doctest::Approx(ucpw_expected_obj(c)));
  }

  SUBCASE("4 h block")
  {
    // 4·1000 + 4·2000 = 12000.  Raw (before): 4000 + 2000.
    const UcpwCase c {.duration = 4.0};
    CHECK(ucpw_solve(c).obj == doctest::Approx(ucpw_expected_obj(c)));
  }

  SUBCASE("discounted stage")
  {
    // 0.5 · 3000 = 1500.  Raw (before): 500 + 2000.
    const UcpwCase c {.discount = 0.5};
    CHECK(ucpw_solve(c).obj == doctest::Approx(ucpw_expected_obj(c)));
  }

  SUBCASE("two scenarios p = 0.5, 4 h block, discount 0.25")
  {
    // 0.25 · 4 · 3000 = 3000.  Raw (before): 1000 + 2·2000 = 5000, and the
    // soft-row dual read back as 100 (= 50 / (0.5·0.25·4)).
    const UcpwCase c {.nscenarios = 2, .duration = 4.0, .discount = 0.25};
    const auto r = ucpw_solve(c);
    CHECK(r.obj == doctest::Approx(ucpw_expected_obj(c)));

    // Output consistency: the binding soft row reads back at the
    // physical penalty (50 $/MW·h) in every scenario — the same basis as
    // a generator cost — and the slack at the 40 MW violation.
    REQUIRE(r.duals.size() == 2);
    for (const double d : r.duals) {
      CHECK(std::abs(d) == doctest::Approx(50.0));
    }
    REQUIRE(r.slack.size() == 2);
    for (const double s : r.slack) {
      CHECK(s == doctest::Approx(40.0));
    }
  }
}
