// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_process_util.cpp
 * @brief     find_trusted_executable / create_exclusive_file helpers
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Helpers are prefixed `pu_` for unity builds.
 */

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/process_util.hpp>

#include "config_test_helpers.hpp"

using namespace gtopt;

namespace
{

namespace fs = std::filesystem;

/// Scratch directory removed on destruction.
struct PuScratch
{
  fs::path root;

  PuScratch()
      : root(fs::temp_directory_path()
             / ("gtopt_pu_"
                + std::to_string(
                    static_cast<std::uint64_t>(std::random_device {}()))))
  {
    fs::create_directories(root);
  }
  ~PuScratch()
  {
    std::error_code ec;
    fs::permissions(root, fs::perms::owner_all, ec);
    fs::remove_all(root, ec);
  }
  PuScratch(const PuScratch&) = delete;
  PuScratch(PuScratch&&) = delete;
  auto operator=(const PuScratch&) -> PuScratch& = delete;
  auto operator=(PuScratch&&) -> PuScratch& = delete;
};

/// Create an executable /bin/sh script @p dir / @p name.
void pu_tool(const fs::path& dir, const std::string& name)
{
  fs::create_directories(dir);
  {
    std::ofstream out(dir / name);
    out << "#!/bin/sh\nexit 0\n";
  }
  fs::permissions(dir / name, fs::perms::owner_all, fs::perm_options::replace);
}

constexpr auto pu_tool_name = "gtopt_pu_fake_tool_7f3a";

}  // namespace

TEST_CASE("find_trusted_executable rejects names with a path")  // NOLINT
{
  CHECK(find_trusted_executable("").empty());
  CHECK(find_trusted_executable("/bin/sh").empty());
  CHECK(find_trusted_executable("./sh").empty());
  CHECK(find_trusted_executable("../bin/sh").empty());
}

TEST_CASE("find_trusted_executable searches absolute PATH entries")  // NOLINT
{
  const PuScratch sc;
  const auto bin = sc.root / "bin";
  pu_tool(bin, pu_tool_name);
  fs::permissions(bin,
                  fs::perms::owner_all | fs::perms::group_read
                      | fs::perms::group_exec | fs::perms::others_read
                      | fs::perms::others_exec,
                  fs::perm_options::replace);

  SUBCASE("found on a trusted absolute entry")
  {
    const test::config::ScopedEnv env {"PATH", bin.string()};
    CHECK(find_trusted_executable(pu_tool_name)
          == (bin / pu_tool_name).string());
  }

  SUBCASE("PATH unset finds nothing")
  {
    const test::config::ScopedEnv env {"PATH", std::nullopt};
    CHECK(find_trusted_executable(pu_tool_name).empty());
  }

  SUBCASE("relative and empty entries are skipped")
  {
    const auto old_cwd = fs::current_path();
    fs::current_path(sc.root);
    std::string found;
    {
      const test::config::ScopedEnv env {"PATH", "bin::."};
      found = find_trusted_executable(pu_tool_name);
    }
    fs::current_path(old_cwd);
    CHECK(found.empty());
  }

  SUBCASE("world-writable directories are skipped")
  {
    fs::permissions(bin, fs::perms::others_write, fs::perm_options::add);
    CHECK(is_world_writable_directory(bin));
    const test::config::ScopedEnv env {"PATH", bin.string()};
    CHECK(find_trusted_executable(pu_tool_name).empty());
  }

  SUBCASE("non-executable files are skipped")
  {
    fs::permissions(bin / pu_tool_name,
                    fs::perms::owner_read | fs::perms::owner_write,
                    fs::perm_options::replace);
    const test::config::ScopedEnv env {"PATH", bin.string()};
    CHECK(find_trusted_executable(pu_tool_name).empty());
  }
}

TEST_CASE("is_world_writable_directory")  // NOLINT
{
  const PuScratch sc;
  fs::permissions(sc.root, fs::perms::owner_all, fs::perm_options::replace);
  CHECK_FALSE(is_world_writable_directory(sc.root));
  CHECK_FALSE(is_world_writable_directory(sc.root / "missing"));
  fs::permissions(sc.root, fs::perms::others_write, fs::perm_options::add);
  CHECK(is_world_writable_directory(sc.root));
}

TEST_CASE("executable_directory and spawn_environment")  // NOLINT
{
  const auto dir = executable_directory();
  CHECK_FALSE(dir.empty());
  CHECK(fs::is_directory(dir));
  CHECK(spawn_environment() != nullptr);
}

TEST_CASE("create_exclusive_file")  // NOLINT
{
  const PuScratch sc;
  const auto file = sc.root / "cuts.parquet";

  SUBCASE("creates a new owner-only file once")
  {
    REQUIRE(create_exclusive_file(file));
    CHECK(fs::is_regular_file(file));
    CHECK(fs::file_size(file) == 0);
    const auto perms = fs::status(file).permissions();
    CHECK((perms & fs::perms::all)
          == (fs::perms::owner_read | fs::perms::owner_write));
    CHECK_FALSE(create_exclusive_file(file));
  }

  SUBCASE("never follows a pre-existing symlink")
  {
    const auto target = sc.root / "target";
    fs::create_symlink(target, file);  // dangling
    CHECK_FALSE(create_exclusive_file(file));
    CHECK_FALSE(fs::exists(target));
  }

  SUBCASE("fails in a missing directory")
  {
    CHECK_FALSE(create_exclusive_file(sc.root / "nope" / "f"));
  }
}

TEST_CASE("gtopt_environment lists GTOPT_* variables")  // NOLINT
{
  std::string a = "GTOPT_SOLVER=highs";
  std::string b = "PATH=/usr/bin";
  std::string c = "GTOPT_WEBSERVICE_API_TOKEN=abc";
  std::string d = "GTOPT_ALPHA=1";
  std::string e = "GTOPT_NOVALUE";
  std::array<char*, 6> envp {
      a.data(),
      b.data(),
      c.data(),
      d.data(),
      e.data(),
      nullptr,
  };

  const auto vars = gtopt_environment(envp.data());
  REQUIRE(vars.size() == 3);
  CHECK(vars[0].first == "GTOPT_ALPHA");
  CHECK(vars[0].second == "1");
  CHECK(vars[1].first == "GTOPT_SOLVER");
  CHECK(vars[1].second == "highs");
  CHECK(vars[2].first == "GTOPT_WEBSERVICE_API_TOKEN");
  CHECK(vars[2].second == "***");

  CHECK(gtopt_environment(nullptr).empty());
}

TEST_CASE("log_gtopt_environment runs with test hooks set")  // NOLINT
{
  const test::config::ScopedEnv hook {"GTOPT_TEST_PU_HOOK", "1"};
  const test::config::ScopedEnv var {"GTOPT_PU_VAR", "x"};
  log_gtopt_environment();
  const auto vars = gtopt_environment(spawn_environment());
  CHECK(std::ranges::any_of(
      vars, [](const auto& kv) { return kv.first == "GTOPT_PU_VAR"; }));
}
