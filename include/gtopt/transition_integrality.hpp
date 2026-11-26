/**
 * @file      transition_integrality.hpp
 * @brief     Integrality of the commitment startup / shutdown / tier columns
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A Commitment has status u, startup v and shutdown w per period.  u is
 * always integer on a MIP build.  The logic row
 * `u[p] − u[p−1] = v[p] − w[p]` and the exclusion `v + w ≤ 1` alone admit
 * v = w = ½ for a unit that stays on (or off), which the start / stop ramp
 * terms (SU·v, SD·w) and user rows on startup / shutdown reward, so v / w
 * must be integer for the formulation to be exact (UnitCommitment.jl's
 * Gar1962 model: binary `switch_on` / `switch_off`).
 *
 * `transition_integrality`:
 *  - `auto` (default): binary (exact).
 *  - `continuous`: always continuous in [0, 1] (smaller MIP, not exact
 *    with start / stop ramps).
 *  - `binary`: always binary.
 *
 * History: on 2026-11-25 the rows `v ≤ u`, `w ≤ 1 − u`
 * (`transition_status_rows`) were added and `auto` kept v / w continuous
 * while they were on.  They were removed on 2026-11-26 (redundant in a
 * MIP with binary v / w; on the CEN PCP case they made the relaxed LP
 * degenerate with a near-singular basis, and CPLEX declared feasible LPs
 * infeasible), so `auto` is binary again, as it was with the rows off.
 * The key `transition_status_rows` is still parsed and ignored with a
 * warning.
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
#include <optional>
#include <span>
#include <variant>

#include <gtopt/basic_types.hpp>
#include <gtopt/enum_option.hpp>

namespace gtopt
{

/// Integrality of the commitment startup / shutdown columns.
enum class TransitionIntegrality : std::uint8_t
{
  automatic = 0,  ///< Binary (default; exact)
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

/// Whether v / w are declared integer (before any relax) for the mode:
/// `auto` and `binary` → binary, `continuous` → continuous.
[[nodiscard]] constexpr bool transition_cols_binary(
    TransitionIntegrality mode) noexcept
{
  return mode != TransitionIntegrality::continuous;
}

/// JSON value of the removed `transition_status_rows` key (a bool or a
/// name): still parsed so old inputs load, then ignored with a warning.
using BoolOrName = std::variant<bool, Name>;
using OptBoolOrName = std::optional<BoolOrName>;

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
