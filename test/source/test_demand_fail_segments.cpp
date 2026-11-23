// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Tests for the native stepwise demand-failure (VoLL) curve
// `Demand.fail_segments` / `model_options.demand_fail_segments`.
//
// Formulation under test (source/demand_lp.cpp): the served load is
// split into K tranche columns `served_k ∈ [lb_k, u_k]`,
// `u_k = fraction_k × lmax`, cost `−cost_k` (+ `cost_k × u_k` on the
// objective constant), all stamped into the bus-balance row with the
// load coefficient.  No extra rows; K = 1 is the legacy `load` column.

#include <string>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/demand.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/json/json_demand.hpp>
#include <gtopt/json/json_model_options.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>
#include <gtopt/waterway_lp.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

[[nodiscard]] PlanningOptions fs_options(bool option_c = false)
{
  PlanningOptions popts;
  popts.model_options.scale_objective = 1.0;
  if (option_c) {
    popts.model_options.demand_fail_rhs_shift = true;
  }
  return popts;
}

[[nodiscard]] Simulation fs_sim(int num_stages = 1)
{
  Simulation sim;
  for (int t = 0; t < num_stages; ++t) {
    sim.block_array.push_back({.uid = Uid {t + 1}, .duration = 1.0});
    sim.stage_array.push_back({
        .uid = Uid {t + 1},
        .first_block = static_cast<Size>(t),
        .count_block = 1,
    });
  }
  sim.scenario_array = {{.uid = Uid {0}}};
  return sim;
}

/// PLP-like curve: 10 % @ 300, 20 % @ 500, open @ 1000.
[[nodiscard]] Array<FailSegment> fs_three_segments()
{
  return {
      {.fraction = 0.1, .cost = 300.0},
      {.fraction = 0.2, .cost = 500.0},
      {.cost = 1000.0},
  };
}

/// 1 bus, lmax = 100 MW; cheap gen (cap `gen_cap`, $50) + backup gen
/// ($5000, above every VoLL step, 100 MW).
[[nodiscard]] System fs_system(Demand demand, double gen_cap)
{
  demand.uid = Uid {1};
  demand.name = "d1";
  demand.bus = Uid {1};
  if (!demand.lmax) {
    demand.lmax = 100.0;
  }
  return System {
      .name = "FailSegFixture",
      .bus_array = {{.uid = Uid {1}, .name = "b1"}},
      .demand_array = {std::move(demand)},
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 50.0,
                  .capacity = gen_cap,
              },
              {
                  .uid = Uid {2},
                  .name = "backup",
                  .bus = Uid {1},
                  .gcost = 5000.0,
                  .capacity = 100.0,
              },
          },
  };
}

struct FsSolveOut
{
  double obj {};
  double fail {};
  Index ncols {};
  Index nrows {};
};

[[nodiscard]] FsSolveOut fs_solve(const System& system,
                                  const PlanningOptions& popts,
                                  const Simulation& sim = fs_sim())
{
  const PlanningOptionsLP options(popts);
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  FsSolveOut out;
  out.ncols = lp.get_numcols();
  out.nrows = lp.get_numrows();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);
  out.obj = lp.get_obj_value();
  const auto& dem = system_lp.elements<DemandLP>().front();
  const auto col_sol = lp.get_col_sol();
  for (const auto& stage : simulation_lp.stages()) {
    for (const auto& block : stage.blocks()) {
      out.fail += dem.fail_sol_at(
          simulation_lp.scenarios().front(), stage, block, col_sol);
    }
  }
  return out;
}

}  // namespace

TEST_CASE("fail_segments — curtailment follows the cost order")  // NOLINT
{
  // gen 75 MW → 25 MW shortfall: 10 MW of seg1 @300 + 15 MW of seg2
  // @500 (seg3 @1000 untouched; backup @5000 unused).
  const auto system =
      fs_system(Demand {.fail_segments = fs_three_segments()}, 75.0);
  const auto r = fs_solve(system, fs_options());
  CHECK(r.fail == doctest::Approx(25.0));
  CHECK(r.obj == doctest::Approx((50.0 * 75) + (300.0 * 10) + (500.0 * 15)));

  SUBCASE("Option C gives the same objective")
  {
    const auto rc = fs_solve(system, fs_options(/*option_c=*/true));
    CHECK(rc.obj == doctest::Approx(r.obj));
    CHECK(rc.fail == doctest::Approx(25.0));
  }
}

