/**
 * @file      gas_demand.hpp
 * @brief     Exogenous gas load at a GasNode with priced shedding
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A ``GasDemand`` withdraws ``load`` [km³/h] from a ``GasNode`` (it
 * enters the node balance right-hand side).  With ``fail_cost`` set, a
 * shedding column ``fail ∈ [0, load]`` priced at ``fail_cost`` [$ / km³]
 * relaxes it; without ``fail_cost`` the load is hard.
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "CITY", "gas_node": "T", "load": 60,
 *  "fail_cost": 3000}
 * ```
 */

#pragma once

#include <gtopt/field_sched.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/single_id.hpp>

namespace gtopt
{

/**
 * @struct GasDemand
 * @brief Gas load (sink) with optional priced shedding
 */
struct GasDemand
{
  /// Canonical class name (LP labels, outputs, PAMPL ``gas_demand``).
  static constexpr LPClassName class_name {"GasDemand"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< Optional tag ("residential", "industrial", …)

  /// The ``GasNode`` the load withdraws from (uid or name).
  SingleId gas_node {unknown_uid};

  /// Gas load [km³/h] per (stage, block).  Default 0.
  OptTBRealFieldSched load {};
  /// Shedding cost [$ / km³] per stage.  Unset = hard load (no shedding).
  OptTRealFieldSched fail_cost {};
};

}  // namespace gtopt
