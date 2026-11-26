// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_ieee738.cpp — IEEE Std 738 steady-state heat balance (ieee738.hpp).
//
// Reference vectors generated with the independent Python reference
// implementation (validated against the IEEE 738-2012 Annex Drake
// example, 1025 A).  Drake 26/7 ACSR: D = 28.14 mm, R25 = 7.283e-5 Ω/m,
// R75 = 8.688e-5 Ω/m, ε = α = 0.8.  The full-sun irradiance on the
// conductor (Q_se·sin θ for lat 30°N, day 161, 11:00, E–W line) is
// 997.7005 W/m², which reproduces q_s = 22.4602 W/m.

#include <array>
#include <cmath>

#include <doctest/doctest.h>
#include <gtopt/ieee738.hpp>
#include <gtopt/line_dlr_lp.hpp>

using namespace gtopt;
using namespace gtopt::ieee738;

namespace
{

constexpr double kSun = 997.7005431224591;  // W/m² on the conductor

constexpr ConductorParams kDrake {
    .diameter = 0.02814,
    .r25 = 7.283e-5,
    .r75 = 8.688e-5,
    .emissivity = 0.8,
    .absorptivity = 0.8,
};

[[nodiscard]] SpanWeather wx(
    double ta, double wind, double phi, double sun, double elevation = 0.0)
{
  return {
      .ambient_temperature = ta,
      .wind_speed = wind,
      .attack_angle = phi,
      .solar_irradiance = sun,
      .elevation = elevation,
  };
}

}  // namespace

TEST_CASE("IEEE 738 Drake example: 100 C, Ta 40, 0.61 m/s perp, sun")  // NOLINT
{
  const auto hb = heat_balance(kDrake, 100.0, wx(40.0, 0.61, 90.0, kSun));
  CHECK(hb.qc == doctest::Approx(82.08309633569729).epsilon(1e-9));
  CHECK(hb.qr == doctest::Approx(39.10545461346049).epsilon(1e-9));
  CHECK(hb.qs == doctest::Approx(22.4602346267728).epsilon(1e-9));
  CHECK(hb.qs == doctest::Approx(22.45).epsilon(1e-3));
  CHECK(hb.qc == doctest::Approx(82.0).epsilon(0.005));
  CHECK(hb.qr == doctest::Approx(39.1).epsilon(0.005));
  CHECK(hb.ampacity == doctest::Approx(1025.3603200725527).epsilon(1e-9));
  // IEEE 738-2012 Annex B value: 1025 A within ±0.5 %.
  CHECK(hb.ampacity == doctest::Approx(1025.0).epsilon(0.005));
  CHECK(hb.k_angle == doctest::Approx(1.0));
  CHECK(hb.resistance == doctest::Approx(9.3905e-05).epsilon(1e-4));
}

TEST_CASE("IEEE 738 reference cases at 75 C")  // NOLINT
{
  struct Vec
  {
    const char* name;
    double tmax;
    double ta;
    double wind;
    double phi;
    double sun;
    double amps;
    double rounded;
  };
  const auto cases = std::to_array<Vec>({
      {
          .name = "sun",
          .tmax = 75,
          .ta = 40,
          .wind = 0.61,
          .phi = 90,
          .sun = kSun,
          .amps = 725.9026694982364,
          .rounded = 726,
      },
      {
          .name = "night",
          .tmax = 75,
          .ta = 40,
          .wind = 0.61,
          .phi = 90,
          .sun = 0.0,
          .amps = 886.2589246743955,
          .rounded = 886,
      },
      {
          .name = "Ta 25 sun",
          .tmax = 75,
          .ta = 25,
          .wind = 0.61,
          .phi = 90,
          .sun = kSun,
          .amps = 918.1158018458999,
          .rounded = 918,
      },
      {
          .name = "3 m/s Ta 10 night",
          .tmax = 75,
          .ta = 10,
          .wind = 3.0,
          .phi = 90,
          .sun = 0.0,
          .amps = 1708.5050459344645,
          .rounded = 1709,
      },
      {
          .name = "wind at 45",
          .tmax = 75,
          .ta = 40,
          .wind = 0.61,
          .phi = 45,
          .sun = kSun,
          .amps = 668.4909924531029,
          .rounded = 668,
      },
      {
          .name = "wind parallel",
          .tmax = 75,
          .ta = 40,
          .wind = 0.61,
          .phi = 0,
          .sun = kSun,
          .amps = 478.33025648302925,
          .rounded = 478,
      },
      {
          .name = "still air",
          .tmax = 75,
          .ta = 35,
          .wind = 0.0,
          .phi = 90,
          .sun = kSun,
          .amps = 551.014310875632,
          .rounded = 551,
      },
      {
          .name = "100C 3 m/s night",
          .tmax = 100,
          .ta = 10,
          .wind = 3.0,
          .phi = 90,
          .sun = 0.0,
          .amps = 1944.056095058017,
          .rounded = 1944,
      },
  });
  for (const auto& c : cases) {
    CAPTURE(c.name);
    const double i = ampacity(kDrake, c.tmax, wx(c.ta, c.wind, c.phi, c.sun));
    CHECK(i == doctest::Approx(c.amps).epsilon(1e-9));
    CHECK(std::round(i) == doctest::Approx(c.rounded));
  }
}

