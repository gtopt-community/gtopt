/**
 * @file      reserve_provision.hpp
 * @brief     Defines ReserveProvision – generator contribution to a reserve
 * zone
 * @date      Thu Apr  3 10:30:07 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the ReserveProvision structure which specifies how a
 * generator contributes up/down spinning reserve to one or more reserve zones.
 * Each provision entry links a generator to zone(s) and defines capacity
 * limits and cost coefficients.
 */

#pragma once

#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/single_id.hpp>

namespace gtopt
{

/**
 * @struct ReserveProvision
 * @brief Generator contribution to spinning-reserve zone(s)
 *
 * Links a @ref Generator to one or more @ref ReserveZone entries and defines
 * the maximum reserve it may offer, optional capacity-factor limits, and the
 * reserve bid cost.
 *
 * `reserve_zones` is a typed array of ReserveZone references (each element
 * is either a Uid number or a Name string).
 *
 * @see ReserveZone for zone-level requirements
 * @see ReserveProvisionLP for the LP formulation
 */
struct ReserveProvision
{
  /// Canonical class-name constant used in LP row labels and config
  /// fields like `VariableScale::class_name`.  Single source of truth —
  /// `ReserveProvisionLP` exposes no separate `ClassName` member;
  /// callers reach the constant via `ReserveProvision::class_name`
  /// directly (or `ReserveProvisionLP::Element::class_name` in generic
  /// contexts).
  static constexpr LPClassName class_name {"ReserveProvision"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status
  OptName type {};  ///< Optional element type/category tag
  OptName description {};  ///< Optional free-text description (e.g. conversion
                           ///< provenance)

  SingleId generator {unknown_uid};  ///< ID of the providing generator
  Array<SingleId> reserve_zones {};  ///< Typed array of ReserveZone IDs / names

  OptTBRealFieldSched urmax {};  ///< Maximum up-reserve offer [MW]
  OptTBRealFieldSched drmax {};  ///< Maximum down-reserve offer [MW]

  OptTBRealFieldSched urmin {};  ///< Minimum up-reserve provision floor [MW]
                                 ///< per-(stage, block).  When set, gtopt
                                 ///< adds the row ``provision_up >= urmin``
                                 ///< on the LP, forcing the unit to commit
                                 ///< at least ``urmin`` MW to up-reserve
                                 ///< whenever the gen is on.  Mirrors
                                 ///< PLEXOS's per-(Reserve, Generator)
                                 ///< ``Min Provision`` / ``Min Spinning
                                 ///< Provision`` / ``Min Replacement
                                 ///< Provision`` properties.
  OptTBRealFieldSched drmin {};  ///< Minimum down-reserve provision floor
                                 ///< [MW] per-(stage, block).  Same
                                 ///< semantics as ``urmin`` for the
                                 ///< down-reserve column.

  OptTBRealFieldSched
      ur_capacity_factor {};  ///< Up-reserve capacity factor [p.u.] —
                              ///< per-(stage, block); accepts a scalar
                              ///< (broadcasts), a 2-D nested array, or a
                              ///< file-backed schedule.
  OptTBRealFieldSched
      dr_capacity_factor {};  ///< Down-reserve capacity factor [p.u.] —
                              ///< same shapes as ``ur_capacity_factor``.

  OptTBRealFieldSched
      ur_provision_factor {};  ///< Up-reserve provision factor [p.u.] —
                               ///< per-(stage, block); same shapes as
                               ///< ``ur_capacity_factor``.
  OptTBRealFieldSched
      dr_provision_factor {};  ///< Down-reserve provision factor [p.u.] —
                               ///< per-(stage, block).

  OptTBRealFieldSched urcost {};  ///< Up-reserve bid cost [$/MW] —
                                  ///< per-(stage, block).
  OptTBRealFieldSched drcost {};  ///< Down-reserve bid cost [$/MW] —
                                  ///< per-(stage, block).

  /// @name Extended (CEN SSCC) provision model
  /// @{
  /// Per-zone effectiveness factors, parallel to `reserve_zones`
  /// (minuta (A.3)–(A.12)): the SAME up column enters zone `z`'s
  /// requirement row with `ur_provision_factor · ur_zone_factors[z]`.
  /// One CPF+ variable then serves the 5 min, 10 s and normal
  /// requirements, each with its own factor.  Missing / short = 1.0.
  Array<Real> ur_zone_factors {};
  Array<Real> dr_zone_factors {};  ///< Down analogue of `ur_zone_factors`
  /// Shared headroom (minuta (A.23)–(A.24)): every provision of the same
  /// generator with this flag shares ONE pair of rows
  /// `p + Σ r_up ≤ Pmax(·u)` and `p − Σ r_dn ≥ Pmin(·u)` instead of one
  /// pair per provision (CPF + CSF + CTF on one unit).
  OptBool shared_headroom {};
  /// Offline (cold) up reserve (minuta (A.19), CTF "en frío"): the up
  /// column needs no headroom and, when the generator has a Commitment,
  /// is gated `r_up ≤ urmax · (1 − u)` (offered only while OFF).
  OptBool offline {};
  /// Symmetric provision (minuta (A.13)): `r_up = r_dn` every block.
  OptBool symmetric {};
  /// @}

  /// @name Native BESS per-side response caps
  /// PLEXOS `Max Response` / `Max Charge Response` of a battery in a
  /// reserve.  Only read when the generator is the discharge generator
  /// of a Battery with `reserve_sustain_hours`: the provision is split
  /// `r = r_dis + r_chg`, the discharge side bounded by its headroom
  /// (up: `Pd − pd`; down: `pd`, only with `reserve_discharge_credit`)
  /// and the charge side by its own (up: `pc`; down: `Pc − pc`), each
  /// capped by these schedules [MW].  Unset = no cap on that side.
  /// @{
  OptTBRealFieldSched urmax_response {};  ///< Up from more discharge
  OptTBRealFieldSched urmax_charge_response {};  ///< Up from less charge
  OptTBRealFieldSched drmax_response {};  ///< Down from less discharge
  OptTBRealFieldSched drmax_charge_response {};  ///< Down from more charge
  /// Expected deployment energy of THIS provision [MWh per MW per hour],
  /// charged to the battery SoC; overrides
  /// `Battery.reserve_energy_usage_up/down` (PLEXOS CF_*_COMP coefficient
  /// per reserve, e.g. CSF 0.3, CPF/CTF 0).
  OptTBRealFieldSched ur_energy_usage {};
  OptTBRealFieldSched dr_energy_usage {};  ///< Down analogue
  /// @}

  /// @name SSCC known variable-cost adders
  /// Each field is optional and off by default.  They add to the
  /// objective coefficient of the provision column (per MW·h held):
  ///
  ///   c = urcost + ur_partload_cost
  ///         + ur_activation_factor · (ur_activation_cost
  ///                                   + ur_degradation_cost)
  ///
  /// and are written separately (`ReserveProvision/up_partload_cost`,
  /// `up_activation_cost`, `up_degradation_cost` [$/h]) so a study can
  /// split θ = known variable costs + derived maintenance delta.
  /// `urcost` stays the remaining direct provision cost (wear).
  /// @{
  /// Part-load efficiency penalty of holding reserve [$/MW·h] (thermal
  /// unit backed off its best-efficiency point).  Alternative: model the
  /// part-load heat rate itself with `Generator.heat_rate_segments`.
  OptTBRealFieldSched ur_partload_cost {};
  OptTBRealFieldSched dr_partload_cost {};  ///< Down analogue
  /// Activation factor γ [MWh deployed per MW·h held, 0..1]: expected
  /// energy of the reserve that is actually deployed (CEN "factor de
  /// activación", PCP objective `C_disp + C_act·γ`).  Needed by the
  /// activation and degradation costs; unset = 0.
  OptTBRealFieldSched ur_activation_factor {};
  OptTBRealFieldSched dr_activation_factor {};  ///< Down analogue
  /// Cost of the deployed energy [$/MWh].  Unset with γ set: the
  /// generator's SRMC (fuel + VOM) for UP reserve, 0 for DOWN reserve
  /// (the fuel saved is not credited: no negative reserve costs).
  OptTBRealFieldSched ur_activation_cost {};
  OptTBRealFieldSched dr_activation_cost {};  ///< Down analogue
  /// Degradation cost per MWh of deployed throughput [$/MWh] (BESS
  /// cycling of activated reserve), applied to γ·r.
  OptTBRealFieldSched ur_degradation_cost {};
  OptTBRealFieldSched dr_degradation_cost {};  ///< Down analogue
  /// @}
};

}  // namespace gtopt
