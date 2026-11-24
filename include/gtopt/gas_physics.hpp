/**
 * @file      gas_physics.hpp
 * @brief     Steady-state natural-gas physics used by the gas-network LP
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Pure functions (no LP state) shared by ``GasPipeLP``,
 * ``GasCompressorLP`` and ``GasTurbineLP``:
 *
 *   * Weymouth constant ``K`` [bar² / (km³/h)²] of a pipe and the tangent
 *     points of its outer approximation (layouts ``uniform`` and
 *     ``relative_pressure``);
 *   * line-pack coefficient [km³ / bar];
 *   * compressor fuel: adiabatic-head fuel fraction, the physics γ of
 *     Saldarriaga's ``LC = γ (β^α − 1) Ψ`` and its first-order Taylor
 *     plane written in squared pressure π = p²;
 *   * electric-drive conversion κ [MW per km³/h of fuel equivalent];
 *   * the concave fuel-gas pressure capability ``P ≤ s π + c`` of a gas
 *     turbine (hard stop + optional derate).
 *
 * Units: flow km³/h (standard), pressure bar abs, π bar², line pack km³.
 * The formulas mirror the gas-network prototype (``gasnet2gtopt``) so the
 * native elements reproduce its LP exactly.
 *
 * @see docs/formulation/mathematical-formulation.md, section "Gas network"
 */

#pragma once

#include <optional>
#include <string_view>
#include <vector>

#include <gtopt/gas_enums.hpp>

