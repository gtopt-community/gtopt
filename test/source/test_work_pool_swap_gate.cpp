// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_work_pool_swap_gate.cpp
 * @brief     Work-pool VmSwap gate: throttle only while swap is GROWING
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Regression suite for the production stall where a process with a large
 * but STATIC VmSwap (cold pages parked on swap, 0 pg/s swap I/O) pinned
 * the pool at `Active: 1/31` for over an hour because the gate tested
 * the VmSwap level instead of its growth.
 *
 * Two layers, both driven by an injected stats source (no real swap):
 *   - `SwapGrowthGate` — the pure state machine, fed `(VmSwap, time)`.
 *   - `AdaptiveWorkPool` — end-to-end through
 *     `WorkPoolConfig::process_swap_source`.
 */

#include <atomic>
#include <chrono>
#include <future>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/json/json_sddp_options.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/work_pool.hpp>

using namespace gtopt;

namespace
{

using SwapGate = SwapGrowthGate;
using SwapTransition = SwapGrowthGate::Transition;
using namespace std::chrono_literals;

constexpr double swap_gate_limit_mb = 2048.0;
constexpr double swap_gate_delta_mb = 64.0;
constexpr auto swap_gate_window = 5s;
constexpr auto swap_gate_sample = 500ms;  // memory-monitor cadence

/// Feeds a `SwapGrowthGate` at a fixed cadence and tallies transitions.
struct SwapGateDriver
{
  SwapGate gate {swap_gate_limit_mb, swap_gate_delta_mb, swap_gate_window};
  SwapGate::time_point now {SwapGate::time_point {} + 1h};
  int engaged_events {0};
  int released_events {0};

  /// One sample `dt` after the previous one.
  void feed(double vmswap_mb, SwapGate::duration dt = swap_gate_sample)
  {
    now += dt;
    switch (gate.update(vmswap_mb, now)) {
      case SwapTransition::engaged:
        ++engaged_events;
        break;
      case SwapTransition::released:
        ++released_events;
        break;
      case SwapTransition::none:
        break;
    }
  }

  /// `count` samples at a constant VmSwap.
  void hold(double vmswap_mb, int count)
  {
    for (int i = 0; i < count; ++i) {
      feed(vmswap_mb);
    }
  }

  /// `count` samples, VmSwap rising `step_mb` per sample from `start_mb`.
  /// Returns the last value fed.
  double ramp(double start_mb, double step_mb, int count)
  {
    double v = start_mb;
    for (int i = 0; i < count; ++i) {
      v += step_mb;
      feed(v);
    }
    return v;
  }
};

/// Restores the built-in process-wide swap-gate defaults on scope exit so
/// a test that overrides them cannot leak into the rest of the binary.
struct SwapDefaultsGuard
{
  SwapDefaultsGuard() = default;
  SwapDefaultsGuard(const SwapDefaultsGuard&) = delete;
  SwapDefaultsGuard(SwapDefaultsGuard&&) = delete;
  SwapDefaultsGuard& operator=(const SwapDefaultsGuard&) = delete;
  SwapDefaultsGuard& operator=(SwapDefaultsGuard&&) = delete;
  ~SwapDefaultsGuard() { reset_default_swap_gate(); }
};

/// Pool config with every NON-swap gate neutralised (CPU, memory %, free
/// memory, RSS, swap I/O) so the only thing that can hold a task back is
/// the VmSwap gate under test — the suite must stay deterministic on a
/// heavily loaded CI host.
[[nodiscard]] WorkPoolConfig make_swap_gate_pool_config(
    std::string name, double max_process_swap_mb)
{
  WorkPoolConfig cfg {
      4,  // max_threads
      1000.0,  // max_cpu_threshold (unreachable)
      0.0,  // min_free_memory_mb (off)
      1000.0,  // max_memory_percent (unreachable)
      0.0,  // max_process_rss_mb (off)
      std::chrono::milliseconds(5),  // scheduler_interval
      std::move(name),
      /*enable_periodic_stats_=*/false,
      max_process_swap_mb,
      0.0,  // max_swap_io_per_sec (off)
  };
  cfg.swap_growth_mb = swap_gate_delta_mb;
  cfg.swap_growth_window = std::chrono::milliseconds(100);
  return cfg;
}

/// Submits `n` tasks that each wait (up to `timeout`) until all `n` are
/// running at once.  Returns the peak number seen running concurrently:
/// `n` when the pool dispatched them in parallel, `1` when a gate
/// serialised them.
[[nodiscard]] int swap_gate_peak_concurrency(AdaptiveWorkPool& pool,
                                             int n,
                                             std::chrono::milliseconds timeout)
{
  struct Shared
  {
    std::atomic<int> running {0};
    std::atomic<int> peak {0};
  };
  auto shared = std::make_shared<Shared>();

  std::vector<std::future<void>> futures;
  futures.reserve(static_cast<std::size_t>(n));
  for (int i = 0; i < n; ++i) {
    auto fut = pool.submit(
        [shared, n, timeout]
        {
          const auto mine = shared->running.fetch_add(1) + 1;
          auto seen = shared->peak.load();
          while (seen < mine
                 && !shared->peak.compare_exchange_weak(seen, mine)) {}
          const auto deadline = std::chrono::steady_clock::now() + timeout;
          while (shared->peak.load() < n
                 && std::chrono::steady_clock::now() < deadline)
          {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
          }
          shared->running.fetch_sub(1);
        });
    REQUIRE(fut.has_value());
    futures.push_back(std::move(fut.value()));
  }
  for (auto& fut : futures) {
    REQUIRE(fut.wait_for(std::chrono::seconds(60))
            == std::future_status::ready);
  }
  return shared->peak.load();
}

}  // namespace

