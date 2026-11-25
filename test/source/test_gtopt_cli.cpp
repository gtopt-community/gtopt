/**
 * @file      test_gtopt_cli.cpp
 * @brief     Unit tests for the gtopt command-line front end (gtopt_cli)
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Drives `gtopt_cli_main()` — the body of the standalone binary's
 * `main()` — in-process with an argument vector and string streams:
 * informational sub-commands, CLI parse errors, the input-error /
 * success exit codes of a real run, and the logging side effects.
 */

#include <cstdlib>
#include <filesystem>
#include <format>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/build_info.hpp>
#include <gtopt/gtopt_cli.hpp>
#include <gtopt/json_schema.hpp>
#include <gtopt/kappa_mode.hpp>
#include <gtopt/names_registry.hpp>
#include <gtopt/plugin_integrity.hpp>
#include <gtopt/solver_registry.hpp>
#include <spdlog/spdlog.h>

using namespace gtopt;

namespace
{

/// Restores the global spdlog level that gtopt_cli_main() changes.
struct SpdlogLevelGuard
{
  spdlog::level::level_enum saved {spdlog::get_level()};
  SpdlogLevelGuard() = default;
  SpdlogLevelGuard(const SpdlogLevelGuard&) = delete;
  SpdlogLevelGuard& operator=(const SpdlogLevelGuard&) = delete;
  SpdlogLevelGuard(SpdlogLevelGuard&&) = delete;
  SpdlogLevelGuard& operator=(SpdlogLevelGuard&&) = delete;
  ~SpdlogLevelGuard() { spdlog::set_level(saved); }
};

struct CliRun
{
  int code {-1};
  std::string out;
  std::string err;
};

CliRun run_cli(const std::vector<std::string>& args)
{
  const SpdlogLevelGuard guard;
  std::ostringstream out;
  std::ostringstream err;
  const int code = gtopt_cli_main(args, out, err, /*stdout_is_tty=*/true);
  return CliRun {
      .code = code,
      .out = out.str(),
      .err = err.str(),
  };
}

/// Fresh, empty per-test scratch directory.
std::filesystem::path scratch_dir(const std::string& name)
{
  auto dir = std::filesystem::temp_directory_path() / ("gtopt_cli_" + name);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

// One bus, one 10 $/MWh generator, one 50 MW demand, one 1 h block:
// optimal objective = 50 MWh * 10 $/MWh = 500 $ (before scaling).
constexpr auto tiny_case_json = R"({
  "options": {
    "output_format": "csv",
    "output_compression": "uncompressed",
    "model_options": {"demand_fail_cost": 1000}
  },
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
    "scenario_array": [{"uid": 1}]
  },
  "system": {
    "name": "cli_tiny",
    "bus_array": [{"uid": 1, "name": "b1"}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10.0, "capacity": 200.0}
    ],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 50.0}]
  }
})";

std::filesystem::path write_tiny_case(const std::filesystem::path& dir)
{
  const auto path = dir / "cli_tiny.json";
  std::ofstream(path) << tiny_case_json;
  return path;
}

bool contains(const std::string& haystack, std::string_view needle)
{
  return haystack.contains(needle);
}

}  // namespace

