/**
 * @file      sanity_options.hpp
 * @brief     Thresholds for the data-quality (sanity) warnings of
 *            validate_planning
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `validate_planning()` runs a set of model-sanity checks that never
 * reject a case: they only warn about data that is legal but almost
 * certainly wrong (rewards above the deficit cost, profiles outside
 * [0, 1], committed units with zero capacity, implausible line
 * impedances, agreement reserve levels without a reservoir floor).
 * This struct holds the tunable thresholds.  Every field is optional;
 * the `effective_*()` accessors supply the defaults.
 *
 * JSON: `{"options": {"sanity_options": {"line_xr_max": 200}}}`.
 */

#pragma once

#include <gtopt/basic_types.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

struct SanityOptions
{
  /// Master switch.  Unset = enabled.
  OptBool enable {};

  /// A water-right reward (`FlowRight.uvalue > 0`, `fcost < 0`, the
  /// global `hydro_use_value`) above `ratio × demand fail cost` is
  /// flagged as a phantom reward: the LP would rather shed load than
  /// forgo the reward.  Default 1.0.
  OptReal reward_fail_cost_ratio {};

  /// Slack on the per-unit profile range check: values outside
  /// `[-tol, 1 + tol]` are flagged.  Default 1e-6.
  OptReal profile_tolerance {};

  /// A line whose `|X|/V²` differs from the median of its kV class by
  /// more than this factor (either direction) is flagged as an
  /// impedance outlier.  Default 50.
  OptReal line_x_outlier_factor {};

  /// Minimum number of lines in a kV class before the outlier test runs
  /// (a median of two lines says nothing).  Default 5.
  OptInt line_x_min_class_size {};

  /// Reactance in per-unit on a 100 MVA base (`100·|X|/V²`, lines with
  /// a known kV only) above this value is flagged as implausible.
  /// Default 5.0 p.u.
  OptReal line_x_pu_max {};

  /// `|X| / R` above this value is flagged (lines with `R > 0`).
  /// Default 100.
  OptReal line_xr_max {};

  /// Number of top offenders named in each summary line.  Default 5.
  OptInt max_offenders {};

  constexpr void merge(const SanityOptions& other) noexcept
  {
    merge_opt(enable, other.enable);
    merge_opt(reward_fail_cost_ratio, other.reward_fail_cost_ratio);
    merge_opt(profile_tolerance, other.profile_tolerance);
    merge_opt(line_x_outlier_factor, other.line_x_outlier_factor);
    merge_opt(line_x_min_class_size, other.line_x_min_class_size);
    merge_opt(line_x_pu_max, other.line_x_pu_max);
    merge_opt(line_xr_max, other.line_xr_max);
    merge_opt(max_offenders, other.max_offenders);
  }

  static constexpr Real default_reward_fail_cost_ratio = 1.0;
  static constexpr Real default_profile_tolerance = 1e-6;
  static constexpr Real default_line_x_outlier_factor = 50.0;
  static constexpr Int default_line_x_min_class_size = 5;
  static constexpr Real default_line_x_pu_max = 5.0;
  static constexpr Real default_line_xr_max = 100.0;
  static constexpr Int default_max_offenders = 5;

  [[nodiscard]] constexpr bool effective_enable() const noexcept
  {
    return enable.value_or(true);
  }
  [[nodiscard]] constexpr Real effective_reward_fail_cost_ratio() const noexcept
  {
    return reward_fail_cost_ratio.value_or(default_reward_fail_cost_ratio);
  }
  [[nodiscard]] constexpr Real effective_profile_tolerance() const noexcept
  {
    return profile_tolerance.value_or(default_profile_tolerance);
  }
  [[nodiscard]] constexpr Real effective_line_x_outlier_factor() const noexcept
  {
    return line_x_outlier_factor.value_or(default_line_x_outlier_factor);
  }
  [[nodiscard]] constexpr Int effective_line_x_min_class_size() const noexcept
  {
    return line_x_min_class_size.value_or(default_line_x_min_class_size);
  }
  [[nodiscard]] constexpr Real effective_line_x_pu_max() const noexcept
  {
    return line_x_pu_max.value_or(default_line_x_pu_max);
  }
  [[nodiscard]] constexpr Real effective_line_xr_max() const noexcept
  {
    return line_xr_max.value_or(default_line_xr_max);
  }
  [[nodiscard]] constexpr Int effective_max_offenders() const noexcept
  {
    return max_offenders.value_or(default_max_offenders);
  }
};

}  // namespace gtopt