TEST_CASE("IEEE 738 heat terms of the reference cases")  // NOLINT
{
  const auto night3 = heat_balance(kDrake, 75.0, wx(10.0, 3.0, 90.0, 0.0));
  CHECK(night3.qc == doctest::Approx(220.5351977046621).epsilon(1e-9));
  CHECK(night3.qr == doctest::Approx(33.066609358866714).epsilon(1e-9));
  CHECK(night3.qs == doctest::Approx(0.0));
  // High Reynolds: the 0.754·Re^0.6 branch wins.
  CHECK(night3.qc2 > night3.qc1);

  const auto still = heat_balance(kDrake, 75.0, wx(35.0, 0.0, 90.0, kSun));
  // Still air: natural convection dominates.
  CHECK(still.qc == doctest::Approx(still.qcn));
  CHECK(still.qcn == doctest::Approx(26.130018678702545).epsilon(1e-9));
  CHECK(still.qr == doctest::Approx(22.708440994283514).epsilon(1e-9));

  // Elevation lowers the air density → less convection → lower rating.
  const double sea = ampacity(kDrake, 75.0, wx(40.0, 0.61, 90.0, kSun));
  const double high = ampacity(kDrake, 75.0, wx(40.0, 0.61, 90.0, kSun, 1000));
  CHECK(high == doctest::Approx(703.0534079875746).epsilon(1e-9));
  CHECK(high < sea);
}

TEST_CASE("IEEE 738 wind-angle factor table")  // NOLINT
{
  CHECK(wind_angle_factor(0.0) == doctest::Approx(0.388));
  CHECK(wind_angle_factor(15.0) == doctest::Approx(0.5800831020451127));
  CHECK(wind_angle_factor(30.0) == doctest::Approx(0.7436719448082347));
  CHECK(wind_angle_factor(45.0) == doctest::Approx(0.8548932188134524));
  CHECK(wind_angle_factor(60.0) == doctest::Approx(0.9156973485926734));
  CHECK(wind_angle_factor(75.0) == doctest::Approx(0.951172026563298));
  CHECK(wind_angle_factor(90.0) == doctest::Approx(1.0));
  // Monotone on [0, 90].
  for (int phi = 0; phi < 90; phi += 5) {
    CHECK(wind_angle_factor(phi) < wind_angle_factor(phi + 5.0));
  }
}

TEST_CASE("IEEE 738 attack angle symmetry and low-wind rule")  // NOLINT
{
  constexpr double v = 2.0;
  // Perpendicular / parallel.
  CHECK(attack_angle(90.0, 0.0, v) == doctest::Approx(90.0));
  CHECK(attack_angle(0.0, 0.0, v) == doctest::Approx(0.0));
  // A wind from 270° hits a 0° span like one from 90°.
  CHECK(attack_angle(270.0, 0.0, v)
        == doctest::Approx(attack_angle(90.0, 0.0, v)));
  // A 180° span is the same line as a 0° span.
  for (int i = 0; i < 22; ++i) {
    const double wd = 17.0 * i;
    CAPTURE(wd);
    CHECK(attack_angle(wd, 180.0, v)
          == doctest::Approx(attack_angle(wd, 0, v)));
    CHECK(attack_angle(wd, 35.0, v)
          == doctest::Approx(attack_angle(wd + 180.0, 35.0, v)));
    const double phi = attack_angle(wd, 35.0, v);
    CHECK(phi >= 0.0);
    CHECK(phi <= 90.0);
  }
  // Negative differences wrap (Python-style floor mod).
  CHECK(attack_angle(10.0, 80.0, v) == doctest::Approx(70.0));
  CHECK(attack_angle(-30.0, 0.0, v) == doctest::Approx(30.0));
  // Three spans at 90/45/0 with a 90° wind: 0, 45, 90.
  CHECK(attack_angle(90.0, 90.0, v) == doctest::Approx(0.0));
  CHECK(attack_angle(90.0, 45.0, v) == doctest::Approx(45.0));
  // Low wind: direction ignored, φ = 45°.
  CHECK(attack_angle(90.0, 0.0, 0.3) == doctest::Approx(kLowWindAttackAngle));
  CHECK(attack_angle(0.0, 0.0, 0.0) == doctest::Approx(45.0));
  CHECK(attack_angle(0.0, 0.0, kLowWindSpeed) == doctest::Approx(0.0));
}

