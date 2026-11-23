/**
 * @file      work_pool_diagnostics.hpp
 * @brief     Pure helpers behind the WorkPool periodic / final log lines
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `BasicWorkPool::log_periodic_stats()` and `log_final_stats()` run on
 * the monitor thread and depend on live CPU / memory readings, so their
 * decision logic (stall detection, which dispatch gate to blame, the
 * compact throttle summary) was only reachable by actually wedging a
 * pool.  These free functions hold that logic so it can be unit tested
 * with synthetic snapshots; the pool feeds them its live statistics.
 */

#pragma once

#include <cstddef>
#include <string>

namespace gtopt
{

/// Stall detector: counts consecutive periodic-log intervals in which
/// work was queued (`pending > 0`) but `tasks_completed` did not advance.
struct StallTracker
{
  std::size_t last_completed {0};
  int intervals {0};

  /// Record one periodic observation and return the updated number of
  /// consecutive no-progress intervals (0 when progress was made or
  /// nothing is pending).
  int observe(std::size_t pending, std::size_t completed) noexcept
  {
    if (pending > 0 && completed == last_completed) {
      ++intervals;
    } else {
      intervals = 0;
    }
    last_completed = completed;
    return intervals;
  }

  /// Intervals without progress before the stall warning fires.
  static constexpr int warn_after = 2;
};

/// Live readings the stall diagnosis looks at.
struct StallGateSnapshot
{
  double memory_percent {0.0};
  double available_memory_mb {0.0};
  double process_rss_mb {0.0};
  double process_swap_mb {0.0};
  double swap_io_rate {0.0};
  double cpu_load {0.0};
  int active_threads {0};
  bool swap_gate_engaged {false};
};

/// Configured dispatch-gate thresholds of the pool.
struct StallGateLimits
{
  double max_memory_percent {0.0};
  double min_free_memory_mb {0.0};
  double max_process_rss_mb {0.0};
  double max_process_swap_mb {0.0};
  double max_swap_io_per_sec {0.0};
  double max_cpu_threshold {0.0};
  int max_threads {0};
};

/// Human-readable name of the gate most likely blocking dispatch, in
/// the same priority order as the scheduler's `can_dispatch_next()`:
/// memory %, free memory, process RSS, VmSwap gate, swap I/O, CPU, then
/// the "tasks not completing" fallbacks.
[[nodiscard]] std::string describe_stall_reason(const StallGateSnapshot& s,
                                                const StallGateLimits& lim);

/// Per-gate throttle counters (ticks on which each gate blocked).
struct ThrottleCounts
{
  std::size_t cpu {0};
  std::size_t memory_pct {0};
  std::size_t free_memory {0};
  std::size_t process_rss {0};
  std::size_t process_swap {0};
  std::size_t swap_io {0};

  [[nodiscard]] constexpr std::size_t total() const noexcept
  {
    return cpu + memory_pct + free_memory + process_rss + process_swap
        + swap_io;
  }
};

/// Compact `label=count` list of the gates that fired (zero counters
/// omitted), e.g. `"cpu=12 swap_io=3"`; empty when none fired.
[[nodiscard]] std::string format_throttle_summary(const ThrottleCounts& c);

}  // namespace gtopt
