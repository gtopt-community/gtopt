// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_active_stages.cpp
 * @brief     Per-stage `active` schedules of Commitment / SimpleCommitment:
 *            integer-free fill stages, boundaries, SDDP
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * 4 chronological stages × 2 hourly blocks.  Load 50 MW in stages 1–2
 * and 30 MW in stages 3–4.  `g1` 10 $/MWh, 100 MW, own pmin 0; backup
 * `g2` 100 $/MWh.  Commitment: pmin 40, no-load 100 $/h, start 1500 $,
 * initially off.  SimpleCommitment: dispatch_pmin 40.
 */

#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace active_stages_test
{
namespace
{

[[nodiscard]] std::string make_json(std::string_view uc, bool phases)
{
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 100000,
                                  "scale_objective": 1}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}, {{"uid": 2, "duration": 1}},
                    {{"uid": 3, "duration": 1}}, {{"uid": 4, "duration": 1}},
                    {{"uid": 5, "duration": 1}}, {{"uid": 6, "duration": 1}},
                    {{"uid": 7, "duration": 1}}, {{"uid": 8, "duration": 1}}],
    "stage_array": [
      {{"uid": 1, "first_block": 0, "count_block": 2, "chronological": true}},
      {{"uid": 2, "first_block": 2, "count_block": 2, "chronological": true}},
      {{"uid": 3, "first_block": 4, "count_block": 2, "chronological": true}},
      {{"uid": 4, "first_block": 6, "count_block": 2, "chronological": true}}],
    {}
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "active_stages",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [[50, 50], [50, 50], [30, 30], [30, 30]]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}],
    {}}}
}})",
      phases
          ? R"("phase_array": [{"uid": 1, "first_stage": 0, "count_stage": 2},
                                 {"uid": 2, "first_stage": 2, "count_stage": 2}],)"
          : "",
      uc);
}

[[nodiscard]] std::string full_uc(std::string_view active)
{
  return std::format(
      R"("commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
         "pmin": 40, "noload_cost": 100, "startup_cost": 1500,
         "initial_status": 0 {}}}])",
      active.empty() ? std::string {}
                     : std::format(R"(, "active": {})", active));
}

[[nodiscard]] std::string simple_uc(std::string_view active)
{
  return std::format(
      R"("simple_commitment_array": [{{"uid": 1, "name": "g1_suc",
         "generator": 1, "dispatch_pmin": 40 {}}}])",
      active.empty() ? std::string {}
                     : std::format(R"(, "active": {})", active));
}

/// Integer columns of the LP of phase @p phase (scene 0).
[[nodiscard]] int count_integers(const PlanningLP& plp, std::size_t phase)
{
  const auto& li = plp.systems()
                       .front()[PhaseIndex {static_cast<int>(phase)}]
                       .linear_interface();
  int nint = 0;
  for (Index c = 0; c < li.get_numcols(); ++c) {
    nint += li.is_integer(ColIndex {c}) ? 1 : 0;
  }
  return nint;
}

struct Solved
{
  double obj;
  int nint;  ///< integer columns in the LP
};

[[nodiscard]] Solved solve(const std::string& json)
{
  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP plp(std::move(base));
  // count before the solve (a MIP solve may fix the integers afterwards
  // for dual recovery)
  const int nint = count_integers(plp, 0);
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  const auto& li = plp.systems().front().front().linear_interface();
  return {.obj = li.get_obj_value(), .nint = nint};
}

}  // namespace
}  // namespace active_stages_test

TEST_CASE(  // NOLINT
    "Commitment active per stage - integer-free fill stages")
{
  using namespace active_stages_test;
  // active stages 1–2: each stage starts from initial_status = 0:
  //   2·50·10 + 2·100 + 1500 = 2700 per stage
  // inactive stages 3–4: g1 continuous with its own pmin 0 (the
  //   commitment pmin 40 is not enforced): 2·30·10 = 600 per stage
  const auto a = solve(make_json(full_uc("[1, 1, 0, 0]"), /*phases=*/false));
  CHECK(a.obj == doctest::Approx((2.0 * 2700.0) + (2.0 * 600.0)));
  CHECK(a.nint == 12);  // u, v, w per block of stages 1–2 (all binary)
  // boundaries active → inactive → active: stage 3 restarts from off
  //   stage 3 with load 30 < pmin 40: the backup serves 2·30·100
  const auto b = solve(make_json(full_uc("[1, 0, 1, 0]"), /*phases=*/false));
  CHECK(b.obj == doctest::Approx(2700.0 + 1000.0 + 6000.0 + 600.0));
  CHECK(b.nint == 12);
  // all active: stages 3–4 by the backup
  const auto c = solve(make_json(full_uc(""), /*phases=*/false));
  CHECK(c.obj == doctest::Approx((2.0 * 2700.0) + (2.0 * 6000.0)));
  CHECK(c.nint == 24);
  // never active: plain economic dispatch, no integers
  const auto d = solve(make_json(full_uc("0"), /*phases=*/false));
  CHECK(d.obj == doctest::Approx(2000.0 + 1200.0));
  CHECK(d.nint == 0);
}

