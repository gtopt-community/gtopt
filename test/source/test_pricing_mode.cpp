/**
 * @file      test_pricing_mode.cpp
 * @brief     Post-MIP pricing modes: fixed / relaxed / fast_start
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Toy UC case (also `cases/uc_pricing_*`): two buses, two hours.
 *
 *   g_slow @ b1: 10 $/MWh, 50..120 MW when on, no-load 300 $/h,
 *                min up/down 4 h (NOT fast-start), on since 10 h.
 *   g_fast @ b2: 50 $/MWh, 40..100 MW when on, start-up 1000 $,
 *                min up/down 1 h (fast-start by the auto rule), off.
 *   demand @ b2: 140 MW, 80 MW.   Line b1-b2: 300 MW (never binds).
 *
 * MIP dispatch (every mode): g_slow = 100 / 80, g_fast = 40 / 0
 * (g_slow alone cannot cover 140 MW, g_fast runs at its pmin).
 * Objective 10·180 + 2·300 + 50·40 + 1000 = 5400.
 *
 * Prices (uniform — the line does not bind):
 *   fixed      : (10, 10)   g_slow marginal; g_fast's pmin dual
 *                (μ = 50 − 10 = 40) keeps the price below its cost.
 *   fast_start : (60, 10)   g_fast's u relaxed: its average cost at pmax,
 *                50 + 1000/100 = 60, sets hour 1.
 *   relaxed    : (60, 12.5) g_slow's u relaxed too: its no-load becomes
 *                divisible, 10 + 300/120 = 12.5 in hour 2.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <fstream>
#include <ranges>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/json/json_commitment.hpp>
#include <gtopt/json/json_model_options.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/pricing_mode.hpp>
#include <gtopt/validate_planning.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

/// The toy case; `{MODEL}` is replaced by extra `model_options` members
/// and `{FAST_SLOW}` / `{FAST_FAST}` by extra commitment members.
constexpr std::string_view toy_json = R"({
  "options": {
    "annual_discount_rate": 0.0,
    "output_format": "csv",
    "output_compression": "uncompressed",
    "model_options": {
      {MODEL}
      "use_single_bus": false,
      "use_kirchhoff": true,
      "scale_objective": 1,
      "demand_fail_cost": 1000
    }
  },
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2,
                     "active": 1, "chronological": true}],
    "scenario_array": [{"uid": 1, "probability_factor": 1}]
  },
  "system": {
    "name": "uc_pricing_toy",
    "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
    "line_array": [{"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
                    "reactance": 0.1, "tmax_ab": 300, "tmax_ba": 300}],
    "generator_array": [
      {"uid": 1, "name": "g_slow", "bus": "b1", "pmin": 0, "pmax": 120,
       "gcost": 10, "capacity": 120},
      {"uid": 2, "name": "g_fast", "bus": "b2", "pmin": 0, "pmax": 100,
       "gcost": 50, "capacity": 100}
    ],
    "demand_array": [
      {"uid": 1, "name": "d2", "bus": "b2", "lmax": [[140.0, 80.0]]}
    ],
    "commitment_array": [
      {"uid": 1, "name": "g_slow_uc", "generator": "g_slow", {FAST_SLOW}
       "pmin": 50, "noload_cost": 300, "min_up_time": 4,
       "min_down_time": 4, "initial_status": 1, "initial_hours": 10},
      {"uid": 2, "name": "g_fast_uc", "generator": "g_fast", {FAST_FAST}
       "pmin": 40, "startup_cost": 1000, "min_up_time": 1,
       "min_down_time": 1, "initial_status": 0, "initial_hours": 10}
    ]
  }
})";

void replace_all(std::string& s, std::string_view from, std::string_view to)
{
  for (auto pos = s.find(from); pos != std::string::npos;
       pos = s.find(from, pos + to.size()))
  {
    s.replace(pos, from.size(), to);
  }
}

struct ToyArgs
{
  std::string model {};  ///< extra model_options members (with trailing ,)
  std::string fast_slow {};  ///< extra g_slow_uc members
  std::string fast_fast {};  ///< extra g_fast_uc members
};

[[nodiscard]] Planning toy_planning(const ToyArgs& args,
                                    const std::string& solver)
{
  std::string json {toy_json};
  replace_all(json, "{MODEL}", args.model);
  replace_all(json, "{FAST_SLOW}", args.fast_slow);
  replace_all(json, "{FAST_FAST}", args.fast_fast);
  auto planning = daw::json::from_json<Planning>(json);
  planning.options.lp_matrix_options.solver_name = solver;
  return planning;
}

[[nodiscard]] std::string mode_member(std::string_view mode)
{
  return std::format(R"("pricing_mode": "{}",)", mode);
}

/// Exact MIP backends that recover fix-integer duals on UC fixtures (see
/// test_fix_integers_duals.cpp: cbc returns the LP-relaxation value here,
/// unlicensed gurobi solves degenerate).
[[nodiscard]] std::vector<std::string> pricing_solvers()
{
  std::vector<std::string> out;
  for (auto& s : solver_test::exact_mip_solvers()) {
    if (s != "cbc" && s != "gurobi") {
      out.push_back(std::move(s));
    }
  }
  return out;
}

/// Prices + dispatch of the solved toy (single scene / phase).
struct ToyResult
{
  std::vector<double> lmp_b1;  ///< per block
  std::vector<double> lmp_b2;
  std::vector<double> g_slow;
  std::vector<double> g_fast;
  double obj {0.0};
  PricingOutcome pricing {};
};

[[nodiscard]] ToyResult solve_toy(PlanningLP& planning_lp)
{
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());

  auto& system = planning_lp.system(SceneIndex {0}, PhaseIndex {0});
  auto& li = system.linear_interface();
  REQUIRE(li.is_optimal());

  const auto& scenario = planning_lp.simulation().scenarios().front();
  const auto& stage = planning_lp.simulation().stages().front();

  ToyResult r;
  r.obj = li.get_obj_value();
  r.pricing = li.last_pricing();

  const auto duals = li.get_row_dual();
  REQUIRE(duals.size() == static_cast<std::size_t>(li.get_numrows()));
  const auto sol = li.get_col_sol();

  const auto& buses = system.elements<BusLP>();
  REQUIRE(buses.size() == 2);
  for (const auto& [i, bus] : std::views::enumerate(buses)) {
    auto& dst = (i == 0) ? r.lmp_b1 : r.lmp_b2;
    for (const auto& row :
         bus.balance_rows_at(scenario, stage) | std::views::values)
    {
      dst.push_back(duals[row]);
    }
  }
  const auto& gens = system.elements<GeneratorLP>();
  REQUIRE(gens.size() == 2);
  for (const auto& [i, gen] : std::views::enumerate(gens)) {
    auto& dst = (i == 0) ? r.g_slow : r.g_fast;
    for (const auto& col :
         gen.generation_cols_at(scenario, stage) | std::views::values)
    {
      dst.push_back(sol[col]);
    }
  }
  return r;
}

[[nodiscard]] ToyResult run_toy(std::string_view mode,
                                const std::string& solver)
{
  PlanningLP planning_lp(
      toy_planning(ToyArgs {.model = mode_member(mode)}, solver));
  return solve_toy(planning_lp);
}

void check_mip_dispatch(const ToyResult& r)
{
  REQUIRE(r.g_slow.size() == 2);
  REQUIRE(r.g_fast.size() == 2);
  CHECK(r.g_slow[0] == doctest::Approx(100.0));
  CHECK(r.g_slow[1] == doctest::Approx(80.0));
  CHECK(r.g_fast[0] == doctest::Approx(40.0));
  CHECK(r.g_fast[1] == doctest::Approx(0.0));
  CHECK(r.obj == doctest::Approx(5400.0));
}

void check_prices(const ToyResult& r, double h1, double h2)
{
  REQUIRE(r.lmp_b1.size() == 2);
  REQUIRE(r.lmp_b2.size() == 2);
  // Uncongested line: one system price.
  CHECK(r.lmp_b1[0] == doctest::Approx(h1));
  CHECK(r.lmp_b2[0] == doctest::Approx(h1));
  CHECK(r.lmp_b1[1] == doctest::Approx(h2));
  CHECK(r.lmp_b2[1] == doctest::Approx(h2));
}

/// RAII setenv for the test-only fallback hook.
struct ScopedEnv
{
  explicit ScopedEnv(const char* name, const char* value)
      : m_name_(name)
  {
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    ::setenv(name, value, 1);
  }
  ScopedEnv(const ScopedEnv&) = delete;
  ScopedEnv& operator=(const ScopedEnv&) = delete;
  ScopedEnv(ScopedEnv&&) = delete;
  ScopedEnv& operator=(ScopedEnv&&) = delete;
  ~ScopedEnv()
  {
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    ::unsetenv(m_name_);
  }

private:
  const char* m_name_;
};

[[nodiscard]] std::string slurp(const std::filesystem::path& p)
{
  std::ifstream in {p};
  std::stringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

}  // namespace

// ═══════════════════════════════════════════════════════════════════════
// Tier 1a — option plumbing, classification rule, provenance format
// ═══════════════════════════════════════════════════════════════════════

TEST_CASE("PricingMode names and the fast-start rule")  // NOLINT
{
  CHECK(enum_from_name<PricingMode>("fixed") == PricingMode::fixed);
  CHECK(enum_from_name<PricingMode>("relaxed") == PricingMode::relaxed);
  CHECK(enum_from_name<PricingMode>("fast_start") == PricingMode::fast_start);
  CHECK(enum_from_name<PricingMode>("fast-start") == PricingMode::fast_start);
  CHECK_FALSE(enum_from_name<PricingMode>("convex").has_value());
  CHECK(enum_name(PricingMode::fast_start) == "fast_start");

  // Explicit flag wins over the auto rule.
  CHECK(is_fast_start(true, 24.0, 24.0, 1.0));
  CHECK_FALSE(is_fast_start(false, 0.0, 0.0, 1.0));
  // Auto rule: both min up and min down <= threshold (unset = 0).
  CHECK(is_fast_start({}, 1.0, 1.0, 1.0));
  CHECK(is_fast_start({}, {}, {}, 1.0));
  CHECK_FALSE(is_fast_start({}, 2.0, 1.0, 1.0));
  CHECK_FALSE(is_fast_start({}, 1.0, 4.0, 1.0));
  CHECK(is_fast_start({}, 4.0, 4.0, 4.0));
}

TEST_CASE("pricing_mode option resolution, JSON and CLI")  // NOLINT
{
  SUBCASE("default is fixed, threshold 1 h")
  {
    const PlanningOptionsLP opts {PlanningOptions {}};
    CHECK(opts.pricing_mode() == PricingMode::fixed);
    CHECK(opts.fast_start_max_hours() == doctest::Approx(1.0));
  }

  SUBCASE("model_options JSON round trip")
  {
    const auto mo = daw::json::from_json<ModelOptions>(
        R"({"pricing_mode": "fast_start", "fast_start_max_hours": 2.5})");
    CHECK(mo.pricing_mode.value_or("") == "fast_start");
    CHECK(mo.fast_start_max_hours.value_or(0.0) == doctest::Approx(2.5));
    const auto back = daw::json::to_json(mo);
    CHECK(back.find(R"("pricing_mode":"fast_start")") != std::string::npos);

    PlanningOptions po;
    po.model_options = mo;
    const PlanningOptionsLP opts {std::move(po)};
    CHECK(opts.pricing_mode() == PricingMode::fast_start);
    CHECK(opts.fast_start_max_hours() == doctest::Approx(2.5));
  }

  SUBCASE("Commitment.fast_start JSON round trip")
  {
    const auto c = daw::json::from_json<Commitment>(
        R"({"uid": 1, "name": "c", "generator": 1, "fast_start": true})");
    CHECK(c.fast_start.value_or(false));
    CHECK(daw::json::to_json(c).find(R"("fast_start":true)")
          != std::string::npos);
  }

  SUBCASE("--pricing-mode CLI shortcut")
  {
    Planning planning;
    apply_cli_options(planning, MainOptions {.pricing_mode = "relaxed"});
    CHECK(planning.options.model_options.pricing_mode.value_or("")
          == "relaxed");
    // Aliases are canonicalised.
    apply_cli_options(planning, MainOptions {.pricing_mode = "fast-start"});
    CHECK(planning.options.model_options.pricing_mode.value_or("")
          == "fast_start");
    CHECK_THROWS(
        apply_cli_options(planning, MainOptions {.pricing_mode = "bogus"}));
  }

  SUBCASE("validate_planning rejects unknown names / negative threshold")
  {
    Planning planning;
    planning.options.model_options.pricing_mode = "convex_hull";
    planning.options.model_options.fast_start_max_hours = -1.0;
    const auto result = validate_planning(planning);
    const auto has = [&](std::string_view needle)
    {
      return std::ranges::any_of(result.errors,
                                 [&](const std::string& e)
                                 { return e.contains(needle); });
    };
    CHECK(has("pricing_mode"));
    CHECK(has("fast_start_max_hours"));
  }
}

TEST_CASE("pricing.csv row format")  // NOLINT
{
  CHECK(pricing_csv_header()
        == "scene,phase,pricing_mode,applied_mode,fallback,attempted,"
           "relaxed_columns,pinned_columns,status,reason\n");
  const PricingOutcome ok {
      .requested = PricingMode::relaxed,
      .applied = PricingMode::relaxed,
      .attempted = true,
      .relaxed_columns = 4,
      .status = 0,
  };
  CHECK(pricing_csv_line(1, 2, ok) == "1,2,relaxed,relaxed,0,1,4,0,0,\"\"\n");
  const PricingOutcome fb {
      .requested = PricingMode::fast_start,
      .applied = PricingMode::fixed,
      .fallback = true,
      .reason = R"(LP "x" failed, sorry)",
  };
  CHECK(pricing_csv_line(1, 1, fb)
        == "1,1,fast_start,fixed,1,0,0,0,-1,\"LP \"\"x\"\" failed, "
           "sorry\"\n");
  CHECK_FALSE(pricing_outcome_reportable(PricingOutcome {}));
  CHECK(pricing_outcome_reportable(ok));
  CHECK(pricing_outcome_reportable(fb));
}

// ═══════════════════════════════════════════════════════════════════════
// Tier 1b — fast-start column registration at LP build
// ═══════════════════════════════════════════════════════════════════════

TEST_CASE("fast-start commitment columns are registered")  // NOLINT
{
  const auto solver = solver_test::first_mip_solver();
  if (solver.empty()) {
    MESSAGE("No MIP-capable solver available — skipping");
    return;
  }

  const auto count_fast = [&](const ToyArgs& args)
  {
    const PlanningLP planning_lp(toy_planning(args, solver));
    const auto& system = planning_lp.system(SceneIndex {0}, PhaseIndex {0});
    const auto cols = system.linear_interface().fast_start_cols();
    // Every registered column must be an integer commitment column.
    for (const int c : cols) {
      CHECK(system.linear_interface().is_integer(ColIndex {c}));
    }
    return cols.size();
  };

  SUBCASE("auto rule: g_fast (1 h) yes, g_slow (4 h) no")
  {
    const PlanningLP planning_lp(toy_planning({}, solver));
    const auto& system = planning_lp.system(SceneIndex {0}, PhaseIndex {0});
    const auto cols = system.linear_interface().fast_start_cols();
    REQUIRE(cols.size() == 6);  // g_fast u, v, w (binary), two blocks

    const auto& scenario = planning_lp.simulation().scenarios().front();
    const auto& stage = planning_lp.simulation().stages().front();
    const auto& cmts = system.elements<CommitmentLP>();
    REQUIRE(cmts.size() == 2);
    const auto* fast_u = cmts[1].find_status_cols(scenario, stage);
    const auto* slow_u = cmts[0].find_status_cols(scenario, stage);
    REQUIRE(fast_u != nullptr);
    REQUIRE(slow_u != nullptr);
    for (const auto& col : *fast_u | std::views::values) {
      CHECK(std::ranges::binary_search(cols, static_cast<int>(col)));
    }
    for (const auto& col : *slow_u | std::views::values) {
      CHECK_FALSE(std::ranges::binary_search(cols, static_cast<int>(col)));
    }
  }

  SUBCASE("explicit false disables, explicit true enables")
  {
    CHECK(count_fast({.fast_fast = R"("fast_start": false,)"}) == 0);
    CHECK(count_fast({.fast_slow = R"("fast_start": true,)"}) == 12);
  }

  SUBCASE("fast_start_max_hours widens the auto rule")
  {
    CHECK(count_fast({.model = R"("fast_start_max_hours": 4,)"}) == 12);
    CHECK(count_fast({.model = R"("fast_start_max_hours": 0.5,)"}) == 0);
  }
}

// ═══════════════════════════════════════════════════════════════════════
// Tier 1c — prices per mode on the toy, dispatch identity, fallback
// ═══════════════════════════════════════════════════════════════════════

TEST_CASE("pricing modes on the 2-bus UC toy")  // NOLINT
{
  const auto solvers = pricing_solvers();
  if (solvers.empty()) {
    MESSAGE("No suitable MIP solver available — skipping");
    return;
  }
  // Each SUBCASE loops over the solvers itself (a SUBCASE inside a loop
  // would be entered once only).
  const auto for_each_solver = [&](const auto& body)
  {
    for (const auto& solver : solvers) {
      CAPTURE(solver);
      if (!solver_test::run_or_skip_license([&] { body(solver); })) {
        MESSAGE("skipped " << solver << " (license)");
      }
    }
  };

  SUBCASE("fixed: pmin dual keeps the price below the peaker cost")
  {
    for_each_solver(
        [](const std::string& solver)
        {
          const auto r = run_toy("fixed", solver);
          check_mip_dispatch(r);
          check_prices(r, 10.0, 10.0);
          CHECK(r.pricing.requested == PricingMode::fixed);
          CHECK(r.pricing.applied == PricingMode::fixed);
          CHECK(r.pricing.attempted);
          CHECK_FALSE(r.pricing.fallback);
          CHECK(r.pricing.relaxed_columns == 0);
        });
  }

  SUBCASE("fast_start: the peaker's average cost sets the price")
  {
    for_each_solver(
        [](const std::string& solver)
        {
          const auto r = run_toy("fast_start", solver);
          check_mip_dispatch(r);  // dispatch unchanged
          // 50 $/MWh + 1000 $ / 100 MW = 60 $/MWh in hour 1; hour 2 is the
          // committed slow unit's marginal cost.
          check_prices(r, 60.0, 10.0);
          CHECK(r.pricing.applied == PricingMode::fast_start);
          CHECK(r.pricing.attempted);
          CHECK_FALSE(r.pricing.fallback);
          // g_fast u, v, w (binary under transition_integrality = auto)
          // x 2 blocks; the slow unit's 6 are pinned.
          CHECK(r.pricing.relaxed_columns == 6);
          CHECK(r.pricing.pinned_columns == 6);
          CHECK(r.pricing.status == 0);
        });
  }

  SUBCASE("relaxed: every commitment divisible")
  {
    for_each_solver(
        [](const std::string& solver)
        {
          const auto r = run_toy("relaxed", solver);
          check_mip_dispatch(r);  // dispatch unchanged
          // Hour 2: 10 + 300 $/h / 120 MW = 12.5 $/MWh.
          check_prices(r, 60.0, 12.5);
          CHECK(r.pricing.applied == PricingMode::relaxed);
          CHECK(r.pricing.relaxed_columns == 12);  // 2 units x u, v, w x 2
          CHECK(r.pricing.pinned_columns == 0);
        });
  }

  SUBCASE("fast_start without fast-start units == fixed")
  {
    for_each_solver(
        [](const std::string& solver)
        {
          PlanningLP planning_lp(
              toy_planning(ToyArgs {.model = mode_member("fast_start"),
                                    .fast_fast = R"("fast_start": false,)"},
                           solver));
          const auto r = solve_toy(planning_lp);
          check_mip_dispatch(r);
          check_prices(r, 10.0, 10.0);
          CHECK(r.pricing.requested == PricingMode::fast_start);
          CHECK(r.pricing.applied == PricingMode::fixed);
          CHECK_FALSE(r.pricing.fallback);
          CHECK(r.pricing.reason.contains("no fast-start"));
        });
  }

  SUBCASE("failed pricing LP falls back to fixed prices")
  {
    const ScopedEnv env {"GTOPT_TEST_FORCE_PRICING_FAIL", "1"};
    for_each_solver(
        [](const std::string& solver)
        {
          const auto r = run_toy("relaxed", solver);
          check_mip_dispatch(r);
          check_prices(r, 10.0, 10.0);  // the fixed (polish) prices
          CHECK(r.pricing.requested == PricingMode::relaxed);
          CHECK(r.pricing.applied == PricingMode::fixed);
          CHECK(r.pricing.fallback);
          CHECK(r.pricing.reason.contains("not optimal"));
        });
  }
}

TEST_CASE(  // NOLINT
    "fast_start pricing relaxes startup-tier selectors with the status")
{
  // g_fast starts from 10 h offline → a COLD start (hot < 1 h, cold
  // >= 5 h) costing 1000, the same as the toy's flat startup cost, so the
  // fast-start price must stay the toy's 50 + 1000 / 100 = 60 $/MWh in
  // hour 1.  Tier selectors pinned at the incumbent (the former bug for
  // integer tiers: they were not registered for relaxation) pin
  // v = Σ y = 1 and, through v <= u, u = 1 — the peaker's start cost
  // then drops out of the price.
  const auto solvers = pricing_solvers();
  if (solvers.empty()) {
    MESSAGE("No suitable MIP solver available — skipping");
    return;
  }
  constexpr std::string_view tiers =
      R"("hot_start_cost": 200, "warm_start_cost": 500,
         "cold_start_cost": 1000, "hot_start_time": 1,
         "cold_start_time": 5,)";

  for (const auto& solver : solvers) {
    CAPTURE(solver);
    for (const auto* tier_mode : {"continuous", "integer"}) {
      CAPTURE(tier_mode);
      const auto body = [&]
      {
        PlanningLP planning_lp(toy_planning(
            ToyArgs {
                .model = mode_member("fast_start")
                    + std::format(R"("startup_tier_integrality": "{}",)",
                                  tier_mode),
                .fast_fast = std::string {tiers},
            },
            solver));
        const auto& system = planning_lp.system(SceneIndex {0}, PhaseIndex {0});
        // Integer tiers are registered with u, v, w: 2 periods x
        // (u + v + w + 3 tiers).
        const auto integer = std::string_view {tier_mode} == "integer";
        CHECK(system.linear_interface().fast_start_cols().size()
              == (integer ? 12U : 6U));
        const auto r = solve_toy(planning_lp);
        check_mip_dispatch(r);
        check_prices(r, 60.0, 10.0);
        CHECK(r.pricing.applied == PricingMode::fast_start);
        CHECK(r.pricing.relaxed_columns == (integer ? 12 : 6));
      };
      if (!solver_test::run_or_skip_license(body)) {
        MESSAGE("skipped " << solver << " (license)");
      }
    }
  }
}

TEST_CASE("solve_pricing_lp leaves the source LP untouched")  // NOLINT
{
  const auto solvers = pricing_solvers();
  if (solvers.empty()) {
    MESSAGE("No suitable MIP solver available — skipping");
    return;
  }
  const auto& solver = solvers.front();
  CAPTURE(solver);
  PlanningLP planning_lp(toy_planning({}, solver));
  auto& li =
      planning_lp.system(SceneIndex {0}, PhaseIndex {0}).linear_interface();
  REQUIRE(li.resolve().has_value());
  REQUIRE(li.has_integer_cols());
  const auto obj = li.get_obj_value();

  SUBCASE("fixed solves nothing")
  {
    const auto p = li.solve_pricing_lp(PricingMode::fixed, {});
    CHECK(p.outcome.applied == PricingMode::fixed);
    CHECK_FALSE(p.outcome.attempted);
    CHECK(p.row_dual_raw.empty());
  }

  SUBCASE("relaxed solves a clone")
  {
    const auto p = li.solve_pricing_lp(PricingMode::relaxed, {});
    CHECK(p.outcome.applied == PricingMode::relaxed);
    CHECK(p.outcome.attempted);
    CHECK(p.row_dual_raw.size() == static_cast<std::size_t>(li.get_numrows()));
    CHECK(p.col_cost_raw.size() == static_cast<std::size_t>(li.get_numcols()));
    // The source MIP keeps its integrality and incumbent.
    CHECK(li.has_integer_cols());
    CHECK(li.get_obj_value() == doctest::Approx(obj));
  }

  SUBCASE("install on a size mismatch records a fallback")
  {
    LinearInterface::PricingDuals bad;
    bad.outcome.requested = PricingMode::relaxed;
    bad.outcome.applied = PricingMode::relaxed;
    bad.row_dual_raw = {1.0};
    li.install_pricing_duals(std::move(bad));
    CHECK(li.last_pricing().fallback);
    CHECK(li.last_pricing().applied == PricingMode::fixed);
    CHECK(li.last_pricing().reason.contains("size mismatch"));
  }
}

// ═══════════════════════════════════════════════════════════════════════
// Tier 1d — provenance: pricing.csv + planning.json via write_out
// ═══════════════════════════════════════════════════════════════════════

TEST_CASE("pricing.csv and planning provenance are written")  // NOLINT
{
  const auto solvers = pricing_solvers();
  if (solvers.empty()) {
    MESSAGE("No suitable MIP solver available — skipping");
    return;
  }
  const auto& solver = solvers.front();
  CAPTURE(solver);

  const auto tmpdir =
      std::filesystem::temp_directory_path() / "gtopt_test_pricing_mode";
  std::filesystem::remove_all(tmpdir);
  std::filesystem::create_directories(tmpdir);

  SUBCASE("fast_start applied")
  {
    auto planning =
        toy_planning(ToyArgs {.model = mode_member("fast_start")}, solver);
    planning.options.output_directory = tmpdir.string();
    PlanningLP planning_lp(std::move(planning));
    REQUIRE(planning_lp.resolve().has_value());
    planning_lp.write_out();
    const auto csv = slurp(tmpdir / "pricing.csv");
    CHECK(csv.starts_with(pricing_csv_header()));
    CHECK(csv.contains(",fast_start,fast_start,0,1,6,6,0,"));
  }

  SUBCASE("fallback recorded")
  {
    const ScopedEnv env {"GTOPT_TEST_FORCE_PRICING_FAIL", "1"};
    auto planning =
        toy_planning(ToyArgs {.model = mode_member("relaxed")}, solver);
    planning.options.output_directory = tmpdir.string();
    PlanningLP planning_lp(std::move(planning));
    REQUIRE(planning_lp.resolve().has_value());
    planning_lp.write_out();
    const auto csv = slurp(tmpdir / "pricing.csv");
    CHECK(csv.contains(",relaxed,fixed,1,0,"));
    CHECK(csv.contains("not optimal"));
  }

  std::filesystem::remove_all(tmpdir);
}
