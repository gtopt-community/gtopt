/**
 * @file      test_output_directory_resolution.cpp
 * @brief     End-to-end: where gtopt_main writes its output
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * One rule for the output directory:
 *   - given on the command line (`-d` or `--set output_directory=`):
 *     relative to the current directory — both spellings agree;
 *   - given in the planning JSON (`options.output_directory`): relative to
 *     the JSON file's directory, also when the case is passed as a
 *     directory argument (`gtopt case/`);
 *   - unset: `output` next to the JSON, or `results` inside the directory
 *     for a directory argument.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <filesystem>
#include <format>
#include <fstream>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_main.hpp>

using namespace gtopt;

namespace
{

namespace fs = std::filesystem;

/// Tiny single-bus case; @p options_extra is spliced into `options`.
[[nodiscard]] std::string tiny_case_json(std::string_view options_extra)
{
  return std::format(R"({{
    "options": {{
      "input_directory": ".",
      "output_format": "csv",
      "output_compression": "uncompressed"{}
    }},
    "simulation": {{
      "block_array": [{{"uid": 1, "duration": 1}}],
      "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
      "scenario_array": [{{"uid": 1}}]
    }},
    "system": {{
      "name": "outdir_case",
      "bus_array": [{{"uid": 1, "name": "b1"}}],
      "generator_array": [
        {{"uid": 1, "name": "g1", "bus": 1, "gcost": 10.0,
          "capacity": 200.0}}
      ],
      "demand_array": [
        {{"uid": 1, "name": "d1", "bus": 1, "capacity": 50.0}}
      ]
    }}
  }})",
                     options_extra);
}

/// Scratch tree `<tmp>/<tag>/case_sub/case_sub.json`; the test runs with
/// the CWD at `<tmp>/<tag>` and restores it on destruction.
struct CaseTree
{
  fs::path base;
  fs::path case_dir;
  fs::path saved_cwd;

  CaseTree(const std::string& tag, std::string_view options_extra)
      : base(fs::temp_directory_path() / tag)
      , case_dir(base / "case_sub")
      , saved_cwd(fs::current_path())
  {
    fs::remove_all(base);
    fs::create_directories(case_dir);
    std::ofstream {case_dir / "case_sub.json"} << tiny_case_json(options_extra);
    fs::current_path(base);
  }

  ~CaseTree()
  {
    fs::current_path(saved_cwd);
    fs::remove_all(base);
  }

  CaseTree(const CaseTree&) = delete;
  CaseTree& operator=(const CaseTree&) = delete;
  CaseTree(CaseTree&&) = delete;
  CaseTree& operator=(CaseTree&&) = delete;
};

[[nodiscard]] bool has_solution(const fs::path& dir)
{
  return fs::exists(dir / "solution.csv");
}

}  // namespace

TEST_CASE(
    "output_directory: --set and -d are both relative to the CWD")  // NOLINT
{
  SUBCASE("--set output_directory")
  {
    const CaseTree tree("gtopt_outdir_set", "");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub/case_sub.json"},
        .set_options = {"output_directory=out_rel"},
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.base / "out_rel"));
    CHECK_FALSE(fs::exists(tree.case_dir / "out_rel"));
  }

  SUBCASE("-d (CLI flag)")
  {
    const CaseTree tree("gtopt_outdir_cli", "");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub/case_sub.json"},
        .output_directory = "out_rel",
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.base / "out_rel"));
    CHECK_FALSE(fs::exists(tree.case_dir / "out_rel"));
  }

  SUBCASE("--set output_directory wins over the JSON value")
  {
    const CaseTree tree("gtopt_outdir_set_json",
                        R"(, "output_directory": "json_out")");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub/case_sub.json"},
        .set_options = {"output_directory=."},
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.base));
    CHECK_FALSE(fs::exists(tree.case_dir / "json_out"));
  }
}

TEST_CASE(
    "output_directory: a JSON value is relative to the JSON file")  // NOLINT
{
  SUBCASE("planning file argument")
  {
    const CaseTree tree("gtopt_outdir_json_file",
                        R"(, "output_directory": "json_out")");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub/case_sub.json"},
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.case_dir / "json_out"));
  }

  SUBCASE("directory argument honours the JSON value")
  {
    // Regression: the directory argument forced `<dir>/results` as if
    // it were a CLI flag, ignoring the case's options.output_directory.
    const CaseTree tree("gtopt_outdir_json_dir",
                        R"(, "output_directory": "json_out")");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub"},
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.case_dir / "json_out"));
    CHECK_FALSE(fs::exists(tree.case_dir / "results"));
  }

  SUBCASE("directory argument without a JSON value defaults to results")
  {
    const CaseTree tree("gtopt_outdir_dir_default", "");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub"},
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.case_dir / "results"));
  }

  SUBCASE("planning file argument without a JSON value defaults to output")
  {
    const CaseTree tree("gtopt_outdir_file_default", "");
    const auto rc = gtopt_main(MainOptions {
        .planning_files = {"case_sub/case_sub.json"},
    });
    REQUIRE(rc.has_value());
    CHECK(*rc == 0);
    CHECK(has_solution(tree.case_dir / "output"));
  }
}
