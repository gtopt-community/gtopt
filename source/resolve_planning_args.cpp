/**
 * @file      resolve_planning_args.cpp
 * @brief     Resolve planning file arguments and directory context
 * @date      2026-03-21
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <filesystem>
#include <format>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/resolve_planning_args.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

[[nodiscard]] std::expected<MainOptions, std::string> resolve_planning_args(
    MainOptions opts)
{
  namespace fs = std::filesystem;

  const auto user_set = [&opts](std::string_view key)
  { return find_set_option(opts.set_options, key).has_value(); };
  // First directory argument (`gtopt dir/`), if any.
  std::optional<fs::path> dir_argument;

  // ── Directory argument → dir/basename.json ──
  // ── Bare stem / missing extension fallback → stem.json ──
  for (auto& pf : opts.planning_files) {
    const auto path = fs::path(pf);

    if (fs::is_directory(path)) {
      // Name the JSON after the directory itself: `.`, `..`, `../`
      // and a trailing slash have no usable filename(), so take it
      // from the absolute, normalised path (`gtopt .` inside
      // `case/` -> `./case.json`).
      auto norm = fs::absolute(path).lexically_normal();
      if (norm.filename().empty()) {
        norm = norm.parent_path();  // "/foo/bar/" -> "/foo/bar"
      }
      const auto dir_name = norm.filename().string();
      const auto json_path = path / (dir_name + ".json");
      if (fs::exists(json_path)) {
        spdlog::info("directory argument '{}' -> planning file '{}'",
                     pf,
                     json_path.string());
        pf = json_path.string();

        // Set directories relative to the given directory (unless already
        // set by the user via CLI).  plp2gtopt writes input_directory="."
        // in the JSON (relative to the JSON location), so when running
        // from a parent directory we must point at the actual dir.
        // Mark as auto-resolved so apply_cli_options skips deprecation
        // warnings.
        //
        // A directory the user already chose via `--set <key>=...` is
        // left alone: the auto-resolved value would otherwise be applied
        // as a CLI flag, which overrides `--set` in `configure_planning`.
        //
        // `output_directory` is decided after the loop: an
        // `options.output_directory` in the planning JSON is honoured
        // (anchored at the JSON's directory by gtopt_main, like for
        // `gtopt dir/case.json`); `dir/results` is only the default.
        //
        // `log_directory` is deliberately NOT auto-resolved: its default
        // is `<output_directory>/logs` (see `PlanningOptionsLP::
        // log_directory`), which already yields `dir/results/logs`, and
        // pinning it here would shadow a `log_directory` given in the
        // JSON options or via `--set`.
        if (!opts.input_directory && !user_set("input_directory")) {
          opts.input_directory = path.string();
        }
        if (!dir_argument) {
          dir_argument = path;
        }
        if (!opts.cut_directory && !user_set("sddp_options.cut_directory")) {
          opts.cut_directory = (path / "cuts").string();
        }
        opts.dirs_auto_resolved = true;
      } else {
        // Directory exists but dir/basename.json does not — treat as a
        // bare stem (e.g. "bat4b24" is both a data directory and a stem
        // for "bat4b24.json" in CWD).
        auto with_ext = path;
        with_ext.replace_extension(".json");
        if (fs::exists(with_ext)) {
          spdlog::info(
              "'{}' is a directory but '{}.json' found as sibling", pf, pf);
          pf = with_ext.string();
        } else {
          return std::unexpected(std::format(
              "Directory '{}' given but neither '{}' nor '{}' exist",
              pf,
              json_path.string(),
              with_ext.string()));
        }
      }
    } else if (!fs::exists(path)) {
      // Not a directory and doesn't exist — try appending .json
      auto with_ext = path;
      with_ext.replace_extension(".json");
      if (fs::exists(with_ext)) {
        spdlog::info("'{}' not found, using '{}'", pf, with_ext.string());
        pf = with_ext.string();
      }
      // If still not found, parse_planning_files will report the error.
    }
  }

  // ── Directory argument: default output directory `dir/results` ──
  // Only when neither the CLI, `--set` nor any planning JSON chose one.
  // A JSON `options.output_directory` is anchored at the JSON's own
  // directory by gtopt_main, so `gtopt dir/` and `gtopt dir/dir.json`
  // write to the same place.
  if (dir_argument && !opts.output_directory && !user_set("output_directory")
      && !peek_planning_directories(opts.planning_files, opts.input_directory)
              .output_directory)
  {
    opts.output_directory = (*dir_argument / "results").string();
  }

  // ── Auto-detect CWD as input directory ──
  if (opts.planning_files.empty()) {
    const auto cwd = fs::current_path();
    const auto cwd_name = cwd.filename().string();
    const auto candidate = cwd / (cwd_name + ".json");
    if (fs::exists(candidate)) {
      spdlog::info("auto-detected planning file '{}' in current directory",
                   candidate.filename().string());
      opts.planning_files.push_back(candidate.string());

      // Running inside the input directory: all relative paths in the JSON
      // resolve correctly against CWD, so no directory overrides are needed.
    } else {
      return std::unexpected("no planning files given and no auto-detected "
                             + cwd_name
                             + ".json in current directory; use --help");
    }
  }

  return opts;
}

[[nodiscard]] std::optional<std::string> find_set_option(
    const std::vector<std::string>& set_options, std::string_view key)
{
  std::optional<std::string> found;
  for (const auto& opt : set_options) {
    const auto eq_pos = opt.find('=');
    if (eq_pos == std::string::npos
        || std::string_view {opt}.substr(0, eq_pos) != key)
    {
      continue;
    }
    auto value = opt.substr(eq_pos + 1);
    if (value != "null") {
      found = std::move(value);
    }
  }
  return found;
}

}  // namespace gtopt
