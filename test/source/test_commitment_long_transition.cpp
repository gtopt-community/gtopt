// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_long_transition.cpp
 * @brief     Cyclic UC units whose min up / down time does not fit the
 *            typical period: explicit `long_transition` on / off / no_uc
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Unit `g1`: 10 $/MWh, no-load 100 $/h, start 1500 $, pmin 40, 100 MW,
 * cyclic; backup `g2` 100 $/MWh.  Hourly typical periods.
 */

#include <format>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace long_transition_test
{
namespace
{

struct Case
{
  int hours = 24;  ///< typical period length (hourly blocks)
  int nstages = 1;
  std::vector<double> load {};  ///< MW per hour (repeated every stage)
  std::string uc_extra {};  ///< extra g1 commitment fields
  std::string model_extra {};  ///< extra model_options fields
  bool two_units = false;  ///< add a second long unit g3
  double pmin = 40.0;  ///< commitment pmin of g1
};

[[nodiscard]] std::string list(const std::vector<double>& v)
{
  std::string out;
  for (std::size_t k = 0; k < v.size(); ++k) {
    out += std::format("{}{:.17g}", k == 0 ? "" : ", ", v[k]);
  }
  return out;
}

[[nodiscard]] std::string make_json(const Case& c)
{
  std::string blocks;
  std::string stages;
  std::string lmax;
  int uid = 1;
  for (int s = 0; s < c.nstages; ++s) {
    for (int h = 0; h < c.hours; ++h, ++uid) {
      blocks += std::format(
          R"({}{{"uid": {}, "duration": 1}})", uid == 1 ? "" : ", ", uid);
    }
    stages += std::format(
        R"({}{{"uid": {}, "first_block": {}, "count_block": {},
             "chronological": true}})",
        s == 0 ? "" : ", ",
        s + 1,
        s * c.hours,
        c.hours);
    lmax += std::format("{}[{}]", s == 0 ? "" : ", ", list(c.load));
  }
  const std::string g3 = c.two_units ? R"(,
      {"uid": 3, "name": "g3", "bus": "b1", "gcost": 20, "capacity": 100})"
                                     : "";
  const std::string uc3 = c.two_units ? R"(,
      {"uid": 2, "name": "g3_uc", "generator": 3, "cycle": "stage",
       "min_down_time": 30})"
                                      : "";
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 100000,
                                  "scale_objective": 1 {}}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "long_uc",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [{}]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 10, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 100,
        "capacity": 1000}}{}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "noload_cost": 100, "startup_cost": 1500,
                           "pmin": {}, "cycle": "stage" {}}}{}]}}
}})",
      c.model_extra,
      blocks,
      stages,
      lmax,
      g3,
      c.pmin,
      c.uc_extra,
      uc3);
}

struct Solved
{
  double obj;
  Index ncols;
  Index nrows;
};

[[nodiscard]] Solved solve(const Case& c)
{
  Planning base;
  base.merge(parse_planning_json(make_json(c)));
  PlanningLP plp(std::move(base));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  REQUIRE(*res == 1);
  const auto& li = plp.systems().front().front().linear_interface();
  return {
      .obj = li.get_obj_value(),
      .ncols = li.get_numcols(),
      .nrows = li.get_numrows(),
  };
}

[[nodiscard]] ValidationResult validate(const Case& c)
{
  Planning p;
  p.merge(parse_planning_json(make_json(c)));
  return validate_planning(p);
}

[[nodiscard]] bool contains(const std::vector<std::string>& v,
                            std::string_view what)
{
  return std::ranges::any_of(
      v, [&](const auto& s) { return s.find(what) != std::string::npos; });
}

constexpr std::string_view kLong =
    R"(, "min_up_time": 72, "min_down_time": 48)";

}  // namespace
}  // namespace long_transition_test

TEST_CASE(  // NOLINT
    "Long transition - undeclared units are one validation error")
{
  using namespace long_transition_test;
  // typical day: MUT 72 h / MDT 48 h of g1 and MDT 30 h of g3 > 24 h
  const Case c {.load = std::vector<double>(24, 50.0),
                .uc_extra = std::string(kLong),
                .two_units = true};
  const auto r = validate(c);
  REQUIRE(r.errors.size() >= 1);
  CHECK(contains(r.errors, "'g1_uc' stage uid=1: min_up_time = 72 h"));
  CHECK(contains(r.errors, "min_down_time = 48 h > typical period 24 h"));
  CHECK(contains(r.errors, "'g3_uc' stage uid=1: min_down_time = 30 h"));
  CHECK(contains(r.errors, R"("long_transition": "no_uc")"));
  // a 2-day typical period: MUT 72 still too long, MDT 48 fits
  const Case two_days {.hours = 48,
                       .load = std::vector<double>(48, 50.0),
                       .uc_extra = std::string(kLong)};
  const auto r2 = validate(two_days);
  CHECK(contains(r2.errors, "min_up_time = 72 h > typical period 48 h"));
  CHECK_FALSE(contains(r2.errors, "min_down_time = 48 h"));
  // a typical week: everything fits, nothing to declare
  const Case week {.hours = 168,
                   .load = std::vector<double>(168, 50.0),
                   .uc_extra = std::string(kLong)};
  CHECK_FALSE(contains(validate(week).errors, "long_transition"));
}

