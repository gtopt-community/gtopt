/**
 * @file      test_check_lp_spawn.cpp
 * @brief     run_check_lp_diagnostic / run_check_json_info with fake tools
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_check_lp.cpp` only sees the "tool not on PATH" branch on hosts
 * without the Python tools installed.  Here PATH points at a temp dir
 * holding fake `gtopt_check_lp` / `gtopt_check_json` scripts so the
 * posix_spawn + pipe-capture path, the argument forwarding, the optional
 * `timeout` wrapper and the 64 KB output cap all run deterministically.
 * Helpers are prefixed `clsp_` for unity builds.
 */

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <random>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/check_lp.hpp>
#include <gtopt/solver_options.hpp>

using namespace gtopt;

namespace
{

class ClspPathSandbox
{
public:
  ClspPathSandbox()
      : m_root_(std::filesystem::temp_directory_path()
                / ("gtopt_clsp_"
                   + std::to_string(
                       static_cast<std::uint64_t>(std::random_device {}()))))
  {
    std::filesystem::create_directories(bin());
    if (const char* old = std::getenv("PATH"); old != nullptr) {  // NOLINT
      m_old_path_ = old;
    }
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    ::setenv("PATH", bin().c_str(), 1);
  }

  ~ClspPathSandbox()
  {
    if (m_old_path_) {
      // NOLINTNEXTLINE(concurrency-mt-unsafe)
      ::setenv("PATH", m_old_path_->c_str(), 1);
    } else {
      // NOLINTNEXTLINE(concurrency-mt-unsafe)
      ::unsetenv("PATH");
    }
    std::error_code ec;
    std::filesystem::remove_all(m_root_, ec);
  }

  ClspPathSandbox(const ClspPathSandbox&) = delete;
  ClspPathSandbox(ClspPathSandbox&&) = delete;
  auto operator=(const ClspPathSandbox&) -> ClspPathSandbox& = delete;
  auto operator=(ClspPathSandbox&&) -> ClspPathSandbox& = delete;

  [[nodiscard]] auto bin() const -> std::filesystem::path
  {
    return m_root_ / "bin";
  }

  void tool(const std::string& name, const std::string& body) const
  {
    const auto p = bin() / name;
    {
      std::ofstream out(p);
      out << "#!/bin/sh\n" << body << "\n";
    }
    std::filesystem::permissions(p,
                                 std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace);
  }

  /// Expose the host `timeout` utility inside the sandbox (if any).
  [[nodiscard]] bool link_timeout() const
  {
    for (const char* dir : {"/usr/bin", "/bin"}) {
      const auto src = std::filesystem::path(dir) / "timeout";
      if (std::filesystem::exists(src)) {
        std::error_code ec;
        std::filesystem::create_symlink(src, bin() / "timeout", ec);
        return !ec;
      }
    }
    return false;
  }

private:
  std::filesystem::path m_root_;
  std::optional<std::string> m_old_path_;
};

}  // namespace

TEST_CASE("run_check_lp_diagnostic forwards solver options")  // NOLINT
{
  ClspPathSandbox sb;
  sb.tool("gtopt_check_lp", R"(echo "ARGS: $*"; echo "stderr line" 1>&2)");

  SUBCASE("defaults, extension appended")
  {
    const auto out = run_check_lp_diagnostic("/x/model", 0);
    CHECK(out.find("--quiet --no-color --no-ai --timeout 0")
          != std::string::npos);
    CHECK(out.find("/x/model.lp") != std::string::npos);
    // stderr is captured too.
    CHECK(out.find("stderr line") != std::string::npos);
  }

  SUBCASE("explicit tolerances and algorithm")
  {
    SolverOptions opts;
    opts.algorithm = LPAlgo::dual;
    opts.optimal_eps = 1e-7;
    opts.feasible_eps = 1e-6;
    opts.barrier_eps = 1e-8;
    const auto out = run_check_lp_diagnostic("/x/model.mps", 0, opts);
    CHECK(out.find("--algo dual") != std::string::npos);
    CHECK(out.find("--optimal-eps 1e-07") != std::string::npos);
    CHECK(out.find("--feasible-eps 1e-06") != std::string::npos);
    CHECK(out.find("--barrier-eps 1e-08") != std::string::npos);
    // An explicit extension is kept as is.
    CHECK(out.find("/x/model.mps") != std::string::npos);
    CHECK(out.find("model.mps.lp") == std::string::npos);
  }

  SUBCASE("timeout wrapper when the utility is on PATH")
  {
    if (sb.link_timeout()) {
      const auto out = run_check_lp_diagnostic("/x/model.lp", 5);
      CHECK(out.find("--timeout 5") != std::string::npos);
    }
  }
}

TEST_CASE("run_check_lp_diagnostic caps captured output")  // NOLINT
{
  ClspPathSandbox sb;
  // ~100 KB of output: the reader stops after 64 KB and marks the cut.
  sb.tool("gtopt_check_lp",
          R"(i=0; while [ $i -lt 2000 ]; do
echo "0123456789012345678901234567890123456789012345678901234567890"; i=$((i+1)); done)");
  const auto out = run_check_lp_diagnostic("/x/model.lp", 0);
  CHECK(out.find("diagnostic output truncated") != std::string::npos);
  CHECK(out.size() < 80UZ * 1024UZ);
}

TEST_CASE("run_check_lp_diagnostic with a failing tool")  // NOLINT
{
  ClspPathSandbox sb;
  sb.tool("gtopt_check_lp", "echo partial; exit 2");
  // Output is still returned even though the tool failed.
  CHECK(run_check_lp_diagnostic("/x/model.lp", 0).find("partial")
        != std::string::npos);
}

TEST_CASE("run_check_lp_diagnostic without the tool")  // NOLINT
{
  const ClspPathSandbox sb;
  CHECK(run_check_lp_diagnostic("/x/model.lp", 0).empty());
}

TEST_CASE("run_check_json_info forwards json files")  // NOLINT
{
  ClspPathSandbox sb;
  sb.tool("gtopt_check_json", R"(echo "ARGS: $*")");

  CHECK(run_check_json_info({}, 0).empty());

  const auto out = run_check_json_info({"/a/case", "/b/other.json"}, 0);
  CHECK(out.find("--info --no-color") != std::string::npos);
  CHECK(out.find("/a/case.json") != std::string::npos);
  CHECK(out.find("/b/other.json") != std::string::npos);
}

TEST_CASE("log_diagnostic_lines truncates long diagnostics")  // NOLINT
{
  std::string diag;
  for (int i = 0; i < 500; ++i) {
    diag += "line " + std::to_string(i) + "\n";
  }
  // Both levels; must not throw.
  CHECK_NOTHROW(log_diagnostic_lines("error", "hdr", diag));
  CHECK_NOTHROW(log_diagnostic_lines("info", "hdr", diag));
  CHECK_NOTHROW(log_diagnostic_lines("info", "hdr", "one\n\ntwo"));
}
