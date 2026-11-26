/**
 * @file      transition_integrality.hpp
 * @brief     Integrality of the commitment startup / shutdown / tier columns
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A Commitment has status u, startup v and shutdown w per period.  u is
 * always integer on a MIP build.  With the logic row
 * `u[p] − u[p−1] = v[p] − w[p]` and the status rows `v ≤ u`,
 * `w ≤ 1 − u` (`transition_status_rows`), an integral u (and integral
 * `initial_status`, rounded at 0.5) forces v, w ∈ {0, 1} in all four
 * (u[p−1], u[p]) cases; ramp rows, min up/down, tiers or user rows only
 * remove points.  So v / w need not be declared integer
 * (Knueven, Ostrowski & Watson 2020; Rajan & Takriti 2005).  Without the
 * status rows they must be: C1 + `v + w ≤ 1` admit v = w = ½ for a
 * unit that stays on.
 *
 * `transition_integrality`:
 *  - `auto` (default): continuous when the status rows are on, binary
 *    when `transition_status_rows = false`.
 *  - `continuous`: always continuous in [0, 1].
 *  - `binary`: always binary (the original three-binary model).
 *
 * `startup_tier_integrality` (hot / warm / cold start selectors y):
 *  - `continuous` (default): with v / w integral, the tier rows
 *    (Σ y = v, cumulative offline-window rows) form an interval matrix,
 *    hence totally unimodular, so the optimal vertices are integral.
 *  - `integer`: declared binary.
 *
 * `relax`, `continuous_phases` and `--no-mip` relax every one of them
 * whatever the mode.  Set globally in `model_options` or per unit in
 * `Commitment`; the per-unit value wins.
 */

#pragma once

#include <array>
#include <cstdint>
#include <span>

#include <gtopt/enum_option.hpp>

namespace gtopt
{

/// Integrality of the commitment startup / shutdown columns.
enum class TransitionIntegrality : std::uint8_t
{
  automatic = 0,  ///< Continuous with status rows, binary without (default)
  continuous = 1,  ///< Always continuous in [0, 1]
  binary = 2,  ///< Always binary
};

inline constexpr auto transition_integrality_entries =
    std::to_array<EnumEntry<TransitionIntegrality>>({
        {.name = "auto", .value = TransitionIntegrality::automatic},
        {.name = "continuous", .value = TransitionIntegrality::continuous},
        {.name = "binary", .value = TransitionIntegrality::binary},
    });

[[nodiscard]] constexpr auto enum_entries(
    TransitionIntegrality /*tag*/) noexcept
{
  return std::span {transition_integrality_entries};
}

/// Whether v / w are declared integer (before any relax), given the mode
/// and whether the `v ≤ u` / `w ≤ 1 − u` status rows are emitted.
[[nodiscard]] constexpr bool transition_cols_binary(TransitionIntegrality mode,
                                                    bool status_rows) noexcept
{
  switch (mode) {
    case TransitionIntegrality::continuous:
      return false;
    case TransitionIntegrality::binary:
      return true;
    case TransitionIntegrality::automatic:
      break;
  }
  return !status_rows;
}

/// Integrality of the hot / warm / cold startup-tier selectors.
enum class StartupTierIntegrality : std::uint8_t
{
  continuous = 0,  ///< Continuous in [0, 1] (default; TU tier rows)
  integer = 1,  ///< Binary
};

inline constexpr auto startup_tier_integrality_entries =
    std::to_array<EnumEntry<StartupTierIntegrality>>({
        {.name = "continuous", .value = StartupTierIntegrality::continuous},
        {.name = "integer", .value = StartupTierIntegrality::integer},
    });

[[nodiscard]] constexpr auto enum_entries(
    StartupTierIntegrality /*tag*/) noexcept
{
  return std::span {startup_tier_integrality_entries};
}

}  // namespace gtopt
