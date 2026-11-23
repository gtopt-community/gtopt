// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_config_canary_files.cpp
 * @brief     Canary tests: every configuration FILE / ENV source gtopt reads
 *            is discovered where documented and its value is effective
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Bug class guarded against: a configuration source that is parsed (or
 * merely searched for) but whose value never reaches the run, so gtopt
 * silently runs with defaults.  Each test writes a file with a
 * distinctive, non-default canary value into a directory tree laid out
 * like a real run (case dir, a separate cwd, fake HOME, env-override
 * dir), then asserts BOTH that the resolver found the file at the
 * expected place AND that the canary is the effective value afterwards.
 * Negative canaries put the same file where gtopt must NOT look.
 *
 * Sources covered here:
 *   - `.gtopt.conf` ([gtopt] + [solver.<name>]): $GTOPT_CONFIG > ./ > ~/
 *   - naming / unit dialect override files (env only; cwd/HOME ignored)
 *   - solver selection: --solver / config `solver` / $GTOPT_SOLVER
 *   - solver plugin search path ($GTOPT_PLUGIN_DIR first, exe-relative)
 * (Solver parameter files: test_config_solver_param_file.cpp.)
 */

#include <cstdlib>
#include <filesystem>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/config_file.hpp>
#include <gtopt/gtopt_cli.hpp>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/names_registry.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/unit_registry.hpp>
#include <spdlog/spdlog.h>
#include <unistd.h>

#include "config_test_helpers.hpp"
#include "log_capture.hpp"

using namespace gtopt;
namespace cfg = gtopt::test::config;

namespace config_canary_files_test
{
namespace
{

namespace fs = std::filesystem;

/// `.gtopt.conf` whose canary is `sddp-max-iterations = <iters>`.
[[nodiscard]] std::string canary_conf_with(int iters,
                                           std::string_view extra = {})
{
  return std::format("[gtopt]\nsddp-max-iterations = {}\n{}\n", iters, extra);
}

/// Run the full CLI entry point (config-file merge included) quietly.
int canary_run_cli(const std::vector<std::string>& args)
{
  const auto saved = spdlog::get_level();
  std::ostringstream out;
  std::ostringstream err;
  const int code = gtopt_cli_main(args, out, err, /*stdout_is_tty=*/true);
  spdlog::set_level(saved);
  return code;
}

[[nodiscard]] bool canary_has_solver(std::string_view name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver(name);
}

}  // namespace
}  // namespace config_canary_files_test

using namespace config_canary_files_test;

// ── .gtopt.conf: resolver search order ───────────────────────────────────

TEST_CASE(  // NOLINT
    ".gtopt.conf canary - search order env > cwd > HOME, case dir ignored")
{
  const cfg::RunTree t {"conf-order"};
  cfg::write_text(t.envdir / "my.conf", canary_conf_with(101));
  cfg::write_text(t.cwd / ".gtopt.conf", canary_conf_with(202));
  cfg::write_text(t.home / ".gtopt.conf", canary_conf_with(303));
  // Negative canaries: neither the case dir nor an unrelated dir is on
  // the search path.
  cfg::write_text(t.case_dir / ".gtopt.conf", canary_conf_with(404));
  cfg::write_text(t.elsewhere / ".gtopt.conf", canary_conf_with(505));

  const cfg::ScopedEnv home {"HOME", t.home.string()};

  SUBCASE("$GTOPT_CONFIG wins over ./ and ~/")
  {
    const cfg::ScopedEnv env {"GTOPT_CONFIG", (t.envdir / "my.conf").string()};
    const cfg::ScopedCwd cwd {t.cwd};
    const auto found = find_config_file();
    CHECK(fs::equivalent(found, t.envdir / "my.conf"));
    CHECK(load_gtopt_config().sddp_max_iterations.value_or(-1) == 101);
  }

  SUBCASE("./.gtopt.conf wins over ~/ when no env override")
  {
    const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
    const cfg::ScopedCwd cwd {t.cwd};
    const auto found = find_config_file();
    REQUIRE_FALSE(found.empty());
    CHECK(fs::equivalent(found, t.cwd / ".gtopt.conf"));
    CHECK(load_gtopt_config().sddp_max_iterations.value_or(-1) == 202);
  }

  SUBCASE("~/.gtopt.conf is the last resort")
  {
    const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
    const cfg::ScopedCwd cwd {t.out};  // a cwd with no .gtopt.conf
    const auto found = find_config_file();
    REQUIRE_FALSE(found.empty());
    CHECK(fs::equivalent(found, t.home / ".gtopt.conf"));
    CHECK(load_gtopt_config().sddp_max_iterations.value_or(-1) == 303);
  }

  SUBCASE("the case directory is NOT searched")
  {
    const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
    const cfg::ScopedEnv no_home {"HOME", t.out.string()};
    const cfg::ScopedCwd cwd {t.out};
    CHECK(find_config_file().empty());
    CHECK_FALSE(load_gtopt_config().sddp_max_iterations.has_value());
  }

  SUBCASE("a missing $GTOPT_CONFIG warns and falls back")
  {
    const cfg::ScopedEnv env {"GTOPT_CONFIG",
                              (t.envdir / "nope.conf").string()};
    const cfg::ScopedCwd cwd {t.cwd};
    const gtopt::test::LogCapture logs;
    const auto found = find_config_file();
    CHECK(logs.contains("GTOPT_CONFIG"));
    CHECK(logs.contains("nope.conf"));
    REQUIRE_FALSE(found.empty());
    CHECK(fs::equivalent(found, t.cwd / ".gtopt.conf"));
  }
}

