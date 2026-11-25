// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      lp_names_helpers.hpp
 * @brief     Build options for white-box tests that inspect LP labels
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * With LP names off a build keeps no label metadata (`row_label_at` /
 * `col_label_at` return null).  Tests that find rows or columns by label
 * build with names on, the way `--lp-file` / `lp_debug` do.
 */

#pragma once

#include <gtopt/lp_matrix_options.hpp>

namespace gtopt::test_support
{

[[nodiscard]] inline auto names_on_flat_opts() -> LpMatrixOptions
{
  LpMatrixOptions opts;
  opts.col_with_names = true;
  opts.row_with_names = true;
  opts.col_with_name_map = true;
  opts.row_with_name_map = true;
  return opts;
}

}  // namespace gtopt::test_support
