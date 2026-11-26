// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      tmp_dir_helpers.hpp
 * @brief     Collision-free scratch directories for the unit tests
 * @copyright Copyright (c) 2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Two layers keep tests that write temporary files from clobbering each
 * other:
 *
 *  1. `main.cpp` (option `GTOPT_TEST_PRIVATE_TMPDIR`, default ON) gives the
 *     whole test process a private `$TMPDIR` before any test runs, so even a
 *     fixed name like `temp_directory_path() / "foo"` is private to this
 *     process.  `test_private_tmpdir()` returns it (empty when disabled).
 *  2. `unique_test_dir(tag)` / `unique_test_path(tag)` add the pid and a
 *     process-wide counter to the name, so a scratch dir stays unique even
 *     with layer 1 disabled (`GTOPT_TEST_PRIVATE_TMPDIR=0` at run time) or
 *     when the same tag is used twice in one process.
 */

#pragma once

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <format>
#include <string_view>

#include <unistd.h>

namespace gtopt::test
{

/// The per-process private TMPDIR installed by `main.cpp`, or an empty path
/// when the private TMPDIR is disabled (compile- or run-time).
[[nodiscard]] std::filesystem::path test_private_tmpdir();

/// A fresh, unique, NOT yet existing path `<tmp>/gtopt_<tag>_<pid>_<n>`.
[[nodiscard]] inline std::filesystem::path unique_test_path(
    std::string_view tag)
{
  static std::atomic<std::uint64_t> counter {0};
  const auto n = counter.fetch_add(1, std::memory_order_relaxed);
  auto path = std::filesystem::temp_directory_path()
      / std::format("gtopt_{}_{}_{}", tag, ::getpid(), n);
  // Only ever removes a path whose name carries this process's pid and
  // a counter value no other call in this process returned.
  std::error_code ec;
  std::filesystem::remove_all(path, ec);
  return path;
}

/// Like `unique_test_path`, but the directory is created (empty).
[[nodiscard]] inline std::filesystem::path unique_test_dir(std::string_view tag)
{
  auto dir = unique_test_path(tag);
  std::filesystem::create_directories(dir);
  return dir;
}

}  // namespace gtopt::test
