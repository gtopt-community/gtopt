// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_typical_period_cycle_weight.cpp
 * @brief     Default cycle_weight of a typical-period stage
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <vector>

#include <doctest/doctest.h>
#include <gtopt/stage_lp.hpp>

using namespace gtopt;

TEST_CASE(  // NOLINT
    "Typical period - default cycle_weight is duration / period_hours")
{
  // A month (730 h) represented by a typical week: 2 weighted blocks.
  // The group balance of a `period_hours` stage runs on ONE real period,
  // so its repetition count is n = 730 / 168, not 730 / 24 days.
  const std::vector<Block> blocks {
      {
          .uid = Uid {1},
          .duration = 365.0,
      },
      {
          .uid = Uid {2},
          .duration = 365.0,
      },
  };
  const StageLP week {
      Stage {
          .uid = Uid {1},
          .first_block = 0,
          .count_block = 2,
          .cycle_group = 1,
          .period_hours = 168.0,
      },
      blocks,
  };
  CHECK(week.time_scale() == doctest::Approx(168.0 / 730.0));
  CHECK(week.repetitions() == doctest::Approx(730.0 / 168.0));
  CHECK(week.cycle_weight() == doctest::Approx(730.0 / 168.0));

  // Without period_hours the legacy day count stays.
  const StageLP days {
      Stage {
          .uid = Uid {2},
          .first_block = 0,
          .count_block = 2,
          .cycle_group = 1,
      },
      blocks,
  };
  CHECK(days.cycle_weight() == doctest::Approx(730.0 / 24.0));
  CHECK(days.repetitions() == doctest::Approx(1.0));

  // An explicit weight wins.
  const StageLP explicit_w {
      Stage {
          .uid = Uid {3},
          .first_block = 0,
          .count_block = 2,
          .cycle_group = 1,
          .cycle_weight = 5.0,
          .period_hours = 168.0,
      },
      blocks,
  };
  CHECK(explicit_w.cycle_weight() == doctest::Approx(5.0));
}

TEST_CASE(  // NOLINT
    "Typical period - Stage.periodic default and override")
{
  CHECK_FALSE(Stage {}.is_periodic());
  CHECK(Stage {.period_hours = 24.0}.is_periodic());
  CHECK(Stage {.sampling = "point"}.is_periodic());
  CHECK_FALSE(Stage {.period_hours = 24.0, .periodic = false}.is_periodic());
  CHECK(Stage {.periodic = true}.is_periodic());
}