TEST_CASE("fail_segments — single segment is LP-identical to fcost")  // NOLINT
{
  const auto sys_fcost = fs_system(Demand {.fcost = 1000.0}, 75.0);
  const auto sys_seg =
      fs_system(Demand {.fail_segments = {{.cost = 1000.0}}}, 75.0);
  for (const bool option_c : {false, true}) {
    CAPTURE(option_c);
    const auto a = fs_solve(sys_fcost, fs_options(option_c));
    const auto b = fs_solve(sys_seg, fs_options(option_c));
    CHECK(a.ncols == b.ncols);
    CHECK(a.nrows == b.nrows);
    CHECK(a.obj == doctest::Approx(b.obj));
    CHECK(a.fail == doctest::Approx(25.0));
    CHECK(b.fail == doctest::Approx(25.0));
  }
}

TEST_CASE("fail_segments — K tranches add K-1 columns and no rows")  // NOLINT
{
  Array<FailSegment> four = {
      {.fraction = 0.05, .cost = 492.4},
      {.fraction = 0.05, .cost = 541.7},
      {.fraction = 0.10, .cost = 648.6},
      {.fraction = 0.80, .cost = 742.1},
  };
  const auto a = fs_solve(
      fs_system(Demand {.fcost = 742.1}, 75.0), fs_options(), fs_sim(3));
  const auto b = fs_solve(
      fs_system(Demand {.fail_segments = four}, 75.0), fs_options(), fs_sim(3));
  // 3 blocks × (4 − 1) extra tranche columns; identical row count.
  CHECK(b.ncols == a.ncols + 9);
  CHECK(b.nrows == a.nrows);
  // 25 MW/block: 5 @492.4 + 5 @541.7 + 10 @648.6 + 5 @742.1.
  const double per_block =
      (50.0 * 75) + (492.4 * 5) + (541.7 * 5) + (648.6 * 10) + (742.1 * 5);
  CHECK(b.obj == doctest::Approx(3 * per_block));
  CHECK(b.fail == doctest::Approx(75.0));
}

TEST_CASE("fail_segments — firm remainder cannot be curtailed")  // NOLINT
{
  // Fractions sum to 0.2 → 80 MW firm: only 20 MW may fail, the
  // remaining 5 MW of shortfall comes from the backup unit.
  const auto system = fs_system(
      Demand {
          .fail_segments =
              {
                  {.fraction = 0.1, .cost = 300.0},
                  {.fraction = 0.1, .cost = 500.0},
              },
      },
      75.0);
  const auto r = fs_solve(system, fs_options());
  CHECK(r.fail == doctest::Approx(20.0));
  CHECK(r.obj
        == doctest::Approx((50.0 * 75) + (300.0 * 10) + (500.0 * 10)
                           + (5000.0 * 5)));
}

TEST_CASE(
    "fail_segments — lmin floor lands on the expensive tranches")  // NOLINT
{
  // lmin = 95 → at most 5 MW of failure, taken from the cheapest seg.
  const auto system = fs_system(
      Demand {
          .lmin = 95.0,
          .fail_segments = fs_three_segments(),
      },
      75.0);
  const auto r = fs_solve(system, fs_options());
  CHECK(r.fail == doctest::Approx(5.0));
  CHECK(r.obj == doctest::Approx((50.0 * 75) + (300.0 * 5) + (5000.0 * 20)));
}

TEST_CASE("fail_segments — per-stage cost schedule")  // NOLINT
{
  // seg1 (10 %) costs 300 in stage 1 and 900 in stage 2; seg2 (open)
  // costs 600.  Shortfall 5 MW per stage: stage 1 fails seg1 @300,
  // stage 2 fails the now-cheaper seg2 @600.
  const auto system = fs_system(
      Demand {
          .fail_segments =
              {
                  {.fraction = 0.1, .cost = std::vector<Real> {300.0, 900.0}},
                  {.cost = 600.0},
              },
      },
      95.0);
  const auto r = fs_solve(system, fs_options(), fs_sim(2));
  CHECK(r.fail == doctest::Approx(10.0));
  CHECK(r.obj == doctest::Approx((2 * 50.0 * 95) + (300.0 * 5) + (600.0 * 5)));
}

