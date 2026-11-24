/**
 * @file      gas_supply.hpp
 * @brief     Gas injection (field, pipeline import, LNG regasification)
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A ``GasSupply`` injects gas [km³/h] into a ``GasNode`` in
 * ``[fmin, fmax]`` at ``cost`` [$ / km³].  ``type`` tags the source:
 * ``field``, ``pipeline_import`` or ``lng_regas`` (regasification
 * send-out of an LNG terminal; the tank itself is modelled with
 * ``LngTerminal``).
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "SUPPLY", "gas_node": "S", "type": "lng_regas",
 *  "fmax": 1000, "cost": 300}
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
 * @struct GasSupply
 * @brief Gas source injecting into a GasNode
 */
struct GasSupply
{
  /// Canonical class name (LP labels, outputs, PAMPL ``gas_supply``).
  static constexpr LPClassName class_name {"GasSupply"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< "field", "pipeline_import", "lng_regas", …

  /// The ``GasNode`` the supply injects into (uid or name).
  SingleId gas_node {unknown_uid};

  /// Minimum injection [km³/h] per (stage, block).  Default 0.
  OptTBRealFieldSched fmin {};
  /// Maximum injection [km³/h] per (stage, block).  Unset = unbounded.
  OptTBRealFieldSched fmax {};
  /// Gas cost [$ / km³] per stage.  Default 0.
  OptTRealFieldSched cost {};
  /// Volume available over each stage [flow unit · h], e.g. an LNG or
  /// pipeline-gas allotment: Σ_b Δt_b f_b ≤ volume (fmax stays the
  /// rate cap).  Unset = no volume limit.
  OptReal volume {};
};

}  // namespace gtopt
