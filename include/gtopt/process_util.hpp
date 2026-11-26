/**
 * @file      process_util.hpp
 * @brief     Safe helpers for locating and spawning external tools
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * gtopt runs a few optional external helpers (`gtopt_check_lp`,
 * `gtopt_check_json`, `gtopt_compress_lp`, `zstd`/`gzip`, ...).  The
 * helpers here centralise the low-level POSIX calls that need care:
 *
 *  - find_trusted_executable(): resolve a tool name without a shell.  The
 *    directory of the running executable is searched first (a tool that
 *    ships with gtopt wins over anything on PATH), then only absolute PATH
 *    entries that are not world-writable.  Empty, relative ("." or "")
 *    and world-writable PATH entries are skipped, so a binary planted in
 *    the current working directory or a shared scratch directory is never
 *    run by accident.
 *  - spawn_environment(): the environment block handed to posix_spawn.
 *  - create_exclusive_file(): create a new, empty, owner-only file that
 *    must not exist yet (never follows a pre-existing symlink).
 *  - gtopt_environment() / log_gtopt_environment(): the `GTOPT_*`
 *    variables that change behaviour, logged once at start-up.
 */

#pragma once

#include <filesystem>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace gtopt
{

/// Directory holding the running executable (`/proc/self/exe`), or an
/// empty path when it cannot be determined.
[[nodiscard]] std::filesystem::path executable_directory();

/// True when @p dir exists and is writable by "others" (mode o+w).
[[nodiscard]] bool is_world_writable_directory(
    const std::filesystem::path& dir) noexcept;

/// Locate the executable @p name (a bare file name, no '/').
///
/// Search order: the directory of the running executable, then the absolute,
/// non-world-writable entries of `$PATH`.  Only regular files the user may
/// execute are returned. Returns an empty string when the tool is not found or
/// @p name contains a path separator.
[[nodiscard]] std::string find_trusted_executable(std::string_view name);

/// Environment block for `posix_spawn` (the current process environment).
[[nodiscard]] char* const* spawn_environment() noexcept;

/// Create @p path as a new, empty regular file with mode 0600.  Fails
/// (returns false) when the path already exists — including a dangling
/// symlink — so the following write can never land on a pre-existing
/// file.
[[nodiscard]] bool create_exclusive_file(
    const std::filesystem::path& path) noexcept;

/// `GTOPT_*` variables of the NULL-terminated environment block @p envp,
/// sorted by name.  Values of secret-like names (containing `TOKEN`, `KEY`,
/// `SECRET` or `PASSWORD`) are replaced by `***`.
[[nodiscard]] std::vector<std::pair<std::string, std::string>>
gtopt_environment(char* const* envp);

/// Log every `GTOPT_*` variable of the process environment (info level);
/// `GTOPT_TEST_*` test hooks are logged as warnings.
void log_gtopt_environment();

}  // namespace gtopt