TEST_CASE("fail_segments — global default and precedence")  // NOLINT
{
  auto popts = fs_options();
  popts.model_options.demand_fail_cost = 2000.0;
  popts.model_options.demand_fail_segments = Array<ScalarFailSegment> {
      {.fraction = 0.1, .cost = 300.0},
      {.fraction = 0.2, .cost = 500.0},
      {.cost = 1000.0},
  };

  SUBCASE("demand without fcost uses the global curve")
  {
    const auto r = fs_solve(fs_system(Demand {}, 75.0), popts);
    CHECK(r.obj == doctest::Approx((50.0 * 75) + (300.0 * 10) + (500.0 * 15)));
  }
  SUBCASE("own fcost wins over the global curve")
  {
    const auto r = fs_solve(fs_system(Demand {.fcost = 1000.0}, 75.0), popts);
    CHECK(r.obj == doctest::Approx((50.0 * 75) + (1000.0 * 25)));
  }
  SUBCASE("own fail_segments win over own fcost")
  {
    const auto r = fs_solve(fs_system(
                                Demand {
                                    .fcost = 9999.0,
                                    .fail_segments = {{.cost = 400.0}},
                                },
                                75.0),
                            popts);
    CHECK(r.obj == doctest::Approx((50.0 * 75) + (400.0 * 25)));
  }
  SUBCASE("forced demand ignores the curve")
  {
    const auto r = fs_solve(fs_system(Demand {.forced = true}, 75.0), popts);
    CHECK(r.fail == doctest::Approx(0.0));
    CHECK(r.obj == doctest::Approx((50.0 * 75) + (5000.0 * 25)));
  }
}

TEST_CASE("fail_segments — user constraints see demand.fail / load")  // NOLINT
{
  for (const bool option_c : {false, true}) {
    CAPTURE(option_c);
    SUBCASE("segmented: demand.fail <= 5")
    {
      auto system =
          fs_system(Demand {.fail_segments = fs_three_segments()}, 75.0);
      system.user_constraint_array = {
          {.uid = Uid {1},
           .name = "uc",
           .expression = "demand('d1').fail <= 5"},
      };
      const auto r = fs_solve(system, fs_options(option_c));
      CHECK(r.fail == doctest::Approx(5.0));
      CHECK(r.obj
            == doctest::Approx((50.0 * 75) + (300.0 * 5) + (5000.0 * 20)));
    }
    SUBCASE("segmented: demand.load >= 90")
    {
      auto system =
          fs_system(Demand {.fail_segments = fs_three_segments()}, 75.0);
      system.user_constraint_array = {
          {.uid = Uid {1},
           .name = "uc",
           .expression = "demand('d1').load >= 90"},
      };
      const auto r = fs_solve(system, fs_options(option_c));
      CHECK(r.fail == doctest::Approx(10.0));
      CHECK(r.obj
            == doctest::Approx((50.0 * 75) + (300.0 * 10) + (5000.0 * 15)));
    }
    SUBCASE("single tranche (fcost): demand.fail <= 5")
    {
      auto system = fs_system(Demand {.fcost = 1000.0}, 75.0);
      system.user_constraint_array = {
          {.uid = Uid {1},
           .name = "uc",
           .expression = "demand('d1').fail <= 5"},
      };
      const auto r = fs_solve(system, fs_options(option_c));
      CHECK(r.fail == doctest::Approx(5.0));
      CHECK(r.obj
            == doctest::Approx((50.0 * 75) + (1000.0 * 5) + (5000.0 * 20)));
    }
  }
}

TEST_CASE("fail_segments — JSON round trip")  // NOLINT
{
  const auto dem = daw::json::from_json<Demand>(R"({
      "uid": 1, "name": "d1", "bus": 1, "lmax": 100,
      "fail_segments": [
        {"fraction": 0.05, "cost": 492.4},
        {"fraction": 0.05, "cost": [541.7, 600.0]},
        {"cost": "fail_cost_seg3"}
      ]
  })");
  REQUIRE(dem.fail_segments.size() == 3);
  CHECK(dem.fail_segments[0].fraction.value_or(-1.0) == doctest::Approx(0.05));
  CHECK_FALSE(dem.fail_segments[2].fraction.has_value());
  CHECK(std::holds_alternative<std::vector<Real>>(
      dem.fail_segments[1].cost.value_or(0.0)));
  CHECK(std::holds_alternative<FileSched>(
      dem.fail_segments[2].cost.value_or(0.0)));

  const auto mo = daw::json::from_json<ModelOptions>(R"({
      "demand_fail_segments": [{"fraction": 0.1, "cost": 300}, {"cost": 900}]
  })");
  REQUIRE(mo.demand_fail_segments.has_value());
  CHECK(mo.demand_fail_segments.value_or(Array<ScalarFailSegment> {}).size()
        == 2);

  // Unset stays absent on write-back.
  const auto js = daw::json::to_json(ModelOptions {});
  CHECK(js.find("demand_fail_segments") == std::string::npos);
}

