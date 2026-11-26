/**
 * @file      line_thermal_resistance.hpp
 * @brief     Conductor temperature from losses: linear R(T) and R_Θ (TDPF)
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Formula layer of the electrothermal (temperature-dependent) loss
 * model of Frank, Sexauer & Mohagheghi, "Temperature-Dependent Power
 * Flow", IEEE Trans. Power Syst. 28(4):4007–4018, 2013,
 * https://doi.org/10.1109/TPWRS.2013.2266409 ("TDPF").  Pure,
 * allocation-free functions; no LP code depends on them yet.
 *
 * Model, in any consistent unit system (per-unit or SI per metre):
 *
 * - linear resistance  `R(T) = R_ref · (1 + α · (T − T_ref))`, with the
 *   "inferred zero-resistance temperature" form
 *   `α = 1 / (T_ref + T_F)` (aluminium `T_F = 228.1 °C`);
 * - lumped thermal law  `T = T_amb + R_Θ · P_loss`, `P_loss = I² · R(T)`;
 * - "simplified" rated-rise thermal resistance
 *   `R_Θ = ΔT_rated / (I_rated² · R(T_ref + ΔT_rated))`: the rated
 *   current heats the conductor exactly `ΔT_rated` above ambient
 *   (Frank's default `ΔT_rated = 25 °C`, `I_rated` = MVA rating at
 *   V = 1 pu).
 *
 * Because both laws are linear in T, the steady state at a fixed
 * current has the closed form (`k = α · R_Θ · I² · R_ref`):
 *
 *     T − T_amb = R_Θ · I² · R(T_amb) / (1 − k)
 *
 * which is the limit of the fixed-point (Picard) iteration
 * `T ← T_amb + R_Θ · I² · R(T)`; that iteration contracts with factor
 * `k`, and `k ≥ 1` is thermal runaway (no steady state).
 *
 * The IEEE Std 738 coupling (`ieee738_conductor_temperature`) solves the
 * full heat balance `q_c(T) + q_r(T) = q_s + I² R(T)` for T at a given
 * current, so the linear R_Θ model can be compared with (or calibrated
 * from) the DLR heat balance in `ieee738.hpp`.
 */

#pragma once

#include <optional>

#include <gtopt/ieee738.hpp>

namespace gtopt::line_thermal
{

/// Inferred zero-resistance temperature T_F of aluminium [°C] (Frank
/// et al. 2013; IEEE/IEC conductor tables).
inline constexpr double kAluminumInferredZero = 228.1;
/// Frank's default rated temperature rise over ambient [°C].
inline constexpr double kDefaultRatedRise = 25.0;

/// `α = 1 / (T_ref + T_F)`: temperature coefficient [1/°C] at `T_ref`.
[[nodiscard]] constexpr double alpha_from_inferred_zero(
    double reference_temperature,
    double inferred_zero = kAluminumInferredZero) noexcept
{
  return 1.0 / (reference_temperature + inferred_zero);
}

/// Linear temperature-dependent resistance `R(T) = r_ref·(1 + α·(T −
/// t_ref))`.  `r_ref` in any unit (pu, Ω, Ω/m).
struct LinearResistance
{
  double r_ref {};  ///< resistance at `t_ref`
  double alpha {};  ///< temperature coefficient α [1/°C] at `t_ref`
  double t_ref {25.0};  ///< reference temperature [°C]
};

/// `R(T)`.
[[nodiscard]] constexpr double resistance_at(const LinearResistance& r,
                                             double temperature) noexcept
{
  return r.r_ref * (1.0 + (r.alpha * (temperature - r.t_ref)));
}

/// The IEEE 738 two-point resistance (`r25`, `r75`) as a linear model
/// referenced at 25 °C; `resistance_at` then equals
/// `ieee738::resistance_at` everywhere.
[[nodiscard]] constexpr LinearResistance from_ieee738(
    const ieee738::ConductorParams& conductor) noexcept
{
  return {
      .r_ref = conductor.r25,
      .alpha = (conductor.r75 - conductor.r25) / (50.0 * conductor.r25),
      .t_ref = 25.0,
  };
}

/// Simplified rated-rise thermal resistance
/// `R_Θ = ΔT_rated / (I_rated² · R(t_ref + ΔT_rated))` [°C per loss
/// unit].  Units follow the inputs: per-unit current and resistance give
/// °C per pu loss (divide by S_base for °C/MW); A and Ω/m give °C·m/W.
/// Returns +∞ for a zero-resistance or zero-rated branch (transformer
/// or ideal link: no conductor heating), as Frank's data import does.
[[nodiscard]] double thermal_resistance_from_rated_rise(
    const LinearResistance& r,
    double rated_current,
    double rated_rise = kDefaultRatedRise) noexcept;

/// `T = T_amb + R_Θ · P_loss`: lumped steady-state conductor temperature.
[[nodiscard]] constexpr double temperature_from_loss(double ambient_temperature,
                                                     double thermal_resistance,
                                                     double loss) noexcept
{
  return ambient_temperature + (thermal_resistance * loss);
}

/// Steady-state operating point of one branch at a given current.
struct ThermalState
{
  double temperature {};  ///< conductor temperature T [°C]
  double resistance {};  ///< R(T)
  double loss {};  ///< P_loss = I² · R(T)
};

/// Steady state of the coupled laws `T = T_amb + R_Θ·I²·R(T)` and the
/// linear `R(T)` at current `current` (closed form of the fixed point).
/// `std::nullopt` on thermal runaway (`α·R_Θ·I²·R_ref ≥ 1`) or a
/// non-finite result.  A zero resistance gives `T = T_amb`, zero loss.
[[nodiscard]] std::optional<ThermalState> steady_state(
    const LinearResistance& r,
    double thermal_resistance,
    double ambient_temperature,
    double current) noexcept;

/// Steady state from a known loss instead of a current: `T = T_amb +
/// R_Θ·P_loss`, `R = R(T)` (no fixed point needed).  The current is
/// then `sqrt(P_loss / R)`.
[[nodiscard]] ThermalState steady_state_from_loss(const LinearResistance& r,
                                                  double thermal_resistance,
                                                  double ambient_temperature,
                                                  double loss) noexcept;

/// IEEE Std 738 steady-state conductor temperature [°C] at `current` [A
/// per sub-conductor]: the root of `q_c(T) + q_r(T) − q_s − I²·R(T)` in
/// `[T_a, max_temperature]` (bisection to ~1e-10 °C).
/// `std::nullopt` when even `max_temperature` cannot shed the heat.
[[nodiscard]] std::optional<double> ieee738_conductor_temperature(
    const ieee738::ConductorParams& conductor,
    const ieee738::SpanWeather& weather,
    double current,
    double max_temperature = 400.0) noexcept;

}  // namespace gtopt::line_thermal
