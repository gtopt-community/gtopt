/**
 * @file      test_gtopt_config_load.cpp
 * @brief     Unit tests for load_gtopt_config_from() (.gtopt.conf reader)
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Covers every `[gtopt]` key, the value-type helpers (bool spellings,
 * malformed numbers), the deprecated-key fallbacks and the per-solver
 * `[solver.<name>]` sections, via the explicit-path entry point (no
 * GTOPT_CONFIG / HOME / CWD lookup involved).
 */

#include <filesystem>
#include <fstream>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/main_options.hpp>

#include "config_test_helpers.hpp"

using namespace gtopt;

namespace
{

std::filesystem::path write_conf(const std::string& name,
                                 const std::string& body)
{
  const auto path = std::filesystem::temp_directory_path()
      / ("gtopt_config_load_" + name + ".conf");
  std::ofstream(path) << body;
  return path;
}

}  // namespace

TEST_CASE("load_gtopt_config_from - empty path and missing section")  // NOLINT
{
  SUBCASE("empty path yields default options")
  {
    const auto opts = load_gtopt_config_from({});
    CHECK_FALSE(opts.solver.has_value());
    CHECK(opts.solver_configs.empty());
  }

  SUBCASE("file without a [gtopt] section yields default options")
  {
    const auto path = write_conf("nosection", "[other]\nsolver = highs\n");
    const auto opts = load_gtopt_config_from(path);
    CHECK_FALSE(opts.solver.has_value());
    CHECK(opts.solver_configs.empty());
    std::filesystem::remove(path);
  }
}

TEST_CASE("load_gtopt_config_from - every [gtopt] key")  // NOLINT
{
  const auto path = write_conf("allkeys",
                               "[gtopt]\n"
                               "input-directory = in\n"
                               "input-format = csv\n"
                               "output-directory = out\n"
                               "output-format = parquet\n"
                               "output-compression = zstd\n"
                               "use-single-bus = yes\n"
                               "use-kirchhoff = no\n"
                               "lp-file = model\n"
                               "lp-format = mps\n"
                               "matrix-eps = 1e-9\n"
                               "lp-only = 1\n"
                               "lp-scales = scales.csv\n"
                               "lp-debug = true\n"
                               "lp-error = 0\n"
                               "lp-compression = zstd\n"
                               "lp-coeff-ratio = 1e7\n"
                               "json-file = merged.json\n"
                               "stats = true\n"
                               "trace-log = trace.log\n"
                               "async-logger = false\n"
                               "lp-dump-backward = 3\n"
                               "fcut-log = true\n"
                               "cut-directory = cuts\n"
                               "log-directory = logs\n"
                               "sddp-max-iterations = 40\n"
                               "sddp-min-iterations = 2\n"
                               "sddp-convergence-tol = 0.001\n"
                               "sddp-elastic-penalty = 1e6\n"
                               "sddp-elastic-mode = single_cut\n"
                               "constraint-mode = strict\n"
                               "sddp-num-apertures = 5\n"
                               "aperture-chunk-size = auto\n"
                               "memory-saving = compress\n"
                               "memory-limit = 8G\n"
                               "memory-quota = 0.5\n"
                               "swap-limit = 1G\n"
                               "swap-growth = 100M\n"
                               "cpu-factor = 1.5\n"
                               "cpu-quota = 0.75\n"
                               "build-mode = direct\n"
                               "write-out = all\n"
                               "solver = highs\n"
                               "algorithm = barrier\n"
                               "threads = 6\n");

  const auto o = load_gtopt_config_from(path);

  CHECK(o.input_directory.value_or("") == "in");
  CHECK(o.input_format.value_or("") == "csv");
  CHECK(o.output_directory.value_or("") == "out");
  CHECK(o.output_format.value_or("") == "parquet");
  CHECK(o.output_compression.value_or("") == "zstd");
  CHECK(o.use_single_bus.value_or(false) == true);
  CHECK(o.use_kirchhoff.value_or(true) == false);
  CHECK(o.lp_file.value_or("") == "model");
  CHECK(o.lp_format.value_or("") == "mps");
  CHECK(o.matrix_eps.value_or(0.0) == doctest::Approx(1e-9));
  CHECK(o.lp_only.value_or(false) == true);
  CHECK(o.lp_scales.value_or("") == "scales.csv");
  CHECK(o.lp_debug.value_or(false) == true);
  CHECK(o.lp_error.value_or(true) == false);
  CHECK(o.lp_compression.value_or("") == "zstd");
  CHECK(o.lp_coeff_ratio_threshold.value_or(0.0) == doctest::Approx(1e7));
  CHECK(o.json_file.value_or("") == "merged.json");
  CHECK(o.print_stats.value_or(false) == true);
  CHECK(o.trace_log.value_or("") == "trace.log");
  CHECK(o.async_logger.value_or(true) == false);
  CHECK(o.lp_dump_backward.value_or("") == "3");
  CHECK(o.fcut_log.value_or(false) == true);
  CHECK(o.cut_directory.value_or("") == "cuts");
  CHECK(o.log_directory.value_or("") == "logs");
  CHECK(o.sddp_max_iterations.value_or(0) == 40);
  CHECK(o.sddp_min_iterations.value_or(0) == 2);
  CHECK(o.sddp_convergence_tol.value_or(0.0) == doctest::Approx(0.001));
  CHECK(o.sddp_elastic_penalty.value_or(0.0) == doctest::Approx(1e6));
  CHECK(o.sddp_elastic_mode.value_or("") == "single_cut");
  CHECK(o.constraint_mode.value_or("") == "strict");
  CHECK(o.sddp_num_apertures.value_or(0) == 5);
  CHECK(o.sddp_aperture_chunk_size.value_or(-7) == 0);  // "auto" -> 0
  CHECK(o.memory_saving.value_or("") == "compress");
  CHECK(o.memory_limit.value_or("") == "8G");
  CHECK(o.memory_quota.value_or(0.0) == doctest::Approx(0.5));
  CHECK(o.swap_limit.value_or("") == "1G");
  CHECK(o.swap_growth.value_or("") == "100M");
  CHECK(o.sddp_cpu_factor.value_or(0.0) == doctest::Approx(1.5));
  CHECK(o.cpu_quota.value_or(0.0) == doctest::Approx(0.75));
  CHECK(o.build_mode.value_or("") == "direct");
  CHECK(o.write_out.value_or("") == "all");
  CHECK(o.solver.value_or("") == "highs");
  CHECK(o.algorithm.value_or(LPAlgo::default_algo) == LPAlgo::barrier);
  CHECK(o.threads.value_or(0) == 6);

  std::filesystem::remove(path);
}

