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
#include <mutex>
#include <set>
#include <string>
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

/// Process-wide one-shot latch keyed by string: true only for the first
/// call (from any thread) that presents @p key.  A single registry shared
/// by every caller (non-template inline function → one static instance).
[[nodiscard]] inline bool first_time_key(std::string key)
{
  static std::mutex mutex;
  static std::set<std::string, std::less<>> seen;
  const std::scoped_lock lock {mutex};
  return seen.insert(std::move(key)).second;
}

/// Keyed variant: emit `spdlog::warn(fmt, args...)` only the first time
/// @p key is seen in this process.  Used for "once per element"
/// warnings, where a function-local `WarnOnceFlag` would be once per
/// call site (silencing every element after the first) and a member
/// flag would repeat in every (scene, phase) cell.  Build the key from
/// the class name, the element uid and the reason, e.g.
/// `std::format("{}:{}:missing_waterway", cname, uid)`.
template<typename... Args>
void warn_once_per_key(std::string key,
                       spdlog::format_string_t<Args...> fmt,
                       Args&&... args)
{
  if (first_time_key(std::move(key))) {
    spdlog::warn(fmt, std::forward<Args>(args)...);
  }
}

}  // namespace gtopt
