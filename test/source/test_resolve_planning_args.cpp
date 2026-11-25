// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <filesystem>
#include <format>
#include <fstream>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/resolve_planning_args.hpp>

using namespace gtopt;

namespace
{

/// RAII helper: create a temporary directory tree, remove on destruction.
struct TmpDir
{
  std::filesystem::path path;

  explicit TmpDir(const std::string& name)
      : path(std::filesystem::temp_directory_path() / name)
  {
    std::filesystem::create_directories(path);
  }

  ~TmpDir() { std::filesystem::remove_all(path); }

  TmpDir(const TmpDir&) = delete;
  TmpDir& operator=(const TmpDir&) = delete;
  TmpDir(TmpDir&&) = delete;
  TmpDir& operator=(TmpDir&&) = delete;
};

/// Write @p content to the file at @p p (creating parent directories).
void write_file(const std::filesystem::path& p, std::string_view content)
{
  std::filesystem::create_directories(p.parent_path());
  std::ofstream ofs(p);
  ofs << content;
}

/// Create a minimal JSON file at the given path.
void touch_json(const std::filesystem::path& p)
{
  std::filesystem::create_directories(p.parent_path());
  std::ofstream ofs(p);
  ofs << "{}";
}

}  // namespace

TEST_CASE("resolve_planning_args — directory argument")  // NOLINT
{
  const TmpDir tmp("test_resolve_dir_arg");
  const auto case_dir = tmp.path / "my_case";
  std::filesystem::create_directories(case_dir);
  touch_json(case_dir / "my_case.json");

  const MainOptions opts {
      .planning_files = {case_dir.string()},
  };

  auto result = resolve_planning_args(opts);
  REQUIRE(result.has_value());
  const auto& resolved = *result;

  SUBCASE("planning file is resolved to dir/basename.json")
  {
    REQUIRE(resolved.planning_files.size() == 1);
    CHECK(resolved.planning_files[0] == (case_dir / "my_case.json").string());
  }

  SUBCASE("input_directory is set to the directory itself")
  {
    REQUIRE(resolved.input_directory.has_value());
    CHECK(resolved.input_directory.value_or("") == case_dir.string());
  }

  SUBCASE("output_directory is set to dir/results")
  {
    REQUIRE(resolved.output_directory.has_value());
    CHECK(resolved.output_directory.value_or("")
          == (case_dir / "results").string());
  }

  SUBCASE("log_directory is not pinned; it derives to dir/results/logs")
  {
    // Pinning it would shadow `--set log_directory` / JSON options.
    CHECK_FALSE(resolved.log_directory.has_value());
    CHECK(resolve_log_directory(resolved) == case_dir / "results" / "logs");
  }

  SUBCASE("cut_directory is set relative to dir")
  {
    REQUIRE(resolved.cut_directory.has_value());
    CHECK(resolved.cut_directory.value_or("") == (case_dir / "cuts").string());
  }

  SUBCASE("dirs_auto_resolved is true")
  {
    CHECK(resolved.dirs_auto_resolved);
  }
}

TEST_CASE("resolve_planning_args — directory missing json")  // NOLINT
{
  const TmpDir tmp("test_resolve_dir_no_json");
  const auto case_dir = tmp.path / "empty_case";
  std::filesystem::create_directories(case_dir);

  const MainOptions opts {
      .planning_files = {case_dir.string()},
  };

  auto result = resolve_planning_args(opts);
  CHECK_FALSE(result.has_value());
  CHECK(result.error().find("exist") != std::string::npos);
}

TEST_CASE("resolve_planning_args — bare stem fallback")  // NOLINT
{
  const TmpDir tmp("test_resolve_stem");
  touch_json(tmp.path / "my_case.json");

  // Pass "my_case" (no extension, not a directory) but the .json file exists
  const auto stem = (tmp.path / "my_case").string();
  const MainOptions opts {
      .planning_files = {stem},
  };

  auto result = resolve_planning_args(opts);
  REQUIRE(result.has_value());
  REQUIRE(result->planning_files.size() == 1);
  CHECK(result->planning_files[0] == (tmp.path / "my_case.json").string());
}