// ─── SwapGrowthGate: pure state machine ──────────────────────────────────────

TEST_CASE("SwapGrowthGate: static high VmSwap never throttles")  // NOLINT
{
  // The production incident: VmSwap parked at 3343 MB, limit 2048 MB,
  // zero swap I/O.  Ten minutes of samples must never close the gate.
  SwapGateDriver drv;
  drv.hold(3343.0, 1200);

  CHECK_FALSE(drv.gate.engaged());
  CHECK_FALSE(drv.gate.growing());
  CHECK(drv.engaged_events == 0);
  CHECK(drv.released_events == 0);
}

TEST_CASE("SwapGrowthGate: static VmSwap with sample jitter stays open")
{
  // Accounting noise of a few MB around a static level is not swap-out.
  SwapGateDriver drv;
  for (int i = 0; i < 600; ++i) {
    drv.feed(3343.0 + ((i % 2 == 0) ? 3.0 : -3.0));
  }
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.engaged_events == 0);
}

TEST_CASE("SwapGrowthGate: growing VmSwap over the limit throttles")  // NOLINT
{
  SwapGateDriver drv;
  drv.feed(3000.0);
  CHECK_FALSE(drv.gate.engaged());  // first sample only sets the baseline

  // 40 MB per 500 ms = 400 MB per 5 s window, far above the 64 MB delta.
  drv.ramp(3000.0, 40.0, 4);
  CHECK(drv.gate.engaged());
  CHECK(drv.gate.growing());
  CHECK(drv.engaged_events == 1);

  // Still growing: stays engaged, and the engage event fires only ONCE.
  drv.ramp(3160.0, 40.0, 60);
  CHECK(drv.gate.engaged());
  CHECK(drv.engaged_events == 1);
  CHECK(drv.released_events == 0);
}

