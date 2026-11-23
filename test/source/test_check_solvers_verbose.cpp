/**
 * @file      test_check_solvers_verbose.cpp
 * @brief     run_solver_tests verbose progress output and unknown tests
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `gtopt --check-solvers -v` prints one progress line per self-test; the
 * existing tests only run the quiet path.  Captures stdout and checks
 * that every self-test is announced and reported `ok`.
 */

#include <format>
#include <iostream>
#include <sstream>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/check_solvers.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

/// Redirect std::cout into a buffer for the guard's lifetime.
class CsvCoutCapture
{
public:
  CsvCoutCapture()
      : m_old_(std::cout.rdbuf(m_buf_.rdbuf()))
  {
  }
  ~CsvCoutCapture() { std::cout.rdbuf(m_old_); }
  CsvCoutCapture(const CsvCoutCapture&) = delete;
  CsvCoutCapture(CsvCoutCapture&&) = delete;
  auto operator=(const CsvCoutCapture&) -> CsvCoutCapture& = delete;
  auto operator=(CsvCoutCapture&&) -> CsvCoutCapture& = delete;

  [[nodiscard]] auto str() const -> std::string { return m_buf_.str(); }

private:
  std::ostringstream m_buf_;
  std::streambuf* m_old_;
};

}  // namespace

TEST_CASE("run_solver_tests - verbose prints one line per test")  // NOLINT
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (reg.available_solvers().empty()) {
    MESSAGE("No solver plugins — skipping");
    return;
  }
  const std::string solver {reg.default_solver()};

  SolverTestReport report;
  std::string out;
  {
    const CsvCoutCapture cap;
    report = run_solver_tests(solver, /*verbose=*/true);
    out = cap.str();
  }
  REQUIRE_FALSE(report.results.empty());
  CHECK(report.n_failed() == 0);
  for (const auto& r : report.results) {
    CAPTURE(r.name);
    CHECK(out.find(std::format("[{}/{}]", solver, r.name))
          != std::string::npos);
  }
  CHECK(out.find("ok (") != std::string::npos);
}
