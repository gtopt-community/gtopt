// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial

// Standard library
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

// Third-party
#include <spdlog/spdlog.h>

// Project headers
#include <gtopt/cpu_monitor.hpp>
#include <gtopt/sddp_pool.hpp>  // SDDPTaskKey — for explicit instantiation
#include <gtopt/work_pool.hpp>
#include <gtopt/work_pool_diagnostics.hpp>

namespace gtopt
{

// ─── BasicWorkPool::log_periodic_stats (out-of-line) ─────────────────────────
//
// Moved out of work_pool.hpp so the logging / format-string code can
// be edited without recompiling every translation unit that includes
// the header.  Three explicit instantiations at the bottom of this
// file cover every concrete pool in use (`int64_t`, `std::string`,
// `SDDPTaskKey`).
template<typename Key, typename KeyCompare>
void BasicWorkPool<Key, KeyCompare>::log_periodic_stats()
{
  try {
    const auto stats = get_statistics();
    // Only include swap fields in the line when they have signal — keeps
    // the common case compact while making thrash visible when it starts.
    const auto swap_tail =
        (stats.process_swap_mb > 0.0 || stats.swap_io_rate > 0.0)
        ? std::format("  Swap: {:.0f} MB ({:.0f} pg/s)",
                      stats.process_swap_mb,
                      stats.swap_io_rate)
        : std::string {};
    spdlog::info(
        "[{}] CPU: {:.1f}%  MEM: {:.0f} MB free ({:.1f}%)  "
        "RSS: {:.0f} MB{}  Active: {}/{}  Pending: {}  Done: {}",
        name_,
        stats.current_cpu_load,
        stats.available_memory_mb,
        stats.current_memory_percent,
        stats.process_rss_mb,
        swap_tail,
        stats.active_threads,
        max_threads_.load(std::memory_order_relaxed),
        stats.tasks_pending,
        stats.tasks_completed);

    // Stall detection: when `tasks_completed` has not advanced since
    // the previous periodic log AND there is *pending* work that
    // could dispatch but isn't, surface the gate that is blocking
    // progress.  Mirrors the checks in `can_dispatch_next()` so the
    // user sees the same condition the scheduler sees.
    //
    // **Why the criterion requires ``pending > 0``** (not ``active >
    // 0``).  Tasks in this pool are coarse-grained — e.g. a per-scene
    // SDDP simulation pass that walks 51 phases × 14 s each ≈ 12 min
    // per scene.  With pool granularity at the *scene* level,
    // ``tasks_completed`` only advances every 12 min even when each
    // scene is making steady internal progress (visible in trace logs
    // as ``SystemLP::write_out [scene=N phase=M]`` lines).  The old
    // criterion ``has_work = pending > 0 || active > 0`` fired the
    // warning every 30 s × 2 intervals during that healthy 12 min
    // window — false positives on the large hydrothermal case single-bus runs.
    //
    // Restricting the stall signal to ``pending > 0`` only flags the
    // case where work is *queued* but the scheduler isn't picking it
    // up — i.e. a true dispatch deadlock or a throttle gate that
    // should be visible in the diagnostic ``reason`` block below.
    // Trade-off: the rare case where every active task simultaneously
    // wedges in a futex with nothing pending will no longer trigger
    // the warning.  That scenario is covered by gdb thread dumps when
    // it surfaces; the more common false positive on long
    // monolithic tasks is what users actually see.
    stall_tracker_.observe(stats.tasks_pending, stats.tasks_completed);

    if (stall_tracker_.intervals >= StallTracker::warn_after) {
      const std::string reason = describe_stall_reason(
          StallGateSnapshot {
              .memory_percent = stats.current_memory_percent,
              .available_memory_mb = stats.available_memory_mb,
              .process_rss_mb = stats.process_rss_mb,
              .process_swap_mb = stats.process_swap_mb,
              .swap_io_rate = stats.swap_io_rate,
              .cpu_load = stats.current_cpu_load,
              .active_threads = stats.active_threads,
              .swap_gate_engaged = stats.swap_gate_engaged,
          },
          StallGateLimits {
              .max_memory_percent = max_memory_percent_,
              .min_free_memory_mb = min_free_memory_mb_,
              .max_process_rss_mb = max_process_rss_mb_,
              .max_process_swap_mb = max_process_swap_mb_,
              .max_swap_io_per_sec = max_swap_io_per_sec_,
              .max_cpu_threshold = max_cpu_threshold_,
              .max_threads = max_threads_.load(std::memory_order_relaxed),
          });
      SPDLOG_WARN(
          "[{}] no progress for {} intervals ({} pending, {} active): {}",
          name_,
          stall_tracker_.intervals,
          stats.tasks_pending,
          stats.tasks_active,
          reason);

      // Deep instrumentation for memory-limit stall debugging.  Logs
      // a per-gate snapshot at stall detection so future repros can
      // be diagnosed without re-running with trace-level.  Each gate
      // value is paired with its threshold so the operator sees
      // exactly which one is blocking (or whether none are, which
      // means workers exited).  Throttle counters since pool start
      // also surface here.
      const auto throttled_cpu = throttled_cpu_.load();
      const auto throttled_mem_pct = throttled_memory_pct_.load();
      const auto throttled_free_mem = throttled_free_memory_.load();
      const auto throttled_rss = throttled_process_rss_.load();
      const auto throttled_swap = throttled_process_swap_.load();
      const auto throttled_swap_io = throttled_swap_io_.load();
      std::size_t workers_alive = 0;
      {
        const std::scoped_lock<std::mutex> qlock(queue_mutex_);
        workers_alive = workers_.size();
      }
      spdlog::warn(
          "[{}] stall diag — workers_alive={} max_threads={} "
          "active_threads={} mem_pct={:.1f}%/{}% free_mb={:.0f}/{:.0f} "
          "rss_mb={:.0f}/{:.0f} swap_mb={:.0f}/{:.0f}({}) "
          "swap_io={:.0f}/{:.0f} "
          "cpu_load={:.1f}%/{:.1f}% "
          "throttle_counters[cpu={} mem%={} free={} rss={} swap={} sw_io={}]",
          name_,
          workers_alive,
          max_threads_.load(std::memory_order_relaxed),
          stats.active_threads,
          stats.current_memory_percent,
          max_memory_percent_,
          stats.available_memory_mb,
          min_free_memory_mb_,
          stats.process_rss_mb,
          max_process_rss_mb_,
          stats.process_swap_mb,
          max_process_swap_mb_,
          stats.swap_gate_engaged ? "gate engaged" : "gate open",
          stats.swap_io_rate,
          max_swap_io_per_sec_,
          stats.current_cpu_load,
          max_cpu_threshold_,
          throttled_cpu,
          throttled_mem_pct,
          throttled_free_mem,
          throttled_rss,
          throttled_swap,
          throttled_swap_io);

      // Recovery nudge — wake any workers parked on the gate
      // back-off (cv_.wait_for at scheduler_interval_ in worker_loop)
      // and replace workers that may have exited.  Required because
      // workers parked on the back-off wait_for only wake on
      // `stop_requested` / `!running_`; they don't re-check memory
      // gates without a notify.  Once memory drops (the stats above
      // confirm it has — e.g. the large hydrothermal case at 91% free), this
      // nudge re-engages the pool.  Symmetric to the implicit nudge on each
      // task completion at worker_loop's `cv_.notify_all()`, but fires from the
      // monitor thread when no task has completed in the stall window.
      std::size_t workers_before = 0;
      std::size_t workers_after = 0;
      {
        const std::scoped_lock<std::mutex> qlock(queue_mutex_);
        workers_before = workers_.size();
        maybe_spawn_worker_unlocked();
        workers_after = workers_.size();
      }
      cv_.notify_all();
      spdlog::warn(
          "[{}] stall recovery nudge fired — workers {}→{} (spawned={}), "
          "cv_.notify_all() called",
          name_,
          workers_before,
          workers_after,
          workers_after - workers_before);
    }
  } catch (const std::exception& e) {
    SPDLOG_WARN("log_periodic_stats failed: {}", e.what());
  }
}

// ─── BasicWorkPool::log_final_stats (out-of-line) ────────────────────────────
template<typename Key, typename KeyCompare>
void BasicWorkPool<Key, KeyCompare>::log_final_stats() const
{
  try {
    const auto stats = get_statistics();
    // Skip the noisy "Final: 0 tasks dispatched, 0 completed ..." that
    // otherwise emits whenever a pool is constructed for a code path
    // (hot-start cut load, monitoring init, ...) that ends up not
    // dispatching any work.
    if (stats.lp_tasks_dispatched == 0 && stats.tasks_completed == 0) {
      return;
    }
    const auto elapsed =
        std::chrono::duration<double>(std::chrono::steady_clock::now()
                                      - pool_start_time_)
            .count();
    spdlog::info(
        "[{}] Final: {} tasks dispatched, {} completed, "
        "avg CPU {:.1f}%, avg mem delta {:.1f} MB, wall {:.1f}s",
        name_,
        stats.lp_tasks_dispatched,
        stats.tasks_completed,
        stats.avg_task_cpu_pct,
        stats.avg_task_rss_delta_mb,
        elapsed);

    // Throttle summary — only emit when at least one gate fired so a
    // healthy pool stays silent.  Operators use this to diagnose
    // "why is my pool only at 50% CPU?" without re-running with
    // DEBUG logs.  Each counter is the number of schedule ticks on
    // which that gate blocked dispatch.
    const ThrottleCounts throttle {
        .cpu = stats.throttled_cpu,
        .memory_pct = stats.throttled_memory_pct,
        .free_memory = stats.throttled_free_memory,
        .process_rss = stats.throttled_process_rss,
        .process_swap = stats.throttled_process_swap,
        .swap_io = stats.throttled_swap_io,
    };
    if (const auto total_throttle = throttle.total(); total_throttle > 0) {
      // Compact form: only emit gates that actually fired, so an
      // operator sees at a glance which gate held the pool back while
      // a swap-IO bottleneck stays distinguishable from a free-memory
      // one when several gates fire (see format_throttle_summary).
      spdlog::info("[{}]   throttle: {} (total={})",
                   name_,
                   format_throttle_summary(throttle),
                   total_throttle);
    }
  } catch (const std::exception& e) {
    SPDLOG_WARN("log_final_stats failed: {}", e.what());
  }
}

// ─── Pure diagnostics helpers (work_pool_diagnostics.hpp) ────────────────────

std::string describe_stall_reason(const StallGateSnapshot& s,
                                  const StallGateLimits& lim)
{
  if (s.memory_percent >= lim.max_memory_percent) {
    return std::format("memory usage {:.1f}% >= {:.1f}%",
                       s.memory_percent,
                       lim.max_memory_percent);
  }
  if (s.available_memory_mb < lim.min_free_memory_mb
      && s.available_memory_mb > 0.0)
  {
    return std::format("free memory {:.0f} MB < {:.0f} MB",
                       s.available_memory_mb,
                       lim.min_free_memory_mb);
  }
  if (lim.max_process_rss_mb > 0.0
      && s.process_rss_mb >= lim.max_process_rss_mb)
  {
    return std::format("process RSS {:.0f} MB >= {:.0f} MB",
                       s.process_rss_mb,
                       lim.max_process_rss_mb);
  }
  if (s.swap_gate_engaged) {
    // Only blame the VmSwap gate while it is actually closed (over the
    // limit AND growing).  A large static VmSwap does not block
    // dispatch, so reporting it here would misdirect the operator.
    return std::format("process VmSwap {:.0f} MB >= {:.0f} MB and growing",
                       s.process_swap_mb,
                       lim.max_process_swap_mb);
  }
  if (lim.max_swap_io_per_sec > 0.0
      && s.swap_io_rate >= lim.max_swap_io_per_sec)
  {
    return std::format("swap thrashing {:.0f} pg/s >= {:.0f} pg/s",
                       s.swap_io_rate,
                       lim.max_swap_io_per_sec);
  }
  if (s.active_threads + 1 >= static_cast<int>(lim.max_threads * 0.8)
      && s.cpu_load >= lim.max_cpu_threshold)
  {
    return std::format(
        "CPU load {:.1f}% >= {:.1f}%", s.cpu_load, lim.max_cpu_threshold);
  }
  if (s.swap_io_rate > 0.0) {
    return std::format(
        "active task(s) not completing (kernel paging "
        "{:.0f} pg/s — likely thrash)",
        s.swap_io_rate);
  }
  return "active task(s) not completing (external block or blocked I/O)";
}

std::string format_throttle_summary(const ThrottleCounts& c)
{
  std::string parts;
  const auto add = [&](std::string_view label, std::size_t v)
  {
    if (v > 0) {
      if (!parts.empty()) {
        parts.append(" ");
      }
      parts.append(std::format("{}={}", label, v));
    }
  };
  add("cpu", c.cpu);
  add("mem%", c.memory_pct);
  add("free_mem", c.free_memory);
  add("rss", c.process_rss);
  add("swap", c.process_swap);
  add("swap_io", c.swap_io);
  return parts;
}

// ─── Explicit instantiations ─────────────────────────────────────────────────
//
// These three are the only concrete `BasicWorkPool` types in the
// codebase.  Adding a new key type means adding a new explicit
// instantiation here (otherwise the out-of-line members above will
// fail to link).
template void
BasicWorkPool<std::int64_t, std::less<std::int64_t>>::log_periodic_stats();
template void
BasicWorkPool<std::int64_t, std::less<std::int64_t>>::log_final_stats() const;

template void
BasicWorkPool<std::string, std::less<std::string>>::log_periodic_stats();
template void
BasicWorkPool<std::string, std::less<std::string>>::log_final_stats() const;

template void
BasicWorkPool<SDDPTaskKey, std::less<SDDPTaskKey>>::log_periodic_stats();
template void
BasicWorkPool<SDDPTaskKey, std::less<SDDPTaskKey>>::log_final_stats() const;

}  // namespace gtopt
