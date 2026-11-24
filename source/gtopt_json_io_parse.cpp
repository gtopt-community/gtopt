/**
 * @file      gtopt_json_io_parse.cpp
 * @brief     JSON parsing of Planning files (split from gtopt_json_io.cpp)
 * @date      2026-04-09
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Contains parse_planning_files() — instantiates from_json<Planning> with
 * StrictParsePolicy, which rejects unknown JSON fields (exact mapping).
 */

#include <algorithm>
#include <cstddef>
#include <expected>
#include <filesystem>
#include <format>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <daw/daw_read_file.h>
#include <daw/json/daw_json_link.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/json_canonicalize.hpp>
#include <spdlog/spdlog.h>
#include <spdlog/stopwatch.h>

namespace gtopt
{

namespace
{

/// Map a planning-file argument to the JSON path that is actually read:
/// force the `.json` extension and, when the file is not found relative
/// to the CWD, fall back to `<input_directory>/<filename>`.  The returned
/// path may not exist — callers check before calling `daw::read_file`,
/// which is noexcept and would `std::terminate` on a missing file.
[[nodiscard]] std::filesystem::path locate_planning_file(
    const std::string& planning_file,
    const std::optional<std::string>& input_directory,
    bool verbose)
{
  std::filesystem::path fpath(planning_file);
  fpath.replace_extension(".json");

  if (!std::filesystem::exists(fpath) && input_directory.has_value()) {
    auto alt =
        std::filesystem::path(input_directory.value()) / fpath.filename();
    if (std::filesystem::exists(alt)) {
      if (verbose) {
        spdlog::info("  Found '{}' in input_directory '{}'",
                     fpath.filename().string(),
                     input_directory.value());
      }
      fpath = std::move(alt);
    }
  }
  return fpath;
}

/// Render a `daw::json::json_exception` with the line / column and the
/// up-to-50 characters that precede the error position in `document`.
///
/// Replaces `daw::json::to_formatted_string`, which (daw_json_link
/// v3.31) starts its context window at
/// `parse_location - min(50, distance(doc, parse_location + 1))`, i.e.
/// ONE BYTE BEFORE the document whenever the error lies in its first 50
/// characters (e.g. `{ this is not valid JSON }`).  That out-of-bounds
/// read was reported by ThreadSanitizer as a heap-use-after-free and by
/// valgrind as "1 bytes before a block".  Here every access stays inside
/// `document`, and a parse location outside it (a pointer into some
/// other buffer) is never dereferenced.
[[nodiscard]] std::string format_json_error(
    const daw::json::json_exception& jex, std::string_view document)
{
  std::string result = "reason: " + jex.reason();
  const char* const loc = jex.parse_location();
  const char* const first = document.data();
  const char* const last = first + document.size();
  const std::less<const char*> before {};
  if (loc == nullptr || first == nullptr || before(loc, first)
      || before(last, loc))
  {
    return result;
  }
  const auto offset = static_cast<std::size_t>(loc - first);
  const auto head = document.substr(0, offset);
  const auto line_no = 1 + std::ranges::count(head, '\n');
  const auto last_nl = head.rfind('\n');
  const auto col_no =
      offset - (last_nl == std::string_view::npos ? 0 : last_nl + 1) + 1;
  constexpr std::size_t max_context = 50;
  const auto ctx_begin = offset > max_context ? offset - max_context : 0;
  const auto ctx_end = std::min(offset + 1, document.size());
  std::string context;
  context.reserve(ctx_end - ctx_begin);
  for (const char c : document.substr(ctx_begin, ctx_end - ctx_begin)) {
    if (c != '\n' && c != '\r') {
      context.push_back(c);
    }
  }
  result += std::format(
      " \nlocation: near line: {} col: {}\n\"{}\"", line_no, col_no, context);
  return result;
}

}  // namespace

Planning parse_planning_json(std::string_view json_content)
{
  // Rewrite alternative key names (PyPSA / pandapower / PLEXOS /
  // PSR SDDP / PLP / gtopt-modern) to their canonical gtopt form
  // before the strict daw::json parser sees the document.  See
  // include/gtopt/json_canonicalize.hpp and
  // docs/analysis/naming-conventions.md §9 / §10.
  const auto canonical = canonicalize_json_keys(json_content);
  return daw::json::from_json<Planning>(canonical, StrictParsePolicy);
}

std::expected<Planning, std::string> parse_planning_files(
    const std::vector<std::string>& planning_files,
    const std::optional<std::string>& input_directory,
    std::string_view enforce_dialect)
{
  const spdlog::stopwatch sw;
  Planning my_planning;

  for (const auto& planning_file : planning_files) {
    try {
      const auto fpath = locate_planning_file(
          planning_file, input_directory, /*verbose=*/true);
      if (!std::filesystem::exists(fpath)) {
        return std::unexpected(
            std::format("Input file '{}' does not exist", fpath.string()));
      }

      const auto json_result = daw::read_file(fpath.string());

      if (!json_result) {
        return std::unexpected(
            std::format("Failed to read input file '{}'", planning_file));
      }

      spdlog::info("  Parsing input file {}", fpath.string());

      // `canonical` must outlive the catch block: `daw::json::json_exception`
      // carries raw pointers into the buffer it was parsed from, and
      // `format_json_error` walks back from the error position looking
      // for the surrounding line.  Passing a different buffer (the
      // pre-canonicalisation source) made daw's `to_formatted_string`
      // segfault when the canonicalisation shifted byte offsets — see the
      // `BAT_ALICANTO` reproducer on the `plp_2y` case (gtopt-cases).
      const auto canonical =
          canonicalize_json_keys(json_result.value(), enforce_dialect);
      try {
        auto plan =
            daw::json::from_json<Planning>(canonical, StrictParsePolicy);
        my_planning.merge(std::move(plan));
      } catch (const daw::json::json_exception& jex) {
        return std::unexpected(
            std::format("JSON parsing error in file '{}': {}",
                        fpath.string(),
                        format_json_error(jex, canonical)));
      }

    } catch (const std::exception& ex) {
      return std::unexpected(
          std::format("Unexpected error processing file '{}': {}",
                      planning_file,
                      ex.what()));
    }
  }

  spdlog::info("  Parse all input files time {:.3f}s", sw.elapsed().count());
  return my_planning;
}

PlanningDirectoryPeek peek_planning_directories(
    const std::vector<std::string>& planning_files,
    const std::optional<std::string>& input_directory)
{
  PlanningDirectoryPeek peek;

  for (const auto& planning_file : planning_files) {
    try {
      const auto fpath = locate_planning_file(
          planning_file, input_directory, /*verbose=*/false);
      if (!std::filesystem::exists(fpath)) {
        continue;  // parse_planning_files reports the missing file later
      }
      const auto json_result = daw::read_file(fpath.string());
      if (!json_result) {
        continue;
      }
      // Canonicalise so aliased option keys resolve exactly as in the
      // full parse.  `json_value` is lazy: only the `options` member is
      // walked; the (possibly large) `system` / `simulation` payloads
      // are skipped without being deserialised.
      const auto canonical = canonicalize_json_keys(json_result.value());
      const daw::json::json_value doc {daw::string_view {canonical}};
      const auto read_string =
          [&doc](std::string_view path) -> std::optional<std::string>
      {
        const auto member =
            doc.find_member(daw::string_view {path.data(), path.size()});
        if (!member || !member.is_string()) {
          return std::nullopt;
        }
        return member.as<std::string>();
      };
      // Later files override earlier ones, matching Planning::merge.
      if (auto v = read_string("options.output_directory")) {
        peek.output_directory = std::move(v);
      }
      if (auto v = read_string("options.log_directory")) {
        peek.log_directory = std::move(v);
      }
    } catch (const std::exception& ex) {
      // Best effort: a malformed file is reported by the full parse.
      spdlog::debug("peek_planning_directories: skipping '{}': {}",
                    planning_file,
                    ex.what());
    }
  }

  return peek;
}

}  // namespace gtopt
