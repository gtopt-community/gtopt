/**
 * @file      emission_capture.hpp
 * @brief     Per-(generator, pollutant) capture & cost (CCS) declaration
 * @date      2026-05-18
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `EmissionCapture` is the data row that describes a Carbon Capture &
 * Storage (CCS) — or, more generally, any abatement-technology —
 * fraction installed on a specific `Generator` for a specific
 * `Emission` pollutant.  It lives **inline** on
 * `Generator.emission_captures[]`; there is no top-level
 * `emission_capture_array` because the capture is intrinsically tied to the
 * generator it lives on (CCS technology is a property of the unit, not of the
 * network).
 *
 * ## Semantics
 *
 * Given an `EmissionSource` row `src` with rate `r` and (optional)
 * upstream rate `r_u`, the matching capture row `cap` with rate
 * `c ∈ [0, 1]` causes:
 *
 *   - **net emission counted by the zone** (cap-row / allowance-pool
 *     coefficient and per-tonne price adder):
 *       (1 − c) · (r + r_u) · gen · dur          [tons of pollutant]
 *
 *   - **capture cost adder** on the generator dispatch column:
 *       c · (r + r_u) · cap.cost · dur · prob · disc / scale_obj
 *     (added per (scen, stg, blk) — same `CostHelper::block_ecost`
 *     chain as fuel cost and emission price).
 *
 * In the special case `c = 0` the row is inert (matches the no-CCS
 * baseline).  In `c = 1` everything is captured (no contribution to
 * the cap row) but the full capture cost is paid.
 *
 * ## Naming alignment
 *
 * | Property                | gtopt        | PLEXOS                |
 * |-------------------------|--------------|-----------------------|
 * | Capture fraction        | `rate`       | `Capture Rate`        |
 * | $/captured-ton          | `cost`       | `Capture Cost`        |
 *
 * IPCC / GHG-Protocol refers to the same concept as "abatement
 * efficiency" (rate) and "abatement cost" (cost).
 *
 * @see Generator.emission_captures[] for the inline list field
 * @see EmissionSource — the source row whose rate is scaled by
 *      `(1 − capture_rate)`
 */

#pragma once

#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @struct EmissionCapture
 * @brief Per-(generator, pollutant) CCS / abatement parameters.
 */
struct EmissionCapture
{
  /// FK to the `Emission` pollutant kind this capture targets.
  /// Must match an entry in `emission_array` (and typically also an
  /// `EmissionSource.emission` on the same generator for the
  /// capture to bite anything).
  SingleId emission {unknown_uid};

  /// Capture fraction `[p.u., 0..1]`, stage-schedulable.
  /// Effective (net) contribution counted by the zone — cap-row /
  /// allowance-pool coefficient and price adder — is
  /// `(1 − rate) · (combustion + upstream) · gen · dur`.
  /// Values outside `[0, 1]` are accepted but should be reserved
  /// for future negative-emission technologies (DAC / BECCS), where
  /// `rate > 1` represents net atmospheric removal.
  OptTRealFieldSched rate {};

  /// Capture cost `[$/ton captured]`, stage-schedulable.  Added to
  /// the generator's dispatch column cost coefficient:
  /// `cost · rate · (combustion + upstream) · dur` per MWh
  /// generated, scaled by the standard `CostHelper::block_ecost`
  /// chain.
  OptTRealFieldSched cost {};
};

}  // namespace gtopt
