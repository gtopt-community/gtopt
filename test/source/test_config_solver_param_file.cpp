// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_config_solver_param_file.cpp
 * @brief     Canary tests: solver parameter files (CPLEX `cplex.prm`) are
 *            found where documented and their values reach the solver
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The parameter file is written with canary values gtopt itself never
 * sets (simplex iteration limit 987654, Markowitz tolerance 0.0123).
 * CPLEX echoes every non-default parameter at the top of the solve log,
 * so finding the canary in the per-cell `cplex_sc0_ph0.log` proves the
 * file was read by the backend AND its values were in force for the
 * solve (not merely that loading "did not fail").
 *
 * Sources covered:
 *   - `solver_options.param_file` in the planning JSON (absolute)
 *   - `--set solver_options.param_file=...` (beats the JSON)
 *   - auto-discovery `<input_directory>/solvers/<solver>.prm`, with and
 *     without an explicit `--solver`
 *   - negative: a prm in the case root / in `<cwd>/input/solvers` is not
 *     picked up
 *   - a relative `param_file` (the input_directory copy beats a cwd copy;
 *     the cwd copy is only a fallback)
 *   - a missing file warns
 *   - typed gtopt options override the same parameter from the file
 * All CPLEX tests skip when the CPLEX plugin is not loadable.
 */

#include <filesystem>
#include <format>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

#include "config_test_helpers.hpp"
#include "log_capture.hpp"

using namespace gtopt;
namespace cfg = gtopt::test::config;

namespace config_param_file_test
{
namespace
{

namespace fs = std::filesystem;

[[nodiscard]] bool prm_cplex_available()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver("cplex");
}

/// A CPLEX parameter file carrying a distinctive iteration-limit canary.
[[nodiscard]] std::string prm_with(int iteration_limit,
                                   std::string_view extra = {})
{
  return std::format(
      "CPLEX Parameter File Version 22.1.1\n"
      "CPXPARAM_Simplex_Limits_Iterations {}\n"
      "CPXPARAM_Simplex_Tolerances_Markowitz 0.0123\n"
      "{}",
      iteration_limit,
      extra);
}

/// Value CPLEX echoes for parameter @p name at the top of a solve log
/// (`CPXPARAM_<name>   <value>`), or nullopt when it is not listed
/// (CPLEX lists only non-default parameters).
[[nodiscard]] std::optional<double> log_param(const std::string& log,
                                              std::string_view name)
{
  const auto pos = log.find(name);
  if (pos == std::string::npos) {
    return std::nullopt;
  }
  std::istringstream line {log.substr(pos + name.size(), 64)};
  double v {};
  if (line >> v) {
    return v;
  }
  return std::nullopt;
}

/// True when @p log echoes `CPXPARAM_Simplex_Limits_Iterations <value>`.
[[nodiscard]] bool log_has_iteration_limit(const std::string& log, int value)
{
  return log_param(log, "CPXPARAM_Simplex_Limits_Iterations").value_or(-1.0)
      == static_cast<double>(value);
}

/// Two-column LP: min x + 2y  s.t.  x + y >= 1.
void prm_build_tiny_lp(LinearInterface& lp)
{
  const auto x = lp.add_col(SparseCol {.lowb = 0.0, .uppb = 10.0, .cost = 1.0});
  const auto y = lp.add_col(SparseCol {.lowb = 0.0, .uppb = 10.0, .cost = 2.0});
  SparseRow row;
  row[x] = 1.0;
  row[y] = 1.0;
  row.greater_equal(1.0);
  (void)lp.add_row(row);
}

/// gtopt_main run of the one-bus case with CPLEX per-cell logs.  Returns
/// the text of `cplex_sc0_ph0.log` ("" when absent).
struct PrmE2eRun
{
  int code {-1};
  std::string log;
  fs::path param_file;  ///< param_file recorded in the post-solve JSON
};

[[nodiscard]] PrmE2eRun prm_run_case(const cfg::RunTree& t,
                                     std::string_view extra_solver_options,
                                     MainOptions opts)
{
  const auto logs = t.out / "logs";
  cfg::write_text(t.case_dir / "case.json",
                  cfg::case_json(std::format(R"("output_directory": "{}",
          "log_directory": "{}",
          "output_compression": "uncompressed",
          "output_format": "csv",
          "solver_options": {{"log_mode": 1,
                              "log_level": 1{}}})",
                                             t.out.generic_string(),
                                             logs.generic_string(),
                                             extra_solver_options)));
  opts.planning_files = {(t.case_dir / "case.json").string()};
  opts.async_logger = false;
  std::error_code ec;
  fs::remove_all(logs, ec);
  const auto r = gtopt_main(opts);
  if (!r.has_value()) {
    MESSAGE("gtopt_main error: " << r.error());
  }
  PrmE2eRun run;
  run.code = r.value_or(-1);
  run.log = cfg::slurp(logs / "cplex_sc0_ph0.log");
  if (fs::exists(t.out / "planning.json")) {
    run.param_file = cfg::read_planning(t.out / "planning.json")
                         .options.solver_options.param_file.value_or("");
  }
  return run;
}

}  // namespace
}  // namespace config_param_file_test

