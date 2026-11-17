/**
 * @file      gtopt_json_io.hpp
 * @brief     JSON parsing, serialization, and --set overlay for Planning
 * @date      2026-04-08
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Separated from gtopt_main.cpp so that the heavy DAW JSON headers
 * compile in their own translation unit.
 */

#pragma once

#include <expected>
#include <optional>
#include <string>
#include <vector>

#include <gtopt/planning.hpp>

namespace gtopt
{

/// Parse a single JSON string into a Planning object.
///
/// Single instantiation point for from_json<Planning> — call sites avoid
/// pulling in the heavy daw::json template machinery.
[[nodiscard]] Planning parse_planning_json(std::string_view json_content);

/// Parse planning JSON files into a Planning object.
///
/// Iterates over each path in @p planning_files, reads the JSON, and merges
/// it into a single Planning.  Returns an error string on the first failure.
///
/// @p enforce_dialect optionally drives the input-warn pass on every
/// alias canonicalization (see `canonicalize_json_keys`): when non-empty
/// and an alias's source dialect differs from this value, a
/// once-per-alias `spdlog::warn` is emitted.  Empty preserves the
/// pre-feature silent behaviour.
[[nodiscard]] std::expected<Planning, std::string> parse_planning_files(
    const std::vector<std::string>& planning_files,
    const std::optional<std::string>& input_directory = {},
    std::string_view enforce_dialect = {});

/// Directory options read from the `options` block of planning files.
struct PlanningDirectoryPeek
{
  std::optional<std::string> output_directory {};
  std::optional<std::string> log_directory {};
};

/// Cheaply read `options.output_directory` / `options.log_directory`
/// from @p planning_files without deserialising the whole Planning.
///
/// Used to place the log files *before* the full parse runs (the file
/// sink must exist before the first log line).  Files are located the
/// same way as in `parse_planning_files`; later files override earlier
/// ones (same as `Planning::merge`).  Missing / malformed files are
/// skipped silently — the full parse reports them.
[[nodiscard]] PlanningDirectoryPeek peek_planning_directories(
    const std::vector<std::string>& planning_files,
    const std::optional<std::string>& input_directory = {});

/// Write the merged Planning to a JSON file.
[[nodiscard]] std::expected<void, std::string> write_json_output(
    const Planning& planning, const std::string& json_file);

/// Apply all `--set key=value` overrides to a Planning object.
/// Returns true if all options were applied successfully.
[[nodiscard]] bool apply_set_options(
    Planning& planning, const std::vector<std::string>& set_options);

/// Load user constraints from external file(s) referenced in planning.
[[nodiscard]] std::expected<void, std::string> load_user_constraints(
    Planning& planning);

}  // namespace gtopt