TEST_CASE(  // NOLINT
    ".gtopt.conf canary - unknown keys and bad numbers warn, file+section+key")
{
  // Regression: a misspelled key or an unparsable number was dropped
  // without any diagnostic.  Each now gets one warning line naming the
  // file, the section and the key; the value is still ignored.  Sections
  // that belong to the Python tools are not checked.
  const cfg::RunTree t {"conf-unknown"};
  const auto conf = t.envdir / "typo.conf";
  cfg::write_text(conf,
                  "[global]\n"
                  "python-only-key = 1\n"  // not ours: no warning
                  "[run_gtopt]\n"
                  "whatever = x\n"  // not ours: no warning
                  "[gtopt]\n"
                  "sddp-max-iteration = 7\n"  // typo: missing trailing 's'
                  "threads = four\n"  // not an integer
                  "sddp-convergence-tol = 1e-3x\n"  // trailing garbage
                  "lp-debug = maybe\n"  // not a boolean
                  "solver = highs\n"
                  "[solver.clp]\n"
                  "optimal-epss = 1e-7\n"  // typo
                  "threads = 2.5\n");  // not an integer
  const gtopt::test::LogCapture logs;
  const auto o = load_gtopt_config_from(conf);
  CHECK_FALSE(o.sddp_max_iterations.has_value());
  CHECK_FALSE(o.threads.has_value());
  CHECK_FALSE(o.sddp_convergence_tol.has_value());
  CHECK_FALSE(o.lp_debug.has_value());
  CHECK(o.solver.value_or("") == "highs");  // a valid key next to them

  const auto file = conf.string();
  CHECK(logs.contains(
      std::format("{}: [gtopt] unknown key 'sddp-max-iteration'", file)));
  CHECK(logs.contains(std::format("{}: [gtopt] threads = 'four'", file)));
  CHECK(logs.contains(
      std::format("{}: [gtopt] sddp-convergence-tol = '1e-3x'", file)));
  CHECK(logs.contains(std::format("{}: [gtopt] lp-debug = 'maybe'", file)));
  CHECK(logs.contains(
      std::format("{}: [solver.clp] unknown key 'optimal-epss'", file)));
  CHECK(logs.contains(std::format("{}: [solver.clp] threads = '2.5'", file)));
  CHECK_FALSE(logs.contains("python-only-key"));
  CHECK_FALSE(logs.contains("whatever"));
  CHECK_FALSE(logs.contains("'solver'"));
}

// ── .gtopt.conf: effective end-to-end through the CLI entry point ───────