TEST_CASE("resolve_planning_args — existing file unchanged")  // NOLINT
{
  const TmpDir tmp("test_resolve_existing");
  const auto json_path = tmp.path / "plan.json";
  touch_json(json_path);

  const MainOptions opts {
      .planning_files = {json_path.string()},
  };

  auto result = resolve_planning_args(opts);
  REQUIRE(result.has_value());
  REQUIRE(result->planning_files.size() == 1);
  CHECK(result->planning_files[0] == json_path.string());
  // Directories should NOT be set (no directory inference for explicit files)
  CHECK_FALSE(result->input_directory.has_value());
  CHECK_FALSE(result->output_directory.has_value());
}

TEST_CASE(
    "resolve_planning_args — directory does not override explicit "
    "directories")  // NOLINT
{
  const TmpDir tmp("test_resolve_no_override");
  const auto case_dir = tmp.path / "my_case";
  std::filesystem::create_directories(case_dir);
  touch_json(case_dir / "my_case.json");

  const MainOptions opts {
      .planning_files = {case_dir.string()},
      .input_directory = "/custom/input",
      .output_directory = "/custom/output",
  };

  auto result = resolve_planning_args(opts);
  REQUIRE(result.has_value());
  CHECK(result->input_directory.value_or("") == "/custom/input");
  CHECK(result->output_directory.value_or("") == "/custom/output");
}

TEST_CASE("resolve_planning_args — auto-detect CWD")  // NOLINT
{
  const TmpDir tmp("test_resolve_cwd");
  const auto case_dir = tmp.path / "auto_case";
  std::filesystem::create_directories(case_dir);
  touch_json(case_dir / "auto_case.json");

  // Change to the case directory, then call with empty planning_files
  const auto original_cwd = std::filesystem::current_path();
  std::filesystem::current_path(case_dir);

  const MainOptions opts {};
  auto result = resolve_planning_args(opts);

  // Restore CWD before any CHECK (to avoid leaving CWD in tmp on failure)
  std::filesystem::current_path(original_cwd);

  REQUIRE(result.has_value());
  REQUIRE(result->planning_files.size() == 1);
  CHECK(result->planning_files[0] == (case_dir / "auto_case.json").string());
  // No directory overrides — JSON relative paths resolve against CWD
  CHECK_FALSE(result->input_directory.has_value());
}

TEST_CASE("resolve_planning_args — trailing-slash directory")  // NOLINT
{
  const TmpDir tmp("test_resolve_trailing_slash");
  const auto case_dir = tmp.path / "slash_case";
  std::filesystem::create_directories(case_dir);
  touch_json(case_dir / "slash_case.json");

  // Pass the path WITH a trailing slash
  const MainOptions opts {
      .planning_files = {(case_dir.string() + "/")},
  };

  auto result = resolve_planning_args(opts);
  REQUIRE(result.has_value());
  const auto& resolved = *result;

  SUBCASE("planning file is resolved to dir/basename.json")
  {
    REQUIRE(resolved.planning_files.size() == 1);
    CHECK(resolved.planning_files[0]
          == (case_dir / "slash_case.json").string());
  }

  SUBCASE("input_directory is set")
  {
    REQUIRE(resolved.input_directory.has_value());
    // The trailing-slash path is kept as-is by std::filesystem
    CHECK(resolved.input_directory.value_or("").find("slash_case")
          != std::string::npos);
  }

  SUBCASE("output_directory is set to dir/results")
  {
    REQUIRE(resolved.output_directory.has_value());
    CHECK(resolved.output_directory.value_or("").find("results")
          != std::string::npos);
  }
}

TEST_CASE("resolve_planning_args — multiple planning files")  // NOLINT
{
  const TmpDir tmp("test_resolve_multi");

  // First argument: a directory (triggers directory overrides)
  const auto case_dir = tmp.path / "dir_case";
  std::filesystem::create_directories(case_dir);
  touch_json(case_dir / "dir_case.json");

  // Second argument: an explicit .json file (no directory overrides)
  const auto extra_json = tmp.path / "extra.json";
  touch_json(extra_json);

  const MainOptions opts {
      .planning_files = {case_dir.string(), extra_json.string()},
  };

  auto result = resolve_planning_args(opts);
  REQUIRE(result.has_value());
  const auto& resolved = *result;

  SUBCASE("both planning files are resolved")
  {
    REQUIRE(resolved.planning_files.size() == 2);
    CHECK(resolved.planning_files[0] == (case_dir / "dir_case.json").string());
    CHECK(resolved.planning_files[1] == extra_json.string());
  }

  SUBCASE("directories are set from the directory argument")
  {
    REQUIRE(resolved.input_directory.has_value());
    CHECK(resolved.input_directory.value_or("") == case_dir.string());
    REQUIRE(resolved.output_directory.has_value());
    CHECK(resolved.output_directory.value_or("")
          == (case_dir / "results").string());
  }
}

