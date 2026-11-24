/**
 * @file      pricing_mode.hpp
 * @brief     Post-MIP pricing methods (fixed / relaxed / fast-start)
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A MIP (unit-commitment) solve carries no LP duals.  gtopt recovers nodal
 * prices with a separate pricing LP solved after the MIP; the reported
 * dispatch is always the MIP solution, only the duals differ per method:
 *
 *  - `fixed` (default): every integer pinned at the MIP incumbent — the
 *    restricted ("IP") pricing of O'Neill et al.  The classic
 *    fix-and-resolve dual-recovery pass.
 *  - `relaxed`: LP relaxation of the full MIP (every integer continuous in
 *    its bounds).  Approximates convex-hull pricing when the UC
 *    formulation is tight.
 *  - `fast_start`: integers pinned at the incumbent except the commitment
 *    columns (u/v/w) of fast-start units, which are relaxed to [0,1]
 *    (PJM / NYISO "integer relaxation" fast-start pricing).
 *
 * See `ModelOptions::pricing_mode` and
 * `LinearInterface::solve_pricing_lp`.
 */

#pragma once

#include <array>
#include <cstdint>
#include <format>
#include <span>
#include <string>
#include <string_view>

#include <gtopt/basic_types.hpp>
#include <gtopt/enum_option.hpp>

namespace gtopt
{

/// Post-MIP pricing method — see the file header.
enum class PricingMode : std::uint8_t
{
  fixed = 0,  ///< Integers pinned at the incumbent (default)
  relaxed = 1,  ///< LP relaxation of the full MIP
  fast_start = 2,  ///< Fast-start commitment relaxed, the rest pinned
};

inline constexpr auto pricing_mode_entries =
    std::to_array<EnumEntry<PricingMode>>({
        {.name = "fixed", .value = PricingMode::fixed},
        {.name = "relaxed", .value = PricingMode::relaxed},
        {.name = "fast_start", .value = PricingMode::fast_start},
        {.name = "fast-start",
         .value = PricingMode::fast_start,
         .is_alias = true},
    });

[[nodiscard]] constexpr auto enum_entries(PricingMode /*tag*/) noexcept
{
  return std::span {pricing_mode_entries};
}

/// Default auto-rule threshold [h] for fast-start classification
/// (`ModelOptions::fast_start_max_hours`).
inline constexpr double default_fast_start_max_hours = 1.0;

/// Fast-start classification of one commitment.
///
/// An explicit `fast_start` flag wins.  Otherwise the auto rule holds when
/// `min_up_time <= max_hours` and `min_down_time <= max_hours` (unset = 0).
/// gtopt has no startup-time / notification-time field, so the rule uses
/// min up/down only.
[[nodiscard]] constexpr bool is_fast_start(OptBool explicit_flag,
                                           OptReal min_up_time,
                                           OptReal min_down_time,
                                           double max_hours) noexcept
{
  if (explicit_flag.has_value()) {
    return *explicit_flag;
  }
  return min_up_time.value_or(0.0) <= max_hours
      && min_down_time.value_or(0.0) <= max_hours;
}

/// Outcome of the pricing pass of one solved cell — recorded on the
/// `LinearInterface` (`last_pricing()`) and written to `pricing.csv`.
struct PricingOutcome
{
  PricingMode requested {PricingMode::fixed};  ///< `model_options` value
  PricingMode applied {PricingMode::fixed};  ///< Duals actually published
  bool attempted {false};  ///< A separate pricing LP was solved
  bool fallback {false};  ///< Pricing LP failed → fixed prices kept
  Index relaxed_columns {0};  ///< Integer columns left continuous
  Index pinned_columns {0};  ///< Integer + SOS columns pinned
  int status {-1};  ///< Pricing LP status (0 = optimal; -1 = not solved)
  std::string reason {};  ///< Fallback / skip reason (empty when none)
};

/// Column layout of `pricing.csv` (one row per solved MIP cell; written
/// by `PlanningLP::write_out` next to `solution.csv`).  Trailing newline
/// included.
[[nodiscard]] constexpr std::string_view pricing_csv_header() noexcept
{
  return "scene,phase,pricing_mode,applied_mode,fallback,attempted,"
         "relaxed_columns,pinned_columns,status,reason\n";
}

/// True when @p o carries anything worth a `pricing.csv` row (a MIP cell
/// whose duals were recovered, or a non-default mode request).
[[nodiscard]] constexpr bool pricing_outcome_reportable(
    const PricingOutcome& o) noexcept
{
  return o.attempted || o.fallback || o.requested != PricingMode::fixed;
}

/// One `pricing.csv` line for cell (@p scene_uid, @p phase_uid).  The
/// free-text reason is double-quoted (embedded quotes doubled).
[[nodiscard]] inline std::string pricing_csv_line(int scene_uid,
                                                  int phase_uid,
                                                  const PricingOutcome& o)
{
  std::string reason;
  reason.reserve(o.reason.size() + 2);
  reason.push_back('"');
  for (const char ch : o.reason) {
    if (ch == '"') {
      reason.push_back('"');
    }
    reason.push_back(ch);
  }
  reason.push_back('"');
  return std::format("{},{},{},{},{},{},{},{},{},{}\n",
                     scene_uid,
                     phase_uid,
                     enum_name(o.requested),
                     enum_name(o.applied),
                     o.fallback ? 1 : 0,
                     o.attempted ? 1 : 0,
                     o.relaxed_columns,
                     o.pinned_columns,
                     o.status,
                     reason);
}

}  // namespace gtopt
