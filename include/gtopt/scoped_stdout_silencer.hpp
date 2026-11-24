// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      scoped_stdout_silencer.hpp
 * @brief     Thread-safe RAII redirect of fd 1 (stdout) to /dev/null
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Some solver libraries (MindOpt, Gurobi) print a banner to stdout while
 * their environment is created, and the banner cannot be suppressed via
 * the API.  The plugins therefore point fd 1 at /dev/null around the
 * environment creation.
 *
 * Doing that with a bare `dup` / `dup2` pair is not thread-safe: when two
 * backends are constructed concurrently (SDDP clones, parallel tests) the
 * second thread can save the *already redirected* descriptor and later
 * "restore" /dev/null, leaving the whole process with stdout silenced for
 * good (the doctest summary then silently disappears).
 *
 * `ScopedStdoutSilencer` serialises the redirect behind a process-wide
 * mutex and reference count: the first live instance saves the real
 * stdout and redirects it, the last one to die restores it.  The restore
 * happens in the destructor, so every exit path (including exceptions)
 * puts stdout back.
 */

#pragma once

#include <cstdio>
#include <mutex>

#include <fcntl.h>
#include <unistd.h>

namespace gtopt
{

class ScopedStdoutSilencer
{
public:
  ScopedStdoutSilencer()
  {
    const std::scoped_lock lock(state().mutex);
    auto& st = state();
    if (st.depth++ > 0) {
      return;  // already silenced by a live instance
    }
    (void)std::fflush(stdout);
    st.saved_fd = ::dup(STDOUT_FILENO);  // NOLINT(android-cloexec-dup)
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg,hicpp-vararg)
    const int devnull = ::open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (st.saved_fd >= 0 && devnull >= 0) {
      (void)::dup2(devnull, STDOUT_FILENO);
    }
    if (devnull >= 0) {
      (void)::close(devnull);
    }
  }

  ~ScopedStdoutSilencer()
  {
    const std::scoped_lock lock(state().mutex);
    auto& st = state();
    if (--st.depth > 0) {
      return;  // another live instance still needs the redirect
    }
    (void)std::fflush(stdout);
    if (st.saved_fd >= 0) {
      (void)::dup2(st.saved_fd, STDOUT_FILENO);
      (void)::close(st.saved_fd);
      st.saved_fd = -1;
    }
  }

  ScopedStdoutSilencer(const ScopedStdoutSilencer&) = delete;
  ScopedStdoutSilencer& operator=(const ScopedStdoutSilencer&) = delete;
  ScopedStdoutSilencer(ScopedStdoutSilencer&&) = delete;
  ScopedStdoutSilencer& operator=(ScopedStdoutSilencer&&) = delete;

private:
  struct State
  {
    std::mutex mutex;
    int depth {0};
    int saved_fd {-1};
  };

  [[nodiscard]] static State& state() noexcept
  {
    static State st;
    return st;
  }
};

}  // namespace gtopt