TEST_CASE(  // NOLINT
    "Long transition - on / off / no_uc on a typical day")
{
  using namespace long_transition_test;
  // "on": committed all day, MUT/MDT rows not emitted (same LP as the
  // unit without them), pmin 40 binds at 30 MW load ⇒ the 10 MW excess
  // is impossible, so use 50 MW: 1200·10 + 24·100 = 14400
  Case on {.load = std::vector<double>(24, 50.0),
           .uc_extra = std::string(kLong) + R"(, "long_transition": "on")"};
  const auto a = solve(on);
  CHECK(a.obj == doctest::Approx(14400.0));
  Case plain = on;
  plain.uc_extra = R"(, "long_transition": "on")";
  const auto b = solve(plain);
  CHECK(a.nrows == b.nrows);
  CHECK(a.ncols == b.ncols);
  CHECK(b.obj == doctest::Approx(14400.0));
  auto vr = validate(on);
  CHECK(vr.errors.empty());
  CHECK(contains(vr.warnings, "'g1_uc': long_transition on (unit)"));
  CHECK(contains(vr.warnings, "Long-transition summary"));

  // "off": unavailable, the backup serves: 1200·100
  Case off = on;
  off.uc_extra = std::string(kLong) + R"(, "long_transition": "off")";
  CHECK(solve(off).obj == doctest::Approx(120000.0));

  // "no_uc": continuous 0..100, pmin relaxed: 30 MW load < pmin served
  // by g1 at 10 $/MWh without no-load / starts: 720·10
  Case nouc {
      .load = std::vector<double>(24, 30.0),
      .uc_extra = std::string(kLong) + R"(, "long_transition": "no_uc")"};
  const auto c = solve(nouc);
  CHECK(c.obj == doctest::Approx(7200.0));
  CHECK(c.ncols < a.ncols);
}

TEST_CASE(  // NOLINT
    "Long transition - per-stage schedule and transitions between stages")
{
  using namespace long_transition_test;
  // two typical days: off then on ⇒ backup day (120000) + on day
  // (14400) + one start between them (1500, × 1)
  const Case c {
      .nstages = 2,
      .load = std::vector<double>(24, 50.0),
      .uc_extra =
          std::string(kLong) + R"(, "long_transition_schedule": ["off", "on"])",
  };
  CHECK(validate(c).errors.empty());
  CHECK(solve(c).obj == doctest::Approx(120000.0 + 14400.0 + 1500.0));
  // on then off: no shutdown cost set ⇒ no transition cost
  Case d = c;
  d.uc_extra =
      std::string(kLong) + R"(, "long_transition_schedule": ["on", "off"])";
  CHECK(solve(d).obj == doctest::Approx(14400.0 + 120000.0));
  // mixing no_uc and on: 50 MW day continuous (12000) + on day (14400)
  Case e = c;
  e.uc_extra =
      std::string(kLong) + R"(, "long_transition_schedule": ["no_uc", "on"])";
  CHECK(solve(e).obj == doctest::Approx(12000.0 + 14400.0));
  // invalid entry
  Case bad = c;
  bad.uc_extra =
      std::string(kLong) + R"(, "long_transition_schedule": ["maybe", "on"])";
  CHECK(contains(validate(bad).errors, "'on', 'off' or 'no_uc'"));
}

TEST_CASE(  // NOLINT
    "Long transition - planning default still warns per unit")
{
  using namespace long_transition_test;
  const Case c {.load = std::vector<double>(24, 50.0),
                .uc_extra = std::string(kLong),
                .model_extra = R"(, "long_transition": "on")",
                .two_units = true};
  const auto r = validate(c);
  CHECK_FALSE(contains(r.errors, "long_transition"));
  CHECK(contains(r.warnings, "'g1_uc': long_transition on (model_options"));
  CHECK(contains(r.warnings, "'g3_uc': long_transition on (model_options"));
}

TEST_CASE(  // NOLINT
    "Long transition - a typical week keeps MUT/MDT and wraps them")
{
  using namespace long_transition_test;
  // 168 h week, MUT 72 / MDT 48 fit, pmin 0 (so the unit may idle on
  // through zero-load hours).  Load 50 MW during 4 h:
  //   one start, on 72 h: 200·10 + 72·100 + 1500 = 10700
  //   (always on: 2000 + 16800)
  std::vector<double> mid(168, 0.0);
  for (int h = 30; h < 34; ++h) {
    mid[static_cast<std::size_t>(h)] = 50.0;
  }
  const Case inside {
      .hours = 168, .load = mid, .uc_extra = std::string(kLong), .pmin = 0.0};
  CHECK(solve(inside).obj == doctest::Approx(10700.0));
  // the same run across the week boundary (166, 167, 0, 1): the 72 h
  // window wraps — same cost
  std::vector<double> edge(168, 0.0);
  for (const int h : {166, 167, 0, 1}) {
    edge[static_cast<std::size_t>(h)] = 50.0;
  }
  const Case across {
      .hours = 168, .load = edge, .uc_extra = std::string(kLong), .pmin = 0.0};
  CHECK(solve(across).obj == doctest::Approx(10700.0));
}