TEST_CASE(  // NOLINT
    ".gtopt.conf canary - [gtopt] values reach the run via gtopt_cli_main")
{
  const cfg::RunTree t {"conf-e2e"};
  const auto json = t.case_dir / "case.json";
  cfg::write_text(json,
                  cfg::case_json(std::format(R"("output_directory": "{}",
                 "output_compression": "uncompressed")",
                                             t.out.generic_string())));
  // Canary: output-format = csv (default is parquet) and a distinctive
  // sddp max-iterations, both visible in the pre-solve snapshot.
  cfg::write_text(t.cwd / ".gtopt.conf",
                  canary_conf_with(4242, "output-format = csv\n"));

  const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedEnv home {"HOME", t.home.string()};
  const cfg::ScopedCwd cwd {t.cwd};

  SUBCASE("config value is effective (snapshot + written files)")
  {
    REQUIRE(canary_run_cli({json.string(), "--quiet", "--no-async-logger"})
            == 0);
    const auto state = cfg::read_planning(t.out / "gtopt_state.json");
    CHECK(state.options.sddp_options.max_iterations.value_or(-1) == 4242);
    CHECK(state.options.output_format.value_or(DataFormat::parquet)
          == DataFormat::csv);
    CHECK(fs::exists(t.out / "Generator" / "generation_sol_s0_p0.csv"));
    CHECK_FALSE(fs::exists(t.out / "Generator" / "generation_sol.parquet"));
  }

  SUBCASE("an explicit CLI flag beats the config file")
  {
    REQUIRE(canary_run_cli({
                json.string(),
                "--quiet",
                "--no-async-logger",
                "--sddp-max-iterations",
                "7",
            })
            == 0);
    const auto state = cfg::read_planning(t.out / "gtopt_state.json");
    CHECK(state.options.sddp_options.max_iterations.value_or(-1) == 7);
  }

  SUBCASE("the planning JSON beats the config file")
  {
    // Precedence: CLI flag > --set > JSON > .gtopt.conf > default.  The
    // JSON sets max_iterations (11) but not output_format, so the config
    // file still supplies the latter.
    cfg::write_text(json,
                    cfg::case_json(std::format(R"("output_directory": "{}",
                   "output_compression": "uncompressed",
                   "sddp_options": {{"max_iterations": 11}})",
                                               t.out.generic_string())));
    REQUIRE(canary_run_cli({json.string(), "--quiet", "--no-async-logger"})
            == 0);
    const auto state = cfg::read_planning(t.out / "gtopt_state.json");
    CHECK(state.options.sddp_options.max_iterations.value_or(-1) == 11);
    CHECK(state.options.output_format.value_or(DataFormat::parquet)
          == DataFormat::csv);
  }
}

TEST_CASE(  // NOLINT
    ".gtopt.conf canary - explicit --set beats a config-file default")
{
  // Regression: the config file used to be merged into MainOptions as if
  // its values were CLI flags, and CLI flags are applied AFTER `--set`, so
  // a `.gtopt.conf` default silently overrode an explicit `--set`.
  // Precedence is CLI flag > --set > JSON > config file > default.
  const cfg::RunTree t {"conf-vs-set"};
  const auto json = t.case_dir / "case.json";
  cfg::write_text(json,
                  cfg::case_json(std::format(R"("output_directory": "{}",
                 "output_compression": "uncompressed")",
                                             t.out.generic_string())));
  cfg::write_text(t.cwd / ".gtopt.conf", canary_conf_with(4242));
  const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedEnv home {"HOME", t.home.string()};
  const cfg::ScopedCwd cwd {t.cwd};

  REQUIRE(canary_run_cli({
              json.string(),
              "--quiet",
              "--no-async-logger",
              "--set",
              "sddp_options.max_iterations=9",
          })
          == 0);
  const auto state = cfg::read_planning(t.out / "gtopt_state.json");
  CHECK(state.options.sddp_options.max_iterations.value_or(-1) == 9);
}