TEST_CASE("IEEE 738 resistance interpolation and extrapolation")  // NOLINT
{
  CHECK(resistance_at(25.0, 7.283e-5, 8.688e-5) == doctest::Approx(7.283e-5));
  CHECK(resistance_at(75.0, 7.283e-5, 8.688e-5) == doctest::Approx(8.688e-5));
  CHECK(resistance_at(50.0, 7.283e-5, 8.688e-5)
        == doctest::Approx((7.283e-5 + 8.688e-5) / 2.0));
  // Linear extrapolation beyond 75 °C (IEEE 738 eq. 10).
  CHECK(resistance_at(100.0, 7.283e-5, 8.688e-5)
        == doctest::Approx(8.688e-5 + ((8.688e-5 - 7.283e-5) / 2.0)));
  CHECK(resistance_at(0.0, 7.283e-5, 8.688e-5)
        == doctest::Approx(7.283e-5 - ((8.688e-5 - 7.283e-5) / 2.0)));
  static_assert(resistance_at(25.0, 1.0, 2.0) == 1.0);
}

TEST_CASE("IEEE 738 ampacity monotonicity")  // NOLINT
{
  const double base = ampacity(kDrake, 75.0, wx(30.0, 1.0, 60.0, 500.0));
  // Hotter air, more sun → lower; more wind, hotter limit → higher.
  CHECK(ampacity(kDrake, 75.0, wx(35.0, 1.0, 60.0, 500.0)) < base);
  CHECK(ampacity(kDrake, 75.0, wx(30.0, 1.0, 60.0, 900.0)) < base);
  CHECK(ampacity(kDrake, 75.0, wx(30.0, 2.0, 60.0, 500.0)) > base);
  CHECK(ampacity(kDrake, 90.0, wx(30.0, 1.0, 60.0, 500.0)) > base);
  // More perpendicular wind → higher.
  CHECK(ampacity(kDrake, 75.0, wx(30.0, 1.0, 90.0, 500.0)) > base);
  double prev = 1e9;
  for (int k = 0; k <= 14; ++k) {
    const double ta = -10.0 + (5.0 * k);
    const double i = ampacity(kDrake, 75.0, wx(ta, 0.61, 90.0, kSun));
    CHECK(i < prev);
    prev = i;
  }
  prev = 0.0;
  for (int k = 0; k <= 20; ++k) {
    const double w = 0.5 * k;
    const double i = ampacity(kDrake, 75.0, wx(25.0, w, 90.0, kSun));
    CHECK(i >= prev);
    prev = i;
  }
}

TEST_CASE("IEEE 738 negative net heat clamps to zero")  // NOLINT
{
  // Ambient above the conductor limit, still air, strong sun.
  const auto hb = heat_balance(kDrake, 50.0, wx(60.0, 0.0, 90.0, 1200.0));
  CHECK(hb.qc + hb.qr - hb.qs < 0.0);
  CHECK(hb.ampacity == doctest::Approx(0.0));
  // Non-positive resistance (bad extrapolation) also yields 0.
  const ConductorParams weird {
      .diameter = 0.02,
      .r25 = 1e-5,
      .r75 = -1e-5,
  };
  CHECK(ampacity(weird, 75.0, wx(20.0, 1.0, 90.0, 0.0)) == doctest::Approx(0));
}

