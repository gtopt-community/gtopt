// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_build_hardening.cpp
 * @brief     The cmake_local/Hardening.cmake flags reach the compiler
 * @date      2026-11-24
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Hardening used to depend on the host compiler's defaults.  The test
 * binary is compiled with the same directory-level flags as the core
 * library and the plugins, so the predefined macros seen here tell
 * whether the project's own hardening options are in effect.
 */

#include <doctest/doctest.h>

#ifdef __SANITIZE_ADDRESS__
#  define GTOPT_TEST_MEM_SANITIZER 1
#elifdef __has_feature
#  if __has_feature(address_sanitizer) || __has_feature(memory_sanitizer)
#    define GTOPT_TEST_MEM_SANITIZER 1
#  endif
#endif

TEST_CASE("Build hardening flags reach the compiler")  // NOLINT
{
#ifdef GTOPT_HARDENING
  // -fstack-protector-strong
#  if !defined(__SSP_STRONG__) && !defined(__SSP_ALL__)
  FAIL("GTOPT_HARDENING is on but -fstack-protector-strong is not");
#  endif

  // _FORTIFY_SOURCE=3 in optimised builds (FORTIFY needs optimisation;
  // ASan/MSan builds skip it).
#  if defined(__OPTIMIZE__) && !defined(GTOPT_TEST_MEM_SANITIZER)
#    if !defined(_FORTIFY_SOURCE) || _FORTIFY_SOURCE < 3
  FAIL("optimised build without _FORTIFY_SOURCE=3");
#    endif
#  endif

  // libstdc++ assertions whenever the build asked for them
  // (GTOPT_GLIBCXX_ASSERTIONS, default: every configuration but Release).
#  if defined(GTOPT_GLIBCXX_ASSERTIONS_ACTIVE) && !defined(_GLIBCXX_ASSERTIONS)
  FAIL("GTOPT_GLIBCXX_ASSERTIONS is active but _GLIBCXX_ASSERTIONS is not");
#  endif
  CHECK(true);
#elifdef GTOPT_HARDENING_DISABLED
  MESSAGE("GTOPT_HARDENING=OFF: hardening checks skipped");
#else
  FAIL("built without cmake_local/Hardening.cmake");
#endif
}