TEST_CASE("SwapGrowthGate: releases when growth stops")  // NOLINT
{
  SwapGateDriver drv;
  drv.feed(3000.0);
  const auto peak = drv.ramp(3000.0, 40.0, 20);
  REQUIRE(drv.gate.engaged());

  // Growth stops but VmSwap stays far above the limit (cold pages do not
  // come back).  Hysteresis: still engaged until one full quiet window.
  drv.hold(peak, 5);  // 2.5 s < 5 s window
  CHECK(drv.gate.engaged());

  drv.hold(peak, 6);  // crosses the 5 s quiet window
  CHECK_FALSE(drv.gate.engaged());
  CHECK_FALSE(drv.gate.growing());
  CHECK(drv.released_events == 1);

  // ... and it STAYS released for as long as VmSwap is static — this is
  // the latch the old level-only gate had.
  drv.hold(peak, 1200);
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.engaged_events == 1);
  CHECK(drv.released_events == 1);

  // A second burst re-engages.
  drv.ramp(peak, 40.0, 4);
  CHECK(drv.gate.engaged());
  CHECK(drv.engaged_events == 2);
}

TEST_CASE("SwapGrowthGate: limit 0 disables the gate")  // NOLINT
{
  SwapGate gate {0.0, swap_gate_delta_mb, swap_gate_window};
  auto now = SwapGate::time_point {} + 1h;
  double v = 0.0;
  for (int i = 0; i < 200; ++i) {
    now += swap_gate_sample;
    v += 500.0;  // 1 GB/s of swap-out — as bad as it gets
    CHECK(gate.update(v, now) == SwapTransition::none);
  }
  CHECK_FALSE(gate.engaged());
  CHECK_FALSE(gate.growing());
}

TEST_CASE("SwapGrowthGate: growth below the limit does not throttle")  // NOLINT
{
  SwapGateDriver drv;
  drv.feed(100.0);
  drv.ramp(100.0, 40.0, 20);  // 100 → 900 MB, growing but under 2048 MB
  CHECK(drv.gate.growing());
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.engaged_events == 0);

  // Keeps growing through the limit: engages when it crosses.
  drv.ramp(900.0, 40.0, 40);  // → 2500 MB
  CHECK(drv.gate.engaged());
  CHECK(drv.engaged_events == 1);
}

TEST_CASE("SwapGrowthGate: slow drift under the delta stays open")  // NOLINT
{
  // 4 MB per 500 ms = 40 MB per 5 s window < 64 MB delta.
  SwapGateDriver drv;
  drv.feed(3000.0);
  drv.ramp(3000.0, 4.0, 600);
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.engaged_events == 0);
}

TEST_CASE("SwapGrowthGate: late samples are judged as a rate")  // NOLINT
{
  // The pool only consults the gate while tasks are queued.  After a
  // ten-minute gap a 200 MB rise is 1.7 MB per window, not a burst.
  SwapGateDriver drv;
  drv.feed(3000.0);
  drv.feed(3200.0, 10min);
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.engaged_events == 0);

  // The same 200 MB inside one window IS a burst.
  drv.feed(3400.0, 1s);
  CHECK(drv.gate.engaged());
  CHECK(drv.engaged_events == 1);
}

TEST_CASE("SwapGrowthGate: swap-in alone does not throttle")  // NOLINT
{
  // VmSwap falling means pages are being faulted back in, not evicted.
  SwapGateDriver drv;
  drv.feed(4000.0);
  drv.ramp(4000.0, -40.0, 40);  // 4000 → 2400 MB
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.engaged_events == 0);
}

TEST_CASE("SwapGrowthGate: churn (swap-in then swap-out) throttles")  // NOLINT
{
  // Growth is measured from the window MINIMUM, so pages faulted in and
  // evicted again inside one window count even with no net growth.
  SwapGateDriver drv;
  drv.feed(3000.0);
  drv.feed(2800.0);
  drv.feed(3000.0);
  CHECK(drv.gate.engaged());
  CHECK(drv.engaged_events == 1);
}

TEST_CASE("SwapGrowthGate: releases when VmSwap drops below limit")  // NOLINT
{
  SwapGateDriver drv;
  drv.feed(2000.0);
  drv.ramp(2000.0, 40.0, 10);  // → 2400 MB, engaged
  REQUIRE(drv.gate.engaged());

  drv.feed(1500.0);  // pages faulted back in: under the limit at once
  CHECK_FALSE(drv.gate.engaged());
  CHECK(drv.released_events == 1);
}

