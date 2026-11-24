/**
 * @file      gas_physics.cpp
 * @brief     Steady-state natural-gas physics used by the gas-network LP
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <cmath>
#include <numbers>

#include <gtopt/gas_physics.hpp>

namespace gtopt::gas
{

namespace
{
/// Hours → days factor of the Weymouth flow unit (m³/d → km³/h).
constexpr double kM3dPerKm3h = 24000.0;
/// kPa² → bar² (1 bar = 100 kPa).
constexpr double kKpa2PerBar2 = 1.0e4;
/// Reference ratio of `adiabatic_gamma` (the ratio cancels exactly when
/// `alpha` equals the head exponent).
constexpr double kGammaRefRatio = 2.0;
}  // namespace

double weymouth_k(const PipeGeometry& g)
{
  const double c = 3.7435e-3 * g.efficiency
      * (kBaseTemperature / kBasePressureKpa) * std::pow(g.diameter_mm, 2.667);
  const double k_kpa = g.gravity * g.temperature * g.length_km * g.z / (c * c);
  return k_kpa * kM3dPerKm3h * kM3dPerKm3h / kKpa2PerBar2;
}

double elevation_exponent(double dh_m,
                          double gravity,
                          double z,
                          double temperature) noexcept
{
  const double molar_mass = gravity * kAirMolarMass;
  return 2.0 * kGravityAcceleration * molar_mass * dh_m
      / (z * kGasConstant * temperature);
}

double elevation_k_factor(double s) noexcept
{
  // Taylor series below 1e-4: (e^s − 1)/s = 1 + s/2 + s²/6 + O(s³);
  // expm1 keeps full precision elsewhere.
  if (std::abs(s) < 1e-4) {
    return 1.0 + (s / 2.0) + (s * s / 6.0);
  }
  return std::expm1(s) / s;
}

double linepack_coeff(double length_km,
                      double diameter_mm,
                      double z,
                      double temperature)
{
  const double d_m = diameter_mm / 1000.0;
  const double vol_m3 = std::numbers::pi * d_m * d_m / 4.0 * length_km * 1000.0;
  return vol_m3 / (z * kBasePressureBar) * (kBaseTemperature / temperature)
      / 1000.0;
}

std::optional<TangentLayout> parse_tangent_layout(std::string_view s) noexcept
{
  return enum_from_name<TangentLayout>(s);
}

std::vector<double> tangent_points(
    double k_weymouth, double fmax, int nseg, TangentLayout layout, double p_in)
{
  const auto n = static_cast<std::size_t>(std::max(nseg, 1));
  const auto dn = static_cast<double>(n);
  std::vector<double> t;
  t.reserve(n);
  if (layout == TangentLayout::uniform || k_weymouth <= 0.0 || p_in <= 0.0) {
    for (std::size_t k = 1; k <= n; ++k) {
      t.push_back(fmax * ((2.0 * static_cast<double>(k)) - 1.0) / (2.0 * dn));
    }
    return t;
  }
  const double sk = std::sqrt(k_weymouth);
  const double f_eff = std::min(fmax, 0.999 * p_in / sk);
  const double theta_f = std::asin(sk * f_eff / p_in);
  const auto breakpoint = [&](std::size_t k)
  { return p_in / sk * std::sin(theta_f * static_cast<double>(k) / dn); };
  for (std::size_t k = 1; k <= n; ++k) {
    t.push_back(0.5 * (breakpoint(k - 1) + breakpoint(k)));
  }
  return t;
}

double compressor_fuel_fraction(double ratio,
                                double efficiency,
                                double driver_efficiency)
{
  constexpr double gravity = 0.6;
  constexpr double z = 0.9;
  const double molar_mass = kWeymouthAirMolarMass * gravity;
  const double head = z * kGasConstant * kBaseTemperature / molar_mass / kAlpha
      * (std::pow(ratio, kAlpha) - 1.0) / efficiency;
  return head / (driver_efficiency * kLhvPerKg);
}

double adiabatic_gamma(double efficiency,
                       double driver_efficiency,
                       double alpha)
{
  return compressor_fuel_fraction(kGammaRefRatio, efficiency, driver_efficiency)
      / (std::pow(kGammaRefRatio, alpha) - 1.0);
}

double gas_lhv_mw_per_km3h(double gravity)
{
  const double rho_std = kBasePressureKpa * 1.0e3
      / (kGasConstant * kBaseTemperature) * kWeymouthAirMolarMass * gravity;
  return kLhvPerKg * rho_std * 1000.0 / 3600.0 / 1.0e6;
}

double electric_kappa(double driver_efficiency, double motor_efficiency)
{
  return gas_lhv_mw_per_km3h() * driver_efficiency / motor_efficiency;
}

SaldarriagaPlane saldarriaga_plane(
    double gamma, double alpha, double psi0, double beta0, double p_in0)
{
  const double a = alpha / 2.0;
  const double r0 = beta0 * beta0;
  const double pi_in0 = p_in0 * p_in0;
  return SaldarriagaPlane {
      .c_psi = gamma * (std::pow(r0, a) - 1.0),
      .c_pi = gamma * a * psi0 * std::pow(r0, a - 1.0) / pi_in0,
      .r0 = r0,
  };
}

CapabilityRows plant_capability_rows(
    double pmax, double p_min, double band, double p_nom, double out_min)
{
  CapabilityRows out;
  const bool derate = p_nom > p_min;
  if (!derate) {
    out_min = 1.0;
  } else if (out_min <= 0.0) {
    out_min = p_min / p_nom;
  }
  const double lo2 = (p_min - band) * (p_min - band);
  const double m2 = p_min * p_min;
  const double s1 = out_min * pmax / (m2 - lo2);
  out.rows.push_back({
      .slope = s1,
      .intercept = -s1 * lo2,
  });
  if (derate && out_min < 1.0) {
    const double n2 = p_nom * p_nom;
    const double s2 = (1.0 - out_min) * pmax / (n2 - m2);
    if (s2 >= s1) {
      out.derate_dropped = true;
    } else {
      out.rows.push_back({
          .slope = s2,
          .intercept = (out_min * pmax) - (s2 * m2),
      });
    }
  }
  return out;
}

}  // namespace gtopt::gas
