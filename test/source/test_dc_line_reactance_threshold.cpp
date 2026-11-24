// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
// Copyright (c) 2026 Marcelo Matus. Dual-licensed, see LICENSING.md
//
// test_dc_line_reactance_threshold.cpp — exercises
// `PlanningLP::validate_line_reactance` and the configurable
// `model_options.dc_line_reactance_threshold` option.
//
// Under the legacy `tiny_reactance_mode = dc` the validator promotes any
// line with `|x_pu| = |X/V²| < threshold` to a DC line by rewriting its
// reactance schedule to 0.0 (which the Kirchhoff assembler then skips).
// Under the default `tiny_reactance_mode = tie` it keeps the value (the
// assemblers emit a zero-impedance tie), except when both buses pin
// inconsistent `reference_theta` values (reactance dropped → DC).
// The legacy-mode checks:
//   1. default threshold (1e-6) accessor;
//   2. scalar X promotion based on per-unit susceptance, not raw X;
//   3. vector-schedule per-entry promotion;
//   4. voltage scaling (a "small X" can be legitimate at low V);
//   5. user-configured tighter / looser threshold;
//   6. threshold = 0 disables promotion entirely;
//   7. zero / unset reactance is left alone.

#include <variant>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/basic_types.hpp>
#include <gtopt/line.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>

#include "require_value.hpp"

using namespace gtopt;

namespace
{

[[nodiscard]] auto legacy_dc_opts() -> PlanningOptions
{
  PlanningOptions opts;
  opts.model_options.tiny_reactance_mode = OptName {"dc"};
  return opts;
}

[[nodiscard]] auto make_planning_with_lines(
    Array<Line> lines, PlanningOptions opts = legacy_dc_opts()) -> Planning
{
  Planning planning;
  planning.options = std::move(opts);
  planning.system.line_array = std::move(lines);
  return planning;
}

[[nodiscard]] auto reactance_scalar(const Line& line) -> double
{
  REQUIRE(line.reactance.has_value());
  REQUIRE(std::holds_alternative<Real>(
      test_support::require_value(line.reactance)));
  return std::get<Real>(test_support::require_value(line.reactance));
}

[[nodiscard]] auto reactance_vector(const Line& line) -> std::vector<double>
{
  REQUIRE(line.reactance.has_value());
  REQUIRE(std::holds_alternative<std::vector<Real>>(
      test_support::require_value(line.reactance)));
  return std::get<std::vector<Real>>(
      test_support::require_value(line.reactance));
}

}  // namespace

TEST_CASE("dc_line_reactance_threshold default is 1e-6")
{
  const PlanningOptionsLP plp_opts {};
  CHECK(PlanningOptionsLP::default_dc_line_reactance_threshold
        == doctest::Approx(1e-6));
  CHECK(plp_opts.dc_line_reactance_threshold() == doctest::Approx(1e-6));
}

TEST_CASE("dc_line_reactance_threshold accessor honours model_options override")
{
  PlanningOptions opts;
  opts.model_options.dc_line_reactance_threshold = 1e-8;
  const PlanningOptionsLP plp_opts {opts};
  CHECK(plp_opts.dc_line_reactance_threshold() == doctest::Approx(1e-8));
}

TEST_CASE(
    "validate_line_reactance promotes scalar X with x_pu below threshold "
    "to DC")
{
  // V = 1 (per-unit default).  x_pu = X / 1 = 1e-7 < 1e-6 → clamp.
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "tiny_x",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 1e-7,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0]) == 0.0);
}

TEST_CASE("validate_line_reactance leaves a normal x_pu line untouched")
{
  // V = 1, X = 0.01 → x_pu = 0.01.  Well above 1e-6 default — keep.
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "normal",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 0.01,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0])
        == doctest::Approx(0.01));
}

