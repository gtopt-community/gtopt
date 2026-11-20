/**
 * @file      hydrogen_demand.hpp
 * @brief     Hydrogen offtake (sink) at a HydrogenNode
 * @date      Fri Nov 20 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * ``HydrogenDemand`` withdraws hydrogen (MWh_LHV) from a
 * ``HydrogenNode``.  It closes the hydrogen-economy topology
 *
 *   Bus → electrolyser (CarrierConverter, η) → HydrogenNode → HydrogenDemand
 *
 * which previously had no sink: without it the node balance forces
 * every electrolyser to zero unless a ``HydrogenStorage`` absorbs the
 * production.
 *
 * Two complementary ways to state the demand:
 *
 *   * a per-block offtake rate window ``[lmin, lmax]`` (MW_LHV), e.g. a
 *     fixed industrial offtake profile (``lmin = lmax``);
 *   * a **per-stage energy requirement** ``[min_energy, max_energy]``
 *     (MWh_LHV per stage) on ``Σ_b dur_b · load_b``.  This is the
 *     "production target over a period" used by capacity-outlook models
 *     (AEMO ISP: weekly / monthly H₂ targets met by flexible
 *     electrolysers): the offtake can happen in any block of the stage,
 *     i.e. intra-stage hydrogen storage is assumed free and unlimited.
 *     ``min_energy_cost`` makes the minimum soft (shortfall slack priced
 *     per MWh_LHV); unset = hard.
 *
 * An optional ``price`` [$ / MWh_LHV] values the offtake (objective
 * ``−price · dur · load``), giving a price-responsive hydrogen buyer.
 *
 * The dual of the stage energy row is the marginal cost of hydrogen
 * for the stage [$ / MWh_LHV]; the node balance duals give the
 * per-block hydrogen price.
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "h2_cq", "hydrogen_node": "h2_cq",
 *  "min_energy": 1919.7, "max_energy": 1919.7}
 * ```
 *
 * @see hydrogen_node.hpp      balance node
 * @see carrier_converter.hpp  electrolyser feeding the node
 * @see hydrogen_storage.hpp   explicit storage alternative
 */

#pragma once

#include <gtopt/field_sched.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/single_id.hpp>

namespace gtopt
{

/**
 * @struct HydrogenDemand
 * @brief Hydrogen offtake at a HydrogenNode with an optional per-stage
 *        energy requirement.
 */
struct HydrogenDemand
{
  /// Canonical class-name used in LP row labels and PAMPL element
  /// resolution.  Matches the JSON ``hydrogen_demand_array`` entry name.
  static constexpr LPClassName class_name {"HydrogenDemand"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Operational status (default: active)
  OptName type {};  ///< Optional tag ("domestic", "export", "commodity", …)

  /// Reference to the ``HydrogenNode`` the offtake withdraws from.
  OptSingleId hydrogen_node {};

  /// Maximum offtake rate [MW_LHV] per (stage, block).  Unset = unbounded.
  OptTBRealFieldSched lmax {};
  /// Minimum offtake rate [MW_LHV] per (stage, block).  Default 0.
  OptTBRealFieldSched lmin {};

  /// Minimum hydrogen energy delivered per stage [MWh_LHV / stage]:
  /// ``Σ_b dur_b · load_b ≥ min_energy``.
  OptTRealFieldSched min_energy {};
  /// Maximum hydrogen energy delivered per stage [MWh_LHV / stage]:
  /// ``Σ_b dur_b · load_b ≤ max_energy``.
  OptTRealFieldSched max_energy {};
  /// Shortfall penalty below ``min_energy`` [$ / MWh_LHV].  Set and
  /// > 0 ⇒ soft minimum; unset ⇒ hard.
  OptTRealFieldSched min_energy_cost {};

  /// Value of the offtake [$ / MWh_LHV]: adds ``−price · dur · load``
  /// to the objective (price-responsive buyer).  Default 0.
  OptTBRealFieldSched price {};
};

}  // namespace gtopt
