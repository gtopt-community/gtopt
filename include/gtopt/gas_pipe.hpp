/**
 * @file      gas_pipe.hpp
 * @brief     Gas pipeline: Weymouth outer approximation + line pack
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A ``GasPipe`` carries gas from ``gas_node_a`` to ``gas_node_b``
 * (fixed direction, radial network, flow in ``[0, fmax]`` km³/h).  The
 * steady-state Weymouth equation ``π_a − π_b = K f²`` is relaxed to the
 * convex ``π_a − π_b ≥ K f²`` and outer-approximated by ``segments``
 * tangent planes
 *
 *     π_a − π_b ≥ K (2 t_k f − t_k²),   k = 1..N
 *
 * at the tangent points of ``tangent_layout`` (``uniform`` or
 * ``relative_pressure``).  ``K`` is ``weymouth_k`` when given, otherwise
 * derived from ``length``, ``diameter``, ``efficiency``, ``gravity``,
 * ``temperature`` and ``z`` (SI Weymouth).
 *
 * With ``linepack: true`` the pipe also stores gas: inlet and outlet
 * flows split (``flow_in``, ``flow_out``), the Weymouth rows use their
 * average, and the line pack ``LP = c (p_a + p_b)/2`` (pressure
 * linearised at ``linepack_pressure_ref``) follows
 * ``LP_b = LP_{b−1} + (flow_in − flow_out) Δt_b`` within each stage,
 * starting from ``linepack_ini`` and closing with ``LP_end ≥
 * linepack_ini`` (daily cycle).
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "S_T", "gas_node_a": "S", "gas_node_b": "T",
 *  "length": 80, "diameter": 406, "fmax": 300, "segments": 8,
 *  "linepack": true}
 * ```
 */

#pragma once

#include <gtopt/field_sched.hpp>
#include <gtopt/gas_enums.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/single_id.hpp>

namespace gtopt
{

/**
 * @struct GasPipe
 * @brief Gas pipeline (radial tangents or linearised) with line pack
 */
struct GasPipe
{
  /// Canonical class name (LP labels, outputs, PAMPL ``gas_pipe``).
  static constexpr LPClassName class_name {"GasPipe"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< Optional tag ("transmission", "lateral", …)

  SingleId gas_node_a {unknown_uid};  ///< Inlet (upstream) node
  SingleId gas_node_b {unknown_uid};  ///< Outlet (downstream) node

  /// Weymouth constant K [bar² / (km³/h)²].  Unset = from the geometry.
  OptReal weymouth_k {};
  OptReal length {};  ///< Length [km]
  OptReal diameter {};  ///< Inner diameter [mm]
  OptReal efficiency {};  ///< Pipeline efficiency E [p.u.] (default 0.92)
  /// Gas specific gravity (default ``gas_options.gas_specific_gravity``)
  OptReal gravity {};
  /// Flowing temperature [K] (default ``gas_options.gas_temperature``)
  OptReal temperature {};
  /// Compressibility factor (default ``gas_options.compressibility``)
  OptReal z {};
  /// Elevation exponent ``s`` of ``π_a − e^s π_b = K_e f|f|``.  Unset =
  /// ``2 g M (h_b − h_a) / (Z R T)`` from the end nodes' ``elevation``.
  OptReal elevation_exponent {};

  /// Flow capacity [km³/h] per stage (also the tangent range).  Required.
  OptTRealFieldSched fmax {};
  /// Number of Weymouth tangents N (default 8).
  OptInt segments {};
  /// ``"uniform"`` (default) or ``"relative_pressure"``.
  OptName tangent_layout {};

  /// ``"radial"`` (default): fixed direction a → b, tangent outer
  /// approximation (exact on radial networks).  ``"linearized"``: signed
  /// flow in [−fmax, fmax] and one equality per block linearised at a
  /// reference flow f₀ (meshed networks; re-linearised by the SLP of
  /// ``gas_options.slp_iterations``).
  OptName weymouth_mode {};
  /// Linearised mode: ``"secant"`` (default) π_a − e^s π_b = K_e|f₀| f,
  /// or ``"tangent"`` π_a − e^s π_b = 2 K_e|f₀| f − K_e f₀|f₀|.
  OptName linearization {};
  /// Linearised mode: signed reference flow f₀ [km³/h] of the first
  /// pass.  Unset = ``slp_initial_flow`` · fmax (a warning is logged
  /// when no SLP iteration corrects it).
  OptReal reference_flow {};

  /// Model line pack (pipe storage).  Default false.
  OptBool linepack {};
  /// Line pack per bar of average pressure [km³ / bar].  Unset = from
  /// ``length`` and ``diameter``.
  OptReal linepack_coeff {};
  /// Linearisation pressure p_r [bar].  Unset = mean of the end nodes'
  /// pmin / pmax.
  OptReal linepack_pressure_ref {};
  /// Initial (and minimum terminal) line pack [km³].  Unset = from the
  /// end nodes' ``p_init``.
  OptReal linepack_ini {};

  /// Parsed ``weymouth_mode`` (``radial`` when unset, nullopt = unknown).
  [[nodiscard]] constexpr auto weymouth_mode_enum() const noexcept
      -> std::optional<GasWeymouthMode>
  {
    return gas_enum_or(weymouth_mode, GasWeymouthMode::radial);
  }

  /// Parsed ``tangent_layout`` (``uniform`` when unset, nullopt =
  /// unknown).
  [[nodiscard]] constexpr auto tangent_layout_enum() const noexcept
      -> std::optional<gas::TangentLayout>
  {
    return gas_enum_or(tangent_layout, gas::TangentLayout::uniform);
  }

  /// Parsed ``linearization`` (``secant`` when unset, nullopt = unknown).
  [[nodiscard]] constexpr auto linearization_enum() const noexcept
      -> std::optional<GasLinearization>
  {
    return gas_enum_or(linearization, GasLinearization::secant);
  }
};

}  // namespace gtopt
