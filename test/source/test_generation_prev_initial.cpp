// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_generation_prev_initial.cpp
 * @brief     `generation_prev` at the horizon start reads
 *            `Generator.initial_generation`
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `<attr>_prev` at the first chronological block used to resolve to the
 * constant 0 (cold start).  PLEXOS uses the unit's raw Initial Generation
 * there, so hard ramp user constraints went infeasible at h1 on CEN PCP
 * cases (PANGUEramp: `gen − gen_prev ≤ 60` with PANGUE_U1 starting on at
 * pmin 80).  Pins:
 *
 *  1. horizon start: `generation_prev` = `initial_generation` (a constant
 *     folded into the row); unset → 0 (legacy);
 *  2. the ramp row binds exactly at its RHS against the initial value;
 *  3. a later stage of the SAME phase chains to the previous stage's last
 *     block (same LP), never the initial value;
 *  4. the first stage of a LATER phase never sees the initial value;
 *  5. native `Commitment` ramp rows: `initial_power` first, then
 *     `Generator.initial_generation` as fallback.
 *
 * All values are read off the objective (scale_objective = 1):
 * gen at 10 $/MWh, unserved demand at 1000 $/MWh.
 */

#include <format>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/monolithic_method.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace test_generation_prev_initial_ns
{
namespace
{

constexpr std::string_view kRampUp =
    "generator('g1').generation - generator('g1').generation_prev <= 60";

/// One-bus case: `demands[t]` MW in one-hour chronological stage t (one
/// block each), grouped into phases by `layout`, a ramp user constraint
/// `ramp_expr` (empty → none) and extra generator / system JSON.
[[nodiscard]] std::string ramp_case(const std::vector<double>& demands,
                                    const std::vector<int>& layout,
                                    std::string_view gen_extra,
                                    std::string_view ramp_expr,
                                    std::string_view system_extra = "",
                                    std::string_view model_extra = "")
{
  std::string blocks;
  std::string stages;
  std::string lmax;
  for (std::size_t t = 0; t < demands.size(); ++t) {
    blocks += std::format(R"({{"uid":{},"duration":1}},)", t + 1);
    stages += std::format(R"({{"uid":{},"first_block":{},"count_block":1,)"
                          R"("chronological":true}},)",
                          t + 1,
                          t);
    lmax += std::format("[{}],", demands[t]);
  }
  blocks.pop_back();
  stages.pop_back();
  lmax.pop_back();

  std::string phases;
  int first = 0;
  for (std::size_t p = 0; p < layout.size(); ++p) {
    phases += std::format(R"({{"uid":{},"first_stage":{},"count_stage":{}}},)",
                          p + 1,
                          first,
                          layout[p]);
    first += layout[p];
  }
  phases.pop_back();

  std::string ucs;
  if (!ramp_expr.empty()) {
    ucs = std::format(R"(,"user_constraint_array":[{{"uid":1,"name":"ramp",)"
                      R"("expression":"{}"}}])",
                      ramp_expr);
  }

  return std::format(
      R"({{"options":{{"method":"monolithic","annual_discount_rate":0.0,)"
      R"("constraint_mode":"strict",)"
      R"("model_options":{{"use_single_bus":true,"demand_fail_cost":1000,)"
      R"("scale_objective":1{}}}}},)"
      R"("simulation":{{"block_array":[{}],"stage_array":[{}],)"
      R"("scenario_array":[{{"uid":1,"probability_factor":1}}],)"
      R"("phase_array":[{}]}},)"
      R"("system":{{"name":"gen_prev_initial",)"
      R"("bus_array":[{{"uid":1,"name":"b1"}}],)"
      R"("demand_array":[{{"uid":1,"name":"d1","bus":"b1",)"
      R"("lmax":[{}]}}],)"
      R"("generator_array":[{{"uid":1,"name":"g1","bus":"b1","gcost":10,)"
      R"("capacity":1000{}}}]{}{}}}}})",
      model_extra,
      blocks,
      stages,
      phases,
      lmax,
      gen_extra,
      system_extra,
      ucs);
}

/// Total objective over every phase LP, or nullopt when the solve fails.
[[nodiscard]] std::optional<double> solve_total(const std::string& json)
{
  PlanningLP plp(parse_planning_json(json));
  MonolithicMethod solver;
  auto res = solver.solve(plp, {});
  if (!res.has_value()) {
    return std::nullopt;
  }
  double obj = 0.0;
  const auto num_phases = static_cast<Index>(plp.simulation().phases().size());
  for (Index p = 0; p < num_phases; ++p) {
    auto& li =
        plp.system(first_scene_index(), PhaseIndex {p}).linear_interface();
    if (!li.is_optimal()) {
      return std::nullopt;
    }
    obj += li.get_obj_value();
  }
  return obj;
}

}  // namespace
}  // namespace test_generation_prev_initial_ns

