/**
 * @file      capital_recovery.hpp
 * @brief     Capital recovery factor and annuity of an overnight cost
 * @date      2026-11-27
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A capacity object (Generator, Line, Battery, Converter) may give its
 * investment cost as an overnight cost plus an economic life and a
 * private discount rate (WACC / hurdle rate) instead of an annualized
 * `annual_capcost`.  The LP then charges the annuity
 *
 *     annual_capcost = overnight_capcost × CRF(wacc, economic_life)
 *     CRF(r, n) = r (1 + r)^n / ((1 + r)^n − 1)
 *
 * while the stage discount factors keep the planning-wide (social)
 * `annual_discount_rate`.  See docs/methods/investment-economics.md.
 */

#pragma once

#include <cmath>
#include <optional>

namespace gtopt
{

/// Capital recovery factor CRF(r, n) [1/year].
///
/// - `n` unset or `n <= 0`: perpetuity, CRF = r.
/// - `r == 0`: straight-line, CRF = 1 / n.
/// - `r <= -1` is not a rate: returns 0 (no annuity).
[[nodiscard]] inline double capital_recovery_factor(
    double rate, std::optional<double> life) noexcept
{
  if (rate <= -1.0) {
    return 0.0;
  }
  if (!life.has_value() || *life <= 0.0) {
    return rate > 0.0 ? rate : 0.0;
  }
  const double n = *life;
  if (std::abs(rate) < 1e-12) {
    return 1.0 / n;
  }
  const double g = std::pow(1.0 + rate, n);
  return rate * g / (g - 1.0);
}

/// Annuity [$/unit-year] of an overnight cost [$/unit].
[[nodiscard]] inline double annuity_from_overnight(
    double overnight_cost, double rate, std::optional<double> life) noexcept
{
  return overnight_cost * capital_recovery_factor(rate, life);
}

}  // namespace gtopt