using namespace config_param_file_test;

TEST_CASE(  // NOLINT
    "cplex.prm canary - LinearInterface applies the file to the solve")
{
  if (!prm_cplex_available()) {
    MESSAGE("CPLEX plugin not available - skipping");
    return;
  }
  const cfg::RunTree t {"prm-li"};
  const auto prm = t.case_dir / "solvers" / "cplex.prm";
  cfg::write_text(
      prm,
      prm_with(987654, "CPXPARAM_Simplex_Tolerances_Optimality 1.2345e-07\n"));

  int run_no = 0;
  const auto solve = [&](const SolverOptions& base) -> std::string
  {
    const auto logbase = t.out / std::format("li_{}", ++run_no);
    LinearInterface lp {"cplex"};
    prm_build_tiny_lp(lp);
    lp.set_log_file(logbase.string());
    auto opts = base;
    opts.log_mode = SolverLogMode::detailed;
    opts.log_level = 1;
    auto r = lp.initial_solve(opts);
    REQUIRE(r.has_value());
    CHECK(lp.is_optimal());
    CHECK(lp.get_obj_value() == doctest::Approx(1.0));
    return cfg::slurp(logbase.string() + ".log");
  };

  SUBCASE("file values are in force")
  {
    SolverOptions o;
    o.param_file = prm.string();
    const auto log = solve(o);
    INFO("cplex log:\n" << log);
    CHECK(log_has_iteration_limit(log, 987654));
    CHECK(log_param(log, "CPXPARAM_Simplex_Tolerances_Markowitz").value_or(0.0)
          == doctest::Approx(0.0123));
    CHECK(log_param(log, "CPXPARAM_Simplex_Tolerances_Optimality").value_or(0.0)
          == doctest::Approx(1.2345e-7));
  }

  SUBCASE("typed gtopt option overrides the same parameter in the file")
  {
    SolverOptions o;
    o.param_file = prm.string();
    o.optimal_eps = 2.5e-8;
    const auto log = solve(o);
    INFO("cplex log:\n" << log);
    CHECK(log_has_iteration_limit(log, 987654));  // file still read
    CHECK(log_param(log, "CPXPARAM_Simplex_Tolerances_Optimality").value_or(0.0)
          == doctest::Approx(2.5e-8));
  }

  SUBCASE("negative: no param_file -> no canary")
  {
    const auto log = solve(SolverOptions {});
    INFO("cplex log:\n" << log);
    CHECK_FALSE(log.contains("987654"));
  }

  SUBCASE("a missing file warns and the solve still runs")
  {
    SolverOptions o;
    o.param_file = (t.elsewhere / "missing.prm").string();
    const gtopt::test::LogCapture logs;
    const auto log = solve(o);
    CHECK(logs.contains("missing.prm"));
    CHECK_FALSE(log.contains("987654"));
  }
}

