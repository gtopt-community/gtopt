// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// UB net of the water-rights agreement rewards.
//
// Agreement valuations (a FlowRight / VolumeRight with positive
// `uvalue`, e.g. PLP's ValorRiego on delivered irrigation) enter the
// objective as negative costs, so the SDDP upper bound mixes real
// operating cost with a valuation.  `SystemLP::agreement_rewards_
// objective()` isolates those terms and SDDP reports
// `upper_bound_net = upper_bound - agreement_rewards` next to the raw UB.
#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_options.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

// One bus, 50 MW demand, free hydro (rsv -> j_up -> ww (pf 2) -> drain)
// vs 100 $/MWh thermal.  One 24 h block, plenty of water.
[[nodiscard]] System make_rewards_system(Array<FlowRight> frs)
{
  return {
      .name = "agreement_rewards",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = 50.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "hydro",
                  .bus = Uid {1},
                  .gcost = 0.0,
                  .capacity = 200.0,
              },
              {
                  .uid = Uid {2},
                  .name = "thermal",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 200.0,
              },
          },
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_up",
              },
              {
                  .uid = Uid {2},
                  .name = "j_down",
                  .drain = true,
              },
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 200.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv",
                  .junction = Uid {1},
                  .capacity = 1000.0,
                  .emin = 0.0,
                  .emax = 1000.0,
                  .eini = 500.0,
              },
          },
      .turbine_array =
          {
              {
                  .uid = Uid {1},
                  .name = "tur",
                  .waterway = Uid {1},
                  .generator = Uid {1},
                  .production_factor = 2.0,
              },
          },
      .flow_right_array = std::move(frs),
  };
}

[[nodiscard]] Simulation make_day()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 24,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

struct RewardResult
{
  double rewards {0.0};
  double obj {0.0};
};

[[nodiscard]] RewardResult solve_rewards(const System& system)
{
  PlanningOptions popts;
  popts.model_options.scale_objective = 1.0;
  popts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options(std::move(popts));
  const auto sim = make_day();
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(system, simulation_lp);
  auto& lp = system_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(lp.is_optimal());
  return {
      .rewards = system_lp.agreement_rewards_objective(),
      .obj = lp.get_obj_value(),
  };
}

}  // namespace

TEST_CASE("SystemLP::agreement_rewards_objective isolates right rewards")
{
  SUBCASE("no rewarded right -> 0")
  {
    const auto r = solve_rewards(make_rewards_system({}));
    CHECK(r.rewards == doctest::Approx(0.0));
  }

  SUBCASE("target-0 reward: the whole delivered flow is valued")
  {
    // 2 m3/s withdrawn at j_up, valued 1100 $/(m3/s.h) over 24 h.
    const auto r = solve_rewards(make_rewards_system({
        {
            .uid = Uid {1},
            .name = "q_riego",
            .junction_a = Uid {1},
            .direction = -1,
            .fmax = 2.0,
            .target = 0.0,
            .uvalue = 1100.0,
        },
    }));
    CHECK(r.rewards == doctest::Approx(-1100.0 * 2.0 * 24.0));
    // Hydro serves the load for free: the objective IS the reward.
    CHECK(r.obj == doctest::Approx(r.rewards));
  }

  SUBCASE("uvalue-only kink: only the flow beyond the target is a reward")
  {
    const auto r = solve_rewards(make_rewards_system({
        {
            .uid = Uid {1},
            .name = "q_riego",
            .junction_a = Uid {1},
            .direction = -1,
            .fmax = 2.0,
            .target = 0.5,
            .uvalue = 1100.0,
        },
    }));
    CHECK(r.rewards == doctest::Approx(-1100.0 * (2.0 - 0.5) * 24.0));
  }

  SUBCASE("fcost-only kink is a shortfall penalty, not a reward")
  {
    const auto r = solve_rewards(make_rewards_system({
        {
            .uid = Uid {1},
            .name = "q_req",
            .junction_a = Uid {1},
            .direction = -1,
            .fmax = 5.0,
            .target = 5.0,
            .fcost = 100.0,
        },
    }));
    CHECK(r.rewards == doctest::Approx(0.0));
  }

  SUBCASE("usage cost (negative uvalue) is a cost, not a reward")
  {
    const auto r = solve_rewards(make_rewards_system({
        {
            .uid = Uid {1},
            .name = "q_use",
            .junction_a = Uid {1},
            .direction = -1,
            .fmax = 2.0,
            .target = 0.0,
            .uvalue = -10.0,
        },
    }));
    CHECK(r.rewards == doctest::Approx(0.0));
  }
}

TEST_CASE("SDDP reports the UB net of agreement rewards")
{
  const auto run = [](bool with_reward)
  {
    auto planning = make_3phase_hydro_planning();
    if (with_reward) {
      planning.system.flow_right_array = {
          {
              .uid = Uid {1},
              .name = "q_riego",
              .junction_a = Uid {1},
              .direction = -1,
              .fmax = 1.0,
              .target = 0.0,
              // Above any water value of the fixture, so the right
              // always withdraws its fmax and the reward is non-zero.
              .uvalue = 1.0e4,
          },
      };
    }
    PlanningLP planning_lp(std::move(planning));
    SDDPOptions opts;
    opts.max_iterations = 3;
    opts.convergence_tol = 1e-6;
    SDDPMethod sddp(planning_lp, opts);
    auto results = sddp.solve();
    REQUIRE(results.has_value());
    REQUIRE_FALSE(results->empty());
    return results->back();
  };

  SUBCASE("no rewarded right: UB_net == UB")
  {
    const auto ir = run(false);
    CHECK(ir.agreement_rewards == doctest::Approx(0.0));
    CHECK(ir.upper_bound_net == doctest::Approx(ir.upper_bound));
  }

  SUBCASE("rewarded right: UB_net = UB - rewards > UB")
  {
    const auto ir = run(true);
    CHECK(ir.agreement_rewards < 0.0);
    CHECK(ir.upper_bound_net
          == doctest::Approx(ir.upper_bound - ir.agreement_rewards));
    CHECK(ir.upper_bound_net > ir.upper_bound);
  }
}
