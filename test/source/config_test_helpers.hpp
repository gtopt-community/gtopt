// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      config_test_helpers.hpp
 * @brief     Fixtures for the "configuration is actually read" tests
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Environment / cwd guards, a throw-away directory tree laid out like a
 * real run (case dir, separate cwd, fake HOME, env-override dir), and a
 * tiny one-bus planning JSON whose `options` block is supplied by the
 * test.  Shared by test_config_canary_files.cpp and
 * test_config_precedence_e2e.cpp.
 */

#pragma once

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <fstream>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>

#include <daw/json/daw_json_link.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning.hpp>
#include <stdlib.h>  // NOLINT(modernize-deprecated-headers) setenv/unsetenv

namespace gtopt::test::config
{

namespace fs = std::filesystem;

/// Set (or unset, with `nullopt`) an environment variable for the
/// lifetime of the guard; restores the previous value on destruction.
class ScopedEnv
{
public:
  ScopedEnv(const char* name, const std::optional<std::string>& value)
      : m_name_ {name}
  {
    if (const char* old = std::getenv(name); old != nullptr) {
      m_old_ = std::string {old};
    }
    if (value.has_value()) {
      ::setenv(name, value->c_str(), 1);
    } else {
      ::unsetenv(name);
    }
  }

  ~ScopedEnv()
  {
    if (m_old_.has_value()) {
      ::setenv(m_name_.c_str(), m_old_->c_str(), 1);
    } else {
      ::unsetenv(m_name_.c_str());
    }
  }

  ScopedEnv(const ScopedEnv&) = delete;
  ScopedEnv(ScopedEnv&&) = delete;
  auto operator=(const ScopedEnv&) -> ScopedEnv& = delete;
  auto operator=(ScopedEnv&&) -> ScopedEnv& = delete;

private:
  std::string m_name_;
  std::optional<std::string> m_old_;
};

/// Change the process cwd for the lifetime of the guard.
class ScopedCwd
{
public:
  explicit ScopedCwd(const fs::path& dir)
      : m_old_ {fs::current_path()}
  {
    fs::current_path(dir);
  }

  ~ScopedCwd()
  {
    std::error_code ec;
    fs::current_path(m_old_, ec);
  }

  ScopedCwd(const ScopedCwd&) = delete;
  ScopedCwd(ScopedCwd&&) = delete;
  auto operator=(const ScopedCwd&) -> ScopedCwd& = delete;
  auto operator=(ScopedCwd&&) -> ScopedCwd& = delete;

private:
  fs::path m_old_;
};

/// A fresh directory tree laid out like a real run:
///   <root>/case/       planning JSON (+ input/ next to it)
///   <root>/cwd/        process cwd during the run (NOT the case dir)
///   <root>/home/       fake $HOME
///   <root>/envdir/     target of GTOPT_* path overrides
///   <root>/elsewhere/  a directory on NO search path (negative canary)
///   <root>/out/        output directory
struct RunTree
{
  fs::path root;
  fs::path case_dir;
  fs::path cwd;
  fs::path home;
  fs::path envdir;
  fs::path elsewhere;
  fs::path out;

  explicit RunTree(std::string_view tag)
  {
    const char* tmp = std::getenv("TMPDIR");
    const fs::path base = (tmp != nullptr && *tmp != '\0')
        ? fs::path {tmp}
        : fs::temp_directory_path();
    root = base
        / std::format("gtopt-config-{}-{}",
                      tag,
                      static_cast<std::uint64_t>(std::random_device {}())
                          ^ (static_cast<std::uint64_t>(std::random_device {}())
                             << 32U));
    case_dir = root / "case";
    cwd = root / "cwd";
    home = root / "home";
    envdir = root / "envdir";
    elsewhere = root / "elsewhere";
    out = root / "out";
    for (const auto& d : {case_dir, cwd, home, envdir, elsewhere, out}) {
      fs::create_directories(d);
    }
  }

  ~RunTree()
  {
    std::error_code ec;
    fs::remove_all(root, ec);
  }

  RunTree(const RunTree&) = delete;
  RunTree(RunTree&&) = delete;
  auto operator=(const RunTree&) -> RunTree& = delete;
  auto operator=(RunTree&&) -> RunTree& = delete;
};

inline void write_text(const fs::path& file, std::string_view text)
{
  fs::create_directories(file.parent_path());
  std::ofstream out {file, std::ios::trunc};
  out << text;
}

[[nodiscard]] inline std::string slurp(const fs::path& file)
{
  std::ifstream in {file};
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

/// One-bus, one-block planning whose `options` object is @p options_body
/// (the text between the braces, may be empty).  The generator has
/// 200 MW at 10 $/MWh; a @p demand above 200 MW leaves load unserved.
[[nodiscard]] inline std::string case_json(std::string_view options_body,
                                           double demand = 50.0)
{
  return std::format(R"json({{
  "options": {{ {} }},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]
  }},
  "system": {{
    "name": "config_canary",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": 10.0, "capacity": 200.0}}
    ],
    "demand_array": [
      {{"uid": 1, "name": "d1", "bus": 1, "capacity": {}}}
    ]
  }}
}})json",
                     options_body,
                     demand);
}

/// Parse a planning JSON written by gtopt (`gtopt_state.json` /
/// `planning.json`) back into a Planning, so tests assert on typed values.
[[nodiscard]] inline Planning read_planning(const fs::path& file)
{
  const auto text = slurp(file);
  return daw::json::from_json<Planning>(text, StrictParsePolicy);
}

}  // namespace gtopt::test::config