TEST_CASE("load_gtopt_config_from - value parsing edge cases")  // NOLINT
{
  const auto path = write_conf("edge",
                               "[gtopt]\n"
                               "solver =\n"  // empty string -> unset
                               "use-single-bus = maybe\n"  // invalid bool
                               "threads = many\n"  // invalid int
                               "matrix-eps = tiny\n"  // invalid double
                               "algorithm = no_such_algo\n"  // swallowed
                               "aperture-chunk-size = 7x\n"  // strict parse
                               "low-memory = rebuild\n"  // deprecated alias
                               "sddp-cpu-factor = 2.5\n");  // fallback key

  const auto o = load_gtopt_config_from(path);

  CHECK_FALSE(o.solver.has_value());
  CHECK_FALSE(o.use_single_bus.has_value());
  CHECK_FALSE(o.threads.has_value());
  CHECK_FALSE(o.matrix_eps.has_value());
  CHECK_FALSE(o.algorithm.has_value());
  CHECK_FALSE(o.sddp_aperture_chunk_size.has_value());
  CHECK(o.memory_saving.value_or("") == "rebuild");
  CHECK(o.sddp_cpu_factor.value_or(0.0) == doctest::Approx(2.5));

  std::filesystem::remove(path);
}

TEST_CASE("load_gtopt_config_from - preferred keys beat deprecated")  // NOLINT
{
  const auto path = write_conf("prefer",
                               "[gtopt]\n"
                               "memory-saving = off\n"
                               "low-memory = rebuild\n"
                               "cpu-factor = 1.25\n"
                               "sddp-cpu-factor = 3.0\n"
                               "aperture-chunk-size = -1\n");

  const auto o = load_gtopt_config_from(path);
  CHECK(o.memory_saving.value_or("") == "off");
  CHECK(o.sddp_cpu_factor.value_or(0.0) == doctest::Approx(1.25));
  CHECK(o.sddp_aperture_chunk_size.value_or(0) == -1);

  std::filesystem::remove(path);
}

