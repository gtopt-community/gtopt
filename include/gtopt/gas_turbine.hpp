/**
 * @file      gas_turbine.hpp
 * @brief     Gas-fired generator ↔ GasNode link (fuel + pressure gate)
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A ``GasTurbine`` couples an existing ``Generator`` to its fuel-gas
 * delivery ``GasNode`` — the gas analogue of the hydro ``Turbine``:
 *
 *   * fuel: ``heat_rate · P`` [km³/h] is withdrawn from the node balance
 *     (heat_rate in km³/MWh, 0.200 ≈ 7.06 MMBtu/MWh);
 *   * pressure gate (``min_pressure`` set): ``P ≤ s π + c`` concave
 *     capability rows — a steep ramp from 0 at ``(min_pressure −
 *     pressure_band)²`` to the full (or derated) output at
 *     ``min_pressure²``; with ``nominal_pressure > min_pressure`` a derate
 *     line from ``derate_min_output · pmax`` at ``min_pressure`` up to
 *     ``pmax`` at ``nominal_pressure``.  The negative intercept keeps
 *     ``π ≥ (min_pressure − pressure_band)²`` even when ``P = 0`` (an
 *     LP cannot express "off below the band"; that needs a binary).
 *
 * ``pmax`` is the generator's ``pmax`` (or ``capacity``) in each block.
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "G_R_gas", "generator": "G_R", "gas_node": "R",
 *  "heat_rate": 0.2, "min_pressure": 30, "pressure_band": 0.5}
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
 * @struct GasTurbine
 * @brief Generator fuel-gas link with heat rate and pressure gate
 */
struct GasTurbine
{
  /// Canonical class name (LP labels, outputs, PAMPL ``gas_turbine``).
  static constexpr LPClassName class_name {"GasTurbine"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< Optional tag ("ccgt", "ocgt", …)

  SingleId generator {unknown_uid};  ///< The gas-fired Generator
  SingleId gas_node {unknown_uid};  ///< Its fuel-gas delivery GasNode

  /// Fuel per unit output [km³ / MWh] per (stage, block).  Default 0.
  OptTBRealFieldSched heat_rate {};
  /// Turbine minimum fuel-gas pressure [bar abs].  Unset = no gate.
  OptReal min_pressure {};
  /// Supply tolerance below ``min_pressure`` [bar] (default 0.5).
  OptReal pressure_band {};
  /// Pressure of full output [bar abs]; > ``min_pressure`` enables the
  /// derate line.
  OptReal nominal_pressure {};
  /// Output fraction at ``min_pressure`` under derate (default
  /// ``min_pressure / nominal_pressure``).
  OptReal derate_min_output {};
};

}  // namespace gtopt
