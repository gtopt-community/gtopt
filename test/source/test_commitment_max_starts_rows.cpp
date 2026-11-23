/**
 * @file      test_commitment_max_starts_rows.cpp
 * @brief     CommitmentLP max_starts / min_starts window rows (C9)
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_commitment.cpp` only round-trips the `max_starts` / `min_starts`
 * JSON fields.  These tests pin the LP side: how many window rows each
 * `starts_scope` emits, that a zero `min_starts` floor is skipped, and
 * that a `max_starts = 0` cap actually blocks a start-up.  Helpers are
 * prefixed `cms_` for unity builds.
 */

#include <optional>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/commitment.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

struct CmsCase
{
  System system;
  Simulation simulation;
};

/// One bus, one 100 MW demand, a cheap committed unit g1 and an
/// expensive non-committed unit g2, four 1-hour chronological blocks.
/// g1's 20 MW floor lives on the Commitment (conditional on u), not on
/// the Generator (an always-on floor that would make u = 0 infeasible).
[[nodiscard]] auto cms_make_case() -> CmsCase
{
  CmsCase tc;
  tc.system.name = "cms";
  tc.system.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  tc.system.demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 100.0,
      },
  };
  tc.system.generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .pmax = 100.0,
          .gcost = 10.0,
          .capacity = 100.0,
      },
      {
          .uid = Uid {2},
          .name = "g2",
          .bus = Uid {1},
          .pmax = 100.0,
          .gcost = 50.0,
          .capacity = 100.0,
      },
  };
  tc.simulation.block_array = {
      {
          .uid = Uid {0},
          .duration = 1.0,
      },
      {
          .uid = Uid {1},
          .duration = 1.0,
      },
      {
          .uid = Uid {2},
          .duration = 1.0,
      },
      {
          .uid = Uid {3},
          .duration = 1.0,
      },
  };
  tc.simulation.stage_array = {
      {
          .uid = Uid {0},
          .first_block = 0,
          .count_block = 4,
          .chronological = true,
      },
  };
  tc.simulation.scenario_array = {
      {
          .uid = Uid {0},
      },
  };
  return tc;
}

struct CmsResult
{
  int rows {0};
  double obj {0.0};
};

[[nodiscard]] auto cms_build(OptInt max_starts,
                             OptInt min_starts,
                             OptStartsScope scope) -> CmsResult
{
  auto tc = cms_make_case();
  tc.system.commitment_array = {
      {
          .uid = Uid {1},
          .name = "g1_uc",
          .generator = Uid {1},
          .startup_cost = 1.0,
          .pmin = 20.0,
          .initial_status = 0.0,
          .relax = true,
          .max_starts = max_starts,
          .min_starts = min_starts,
          .starts_scope = std::move(scope),
      },
  };

  PlanningOptions popts;
  popts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(tc.simulation, options);
  SystemLP system_lp(tc.system, simulation_lp);
  auto&& li = system_lp.linear_interface();

  CmsResult res {
      .rows = static_cast<int>(li.get_numrows()),
  };
  const auto status = li.resolve();
  if (!status.has_value()) {
    MESSAGE(status.error().message);
  }
  REQUIRE(status.has_value());
  REQUIRE(status.value() == 0);
  res.obj = li.get_obj_value();
  return res;
}

}  // namespace

TEST_CASE("CommitmentLP max_starts window rows per starts_scope")  // NOLINT
{
  const auto base = cms_build(std::nullopt, std::nullopt, std::nullopt);

  SUBCASE("horizon (unset scope) adds one row per stage")
  {
    CHECK(cms_build(2, std::nullopt, std::nullopt).rows == base.rows + 1);
  }

  SUBCASE("named horizon scope matches the unset default")
  {
    CHECK(cms_build(2, std::nullopt, StartsScopeValue {Name {"horizon"}}).rows
          == base.rows + 1);
  }

  SUBCASE("hour scope adds one row per 1-hour block")
  {
    CHECK(cms_build(1, std::nullopt, StartsScopeValue {Name {"hour"}}).rows
          == base.rows + 4);
  }

  SUBCASE("integer 2-hour scope splits the stage into two windows")
  {
    CHECK(cms_build(1, std::nullopt, StartsScopeValue {Int {2}}).rows
          == base.rows + 2);
  }

  SUBCASE("integer 3-hour scope flushes the partial tail window")
  {
    // Windows {0,1,2} and the stage-end remainder {3}.
    CHECK(cms_build(1, std::nullopt, StartsScopeValue {Int {3}}).rows
          == base.rows + 2);
  }

  SUBCASE("min_starts and max_starts share the window LHS")
  {
    CHECK(cms_build(3, 1, std::nullopt).rows == base.rows + 2);
    CHECK(cms_build(3, 1, StartsScopeValue {Int {2}}).rows == base.rows + 4);
  }

  SUBCASE("min_starts alone emits only the floor row")
  {
    CHECK(cms_build(std::nullopt, 1, std::nullopt).rows == base.rows + 1);
  }

  SUBCASE("a zero min_starts floor is never emitted")
  {
    CHECK(cms_build(std::nullopt, 0, std::nullopt).rows == base.rows);
    CHECK(cms_build(2, 0, std::nullopt).rows == base.rows + 1);
  }
}

TEST_CASE("CommitmentLP max_starts = 0 blocks the start-up")  // NOLINT
{
  // g1 starts offline (initial_status = 0).  Without a cap the cheap
  // unit is started; with `max_starts = 0` the startup column is pinned
  // to 0 through the window row, u stays 0 and the expensive g2 serves
  // the whole load.
  const auto free_run = cms_build(std::nullopt, std::nullopt, std::nullopt);
  const auto capped = cms_build(0, std::nullopt, std::nullopt);
  CHECK(capped.obj > free_run.obj + 1.0);
  // 4 h × 100 MW at g2's 50 $/MWh.
  CHECK(capped.obj == doctest::Approx(20000.0));
  // Generous cap is non-binding.
  const auto loose = cms_build(4, std::nullopt, std::nullopt);
  CHECK(loose.obj == doctest::Approx(free_run.obj));
}

TEST_CASE("CommitmentLP min_starts forces a start-up")  // NOLINT
{
  // g1 is now dearer than g2 and its startup is costly, so a floor of
  // one start per stage must raise the objective over the free run.
  auto tc = cms_make_case();
  tc.system.generator_array[0].gcost = 60.0;  // g1 now dearer than g2
  auto run = [&tc](OptInt min_starts) -> double
  {
    tc.system.commitment_array = {
        {
            .uid = Uid {1},
            .name = "g1_uc",
            .generator = Uid {1},
            .startup_cost = 500.0,
            .pmin = 20.0,
            .initial_status = 0.0,
            .relax = true,
            .min_starts = min_starts,
        },
    };
    PlanningOptions popts;
    popts.model_options.demand_fail_cost = 1000.0;
    const PlanningOptionsLP options(popts);
    SimulationLP simulation_lp(tc.simulation, options);
    SystemLP system_lp(tc.system, simulation_lp);
    auto&& li = system_lp.linear_interface();
    const auto status = li.resolve();
    REQUIRE(status.has_value());
    REQUIRE(status.value() == 0);
    return li.get_obj_value();
  };
  const double free_obj = run(std::nullopt);
  const double forced_obj = run(1);
  CHECK(free_obj == doctest::Approx(20000.0));
  // One start (500 $) plus at least one block at g1's floor.
  CHECK(forced_obj >= free_obj + 500.0 - 1e-6);
}