TEST_CASE(  // NOLINT
    ".gtopt.conf canary - [solver.<name>] section applies to the solver used")
{
  if (!canary_has_solver("clp")) {
    MESSAGE("clp plugin not available - skipping");
    return;
  }
  const cfg::RunTree t {"conf-solver-sec"};
  const auto json = t.case_dir / "case.json";
  const auto write_case = [&](std::string_view solver_opts)
  {
    cfg::write_text(json,
                    cfg::case_json(std::format(R"("output_directory": "{}",
                   "output_compression": "uncompressed",
                   "output_format": "csv"{})",
                                               t.out.generic_string(),
                                               solver_opts)));
  };
  write_case("");
  // Canary: a distinctive optimality tolerance only in the clp section;
  // a different one in an unrelated solver's section.
  cfg::write_text(t.cwd / ".gtopt.conf",
                  "[gtopt]\n"
                  "[solver.clp]\n"
                  "optimal-eps = 1.2345e-07\n"
                  "[solver.nosuchsolver]\n"
                  "optimal-eps = 9.9e-05\n");
  const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedEnv home {"HOME", t.home.string()};
  const cfg::ScopedCwd cwd {t.cwd};

  SUBCASE("explicit --solver picks its section")
  {
    REQUIRE(canary_run_cli({json.string(), "--quiet", "--solver", "clp"}) == 0);
    const auto p = cfg::read_planning(t.out / "planning.json");
    CHECK(p.options.solver_options.optimal_eps.value_or(0.0)
          == doctest::Approx(1.2345e-7));
  }

  SUBCASE("default solver from $GTOPT_SOLVER also picks its section")
  {
    // Regression: the section used to be applied only with --solver.
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "clp"};
    REQUIRE(canary_run_cli({json.string(), "--quiet"}) == 0);
    const auto p = cfg::read_planning(t.out / "planning.json");
    CHECK(p.options.solver_options.optimal_eps.value_or(0.0)
          == doctest::Approx(1.2345e-7));
  }

  SUBCASE("planning JSON solver_options beat the config section")
  {
    write_case(R"(, "solver_options": {"optimal_eps": 2.5e-8})");
    REQUIRE(canary_run_cli({json.string(), "--quiet", "--solver", "clp"}) == 0);
    const auto p = cfg::read_planning(t.out / "planning.json");
    CHECK(p.options.solver_options.optimal_eps.value_or(0.0)
          == doctest::Approx(2.5e-8));
  }
}

// ── Naming / unit dialect override files ────────────────────────────────

TEST_CASE(  // NOLINT
    "Dialect files canary - only the env override is read, never cwd/HOME")
{
  // Canary dictionary: maps a made-up alias `canary_pmax_xyz` to pmax.
  constexpr std::string_view canary_names = R"json({
  "version": 1,
  "aliases": [
    {"class": "generator", "canonical": "pmax", "alt": "canary_pmax_xyz",
     "dialect": "gtopt"}
  ]
})json";
  const cfg::RunTree t {"dialects"};
  // Negative canaries in every "natural" place: cwd, case dir, HOME share.
  for (const auto& d :
       {
           t.cwd,
           t.case_dir,
           t.home / ".local" / "share" / "gtopt",
           t.home / ".config" / "gtopt",
       })
  {
    cfg::write_text(d / "naming_dialects.json", canary_names);
  }
  cfg::write_text(t.envdir / "naming_dialects.json", canary_names);
  const cfg::ScopedEnv home {"HOME", t.home.string()};
  const cfg::ScopedCwd cwd {t.cwd};

  SUBCASE("without the env var nothing on disk is read")
  {
    const cfg::ScopedEnv env {"GTOPT_NAMING_DIALECTS_FILE", std::nullopt};
    CHECK_FALSE(find_names_file().has_value());
    const auto r = load_names_registry();
    CHECK_FALSE(r.source_path().has_value());
    CHECK_FALSE(r.canonical_for("canary_pmax_xyz").has_value());
  }

  SUBCASE("the env override is discovered and its canary is effective")
  {
    const auto file = t.envdir / "naming_dialects.json";
    const cfg::ScopedEnv env {"GTOPT_NAMING_DIALECTS_FILE", file.string()};
    CHECK(find_names_file().value_or(fs::path {}) == file);
    const auto r = load_names_registry();
    CHECK(r.source_path().value_or(fs::path {}) == file);
    CHECK(r.canonical_for("canary_pmax_xyz").value_or("") == "pmax");
  }

  SUBCASE("a missing env override warns (not silent)")
  {
    const cfg::ScopedEnv env {"GTOPT_NAMING_DIALECTS_FILE",
                              (t.envdir / "missing.json").string()};
    const gtopt::test::LogCapture logs;
    CHECK_FALSE(find_names_file().has_value());
    CHECK(logs.contains("GTOPT_NAMING_DIALECTS_FILE"));
  }

  SUBCASE("unit dialects: cwd copy ignored, missing override warns")
  {
    cfg::write_text(t.cwd / "unit_dialects.json",
                    R"({"version": 1, "units": []})");
    {
      const cfg::ScopedEnv env {"GTOPT_UNIT_DIALECTS_FILE", std::nullopt};
      CHECK_FALSE(find_units_file().has_value());
    }
    const cfg::ScopedEnv env {"GTOPT_UNIT_DIALECTS_FILE",
                              (t.envdir / "missing_units.json").string()};
    const gtopt::test::LogCapture logs;
    CHECK_FALSE(find_units_file().has_value());
    CHECK(logs.contains("GTOPT_UNIT_DIALECTS_FILE"));
  }
}

