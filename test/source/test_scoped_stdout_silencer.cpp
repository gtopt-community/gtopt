// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_scoped_stdout_silencer.cpp
 * @brief     ScopedStdoutSilencer restores the real stdout on every path
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <latch>
#include <optional>
#include <stdexcept>
#include <thread>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/scoped_stdout_silencer.hpp>
#include <sys/stat.h>
#include <unistd.h>

using namespace gtopt;

namespace test_scoped_stdout_silencer_ns
{
namespace
{

/// (device, inode) identity of the file fd 1 currently points at.
std::pair<dev_t, ino_t> stdout_identity()
{
  struct stat st {};
  REQUIRE(::fstat(STDOUT_FILENO, &st) == 0);
  return {st.st_dev, st.st_ino};
}

bool stdout_is_devnull()
{
  struct stat out {};
  struct stat null {};
  REQUIRE(::fstat(STDOUT_FILENO, &out) == 0);
  REQUIRE(::stat("/dev/null", &null) == 0);
  return out.st_dev == null.st_dev && out.st_ino == null.st_ino;
}

}  // namespace
}  // namespace test_scoped_stdout_silencer_ns

using namespace test_scoped_stdout_silencer_ns;

TEST_CASE("ScopedStdoutSilencer redirects and restores stdout")  // NOLINT
{
  const auto before = stdout_identity();
  if (stdout_is_devnull()) {
    // Cannot distinguish redirect from the original target; nothing to
    // check in this environment.
    return;
  }

  SUBCASE("single scope")
  {
    {
      const ScopedStdoutSilencer silence;
      CHECK(stdout_is_devnull());
    }
    CHECK(stdout_identity() == before);
  }

  SUBCASE("nested scopes restore only at the outermost exit")
  {
    {
      const ScopedStdoutSilencer outer;
      {
        const ScopedStdoutSilencer inner;
        CHECK(stdout_is_devnull());
      }
      CHECK(stdout_is_devnull());
    }
    CHECK(stdout_identity() == before);
  }

  SUBCASE("overlapping lifetimes across threads (non-LIFO)")
  {
    // Thread A silences first, thread B second; A releases BEFORE B.  With
    // a bare dup/dup2 pair B would have saved /dev/null and "restored" it.
    std::optional<ScopedStdoutSilencer> a;
    a.emplace();
    std::latch b_started {1};
    std::latch a_released {1};
    std::jthread tb(
        [&]
        {
          const ScopedStdoutSilencer b;
          b_started.count_down();
          a_released.wait();
        });
    b_started.wait();
    a.reset();
    CHECK(stdout_is_devnull());  // B still alive
    a_released.count_down();
    tb.join();
    CHECK(stdout_identity() == before);
  }

  SUBCASE("exception unwinding restores stdout")
  {
    CHECK_THROWS_AS(
        []
        {
          const ScopedStdoutSilencer silence;
          throw std::runtime_error("boom");
        }(),
        std::runtime_error);
    CHECK(stdout_identity() == before);
  }
}
