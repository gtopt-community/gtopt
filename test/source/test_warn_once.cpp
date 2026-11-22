/**
 * @file      test_warn_once.cpp
 * @brief     Unit tests for the thread-safe warn_once helper
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <atomic>
#include <ranges>
#include <thread>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/warn_once.hpp>

#include "log_capture.hpp"

using namespace gtopt;

TEST_CASE("WarnOnceFlag fires exactly once")
{
  SUBCASE("single thread")
  {
    WarnOnceFlag flag;
    CHECK_FALSE(flag.fired());
    CHECK(flag.fire());
    CHECK(flag.fired());
    CHECK_FALSE(flag.fire());
    CHECK_FALSE(flag.fire());
    CHECK(flag.fired());
  }

  SUBCASE("concurrent callers")
  {
    constexpr int kThreads = 16;
    constexpr int kCallsPerThread = 1000;
    WarnOnceFlag flag;
    std::atomic<int> winners {0};
    {
      std::vector<std::jthread> threads;
      threads.reserve(kThreads);
      for ([[maybe_unused]] const auto t : std::views::iota(0, kThreads)) {
        threads.emplace_back(
            [&]
            {
              for ([[maybe_unused]] const auto c :
                   std::views::iota(0, kCallsPerThread))
              {
                if (flag.fire()) {
                  winners.fetch_add(1, std::memory_order_relaxed);
                }
              }
            });
      }
    }  // jthreads join here
    CHECK(winners.load() == 1);
    CHECK(flag.fired());
  }
}

TEST_CASE("warn_once logs the warning only on the first call")
{
  const gtopt::test::LogCapture logs;
  WarnOnceFlag flag;

  for (const auto i : std::views::iota(0, 5)) {
    warn_once(flag, "warn_once test message {}", i);
  }

  const auto msgs = logs.messages();
  const auto hits = std::ranges::count_if(
      msgs, [](const auto& m) { return m.contains("warn_once test message"); });
  CHECK(hits == 1);
  CHECK(logs.contains("warn_once test message 0"));
  CHECK_FALSE(logs.contains("warn_once test message 1"));
}
