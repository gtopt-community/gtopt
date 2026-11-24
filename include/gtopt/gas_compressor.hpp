/**
 * @file      gas_compressor.hpp
 * @brief     Gas compressor station: ratio bounds in π + fuel model
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A ``GasCompressor`` moves gas from ``gas_node_in`` (suction) to
 * ``gas_node_out`` (discharge), flow ``Ψ ∈ [0, fmax]`` km³/h, with
 *
 *     π_out ≤ max_ratio² · π_in            (ratio)
 *     π_out ≥ π_in                          (no expansion, unless
 *                                            allow_bypass)
 *
 * and one of three fuel models (``fuel_model``):
 *
 *   * ``none`` — no fuel (a bypassed station: ``max_ratio = 1`` and
 *     ``allow_bypass`` make it a lossless regulator);
 *   * ``fixed_fraction`` — ``LC = γ_ff Ψ``; ``γ_ff = fuel_fraction`` or
 *     ``γ (max_ratio^α − 1)`` (the Saldarriaga fuel at the maximum ratio);
 *   * ``saldarriaga`` — ``LC ≥ c_Ψ Ψ + c_π (π_out − β0² π_in)``, the
 *     first-order Taylor plane in π of ``LC = γ (β^α − 1) Ψ`` at the
 *     operating point (``op_flow``, ``op_ratio``, ``op_suction_pressure``);
 *     several ``op_ratio`` values give one plane each (max of planes).
 *
 * ``γ`` defaults to the adiabatic-head value from ``efficiency`` and
 * ``driver_efficiency``; ``α`` to (κ − 1)/κ = 0.2308.
 *
 * ``drive: "gas"`` (default) draws ``LC`` from the suction node;
 * ``drive: "electric"`` draws ``power = κ · LC`` MW from ``bus``
 * (``κ = LHV · driver_efficiency / motor_efficiency``), bounded by
 * ``power_max``.
 *
 * ### JSON example
 * ```json
 * {"uid": 1, "name": "BOOSTER", "gas_node_in": "RL", "gas_node_out": "R",
 *  "fmax": 150, "max_ratio": 1.6, "fuel_model": "saldarriaga",
 *  "op_flow": 80, "op_ratio": [1.3], "op_suction_pressure": 25,
 *  "allow_bypass": true, "drive": "electric", "bus": "B",
 *  "power_max": 5}
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
 * @struct GasCompressor
 * @brief Compressor station between two gas nodes
 */
struct GasCompressor
{
  /// Canonical class name (LP labels, outputs, PAMPL ``gas_compressor``).
  static constexpr LPClassName class_name {"GasCompressor"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< "transmission", "fuel_gas_booster", …

  SingleId gas_node_in {unknown_uid};  ///< Suction node
  SingleId gas_node_out {unknown_uid};  ///< Discharge node

  /// Throughput capacity [km³/h] per stage.  Required.
  OptTRealFieldSched fmax {};
  /// Maximum pressure ratio β̄ = p_out / p_in (default 1: no boost).
  OptReal max_ratio {};

  /// ``"none"``, ``"fixed_fraction"`` (default) or ``"saldarriaga"``.
  OptName fuel_model {};
  /// Fixed fuel fraction γ_ff [p.u. of throughput].
  OptReal fuel_fraction {};
  /// Saldarriaga γ.  Unset = adiabatic-head value.
  OptReal gamma {};
  /// Saldarriaga α (default (κ − 1)/κ = 0.2308).
  OptReal alpha {};
  /// Isentropic compressor efficiency η_c (default 0.80).
  OptReal efficiency {};
  /// Driver (gas turbine) efficiency η_driver (default 0.33).
  OptReal driver_efficiency {};
  /// Operating-point flow Ψ0 [km³/h] (default 0.6 fmax).
  OptReal op_flow {};
  /// Operating-point ratio(s) β0 (default 1 + (β̄ − 1)/2); one Taylor
  /// plane per value.
  Array<Real> op_ratio {};
  /// Operating-point suction pressure [bar] (default mid suction band).
  OptReal op_suction_pressure {};

  /// Allow π_out < π_in (bypass / regulator).  Default false for
  /// ``saldarriaga``, true otherwise.
  OptBool allow_bypass {};
  /// Open valve with signed flow in [−fmax, fmax] and π_out = π_in
  /// (meshed networks).  Requires ``max_ratio`` 1 and ``fuel_model``
  /// ``"none"``; compressors stay fixed-direction.  Default false.
  OptBool bidirectional {};

  /// ``"gas"`` (default) or ``"electric"``.
  OptName drive {};
  /// Power bus of an electric drive.
  OptSingleId bus {};
  /// Motor efficiency of an electric drive (default 0.95).
  OptReal motor_efficiency {};
  /// Electric drive rating [MW].  Unset = unbounded.
  OptReal power_max {};

  /// Parsed ``fuel_model`` (``fixed_fraction`` when unset, nullopt =
  /// unknown).
  [[nodiscard]] constexpr auto fuel_model_enum() const noexcept
      -> std::optional<GasCompressorFuelModel>
  {
    return gas_enum_or(fuel_model, GasCompressorFuelModel::fixed_fraction);
  }

  /// Parsed ``drive`` (``gas`` when unset, nullopt = unknown).
  [[nodiscard]] constexpr auto drive_enum() const noexcept
      -> std::optional<GasCompressorDrive>
  {
    return gas_enum_or(drive, GasCompressorDrive::gas);
  }
};

}  // namespace gtopt