TEST_CASE("IEEE 738 ampere to MW conversion")  // NOLINT
{
  // √3 · 230 kV · 1025 A = 408.3 MW.
  CHECK(ampacity_to_mw(1025.0, 230.0, 1.0, 1.0, 1, 1.0)
        == doctest::Approx(408.3313).epsilon(1e-5));
  const double base = ampacity_to_mw(1000.0, 220.0, 1.0, 1.0, 1, 1.0);
  CHECK(ampacity_to_mw(1000.0, 220.0, 1.0, 1.0, 2, 1.0)
        == doctest::Approx(2.0 * base));
  CHECK(ampacity_to_mw(1000.0, 220.0, 0.95, 0.9, 1, 0.8)
        == doctest::Approx(base * 0.95 * 0.9 * 0.8));
  static_assert(ampacity_to_mw(0.0, 220.0, 1.0, 1.0, 1, 1.0) == 0.0);

  const double mw = dlr_span_rating_mw(
      kDrake, 100.0, wx(40.0, 0.61, 90.0, kSun), 230.0, 1.0, 1.0, 1, 1.0);
  CHECK(mw
        == doctest::Approx(
            ampacity_to_mw(1025.3603200725527, 230.0, 1.0, 1.0, 1, 1.0)));
}

TEST_CASE("IEEE 738 CEN AAAC Flint vector (shared with Python)")  // NOLINT
{
  // Los Vilos–Las Palmas 220 kV, CEN data: D 25.17 mm, R20 0.0889 Ω/km,
  // α_R 0.0036 /K, ac factor 1.02, ε = α = 0.5, Tmax 55 °C, Ta 25 °C,
  // GHI 1000 W/m² used as absorbed flux, 0.61 m/s perpendicular.
  // CEN's static table: 587 A (IEEE 738 reproduces it within ~1 %).
  const auto rp = resistance_from_r20(8.89e-5, 0.0036, 1.02);
  CHECK(rp.r25 == doctest::Approx(9.2310204e-05));
  CHECK(rp.r75 == doctest::Approx(0.000108632244));
  const ConductorParams flint {
      .diameter = 0.02517,
      .r25 = rp.r25,
      .r75 = rp.r75,
      .emissivity = 0.5,
      .absorptivity = 0.5,
  };
  const auto hb = heat_balance(flint, 55.0, wx(25.0, 0.61, 90.0, 1000.0));
  CHECK(hb.qs == doctest::Approx(0.5 * 1000.0 * 0.02517));
  CHECK(hb.qc == doctest::Approx(38.89316559924475).epsilon(1e-9));
  CHECK(hb.qr == doctest::Approx(8.261972735263194).epsilon(1e-9));
  CHECK(hb.ampacity == doctest::Approx(581.8759361999057).epsilon(1e-9));
  CHECK(hb.ampacity == doctest::Approx(587.0).epsilon(0.015));
  // r20 form == linear R(T) = ac·r20·(1 + α_R (T − 20)).
  CHECK(hb.resistance
        == doctest::Approx(1.02 * 8.89e-5 * (1.0 + (0.0036 * 35.0))));
}

TEST_CASE("IEEE 738 configurable low-wind rule")  // NOLINT
{
  // Defaults: 0.5 m/s, 45°.
  CHECK(attack_angle(0.0, 0.0, 0.3) == doctest::Approx(45.0));
  // Disabled: the direction is used at any speed.
  CHECK(attack_angle(0.0, 0.0, 0.3, 0.0, 45.0) == doctest::Approx(0.0));
  CHECK(attack_angle(90.0, 0.0, 0.0, 0.0, 45.0) == doctest::Approx(90.0));
  // Custom threshold and angle.
  CHECK(attack_angle(0.0, 0.0, 0.8, 1.0, 30.0) == doctest::Approx(30.0));
  CHECK(attack_angle(0.0, 0.0, 1.2, 1.0, 30.0) == doctest::Approx(0.0));
}

// ── Literature benchmarks ────────────────────────────────────────────

