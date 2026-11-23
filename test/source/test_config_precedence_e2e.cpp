// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_config_precedence_e2e.cpp
 * @brief     End-to-end: planning-JSON options, `--set` and CLI flags are
 *            effective in the solved run, with CLI > --set > JSON > default
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Every check reads an artefact of the run (solution.csv objective and
 * scale, written file formats / parquet codec, solver log names, LP debug
 * dumps, the post-solve planning.json) instead of the parsed options, so
 * an option that is parsed but never reaches the LP fails here.
 *
 * Precedence implemented by gtopt_main / configure_planning (verified):
 *   1. specific CLI flags (MainOptions fields: --output-format, --no-scale,
 *      --mip-gap, ...)                                   — highest
 *   2. `--set key=value` (applied before the CLI flags)
 *   3. planning JSON `options` (later -s files merge over earlier ones)
 *   4. built-in defaults                                 — lowest
 * `.gtopt.conf` values enter as CLI-flag defaults (see
 * test_config_canary_files.cpp for that source and its known inversion
 * against `--set`).
 */

#include <algorithm>
#include <filesystem>
#include <format>
#include <map>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/solver_registry.hpp>
#include <parquet/file_reader.h>
#include <parquet/metadata.h>

#include "cascade_helpers.hpp"
#include "config_test_helpers.hpp"

namespace cfg = gtopt::test::config;

namespace config_precedence_test
{
namespace
{

namespace fs = std::filesystem;

[[nodiscard]] bool prec_has_solver(std::string_view name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver(name);
}

/// First data row of `<out>/solution.csv`, keyed by header name.
[[nodiscard]] std::map<std::string, std::string> prec_solution_row(
    const fs::path& out)
{
  std::istringstream in {cfg::slurp(out / "solution.csv")};
  std::string header;
  std::string row;
  std::getline(in, header);
  std::getline(in, row);
  const auto split = [](const std::string& line)
  {
    std::vector<std::string> cells;
    std::istringstream ss {line};
    std::string cell;
    while (std::getline(ss, cell, ',')) {
      cells.push_back(cell);
    }
    return cells;
  };
  const auto keys = split(header);
  const auto vals = split(row);
  std::map<std::string, std::string> m;
  for (std::size_t i = 0; i < keys.size() && i < vals.size(); ++i) {
    m[keys[i]] = vals[i];
  }
  return m;
}

[[nodiscard]] double prec_cell(const std::map<std::string, std::string>& row,
                               const std::string& key)
{
  const auto it = row.find(key);
  REQUIRE(it != row.end());
  return std::stod(it->second);
}

/// Run gtopt_main on `<case>/case.json` written from @p options_body.
[[nodiscard]] int prec_run(const cfg::RunTree& t,
                           std::string_view options_body,
                           MainOptions opts = {},
                           double demand = 50.0)
{
  const auto json = t.case_dir / "case.json";
  cfg::write_text(json, cfg::case_json(options_body, demand));
  opts.planning_files = {json.string()};
  opts.async_logger = false;
  const auto r = gtopt_main(opts);
  if (!r.has_value()) {
    MESSAGE("gtopt_main error: " << r.error());
    return -1;
  }
  return *r;
}

/// Compression codec of the first column chunk of a parquet file.
[[nodiscard]] parquet::Compression::type prec_parquet_codec(
    const fs::path& file)
{
  const auto reader = parquet::ParquetFileReader::OpenFile(file.string());
  return reader->metadata()->RowGroup(0)->ColumnChunk(0)->compression();
}

}  // namespace
}  // namespace config_precedence_test

using namespace config_precedence_test;

TEST_CASE(  // NOLINT
    "Config precedence e2e - model options reach the solved objective")
{
  const cfg::RunTree t {"prec-model"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const auto base = std::format(R"("output_directory": "{}",
      "output_format": "csv", "output_compression": "uncompressed",
      "model_options": {{"demand_fail_cost": 1234, "scale_objective": 7}})",
                                t.out.generic_string());
  // 300 MW demand vs a 200 MW generator: 100 MWh unserved, so the
  // objective is 200*10 + 100*demand_fail_cost.
  constexpr double demand = 300.0;

  SUBCASE("JSON values are effective")
  {
    REQUIRE(prec_run(t, base, {}, demand) == 0);
    const auto row = prec_solution_row(t.out);
    CHECK(prec_cell(row, "scale_objective") == doctest::Approx(7.0));
    CHECK(prec_cell(row, "obj_value")
          == doctest::Approx(2000.0 + (100.0 * 1234)));
  }

  SUBCASE("--set beats the JSON")
  {
    REQUIRE(prec_run(t,
                     base,
                     MainOptions {
                         .set_options =
                             {
                                 "model_options.demand_fail_cost=555",
                                 "model_options.scale_objective=3",
                             },
                     },
                     demand)
            == 0);
    const auto row = prec_solution_row(t.out);
    CHECK(prec_cell(row, "scale_objective") == doctest::Approx(3.0));
    CHECK(prec_cell(row, "obj_value")
          == doctest::Approx(2000.0 + (100.0 * 555)));
  }

  SUBCASE("a CLI flag (--no-scale) beats both --set and the JSON")
  {
    REQUIRE(prec_run(t,
                     base,
                     MainOptions {
                         .no_scale = true,
                         .set_options = {"model_options.scale_objective=3"},
                     },
                     demand)
            == 0);
    const auto row = prec_solution_row(t.out);
    CHECK(prec_cell(row, "scale_objective") == doctest::Approx(1.0));
    // --no-scale only touches scaling: the JSON fail cost still applies.
    CHECK(prec_cell(row, "obj_value")
          == doctest::Approx(2000.0 + (100.0 * 1234)));
  }

  SUBCASE("a later planning file overrides an earlier one")
  {
    const auto overlay = t.case_dir / "overlay.json";
    cfg::write_text(
        overlay, R"({"options": {"model_options": {"demand_fail_cost": 99}}})");
    cfg::write_text(t.case_dir / "case.json", cfg::case_json(base, demand));
    const auto r = gtopt_main(MainOptions {
        .planning_files =
            {
                (t.case_dir / "case.json").string(),
                overlay.string(),
            },
        .async_logger = false,
    });
    REQUIRE(r.value_or(-1) == 0);
    const auto row = prec_solution_row(t.out);
    CHECK(prec_cell(row, "obj_value")
          == doctest::Approx(2000.0 + (100.0 * 99)));
  }
}

