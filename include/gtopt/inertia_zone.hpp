/**
 * @file      inertia_zone.hpp
 * @brief     Defines the InertiaZone structure for system inertia requirements
 * @date      2026-04-13
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the InertiaZone structure which represents a regional
 * or system-wide minimum inertia requirement in the LP formulation.
 * Each zone specifies a required inertia level [MWs] and a shortage
 * penalty cost.
 *
 * The inertia requirement ensures that sufficient synchronous generation
 * is online to maintain frequency stability.  The constraint is:
 *
 *   sum_g (provision_factor_g * r_inertia_g) >= H_req
 *
 * where provision_factor = H_g * S_g / Pmin_g [MWs/MW].
 *
 * @see InertiaProvision for generator-to-zone linkage
 * @see InertiaZoneLP for the LP formulation
 */

#pragma once

#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @struct InertiaZone
 * @brief System inertia requirement zone
 *
 * An inertia zone specifies the minimum system inertia [MWs] that must
 * be provided by committed synchronous generators.  Generators contribute
 * to zones via @ref InertiaProvision entries.  If the requirement is
 * not met, the shortage is penalized at `cost` [$/MWs].
 *
 * @see InertiaProvision for generator-to-zone linkage
 * @see InertiaZoneLP for the LP formulation
 */
/**
 * @struct InertiaRequirementCredit
 * @brief Commitment-dependent relief of an inertia requirement
 *
 * Mirrors the PLEXOS PCP Guacolda rows (`InertiaNRMin_D`,
 * `InertiaCommitGuacolda2`, decision variable `InertiaGuacolda2`):
 *
 *   Σ pf·r + credit·y ≥ requirement,   units·y ≤ Σ_{g ∈ generators} u_g,
 *   0 ≤ y ≤ 1
 *
 * so the requirement drops by `credit` once `units` of the listed
 * generators are committed.  With `integer = false` (default, as PLEXOS)
 * `y` is continuous and the relief is linear in the committed count up
 * to `units` — the requirement becomes max(req − credit·n/units,
 * req − credit), a convex function of n that an LP represents exactly.
 * `integer = true` makes `y` binary: an exact step (relief only when
 * n ≥ units).  Several credits stack into a staircase, e.g. Norte Grande
 * 13 400 / 9 500 / 7 500 MWs for 0–1 / 2–3 / ≥ 4 Guacolda units =
 * requirement 13 400 with credits {units 2, credit 3 900} and
 * {units 4, credit 2 000}.  Every listed generator must have a Commitment
 * (its status `u` is what the credit counts): a
 * generator without one is an error under `constraint_mode` strict / debug
 * (the default) and a warning under `normal`, where it is dropped from the
 * credit (a credit left with no generator applies no relief and the
 * exogenous `requirement` schedule holds).  A committed generator whose
 * status column is absent at a block (inactive commitment, non-chronological
 * stage) contributes no relief at that block, silently.
 */
struct InertiaRequirementCredit
{
  OptName name {};  ///< Optional label (provenance)
  Array<SingleId> generators {};  ///< Units counted (status u)
  OptReal units {};  ///< Committed units needed for the full credit
  OptTBRealFieldSched credit {};  ///< Requirement relief [MWs]
  OptBool integer {};  ///< Binary y (exact step); default continuous

  [[nodiscard]] bool operator==(const InertiaRequirementCredit&) const =
      default;
};

struct InertiaZone
{
  /// Canonical class-name constant used in LP row labels and config
  /// fields like `VariableScale::class_name`.  Single source of truth —
  /// `InertiaZoneLP` exposes no separate `ClassName` member; callers
  /// reach the constant via `InertiaZone::class_name` directly (or
  /// `InertiaZoneLP::Element::class_name` in generic contexts).
  static constexpr LPClassName class_name {"InertiaZone"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status

  OptTBRealFieldSched requirement {};  ///< Min inertia requirement [MWs]
  OptTBRealFieldSched cost {};  ///< Inertia shortage penalty [$/MWs] —
                                ///< per-(stage, block); accepts a scalar
                                ///< (broadcasts), a 2-D nested array, or
                                ///< a file-backed schedule.
  /// Commitment-dependent relief steps (see InertiaRequirementCredit).
  Array<InertiaRequirementCredit> requirement_credits {};
};

}  // namespace gtopt
