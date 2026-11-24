/**
 * @file      test_config_file.hpp
 * @brief     Unit tests for config_file.hpp INI parser
 * @date      2026-03-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstdlib>
#include <filesystem>
#include <fstream>

#include <doctest/doctest.h>
#include <gtopt/config_file.hpp>
#include <gtopt/main_options.hpp>

using namespace gtopt;

TEST_CASE("parse_ini_file - Basic sections and key-value pairs")
{
  using namespace gtopt;

  const auto tmp =
      std::filesystem::temp_directory_path() / "test_ini_basic.ini";
  {
    std::ofstream f(tmp);
    f << "[global]\n"
      << "ai_enabled = false\n"
      << "\n"
      << "[gtopt]\n"
      << "solver = highs\n"
      << "threads = 4\n"
      << "algorithm = barrier\n"
      << "output-format = parquet\n";
  }

  const auto data = parse_ini_file(tmp);

  REQUIRE(data.contains("global"));
  CHECK(data.at("global").at("ai_enabled") == "false");

  REQUIRE(data.contains("gtopt"));
  CHECK(data.at("gtopt").at("solver") == "highs");
  CHECK(data.at("gtopt").at("threads") == "4");
  CHECK(data.at("gtopt").at("algorithm") == "barrier");
  CHECK(data.at("gtopt").at("output-format") == "parquet");

  std::filesystem::remove(tmp);
}

TEST_CASE("parse_ini_file - Comments and blank lines")
{
  using namespace gtopt;

  const auto tmp =
      std::filesystem::temp_directory_path() / "test_ini_comments.ini";
  {
    std::ofstream f(tmp);
    f << "# This is a comment\n"
      << "; This is also a comment\n"
      << "\n"
      << "[section]\n"
      << "key1 = value1\n"
      << "# another comment\n"
      << "key2 = value2\n";
  }

  const auto data = parse_ini_file(tmp);

  REQUIRE(data.contains("section"));
  CHECK(data.at("section").at("key1") == "value1");
  CHECK(data.at("section").at("key2") == "value2");
  CHECK(data.at("section").size() == 2);

  std::filesystem::remove(tmp);
}

TEST_CASE("parse_ini_file - Whitespace trimming")
{
  using namespace gtopt;

  const auto tmp = std::filesystem::temp_directory_path() / "test_ini_trim.ini";
  {
    std::ofstream f(tmp);
    f << "[  section  ]\n"
      << "  key1  =  value with spaces  \n"
      << "key2=no_spaces\n";
  }

  const auto data = parse_ini_file(tmp);

  REQUIRE(data.contains("section"));
  CHECK(data.at("section").at("key1") == "value with spaces");
  CHECK(data.at("section").at("key2") == "no_spaces");

  std::filesystem::remove(tmp);
}

TEST_CASE("parse_ini_file - Nonexistent file returns empty")
{
  using namespace gtopt;

  const auto data = parse_ini_file("/tmp/nonexistent_gtopt_test_file.ini");
  CHECK(data.empty());
}

TEST_CASE("parse_ini_file - Empty value")
{
  using namespace gtopt;

  const auto tmp =
      std::filesystem::temp_directory_path() / "test_ini_empty.ini";
  {
    std::ofstream f(tmp);
    f << "[gtopt]\n"
      << "key1 = \n"
      << "key2 = value\n";
  }

  const auto data = parse_ini_file(tmp);

  REQUIRE(data.contains("gtopt"));
  CHECK(data.at("gtopt").at("key1").empty());
  CHECK(data.at("gtopt").at("key2") == "value");

  std::filesystem::remove(tmp);
}

TEST_CASE("parse_ini_file - CRLF line endings (Windows-edited config)")
{
  // A .gtopt.conf saved on Windows: every line ends in "\r\n".  Values,
  // section names and keys must not carry the '\r' — in particular an
  // empty value must stay empty (an "\r" value would be passed on as a
  // non-empty option string).
  const auto tmp = std::filesystem::temp_directory_path() / "test_ini_crlf.ini";
  {
    std::ofstream f(tmp, std::ios::binary);
    f << "[gtopt]\r\n"
      << "solver = highs\r\n"
      << "empty =\r\n"
      << "blank = \t\r\n"
      << "\r\n"
      << "# comment\r\n"
      << "[sddp]\r\n"
      << "max_iterations=7\r\n";
  }

  const auto data = parse_ini_file(tmp);

  REQUIRE(data.contains("gtopt"));
  REQUIRE(data.contains("sddp"));
  CHECK(data.size() == 2);
  CHECK(data.at("gtopt").at("solver") == "highs");
  CHECK(data.at("gtopt").at("empty").empty());
  CHECK(data.at("gtopt").at("blank").empty());
  CHECK(data.at("sddp").at("max_iterations") == "7");

  std::filesystem::remove(tmp);
}

TEST_CASE("load_gtopt_config - Reads [gtopt] section via GTOPT_CONFIG")
{
  using namespace gtopt;

  const auto tmp =
      std::filesystem::temp_directory_path() / "test_gtopt_config.conf";
  {
    std::ofstream f(tmp);
    f << "[gtopt]\n"
      << "solver = highs\n"
      << "threads = 8\n"
      << "algorithm = dual\n"
      << "output-format = csv\n"
      << "output-compression = gzip\n"
      << "sddp-max-iterations = 500\n"
      << "sddp-convergence-tol = 1e-6\n"
      << "use-single-bus = true\n"
      << "lp-debug = false\n";
  }

  // Point GTOPT_CONFIG to our test file
  setenv("GTOPT_CONFIG", tmp.c_str(), 1);

  const auto opts = load_gtopt_config();

  REQUIRE(opts.solver.has_value());
  CHECK(opts.solver.value_or("") == "highs");
  REQUIRE(opts.threads.has_value());
  CHECK(opts.threads.value_or(-1) == 8);
  REQUIRE(opts.algorithm.has_value());
  CHECK(opts.algorithm.value_or(LPAlgo::primal) == LPAlgo::dual);
  REQUIRE(opts.output_format.has_value());
  CHECK(opts.output_format.value_or("") == "csv");
  REQUIRE(opts.output_compression.has_value());
  CHECK(opts.output_compression.value_or("") == "gzip");
  REQUIRE(opts.sddp_max_iterations.has_value());
  CHECK(opts.sddp_max_iterations.value_or(-1) == 500);
  REQUIRE(opts.sddp_convergence_tol.has_value());
  CHECK(opts.sddp_convergence_tol.value_or(0.0) == doctest::Approx(1e-6));
  REQUIRE(opts.use_single_bus.has_value());
  CHECK(opts.use_single_bus.value_or(false));
  REQUIRE(opts.lp_debug.has_value());
  CHECK_FALSE(opts.lp_debug.value_or(true));

  unsetenv("GTOPT_CONFIG");
  std::filesystem::remove(tmp);
}

TEST_CASE("merge_config_defaults - CLI overrides config")
{
  using namespace gtopt;

  MainOptions cli_opts;
  cli_opts.solver = "clp";
  cli_opts.threads = 2;

  MainOptions config_opts;
  config_opts.solver = "highs";
  config_opts.threads = 8;
  config_opts.output_format = "csv";
  config_opts.sddp_max_iterations = 200;

  merge_config_defaults(cli_opts, config_opts);

  // CLI values preserved
  CHECK(cli_opts.solver.value_or("") == "clp");
  CHECK(cli_opts.threads.value_or(-1) == 2);

  // Config values fill in missing CLI fields
  REQUIRE(cli_opts.output_format.has_value());
  CHECK(cli_opts.output_format.value_or("") == "csv");
  REQUIRE(cli_opts.sddp_max_iterations.has_value());
  CHECK(cli_opts.sddp_max_iterations.value_or(-1) == 200);

  // Fields absent from both remain nullopt
  CHECK_FALSE(cli_opts.input_directory.has_value());
}

TEST_CASE("merge_config_defaults - Empty config changes nothing")
{
  using namespace gtopt;

  MainOptions cli_opts;
  cli_opts.solver = "clp";

  const MainOptions empty_config;
  merge_config_defaults(cli_opts, empty_config);

  CHECK(*cli_opts.solver == "clp");
  CHECK_FALSE(cli_opts.threads.has_value());
}

TEST_CASE(  // NOLINT
    "split_config_defaults - runtime keys merge, planning keys are layered")
{
  MainOptions cli;
  cli.solver = "clp";

  MainOptions conf;
  conf.solver = "highs";  // layered: CLI keeps its value
  conf.matrix_eps = 1e-11;  // runtime-only: fills the unset CLI field
  conf.sddp_max_iterations = 4242;  // planning-level: layered
  conf.output_format = "csv";  // planning-level: layered
  conf.solver_configs["clp"] = SolverOptions {
      .optimal_eps = 1.5e-7,
  };

  split_config_defaults(cli, conf);

  CHECK(cli.solver.value_or("") == "clp");
  CHECK(cli.matrix_eps.value_or(0.0) == doctest::Approx(1e-11));
  CHECK(cli.solver_configs.contains("clp"));
  // Planning-level keys must NOT become CLI values (they would beat the
  // JSON and --set otherwise).
  CHECK_FALSE(cli.sddp_max_iterations.has_value());
  CHECK_FALSE(cli.output_format.has_value());
  REQUIRE(cli.config_defaults != nullptr);
  CHECK(cli.config_defaults->sddp_max_iterations.value_or(-1) == 4242);
  CHECK(cli.config_defaults->output_format.value_or("") == "csv");
  CHECK(cli.config_defaults->from_config_file);
  // `solver` is layered too: it fills the planning pin
  // lp_matrix_options.solver_name, below a JSON pin.
  CHECK(cli.config_defaults->solver.value_or("") == "highs");
}

TEST_CASE(  // NOLINT
    "apply_config_file_defaults - fills only what the JSON left unset")
{
  MainOptions conf;
  conf.from_config_file = true;
  conf.sddp_max_iterations = 4242;
  conf.sddp_convergence_tol = 0.0123;
  conf.output_format = "csv";
  conf.threads = 6;
  conf.lp_only = true;

  Planning planning;
  planning.options.sddp_options.max_iterations = 11;  // set by the JSON
  planning.options.solver_options.threads = 3;  // set by the JSON

  apply_config_file_defaults(planning, conf);

  const auto& po = planning.options;
  CHECK(po.sddp_options.max_iterations.value_or(-1) == 11);
  CHECK(po.solver_options.threads == 3);
  CHECK(po.sddp_options.convergence_tol.value_or(0.0)
        == doctest::Approx(0.0123));
  CHECK(po.output_format.value_or(DataFormat::parquet) == DataFormat::csv);
  CHECK(po.lp_only.value_or(false));
}

TEST_CASE(  // NOLINT
    "apply_config_file_defaults - relative directories are CWD-relative")
{
  MainOptions conf;
  conf.output_directory = "conf_out";
  Planning planning;
  apply_config_file_defaults(planning, conf);
  const auto out = planning.options.output_directory.value_or("");
  CHECK(std::filesystem::path(out).is_absolute());
  CHECK(std::filesystem::path(out)
        == (std::filesystem::current_path() / "conf_out").lexically_normal());
}
