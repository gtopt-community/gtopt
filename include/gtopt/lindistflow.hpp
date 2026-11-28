/**
 * @file      lindistflow.hpp
 * @brief     Shared helpers of the LinDistFlow network model
 * @date      2026-11-27
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `model_options.network_model = "lindistflow"` (see `NetworkModel`)
 * adds, per block: a squared-voltage column `w = |V|²` and a reactive
 * balance row per bus (BusLP), reactive injections of generators and
 * demands, and per line a reactive flow column, the voltage-drop row
 * `w_a/t² − w_b − 2(r·P + x·Q)/V² = 0` and the MVA polygon (LineLP).
 */

#pragma once

#include <cmath>

namespace gtopt
{

/// Reactive-to-active ratio `Q/P = sign(pf)·tan(acos|pf|)` of a signed
/// power factor (negative = leading / absorbing).  `|pf| ≥ 1` or `0`
/// (undefined) → 0 (unity).
[[nodiscard]] inline double reactive_ratio(double power_factor) noexcept
{
  const double a = std::abs(power_factor);
  if (a >= 1.0 || a <= 0.0) {
    return 0.0;
  }
  const double t = std::sqrt((1.0 / (a * a)) - 1.0);
  return power_factor < 0.0 ? -t : t;
}

}  // namespace gtopt
