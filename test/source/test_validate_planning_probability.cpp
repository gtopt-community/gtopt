// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_validate_planning_probability.cpp
 * @brief     Scenario/scene probability validation and rescaling in
 *            validate_planning, for every probability_rescale mode
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Scenario `probability_factor` values are GLOBAL probabilities (the
 * scene weight is the sum of its scenarios' factors).  Rules: total 1.0
 * is left untouched; every scene summing to 1.0 is read as conditional
 * per-scene probabilities (equal scene weights); any other total is
 * divided by the global total (scene weights preserved).  Always
 * warnings, never errors; `none` only warns.
 */

#include <algorithm>
#include <array>
#include <format>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Planning vpp_make_minimal_planning()
{
  Planning p;
  p.system.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  p.simulation.block_array = {
      {
          .uid = Uid {1},
          .duration = 1.0,
      },
  };
  p.simulation.stage_array = {
      {
          .uid = Uid {1},
          .count_block = 1,
      },
  };
  return p;
}

/// 2 scenes ("wet" = scenarios 0,1 and "dry" = scenarios 2,3).
[[nodiscard]] Planning vpp_make_two_scene_planning(double p0,
                                                   double p1,
                                                   double p2,
                                                   double p3)
{
  auto p = vpp_make_minimal_planning();
  p.simulation.scenario_array = {
      {
          .uid = Uid {1},
          .probability_factor = p0,
      },
      {
          .uid = Uid {2},
          .probability_factor = p1,
      },
      {
          .uid = Uid {3},
          .probability_factor = p2,
      },
      {
          .uid = Uid {4},
          .probability_factor = p3,
      },
  };
  p.simulation.scene_array = {
      {
          .uid = Uid {1},
          .name = "wet",
          .active = true,
          .first_scenario = 0,
          .count_scenario = 2,
      },
      {
          .uid = Uid {2},
          .name = "dry",
          .active = true,
          .first_scenario = 2,
          .count_scenario = 2,
      },
  };
  return p;
}

[[nodiscard]] double vpp_prob(const Planning& p, std::size_t i)
{
  return p.simulation.scenario_array[i].probability_factor.value_or(-1.0);
}

[[nodiscard]] bool vpp_has_message(const std::vector<std::string>& msgs,
                                   std::string_view needle)
{
  return std::ranges::any_of(
      msgs, [needle](const std::string& m) { return m.contains(needle); });
}

/// Every mode, including "unset" (defaults to runtime).
constexpr auto vpp_all_modes =
    std::to_array<std::optional<ProbabilityRescaleMode>>({
        std::nullopt,
        ProbabilityRescaleMode::none,
        ProbabilityRescaleMode::build,
        ProbabilityRescaleMode::runtime,
    });

/// Modes that rescale at validation time.
constexpr auto vpp_rescale_modes =
    std::to_array<std::optional<ProbabilityRescaleMode>>({
        std::nullopt,
        ProbabilityRescaleMode::build,
        ProbabilityRescaleMode::runtime,
    });

}  // namespace

