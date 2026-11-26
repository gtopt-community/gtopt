// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      main.cpp
 * @brief     doctest entry point + a clean per-test spdlog default logger
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * We own `main()` (rather than DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN) so the
 * whole test binary runs against one known-good, **synchronous** spdlog
 * default logger.  Some tests exercise `gtopt_main()`, which installs an
 * `async_logger` as the global default and never restores it; left in
 * place, that breaks every later `LogCapture`-based test (the async backend
 * thread delivers messages *after* the synchronous `contains()` check has
 * already run, and mutating a live async logger's sinks is racy).
 *
 * A doctest listener re-installs our synchronous logger before every test
 * case (and every subcase re-entry), so no test can inherit a polluted
 * global logger from a predecessor.  `LogCapture` then swaps sinks on this
 * synchronous logger and captures deterministically.
 *
 * Private TMPDIR (option `GTOPT_TEST_PRIVATE_TMPDIR`, default ON): before
 * any test runs, `main()` creates a directory unique to this process --
 * `<base>/gtoptTests-<test-case filter>-<pid>-<random>`, where `<base>` is
 * `$GTOPT_TEST_TMPDIR_BASE` (set by ctest to `<build>/test_tmp/gtoptTests`)
 * or else the caller's `temp_directory_path()` -- and points `$TMPDIR` at
 * it.  Every `temp_directory_path() / "fixed_name"` in the suite is then
 * private to this process, so concurrent ctest runs (several build dirs
 * on one host) and concurrent tests of one `ctest -j` run never collide.
 * The directory is removed when the run passes and kept (path printed to
 * stderr) when it fails; `GTOPT_TEST_KEEP_TMPDIR=1` always keeps it and
 * `GTOPT_TEST_PRIVATE_TMPDIR=0` disables the mechanism at run time.
 */

#define DOCTEST_CONFIG_IMPLEMENT
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <memory>
#include <print>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include <doctest/doctest.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/spdlog.h>
#include <unistd.h>

#include "tmp_dir_helpers.hpp"

#ifndef GTOPT_TEST_PRIVATE_TMPDIR
#  define GTOPT_TEST_PRIVATE_TMPDIR 0
#endif

namespace
{

/// The private TMPDIR of this process (empty when disabled).
std::filesystem::path& gtopt_tmpdir_storage()
{
  static std::filesystem::path path;
  return path;
}

/// True when `name` is set to a non-empty value other than 0/off/false/no.
bool gtopt_tmpdir_env_flag(const char* name, bool fallback)
{
  const char* const value = std::getenv(name);  // NOLINT(concurrency-mt-unsafe)
  if (value == nullptr || *value == '\0') {
    return fallback;
  }
  const std::string_view v {value};
  return !(v == "0" || v == "off" || v == "OFF" || v == "false" || v == "FALSE"
           || v == "no" || v == "NO");
}

/// The `--test-case=` / `-tc=` filter, reduced to `[A-Za-z0-9._-]` and at
/// most 60 characters (so ctest's one-test-per-process dirs are readable).
std::string gtopt_tmpdir_test_tag(int argc, char** argv)
{
  std::string tag;
  for (int i = 1; i < argc; ++i) {
    const std::string_view arg {argv[i]};  // NOLINT
    for (const std::string_view prefix : {"--test-case=", "-tc="}) {
      if (arg.starts_with(prefix)) {
        tag = arg.substr(prefix.size());
      }
    }
  }
  constexpr std::size_t max_len = 60;
  if (tag.size() > max_len) {
    tag.resize(max_len);
  }
  for (auto& c : tag) {
    const bool keep = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
        || (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_';
    if (!keep) {
      c = '_';
    }
  }
  return tag.empty() ? std::string {"all"} : tag;
}

/// True when `path` is the gtopt checkout or below it.  Tests that walk up
/// from `$TMPDIR` looking for repo markers must never find the checkout.
bool gtopt_tmpdir_inside_source_tree(const std::filesystem::path& path)
{
#ifdef GTOPT_TEST_SOURCE_ROOT
  namespace fs = std::filesystem;
  std::error_code ec;
  const auto root = fs::weakly_canonical(GTOPT_TEST_SOURCE_ROOT, ec);
  if (ec) {
    return false;
  }
  const auto here = fs::weakly_canonical(path, ec);
  if (ec) {
    return false;
  }
  const auto rel = here.lexically_relative(root);
  return !rel.empty() && *rel.begin() != "..";
#else
  static_cast<void>(path);
  return false;
#endif
}

/// Create this process's private TMPDIR and export it as `$TMPDIR`.
/// Returns the empty path (and leaves `$TMPDIR` alone) when disabled or
/// when no directory could be created.
std::filesystem::path gtopt_make_private_tmpdir(int argc, char** argv)
{
  namespace fs = std::filesystem;
  if (GTOPT_TEST_PRIVATE_TMPDIR == 0
      || !gtopt_tmpdir_env_flag("GTOPT_TEST_PRIVATE_TMPDIR",
                                /*fallback=*/true))
  {
    return {};
  }

  std::error_code ec;
  fs::path base;
  // NOLINTNEXTLINE(concurrency-mt-unsafe)
  if (const char* b = std::getenv("GTOPT_TEST_TMPDIR_BASE");
      b != nullptr && *b != '\0')
  {
    base = b;
  } else {
    base = fs::temp_directory_path(ec);  // honours the caller's $TMPDIR
    if (ec) {
      return {};
    }
  }
  if (gtopt_tmpdir_inside_source_tree(base)) {
    std::println(stderr,
                 "gtoptTests: TMPDIR base {} is inside the source tree; "
                 "using /tmp instead",
                 base.string());
    base = "/tmp";
  }
  fs::create_directories(base, ec);

  const auto tag = gtopt_tmpdir_test_tag(argc, argv);
  std::random_device rd;
  constexpr int max_attempts = 100;
  for (int attempt = 0; attempt < max_attempts; ++attempt) {
    auto dir =
        base / std::format("gtoptTests-{}-{}-{:08x}", tag, ::getpid(), rd());
    // create_directory is atomic: true only for the process that made it.
    if (fs::create_directory(dir, ec) && !ec) {
      // NOLINTNEXTLINE(concurrency-mt-unsafe)
      if (::setenv("TMPDIR", dir.c_str(), 1) != 0) {
        fs::remove(dir, ec);
        return {};
      }
      return dir;
    }
  }
  std::println(stderr,
               "gtoptTests: could not create a private TMPDIR under {}",
               base.string());
  return {};
}

/// Remove the private TMPDIR after a passing run; keep it (and say where)
/// after a failing one or when GTOPT_TEST_KEEP_TMPDIR is set.
void gtopt_finish_private_tmpdir(const std::filesystem::path& dir, int rc)
{
  if (dir.empty()) {
    return;
  }
  if (rc == 0
      && !gtopt_tmpdir_env_flag("GTOPT_TEST_KEEP_TMPDIR", /*fallback=*/false))
  {
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);  // only ever our own private dir
    return;
  }
  std::println(stderr, "gtoptTests: private TMPDIR kept: {}", dir.string());
}

/// The single synchronous logger the whole test binary runs against.
/// Created once; re-installed as the default before each test case.
std::shared_ptr<spdlog::logger> gtopt_test_logger()
{
  static const std::shared_ptr<spdlog::logger> logger = []
  {
    auto sink = std::make_shared<spdlog::sinks::stderr_color_sink_mt>();
    auto lg = std::make_shared<spdlog::logger>("gtopt_test", std::move(sink));
    lg->set_level(spdlog::level::info);  // spdlog's normal default level
    return lg;
  }();
  return logger;
}

/// Restore our synchronous logger as spdlog's default.
void install_test_logger()
{
  spdlog::set_default_logger(gtopt_test_logger());
}

/// doctest listener that resets the default logger before each test case,
/// neutralising any global-logger pollution left by a previous test (e.g.
/// the async logger installed by `gtopt_main()`).
struct LoggerResetListener : doctest::IReporter
{
  explicit LoggerResetListener(const doctest::ContextOptions& /*opts*/) {}

  void test_case_start(const doctest::TestCaseData& /*data*/) override
  {
    install_test_logger();
  }
  void test_case_reenter(const doctest::TestCaseData& /*data*/) override
  {
    install_test_logger();
  }

  // Remaining IReporter hooks are unused.
  void report_query(const doctest::QueryData& /*query*/) override {}
  void test_run_start() override {}
  void test_run_end(const doctest::TestRunStats& /*stats*/) override {}
  void test_case_end(const doctest::CurrentTestCaseStats& /*stats*/) override {}
  void test_case_exception(const doctest::TestCaseException& /*exc*/) override
  {
  }
  void subcase_start(const doctest::SubcaseSignature& /*signature*/) override {}
  void subcase_end() override {}
  void log_assert(const doctest::AssertData& /*assert_data*/) override {}
  void log_message(const doctest::MessageData& /*message*/) override {}
  void test_case_skipped(const doctest::TestCaseData& /*data*/) override {}
};

}  // namespace

DOCTEST_REGISTER_LISTENER("gtopt_logger_reset", 1, LoggerResetListener);

std::filesystem::path gtopt::test::test_private_tmpdir()
{
  return gtopt_tmpdir_storage();
}

int main(int argc, char** argv)
{
  install_test_logger();

  try {
    gtopt_tmpdir_storage() = gtopt_make_private_tmpdir(argc, argv);
  } catch (const std::exception& e) {
    // C stdio: std::println could throw inside main()'s handler.
    static_cast<void>(
        std::fputs("gtoptTests: private TMPDIR setup failed: ", stderr));
    static_cast<void>(std::fputs(e.what(), stderr));
    static_cast<void>(std::fputs("\n", stderr));
  }

  doctest::Context context(argc, argv);
  const int rc = context.run();

  try {
    gtopt_finish_private_tmpdir(gtopt_tmpdir_storage(), rc);
  } catch (const std::exception& e) {
    // C stdio: std::println could throw inside main()'s handler.
    static_cast<void>(
        std::fputs("gtoptTests: private TMPDIR cleanup failed: ", stderr));
    static_cast<void>(std::fputs(e.what(), stderr));
    static_cast<void>(std::fputs("\n", stderr));
  }
  return rc;
}