TEST_CASE("fail_segments — validation")  // NOLINT
{
  auto make_planning = [](Array<FailSegment> segs)
  {
    return Planning {
        .simulation = fs_sim(),
        .system = fs_system(Demand {.fail_segments = std::move(segs)}, 75.0),
    };
  };
  const auto has = [](const std::vector<std::string>& v, std::string_view s)
  {
    return std::ranges::any_of(
        v, [&](const auto& m) { return m.find(s) != std::string::npos; });
  };

  SUBCASE("valid curve")
  {
    auto p = make_planning(fs_three_segments());
    const auto r = validate_planning(p);
    CHECK_FALSE(has(r.errors, "fail_segments"));
    CHECK_FALSE(has(r.warnings, "fail_segments"));
  }
  SUBCASE("non-positive fraction is an error")
  {
    auto p = make_planning({{.fraction = 0.0, .cost = 1.0}, {.cost = 2.0}});
    CHECK(has(validate_planning(p).errors, "must be in (0, 1]"));
  }
  SUBCASE("open non-last segment is an error")
  {
    auto p = make_planning({{.cost = 1.0}, {.fraction = 0.5, .cost = 2.0}});
    CHECK(has(validate_planning(p).errors, "only the last"));
  }
  SUBCASE("fractions summing above 1 are an error")
  {
    auto p = make_planning(
        {{.fraction = 0.6, .cost = 1.0}, {.fraction = 0.6, .cost = 2.0}});
    CHECK(has(validate_planning(p).errors, "sum to"));
  }
  SUBCASE("missing cost is an error")
  {
    auto p = make_planning({{.fraction = 0.5}});
    CHECK(has(validate_planning(p).errors, "cost is required"));
  }
  SUBCASE("decreasing costs warn")
  {
    auto p = make_planning({{.fraction = 0.5, .cost = 900.0}, {.cost = 300.0}});
    const auto r = validate_planning(p);
    CHECK_FALSE(has(r.errors, "fail_segments"));
    CHECK(has(r.warnings, "costs decrease"));
  }
}

TEST_CASE(
    "fail_segments — SDDP 2 phases reaches the analytic optimum")  // NOLINT
{
  // 2-phase hydro fixture without the thermal unit: water is short, so
  // part of the 30 MW demand fails along the stepwise curve
  // (6 MW @ 300, 24 MW @ 1000).  Water: eini 50 + 5/h · 8 h = 90 MWh
  // (hydro cap 20 MW never binds).  The convex curve spreads the
  // 240 − 90 = 150 MWh of failure evenly: 18.75 MW per block, i.e.
  // 6·300 + 12.75·1000 = 14550 $/block.  Optimum = 8·14550 + 90·5.
  constexpr double expected =
      (8.0 * ((6.0 * 300.0) + (12.75 * 1000.0))) + (90.0 * 5.0);

  auto planning = make_2phase_linear_planning();
  planning.system.generator_array[1].capacity = 0.0;
  planning.system.demand_array[0].fail_segments = {
      {.fraction = 0.2, .cost = 300.0},
      {.cost = 1000.0},
  };

  PlanningLP plp(std::move(planning));
  SDDPOptions sddp_opts;
  sddp_opts.max_iterations = 20;
  sddp_opts.convergence_tol = 1e-5;
  sddp_opts.enable_api = false;
  SDDPMethod sddp(plp, sddp_opts);
  const auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  const auto& last = results->back();
  CHECK(last.converged);
  CHECK(last.upper_bound == doctest::Approx(expected).epsilon(1e-4));
  CHECK(last.lower_bound == doctest::Approx(expected).epsilon(1e-4));
}

