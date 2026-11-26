/**
 * @file      test_solver_registry.hpp
 * @brief     Unit tests for SolverRegistry and classify_error_exit_code
 * @date      2026-03-27
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <atomic>
#include <barrier>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <exception>
#include <filesystem>
#include <format>
#include <fstream>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/solver_backend.hpp>
#include <gtopt/solver_registry.hpp>
#include <spdlog/spdlog.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

#include "solver_test_helpers.hpp"

using namespace gtopt;

// ─── post-fork logger contract (regression guard) ───────────────────────────
//
// `validate_solver_subprocess` forks a child to test-create each plugin's
// backend.  A failing backend ctor logs via `spdlog::error` (log_and_throw)
// before throwing.  The post-fork logger reset MUST leave a valid default
// logger: a previous version called `spdlog::drop_all()` AFTER
// `set_default_logger()`, which nulled the default → the next `spdlog::*()`
// dereferenced null (should_log() on a null logger, si_addr 0x40) and the
// child SIGSEGV'd on every run that validated an unavailable plugin, dumping
// a ~1 GB core each time while the parent test silently passed.
TEST_CASE(
    "SolverRegistry::reset_default_logger_after_fork keeps a valid "
    "default logger")  // NOLINT
{
  using namespace gtopt;

  auto saved = spdlog::default_logger();  // restore afterwards for other tests

  SolverRegistry::reset_default_logger_after_fork();

  // The default logger must be non-null (this is the post-condition that the
  // mis-ordered drop_all() used to violate)...
  CHECK(spdlog::default_logger_raw() != nullptr);

  // ...and logging through it must not crash — this is the exact call
  // (spdlog::error → should_log) that faulted in the fork child.
  spdlog::error("{}", std::string {"reset_default_logger_after_fork guard"});
  CHECK(spdlog::default_logger_raw() != nullptr);  // still valid after logging

  // Idempotent: a second call is also safe and keeps a valid default.
  SolverRegistry::reset_default_logger_after_fork();
  CHECK(spdlog::default_logger_raw() != nullptr);

  spdlog::set_default_logger(std::move(saved));
}

// ─── validation-probe orphan hardening ───────────────────────────────────────
//
// 23 validation children were once found hung for 20 h in futex_wait,
// orphaned (PPID 1) after the parent `gtopt --solvers` was killed.  The
// probe now (a) bounds the parent's wait and kills the child on timeout and
// (b) arms PR_SET_PDEATHSIG in the child.  These tests pin both helpers.

TEST_CASE(
    "SolverRegistry::wait_child_with_timeout reaps a fast child")  // NOLINT
{
  const pid_t pid = ::fork();
  REQUIRE(pid != -1);
  if (pid == 0) {
    ::_exit(7);
  }
  const auto status = SolverRegistry::wait_child_with_timeout(
      pid, std::chrono::milliseconds {1500});
  REQUIRE(status.has_value());
  CHECK(WIFEXITED(status.value_or(0)));
  CHECK(WEXITSTATUS(status.value_or(0)) == 7);
}

TEST_CASE(
    "SolverRegistry::wait_child_with_timeout kills a hung child")  // NOLINT
{
  const pid_t pid = ::fork();
  REQUIRE(pid != -1);
  if (pid == 0) {
    while (true) {
      ::pause();
    }
  }
  const auto t0 = std::chrono::steady_clock::now();
  const auto status = SolverRegistry::wait_child_with_timeout(
      pid, std::chrono::milliseconds {150});
  const auto elapsed = std::chrono::steady_clock::now() - t0;
  CHECK_FALSE(status.has_value());
  CHECK(elapsed >= std::chrono::milliseconds {150});
  CHECK(elapsed < std::chrono::seconds {2});
  // Killed AND reaped: no zombie left behind.
  errno = 0;
  CHECK(::kill(pid, 0) == -1);
  CHECK(errno == ESRCH);
}

TEST_CASE(
    "SolverRegistry::arm_child_parent_death_signal checks the parent "
    "pid")  // NOLINT
{
  const pid_t parent = ::getpid();
  for (const bool match : {true, false}) {
    const pid_t pid = ::fork();
    REQUIRE(pid != -1);
    if (pid == 0) {
      const bool ok = SolverRegistry::arm_child_parent_death_signal(
          match ? parent : parent + 1000000);
      ::_exit(ok ? 0 : 1);
    }
    const auto status = SolverRegistry::wait_child_with_timeout(
        pid, std::chrono::milliseconds {1500});
    REQUIRE(status.has_value());
    CHECK(WIFEXITED(status.value_or(0)));
    CHECK(WEXITSTATUS(status.value_or(0)) == (match ? 0 : 1));
  }
}

TEST_CASE(
    "SolverRegistry::arm_child_parent_death_signal kills the child "
    "when its parent dies")  // NOLINT
{
  // Become a child subreaper so the orphaned grandchild is reparented to
  // this test process (not init) and can be reaped/inspected here.
  int was_subreaper = 0;
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
  REQUIRE(::prctl(PR_GET_CHILD_SUBREAPER, &was_subreaper, 0, 0, 0) == 0);
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
  REQUIRE(::prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0);

  std::array<int, 2> pid_pipe {-1, -1};  // grandchild pid -> test
  std::array<int, 2> armed_pipe {-1, -1};  // grandchild armed -> middle
  REQUIRE(::pipe(pid_pipe.data()) == 0);
  REQUIRE(::pipe(armed_pipe.data()) == 0);

  const pid_t middle = ::fork();
  REQUIRE(middle != -1);
  if (middle == 0) {
    const pid_t me = ::getpid();
    const pid_t grandchild = ::fork();
    if (grandchild == 0) {
      if (!SolverRegistry::arm_child_parent_death_signal(me)) {
        ::_exit(4);
      }
      const pid_t self = ::getpid();
      // ``(void)`` does not silence warn_unused_result under
      // _FORTIFY_SOURCE (g++ Release builds): bind the results instead.
      [[maybe_unused]] const auto n_pid =
          ::write(pid_pipe[1], &self, sizeof(self));
      const char c = 1;
      [[maybe_unused]] const auto n_armed = ::write(armed_pipe[1], &c, 1);
      while (true) {
        ::pause();
      }
    }
    char c = 0;
    [[maybe_unused]] const auto n_read = ::read(armed_pipe[0], &c, 1);
    ::_exit(grandchild > 0 ? 0 : 1);  // parent dies -> PDEATHSIG fires
  }
  ::close(pid_pipe[1]);
  ::close(armed_pipe[0]);
  ::close(armed_pipe[1]);

  const auto mid_status = SolverRegistry::wait_child_with_timeout(
      middle, std::chrono::milliseconds {1500});
  CHECK(mid_status.has_value());

  pid_t grandchild = -1;
  const auto n = ::read(pid_pipe[0], &grandchild, sizeof(grandchild));
  ::close(pid_pipe[0]);
  REQUIRE(n == static_cast<ssize_t>(sizeof(grandchild)));

  const auto gc_status = SolverRegistry::wait_child_with_timeout(
      grandchild, std::chrono::milliseconds {1500});
  REQUIRE(gc_status.has_value());  // died on its own, not via the timeout
  CHECK(WIFSIGNALED(gc_status.value_or(0)));
  CHECK(WTERMSIG(gc_status.value_or(0)) == SIGKILL);

  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
  (void)::prctl(PR_SET_CHILD_SUBREAPER, was_subreaper, 0, 0, 0);
}

// ─── SolverRegistry singleton ───────────────────────────────────────────────

TEST_CASE("SolverRegistry instance returns same object")  // NOLINT
{
  using namespace gtopt;

  auto& reg1 = SolverRegistry::instance();
  auto& reg2 = SolverRegistry::instance();
  CHECK(&reg1 == &reg2);
}

TEST_CASE("SolverRegistry has at least one solver")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  const auto solvers = reg.available_solvers();
  CHECK_FALSE(solvers.empty());
}

TEST_CASE("SolverRegistry has_solver for known solvers")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  // At least one of these should be available in the test environment
  const bool has_any = reg.has_solver("clp") || reg.has_solver("cbc")
      || reg.has_solver("highs") || reg.has_solver("cplex");
  CHECK(has_any);
}

TEST_CASE("SolverRegistry has_solver returns false for unknown")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  CHECK_FALSE(reg.has_solver("nonexistent_solver_xyz"));
}

TEST_CASE("SolverRegistry default_solver returns a valid name")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  const auto name = reg.default_solver();
  CHECK_FALSE(name.empty());
  CHECK(reg.has_solver(name));
}

TEST_CASE("SolverRegistry create produces a backend")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  const auto name = reg.default_solver();
  auto backend = reg.create(name);
  CHECK(backend != nullptr);
}

TEST_CASE("SolverRegistry create throws for unknown solver")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  CHECK_THROWS_AS((void)reg.create("nonexistent_solver_xyz"),
                  std::runtime_error);
}

TEST_CASE("SolverRegistry searched_directories is not empty")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  CHECK_FALSE(reg.searched_directories().empty());
}

TEST_CASE("SolverRegistry load_errors returns a vector")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  // May or may not have errors — just verify it's callable
  (void)reg.load_errors();
}

TEST_CASE(  // NOLINT
    "SolverRegistry discover_plugins with nonexistent dir is safe")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  const auto dirs_before = reg.searched_directories().size();
  reg.discover_plugins("/tmp/nonexistent_plugin_dir_xyz_12345");
  CHECK(reg.searched_directories().size() == dirs_before + 1);
}

TEST_CASE(  // NOLINT
    "SolverRegistry load_plugin with nonexistent file returns false")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  CHECK_FALSE(reg.load_plugin("/tmp/nonexistent_plugin.so"));
  CHECK_FALSE(reg.load_errors().empty());
}

TEST_CASE("SolverRegistry supports_mip per backend")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();

  // Pure-LP backends report false; B&B-capable backends report true.
  if (reg.has_solver("clp")) {
    CHECK_FALSE(reg.supports_mip("clp"));
  }
  if (reg.has_solver("cbc")) {
    CHECK(reg.supports_mip("cbc"));
  }
  if (reg.has_solver("cplex")) {
    CHECK(reg.supports_mip("cplex"));
  }
  if (reg.has_solver("highs")) {
    CHECK(reg.supports_mip("highs"));
  }
  if (reg.has_solver("mindopt")) {
    // supports_mip() maps a license-failed create() to `false`, which is
    // indistinguishable from "LP-only".  Probe creation separately so a
    // transient MindOpt license failure skips instead of failing (see
    // solver_test_helpers.hpp).
    const bool created = solver_test::run_or_skip_license(
        [&] { std::ignore = reg.create("mindopt"); });
    if (created) {
      CHECK(reg.supports_mip("mindopt"));
    } else {
      MESSAGE("mindopt license unavailable — skipping supports_mip probe");
    }
  }

  CHECK_FALSE(reg.supports_mip("nonexistent_solver_xyz"));
}

TEST_CASE("SolverRegistry has_mip_solver returns true when MIP available")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();

  // The test environment ships at least one MIP-capable backend.
  CHECK(reg.has_mip_solver());
}

// ─── SolverRegistry additional coverage ────────────────────────────────────

TEST_CASE(  // NOLINT
    "SolverRegistry create all available solvers")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  const auto solvers = reg.available_solvers();
  for (const auto& name : solvers) {
    CAPTURE(name);
    const bool ran = solver_test::run_or_skip_license(
        [&]
        {
          auto backend = reg.create(name);
          REQUIRE(backend != nullptr);
          // Exercise basic backend accessors
          CHECK_FALSE(backend->solver_name().empty());
          CHECK(backend->infinity() >= 1e20);
        });
    if (!ran) {
      MESSAGE("solver '" << name << "' license unavailable — skipping");
    }
  }
}

TEST_CASE(  // NOLINT
    "SolverRegistry load_plugin with non-so file returns false")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  // Create a temporary text file (not a shared library)
  const auto tmp_path =
      std::filesystem::temp_directory_path() / "fake_plugin.so";
  {
    std::ofstream ofs(tmp_path);
    ofs << "not a shared library";
  }
  const auto errors_before = reg.load_errors().size();
  CHECK_FALSE(reg.load_plugin(tmp_path));
  CHECK(reg.load_errors().size() > errors_before);
  std::filesystem::remove(tmp_path);
}

TEST_CASE(  // NOLINT
    "SolverRegistry discover_plugins with empty dir adds to searched")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  const auto tmp_dir =
      std::filesystem::temp_directory_path() / "gtopt_empty_plugin_dir";
  std::filesystem::create_directories(tmp_dir);
  const auto dirs_before = reg.searched_directories().size();
  reg.discover_plugins(tmp_dir);
  CHECK(reg.searched_directories().size() == dirs_before + 1);
  std::filesystem::remove(tmp_dir);
}

TEST_CASE(  // NOLINT
    "SolverRegistry create throws with descriptive message")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  bool caught = false;
  try {
    (void)reg.create("totally_fake_solver_42");
  } catch (const std::runtime_error& ex) {
    caught = true;
    const std::string msg = ex.what();
    // Message should mention the requested solver name
    CHECK(msg.find("totally_fake_solver_42") != std::string::npos);
    // Message should mention available solvers
    CHECK(msg.find("Available") != std::string::npos);
  }
  CHECK(caught);
}

TEST_CASE(  // NOLINT
    "SolverRegistry default_solver is in available_solvers list")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  const auto def = std::string(reg.default_solver());
  const auto solvers = reg.available_solvers();
  const bool found = std::ranges::find(solvers, def) != solvers.end();
  CHECK(found);
}

TEST_CASE(  // NOLINT
    "SolverRegistry available_solvers returns consistent results")
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  const auto solvers1 = reg.available_solvers();
  const auto solvers2 = reg.available_solvers();
  CHECK(solvers1 == solvers2);
}

// ─── classify_error_exit_code ───────────────────────────────────────────────

TEST_CASE("classify_error_exit_code input errors return 2")  // NOLINT
{
  using namespace gtopt;

  CHECK(classify_error_exit_code("File not found") == 2);
  CHECK(classify_error_exit_code("does not exist") == 2);
  CHECK(classify_error_exit_code("Cannot open file") == 2);
  CHECK(classify_error_exit_code("Failed to parse") == 2);
  CHECK(classify_error_exit_code("Invalid parameter") == 2);
  CHECK(classify_error_exit_code("JSON error") == 2);
}

TEST_CASE("classify_error_exit_code internal errors return 3")  // NOLINT
{
  using namespace gtopt;

  CHECK(classify_error_exit_code("Solver crashed") == 3);
  CHECK(classify_error_exit_code("Segmentation fault") == 3);
  CHECK(classify_error_exit_code("Unknown error") == 3);
  CHECK(classify_error_exit_code("") == 3);
}

// `SolverRegistry::create` used to hold its internal recursive mutex
// across the entire `plugin.create_fn()` call, which serialized the
// `PlanningLP` parallel LP build — every (scene, phase) cell funneled
// through one lock while its backend was constructed.  The refactor
// (source/solver_registry.cpp:443-501) releases the lock after the
// plugin lookup and only then calls `plugin.create_fn`.  This test
// pins the regression: N threads call `create()` simultaneously and
// every thread must get a non-null backend without deadlocking.  We
// don't measure parallelism here (CI boxes have variable core counts
// and plugin startup cost) — we test correctness of the lock release
// under concurrent load, which a regression would break via a
// deadlock if the wrong scope were re-acquired or a data race
// otherwise.
TEST_CASE(
    "SolverRegistry::create is thread-safe under parallel load")  // NOLINT
{
  using namespace gtopt;

  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();

  // The point is to exercise the parallel `create()` path; the identity
  // of the backend doesn't matter.  Prefer a license-free solver: a
  // licensed backend (MindOpt, Gurobi, ...) can refuse `create()` with a
  // license error under parallel load (nightly 2026-11-25: MindOpt
  // `MDOstartenv rc=-10` for 53 of 64 creates while the rest of ctest
  // ran at -j16), which says nothing about the registry's locking.
  const auto solvers = reg.available_solvers();
  REQUIRE_FALSE(solvers.empty());
  const auto pick = [&]() -> std::string
  {
    for (const auto* name : {"clp", "highs", "cbc", "scip"}) {
      if (std::ranges::contains(solvers, std::string {name})) {
        return name;
      }
    }
    return solvers.front();
  };
  const auto solver_name = pick();
  CAPTURE(solver_name);

  constexpr int num_threads = 16;
  constexpr int creates_per_thread = 4;

  std::barrier start_gate(num_threads);
  std::vector<std::thread> workers;
  std::atomic<int> success_count {0};
  std::atomic<int> null_count {0};
  std::atomic<int> exception_count {0};
  std::atomic<int> license_count {0};

  workers.reserve(num_threads);
  for (int i = 0; i < num_threads; ++i) {
    workers.emplace_back(
        [&]
        {
          // All threads start their first `create()` call together so
          // they race through the mutex at the same time.  A pre-fix
          // regression would still complete (it would just serialize
          // on the lock), so this is a correctness — not a timing —
          // test.
          start_gate.arrive_and_wait();
          for (int j = 0; j < creates_per_thread; ++j) {
            try {
              auto backend = reg.create(solver_name);
              if (backend) {
                success_count.fetch_add(1, std::memory_order_relaxed);
              } else {
                null_count.fetch_add(1, std::memory_order_relaxed);
              }
            } catch (const std::exception& e) {
              // Only a licensed fallback backend (no open-source solver
              // loaded) can hit this; a vendor license refusal is
              // environmental, any other exception is a registry bug.
              if (gtopt::solver_test::is_license_failure(e)) {
                license_count.fetch_add(1, std::memory_order_relaxed);
              } else {
                exception_count.fetch_add(1, std::memory_order_relaxed);
              }
            } catch (...) {
              exception_count.fetch_add(1, std::memory_order_relaxed);
            }
          }
        });
  }
  for (auto& w : workers) {
    w.join();
  }

  if (license_count.load() > 0) {
    MESSAGE("'" << solver_name << "' license unavailable for "
                << license_count.load() << " create() call(s) — skipped");
  }

  // Every create() call must succeed (or be a license skip) — no other
  // exceptions, no null backends, no deadlocks (join would hang).
  CHECK(success_count.load() + license_count.load()
        == num_threads * creates_per_thread);
  CHECK(null_count.load() == 0);
  CHECK(exception_count.load() == 0);
}

TEST_CASE(  // NOLINT
    "insecure_plugin_location refuses world-writable plugins")
{
  using namespace gtopt;
  namespace fs = std::filesystem;

  const auto root = fs::temp_directory_path()
      / std::format("gtopt_plugin_perm_{}", ::getpid());
  fs::remove_all(root);
  fs::create_directories(root / "dir");
  fs::permissions(root, fs::perms::owner_all, fs::perm_options::replace);
  fs::permissions(
      root / "dir", fs::perms::owner_all, fs::perm_options::replace);
  const auto so = root / "dir" / "libgtopt_solver_fake.so";
  {
    std::ofstream ofs(so);
    ofs << "x";
  }
  fs::permissions(so,
                  fs::perms::owner_read | fs::perms::owner_write,
                  fs::perm_options::replace);

  CHECK(insecure_plugin_location(so).empty());
  CHECK(insecure_plugin_location(root / "dir" / "missing.so").empty());

  fs::permissions(so, fs::perms::others_write, fs::perm_options::add);
  CHECK(insecure_plugin_location(so).find("file") != std::string::npos);

  auto& reg = SolverRegistry::instance();
  const auto errors_before = reg.load_errors().size();
  CHECK_FALSE(reg.load_plugin(so));
  REQUIRE(reg.load_errors().size() > errors_before);
  CHECK(reg.load_errors().back().find("world-writable") != std::string::npos);

  fs::permissions(so, fs::perms::others_write, fs::perm_options::remove);
  fs::permissions(root / "dir", fs::perms::others_all, fs::perm_options::add);
  CHECK(insecure_plugin_location(so).find("directory") != std::string::npos);

  fs::remove_all(root);
}
