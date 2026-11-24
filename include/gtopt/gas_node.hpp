/**
 * @file      gas_node.hpp
 * @brief     Natural-gas network node (squared pressure + gas balance)
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A ``GasNode`` carries, per block, the squared pressure ``π = p²``
 * [bar²] bounded by ``[pmin², pmax²]`` and a gas balance row [km³/h]
 * whose dual is the nodal gas price [$ / km³].  Pipes, compressors,
 * supplies, demands and gas turbines stamp their flows into the balance.
 *
 * ``pressure_cost`` [$ / (bar² h)] prices π in the objective.  A tiny
 * negative value (e.g. −1e-4) is the "pressure tie-break": among the
 * LP-optimal dispatches it selects the highest pressure profile, i.e.
 * the pressures of the exact Weymouth equation from the sources down,
 * instead of an arbitrary degenerate vertex at ``pmin``.
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "S", "pmin": 30, "pmax": 70, "p_init": 70,
 *  "pressure_cost": -1e-4}
 * ```
 *
 * @see gas_pipe.hpp, gas_compressor.hpp, gas_supply.hpp, gas_demand.hpp,
 *      gas_turbine.hpp
 */

#pragma once

#include <gtopt/field_sched.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @struct GasNode
 * @brief Gas-network node with a squared-pressure column and a balance row
 */
struct GasNode
{
  /// Canonical class name (LP labels, outputs, PAMPL ``gas_node``).
  static constexpr LPClassName class_name {"GasNode"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< Optional tag ("city_gate", "plant", "junction", …)

  /// Minimum pressure [bar abs] per stage.  Default 0.
  OptTRealFieldSched pmin {};
  /// Maximum pressure [bar abs] per stage.  Unset = unbounded π.
  OptTRealFieldSched pmax {};
  /// Objective coefficient of π [$ / (bar² h)].  Negative = reward
  /// (pressure tie-break).  Default 0.
  OptTRealFieldSched pressure_cost {};
  /// Initial pressure [bar abs]: sets the initial line pack of the
  /// pipes attached to this node.
  OptReal p_init {};
  /// Elevation above the datum [m] (default 0): enters the pipe
  /// equation of the attached pipes through ``e^s``.
  OptReal elevation {};
};

}  // namespace gtopt