TEST_CASE("SwapGrowthGate: growth delta 0 treats any growth as active")
{
  SwapGate gate {swap_gate_limit_mb, 0.0, swap_gate_window};
  auto now = SwapGate::time_point {} + 1h;
  CHECK(gate.update(3000.0, now) == SwapTransition::none);
  now += swap_gate_sample;
  CHECK(gate.update(3000.0, now) == SwapTransition::none);  // static
  now += swap_gate_sample;
  CHECK(gate.update(3001.0, now) == SwapTransition::engaged);
}

// ─── WorkPoolConfig defaults ─────────────────────────────────────────────────

TEST_CASE("WorkPoolConfig swap-gate defaults follow the process-wide knobs")
{
  const SwapDefaultsGuard guard;

  SUBCASE("built-in defaults")
  {
    const WorkPoolConfig cfg;
    CHECK(cfg.max_process_swap_mb == doctest::Approx(2048.0));
    CHECK(cfg.swap_growth_mb == doctest::Approx(64.0));
    CHECK(cfg.swap_growth_window == std::chrono::seconds(5));
    CHECK_FALSE(static_cast<bool>(cfg.process_swap_source));
  }

  SUBCASE("set_default_swap_gate reaches default-constructed configs")
  {
    set_default_swap_gate(0.0, 256.0);
    const WorkPoolConfig cfg;
    CHECK(cfg.max_process_swap_mb == doctest::Approx(0.0));
    CHECK(cfg.swap_growth_mb == doctest::Approx(256.0));
    CHECK(default_max_process_swap_mb() == doctest::Approx(0.0));
    CHECK(default_swap_growth_mb() == doctest::Approx(256.0));
  }

  SUBCASE("negative values clamp to 0")
  {
    set_default_swap_gate(-5.0, -1.0);
    CHECK(default_max_process_swap_mb() == doctest::Approx(0.0));
    CHECK(default_swap_growth_mb() == doctest::Approx(0.0));
  }

  SUBCASE("an explicit constructor argument wins over the default")
  {
    set_default_swap_gate(0.0, 256.0);
    const auto cfg = make_swap_gate_pool_config("SwapExplicit", 512.0);
    CHECK(cfg.max_process_swap_mb == doctest::Approx(512.0));
  }

  SUBCASE("reset restores the built-ins")
  {
    set_default_swap_gate(1.0, 2.0);
    reset_default_swap_gate();
    CHECK(default_max_process_swap_mb() == doctest::Approx(2048.0));
    CHECK(default_swap_growth_mb() == doctest::Approx(64.0));
  }
}

// ─── AdaptiveWorkPool end-to-end (injected VmSwap source) ────────────────────

TEST_CASE(  // NOLINT
    "AdaptiveWorkPool: static high VmSwap does not throttle dispatch")
{
  // Production repro: VmSwap 3343 MB >= 2048 MB limit, but static.
  auto cfg = make_swap_gate_pool_config("SwapStaticPool", swap_gate_limit_mb);
  cfg.process_swap_source = [] { return 3343.0; };
  AdaptiveWorkPool pool(std::move(cfg));
  pool.start();

  // Let several growth windows elapse while the pool keeps evaluating.
  for (int round = 0; round < 4; ++round) {
    CHECK(swap_gate_peak_concurrency(pool, 4, std::chrono::seconds(20)) == 4);
    std::this_thread::sleep_for(std::chrono::milliseconds(120));
  }

  const auto stats = pool.get_statistics();
  CHECK(stats.process_swap_mb == doctest::Approx(3343.0));
  CHECK(stats.throttled_process_swap == 0);
  CHECK_FALSE(stats.swap_gate_engaged);
}

