/**
 * @file      warn_once.hpp
 * @brief     Thread-safe "log this warning only once" helper.
 * @date      2026-11-22
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * LP assembly runs on WorkPool threads, so a function-local
 * `static bool warned` flag is a data race.  Use a function-local
 * `static WarnOnceFlag` instead (constant-initialised, so its
 * construction is race-free too):
 *
 *     static WarnOnceFlag warned;
 *     warn_once(warned, "mode '{}' is not supported", name);
 */

#pragma once

#include <atomic>
#include <utility>

#include <spdlog/spdlog.h>

namespace gtopt
{

/// One-shot latch: `fire()` returns true for exactly one caller, across
/// all threads, over the lifetime of the flag.
class WarnOnceFlag
{
public:
  constexpr WarnOnceFlag() noexcept = default;

  WarnOnceFlag(const WarnOnceFlag&) = delete;
  WarnOnceFlag& operator=(const WarnOnceFlag&) = delete;
  WarnOnceFlag(WarnOnceFlag&&) = delete;
  WarnOnceFlag& operator=(WarnOnceFlag&&) = delete;
  ~WarnOnceFlag() = default;

  /// Latch the flag; true only for the call that latched it.
  [[nodiscard]] bool fire() noexcept
  {
    return !m_fired_.exchange(true, std::memory_order_relaxed);
  }

  /// True once any caller has latched the flag.
  [[nodiscard]] bool fired() const noexcept
  {
    return m_fired_.load(std::memory_order_relaxed);
  }

private:
  std::atomic<bool> m_fired_ {false};
};

/// Emit `spdlog::warn(fmt, args...)` only on the first call that latches
/// @p flag; every later call (from any thread) is a no-op.
template<typename... Args>
void warn_once(WarnOnceFlag& flag,
               spdlog::format_string_t<Args...> fmt,
               Args&&... args)
{
  if (flag.fire()) {
    spdlog::warn(fmt, std::forward<Args>(args)...);
  }
}

}  // namespace gtopt