// ── Solver selection: --solver / config `solver` / $GTOPT_SOLVER ─────────

TEST_CASE(  // NOLINT
    "Solver selection canary - --solver > config solver > GTOPT_SOLVER")
{
  if (!canary_has_solver("clp") || !canary_has_solver("highs")) {
    MESSAGE("needs both clp and highs plugins - skipping");
    return;
  }
  // (JSON `solver_options.log_mode` takes the name or the code 1.)
  // Observable: with solver_options.log_mode = detailed the per-cell
  // solver log is named `<solver>_sc0_ph0.log`, i.e. the backend that
  // really solved the LP.
  const cfg::RunTree t {"solver-sel"};
  const auto logs = t.out / "logs";
  const auto json = t.case_dir / "case.json";
  cfg::write_text(json,
                  cfg::case_json(std::format(R"("output_directory": "{}",
                 "log_directory": "{}",
                 "output_compression": "uncompressed",
                 "output_format": "csv",
                 "solver_options": {{"log_mode": 1,
                                     "log_level": 1}})",
                                             t.out.generic_string(),
                                             logs.generic_string())));
  const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedEnv home {"HOME", t.home.string()};
  const cfg::ScopedCwd cwd {t.cwd};
  const auto used = [&](std::string_view name)
  { return fs::exists(logs / std::format("{}_sc0_ph0.log", name)); };

  SUBCASE("unit: default_solver() follows $GTOPT_SOLVER")
  {
    for (const auto* name : {"clp", "highs"}) {
      const cfg::ScopedEnv solver {"GTOPT_SOLVER", std::string {name}};
      CHECK(SolverRegistry::instance().default_solver() == name);
      const LinearInterface li {""};
      CHECK(li.solver_name() == name);
    }
  }

  SUBCASE("unit: an unavailable $GTOPT_SOLVER warns and falls back")
  {
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "no_such_solver_xyz"};
    const gtopt::test::LogCapture capture;
    const auto chosen = SolverRegistry::instance().default_solver();
    CHECK(chosen != "no_such_solver_xyz");
    CHECK_FALSE(chosen.empty());
  }

  SUBCASE("$GTOPT_SOLVER is effective end-to-end")
  {
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "clp"};
    REQUIRE(canary_run_cli({json.string(), "--quiet"}) == 0);
    CHECK(used("clp"));
    CHECK_FALSE(used("highs"));
  }

  SUBCASE("--solver beats $GTOPT_SOLVER")
  {
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "clp"};
    REQUIRE(canary_run_cli({json.string(), "--quiet", "--solver", "highs"})
            == 0);
    CHECK(used("highs"));
    CHECK_FALSE(used("clp"));
  }

  SUBCASE("config-file `solver` beats $GTOPT_SOLVER")
  {
    // The config value is a default for --solver, so it outranks the env.
    cfg::write_text(t.cwd / ".gtopt.conf", "[gtopt]\nsolver = highs\n");
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "clp"};
    REQUIRE(canary_run_cli({json.string(), "--quiet"}) == 0);
    CHECK(used("highs"));
    CHECK_FALSE(used("clp"));
    fs::remove(t.cwd / ".gtopt.conf");
  }

  SUBCASE("gtopt_state.json records the solver; a JSON pin beats the conf")
  {
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "clp"};
    REQUIRE(canary_run_cli({json.string(), "--quiet"}) == 0);
    CHECK(cfg::read_planning(t.out / "gtopt_state.json")
              .options.lp_matrix_options.solver_name
          == "clp");

    // Replaying a snapshot that pins highs ignores $GTOPT_SOLVER=clp and a
    // config-file `solver = clp` (JSON > .gtopt.conf > env).
    cfg::write_text(t.cwd / ".gtopt.conf", "[gtopt]\nsolver = clp\n");
    cfg::write_text(json,
                    cfg::case_json(std::format(R"("output_directory": "{}",
                   "log_directory": "{}",
                   "output_compression": "uncompressed",
                   "output_format": "csv",
                   "lp_matrix_options": {{"solver_name": "highs"}},
                   "solver_options": {{"log_mode": 1, "log_level": 1}})",
                                               t.out.generic_string(),
                                               logs.generic_string())));
    fs::remove_all(logs);
    REQUIRE(canary_run_cli({json.string(), "--quiet"}) == 0);
    CHECK(used("highs"));
    CHECK_FALSE(used("clp"));
    fs::remove(t.cwd / ".gtopt.conf");
  }
}

