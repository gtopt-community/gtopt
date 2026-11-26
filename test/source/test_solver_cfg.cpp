/**
 * @file      test_solver_cfg.cpp
 * @brief     Unit tests for the shared solver tuning-file loader
 * @date      2026-07-08
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Covers `include/gtopt/solver_cfg.hpp` without touching any solver
 * backend: value translation (numbers pass through, per-key EnumEntry
 * names, generic booleans, unknown-name dropping) and file loading (INI
 * `.cfg` with sections + inline comments, legacy `.prm` fallback, `.cfg`
 * precedence over `.prm`).
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <array>
#include <filesystem>
#include <format>
#include <fstream>
#include <optional>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/solver_cfg.hpp>
#include <unistd.h>

#include "tmp_dir_helpers.hpp"

using namespace gtopt;

// Unity builds concatenate test TUs: keep helpers in a file-unique
// namespace so sibling anonymous-namespace helpers cannot collide.
namespace solver_cfg_test
{
namespace
{

// A miniature "method"-style table standing in for a backend's own.
inline constexpr auto fake_method_entries = std::to_array<EnumEntry<int>>({
    {.name = "concurrent", .value = 0},
    {.name = "pdlp", .value = 1},
    {.name = "barrier", .value = 3},
    {.name = "primal", .value = 1, .is_alias = true},
});

inline constexpr auto fake_enum_keys = std::to_array<SolverCfgEnumKey>({
    {.key = "method", .entries = fake_method_entries},
});

[[nodiscard]] std::filesystem::path make_scratch_dir(const std::string& tag)
{
  return gtopt::test::unique_test_dir("scfg_" + tag);
}

void write_file(const std::filesystem::path& path, const std::string& content)
{
  std::ofstream out {path};
  out << content;
}

}  // namespace
}  // namespace solver_cfg_test

using namespace solver_cfg_test;

TEST_CASE("solver_cfg translate: numbers pass through verbatim")  // NOLINT
{
  const auto t = [&](std::string_view v)
  { return translate_solver_cfg_value(fake_enum_keys, "method", v); };
  CHECK(t("2").value_or("") == "2");
  CHECK(t("-1").value_or("") == "-1");
  CHECK(t("1e-4").value_or("") == "1e-4");
  CHECK(t("0.5").value_or("") == "0.5");
}

TEST_CASE("solver_cfg translate: named enum values and aliases")  // NOLINT
{
  const auto t = [&](std::string_view v)
  { return translate_solver_cfg_value(fake_enum_keys, "method", v); };
  CHECK(t("concurrent").value_or("") == "0");
  CHECK(t("pdlp").value_or("") == "1");
  CHECK(t("barrier").value_or("") == "3");
  // Aliases and ASCII case-insensitivity both resolve.
  CHECK(t("primal").value_or("") == "1");
  CHECK(t("Concurrent").value_or("") == "0");
  CHECK(t("PDLP").value_or("") == "1");
  // Unknown symbolic value on an enum key is dropped, not forwarded.
  CHECK_FALSE(t("simplex").has_value());
}

TEST_CASE("solver_cfg translate: generic booleans for unlisted keys")  // NOLINT
{
  const auto t = [&](std::string_view v)
  { return translate_solver_cfg_value(fake_enum_keys, "crossover", v); };
  CHECK(t("true").value_or("") == "1");
  CHECK(t("false").value_or("") == "0");
  CHECK(t("on").value_or("") == "1");
  CHECK(t("off").value_or("") == "0");
  CHECK(t("Yes").value_or("") == "1");
  CHECK(t("NO").value_or("") == "0");
  // Non-boolean symbolic value on an unlisted key is dropped.
  CHECK_FALSE(t("fast").has_value());
}

TEST_CASE("solver_cfg load: INI .cfg with section and inline comments")
// NOLINT
{
  const auto dir = make_scratch_dir("cfg");
  write_file(dir / "cuopt.cfg",
             "# tuning file\n"
             "crossover = on            ; generic boolean\n"
             "[cuopt]\n"
             "method = concurrent       # named value\n"
             "presolve = 2\n"
             "relative_gap_tolerance = 1e-4\n");

  const std::optional<std::string> param_file = (dir / "cplex.prm").string();
  const auto pairs = load_solver_cfg(param_file, "cuopt", fake_enum_keys);

  REQUIRE(pairs.size() == 4);
  // Sectionless entries come first, then the [cuopt] section.
  CHECK(pairs[0].first == "crossover");
  CHECK(pairs[0].second == "1");
  // INI map ordering is alphabetical within the section.
  const auto find = [&](std::string_view key) -> std::string
  {
    for (const auto& [k, v] : pairs) {
      if (k == key) {
        return v;
      }
    }
    return {};
  };
  CHECK(find("method") == "0");
  CHECK(find("presolve") == "2");
  CHECK(find("relative_gap_tolerance") == "1e-4");

  std::filesystem::remove_all(dir);
}

TEST_CASE("solver_cfg load: legacy .prm fallback accepts named values")
// NOLINT
{
  const auto dir = make_scratch_dir("prm");
  write_file(dir / "cuopt.prm",
             "# legacy dialect\n"
             "method pdlp\n"
             "crossover = off\n"
             "time_limit 60\n");

  const std::optional<std::string> param_file = (dir / "cplex.prm").string();
  const auto pairs = load_solver_cfg(param_file, "cuopt", fake_enum_keys);

  REQUIRE(pairs.size() == 3);
  CHECK(pairs[0].first == "method");
  CHECK(pairs[0].second == "1");
  CHECK(pairs[1].first == "crossover");
  CHECK(pairs[1].second == "0");
  CHECK(pairs[2].first == "time_limit");
  CHECK(pairs[2].second == "60");

  std::filesystem::remove_all(dir);
}

TEST_CASE("solver_cfg load: .cfg takes precedence over legacy .prm")  // NOLINT
{
  const auto dir = make_scratch_dir("both");
  write_file(dir / "cuopt.cfg", "method = barrier\n");
  write_file(dir / "cuopt.prm", "method pdlp\n");

  const std::optional<std::string> param_file = (dir / "cplex.prm").string();
  const auto pairs = load_solver_cfg(param_file, "cuopt", fake_enum_keys);

  REQUIRE(pairs.size() == 1);
  CHECK(pairs[0].second == "3");  // barrier from the .cfg, not pdlp

  std::filesystem::remove_all(dir);
}

TEST_CASE("solver_cfg load: absent param_file or files yield empty")  // NOLINT
{
  CHECK(load_solver_cfg(std::nullopt, "cuopt", fake_enum_keys).empty());
  CHECK(load_solver_cfg(std::string {}, "cuopt", fake_enum_keys).empty());
  const auto dir = make_scratch_dir("none");
  const std::optional<std::string> param_file = (dir / "cplex.prm").string();
  CHECK(load_solver_cfg(param_file, "cuopt", fake_enum_keys).empty());
  std::filesystem::remove_all(dir);
}

TEST_CASE(  // NOLINT
    "solver param file: native formats parse to name/value pairs")
{
  const auto dir =
      make_scratch_dir(std::format("native_{}", static_cast<int>(::getpid())));
  write_file(dir / "cplex_warmstart.prm",
             "CPLEX Parameter File Version 22.1.1.0\n"
             "# comment\n"
             "\n"
             "CPXPARAM_LPMethod                1\n"
             "CPXPARAM_WorkDir   \"/tmp/a b\"  \n"
             "; another comment\n"
             "// and another\n"
             "NameOnly\n");
  write_file(dir / "highs_warmstart.opts",
             "solver = simplex\n"
             "presolve=off\n"
             "simplex_strategy  =  4\n");

  const auto cplex = parse_solver_param_file(dir / "cplex_warmstart.prm");
  REQUIRE(cplex.has_value());
  const SolverCfgPairs expected_cplex {
      {"CPXPARAM_LPMethod", "1"},
      {"CPXPARAM_WorkDir", "/tmp/a b"},
  };
  CHECK(cplex.value_or(SolverCfgPairs {}) == expected_cplex);

  const auto highs = parse_solver_param_file(dir / "highs_warmstart.opts");
  const SolverCfgPairs expected_highs {
      {"solver", "simplex"},
      {"presolve", "off"},
      {"simplex_strategy", "4"},
  };
  CHECK(highs.value_or(SolverCfgPairs {}) == expected_highs);

  CHECK_FALSE(parse_solver_param_file(dir / "missing.prm").has_value());
  std::filesystem::remove_all(dir);
}

TEST_CASE(  // NOLINT
    "solver param file: read once per process, missing file cached")
{
  const auto dir =
      make_scratch_dir(std::format("cached_{}", static_cast<int>(::getpid())));
  const auto path = dir / "gurobi_warmstart.prm";
  write_file(path, "Method 0\n");
  const std::optional<std::string> param_file = (dir / "cplex.prm").string();

  const auto* first =
      cached_sibling_param_file(param_file, "gurobi_warmstart.prm");
  REQUIRE(first != nullptr);
  CHECK(first->size() == 1);

  // Rewriting (or deleting) the file mid-run does not trigger a re-read:
  // the hot path returns the same cached object without touching the disk.
  write_file(path, "Method 1\nPresolve 0\n");
  CHECK(cached_sibling_param_file(param_file, "gurobi_warmstart.prm") == first);
  std::filesystem::remove(path);
  CHECK(cached_sibling_param_file(param_file, "gurobi_warmstart.prm") == first);
  CHECK(first->front().second == "0");

  // A missing sibling is cached as missing (no stat per call later).
  CHECK(cached_sibling_param_file(param_file, "mindopt_warmstart.prm")
        == nullptr);
  write_file(dir / "mindopt_warmstart.prm", "Method 0\n");
  CHECK(cached_sibling_param_file(param_file, "mindopt_warmstart.prm")
        == nullptr);

  CHECK(cached_sibling_param_file(std::nullopt, "x.prm") == nullptr);
  CHECK(cached_sibling_param_file(std::string {}, "x.prm") == nullptr);

  // cuOpt's tuning-file loader: same once-per-process contract.
  write_file(dir / "cuopt.prm", "method pdlp\n");
  const auto cfg1 = load_solver_cfg_cached(param_file, "cuopt", fake_enum_keys);
  write_file(dir / "cuopt.prm", "method barrier\n");
  const auto cfg2 = load_solver_cfg_cached(param_file, "cuopt", fake_enum_keys);
  REQUIRE(cfg1.size() == 1);
  CHECK(cfg1 == cfg2);
  CHECK(load_solver_cfg_cached(std::nullopt, "cuopt", fake_enum_keys).empty());

  std::filesystem::remove_all(dir);
}