TEST_CASE(  // NOLINT
    "SimpleCommitment active per stage - integer-free fill stages")
{
  using namespace active_stages_test;
  // active 1–2: 2·50·10 per stage; inactive 3–4: continuous 2·30·10
  const auto a = solve(make_json(simple_uc("[1, 1, 0, 0]"), /*phases=*/false));
  CHECK(a.obj == doctest::Approx(2000.0 + 1200.0));
  CHECK(a.nint == 12);
  // all active: load 30 < dispatch_pmin ⇒ backup in stages 3–4
  const auto c = solve(make_json(simple_uc(""), /*phases=*/false));
  CHECK(c.obj == doctest::Approx(2000.0 + 12000.0));
  CHECK(c.nint == 24);
}

TEST_CASE(  // NOLINT
    "Commitment active per stage - SDDP with an integer-free fill phase")
{
  using namespace active_stages_test;
  // phase 1 = stages 1–2 (MIP), phase 2 = stages 3–4 (inactive): same
  // optimum as the monolithic solve, 6600
  Planning base;
  base.merge(
      parse_planning_json(make_json(full_uc("[1, 1, 0, 0]"), /*phases=*/true)));
  PlanningLP plp(std::move(base));
  SDDPOptions opts;
  opts.max_iterations = 5;
  opts.convergence_tol = 1e-6;
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  CHECK(results->back().upper_bound == doctest::Approx(6600.0));
  CHECK(results->back().lower_bound == doctest::Approx(6600.0));
}

TEST_CASE(  // NOLINT
    "continuous_phases relaxes every integer of the matching phases")
{
  using namespace active_stages_test;
  // 4 phases × 1 stage; Commitment + SimpleCommitment on two units
  const auto json = [](std::string_view cont)
  {
    auto j = make_json(std::string(full_uc("")) + R"(,
    "simple_commitment_array": [{"uid": 1, "name": "g2_suc",
       "generator": 2, "dispatch_pmin": 1}])",
                       /*phases=*/false);
    const std::string stages =
        R"("phase_array": [{"uid": 1, "first_stage": 0, "count_stage": 1},
                       {"uid": 2, "first_stage": 1, "count_stage": 1},
                       {"uid": 3, "first_stage": 2, "count_stage": 1},
                       {"uid": 4, "first_stage": 3, "count_stage": 1}],
    "scenario_array")";
    j.replace(j.find(R"("scenario_array")"),
              std::string_view {R"("scenario_array")"}.size(),
              stages);
    if (!cont.empty()) {
      const std::string opt =
          std::format(R"("continuous_phases": "{}", "use_single_bus")", cont);
      j.replace(j.find(R"("use_single_bus")"),
                std::string_view {R"("use_single_bus")"}.size(),
                opt);
    }
    return j;
  };
  const auto counts = [&](std::string_view cont)
  {
    Planning base;
    base.merge(parse_planning_json(json(cont)));
    PlanningLP plp(std::move(base));
    std::vector<int> n;
    n.reserve(4);
    for (std::size_t p = 0; p < 4; ++p) {
      n.push_back(count_integers(plp, p));
    }
    return n;
  };
  // per phase: Commitment u per block (2) + SimpleCommitment u (2)
  CHECK(counts("") == std::vector<int> {12, 12, 12, 12});
  CHECK(counts("2:") == std::vector<int> {12, 12, 0, 0});
  CHECK(counts("all") == std::vector<int> {0, 0, 0, 0});
  CHECK(counts("none") == std::vector<int> {12, 12, 12, 12});
}