TEST_CASE(  // NOLINT
    "Solver param file canary - gtopt_state.json records the resolved file")
{
  if (!canary_has_solver("clp")) {
    MESSAGE("clp plugin not available - skipping");
    return;
  }
  // A case `solvers/clp.prm` is auto-discovered for --solver clp; the
  // pre-solve snapshot must name the solver and the resolved file, and,
  // since clp does not read parameter files, the run must say so.
  const cfg::RunTree t {"state-prm"};
  const auto json = t.case_dir / "case.json";
  const auto prm = t.case_dir / "solvers" / "clp.prm";
  cfg::write_text(prm, "canary 777\n");
  cfg::write_text(json,
                  cfg::case_json(std::format(R"("output_directory": "{}",
                 "input_directory": "{}",
                 "output_compression": "uncompressed",
                 "output_format": "csv")",
                                             t.out.generic_string(),
                                             t.case_dir.generic_string())));
  const cfg::ScopedEnv env {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedEnv home {"HOME", t.home.string()};
  const cfg::ScopedCwd cwd {t.cwd};

  const gtopt::test::LogCapture logs;
  REQUIRE(gtopt_main(MainOptions {
                         .planning_files = {json.string()},
                         .async_logger = false,
                         .solver = "clp",
                     })
              .value_or(-1)
          == 0);
  const auto state = cfg::read_planning(t.out / "gtopt_state.json");
  CHECK(state.options.lp_matrix_options.solver_name == "clp");
  const fs::path recorded =
      state.options.solver_options.param_file.value_or("");
  REQUIRE_FALSE(recorded.empty());
  CHECK(fs::equivalent(recorded, prm));
  CHECK(logs.contains("is ignored by the 'clp' solver"));
}

// ── Solver plugin search path ────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "SolverRegistry - GTOPT_PLUGIN_DIR canary is searched first")
{
  // The registry singleton reads $GTOPT_PLUGIN_DIR once, at construction,
  // so this check needs the variable set when the process starts.  CTest
  // registers `gtoptTests.plugin_dir_env_canary`, which runs exactly this
  // test case with GTOPT_PLUGIN_DIR pointing at a canary directory.
  const char* env = std::getenv("GTOPT_PLUGIN_DIR");
  const auto& dirs = SolverRegistry::instance().searched_directories();
  REQUIRE_FALSE(dirs.empty());

  // Exe-relative build-tree layout is always on the search path.
  std::error_code ec;
  const auto exe = fs::read_symlink("/proc/self/exe", ec);
  if (!ec) {
    const auto expected =
        fs::weakly_canonical(exe.parent_path() / ".." / "plugins", ec);
    CHECK(std::ranges::find(dirs, expected.string()) != dirs.end());
  }

  if (env == nullptr || *env == '\0') {
    MESSAGE(
        "GTOPT_PLUGIN_DIR not set - env part runs via CTest "
        "gtoptTests.plugin_dir_env_canary");
    return;
  }
  CHECK(dirs.front() == fs::weakly_canonical(fs::path {env}, ec).string());
}