TEST_CASE(  // NOLINT
    "cplex.prm canary - gtopt_main honours every documented source")
{
  if (!prm_cplex_available()) {
    MESSAGE("CPLEX plugin not available - skipping");
    return;
  }
  const cfg::RunTree t {"prm-e2e"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedCwd cwd {t.cwd};  // cwd is NOT the case directory
  const auto auto_prm = t.case_dir / "input" / "solvers" / "cplex.prm";
  const auto json_prm = t.envdir / "json.prm";
  const auto set_prm = t.envdir / "set.prm";
  cfg::write_text(json_prm, prm_with(111111));
  cfg::write_text(set_prm, prm_with(222222));

  SUBCASE("JSON solver_options.param_file (absolute)")
  {
    const auto r = prm_run_case(
        t,
        std::format(R"(, "param_file": "{}")", json_prm.generic_string()),
        MainOptions {.solver = "cplex"});
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK(log_has_iteration_limit(r.log, 111111));
    CHECK(r.param_file == json_prm);
  }

  SUBCASE("--set solver_options.param_file beats the JSON")
  {
    const auto r = prm_run_case(
        t,
        std::format(R"(, "param_file": "{}")", json_prm.generic_string()),
        MainOptions {
            .solver = "cplex",
            .set_options = {std::format("solver_options.param_file={}",
                                        set_prm.string())},
        });
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK(log_has_iteration_limit(r.log, 222222));
    CHECK_FALSE(r.log.contains("111111"));
  }

  SUBCASE("auto-discovered <input_directory>/solvers/cplex.prm")
  {
    cfg::write_text(auto_prm, prm_with(333333));
    const auto r = prm_run_case(t, "", MainOptions {.solver = "cplex"});
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK(log_has_iteration_limit(r.log, 333333));
    CHECK(fs::equivalent(r.param_file, auto_prm));
    fs::remove(auto_prm);
  }

  SUBCASE("auto-discovery also works when CPLEX is the default solver")
  {
    // Regression: discovery used to run only with an explicit --solver.
    cfg::write_text(auto_prm, prm_with(333334));
    const cfg::ScopedEnv solver {"GTOPT_SOLVER", "cplex"};
    const auto r = prm_run_case(t, "", MainOptions {});
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK(log_has_iteration_limit(r.log, 333334));
    fs::remove(auto_prm);
  }

  SUBCASE("the case .prm optimality tolerance survives scale_objective")
  {
    // Regression: with scale_objective != 1 the solver path always set a
    // typed optimal_eps = 1e-6 / scale_objective (1e-9 here), which CPLEX
    // applies after the .prm, silently replacing the case's EpOpt.
    cfg::write_text(
        auto_prm,
        prm_with(333335, "CPXPARAM_Simplex_Tolerances_Optimality 3.3e-07\n"));
    const gtopt::test::LogCapture logs;
    const auto r =
        prm_run_case(t,
                     "",
                     MainOptions {
                         .solver = "cplex",
                         .set_options = {"model_options.scale_objective=1000"},
                     });
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK(log_has_iteration_limit(r.log, 333335));
    CHECK(
        log_param(r.log, "CPXPARAM_Simplex_Tolerances_Optimality").value_or(0.0)
        == doctest::Approx(3.3e-7));
    CHECK(logs.contains("optimality tolerance 3.3e-07: from parameter file"));
    fs::remove(auto_prm);
  }

  SUBCASE("without a user value the scale-derived tolerance is used")
  {
    const auto r =
        prm_run_case(t,
                     "",
                     MainOptions {
                         .solver = "cplex",
                         .set_options = {"model_options.scale_objective=1000"},
                     });
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK(
        log_param(r.log, "CPXPARAM_Simplex_Tolerances_Optimality").value_or(0.0)
        == doctest::Approx(1e-9));
  }

  SUBCASE("explicit JSON param_file beats auto-discovery")
  {
    cfg::write_text(auto_prm, prm_with(333335));
    const auto r = prm_run_case(
        t,
        std::format(R"(, "param_file": "{}")", json_prm.generic_string()),
        MainOptions {.solver = "cplex"});
    REQUIRE(r.code == 0);
    CHECK(log_has_iteration_limit(r.log, 111111));
    CHECK_FALSE(r.log.contains("333335"));
    fs::remove(auto_prm);
  }

  SUBCASE("negative: case-root and cwd-relative prm are not discovered")
  {
    cfg::write_text(t.case_dir / "cplex.prm", prm_with(444444));
    cfg::write_text(t.case_dir / "solvers" / "cplex.prm", prm_with(444445));
    cfg::write_text(t.cwd / "input" / "solvers" / "cplex.prm",
                    prm_with(444446));
    cfg::write_text(t.cwd / "cplex.prm", prm_with(444447));
    const auto r = prm_run_case(t, "", MainOptions {.solver = "cplex"});
    REQUIRE(r.code == 0);
    INFO("cplex log:\n" << r.log);
    CHECK_FALSE(r.log.empty());
    CHECK_FALSE(r.log.contains("44444"));
    CHECK(r.param_file.empty());
  }
}

TEST_CASE(  // NOLINT
    "cplex.prm canary - relative param_file resolves against the case dir")
{
  // A relative `solver_options.param_file` is case data: it resolves
  // against input_directory first, and the same relative path under the
  // process cwd is only a fallback — it must not win.
  if (!prm_cplex_available()) {
    MESSAGE("CPLEX plugin not available - skipping");
    return;
  }
  const cfg::RunTree t {"prm-rel"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const cfg::ScopedCwd cwd {t.cwd};
  // input_directory defaults to "input" next to the planning JSON.
  const auto case_prm = t.case_dir / "input" / "tuning" / "my.prm";
  cfg::write_text(case_prm, prm_with(555555));
  // Same relative path under cwd with a different canary: must NOT win.
  cfg::write_text(t.cwd / "tuning" / "my.prm", prm_with(666666));
  const gtopt::test::LogCapture logs;
  const auto r = prm_run_case(
      t, R"(, "param_file": "tuning/my.prm")", MainOptions {.solver = "cplex"});
  REQUIRE(r.code == 0);
  INFO("cplex log:\n" << r.log);
  CHECK(log_has_iteration_limit(r.log, 555555));
  CHECK_FALSE(r.log.contains("666666"));
  CHECK(fs::equivalent(r.param_file, case_prm));
  CHECK(logs.contains("resolved against input_directory"));

  SUBCASE("the cwd copy is the fallback when the case has none")
  {
    fs::remove(case_prm);
    const gtopt::test::LogCapture fb_logs;
    const auto fb = prm_run_case(t,
                                 R"(, "param_file": "tuning/my.prm")",
                                 MainOptions {.solver = "cplex"});
    REQUIRE(fb.code == 0);
    INFO("cplex log:\n" << fb.log);
    CHECK(log_has_iteration_limit(fb.log, 666666));
    CHECK(fb_logs.contains("using the working-directory copy"));
  }
}
