// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial

#include <algorithm>
#include <cmath>
#include <numbers>

#include <gtopt/ieee738.hpp>

namespace gtopt::ieee738
{

namespace
{
[[nodiscard]] double deg2rad(double deg) noexcept
{
  return deg * std::numbers::pi / 180.0;
}
}  // namespace

double wind_angle_factor(double attack_angle_deg) noexcept
{
  const double phi = deg2rad(attack_angle_deg);
  return 1.194 - std::cos(phi) + (0.194 * std::cos(2.0 * phi))
      + (0.368 * std::sin(2.0 * phi));
}

double attack_angle(double wind_direction_deg,
                    double azimuth_deg,
                    double wind_speed,
                    double low_wind_speed,
                    double low_wind_angle) noexcept
{
  if (wind_speed < low_wind_speed) {
    return low_wind_angle;
  }
  // Floor-mod into [0, 180) so negative differences wrap like Python's %.
  double m = std::fmod(wind_direction_deg - azimuth_deg + 90.0, 180.0);
  if (m < 0.0) {
    m += 180.0;
  }
  return std::abs(m - 90.0);
}

AirProperties air_properties(double film_temperature, double elevation) noexcept
{
  const double tf = film_temperature;
  const double he = elevation;
  return AirProperties {
      .viscosity = 1.458e-6 * std::pow(tf + 273.0, 1.5) / (tf + 383.4),
      .density = (1.293 - (1.525e-4 * he) + (6.379e-9 * he * he))
          / (1.0 + (0.00367 * tf)),
      .conductivity = 2.424e-2 + (7.477e-5 * tf) - (4.407e-9 * tf * tf),
  };
}

HeatBalance heat_balance(const ConductorParams& conductor,
                         double conductor_temperature,
                         const SpanWeather& weather) noexcept
{
  const double ts = conductor_temperature;
  const double ta = weather.ambient_temperature;
  const double d = conductor.diameter;
  const double dt = ts - ta;
  const auto air = air_properties((ts + ta) / 2.0, weather.elevation);

  HeatBalance hb;
  const double vw = std::max(weather.wind_speed, 0.0);
  hb.reynolds = d * air.density * vw / air.viscosity;
  hb.k_angle = wind_angle_factor(weather.attack_angle);
  hb.qc1 = hb.k_angle * (1.01 + (1.35 * std::pow(hb.reynolds, 0.52)))
      * air.conductivity * dt;
  hb.qc2 =
      hb.k_angle * 0.754 * std::pow(hb.reynolds, 0.6) * air.conductivity * dt;
  hb.qcn = dt > 0.0
      ? 3.645 * std::sqrt(air.density) * std::pow(d, 0.75) * std::pow(dt, 1.25)
      : 0.0;
  hb.qc = std::max({hb.qc1, hb.qc2, hb.qcn});
  const double tk_s = (ts + 273.0) / 100.0;
  const double tk_a = (ta + 273.0) / 100.0;
  hb.qr = 17.8 * d * conductor.emissivity
      * ((tk_s * tk_s * tk_s * tk_s) - (tk_a * tk_a * tk_a * tk_a));
  hb.qs = conductor.absorptivity * std::max(weather.solar_irradiance, 0.0) * d;
  hb.resistance = resistance_at(ts, conductor.r25, conductor.r75);

  const double net = hb.qc + hb.qr - hb.qs;
  hb.ampacity =
      (net > 0.0 && hb.resistance > 0.0) ? std::sqrt(net / hb.resistance) : 0.0;
  return hb;
}

double ampacity(const ConductorParams& conductor,
                double conductor_temperature,
                const SpanWeather& weather) noexcept
{
  return heat_balance(conductor, conductor_temperature, weather).ampacity;
}

}  // namespace gtopt::ieee738
