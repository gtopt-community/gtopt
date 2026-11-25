// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      highs_real_stub.cpp
 * @brief     TEST ONLY: link-time stand-in for `libhighs_real.so.1`
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Gives the fake `libhighs.so.1` a DT_NEEDED on `libhighs_real.so.1`.  At
 * test time that name is a symlink to the real libhighs, so every HiGHS
 * symbol the fake does not define resolves to the real library.
 */

extern "C" int gtopt_fake_highs_real_stub()
{
  return 0;
}