TEST_CASE("fail_segments — validation of limits and combinations")  // NOLINT
{
  const auto has = [](const std::vector<std::string>& v, std::string_view s)
  {
    return std::ranges::any_of(
        v, [&](const auto& m) { return m.find(s) != std::string::npos; });
  };
  auto make_planning = [](Demand dem)
  {
    return Planning {
        .simulation = fs_sim(),
        .system = fs_system(std::move(dem), 75.0),
    };
  };

  SUBCASE("more than max_fail_segments is an error")
  {
    Array<FailSegment> segs;
    for (std::size_t k = 0; k <= max_fail_segments; ++k) {
      segs.push_back(
          {.fraction = 0.01, .cost = 100.0 + static_cast<double>(k)});
    }
    auto p = make_planning(Demand {.fail_segments = segs});
    CHECK(has(validate_planning(p).errors, "segments (max 16)"));
  }
  SUBCASE("an empty open last segment is an error")
  {
    auto p = make_planning(Demand {
        .fail_segments =
            {
                {.fraction = 0.4, .cost = 100.0},
                {.fraction = 0.6, .cost = 200.0},
                {.cost = 300.0},
            },
    });
    CHECK(has(validate_planning(p).errors, "open last fail segment is empty"));
  }
  SUBCASE("fcost next to fail_segments warns (segments win)")
  {
    auto p = make_planning(Demand {
        .fcost = 900.0,
        .fail_segments = fs_three_segments(),
    });
    const auto r = validate_planning(p);
    CHECK(has(r.warnings, "both fcost and fail_segments"));
  }
  SUBCASE("a forced demand warns that its curve is ignored")
  {
    auto p = make_planning(Demand {
        .fail_segments = fs_three_segments(),
        .forced = true,
    });
    CHECK(has(validate_planning(p).warnings, "forced demand ignores"));
  }
  SUBCASE("the global curve is validated too")
  {
    auto p = make_planning(Demand {});
    p.options.model_options.demand_fail_segments = Array<ScalarFailSegment> {
        {.fraction = 1.5, .cost = 100.0},
        {.cost = 50.0},
    };
    const auto r = validate_planning(p);
    CHECK(has(r.errors, "model_options.demand_fail_segments"));
    CHECK(has(r.errors, "must be in (0, 1]"));
    CHECK(has(r.warnings, "costs decrease"));
  }
  SUBCASE("DemandProfile / Converter on a segmented demand are errors")
  {
    auto p = make_planning(Demand {.fail_segments = fs_three_segments()});
    p.system.demand_profile_array = {
        {
            .uid = Uid {1},
            .name = "dp1",
            .demand = Name {"d1"},
            .profile = 0.5,
        },
    };
    p.system.converter_array = {
        {
            .uid = Uid {1},
            .name = "conv1",
            .battery = Uid {1},
            .generator = Uid {1},
            .demand = Uid {1},
        },
    };
    const auto r = validate_planning(p);
    CHECK(has(r.errors, "DemandProfile 'dp1'"));
    CHECK(has(r.errors, "Converter 'conv1'"));
  }
}

TEST_CASE(
    "fail_segments — LP build rejects unsupported combinations")  // NOLINT
{
  const PlanningOptionsLP options(fs_options());
  const auto sim = fs_sim();

  SUBCASE("more than max_fail_segments throws")
  {
    Array<FailSegment> segs;
    for (std::size_t k = 0; k <= max_fail_segments; ++k) {
      segs.push_back({.fraction = 0.01, .cost = 100.0});
    }
    const auto sys = fs_system(Demand {.fail_segments = segs}, 75.0);
    SimulationLP simulation_lp(sim, options);
    CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::invalid_argument);
  }
  SUBCASE("DemandProfile throws")
  {
    auto sys = fs_system(Demand {.fail_segments = fs_three_segments()}, 75.0);
    sys.demand_profile_array = {
        {
            .uid = Uid {1},
            .name = "dp1",
            .demand = Uid {1},
            .profile = 0.5,
        },
    };
    SimulationLP simulation_lp(sim, options);
    CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::runtime_error);
  }
  SUBCASE("CapacityProfile with a demand owner throws")
  {
    auto sys = fs_system(Demand {.fail_segments = fs_three_segments()}, 75.0);
    sys.capacity_profile_array = {
        {
            .uid = Uid {1},
            .name = "cp1",
            .owner_kind = ProfileOwnerKind::Demand,
            .owner = Uid {1},
            .profile = 0.5,
        },
    };
    SimulationLP simulation_lp(sim, options);
    CHECK_THROWS_AS(SystemLP(sys, simulation_lp), std::runtime_error);
  }
}