TEST_CASE(  // NOLINT
    "AdaptiveWorkPool: growing VmSwap throttles, then releases when static")
{
  // Each read of the source adds 1 GB while `growing` is set, so the very
  // first re-read after the baseline already exceeds 64 MB per 100 ms
  // window (read-driven rather than time-driven keeps this deterministic
  // on a loaded host: no dependence on how fast the pool polls).
  struct Source
  {
    std::atomic<bool> growing {true};
    std::atomic<double> vmswap_mb {3000.0};
  };
  auto src = std::make_shared<Source>();

  auto cfg = make_swap_gate_pool_config("SwapGrowPool", swap_gate_limit_mb);
  cfg.process_swap_source = [src]
  {
    if (src->growing.load()) {
      return src->vmswap_mb.fetch_add(1024.0) + 1024.0;
    }
    return src->vmswap_mb.load();
  };
  AdaptiveWorkPool pool(std::move(cfg));
  pool.start();

  // ── Growing: the gate closes; only the idle-progress admission runs, so
  //    tasks execute one at a time — but every one still completes.
  CHECK(swap_gate_peak_concurrency(pool, 4, std::chrono::milliseconds(150))
        == 1);

  // ── Growth stops; VmSwap stays far above the limit.  After one quiet
  //    window the gate must release and full concurrency must return.
  //    (Freeze BEFORE reading statistics: `get_statistics()` reads the
  //    source too, and must not add growth the gate never sampled.)
  src->growing.store(false);
  const auto frozen_mb = src->vmswap_mb.load();
  const auto while_growing = pool.get_statistics();
  CHECK(while_growing.throttled_process_swap > 0);
  CHECK(while_growing.swap_gate_engaged);
  CHECK(frozen_mb > swap_gate_limit_mb);
  std::this_thread::sleep_for(std::chrono::milliseconds(300));

  CHECK(swap_gate_peak_concurrency(pool, 4, std::chrono::seconds(20)) == 4);
  const auto stats = pool.get_statistics();
  CHECK_FALSE(stats.swap_gate_engaged);
  CHECK(stats.process_swap_mb == doctest::Approx(frozen_mb));

  // Still static a few windows later: no new throttle events at all.
  const auto throttled_after_release = stats.throttled_process_swap;
  std::this_thread::sleep_for(std::chrono::milliseconds(250));
  CHECK(swap_gate_peak_concurrency(pool, 4, std::chrono::seconds(20)) == 4);
  CHECK(pool.get_statistics().throttled_process_swap
        == throttled_after_release);
}

TEST_CASE(  // NOLINT
    "AdaptiveWorkPool: max_process_swap_mb 0 never throttles on swap")
{
  // Gate off: even runaway VmSwap growth must not hold work back.
  auto vmswap_mb = std::make_shared<std::atomic<double>>(3000.0);
  auto cfg = make_swap_gate_pool_config("SwapOffPool", 0.0);
  cfg.process_swap_source = [vmswap_mb]
  { return vmswap_mb->fetch_add(40.0) + 40.0; };
  AdaptiveWorkPool pool(std::move(cfg));
  pool.start();

  for (int round = 0; round < 3; ++round) {
    CHECK(swap_gate_peak_concurrency(pool, 4, std::chrono::seconds(20)) == 4);
    std::this_thread::sleep_for(std::chrono::milliseconds(120));
  }

  const auto stats = pool.get_statistics();
  CHECK(stats.throttled_process_swap == 0);
  CHECK_FALSE(stats.swap_gate_engaged);
}

// ─── Option plumbing ─────────────────────────────────────────────────────────

TEST_CASE("PlanningOptionsLP swap-gate accessors")  // NOLINT
{
  SUBCASE("defaults")
  {
    const PlanningOptionsLP opts {PlanningOptions {}};
    CHECK(opts.pool_max_process_swap_mb() == doctest::Approx(2048.0));
    CHECK(opts.pool_swap_growth_mb() == doctest::Approx(64.0));
  }

  SUBCASE("explicit values, 0 = off")
  {
    PlanningOptions po;
    po.sddp_options.pool_max_process_swap_mb = 0.0;
    po.sddp_options.pool_swap_growth_mb = 128.0;
    const PlanningOptionsLP opts {po};
    CHECK(opts.pool_max_process_swap_mb() == doctest::Approx(0.0));
    CHECK(opts.pool_swap_growth_mb() == doctest::Approx(128.0));
  }

  SUBCASE("negative values clamp to 0")
  {
    PlanningOptions po;
    po.sddp_options.pool_max_process_swap_mb = -1.0;
    po.sddp_options.pool_swap_growth_mb = -1.0;
    const PlanningOptionsLP opts {po};
    CHECK(opts.pool_max_process_swap_mb() == doctest::Approx(0.0));
    CHECK(opts.pool_swap_growth_mb() == doctest::Approx(0.0));
  }
}