TEST_CASE("validate_line_reactance accounts for V — high-V line stays in KVL")
{
  // V = 220 kV, X = 0.1 Ω → x_pu = 0.1 / 48400 ≈ 2.07e-6  > 1e-6 → keep.
  // (The legacy raw-|X| ≥ 1e-4 check would have kept it too, but for the
  // wrong reason — this test pins the dimensionally-correct behaviour.)
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "hv_line",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .voltage = 220.0,
          .reactance = 0.1,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0])
        == doctest::Approx(0.1));
}

TEST_CASE("validate_line_reactance catches V-vs-kV unit-typo lines via x_pu")
{
  // Realistic data-error: voltage entered in V instead of kV.
  // V = 220 000, X = 0.1 → x_pu ≈ 2.07e-12  ≪ 1e-6 → clamp.
  // The legacy raw-X ≥ 1e-4 check would NOT have caught this (X = 0.1
  // is "large"), demonstrating why the threshold belongs on x_pu.
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "typo_v_in_volts",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .voltage = 220'000.0,
          .reactance = 0.1,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0]) == 0.0);
}

TEST_CASE(
    "validate_line_reactance clamps offending entries in vector schedules")
{
  // Three-stage schedule, only the middle stage is below threshold.
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "mixed",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = std::vector<Real> {0.01, 1e-9, 0.02},
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines));
  PlanningLP::validate_line_reactance(planning);
  const auto vec = reactance_vector(planning.system.line_array[0]);
  REQUIRE(vec.size() == 3);
  CHECK(vec[0] == doctest::Approx(0.01));
  CHECK(vec[1] == 0.0);
  CHECK(vec[2] == doctest::Approx(0.02));
}

TEST_CASE("validate_line_reactance honours user-tightened threshold")
{
  // x_pu = 1e-7.  Default 1e-6 would clamp; user lowers threshold to
  // 1e-8 so this line stays in KVL.
  auto opts = legacy_dc_opts();
  opts.model_options.dc_line_reactance_threshold = 1e-8;
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "small_legit",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 1e-7,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines), std::move(opts));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0])
        == doctest::Approx(1e-7));
}

TEST_CASE("validate_line_reactance with threshold=0 disables promotion")
{
  // Even an absurdly tiny x_pu survives when the user sets threshold = 0.
  // This is the documented kill-switch.
  auto opts = legacy_dc_opts();
  opts.model_options.dc_line_reactance_threshold = 0.0;
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "kept",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 1e-30,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines), std::move(opts));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0])
        == doctest::Approx(1e-30));
}

TEST_CASE("validate_line_reactance leaves zero or unset reactance alone")
{
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "explicit_zero",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 0.0,
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
      {
          .uid = Uid {2},
          .name = "unset",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .tmax_ba = 100.0,
          .tmax_ab = 100.0,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines));
  PlanningLP::validate_line_reactance(planning);
  CHECK(reactance_scalar(planning.system.line_array[0]) == 0.0);
  CHECK_FALSE(planning.system.line_array[1].reactance.has_value());
}

// ── tiny_reactance_mode = tie (default) ─────────────────────────────

TEST_CASE("tiny_reactance_mode default is tie, names round-trip")
{
  const PlanningOptionsLP plp_opts {};
  CHECK(plp_opts.tiny_reactance_mode() == TinyReactanceMode::tie);
  CHECK(PlanningOptionsLP::default_tiny_reactance_mode
        == TinyReactanceMode::tie);
  for (const auto& entry : tiny_reactance_mode_entries) {
    const auto parsed = enum_from_name<TinyReactanceMode>(entry.name);
    REQUIRE(parsed.has_value());
    CHECK(test_support::require_value(parsed) == entry.value);
    CHECK(enum_name(test_support::require_value(parsed)) == entry.name);
  }
  CHECK_FALSE(enum_from_name<TinyReactanceMode>("short").has_value());

  PlanningOptions opts;
  opts.model_options.tiny_reactance_mode = OptName {"dc"};
  CHECK(PlanningOptionsLP {opts}.tiny_reactance_mode()
        == TinyReactanceMode::dc);
  opts.model_options.tiny_reactance_mode = OptName {"bogus"};
  CHECK(PlanningOptionsLP {opts}.tiny_reactance_mode()
        == TinyReactanceMode::tie);
}