TEST_CASE(  // NOLINT
    "fail_segments — a pump demand draws its load from every tranche")
{
  // The pump conversion row is `pump_factor·flow − load ≤ 0`.  With a
  // stepwise curve the served load is Σ tranche columns, so the row must
  // see all of them: an end-of-horizon volume requirement that can only
  // be met by pumping makes the pump flow (and its load) strictly
  // positive.  Curtailment (1 $/MWh) is cheaper than generation
  // (50 $/MWh), so only the load the pump needs is served: flow =
  // 0.2 hm³ / (0.0036 · 1 h) and load = 2·flow, unique.  Two tranches
  // with the same cost are economically the single-column `fcost`
  // demand, so both LPs must reach the same optimum (dropping a tranche
  // from the row would make the volume target infeasible).
  const auto solve_pump = [](bool segmented)
  {
    Demand pump_demand {
        .uid = Uid {2},
        .name = "pump_demand",
        .bus = Uid {1},
        .lmax = 200.0,
    };
    if (segmented) {
      pump_demand.fail_segments = {
          {.fraction = 0.5, .cost = 1.0},
          {.cost = 1.0},
      };
    } else {
      pump_demand.fcost = 1.0;
    }
    const System system {
        .name = "PumpFailSegments",
        .bus_array = {{.uid = Uid {1}, .name = "b1"}},
        .demand_array = {std::move(pump_demand)},
        .generator_array =
            {
                {
                    .uid = Uid {1},
                    .name = "g1",
                    .bus = Uid {1},
                    .gcost = 50.0,
                    .capacity = 300.0,
                },
            },
        .junction_array =
            {
                {.uid = Uid {1}, .name = "j_up"},
                {.uid = Uid {2}, .name = "j_down", .drain = true},
            },
        .waterway_array =
            {
                {
                    .uid = Uid {1},
                    .name = "ww_pump",
                    .junction_a = Uid {2},
                    .junction_b = Uid {1},
                    .fmin = 0.0,
                    .fmax = 100.0,
                },
            },
        .reservoir_array =
            {
                {
                    .uid = Uid {1},
                    .name = "rsv_up",
                    .junction = Uid {1},
                    .capacity = 5000.0,
                    .emin = 0.0,
                    .emax = 5000.0,
                    .eini = 2000.0,
                    .efin = 2000.2,
                },
                {
                    .uid = Uid {2},
                    .name = "rsv_down",
                    .junction = Uid {2},
                    .capacity = 5000.0,
                    .emin = 0.0,
                    .emax = 5000.0,
                    .eini = 3000.0,
                },
            },
        .pump_array =
            {
                {
                    .uid = Uid {1},
                    .name = "pump1",
                    .waterway = Uid {1},
                    .demand = Uid {2},
                    .pump_factor = 2.0,
                },
            },
    };
    const PlanningOptionsLP options(fs_options());
    const auto sim = fs_sim();
    SimulationLP simulation_lp(sim, options);
    SystemLP system_lp(system, simulation_lp);
    auto&& lp = system_lp.linear_interface();
    const auto result = lp.resolve();
    REQUIRE(result.has_value());
    REQUIRE(result.value() == 0);

    const auto& scenario = simulation_lp.scenarios().front();
    const auto& stage = simulation_lp.stages().front();
    const auto buid = stage.blocks().front().uid();
    const auto sol = lp.get_col_sol();
    double flow = 0.0;
    for (const auto& ww : system_lp.elements<WaterwayLP>()) {
      flow = sol[ww.flow_cols_at(scenario, stage).at(buid)];
    }
    return std::pair {lp.get_obj_value(), flow};
  };

  const auto [obj_fcost, flow_fcost] = solve_pump(false);
  const auto [obj_segs, flow_segs] = solve_pump(true);
  CHECK(flow_fcost == doctest::Approx(0.2 / 0.0036));
  CHECK(flow_segs == doctest::Approx(flow_fcost));
  CHECK(obj_segs == doctest::Approx(obj_fcost));
}
