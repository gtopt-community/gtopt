/**
 * @file      ieee738.hpp
 * @brief     IEEE Std 738 steady-state conductor heat balance (SI units)
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Pure, allocation-free functions computing the steady-state ampacity of
 * a bare overhead conductor from its thermal parameters and the local
 * weather, following IEEE Std 738-2012 ("Standard for Calculating the
 * Current-Temperature Relationship of Bare Overhead Conductors"), SI
 * form.  Used by the Line dynamic line rating (DLR, `Line.dlr`) to turn
 * a per-block weather forecast into a per-block thermal rating.
 *
 * @see IEEE Std 738-2012 (published 2013), the edition implemented here.
 * @see M. Matus et al., "Identification of Critical Spans for Monitoring
 *      Systems in Dynamic Thermal Rating", IEEE Trans. Power Delivery
 *      27(2):1002–1009, 2012, https://doi.org/10.1109/TPWRD.2012.2185254
 *      (open access:
 * https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)
 *
 * Heat balance at the maximum allowed conductor temperature `T_s`:
 *
 *     q_c + q_r = q_s + I² · R(T_s)        [W/m]
 *
 * - forced convection (eqs. 3a/3b), wind-angle factor `K_angle`
 *   (eq. 4a): `q_c1 = K_angle·(1.01 + 1.35·Re^0.52)·k_f·(T_s − T_a)`,
 *   `q_c2 = K_angle·0.754·Re^0.6·k_f·(T_s − T_a)`;
 * - natural convection (eq. 5c): `q_cn = 3.645·ρ_f^0.5·D^0.75·(T_s −
 *   T_a)^1.25`;
 * - convective loss `q_c = max(q_c1, q_c2, q_cn)`;
 * - radiation (eq. 7c): `q_r = 17.8·D·ε·[((T_s+273)/100)^4 −
 *   ((T_a+273)/100)^4]`;
 * - solar gain `q_s = α·S·D`, with `S` [W/m²] the irradiance ON THE
 *   CONDUCTOR (already projected: `Q_se·sin θ` in IEEE 738 eq. 8).  There
 *   is no sun-geometry model here: the forecast provider (or the Python
 *   tooling) supplies the effective irradiance;
 * - `R(T)` linear between the 25 °C and 75 °C values, extrapolated
 *   beyond (IEEE 738 eq. 10);
 * - air viscosity, density and conductivity at the film temperature
 *   `T_film = (T_s + T_a)/2` and the elevation `H_e` (eqs. 13a, 14a, 15a).
 *
 * `I = sqrt((q_c + q_r − q_s) / R(T_s))`, clamped to 0 when the net heat
 * removal is negative (hot, still, sunny air above the conductor limit).
 *
 * Validated against the IEEE 738-2012 Annex (Drake 26/7 ACSR, 100 °C,
 * T_a 40 °C, 0.61 m/s perpendicular wind, full sun): I ≈ 1025 A.
 */

#pragma once

#include <numbers>