TEST_CASE("gtopt_cli_main - informational commands")  // NOLINT
{
  SUBCASE("--help prints options, examples and output layout")
  {
    const auto r = run_cli({"--help"});
    CHECK(r.code == 0);
    CHECK(contains(r.out, "--lp-only"));
    CHECK(contains(r.out, "Examples\n========"));
    CHECK(contains(r.out, "Outputs\n======="));
    CHECK(r.err.empty());
  }

  SUBCASE("-h short flag is the same as --help")
  {
    CHECK(run_cli({"-h"}).out == run_cli({"--help"}).out);
  }

  SUBCASE("--help shows the compiled-in --plugin-check default")
  {
    const auto r = run_cli({"--help"});
    CHECK(contains(r.out, "--plugin-check"));
    CHECK(contains(r.out, "GTOPT_PLUGIN_CHECK_DEFAULT"));
  }

  SUBCASE("--version: build version, then the plugin check mode")
  {
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    ::unsetenv("GTOPT_PLUGIN_CHECK");
    const auto r = run_cli({"--version"});
    CHECK(r.code == 0);
    CHECK(r.out
          == build_info().version_string() + "\n"
              + std::format("plugin check: {} (compiled default)\n",
                            k_plugin_check_default_name)
              + describe_kappa_mode() + "\n");
  }

  SUBCASE("--plugin-check on the command line shows as its source")
  {
    const auto r = run_cli({"--plugin-check", "warning", "--version"});
    SolverRegistry::set_plugin_check_mode(std::nullopt);
    CHECK(r.code == 0);
    CHECK(contains(r.out, "plugin check: warning (from --plugin-check"));
  }

  SUBCASE("--plugin-check rejects an unknown mode")
  {
    const auto r = run_cli({"--plugin-check", "strict", "--version"});
    SolverRegistry::set_plugin_check_mode(std::nullopt);
    CHECK(r.code == 1);
    CHECK(contains(r.err, "--plugin-check must be 'warning' or 'error'"));
  }

  SUBCASE("--help wins over --version (checked first)")
  {
    const auto r = run_cli({"--version", "--help"});
    CHECK(r.code == 0);
    CHECK(contains(r.out, "Examples"));
  }

  SUBCASE("--solvers lists the loadable plugins")
  {
    const auto r = run_cli({"--solvers"});
    CHECK(r.code == 0);
    const auto solvers = SolverRegistry::instance().available_solvers();
    if (solvers.empty()) {
      CHECK(contains(r.out, "No LP solver plugins found."));
      CHECK(contains(r.out, "GTOPT_PLUGIN_DIR"));
    } else {
      CHECK(contains(r.out, "Available LP solvers:\n"));
      for (const auto& s : solvers) {
        CHECK(contains(r.out, "  " + s + "\n"));
      }
      // Identity block after a blank line (name block stays parseable).
      CHECK(contains(r.out, "\n\nPlugin details:\n"));
      CHECK(contains(r.out, "    build-id   "));
      CHECK(contains(r.out, "    build tag  "));
    }
    CHECK(contains(r.out, "\nplugin check: "));
  }

  SUBCASE("--check-solvers with an unknown solver fails with exit 1")
  {
    const auto r = run_cli({"--check-solvers", "no_such_solver_xyz"});
    CHECK(r.code == 1);
    CHECK(contains(r.err, "ERROR: solver 'no_such_solver_xyz' not available."));
    CHECK(r.out.empty());
  }
}

TEST_CASE("gtopt_cli_main - --check-solvers runs one solver suite")  // NOLINT
{
  auto& registry = SolverRegistry::instance();
  registry.load_all_plugins();
  // Prefer a fast, non-CLP backend; skip when none is available.
  std::string solver;
  for (const auto* cand : {"highs", "cplex", "cbc"}) {
    if (registry.has_solver(cand)) {
      solver = cand;
      break;
    }
  }
  if (solver.empty()) {
    MESSAGE("no highs/cplex/cbc plugin: skipping");
    return;
  }
  const auto r = run_cli({"--check-solvers", solver});
  CHECK(r.code == 0);
  CHECK(contains(r.out, "passed, 0 failed"));
  CHECK(contains(r.out, "✓"));
  CHECK_FALSE(contains(r.out, "✗"));
}

TEST_CASE("gtopt_cli_main - --list-dialects")  // NOLINT
{
  SUBCASE("no filter dumps every dialect with a summary trailer")
  {
    const auto r = run_cli({"--list-dialects"});
    CHECK(r.code == 0);
    CHECK(r.out.starts_with("# canonical\tdialect\talias\tunit\n"));
    CHECK(contains(r.out, " canonicals, "));
    CHECK(contains(r.out, " dialects, names from "));
  }

  SUBCASE("a known filter restricts rows to that dialect")
  {
    // Pick a registered dialect from the registry instead of hard-coding.
    const auto& names = NamesRegistry::instance();
    std::string dialect;
    for (const auto& [canonical, aliases] : names.canonical_to_aliases()) {
      for (const auto& alias : aliases) {
        if (const auto d = names.dialect_for(alias); d && !d->empty()) {
          dialect = std::string {*d};
          break;
        }
      }
      if (!dialect.empty()) {
        break;
      }
    }
    REQUIRE_FALSE(dialect.empty());

    const auto r = run_cli({"--list-dialects", dialect});
    CHECK(r.code == 0);
    CHECK(contains(r.out, "rows matching dialect '" + dialect + "'"));
    // Every data row carries the requested dialect in column 2.
    std::istringstream lines(r.out);
    std::string line;
    int data_rows = 0;
    while (std::getline(lines, line)) {
      if (line.empty() || line.starts_with('#')) {
        continue;
      }
      const auto t1 = line.find('\t');
      REQUIRE(t1 != std::string::npos);
      const auto t2 = line.find('\t', t1 + 1);
      REQUIRE(t2 != std::string::npos);
      CHECK(line.substr(t1 + 1, t2 - t1 - 1) == dialect);
      ++data_rows;
    }
    CHECK(data_rows > 0);
  }

  SUBCASE("an unknown filter is an error listing the known dialects")
  {
    const auto r = run_cli({"--list-dialects", "no_such_dialect"});
    CHECK(r.code == 1);
    CHECK(contains(r.err, "unknown dialect 'no_such_dialect'"));
    CHECK(contains(r.err, "Known dialects: "));
    CHECK(r.out.empty());
  }
}