TEST_CASE("load_gtopt_config_from - [solver.<name>] sections")  // NOLINT
{
  const auto path = write_conf("solvers",
                               "[gtopt]\n"
                               "solver = cplex\n"
                               "[solver.cplex]\n"
                               "algorithm = dual\n"
                               "threads = 4\n"
                               "presolve = no\n"
                               "optimal-eps = 1e-8\n"
                               "feasible-eps = 1e-7\n"
                               "barrier-eps = 1e-6\n"
                               "log-level = 2\n"
                               "time-limit = 30\n"
                               "scaling = aggressive\n"
                               "max-fallbacks = 0\n"
                               "[solver.highs]\n"
                               "algorithm = bogus\n"  // swallowed
                               "threads = x\n"  // invalid int ignored
                               "presolve = perhaps\n"  // invalid bool
                               "optimal-eps = y\n"  // invalid double
                               "[solver.]\n"  // empty name skipped
                               "threads = 9\n"
                               "[unrelated]\n"
                               "threads = 9\n");

  const auto o = load_gtopt_config_from(path);

  REQUIRE(o.solver_configs.size() == 2);
  REQUIRE(o.solver_configs.contains("cplex"));
  REQUIRE(o.solver_configs.contains("highs"));

  const auto& cplex = o.solver_configs.at("cplex");
  CHECK(cplex.algorithm == LPAlgo::dual);
  CHECK(cplex.threads == 4);
  CHECK(cplex.presolve == false);
  CHECK(cplex.optimal_eps.value_or(0.0) == doctest::Approx(1e-8));
  CHECK(cplex.feasible_eps.value_or(0.0) == doctest::Approx(1e-7));
  CHECK(cplex.barrier_eps.value_or(0.0) == doctest::Approx(1e-6));
  CHECK(cplex.log_level == 2);
  CHECK(cplex.time_limit.value_or(0.0) == doctest::Approx(30.0));
  CHECK(cplex.scaling.value_or(SolverScaling::none)
        == SolverScaling::aggressive);
  CHECK(cplex.max_fallbacks == 0);

  // Malformed values fall back to the SolverOptions defaults.
  const auto& highs = o.solver_configs.at("highs");
  const SolverOptions defaults {};
  CHECK(highs.algorithm == defaults.algorithm);
  CHECK(highs.threads == defaults.threads);
  CHECK(highs.presolve == defaults.presolve);
  CHECK_FALSE(highs.optimal_eps.has_value());
  CHECK(highs.max_fallbacks == defaults.max_fallbacks);

  std::filesystem::remove(path);
}

TEST_CASE("load_gtopt_config - delegates to the discovered file")  // NOLINT
{
  const auto path = write_conf("env", "[gtopt]\nthreads = 3\n");
  const auto o = [&]
  {
    const test::config::ScopedEnv env {"GTOPT_CONFIG", path.string()};
    return load_gtopt_config();
  }();
  CHECK(o.threads.value_or(0) == 3);
  std::filesystem::remove(path);
}

TEST_CASE("merge_config_defaults - fills every unset field")  // NOLINT
{
  const auto path = write_conf("merge",
                               "[gtopt]\n"
                               "output-format = csv\n"
                               "solver = highs\n"
                               "threads = 6\n"
                               "[solver.highs]\n"
                               "threads = 2\n"
                               "[solver.cplex]\n"
                               "threads = 8\n");
  const auto cfg = load_gtopt_config_from(path);

  MainOptions cli;
  cli.solver = "cplex";
  cli.solver_configs["cplex"] = SolverOptions {.threads = 1};

  merge_config_defaults(cli, cfg);

  CHECK(cli.solver.value_or("") == "cplex");  // CLI wins
  CHECK(cli.output_format.value_or("") == "csv");  // filled from config
  CHECK(cli.threads.value_or(0) == 6);
  // Per-solver configs: existing entry kept, missing one added.
  CHECK(cli.solver_configs.at("cplex").threads == 1);
  CHECK(cli.solver_configs.at("highs").threads == 2);

  std::filesystem::remove(path);
}