namespace gtopt::ieee738
{

/// Wind speeds below this value [m/s] use the low-wind attack angle.
inline constexpr double kLowWindSpeed = 0.5;
/// Attack angle [deg] assumed for low wind (direction unreliable).
inline constexpr double kLowWindAttackAngle = 45.0;

/// Conductor thermal parameters (SI).
struct ConductorParams
{
  double diameter {};  ///< Outside diameter D [m]
  double r25 {};  ///< AC resistance at 25 °C [Ω/m]
  double r75 {};  ///< AC resistance at 75 °C [Ω/m]
  double emissivity {0.8};  ///< ε [0, 1]
  double absorptivity {0.8};  ///< α [0, 1]
};

/// Weather at the span (SI).
struct SpanWeather
{
  double ambient_temperature {};  ///< T_a [°C]
  double wind_speed {};  ///< V_w [m/s]
  double attack_angle {90.0};  ///< φ [deg], 0 = parallel, 90 = perpendicular
  double solar_irradiance {};  ///< S on the conductor [W/m²]
  double elevation {};  ///< H_e above sea level [m]
};

/// Air properties at the film temperature.
struct AirProperties
{
  double viscosity {};  ///< μ_f [kg/(m·s)]
  double density {};  ///< ρ_f [kg/m³]
  double conductivity {};  ///< k_f [W/(m·°C)]
};

/// All heat-balance terms at one conductor temperature [W/m], plus the
/// resistance [Ω/m] and the resulting ampacity [A].
struct HeatBalance
{
  double qc1 {};  ///< forced convection, low-Reynolds form
  double qc2 {};  ///< forced convection, high-Reynolds form
  double qcn {};  ///< natural convection
  double qc {};  ///< convective loss = max(qc1, qc2, qcn)
  double qr {};  ///< radiated heat loss
  double qs {};  ///< solar heat gain
  double reynolds {};  ///< Reynolds number
  double k_angle {};  ///< wind-angle factor
  double resistance {};  ///< R(T_s)
  double ampacity {};  ///< steady-state current [A]
};

/// Wind-direction factor `K_angle = 1.194 − cos φ + 0.194·cos 2φ +
/// 0.368·sin 2φ` (IEEE 738 eq. 4a); `K(90°) = 1`, `K(0°) = 0.388`.
[[nodiscard]] double wind_angle_factor(double attack_angle_deg) noexcept;

/// Angle [deg, 0..90] between the wind and the conductor axis.
///
/// `φ = |((wind_direction − azimuth + 90) mod 180) − 90|` — direction-
/// agnostic (a wind from 270° hits a 0° span like one from 90°, and a
/// 180° span is the same as a 0° span).  Below `kLowWindSpeed` the
/// direction is meaningless and `kLowWindAttackAngle` (45°) is used
/// (both configurable; `low_wind_speed = 0` disables the rule).
[[nodiscard]] double attack_angle(
    double wind_direction_deg,
    double azimuth_deg,
    double wind_speed,
    double low_wind_speed = kLowWindSpeed,
    double low_wind_angle = kLowWindAttackAngle) noexcept;

/// `R(T) = r25 + (r75 − r25)/50 · (T − 25)`: linear between the two
/// tabulated points, extrapolated beyond them.
[[nodiscard]] constexpr double resistance_at(double temperature,
                                             double r25,
                                             double r75) noexcept
{
  return r25 + ((r75 - r25) / 50.0 * (temperature - 25.0));
}

/// `(r25, r75)` of the linear model `R(T) = ac_factor · r20 · (1 +
/// alpha_r·(T − 20))` — the DC-at-20 °C form of conductor data sheets
/// (e.g. AAAC Flint: r20 = 8.89e-5 Ω/m, alpha_r = 0.0036 /K).
struct ResistancePoints
{
  double r25 {};
  double r75 {};
};

[[nodiscard]] constexpr ResistancePoints resistance_from_r20(
    double r20, double alpha_r, double ac_factor) noexcept
{
  return {
      .r25 = ac_factor * r20 * (1.0 + (alpha_r * 5.0)),
      .r75 = ac_factor * r20 * (1.0 + (alpha_r * 55.0)),
  };
}

/// Air viscosity, density and thermal conductivity at the film
/// temperature [°C] and elevation [m] (IEEE 738 eqs. 13a, 14a, 15a).
[[nodiscard]] AirProperties air_properties(double film_temperature,
                                           double elevation) noexcept;

/// Full heat balance at conductor temperature `conductor_temperature`.
[[nodiscard]] HeatBalance heat_balance(const ConductorParams& conductor,
                                       double conductor_temperature,
                                       const SpanWeather& weather) noexcept;

/// Steady-state ampacity [A] at `conductor_temperature` (0 when the net
/// heat removal or the resistance is not positive).
[[nodiscard]] double ampacity(const ConductorParams& conductor,
                              double conductor_temperature,
                              const SpanWeather& weather) noexcept;

/// Three-phase rating [MW] of `amps` per sub-conductor:
/// `η · √3 · V[kV] · v_pu · pf · I · n_sub / 1000`.
[[nodiscard]] constexpr double ampacity_to_mw(double amps,
                                              double voltage_kv,
                                              double v_pu,
                                              double power_factor,
                                              int subconductors,
                                              double safety_factor) noexcept
{
  return safety_factor * std::numbers::sqrt3 * voltage_kv * v_pu * power_factor
      * amps * static_cast<double>(subconductors) / 1000.0;
}

}  // namespace gtopt::ieee738
