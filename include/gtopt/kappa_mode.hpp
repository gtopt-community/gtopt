/**
 * @file      kappa_mode.hpp
 * @brief     Default `kappa_mode`: compiled default, `$GTOPT_KAPPA_MODE`
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Precedence: `solver_options.kappa_mode` (JSON / `--set`) >
 * `$GTOPT_KAPPA_MODE` > the compiled default (CMake
 * `GTOPT_KAPPA_MODE_DEFAULT`; `auto` = `off` in NDEBUG builds, else
 * `estimate`).  LinearInterface fills an unset `kappa_mode` with
 * `default_kappa_mode()` before every solve.
 */

#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include <gtopt/kappa_mode_default.hpp>
#include <gtopt/solver_enums.hpp>

namespace gtopt
{

/// Environment variable selecting the default kappa mode.
inline constexpr std::string_view k_kappa_mode_env = "GTOPT_KAPPA_MODE";

/// The compiled default resolved for this build (`auto` → `off` under
/// NDEBUG, `estimate` otherwise).
[[nodiscard]] constexpr KappaMode compiled_default_kappa_mode() noexcept
{
  if (k_kappa_mode_default_name == "off") {
    return KappaMode::off;
  }
  if (k_kappa_mode_default_name == "estimate") {
    return KappaMode::estimate;
  }
  if (k_kappa_mode_default_name == "exact") {
    return KappaMode::exact;
  }
  return k_kappa_mode_release_build ? KappaMode::off : KappaMode::estimate;
}
static_assert(k_kappa_mode_default_name == "auto"
                  || k_kappa_mode_default_name == "off"
                  || k_kappa_mode_default_name == "estimate"
                  || k_kappa_mode_default_name == "exact",
              "GTOPT_KAPPA_MODE_DEFAULT must be auto, off, estimate or exact");

/// Where the default kappa mode came from.
enum class KappaModeSource : int
{
  compiled_default = 0,  ///< `GTOPT_KAPPA_MODE_DEFAULT` (CMake)
  environment,  ///< `$GTOPT_KAPPA_MODE`
};

/// Mode named by `$GTOPT_KAPPA_MODE`; nullopt when unset, empty or
/// unparsable (the last logs a warning once).
[[nodiscard]] std::optional<KappaMode> kappa_mode_from_env();

/// Default for an unset `solver_options.kappa_mode`, and where it came
/// from: `$GTOPT_KAPPA_MODE`, else the compiled default.  Reads the
/// environment on every call (cheap; lets tests change it).
[[nodiscard]] std::pair<KappaMode, KappaModeSource>
effective_default_kappa_mode();

[[nodiscard]] inline KappaMode default_kappa_mode()
{
  return effective_default_kappa_mode().first;
}

/// `opts_mode` if set, else `default_kappa_mode()`.
[[nodiscard]] inline KappaMode resolve_kappa_mode(
    std::optional<KappaMode> opts_mode)
{
  return opts_mode.has_value() ? *opts_mode : default_kappa_mode();
}

/// One line for `--version`, e.g. `kappa mode: off (compiled default,
/// auto)` or `kappa mode: exact (from $GTOPT_KAPPA_MODE; compiled default:
/// auto=off)`.
[[nodiscard]] std::string describe_kappa_mode();

}  // namespace gtopt
