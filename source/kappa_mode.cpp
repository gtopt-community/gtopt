/**
 * @file      kappa_mode.cpp
 * @brief     Default `kappa_mode` resolution (env + compiled default)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <atomic>
#include <cstdlib>
#include <format>
#include <string>

#include <gtopt/enum_option.hpp>
#include <gtopt/kappa_mode.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

std::optional<KappaMode> kappa_mode_from_env()
{
  // NOLINTNEXTLINE(concurrency-mt-unsafe) -- read-only, no setenv here
  const char* raw = std::getenv(std::string {k_kappa_mode_env}.c_str());
  if (raw == nullptr || *raw == '\0') {
    return std::nullopt;
  }
  if (const auto mode = enum_from_name<KappaMode>(raw)) {
    return mode;
  }
  static std::atomic<bool> warned {false};
  if (!warned.exchange(true)) {
    spdlog::warn("{}='{}' is not off, estimate or exact; ignored",
                 k_kappa_mode_env,
                 raw);
  }
  return std::nullopt;
}

std::pair<KappaMode, KappaModeSource> effective_default_kappa_mode()
{
  if (const auto env = kappa_mode_from_env()) {
    return {*env, KappaModeSource::environment};
  }
  return {compiled_default_kappa_mode(), KappaModeSource::compiled_default};
}

std::string describe_kappa_mode()
{
  const auto [mode, source] = effective_default_kappa_mode();
  const auto compiled = compiled_default_kappa_mode();
  const auto compiled_txt = k_kappa_mode_default_name == "auto"
      ? std::format("auto={}", enum_name(compiled))
      : std::string {enum_name(compiled)};
  if (source == KappaModeSource::environment) {
    return std::format("kappa mode: {} (from ${}; compiled default: {})",
                       enum_name(mode),
                       k_kappa_mode_env,
                       compiled_txt);
  }
  return std::format("kappa mode: {} (compiled default{})",
                     enum_name(mode),
                     k_kappa_mode_default_name == "auto" ? ", auto" : "");
}

}  // namespace gtopt
