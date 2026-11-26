// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_private_tmpdir.cpp
 * @brief     The test process runs in a private TMPDIR (see main.cpp)
 * @copyright Copyright (c) 2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The probe test case is also driven by `check_private_tmpdir.cmake`
 * (ctest `gtoptTests.private_tmpdir_concurrent`), which runs two copies
 * at the same time and requires that neither sees the other's file.
 */

#include <charconv>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <format>
#include <fstream>
#include <string>
#include <thread>

#include <doctest/doctest.h>
#include <unistd.h>

#include "tmp_dir_helpers.hpp"

using namespace gtopt;

namespace
{

namespace fs = std::filesystem;

[[nodiscard]] std::string ptd_read_file(const fs::path& path)
{
  std::ifstream in {path};
  std::string text;
  std::getline(in, text);
  return text;
}

/// GTOPT_TEST_PROBE_MS (default 200): how long the probe keeps re-reading.
[[nodiscard]] int ptd_probe_ms()
{
  const char* const v = std::getenv("GTOPT_TEST_PROBE_MS");  // NOLINT
  if (v == nullptr || *v == '\0') {
    return 200;
  }
  int ms = 200;
  std::from_chars(v, v + std::strlen(v), ms);
  return ms;
}

}  // namespace

TEST_CASE("private TMPDIR - temp_directory_path is process-private")  // NOLINT
{
  const auto priv = gtopt::test::test_private_tmpdir();
  if (priv.empty()) {
    MESSAGE("private TMPDIR disabled (GTOPT_TEST_PRIVATE_TMPDIR off)");
    return;
  }

  REQUIRE(fs::is_directory(priv));
  CHECK(fs::equivalent(fs::temp_directory_path(), priv));

  const auto name = priv.filename().string();
  CHECK(name.starts_with("gtoptTests-"));
  CHECK(name.contains(std::format("-{}-", ::getpid())));

#ifdef GTOPT_TEST_SOURCE_ROOT
  // Never inside the checkout: tests that walk up from $TMPDIR looking for
  // repo markers (include/gtopt, .git) must not find it.
  const auto rel = fs::weakly_canonical(priv).lexically_relative(
      fs::weakly_canonical(GTOPT_TEST_SOURCE_ROOT));
  CHECK((rel.empty() || *rel.begin() == ".."));
#endif

  // Under ctest the directory sits in the per-build-dir root.
  // NOLINTNEXTLINE(concurrency-mt-unsafe)
  if (const char* base = std::getenv("GTOPT_TEST_TMPDIR_BASE");
      base != nullptr && *base != '\0')
  {
    CHECK(fs::equivalent(priv.parent_path(), fs::path {base}));
  }
}

TEST_CASE("unique_test_dir - distinct per call, inside TMPDIR")  // NOLINT
{
  const auto a = gtopt::test::unique_test_dir("ptd_same_tag");
  const auto b = gtopt::test::unique_test_dir("ptd_same_tag");
  CHECK(a != b);
  CHECK(fs::is_directory(a));
  CHECK(fs::is_directory(b));
  CHECK(fs::equivalent(a.parent_path(), fs::temp_directory_path()));
  CHECK(a.filename().string().contains(std::format("_{}_", ::getpid())));

  const auto p = gtopt::test::unique_test_path("ptd_path");
  CHECK_FALSE(fs::exists(p));
  CHECK(p.parent_path() == fs::temp_directory_path());

  fs::remove_all(a);
  fs::remove_all(b);
}

TEST_CASE("private TMPDIR - fixed-name probe is not clobbered")  // NOLINT
{
  // A deliberately FIXED name: with a shared TMPDIR, a second copy of this
  // test running at the same time overwrites it and the re-read fails.
  const auto probe = fs::temp_directory_path() / "gtopt_private_tmpdir_probe";
  const auto mine = std::to_string(::getpid());
  {
    std::ofstream out {probe, std::ios::trunc};
    out << mine << '\n';
  }

  using namespace std::chrono_literals;
  const auto deadline = std::chrono::steady_clock::now()
      + std::chrono::milliseconds(ptd_probe_ms());
  bool clobbered = false;
  while (!clobbered) {
    clobbered = ptd_read_file(probe) != mine;
    if (std::chrono::steady_clock::now() >= deadline) {
      break;
    }
    std::this_thread::sleep_for(20ms);
  }

  CHECK_FALSE(clobbered);
  std::error_code ec;
  fs::remove(probe, ec);
}