namespace gtopt::gas
{

/// Standard (base) temperature [K].
inline constexpr double kBaseTemperature = 288.15;
/// Standard (base) pressure [kPa].
inline constexpr double kBasePressureKpa = 101.325;
/// Standard (base) pressure [bar].
inline constexpr double kBasePressureBar = 1.01325;
/// Universal gas constant [J / (mol K)].
inline constexpr double kGasConstant = 8.314;
/// Lower heating value of natural gas [J / kg].
inline constexpr double kLhvPerKg = 47.0e6;
/// Isentropic exponent κ of natural gas.
inline constexpr double kIsentropicExponent = 1.3;
/// Saldarriaga's α = (κ − 1) / κ for natural gas (≈ 0.2308).
inline constexpr double kAlpha =
    (kIsentropicExponent - 1.0) / kIsentropicExponent;

/// Pipe geometry / gas properties feeding the Weymouth constant.
struct PipeGeometry
{
  double length_km {0.0};  ///< Pipe length [km]
  double diameter_mm {0.0};  ///< Inner diameter [mm]
  double efficiency {0.92};  ///< Pipeline efficiency factor E [p.u.]
  double gravity {0.6};  ///< Gas specific gravity G (air = 1)
  double temperature {kBaseTemperature};  ///< Flowing temperature [K]
  double z {0.9};  ///< Compressibility factor Z
};

/// Weymouth constant ``K`` [bar² / (km³/h)²]: ``p_a² − p_b² = K q|q|``.
///
/// SI Weymouth (Menon, *Gas Pipeline Hydraulics*, 2005, eq. 2.62),
/// converted from (m³/d, kPa) to (km³/h, bar).
[[nodiscard]] double weymouth_k(const PipeGeometry& g);

/// Standard gravity [m/s²].
inline constexpr double kGravityAcceleration = 9.80665;
/// Molar mass of air [kg/mol]; gas molar mass = specific gravity × this
/// (elevation exponent).
inline constexpr double kAirMolarMass = 0.028964;
/// Molar mass of air as rounded in the Weymouth / standard-density
/// constants [kg/mol] (keeps K identical to the reference converter).
inline constexpr double kWeymouthAirMolarMass = 0.02896;

/// Elevation exponent ``s = 2 g M (h_b − h_a) / (Z R T)`` of a pipe
/// climbing ``dh_m = h_b − h_a`` metres (dimensionless).  The pipe
/// equation becomes ``π_a − e^s π_b = K_e f|f|``.
[[nodiscard]] double elevation_exponent(double dh_m,
                                        double gravity,
                                        double z,
                                        double temperature) noexcept;

/// Effective-length factor ``(e^s − 1) / s`` (→ 1 as s → 0, no
/// division by zero): ``K_e = K · elevation_k_factor(s)``.
[[nodiscard]] double elevation_k_factor(double s) noexcept;

/// Line pack per bar of average pressure [km³ (std) / bar].
[[nodiscard]] double linepack_coeff(double length_km,
                                    double diameter_mm,
                                    double z = 0.9,
                                    double temperature = kBaseTemperature);

/// Parses ``"uniform"`` / ``"relative_pressure"``; nullopt = unknown.
[[nodiscard]] std::optional<TangentLayout> parse_tangent_layout(
    std::string_view s) noexcept;

/// Tangent points ``t_k`` (k = 1..nseg) of the outer approximation of
/// ``K f²`` on ``[0, fmax]``.  ``p_in`` (inlet pressure, bar) is only
/// used by the ``relative_pressure`` layout.
[[nodiscard]] std::vector<double> tangent_points(double k_weymouth,
                                                 double fmax,
                                                 int nseg,
                                                 TangentLayout layout,
                                                 double p_in);

/// Adiabatic-head fuel burnt per unit throughput at pressure ratio
/// ``ratio`` [p.u.]:  ``H = Z R T / M · κ/(κ−1) · (r^α − 1) / η_c`` and
/// ``fuel = H / (η_driver · LHV)``.
[[nodiscard]] double compressor_fuel_fraction(double ratio,
                                              double efficiency = 0.80,
                                              double driver_efficiency = 0.33);

/// Physics-consistent γ of ``LC = γ (β^α − 1) Ψ`` (≈ 0.043 with the
/// default efficiencies): ``compressor_fuel_fraction(2) / (2^α − 1)``.
[[nodiscard]] double adiabatic_gamma(double efficiency = 0.80,
                                     double driver_efficiency = 0.33,
                                     double alpha = kAlpha);

/// Thermal power of 1 km³/h (std) of gas at LHV [MW] (≈ 9.6).
[[nodiscard]] double gas_lhv_mw_per_km3h(double gravity = 0.6);

/// MW of electric motor input per km³/h of Saldarriaga fuel equivalent:
/// ``κ = LHV · η_driver / η_motor`` (≈ 3.3).
[[nodiscard]] double electric_kappa(double driver_efficiency = 0.33,
                                    double motor_efficiency = 0.95);

/// First-order Taylor plane of Saldarriaga's compressor fuel in π:
/// ``LC ≈ c_psi Ψ + c_pi (π_out − r0 π_in)`` (no constant term, Euler).
struct SaldarriagaPlane
{
  double c_psi {0.0};  ///< ∂LC/∂Ψ = γ (β0^α − 1)
  double c_pi {0.0};  ///< ∂LC/∂π_out = γ (α/2) Ψ0 r0^(α/2 − 1) / π_in0
  double r0 {1.0};  ///< β0²
};

[[nodiscard]] SaldarriagaPlane saldarriaga_plane(
    double gamma, double alpha, double psi0, double beta0, double p_in0);

/// One concave capability row ``P ≤ slope · π + intercept``.
struct CapabilityRow
{
  double slope {0.0};
  double intercept {0.0};
};

/// Concave piecewise-linear fuel-gas pressure capability of a gas
/// turbine:
///
///   * hard stop: ``cap = 0`` at ``(p_min − band)²`` rising to
///     ``out_min · pmax`` at ``p_min²``;
///   * derate (``p_nom > p_min``): line from ``(p_min², out_min pmax)``
///     to ``(p_nom², pmax)``; ``out_min`` defaults to ``p_min / p_nom``.
///
/// Without derate (``p_nom ≤ p_min``) ``out_min = 1`` and a single row is
/// returned.  A derate row steeper than the hard-stop ramp is dropped
/// (not concave); ``derate_dropped`` reports it.
struct CapabilityRows
{
  std::vector<CapabilityRow> rows {};
  bool derate_dropped {false};
};

[[nodiscard]] CapabilityRows plant_capability_rows(
    double pmax, double p_min, double band, double p_nom, double out_min);

}  // namespace gtopt::gas