TEST_CASE("SddpOptions swap-gate knobs: JSON and merge")  // NOLINT
{
  SUBCASE("absent keys stay unset")
  {
    const auto opts = daw::json::from_json<SddpOptions>(R"({})");
    CHECK_FALSE(opts.pool_max_process_swap_mb.has_value());
    CHECK_FALSE(opts.pool_swap_growth_mb.has_value());
  }

  SUBCASE("round trip")
  {
    const std::string_view json_data = R"({
      "pool_max_process_swap_mb": 0,
      "pool_swap_growth_mb": 256.5
    })";
    const auto opts = daw::json::from_json<SddpOptions>(json_data);
    CHECK(opts.pool_max_process_swap_mb.value_or(-1.0) == doctest::Approx(0.0));
    CHECK(opts.pool_swap_growth_mb.value_or(-1.0) == doctest::Approx(256.5));

    const auto text = daw::json::to_json(opts);
    const auto back = daw::json::from_json<SddpOptions>(text);
    CHECK(back.pool_max_process_swap_mb.value_or(-1.0) == doctest::Approx(0.0));
    CHECK(back.pool_swap_growth_mb.value_or(-1.0) == doctest::Approx(256.5));
  }

  SUBCASE("merge overrides only what the overlay sets")
  {
    SddpOptions base;
    base.pool_max_process_swap_mb = 4096.0;
    SddpOptions overlay;
    overlay.pool_swap_growth_mb = 32.0;
    base.merge(std::move(overlay));
    CHECK(base.pool_max_process_swap_mb.value_or(-1.0)
          == doctest::Approx(4096.0));
    CHECK(base.pool_swap_growth_mb.value_or(-1.0) == doctest::Approx(32.0));
  }
}

TEST_CASE("CLI --swap-limit / --swap-growth map to sddp_options")  // NOLINT
{
  SUBCASE("parsed from the command line")
  {
    const auto desc = make_options_description();
    const std::vector<std::string> args {
        "--swap-limit",
        "3G",
        "--swap-growth",
        "128M",
    };
    po::variables_map vm;
    po::store(po::command_line_parser(args).options(desc), vm);
    po::notify(vm);
    CHECK(get_opt<std::string>(vm, "swap-limit").value_or("") == "3G");
    CHECK(get_opt<std::string>(vm, "swap-growth").value_or("") == "128M");
  }

  SUBCASE("applied to the planning options")
  {
    Planning planning {};
    MainOptions opts {};
    opts.swap_limit = "3G";
    opts.swap_growth = "128M";
    apply_cli_options(planning, opts);
    CHECK(planning.options.sddp_options.pool_max_process_swap_mb.value_or(-1.0)
          == doctest::Approx(3072.0));
    CHECK(planning.options.sddp_options.pool_swap_growth_mb.value_or(-1.0)
          == doctest::Approx(128.0));
  }

  SUBCASE("--swap-limit 0 turns the gate off")
  {
    Planning planning {};
    MainOptions opts {};
    opts.swap_limit = "0";
    apply_cli_options(planning, opts);
    const PlanningOptionsLP lp_opts {planning.options};
    CHECK(lp_opts.pool_max_process_swap_mb() == doctest::Approx(0.0));
  }

  SUBCASE("unset flags leave the options untouched")
  {
    Planning planning {};
    apply_cli_options(planning, MainOptions {});
    CHECK_FALSE(
        planning.options.sddp_options.pool_max_process_swap_mb.has_value());
    CHECK_FALSE(planning.options.sddp_options.pool_swap_growth_mb.has_value());
  }
}