TEST_CASE(  // NOLINT
    "validate_planning - a normalised global distribution keeps its scene "
    "weights in every probability_rescale mode")
{
  for (const auto mode : vpp_all_modes) {
    CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
    auto p = vpp_make_two_scene_planning(0.3, 0.3, 0.2, 0.2);
    p.simulation.probability_rescale = mode;
    const auto result = validate_planning(p);
    CHECK(result.ok());
    CHECK(result.warnings.empty());
    CHECK(vpp_prob(p, 0) == doctest::Approx(0.3));
    CHECK(vpp_prob(p, 1) == doctest::Approx(0.3));
    CHECK(vpp_prob(p, 2) == doctest::Approx(0.2));
    CHECK(vpp_prob(p, 3) == doctest::Approx(0.2));
    CHECK(vpp_prob(p, 0) + vpp_prob(p, 1) == doctest::Approx(0.6));
    CHECK(vpp_prob(p, 2) + vpp_prob(p, 3) == doctest::Approx(0.4));
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - conditional per-scene probabilities get equal "
    "scene weights in every probability_rescale mode")
{
  for (const auto mode : vpp_all_modes) {
    CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
    // Each scene sums to 1.0 (conditional), total 2.0.
    auto p = vpp_make_two_scene_planning(0.4, 0.6, 0.5, 0.5);
    p.simulation.probability_rescale = mode;
    const auto result = validate_planning(p);
    CHECK(result.ok());
    CHECK(vpp_has_message(result.warnings, "sum to 2.000000 across 2 scene"));
    CHECK_FALSE(vpp_has_message(result.warnings, "representative-period"));
    CHECK_FALSE(vpp_has_message(result.warnings, "divided by their total"));
    if (mode == ProbabilityRescaleMode::none) {
      CHECK(vpp_has_message(result.warnings, "left unchanged"));
      CHECK(vpp_has_message(result.warnings, "Every scene sums to 1.0"));
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.4));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.6));
      CHECK(vpp_prob(p, 2) == doctest::Approx(0.5));
      CHECK(vpp_prob(p, 3) == doctest::Approx(0.5));
    } else {
      CHECK(vpp_has_message(result.warnings,
                            "interpreted as CONDITIONAL per-scene "
                            "probabilities"));
      CHECK(vpp_has_message(result.warnings, "equal weight 0.500000"));
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.2));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.3));
      CHECK(vpp_prob(p, 2) == doctest::Approx(0.25));
      CHECK(vpp_prob(p, 3) == doctest::Approx(0.25));
    }
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - any other total is divided by the global total, "
    "preserving scene weights, in every probability_rescale mode")
{
  // Scene sums 0.9 / 0.6 (total 1.5, scene weights 0.6 / 0.4).
  for (const auto mode : vpp_all_modes) {
    CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
    auto p = vpp_make_two_scene_planning(0.45, 0.45, 0.3, 0.3);
    p.simulation.probability_rescale = mode;
    const auto result = validate_planning(p);
    CHECK(result.ok());
    CHECK(vpp_has_message(result.warnings, "sum to 1.500000"));
    // 1.5 is close to 1: no representative-period hint.
    CHECK_FALSE(vpp_has_message(result.warnings, "representative-period"));
    CHECK_FALSE(vpp_has_message(result.warnings, "CONDITIONAL"));
    if (mode == ProbabilityRescaleMode::none) {
      CHECK(vpp_has_message(result.warnings, "left unchanged"));
      CHECK_FALSE(vpp_has_message(result.warnings, "divided by"));
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.45));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.45));
      CHECK(vpp_prob(p, 2) == doctest::Approx(0.3));
      CHECK(vpp_prob(p, 3) == doctest::Approx(0.3));
    } else {
      CHECK(
          vpp_has_message(result.warnings, "divided by their total 1.500000"));
      CHECK(vpp_has_message(result.warnings,
                            "relative scene weights preserved: "
                            "0.600000/0.400000"));
      CHECK(vpp_has_message(result.warnings,
                            "changes the expected OPEX relative to CAPEX"));
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.3));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.3));
      CHECK(vpp_prob(p, 2) == doctest::Approx(0.2));
      CHECK(vpp_prob(p, 3) == doctest::Approx(0.2));
    }
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - a total far from 1 hints at representative-period "
    "weights in every probability_rescale mode")
{
  struct FarCase
  {
    std::array<double, 4> factors;
    double total;
  };
  // Day counts (total 365) and a too-small total (0.2).
  constexpr auto far_cases = std::to_array<FarCase>({
      {
          .factors =
              {
                  90.0,
                  91.0,
                  92.0,
                  92.0,
              },
          .total = 365.0,
      },
      {
          .factors =
              {
                  0.05,
                  0.05,
                  0.04,
                  0.06,
              },
          .total = 0.2,
      },
  });
  for (const auto& fc : far_cases) {
    for (const auto mode : vpp_all_modes) {
      CAPTURE(fc.total);
      CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
      const auto& f = fc.factors;
      auto p = vpp_make_two_scene_planning(f[0], f[1], f[2], f[3]);
      p.simulation.probability_rescale = mode;
      const auto result = validate_planning(p);
      CHECK(result.ok());
      CHECK(vpp_has_message(result.warnings, "representative-period"));
      CHECK(vpp_has_message(result.warnings, "block/stage durations"));
      for (std::size_t i = 0; i < f.size(); ++i) {
        const double expected =
            (mode == ProbabilityRescaleMode::none) ? f[i] : f[i] / fc.total;
        CHECK(vpp_prob(p, i) == doctest::Approx(expected));
      }
    }
  }
}

namespace
{

/// One single-scenario scene per factor.
[[nodiscard]] Planning vpp_make_single_scenario_scenes(
    std::span<const double> factors)
{
  auto p = vpp_make_minimal_planning();
  for (std::size_t i = 0; i < factors.size(); ++i) {
    p.simulation.scenario_array.push_back({
        .uid = Uid {static_cast<int>(i) + 1},
        .probability_factor = factors[i],
    });
    p.simulation.scene_array.push_back({
        .uid = Uid {static_cast<int>(i) + 1},
        .name = std::format("s{}", i),
        .active = true,
        .first_scenario = i,
        .count_scenario = 1,
    });
  }
  return p;
}

}  // namespace