TEST_CASE("resolve_planning_args — no files, no auto-detect")  // NOLINT
{
  const TmpDir tmp("test_resolve_empty");
  const auto empty_dir = tmp.path / "no_json_here";
  std::filesystem::create_directories(empty_dir);

  const auto original_cwd = std::filesystem::current_path();
  std::filesystem::current_path(empty_dir);

  const MainOptions opts {};
  auto result = resolve_planning_args(opts);

  std::filesystem::current_path(original_cwd);

  CHECK_FALSE(result.has_value());
  CHECK(result.error().find("no planning files given") != std::string::npos);
}

TEST_CASE(
    "resolve_planning_args — file in CWD has no directory "
    "overrides")  // NOLINT
{
  // When the planning file is specified without a directory prefix
  // (just "case.json"), no directory overrides should be set because
  // relative paths in the JSON already resolve against CWD correctly.
  const TmpDir tmp("test_resolve_cwd_file");
  const auto case_dir = tmp.path / "my_case";
  std::filesystem::create_directories(case_dir);
  touch_json(case_dir / "my_case.json");

  const auto original_cwd = std::filesystem::current_path();
  std::filesystem::current_path(case_dir);

  const MainOptions opts {
      .planning_files = {"my_case.json"},
  };

  auto result = resolve_planning_args(opts);
  std::filesystem::current_path(original_cwd);

  REQUIRE(result.has_value());
  REQUIRE(result->planning_files.size() == 1);
  CHECK(result->planning_files[0] == "my_case.json");
  CHECK_FALSE(result->input_directory.has_value());
  CHECK_FALSE(result->output_directory.has_value());
}

TEST_CASE("find_set_option — last matching key wins, null ignored")  // NOLINT
{
  const std::vector<std::string> set_opts {
      "output_directory=a",
      "log_directory=first",
      "sddp_options.log_directory=nested",
      "log_directory=second",
      "log_directory=null",
  };
  CHECK(find_set_option(set_opts, "log_directory").value_or("") == "second");
  CHECK(find_set_option(set_opts, "output_directory").value_or("") == "a");
  CHECK_FALSE(find_set_option(set_opts, "input_directory").has_value());
}