using namespace test_generation_prev_initial_ns;

TEST_CASE(  // NOLINT
    "generation_prev - PANGUE pattern is feasible only with initial_generation")
{
  // pmin 80 forces gen ≥ 80 at h1; the ramp row allows gen ≤ 60 + prev.
  const std::vector<double> demand {100.0, 100.0, 100.0};

  SUBCASE("unset -> cold start 0 -> infeasible at h1")
  {
    CHECK_FALSE(solve_total(ramp_case(demand, {3}, R"(,"pmin":80)", kRampUp))
                    .has_value());
  }

  SUBCASE("initial_generation = 80 -> feasible, demand served")
  {
    const auto obj = solve_total(ramp_case(
        demand, {3}, R"(,"pmin":80,"initial_generation":80)", kRampUp));
    REQUIRE(obj.has_value());
    CHECK(obj.value_or(-1.0) == doctest::Approx(3000.0));
  }
}

TEST_CASE(  // NOLINT
    "generation_prev - ramp row binds exactly at RHS against the initial value")
{
  // demand 100 at h1, ramp 60:
  //   initial 20 → gen = 80 (Δ = 60 at the RHS), 20 MW unserved;
  //   unset      → gen = 60, 40 MW unserved.
  const std::vector<double> demand {100.0};
  const auto with_init = solve_total(
      ramp_case(demand, {1}, R"(,"initial_generation":20)", kRampUp));
  const auto cold = solve_total(ramp_case(demand, {1}, "", kRampUp));
  REQUIRE(with_init.has_value());
  REQUIRE(cold.has_value());
  CHECK(with_init.value_or(-1.0)
        == doctest::Approx((10.0 * 80.0) + (1000.0 * 20.0)));
  CHECK(cold.value_or(-1.0)
        == doctest::Approx((10.0 * 60.0) + (1000.0 * 40.0)));

  SUBCASE("ramp-down form reads the same initial value")
  {
    // gen_prev − gen ≤ 60 with initial 161.7 → gen ≥ 101.7 although the
    // demand is 0: the surplus has nowhere to go → infeasible, while the
    // cold start (prev = 0) is trivially feasible.
    constexpr std::string_view ramp_down =
        "generator('g1').generation_prev - generator('g1').generation <= 60";
    CHECK_FALSE(
        solve_total(
            ramp_case({0.0}, {1}, R"(,"initial_generation":161.7)", ramp_down))
            .has_value());
    CHECK(solve_total(ramp_case({0.0}, {1}, "", ramp_down)).value_or(-1.0)
          == doctest::Approx(0.0));
  }
}

TEST_CASE(  // NOLINT
    "generation_prev - later stage of the same phase chains to the previous "
    "stage, never the initial value")
{
  // One phase, four one-block stages, demand [100, 100, 200, 200],
  // initial 20, ramp 60:
  //   s1 ≤ 80 → 80 (20 unserved); s2 ≤ 140 → 100;
  //   s3 ≤ 160 → 160 (40 unserved); s4 ≤ 220 → 200.
  // (s3 reading the initial value would cap it at 80, a 0 at 60.)
  const auto obj = solve_total(ramp_case({100.0, 100.0, 200.0, 200.0},
                                         {4},
                                         R"(,"initial_generation":20)",
                                         kRampUp));
  REQUIRE(obj.has_value());
  CHECK(obj.value_or(-1.0)
        == doctest::Approx((10.0 * (80.0 + 100.0 + 160.0 + 200.0))
                           + (1000.0 * (20.0 + 40.0))));
}

TEST_CASE(  // NOLINT
    "generation_prev - first stage of a later phase never sees the initial "
    "value")
{
  // Two phases of two stages.  Phase 1: s1 ≤ 80 → 80, s2 ≤ 140 → 100.
  // Phase 2 is another LP: `_prev` at its first block has no prior column
  // and carries no cross-phase state link, so it is the legacy 0 —
  // NOT the horizon initial value (which would allow 80):
  //   s3 ≤ 60 → 60 (140 unserved), s4 ≤ 120 → 120 (80 unserved).
  const auto obj = solve_total(ramp_case({100.0, 100.0, 200.0, 200.0},
                                         {2, 2},
                                         R"(,"initial_generation":20)",
                                         kRampUp));
  REQUIRE(obj.has_value());
  CHECK(obj.value_or(-1.0)
        == doctest::Approx((10.0 * (80.0 + 100.0 + 60.0 + 120.0))
                           + (1000.0 * (20.0 + 140.0 + 80.0))));
}

