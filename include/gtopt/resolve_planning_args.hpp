/**
 * @file      resolve_planning_args.hpp
 * @brief     Resolve planning file arguments and directory context
 * @date      2026-03-21
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Provides a single free function that normalises the planning-file list
 * and directory options before any JSON parsing takes place.  Three
 * convenience features are implemented:
 *
 * 1. **Directory as argument**: `dir` → `dir/basename(dir).json` with
 *    input/output directories set relative to `dir`.
 * 2. **Bare stem fallback**: `name` (not a dir, not a file) → `name.json`.
 * 3. **Auto-detect CWD**: no files given → look for `cwd_name.json`.
 */

#pragma once

#include <expected>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/gtopt_main.hpp>

namespace gtopt
{

/**
 * @brief Resolve planning file arguments and directory context.
 *
 * See the file-level documentation for the three features implemented.
 *
 * @param opts  A copy of the original MainOptions (modified in place).
 * @return The resolved MainOptions, or an error string.
 */
[[nodiscard]] std::expected<MainOptions, std::string> resolve_planning_args(
    MainOptions opts);

/**
 * @brief Value of the last `--set <key>=<value>` entry for @p key.
 *
 * Only exact top-level keys match (e.g. `"log_directory"`).  A literal
 * `null` value is treated as "not set", mirroring `apply_set_options`,
 * where a JSON `null` overlay leaves the option untouched.
 */
[[nodiscard]] std::optional<std::string> find_set_option(
    const std::vector<std::string>& set_options, std::string_view key);

}  // namespace gtopt
