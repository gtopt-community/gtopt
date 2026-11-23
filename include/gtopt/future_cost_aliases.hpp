/**
 * @file      future_cost_aliases.hpp
 * @brief     Translate the deprecated boundary-cut option keys into FutureCost
 * @date      Fri Nov 20 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The `FutureCost` element (`system.future_cost_array`) is the ONE place the
 * future-cost-function (FCF) boundary cuts are configured, for every method.
 * The pre-existing per-method keys survive only as deprecated aliases that
 * `resolve_future_cost_aliases` folds into the element once, after every JSON
 * file and `--set` / CLI override has been merged:
 *
 * - method = sddp / cascade only: `sddp_options.boundary_cuts_file` →
 *   `cuts_file`, `boundary_cuts_mode` → `mode`, `boundary_cut_sharing_mode`
 *   → `sharing`, `boundary_cuts_mean_shift` → `mean_shift`,
 *   `boundary_max_iterations` → `iteration_limit`, `missing_cut_var_mode`
 *   → `missing_cut_var_mode`;
 * - method = monolithic only: `monolithic_options.boundary_cuts_file` →
 *   `cuts_file`, `boundary_cuts_mode` → `mode`, `boundary_cut_sharing_mode`
 *   → `sharing`, `boundary_max_iterations` → `iteration_limit`;
 * - any method: `simulation.boundary_cuts_file` → `cuts_file`,
 *   `simulation.boundary_cuts_valuation` → `valuation`.
 *
 * An honoured alias lands in the first active FutureCost element (one
 * `{uid: 1, name: "fcf"}` is created when there is none) with a deprecation
 * warning naming old → new key.  A key of the OTHER method is ignored with a
 * warning (monolithic never reads `sddp_options`, and vice versa).  Every
 * legacy key is cleared afterwards, so the call is idempotent.
 *
 * The same field given with DIFFERENT values in the element and an alias (or
 * in two aliases) is a hard `InvalidInput` error; equal values are accepted.
 */

#pragma once

#include <expected>

#include <gtopt/error.hpp>

namespace gtopt
{

struct Planning;

/// Fold the deprecated boundary-cut keys of @p planning into its FutureCost
/// element (see the file comment for the full mapping and rules).
[[nodiscard]] std::expected<void, Error> resolve_future_cost_aliases(
    Planning& planning);

}  // namespace gtopt