TEST_CASE("gtopt_cli_main - --json-schema")  // NOLINT
{
  SUBCASE("no name dumps the default schema")
  {
    const auto r = run_cli({"--json-schema"});
    CHECK(r.code == 0);
    CHECK(r.out == dump_json_schema() + "\n");
    CHECK(contains(r.out, "\"properties\""));
  }

  SUBCASE("every known schema name is accepted")
  {
    for (const auto name : known_schema_names()) {
      const auto r = run_cli({"--json-schema", std::string {name}});
      CHECK(r.code == 0);
      CHECK_FALSE(r.out.empty());
    }
  }

  SUBCASE("an unknown name exits 1 without stdout output")
  {
    const auto r = run_cli({"--json-schema", "no_such_schema"});
    CHECK(r.code == 1);
    CHECK(r.out.empty());
  }
}

TEST_CASE("gtopt_cli_main - parse errors")  // NOLINT
{
  SUBCASE("unknown option")
  {
    const auto r = run_cli({"--no-such-flag"});
    CHECK(r.code == 1);
    CHECK(r.out.starts_with("ERROR: "));
    // The option table is echoed after the error.
    CHECK(contains(r.out, "--lp-only"));
  }

  SUBCASE("value expected for a typed option")
  {
    const auto r = run_cli({"--threads", "not_an_int"});
    CHECK(r.code == 1);
    CHECK(r.out.starts_with(
        "ERROR: invalid value 'not_an_int' for option '--threads'"));
  }

  SUBCASE("out-of-range typed value")
  {
    const auto r = run_cli({"--threads", "99999999999999999999"});
    CHECK(r.code == 1);
    CHECK(
        r.out.starts_with("ERROR: value '99999999999999999999' out of "
                          "range for option '--threads'"));
  }
}

TEST_CASE("gtopt_cli_main - run exit codes")  // NOLINT
{
  SUBCASE("missing planning file is an input error (exit 2)")
  {
    const auto dir = scratch_dir("missing");
    const auto r = run_cli({
        (dir / "does_not_exist.json").string(),
        "--quiet",
    });
    CHECK(r.code == 2);
  }

  SUBCASE("bad --set key is an input error")
  {
    const auto dir = scratch_dir("badset");
    const auto json = write_tiny_case(dir);
    const auto r = run_cli({
        json.string(),
        "--quiet",
        "--set",
        "no_such_section.no_such_key=1",
    });
    CHECK(r.code != 0);
  }

  SUBCASE("--lp-only builds without solving and exits 0")
  {
    const auto dir = scratch_dir("lponly");
    const auto json = write_tiny_case(dir);
    const auto out_dir = dir / "out";
    const auto r = run_cli({
        json.string(),
        "--lp-only",
        "--quiet",
        "--output-directory",
        out_dir.string(),
    });
    CHECK(r.code == 0);
    // The pre-solve snapshot and README are always written.
    CHECK(std::filesystem::exists(out_dir / "gtopt_state.json"));
    CHECK(std::filesystem::exists(out_dir / "README.md"));
  }

  SUBCASE("full solve writes the solution and exits 0")
  {
    const auto dir = scratch_dir("solve");
    const auto json = write_tiny_case(dir);
    const auto out_dir = dir / "out";
    const auto merged = dir / "merged.json";
    const auto r = run_cli({
        json.string(),
        "--output-directory",
        out_dir.string(),
        "--json-file",
        merged.string(),
        "--quiet",
    });
    CHECK(r.code == 0);
    CHECK(std::filesystem::exists(merged));
    CHECK(std::filesystem::exists(out_dir / "solution.csv"));
    // Generator output written as CSV (output_format from the JSON).
    CHECK(std::filesystem::exists(out_dir / "Generator"));
  }

  SUBCASE("--verbose and --quiet set the global spdlog level")
  {
    const auto dir = scratch_dir("levels");
    const auto json = write_tiny_case(dir);
    const SpdlogLevelGuard guard;
    std::ostringstream out;
    std::ostringstream err;
    CHECK(
        gtopt_cli_main({json.string(), "--lp-only", "--quiet"}, out, err, true)
        == 0);
    CHECK(spdlog::get_level() == spdlog::level::off);
    CHECK(gtopt_cli_main(
              {json.string(), "--lp-only", "--verbose"}, out, err, true)
          == 0);
    CHECK(spdlog::get_level() == spdlog::level::trace);
  }
}
