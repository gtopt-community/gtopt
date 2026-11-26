// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial

#include <cmath>
#include <limits>

#include <gtopt/line_thermal_resistance.hpp>

namespace gtopt::line_thermal
{

double thermal_resistance_from_rated_rise(const LinearResistance& r,
                                          double rated_current,
                                          double rated_rise) noexcept
{
  const double rated_loss =
      rated_current * rated_current * resistance_at(r, r.t_ref + rated_rise);
  if (!(rated_loss > 0.0)) {
    return std::numeric_limits<double>::infinity();
  }
  return rated_rise / rated_loss;
}

std::optional<ThermalState> steady_state(const LinearResistance& r,
                                         double thermal_resistance,
                                         double ambient_temperature,
                                         double current) noexcept
{
  const double i2 = current * current;
  if (r.r_ref == 0.0 || i2 == 0.0) {
    return ThermalState {
        .temperature = ambient_temperature,
        .resistance = resistance_at(r, ambient_temperature),
        .loss = 0.0,
    };
  }
  // T − T_amb = R_Θ·I²·R(T_amb) + k·(T − T_amb), k = α·R_Θ·I²·R_ref.
  const double k = r.alpha * thermal_resistance * i2 * r.r_ref;
  if (!(k < 1.0)) {
    return std::nullopt;  // thermal runaway: no steady state
  }
  const double rise = thermal_resistance * i2
      * resistance_at(r, ambient_temperature) / (1.0 - k);
  const double temperature = ambient_temperature + rise;
  const double resistance = resistance_at(r, temperature);
  if (!std::isfinite(temperature) || !std::isfinite(resistance)) {
    return std::nullopt;
  }
  return ThermalState {
      .temperature = temperature,
      .resistance = resistance,
      .loss = i2 * resistance,
  };
}

ThermalState steady_state_from_loss(const LinearResistance& r,
                                    double thermal_resistance,
                                    double ambient_temperature,
                                    double loss) noexcept
{
  const double temperature =
      temperature_from_loss(ambient_temperature, thermal_resistance, loss);
  return {
      .temperature = temperature,
      .resistance = resistance_at(r, temperature),
      .loss = loss,
  };
}

std::optional<double> ieee738_conductor_temperature(
    const ieee738::ConductorParams& conductor,
    const ieee738::SpanWeather& weather,
    double current,
    double max_temperature) noexcept
{
  // Net heat removal: q_c + q_r − q_s − I²·R(T).  Non-positive at the
  // ambient temperature (q_c = q_r = 0 there), increasing with T.
  const auto residual = [&](double t) noexcept
  {
    const auto hb = ieee738::heat_balance(conductor, t, weather);
    return hb.qc + hb.qr - hb.qs - (current * current * hb.resistance);
  };

  double lo = weather.ambient_temperature;
  double hi = max_temperature;
  if (!(hi > lo) || residual(hi) < 0.0) {
    return std::nullopt;
  }
  if (residual(lo) >= 0.0) {
    return lo;
  }
  constexpr int kMaxBisections = 100;
  constexpr double kTolerance = 1e-10;
  for (int it = 0; it < kMaxBisections && (hi - lo) > kTolerance; ++it) {
    const double mid = 0.5 * (lo + hi);
    if (residual(mid) < 0.0) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return 0.5 * (lo + hi);
}

}  // namespace gtopt::line_thermal