TEST_CASE("classify_line_reactance — unset / zero / tiny / regular")
{
  using enum TinyReactanceMode;
  constexpr double thr = 1e-6;
  // Unset reactance is always a DC link.
  CHECK(classify_line_reactance(std::nullopt, 1.0, thr, tie)
        == LineKvlKind::dc);
  CHECK(classify_line_reactance(std::nullopt, 1.0, thr, dc) == LineKvlKind::dc);
  // Explicit zero: tie by default, DC in legacy mode, even with the
  // sub-threshold test disabled (threshold = 0).
  CHECK(classify_line_reactance(0.0, 1.0, thr, tie) == LineKvlKind::tie);
  CHECK(classify_line_reactance(0.0, 1.0, 0.0, tie) == LineKvlKind::tie);
  CHECK(classify_line_reactance(0.0, 1.0, thr, dc) == LineKvlKind::dc);
  // Tiny per-unit reactance (X/V² = 0.025/220² ≈ 5.2e-7).
  CHECK(classify_line_reactance(0.025, 220.0, thr, tie) == LineKvlKind::tie);
  CHECK(classify_line_reactance(0.025, 220.0, thr, dc) == LineKvlKind::dc);
  CHECK(classify_line_reactance(0.025, 220.0, 0.0, tie) == LineKvlKind::ac);
  // Regular line.
  CHECK(classify_line_reactance(10.0, 220.0, thr, tie) == LineKvlKind::ac);
  CHECK(classify_line_reactance(10.0, 220.0, thr, dc) == LineKvlKind::ac);
}

TEST_CASE("validate_line_reactance in tie mode keeps zero and tiny reactance")
{
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "tiny_x",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 1e-7,
      },
      {
          .uid = Uid {2},
          .name = "explicit_zero",
          .bus_a = Uid {2},
          .bus_b = Uid {3},
          .reactance = 0.0,
      },
      {
          .uid = Uid {3},
          .name = "mixed",
          .bus_a = Uid {1},
          .bus_b = Uid {3},
          .reactance = std::vector<Real> {0.01, 1e-9},
      },
      {
          .uid = Uid {4},
          .name = "unset",
          .bus_a = Uid {1},
          .bus_b = Uid {3},
      },
  };
  auto planning = make_planning_with_lines(std::move(lines), {});
  PlanningLP::validate_line_reactance(planning);
  const auto& la = planning.system.line_array;
  CHECK(reactance_scalar(la[0]) == doctest::Approx(1e-7));
  CHECK(reactance_scalar(la[1]) == 0.0);
  const auto vec = reactance_vector(la[2]);
  REQUIRE(vec.size() == 2);
  CHECK(vec[1] == doctest::Approx(1e-9));
  CHECK_FALSE(la[3].reactance.has_value());
}

TEST_CASE(
    "validate_line_reactance in tie mode drops a tie between buses pinned "
    "to different reference angles")
{
  Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "conflict",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 0.0,
      },
      {
          .uid = Uid {2},
          .name = "consistent",
          .bus_a = Uid {1},
          .bus_b = Uid {3},
          .reactance = 1e-8,
      },
  };
  auto planning = make_planning_with_lines(std::move(lines), {});
  planning.system.bus_array = {
      {.uid = Uid {1}, .name = "b1", .reference_theta = 0.0},
      {.uid = Uid {2}, .name = "b2", .reference_theta = 0.1},
      {.uid = Uid {3}, .name = "b3", .reference_theta = 0.0},
  };
  PlanningLP::validate_line_reactance(planning);
  // Infeasible tie → falls back to DC (reactance dropped).
  CHECK_FALSE(planning.system.line_array[0].reactance.has_value());
  // Consistent pins (both 0) → the tie is kept.
  CHECK(reactance_scalar(planning.system.line_array[1])
        == doctest::Approx(1e-8));
}