TEST_CASE(  // NOLINT
    "Config precedence e2e - output format and codec reach the files")
{
  const cfg::RunTree t {"prec-output"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  // Layout: parquet = <field>.parquet/scene=S/phase=P/part.parquet,
  //         csv     = <field>_sS_pP.csv.
  const auto gen_dir = t.out / "Generator";
  const auto gen_parquet = gen_dir / "generation_sol.parquet" / "scene=0"
      / "phase=0" / "part.parquet";
  const auto gen_csv = gen_dir / "generation_sol_s0_p0.csv";
  const auto body = [&](std::string_view fmt, std::string_view codec)
  {
    return std::format(R"("output_directory": "{}",
        "output_format": "{}", "output_compression": "{}")",
                       t.out.generic_string(),
                       fmt,
                       codec);
  };

  SUBCASE("JSON output_compression=zstd is the codec on disk")
  {
    REQUIRE(prec_run(t, body("parquet", "zstd")) == 0);
    REQUIRE(fs::exists(gen_parquet));
    CHECK(prec_parquet_codec(gen_parquet) == parquet::Compression::ZSTD);
  }

  SUBCASE("--set output_compression beats the JSON")
  {
    REQUIRE(prec_run(t,
                     body("parquet", "zstd"),
                     MainOptions {.set_options = {"output_compression=snappy"}})
            == 0);
    CHECK(prec_parquet_codec(gen_parquet) == parquet::Compression::SNAPPY);
  }

  SUBCASE("--output-format (CLI flag) beats --set output_format")
  {
    REQUIRE(prec_run(t,
                     body("parquet", "uncompressed"),
                     MainOptions {
                         .output_format = "csv",
                         .set_options = {"output_format=parquet"},
                     })
            == 0);
    CHECK(fs::exists(gen_csv));
    CHECK_FALSE(fs::exists(gen_dir / "generation_sol.parquet"));
  }
}

TEST_CASE(  // NOLINT
    "Config precedence e2e - misspelled option keys are rejected, not ignored")
{
  const cfg::RunTree t {"prec-strict"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const auto prefix = std::format(R"("output_directory": "{}",
      "output_format": "csv", "output_compression": "uncompressed", )",
                                  t.out.generic_string());

  SUBCASE("nested JSON typos fail the parse")
  {
    for (const std::string_view bad :
         {
             R"("lp_matrix_options": {"equilibration_methd": "ruiz"})",
             R"("model_options": {"demand_fail_cots": 1})",
             R"("sddp_options": {"max_iteration": 3})",
             R"("solver_options": {"optimal_epss": 1e-7})",
             R"("monolithic_options": {"solve_mod": "x"})",
             R"("cascade_options": {"level_array":
                  [{"sddp_options": {"max_iteration": 3}}]})",
         })
    {
      INFO(bad);
      CHECK(prec_run(t, std::string {prefix} + std::string {bad}) != 0);
    }
  }

  SUBCASE("invalid enum values fail the parse")
  {
    for (const std::string_view bad :
         {
             R"("lp_matrix_options": {"equilibration_method": "ruizz"})",
             R"("output_format": "parqet")",
             // string-typed model_options enums used to fall back to the
             // default silently; validate_planning now rejects them
             R"("model_options": {"line_losses_mode": "linaer"})",
             R"("model_options": {"kirchhoff_mode": "node_angel"})",
             R"("model_options": {"tiny_reactance_mode": "ite"})",
             R"("model_options": {"objective_mode": "emisions"})",
             R"("cascade_options": {"level_array":
                  [{"model_options": {"line_losses_mode": "linaer"}}]})",
         })
    {
      INFO(bad);
      const auto json = std::format(R"("output_directory": "{}",
          "output_compression": "uncompressed", {})",
                                    t.out.generic_string(),
                                    bad);
      CHECK(prec_run(t, json) != 0);
    }
  }

  SUBCASE("nested --set typos are rejected")
  {
    for (const std::string_view bad :
         {
             "lp_matrix_options.equilibration_methd=ruiz",
             "solver_options.optimal_epss=1e-7",
             "sddp_options.max_iteration=3",
             "cascade_options.level_array.0.sddp_options.max_iteration=3",
         })
    {
      INFO(bad);
      CHECK(prec_run(t,
                     std::string {prefix} + R"("model_options": {})",
                     MainOptions {.set_options = {std::string {bad}}})
            != 0);
    }
  }
}

