/**
 * @file      storage_state_defaults.hpp
 * @brief     Per-class defaults of `use_state_variable` / `daily_cycle`
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Single source of truth for "does this storage element carry a state
 * variable across stages / phases".  The `*_lp.cpp` builders resolve
 * their `StorageOptions` from these defaults, and `validate_planning`
 * uses `storage_carries_state` with the same defaults, so validation and
 * the LP agree on which elements are state-carrying.
 */

#pragma once

namespace gtopt
{

struct StorageStateDefaults
{
  bool use_state_variable {true};
  bool daily_cycle {false};
};

namespace storage_state_defaults
{
inline constexpr StorageStateDefaults reservoir {
    .use_state_variable = true,
    .daily_cycle = false,
};
inline constexpr StorageStateDefaults battery {
    .use_state_variable = false,
    .daily_cycle = true,
};
inline constexpr StorageStateDefaults lng_terminal {
    .use_state_variable = true,
    .daily_cycle = false,
};
// H2 / thermal stores are seasonal / multi-day carriers: they carry
// their level across stages by default (#546).  `false` would add the
// `efin == eini` close row per stage, contradicting any `efin` target
// above `eini`.
inline constexpr StorageStateDefaults thermal_storage {
    .use_state_variable = true,
    .daily_cycle = false,
};
inline constexpr StorageStateDefaults hydrogen_storage {
    .use_state_variable = true,
    .daily_cycle = false,
};
inline constexpr StorageStateDefaults ammonia_storage {
    .use_state_variable = true,
    .daily_cycle = false,
};
inline constexpr StorageStateDefaults volume_right {
    .use_state_variable = true,
    .daily_cycle = false,
};
inline constexpr StorageStateDefaults allowance_pool {
    .use_state_variable = true,
    .daily_cycle = false,
};
}  // namespace storage_state_defaults

/// Effective `use_state_variable` of a storage element — the same rule
/// as `StorageLP::add_to_lp` (`daily_cycle ? false : use_state_variable`).
/// Elements without a `daily_cycle` field use the class default.
template<typename StorageElement>
[[nodiscard]] constexpr bool storage_carries_state(
    const StorageElement& element, StorageStateDefaults defaults) noexcept
{
  bool daily_cycle = defaults.daily_cycle;
  if constexpr (requires { element.daily_cycle.value_or(false); }) {
    daily_cycle = element.daily_cycle.value_or(defaults.daily_cycle);
  }
  if (daily_cycle) {
    return false;
  }
  return element.use_state_variable.value_or(defaults.use_state_variable);
}

}  // namespace gtopt
