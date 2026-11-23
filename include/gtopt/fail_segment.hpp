/**
 * @file      fail_segment.hpp
 * @brief     Stepwise value-of-lost-load (VoLL) curve segment for Demand
 * @date      Sun Nov 22 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A demand-failure curve is an ordered list of segments
 * `{fraction, cost}`: segment k covers `fraction_k × lmax` MW of
 * curtailment priced at `cost_k` $/MWh.  The last segment may leave
 * `fraction` unset ("open"): it then covers the residual
 * `1 − Σ_{j<K} fraction_j`.  When every fraction is given and they sum
 * to less than 1, the uncovered remainder is **firm** load (it cannot
 * be curtailed) — the PLP `FALLA_*` semantics, where the four failure
 * units are capped at 5/5/10/80 % of the bus demand.
 *
 * Typical sources: PLP `FALLA_<bus>_1..4`, SDDP `deficit_segments`,
 * CNE Chile CFLD (0–5/5–10/10–20/>20 %), CRIE Guatemala (4 tiers),
 * PLEXOS `VoLL` + `Internal VoLL`/`Level` (2 bands).
 *
 * ### JSON Example
 * ```json
 * "fail_segments": [
 *   {"fraction": 0.05, "cost": 492.4},
 *   {"fraction": 0.05, "cost": 541.7},
 *   {"fraction": 0.10, "cost": 648.6},
 *   {"cost": 742.1}
 * ]
 * ```
 */

#pragma once

#include <algorithm>
#include <cstddef>
#include <optional>
#include <span>
#include <vector>

#include <gtopt/field_sched.hpp>

namespace gtopt
{

/// One segment of a stepwise demand-failure (VoLL) curve.
struct FailSegment
{
  /// Fraction of the block demand `lmax` covered by this segment
  /// (0 < fraction ≤ 1).  Unset only on the last segment = open
  /// (covers the residual `1 − Σ` of the previous fractions).
  OptReal fraction {};
  /// Curtailment cost of this segment [$/MWh].  On `Demand.fail_segments`
  /// it is a per-stage schedule (scalar, per-stage array, or file).
  OptTRealFieldSched cost {};

  [[nodiscard]] bool operator==(const FailSegment&) const = default;
};

/// Segment of the global default curve
/// (`model_options.demand_fail_segments`): same shape as `FailSegment`
/// but the cost is a plain scalar (options are schedule-free).
struct ScalarFailSegment
{
  OptReal fraction {};  ///< See `FailSegment::fraction`.
  OptReal cost {};  ///< Curtailment cost [$/MWh].

  [[nodiscard]] bool operator==(const ScalarFailSegment&) const = default;
};

/// Maximum number of segments per demand (bounds the static
/// `fail_seg<k>` output-field names).
inline constexpr std::size_t max_fail_segments = 16;

/// Segment widths (fractions of `lmax`) after resolving the open last
/// segment, plus the firm (non-curtailable) remainder.
struct FailSegmentWidths
{
  std::vector<double> widths {};
  double firm {0.0};
};

/// Resolve segment widths: explicit fractions are clipped so the
/// cumulative sum never exceeds 1; an open last segment takes the
/// residual; `firm = 1 − Σ widths` (≥ 0).
template<typename Segment>
[[nodiscard]] FailSegmentWidths resolve_fail_segment_widths(
    std::span<const Segment> segments)
{
  FailSegmentWidths out;
  out.widths.reserve(segments.size());
  double cum = 0.0;
  for (std::size_t k = 0; k < segments.size(); ++k) {
    const bool open =
        !segments[k].fraction.has_value() && (k + 1 == segments.size());
    const double want =
        open ? (1.0 - cum) : std::max(0.0, segments[k].fraction.value_or(0.0));
    const double w = std::clamp(want, 0.0, std::max(0.0, 1.0 - cum));
    out.widths.push_back(w);
    cum += w;
  }
  out.firm = std::max(0.0, 1.0 - cum);
  // Absorb FP noise (e.g. 0.05+0.05+0.1+0.8 = 0.9999999999999999).
  if (out.firm < 1e-12) {
    out.firm = 0.0;
  }
  return out;
}

}  // namespace gtopt
