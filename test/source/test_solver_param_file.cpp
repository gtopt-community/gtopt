/**
 * @file      test_solver_param_file.cpp
 * @brief     Unit tests for relative `solver_options.param_file` resolution
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Regression: a case pinning `param_file = "solvers/cplex.prm"` and run from
 * an output directory (`--set input_directory=<case>`) passed the relative
 * string verbatim to `CPXreadcopyparam`, which failed with 1423 (cannot
 * open) and the MIP ran with CPLEX defaults.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <filesystem>
#include <fstream>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/solver_param_file.hpp>

using namespace gtopt;

namespace solver_param_file_test
{
namespace
{

[[nodiscard]] std::filesystem::path make_case_dir(const std::string& tag)
{
  auto dir = std::filesystem::temp_directory_path() / ("gtopt_prm_" + tag);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir / "solvers");
  std::ofstream {dir / "solvers" / "cplex.prm"} << "CPLEX Parameter File\n";
  return dir;
}

[[nodiscard]] SolverOptions with_param_file(const std::string& path)
{
  SolverOptions so;
  so.param_file = path;
  return so;
}

}  // namespace
}  // namespace solver_param_file_test

TEST_CASE("resolve_solver_param_file: relative path via input_directory")
{
  using namespace solver_param_file_test;
  const auto dir = make_case_dir("indir");
  const auto expected = std::filesystem::canonical(dir / "solvers/cplex.prm");

  SUBCASE("free function")
  {
    const auto r = resolve_solver_param_file("solvers/cplex.prm", dir.string());
    REQUIRE(r.has_value());
    CHECK(std::filesystem::equivalent(r.value_or(""), expected));
    CHECK(r.value_or("").is_absolute());
    CHECK(resolve_solver_param_file_source("solvers/cplex.prm", dir.string())
              .value_or(ResolvedParamFile {})
              .source
          == ParamFileSource::input_directory);
  }

  SUBCASE("in-place on SolverOptions")
  {
    SolverOptions so;
    so.param_file = "solvers/cplex.prm";
    CHECK(resolve_solver_param_file(so, dir.string(), "solver_options"));
    const std::filesystem::path got {so.param_file.value_or("")};
    CHECK(got.is_absolute());
    CHECK(std::filesystem::equivalent(got, expected));
  }

  SUBCASE("every SolverOptions bundle in PlanningOptions")
  {
    PlanningOptions po;
    po.input_directory = dir.string();
    po.solver_options.param_file = "solvers/cplex.prm";
    po.monolithic_options.solver_options = with_param_file("solvers/cplex.prm");
    po.sddp_options.forward_solver_options =
        with_param_file("solvers/cplex.prm");
    po.sddp_options.backward_solver_options =
        with_param_file("solvers/missing.prm");
    CHECK(resolve_solver_param_files(po) == 1);
    CHECK(std::filesystem::path {po.solver_options.param_file.value_or("")}
              .is_absolute());
    CHECK(std::filesystem::path {
        po.monolithic_options.solver_options.value_or(SolverOptions {})
            .param_file.value_or("")}
              .is_absolute());
    CHECK(std::filesystem::path {
        po.sddp_options.forward_solver_options.value_or(SolverOptions {})
            .param_file.value_or("")}
              .is_absolute());
    // Not found: kept as given (the plugin warns again when opening it).
    CHECK(po.sddp_options.backward_solver_options.value_or(SolverOptions {})
              .param_file.value_or("")
          == "solvers/missing.prm");
  }

  std::filesystem::remove_all(dir);
}

TEST_CASE(
    "resolve_solver_param_file: case dir first, cwd fallback, absolute, "
    "missing, unset")
{
  using namespace solver_param_file_test;
  const auto dir = make_case_dir("cwd");
  const auto file = dir / "solvers/cplex.prm";

  SUBCASE("working-directory copy is used when the case has none")
  {
    const auto rel =
        std::filesystem::relative(file, std::filesystem::current_path());
    REQUIRE(rel.is_relative());
    // A bogus input_directory: only the CWD candidate exists.
    const auto r =
        resolve_solver_param_file_source(rel.string(), "/nonexistent");
    REQUIRE(r.has_value());
    CHECK(std::filesystem::equivalent(r.value_or(ResolvedParamFile {}).path,
                                      file));
    CHECK(r.value_or(ResolvedParamFile {}).source
          == ParamFileSource::working_directory);
  }

  SUBCASE("the input_directory copy wins over a working-directory copy")
  {
    // Same relative path under the CWD and under input_directory.
    const auto rel =
        std::filesystem::relative(file, std::filesystem::current_path());
    REQUIRE(rel.is_relative());
    const auto case_dir = make_case_dir("casewins");
    const auto case_copy = case_dir / rel;
    std::filesystem::create_directories(case_copy.parent_path());
    std::ofstream {case_copy} << "CPLEX Parameter File\n";

    const auto r =
        resolve_solver_param_file_source(rel.string(), case_dir.string());
    REQUIRE(r.has_value());
    CHECK(std::filesystem::equivalent(r.value_or(ResolvedParamFile {}).path,
                                      case_copy));
    CHECK(r.value_or(ResolvedParamFile {}).source
          == ParamFileSource::input_directory);

    SolverOptions so;
    so.param_file = rel.string();
    CHECK(resolve_solver_param_file(so, case_dir.string(), "solver_options"));
    CHECK(std::filesystem::equivalent(so.param_file.value_or(""), case_copy));
    std::filesystem::remove_all(case_dir);
  }

  SUBCASE("absolute path is returned unchanged")
  {
    const auto r = resolve_solver_param_file(file.string(), "/nonexistent");
    CHECK(r.value_or("") == file);
    CHECK(resolve_solver_param_file_source(file.string(), dir.string())
              .value_or(ResolvedParamFile {
                  .source = ParamFileSource::working_directory,
              })
              .source
          == ParamFileSource::absolute);
  }

  SUBCASE("missing file → nullopt / false, string kept")
  {
    CHECK_FALSE(resolve_solver_param_file("solvers/none.prm", dir.string())
                    .has_value());
    CHECK_FALSE(
        resolve_solver_param_file((dir / "none.prm").string(), dir.string())
            .has_value());
    SolverOptions so;
    so.param_file = "solvers/none.prm";
    CHECK_FALSE(resolve_solver_param_file(so, dir.string(), "solver_options"));
    CHECK(so.param_file.value_or("") == "solvers/none.prm");
  }

  SUBCASE("unset / empty param_file is a no-op")
  {
    SolverOptions so;
    CHECK(resolve_solver_param_file(so, dir.string(), "solver_options"));
    CHECK_FALSE(so.param_file.has_value());
    so.param_file = "";
    CHECK(resolve_solver_param_file(so, dir.string(), "solver_options"));
    CHECK(so.param_file.value_or("x").empty());
  }

  std::filesystem::remove_all(dir);
}
