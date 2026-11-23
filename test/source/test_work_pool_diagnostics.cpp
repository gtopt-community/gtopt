/**
 * @file      test_work_pool_diagnostics.cpp
 * @brief     Unit tests for the WorkPool stall / throttle diagnostics
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <string>

#include <doctest/doctest.h>
#include <gtopt/work_pool_diagnostics.hpp>

using namespace gtopt;

namespace
{

/// Limits with every gate open for a healthy snapshot.
constexpr StallGateLimits k_limits {
    .max_memory_percent = 90.0,
    .min_free_memory_mb = 4096.0,
    .max_process_rss_mb = 10000.0,
    .max_process_swap_mb = 2000.0,
    .max_swap_io_per_sec = 500.0,
    .max_cpu_threshold = 95.0,
    .max_threads = 10,
};

constexpr StallGateSnapshot k_healthy {
    .memory_percent = 40.0,
    .available_memory_mb = 64000.0,
    .process_rss_mb = 2000.0,
    .process_swap_mb = 0.0,
    .swap_io_rate = 0.0,
    .cpu_load = 30.0,
    .active_threads = 2,
    .swap_gate_engaged = false,
};

}  // namespace

TEST_CASE("StallTracker counts consecutive no-progress intervals")  // NOLINT
{
  StallTracker t;
  SUBCASE("pending work without completions accumulates")
  {
    CHECK(t.observe(3, 0) == 1);
    CHECK(t.observe(3, 0) == 2);
    CHECK(t.observe(3, 0) == 3);
    CHECK(t.intervals >= StallTracker::warn_after);
  }
  SUBCASE("progress resets the counter")
  {
    CHECK(t.observe(3, 0) == 1);
    CHECK(t.observe(3, 5) == 0);
    CHECK(t.last_completed == 5);
    CHECK(t.observe(3, 5) == 1);
  }
  SUBCASE("nothing pending is never a stall (long active tasks)")
  {
    CHECK(t.observe(0, 0) == 0);
    CHECK(t.observe(0, 0) == 0);
  }
}

TEST_CASE("describe_stall_reason picks gates in scheduler order")  // NOLINT
{
  SUBCASE("memory percent is checked first")
  {
    auto s = k_healthy;
    s.memory_percent = 95.0;
    s.available_memory_mb = 100.0;  // also failing, but lower priority
    s.cpu_load = 99.0;
    CHECK(describe_stall_reason(s, k_limits) == "memory usage 95.0% >= 90.0%");
  }
  SUBCASE("free memory below the floor")
  {
    auto s = k_healthy;
    s.available_memory_mb = 1024.0;
    CHECK(describe_stall_reason(s, k_limits)
          == "free memory 1024 MB < 4096 MB");
  }
  SUBCASE("zero free memory means 'no reading', not a gate")
  {
    auto s = k_healthy;
    s.available_memory_mb = 0.0;
    CHECK(describe_stall_reason(s, k_limits).starts_with("active task(s)"));
  }
  SUBCASE("process RSS over the cap")
  {
    auto s = k_healthy;
    s.process_rss_mb = 12000.0;
    CHECK(describe_stall_reason(s, k_limits)
          == "process RSS 12000 MB >= 10000 MB");
  }
  SUBCASE("RSS cap of 0 disables the RSS gate")
  {
    auto s = k_healthy;
    s.process_rss_mb = 1e9;
    auto lim = k_limits;
    lim.max_process_rss_mb = 0.0;
    CHECK(describe_stall_reason(s, lim).starts_with("active task(s)"));
  }
  SUBCASE("VmSwap gate only when engaged")
  {
    auto s = k_healthy;
    s.process_swap_mb = 3000.0;
    CHECK(describe_stall_reason(s, k_limits).starts_with("active task(s)"));
    s.swap_gate_engaged = true;
    CHECK(describe_stall_reason(s, k_limits)
          == "process VmSwap 3000 MB >= 2000 MB and growing");
  }
  SUBCASE("swap I/O thrashing over the limit")
  {
    auto s = k_healthy;
    s.swap_io_rate = 800.0;
    CHECK(describe_stall_reason(s, k_limits)
          == "swap thrashing 800 pg/s >= 500 pg/s");
  }
  SUBCASE("CPU gate needs both a near-full pool and high load")
  {
    auto s = k_healthy;
    s.cpu_load = 99.0;
    s.active_threads = 2;  // 2 + 1 < 0.8 * 10
    CHECK(describe_stall_reason(s, k_limits).starts_with("active task(s)"));
    s.active_threads = 7;  // 7 + 1 >= 8
    CHECK(describe_stall_reason(s, k_limits) == "CPU load 99.0% >= 95.0%");
  }
  SUBCASE("paging below the swap-I/O limit is reported as likely thrash")
  {
    auto s = k_healthy;
    s.swap_io_rate = 120.0;
    CHECK(describe_stall_reason(s, k_limits)
          == "active task(s) not completing (kernel paging 120 pg/s — "
             "likely thrash)");
  }
  SUBCASE("no gate and no paging: external block")
  {
    CHECK(describe_stall_reason(k_healthy, k_limits)
          == "active task(s) not completing (external block or blocked "
             "I/O)");
  }
}

TEST_CASE("format_throttle_summary lists only the gates that fired")  // NOLINT
{
  CHECK(format_throttle_summary({}).empty());
  CHECK(ThrottleCounts {}.total() == 0);

  const ThrottleCounts only_cpu {.cpu = 35876};
  CHECK(format_throttle_summary(only_cpu) == "cpu=35876");

  const ThrottleCounts all {
      .cpu = 1,
      .memory_pct = 2,
      .free_memory = 3,
      .process_rss = 4,
      .process_swap = 5,
      .swap_io = 6,
  };
  CHECK(format_throttle_summary(all)
        == "cpu=1 mem%=2 free_mem=3 rss=4 swap=5 swap_io=6");
  CHECK(all.total() == 21);

  const ThrottleCounts sparse {.free_memory = 7, .swap_io = 2};
  CHECK(format_throttle_summary(sparse) == "free_mem=7 swap_io=2");
}