TEST_CASE("resolve_log_directory — precedence")  // NOLINT
{
  // Regression: `--set log_directory=<dir>` and JSON
  // `options.log_directory` used to be ignored for gtopt_N.log /
  // trace_N.log, which always went to `./output/logs` (CWD-relative).
  const TmpDir tmp("test_resolve_log_directory");
  const auto case_dir = tmp.path / "case_x";
  const auto json_path = case_dir / "case_x.json";

  SUBCASE("default is <json_dir>/output/logs")
  {
    write_file(json_path, R"({"options": {}})");
    const MainOptions opts {.planning_files = {json_path.string()}};
    CHECK(resolve_log_directory(opts) == case_dir / "output" / "logs");
  }

  SUBCASE("JSON output_directory moves the default")
  {
    write_file(json_path, R"({"options": {"output_directory": "res"}})");
    const MainOptions opts {.planning_files = {json_path.string()}};
    CHECK(resolve_log_directory(opts) == case_dir / "res" / "logs");
  }

  SUBCASE("--set output_directory overrides JSON output_directory")
  {
    write_file(json_path, R"({"options": {"output_directory": "res"}})");
    const MainOptions opts {
        .planning_files = {json_path.string()},
        .set_options = {"output_directory=other"},
    };
    CHECK(resolve_log_directory(opts) == case_dir / "other" / "logs");
  }

  SUBCASE("JSON log_directory is honoured")
  {
    const auto logs = (tmp.path / "json_logs").string();
    write_file(json_path,
               std::format(R"({{"options": {{"output_directory": "res",
                               "log_directory": "{}"}},
                               "system": {{"bus_array": [{{"uid": 1}}]}}}})",
                           logs));
    const MainOptions opts {.planning_files = {json_path.string()}};
    CHECK(resolve_log_directory(opts) == std::filesystem::path(logs));
  }

  SUBCASE("--set log_directory overrides JSON log_directory")
  {
    write_file(json_path, R"({"options": {"log_directory": "json_logs"}})");
    const auto logs = (tmp.path / "set_logs").string();
    const MainOptions opts {
        .planning_files = {json_path.string()},
        .set_options = {"log_directory=" + logs},
    };
    CHECK(resolve_log_directory(opts) == std::filesystem::path(logs));
  }

  SUBCASE("--set log_directory beats a directory argument's auto dirs")
  {
    write_file(json_path, R"({"options": {}})");
    const auto logs = (tmp.path / "set_logs").string();
    const MainOptions raw {
        .planning_files = {case_dir.string()},
        .set_options = {"log_directory=" + logs},
    };
    const auto resolved = resolve_planning_args(raw);
    REQUIRE(resolved.has_value());
    CHECK(resolve_log_directory(*resolved) == std::filesystem::path(logs));
  }

  SUBCASE("CLI --log-directory wins over everything")
  {
    write_file(json_path, R"({"options": {"log_directory": "json_logs"}})");
    const MainOptions opts {
        .planning_files = {json_path.string()},
        .log_directory = "cli_logs",
        .set_options = {"log_directory=set_logs"},
    };
    CHECK(resolve_log_directory(opts) == std::filesystem::path("cli_logs"));
  }
}

TEST_CASE("peek_planning_directories — later files override")  // NOLINT
{
  const TmpDir tmp("test_peek_planning_directories");
  const auto a = tmp.path / "a.json";
  const auto b = tmp.path / "b.json";
  write_file(
      a, R"({"options": {"output_directory": "oa", "log_directory": "la"}})");
  write_file(b, R"({"options": {"log_directory": "lb"}})");

  const auto peek = peek_planning_directories({a.string(), b.string()});
  CHECK(peek.output_directory.value_or("") == "oa");
  CHECK(peek.log_directory.value_or("") == "lb");

  // Missing / malformed files are skipped (the full parse reports them).
  const auto bad = tmp.path / "bad.json";
  write_file(bad, "{not json");
  const auto peek2 = peek_planning_directories(
      {bad.string(), (tmp.path / "missing.json").string()});
  CHECK_FALSE(peek2.output_directory.has_value());
  CHECK_FALSE(peek2.log_directory.has_value());
}

TEST_CASE("resolve_planning_args — '.' and '..' directory arguments")  // NOLINT
{
  const TmpDir tmp("test_resolve_dot_dir");
  const auto case_dir = tmp.path / "dot_case";
  const auto sub_dir = case_dir / "sub";
  std::filesystem::create_directories(sub_dir);
  touch_json(case_dir / "dot_case.json");

  const auto original_cwd = std::filesystem::current_path();

  // `gtopt .` from inside the case dir names the JSON after the
  // directory, not "..json".
  std::filesystem::current_path(case_dir);
  auto dot = resolve_planning_args(MainOptions {.planning_files = {"."}});
  auto dot_slash =
      resolve_planning_args(MainOptions {.planning_files = {"./"}});
  // `gtopt ..` from a subdirectory of the case.
  std::filesystem::current_path(sub_dir);
  auto dotdot = resolve_planning_args(MainOptions {.planning_files = {".."}});
  std::filesystem::current_path(original_cwd);

  REQUIRE(dot.has_value());
  REQUIRE(dot->planning_files.size() == 1);
  CHECK(std::filesystem::path(dot->planning_files[0]).filename()
        == "dot_case.json");
  CHECK(dot->output_directory.value_or("").find("results")
        != std::string::npos);

  REQUIRE(dot_slash.has_value());
  CHECK(std::filesystem::path(dot_slash->planning_files.front()).filename()
        == "dot_case.json");

  REQUIRE(dotdot.has_value());
  CHECK(std::filesystem::path(dotdot->planning_files.front()).filename()
        == "dot_case.json");
}