TEST_CASE(  // NOLINT
    "Config precedence e2e - JSON lp_debug / lp_error are effective")
{
  const cfg::RunTree t {"prec-lpdebug"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const auto logs = t.out / "logs";
  const auto lp_dumps = [&]
  {
    std::size_t n = 0;
    std::error_code ec;
    for (const auto& e : fs::directory_iterator(logs, ec)) {
      n += e.path().filename().string().contains(".lp") ? 1U : 0U;
    }
    return n;
  };

  SUBCASE("JSON lp_debug=true dumps the LP, with names")
  {
    REQUIRE(prec_run(t,
                     std::format(R"("output_directory": "{}",
                    "log_directory": "{}", "lp_debug": true,
                    "lp_compression": "uncompressed",
                    "output_format": "csv",
                    "output_compression": "uncompressed")",
                                 t.out.generic_string(),
                                 logs.generic_string()))
            == 0);
    REQUIRE(lp_dumps() > 0);
    // Regression: names were enabled only by the CLI flags, so a
    // JSON-only lp_debug dumped an LP with generic column names.
    std::string text;
    std::error_code ec;
    for (const auto& e : fs::directory_iterator(logs, ec)) {
      if (e.path().extension() == ".lp") {
        text += cfg::slurp(e.path());
      }
    }
    INFO("LP dump:\n" << text.substr(0, 2000));
    CHECK(text.contains("generation"));
  }

  SUBCASE("--lp-error alone reaches the run (post-solve planning.json)")
  {
    REQUIRE(prec_run(t,
                     std::format(R"("output_directory": "{}",
                    "output_format": "csv",
                    "output_compression": "uncompressed")",
                                 t.out.generic_string()),
                     MainOptions {.lp_error = true})
            == 0);
    CHECK(cfg::read_planning(t.out / "planning.json")
              .options.lp_error.value_or(false));
  }
}

TEST_CASE(  // NOLINT
    "Config precedence e2e - --solver is kept by rebuilt cascade levels")
{
  if (!prec_has_solver("clp") || !prec_has_solver("highs")) {
    MESSAGE("needs both clp and highs plugins - skipping");
    return;
  }
  // Regression: a cascade level with model-option overrides rebuilds its
  // PlanningLP with default LpMatrixOptions and used to fall back to the
  // process-default solver, ignoring `--solver`.  Observable: every
  // per-solve log of every level is named after the backend that ran it.
  const cfg::RunTree t {"prec-cascade"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedEnv default_solver {"GTOPT_SOLVER", "highs"};
  const auto logs = t.out / "logs";

  auto planning = make_3phase_2bus_hydro_planning();
  auto& o = planning.options;
  o.output_directory = t.out.string();
  o.log_directory = logs.string();
  o.output_format = DataFormat::csv;
  o.output_compression = CompressionCodec::uncompressed;
  o.method = MethodType::cascade;
  o.solver_options.log_mode = SolverLogMode::detailed;
  o.solver_options.log_level = 1;
  o.cascade_options.level_array = {
      CascadeLevel {
          .name = OptName {"copper"},
          .model_options = ModelOptions {.use_single_bus = OptBool {true}},
          .sddp_options = CascadeLevelMethod {.max_iterations = OptInt {2}},
      },
      CascadeLevel {
          .name = OptName {"network"},
          .model_options = ModelOptions {.use_single_bus = OptBool {false}},
          .sddp_options = CascadeLevelMethod {.max_iterations = OptInt {2}},
      },
  };
  const auto json = t.case_dir / "case.json";
  REQUIRE(write_json_output(planning, json.string()).has_value());

  const auto r = gtopt_main(MainOptions {
      .planning_files = {json.string()},
      .async_logger = false,
      .solver = "clp",
  });
  REQUIRE(r.has_value());

  std::vector<std::string> wrong;
  std::size_t n_logs = 0;
  std::error_code ec;
  for (const auto& e : fs::recursive_directory_iterator(logs, ec)) {
    const auto name = e.path().filename().string();
    if (name.starts_with("clp_")) {
      ++n_logs;
    } else if (name.starts_with("highs_")) {
      wrong.push_back(name);
    }
  }
  INFO("logs named after the default solver: " << wrong.size());
  CHECK(n_logs > 0);
  CHECK(wrong.empty());
}