TEST_CASE(  // NOLINT
    "validate_planning - single-scenario scenes keep their factor as the "
    "relative scene weight")
{
  constexpr auto factors = std::to_array({
      3.0,
      1.0,
  });
  for (const auto mode : vpp_all_modes) {
    CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
    auto p = vpp_make_single_scenario_scenes(factors);
    p.simulation.probability_rescale = mode;
    const auto result = validate_planning(p);
    CHECK(result.ok());
    CHECK(vpp_has_message(result.warnings, "sum to 4.000000 across 2 scene"));
    CHECK(vpp_has_message(result.warnings, "representative-period"));
    CHECK_FALSE(vpp_has_message(result.warnings, "CONDITIONAL"));
    if (mode == ProbabilityRescaleMode::none) {
      CHECK(vpp_prob(p, 0) == doctest::Approx(3.0));
      CHECK(vpp_prob(p, 1) == doctest::Approx(1.0));
    } else {
      CHECK(
          vpp_has_message(result.warnings, "divided by their total 4.000000"));
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.75));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.25));
    }
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - single-scenario scenes with factor 1 are "
    "conditional and get equal weights")
{
  constexpr auto factors = std::to_array({
      1.0,
      1.0,
      1.0,
      1.0,
  });
  for (const auto mode : vpp_rescale_modes) {
    CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
    auto p = vpp_make_single_scenario_scenes(factors);
    p.simulation.probability_rescale = mode;
    const auto result = validate_planning(p);
    CHECK(result.ok());
    CHECK(vpp_has_message(result.warnings, "CONDITIONAL"));
    CHECK(vpp_has_message(result.warnings, "equal weight 0.250000"));
    CHECK_FALSE(vpp_has_message(result.warnings, "representative-period"));
    for (std::size_t i = 0; i < factors.size(); ++i) {
      CHECK(vpp_prob(p, i) == doctest::Approx(0.25));
    }
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - without scenes the scenarios are divided by their "
    "total")
{
  for (const auto mode : vpp_all_modes) {
    CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
    auto p = vpp_make_minimal_planning();
    p.simulation.probability_rescale = mode;
    p.simulation.scenario_array = {
        {
            .uid = Uid {1},
            .probability_factor = 0.7,
        },
        {
            .uid = Uid {2},
            .probability_factor = 0.5,
        },
    };
    const auto result = validate_planning(p);
    CHECK(result.ok());
    CHECK(vpp_has_message(result.warnings, "sum to 1.200000 across 1 scene"));
    CHECK_FALSE(vpp_has_message(result.warnings, "CONDITIONAL"));
    if (mode == ProbabilityRescaleMode::none) {
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.7));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.5));
    } else {
      CHECK(vpp_prob(p, 0) == doctest::Approx(0.7 / 1.2));
      CHECK(vpp_prob(p, 1) == doctest::Approx(0.5 / 1.2));
    }
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - count_scenario 'all remaining' scene is range "
    "checked and normalised")
{
  auto p = vpp_make_two_scene_planning(0.5, 0.5, 0.5, 0.5);
  p.simulation.scene_array = {
      {
          .uid = Uid {1},
          .name = "all",
          .active = true,
          .first_scenario = 1,
          .count_scenario = std::dynamic_extent,
      },
  };
  const auto result = validate_planning(p);
  CHECK(result.ok());
  // Scenarios 1..3 form the scene (sum 1.5) -> rescaled to 1/3 each;
  // scenario 0 is outside every scene and stays untouched.
  CHECK(vpp_prob(p, 1) + vpp_prob(p, 2) + vpp_prob(p, 3)
        == doctest::Approx(1.0));
  CHECK(vpp_prob(p, 0) == doctest::Approx(0.5));
}

TEST_CASE(  // NOLINT
    "validate_planning - out-of-range scene is an error and excluded from "
    "the probability totals")
{
  auto p = vpp_make_two_scene_planning(0.6, 0.4, 0.5, 0.5);
  p.simulation.scene_array[1].count_scenario = 5;
  const auto result = validate_planning(p);
  CHECK_FALSE(result.ok());
  CHECK(vpp_has_message(result.errors, "Scene 'dry'"));
  // The valid scene "wet" alone sums to 1.0: nothing to rescale.
  CHECK(vpp_prob(p, 0) == doctest::Approx(0.6));
  CHECK(vpp_prob(p, 1) == doctest::Approx(0.4));
}
