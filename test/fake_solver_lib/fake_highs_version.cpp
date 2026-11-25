// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      fake_highs_version.cpp
 * @brief     TEST ONLY: a fake HiGHS that reports the pinned version
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Exports the HiGHS version functions with the values of
 * GTOPT_FAKE_HIGHS_MAJOR/MINOR/PATCH (the pinned release).  Built twice
 * by test/CMakeLists.txt, only in the build tree:
 *  - `libgtopt_fake_highs.so`, interposed with LD_PRELOAD;
 *  - `libhighs.so.1` that forwards every other symbol to the real library
 *    (through its DT_NEEDED `libhighs_real.so.1`), found via
 *    LD_LIBRARY_PATH.
 * The version, pin and (for the preload) RUNPATH checks all pass; only
 * the solver-library identity check can tell it is not the library gtopt
 * was built against (check_fake_solver_library.cmake).
 */

#include <Highs.h>

HighsInt highsVersionMajor()
{
  return GTOPT_FAKE_HIGHS_MAJOR;
}

HighsInt highsVersionMinor()
{
  return GTOPT_FAKE_HIGHS_MINOR;
}

HighsInt highsVersionPatch()
{
  return GTOPT_FAKE_HIGHS_PATCH;
}
