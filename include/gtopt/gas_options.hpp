/**
 * @file      gas_options.hpp
 * @brief     Network-wide gas properties of the gas-network model
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * ```json
 * {"options": {"gas_options": {"gas_specific_gravity": 0.6,
 *                              "gas_temperature": 288.15,
 *                              "compressibility": 0.9}}}
 * ```
 *
 * Every field is a network default that a `GasPipe` may override
 * (`gravity`, `temperature`, `z`).  They feed the Weymouth constant
 * from geometry and the elevation exponent
 * ``s = 2 g M (h_b − h_a) / (Z R T)``.
 */

#pragma once

#include <gtopt/basic_types.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

/// Default gas specific gravity (air = 1).
inline constexpr double kDefaultGasSpecificGravity = 0.6;
/// Default flowing gas temperature [K].
inline constexpr double kDefaultGasTemperature = 288.15;
/// Default gas compressibility factor Z.
inline constexpr double kDefaultGasCompressibility = 0.9;
/// Default SLP stopping tolerance on the relative flow change.
inline constexpr double kDefaultSlpTolerance = 1e-4;
/// Default SLP damping ω.
inline constexpr double kDefaultSlpDamping = 0.5;
/// Default minimum |f₀| as a fraction of fmax.
inline constexpr double kDefaultSlpMinFlow = 1e-3;
/// Default first-pass |f₀| as a fraction of fmax.
inline constexpr double kDefaultSlpInitialFlow = 0.1;
/// Default SLP proximal cost ρ [$ / flow unit / h] on |f̄ − f₀|.
inline constexpr double kDefaultSlpProximalCost = 1e-6;

/**
 * @struct GasOptions
 * @brief Network-level gas properties (``options.gas_options``)
 */
struct GasOptions
{
  OptReal gas_specific_gravity {};  ///< G (air = 1), default 0.6
  OptReal gas_temperature {};  ///< Flowing temperature [K], default 288.15
  OptReal compressibility {};  ///< Z, default 0.9

  /// Sequential-LP passes re-linearising ``weymouth_mode: linearized``
  /// pipes at the last solution (monolithic; default 0 = single pass).
  OptInt slp_iterations {};
  /// Stop when max |f − f₀| / max(|f₀|, f_min) < tolerance (1e-4).
  OptReal slp_tolerance {};
  /// Damping ω of f₀ ← f₀ + ω (f − f₀) (default 0.5; 1 = no damping).
  OptReal slp_damping {};
  /// Minimum |f₀| as a fraction of the pipe's fmax (default 1e-3).
  OptReal slp_min_flow {};
  /// Default |f₀| as a fraction of fmax when a pipe has no
  /// ``reference_flow`` (default 0.1).
  OptReal slp_initial_flow {};
  /// Cost ρ of the SLP proximal term ρ |f̄ − f₀| (default 1e-6, 0 = off):
  /// picks, among equal-cost LP vertices, the one nearest f₀.
  OptReal slp_proximal_cost {};
  /// Line pack closes each stage with at least its initial value
  /// (default true); false lets the pack be drawn down.
  OptBool linepack_cyclic {};

  [[nodiscard]] constexpr double specific_gravity() const noexcept
  {
    return gas_specific_gravity.value_or(kDefaultGasSpecificGravity);
  }
  [[nodiscard]] constexpr double temperature() const noexcept
  {
    return gas_temperature.value_or(kDefaultGasTemperature);
  }
  [[nodiscard]] constexpr double z() const noexcept
  {
    return compressibility.value_or(kDefaultGasCompressibility);
  }

  [[nodiscard]] constexpr int slp_max_iterations() const noexcept
  {
    return slp_iterations.value_or(0);
  }
  [[nodiscard]] constexpr double slp_tol() const noexcept
  {
    return slp_tolerance.value_or(kDefaultSlpTolerance);
  }
  [[nodiscard]] constexpr double slp_omega() const noexcept
  {
    return slp_damping.value_or(kDefaultSlpDamping);
  }
  [[nodiscard]] constexpr double slp_min_flow_fraction() const noexcept
  {
    return slp_min_flow.value_or(kDefaultSlpMinFlow);
  }
  [[nodiscard]] constexpr double slp_initial_flow_fraction() const noexcept
  {
    return slp_initial_flow.value_or(kDefaultSlpInitialFlow);
  }

  [[nodiscard]] constexpr double slp_proximal_cost_value() const noexcept
  {
    return slp_proximal_cost.value_or(kDefaultSlpProximalCost);
  }
  [[nodiscard]] constexpr bool linepack_is_cyclic() const noexcept
  {
    return linepack_cyclic.value_or(true);
  }

  void merge(const GasOptions& other)
  {
    merge_opt(linepack_cyclic, other.linepack_cyclic);
    merge_opt(slp_proximal_cost, other.slp_proximal_cost);
    merge_opt(slp_iterations, other.slp_iterations);
    merge_opt(slp_tolerance, other.slp_tolerance);
    merge_opt(slp_damping, other.slp_damping);
    merge_opt(slp_min_flow, other.slp_min_flow);
    merge_opt(slp_initial_flow, other.slp_initial_flow);
    merge_opt(gas_specific_gravity, other.gas_specific_gravity);
    merge_opt(gas_temperature, other.gas_temperature);
    merge_opt(compressibility, other.compressibility);
  }
};

}  // namespace gtopt