namespace
{

/// Conductor temperature [°C] at which the steady-state ampacity equals
/// `current` (the inverse of `ampacity(T)`, monotone increasing in T
/// above ambient), by bisection on [T_a, 400 °C].
[[nodiscard]] double temperature_at(const ConductorParams& conductor,
                                    double current,
                                    const SpanWeather& weather)
{
  double lo = weather.ambient_temperature + 1e-6;
  double hi = 400.0;
  for (int it = 0; it < 200; ++it) {
    const double mid = 0.5 * (lo + hi);
    if (ampacity(conductor, mid, weather) < current) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return 0.5 * (lo + hi);
}

}  // namespace

TEST_CASE("IEEE 738-2012 Annex B numerical examples 1 and 2")  // NOLINT
{
  // IEEE Std 738-2012, Annex B (DOI 10.1109/IEEESTD.2013.6692858):
  // Drake 26/7 ACSR, D = 28.14 mm, R25 = 7.283e-5 Ω/m,
  // R75 = 8.688e-5 Ω/m, α = ε = 0.5, T_a = 40 °C, 0.61 m/s wind
  // perpendicular to the conductor, sea level.  The worked examples give
  // the conductor temperature for a given current:
  //   I = 1000 A → T_s = 100.7 °C,  I = 1003 A → T_s = 101.1 °C,
  // with q_s = 13.74 W/m and q_c(100.7 °C) = 83.06 W/m.
  // Cross-checked in two open implementations:
  //   * pandapower, pandapower/test/loadflow/test_tdpf.py
  //     (https://github.com/e2nIEE/pandapower), test_IEEE_example_2
  //     (`max_cond_temp = 100.7`, line 482) and test_IEEE_example_3
  //     (`max_cond_temp = 101.1`, line 539);
  //   * linerate, tests/acceptance_tests/test_ieee_ampacity_cases.py
  //     (https://github.com/statnett/linerate), lines 59-64:
  //     q_s = 13.738 W/m, q_c(100.7 °C) = 83.061 W/m.
  //
  // gtopt has no sun-geometry model: `q_s = α·S·D` with S the irradiance
  // already projected on the conductor (`ieee738.hpp`).  The Annex
  // computes q_s from the solar position (lat 30° N, 11:00, June 10,
  // E–W line); S is chosen here so that α·S·D = 13.738 W/m.
  //
  // Tolerance ±0.5 °C: the Annex prints temperatures to 0.1 °C and
  // derives them from rounded intermediate values (q_s, q_c, q_r to
  // 0.01 W/m, R(T) from rounded tables); 0.5 °C is the 0.5 W/m q_c
  // tolerance of linerate expressed in temperature (∂q_c/∂T ≈
  // 1.4 W/(m·°C)).  gtopt lands within 0.1 °C of both values.
  constexpr double kQs = 13.738;  // W/m, linerate line 60
  const ConductorParams drake {
      .diameter = 0.02814,
      .r25 = 7.283e-5,
      .r75 = 8.688e-5,
      .emissivity = 0.5,
      .absorptivity = 0.5,
  };
  const double sun = kQs / (drake.absorptivity * drake.diameter);
  const auto weather = wx(40.0, 0.61, 90.0, sun);

  SUBCASE("heat terms")
  {
    const auto hb = heat_balance(drake, 100.7, weather);
    CHECK(hb.qs == doctest::Approx(kQs).epsilon(1e-12));
    CHECK(hb.qc == doctest::Approx(83.061).epsilon(0.5 / 83.061));
    // Low-Reynolds forced convection (eq. 3a) governs at 0.61 m/s.
    CHECK(hb.qc == doctest::Approx(hb.qc1));
    const auto hb3 = heat_balance(drake, 101.1, weather);
    // linerate example C, line 89: q_c(101.1 °C) = 83.600 W/m.
    CHECK(hb3.qc == doctest::Approx(83.600).epsilon(0.5 / 83.6));
  }

  SUBCASE("example 1: 1000 A -> 100.7 C")
  {
    const double t = temperature_at(drake, 1000.0, weather);
    CAPTURE(t);
    CHECK(std::abs(t - 100.7) <= 0.5);
    // The inverse is exact: the ampacity at T(I) is I.
    CHECK(ampacity(drake, t, weather) == doctest::Approx(1000.0));
  }

  SUBCASE("example 2: 1003 A -> 101.1 C")
  {
    const double t = temperature_at(drake, 1003.0, weather);
    CAPTURE(t);
    CHECK(std::abs(t - 101.1) <= 0.5);
    // Monotone: more current, hotter conductor.
    CHECK(t > temperature_at(drake, 1000.0, weather));
  }
}

TEST_CASE("NREL OEDI 6231 static line ratings per voltage class")  // NOLINT
{
  // NREL, "Dynamic Line Ratings for the U.S. Transmission Network"
  // (NREL/TP-6A40-91599, DOI 10.2172/2476805); data: OEDI submission
  // 6231, https://data.openei.org/submissions/6231, files
  // SLR_A-75C.h5 / SLR_A-100C.h5 (dataset `data`: one static rating per
  // HIFLD line).  Code: https://github.com/NREL/DynamicLineRatings.
  //
  // Static-rating assumptions (dlr/physics.py:332-343, `ampacity`
  // defaults): T_a = 40 °C, 0.61 m/s perpendicular wind, 1000 W/m² GHI
  // used as the absorbed flux (q_s = α·S·D, as gtopt), α = ε = 0.8.
  // Conductor per voltage class from data/kv_to_conductor_props.csv
  // (lines 2-7; 345, 500 and 765 kV share one conductor, so there are
  // four distinct ratings).  Each class's rating was reproduced from the
  // CSV and matched to the four distinct values of the h5 files (line
  // counts 29985 / 44025 / 7214 / 3412 for 69 / 115 / 230 / 345+ kV):
  //
  //   class    D [m]   R25 [Ω/m]  R75 [Ω/m]   75 °C [A]   100 °C [A]
  //   69 kV    0.0183  1.70e-4    2.03e-4      427.5        615.0
  //   115 kV   0.0277  7.30e-5    8.70e-5      722.3       1060.2
  //   230 kV   0.0362  4.40e-5    5.20e-5      997.0       1485.2
  //   345+ kV  0.0304  6.10e-5    7.30e-5      806.6       1189.7
  //
  // The published 100 °C ratings use R(75 °C), not R(100 °C):
  // dlr/helpers.py:592 calls `lookup_diameter_resistance(dflines)`
  // with its default 75 °C conductor temperature, which picks AC_R_75C
  // (helpers.py:351-354), and the 100 °C SLR then only changes the heat
  // balance.  The 100 °C reference is therefore reproduced with a flat
  // R = R75; gtopt's own 100 °C rating (R extrapolated linearly to
  // 100 °C, IEEE 738 eq. 10, = the CSV's AC_R_100C column) is lower by
  // exactly sqrt(R75 / R100).
  //
  // Tolerance 0.1 %: the references are rounded to 0.1 A (≤ 0.012 % at
  // 427.5 A) and NREL's air density is the ideal-gas law at 101325 Pa,
  // 50 % humidity (physics.py:213) instead of IEEE 738 eq. 14a, and its
  // radiation uses π·σ = 17.81 with 273.15 K instead of 17.8 with 273:
  // the model differences move the ratings by at most 0.03 %.
  struct Class
  {
    const char* name;
    double diameter;
    double r25;
    double r75;
    double rating75;
    double rating100;
  };
  const auto classes = std::to_array<Class>({
      {
          .name = "69 kV",
          .diameter = 0.0183,
          .r25 = 0.00017,
          .r75 = 0.000203,
          .rating75 = 427.5,
          .rating100 = 615.0,
      },
      {
          .name = "115 kV",
          .diameter = 0.0277,
          .r25 = 7.3e-05,
          .r75 = 8.7e-05,
          .rating75 = 722.3,
          .rating100 = 1060.2,
      },
      {
          .name = "230 kV",
          .diameter = 0.0362,
          .r25 = 4.4e-05,
          .r75 = 5.2e-05,
          .rating75 = 997.0,
          .rating100 = 1485.2,
      },
      {
          .name = "345/500/765 kV",
          .diameter = 0.0304,
          .r25 = 6.1e-05,
          .r75 = 7.3e-05,
          .rating75 = 806.6,
          .rating100 = 1189.7,
      },
  });
  constexpr double kTol = 1e-3;
  const auto weather = wx(40.0, 0.61, 90.0, 1000.0);
  for (const auto& c : classes) {
    CAPTURE(c.name);
    const ConductorParams cond {
        .diameter = c.diameter,
        .r25 = c.r25,
        .r75 = c.r75,
        .emissivity = 0.8,
        .absorptivity = 0.8,
    };
    const double i75 = ampacity(cond, 75.0, weather);
    CHECK(i75 == doctest::Approx(c.rating75).epsilon(kTol));

    // 100 °C as published: resistance frozen at R75.
    const ConductorParams flat {
        .diameter = c.diameter,
        .r25 = c.r75,
        .r75 = c.r75,
        .emissivity = 0.8,
        .absorptivity = 0.8,
    };
    CHECK(ampacity(flat, 100.0, weather)
          == doctest::Approx(c.rating100).epsilon(kTol));

    // gtopt's own 100 °C rating (R extrapolated to 100 °C).
    const double r100 = resistance_at(100.0, c.r25, c.r75);
    CHECK(ampacity(cond, 100.0, weather)
          == doctest::Approx(c.rating100 * std::sqrt(c.r75 / r100))
                 .epsilon(kTol));
  }
}