TEST_CASE(  // NOLINT
    "generation_prev - native Commitment ramp falls back to "
    "initial_generation")
{
  // Commitment ramp_up = 60, online at t = 0, demand 100 at h1:
  //   p[0] ≤ p_prev + 60.
  const std::vector<double> demand {100.0};
  const auto commitment = [](std::string_view extra)
  {
    return std::format(
        R"(,"commitment_array":[{{"uid":1,"name":"c1","generator":1,)"
        R"("ramp_up":60,"initial_status":1,"relax":true{}}}])",
        extra);
  };

  SUBCASE("neither set -> p_prev = 0 -> gen 60")
  {
    CHECK(solve_total(ramp_case(demand, {1}, "", "", commitment("")))
              .value_or(-1.0)
          == doctest::Approx((10.0 * 60.0) + (1000.0 * 40.0)));
  }
  SUBCASE("initial_generation only -> p_prev = 80 -> demand served")
  {
    CHECK(
        solve_total(
            ramp_case(
                demand, {1}, R"(,"initial_generation":80)", "", commitment("")))
            .value_or(-1.0)
        == doctest::Approx(1000.0));
  }
  SUBCASE("initial_power wins over initial_generation")
  {
    CHECK(solve_total(ramp_case(demand,
                                {1},
                                R"(,"initial_generation":80)",
                                "",
                                commitment(R"(,"initial_power":20)")))
              .value_or(-1.0)
          == doctest::Approx((10.0 * 80.0) + (1000.0 * 20.0)));
  }
}

TEST_CASE("generation_prev - initial_generation JSON round-trip")  // NOLINT
{
  const auto planning = parse_planning_json(
      ramp_case({100.0}, {1}, R"(,"initial_generation":42.5)", kRampUp));
  REQUIRE(planning.system.generator_array.size() == 1);
  CHECK(
      planning.system.generator_array.front().initial_generation.value_or(-1.0)
      == doctest::Approx(42.5));
}

TEST_CASE(  // NOLINT
    "generation_prev - prev_stage_link = none restores the legacy stage-start "
    "0")
{
  // Same 4-stage, one-phase case as the same-phase link test, with the
  // stage link switched off: the horizon start still reads the initial
  // value (s1 ≤ 80), but every later stage start reads 0 again:
  //   s1 → 80 (20 unserved), s2 ≤ 60 → 60 (40), s3 ≤ 60 → 60 (140),
  //   s4 ≤ 60 → 60 (140).
  const std::vector<double> demand {100.0, 100.0, 200.0, 200.0};
  const auto legacy = solve_total(ramp_case(demand,
                                            {4},
                                            R"(,"initial_generation":20)",
                                            kRampUp,
                                            "",
                                            R"(,"prev_stage_link":"none")"));
  REQUIRE(legacy.has_value());
  CHECK(legacy.value_or(-1.0)
        == doctest::Approx((10.0 * (80.0 + 60.0 + 60.0 + 60.0))
                           + (1000.0 * (20.0 + 40.0 + 140.0 + 140.0))));

  SUBCASE("explicit stage equals the default")
  {
    const auto linked = solve_total(ramp_case(demand,
                                              {4},
                                              R"(,"initial_generation":20)",
                                              kRampUp,
                                              "",
                                              R"(,"prev_stage_link":"stage")"));
    CHECK(linked.value_or(-1.0)
          == doctest::Approx((10.0 * (80.0 + 100.0 + 160.0 + 200.0))
                             + (1000.0 * (20.0 + 40.0))));
  }

  SUBCASE("an unknown value is a validation error")
  {
    auto planning = parse_planning_json(
        ramp_case(demand, {4}, "", kRampUp, "", R"(,"prev_stage_link":"x")"));
    const auto result = validate_planning(planning);
    const auto hit = std::ranges::any_of(
        result.errors,
        [](const std::string& e) { return e.contains("prev_stage_link"); });
    CHECK(hit);
  }
}

TEST_CASE(  // NOLINT
    "generation_prev - a later-phase start is counted for the WARN summary")
{
  const std::vector<double> demand {100.0, 100.0, 200.0, 200.0};
  const auto counts = [&](const std::vector<int>& layout)
  {
    PlanningLP plp(parse_planning_json(
        ramp_case(demand, layout, R"(,"initial_generation":20)", kRampUp)));
    return plp.simulation().prev_at_phase_start_counts();
  };

  SUBCASE("two phases: the phase-2 start of 'ramp' is recorded")
  {
    const auto c = counts({2, 2});
    REQUIRE(c.contains("ramp"));
    const auto& terms = c.at("ramp");
    REQUIRE(terms.size() == 1);
    CHECK(terms.begin()->first == "generator(g1).generation_prev");
    CHECK(terms.begin()->second >= 1);
  }
  SUBCASE("one phase: nothing is recorded")
  {
    CHECK(counts({4}).empty());
  }
}
