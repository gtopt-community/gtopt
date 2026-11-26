// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_thermal_resistance.cpp — electrothermal (TDPF) formula layer
// (line_thermal_resistance.hpp), the reference test of the
// temperature-dependent loss model.
//
// Benchmark: S. Frank, J. Sexauer, S. Mohagheghi, "Temperature-Dependent
// Power Flow", IEEE Trans. Power Syst. 28(4):4007–4018, 2013,
// https://doi.org/10.1109/TPWRS.2013.2266409, IEEE 30-bus case
// (MATPOWER `case30`, public data), "simplified" rated-rise thermal model:
//   T_amb = T_ref = 25 °C, α = 1/(25 + 228.1), ΔT_rated = 25 °C,
//   I_rated = rateA / S_base at V = 1 pu, S_base = 100 MVA.
//
// Reference values: pandapower's reproduction of the paper,
// `pandapower/test/loadflow/test_tdpf.py` (setup `prepare_case_30`,
// lines 114–135; assertions `test_tdpf_frank`, lines 156–189: losses and
// R_Θ to atol 1e-6 MW / 1e-6 K/MW) and its data file
// `test_files/tdpf/case30_branch_details.csv` (41 rows, columns R_PF,
// R_TDPF, PLoss_TDPF, R_THETA); formulas in
// `pandapower/pf/create_jacobian_tdpf.py` (`calc_r_theta_from_t_rise`,
// lines 18–39; `calc_T_frank`, lines 101–126).  pandapower is
// BSD-3-Clause, Copyright (c) 2016-2026 University of Kassel, Fraunhofer
// IEE Kassel and individual contributors; the per-branch numbers below
// are derived from that CSV and are redistributed under its terms.
// Frank's GPL-3 MATLAB code (`importCaseData.m`, rated-rise R_therm) was
// read for the formulas only; no code is copied.
//
// Per-branch current: the CSV's `loading_TDPF` column is a terminal
// quantity (it includes the line charging), not the series current that
// produces the loss, so the series current magnitude used as the input
// below is `I = sqrt(PLoss_TDPF / R_TDPF)`.  R_Θ is computed from the
// rating and R_PF alone (fully independent), and R_TDPF from the thermal
// fixed point at that current; PLoss_TDPF follows.  The CSV stores the
// losses in single precision (~1e-7 relative), which bounds the match.

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>

#include <doctest/doctest.h>
#include <gtopt/ieee738.hpp>
#include <gtopt/line_thermal_resistance.hpp>

using namespace gtopt;
using namespace gtopt::line_thermal;

namespace
{

constexpr double kInf = std::numeric_limits<double>::infinity();
constexpr double kSbase = 100.0;  // MVA
constexpr double kTamb = 25.0;  // °C (= T_ref)

struct Case30Branch
{
  int from;
  int to;
  double r_pf;  ///< R at 25 °C [pu]           (CSV R_PF)
  double rate_mva;  ///< MATPOWER case30 rateA [MVA]
  double i_pu;  ///< series current [pu]  (= sqrt(PLoss_TDPF/R_TDPF))
  double r_theta;  ///< R_Θ [K/MW]                (CSV R_THETA)
  double r_tdpf;  ///< R at the TDPF solution [pu] (CSV R_TDPF)
  double ploss_tdpf;  ///< TDPF series loss [pu]   (CSV PLoss_TDPF)
};

// Zero-resistance branches (transformers 6-9, 6-10, 9-11, 9-10, 4-12,
// 12-13, 28-27) are not thermally modelled: R_Θ = Inf, no loss; their
// `i_pu` is the CSV `loading_TDPF` (no series loss to derive it from).
// Columns: from, to, R_PF [pu], rateA [MVA], I [pu], R_THETA [K/MW],
// R_TDPF [pu], PLoss_TDPF [pu] -- see Case30Branch.
// clang-format off
constexpr std::array<std::array<double, 8>, 41> kCase30Rows {{
    {1.0, 2.0, 0.02, 130.0, 0.11506212328797881, 6.731540525416552, 0.02001409471406782, 0.000264972448348999,},
    {1.0, 3.0, 0.05, 130.0, 0.15939003101012703, 2.692616210166621, 0.05006766026704924, 0.00127197802066803,},
    {2.0, 4.0, 0.06, 65.0, 0.17233010865171952, 8.97538736722207, 0.06038154028329644, 0.001793190836906433,},
    {3.0, 4.0, 0.01, 130.0, 0.1354538925341886, 13.4630810508331, 0.01000976913349742, 0.0001836568117141724,},
    {2.0, 5.0, 0.05, 130.0, 0.14861711005874073, 2.692616210166621, 0.05005881227153991, 0.001105651259422302,},
    {2.0, 6.0, 0.06, 65.0, 0.2196181946092093, 8.97538736722207, 0.06062212791056512, 0.002923935651779175,},
    {4.0, 6.0, 0.01, 90.0, 0.2576361100085916, 28.08963824186166, 0.0100742125115911, 0.0006686896085739136,},
    {5.0, 7.0, 0.05, 70.0, 0.1551700775400879, 9.286778357513448, 0.05022184722548338, 0.001209229230880737,},
    {6.0, 7.0, 0.03, 130.0, 0.10224385315636342, 4.487693683611035, 0.03001669125871012, 0.0003137886524200439,},
    {6.0, 8.0, 0.01, 32.0, 0.3575726136474518, 222.193427499101, 0.01126437119713192, 0.001440241932868958,},
    {6.0, 9.0, 0.0, 65.0, 0.06701884893213833, kInf, 0.0, 0.0,},
    {6.0, 10.0, 0.0, 32.0, 0.03829648510408636, kInf, 0.0, 0.0,},
    {9.0, 11.0, 0.0, 65.0, 0.0, kInf, 0.0, 0.0,},
    {9.0, 10.0, 0.0, 65.0, 0.06752079032586208, kInf, 0.0, 0.0,},
    {4.0, 12.0, 0.0, 65.0, 0.02628365020429532, kInf, 0.0, 0.0,},
    {12.0, 13.0, 0.0, 65.0, 0.3815027958388256, kInf, 0.0, 0.0,},
    {12.0, 14.0, 0.12, 32.0, 0.05545134559771063, 18.51611895825842, 0.1203248039055497, 0.0003699809312820435,},
    {12.0, 15.0, 0.07, 32.0, 0.09658894236168349, 31.74191821415729, 0.07057804703685053, 0.0006584525108337402,},
    {12.0, 16.0, 0.09, 32.0, 0.09407205463523256, 24.68815861101123, 0.0907046819852481, 0.0008026957511901855,},
    {14.0, 15.0, 0.22, 16.0, 0.011829386331370595, 40.39880499983656, 0.2201081676474188, 3.080070018768311e-05,},
    {16.0, 17.0, 0.08, 16.0, 0.062010660948319384, 111.0967137495505, 0.08109505381495655, 0.0003118366003036499,},
    {15.0, 18.0, 0.11, 16.0, 0.0937474036514117, 80.79760999967313, 0.1135028551643535, 0.0009975284337997437,},
    {18.0, 19.0, 0.06, 16.0, 0.0606717214305061, 148.1289516660673, 0.06078574414697364, 0.0002237558364868164,},
    {19.0, 20.0, 0.03, 32.0, 0.05493552340068959, 74.06447583303367, 0.03007969817380454, 9.077787399291992e-05,},
    {10.0, 20.0, 0.09, 32.0, 0.07668940034567186, 24.68815861101123, 0.09046709180966453, 0.0005320608615875244,},
    {10.0, 17.0, 0.03, 32.0, 0.08840424716744633, 74.06447583303367, 0.03020725589730108, 0.0002360790967941284,},
    {10.0, 21.0, 0.03, 32.0, 0.12011250726898604, 74.06447583303367, 0.03038482861196866, 0.0004383623600006104,},
    {10.0, 22.0, 0.07, 32.0, 0.09498058068001194, 31.74191821415729, 0.07055880497189893, 0.0006365329027175903,},
    {21.0, 22.0, 0.01, 32.0, 0.30485031719172784, 222.193427499101, 0.01088833200608995, 0.001011893153190613,},
    {15.0, 23.0, 0.1, 16.0, 0.10412612290433113, 88.87737099964039, 0.1039580150659214, 0.001127138733863831,},
    {22.0, 24.0, 0.12, 16.0, 0.08127373346949933, 74.06447583303367, 0.1228495032248199, 0.0008114725351333618,},
    {23.0, 24.0, 0.13, 16.0, 0.07124957128521735, 68.36720846126185, 0.1323594945626895, 0.0006719231605529785,},
    {24.0, 25.0, 0.19, 16.0, 0.04298190908094337, 46.77756368402127, 0.191240673443684, 0.0003533065319061279,},
    {25.0, 26.0, 0.25, 16.0, 0.043086242328358705, 35.55094839985616, 0.2516404299215094, 0.000467151403427124,},
    {25.0, 27.0, 0.11, 16.0, 0.07536235790632956, 80.79760999967313, 0.1122384821728717, 0.0006374567747116089,},
    {28.0, 27.0, 0.0, 65.0, 0.08646060842831221, kInf, 0.0, 0.0,},
    {27.0, 29.0, 0.22, 16.0, 0.06407826776169463, 40.39880499983656, 0.2232185037328644, 0.0009165406227111816,},
    {27.0, 30.0, 0.32, 16.0, 0.07313507483259443, 27.77417843738763, 0.3261254056296303, 0.001744359731674194,},
    {29.0, 30.0, 0.24, 16.0, 0.03818973547795679, 37.03223791651683, 0.2412354860662685, 0.000351831316947937,},
    {8.0, 28.0, 0.06, 32.0, 0.07753399291367287, 37.03223791651683, 0.06031833097862967, 0.0003626048564910889,},
    {6.0, 28.0, 0.02, 32.0, 0.023522527343638782, 111.0967137495505, 0.02000971773735906, 1.107156276702881e-05,},
}};
// clang-format on

[[nodiscard]] constexpr Case30Branch to_branch(
    const std::array<double, 8>& v) noexcept
{
  return {
      .from = static_cast<int>(v[0]),
      .to = static_cast<int>(v[1]),
      .r_pf = v[2],
      .rate_mva = v[3],
      .i_pu = v[4],
      .r_theta = v[5],
      .r_tdpf = v[6],
      .ploss_tdpf = v[7],
  };
}

[[nodiscard]] LinearResistance case30_resistance(double r_pf) noexcept
{
  return {
      .r_ref = r_pf,
      .alpha = alpha_from_inferred_zero(kTamb),
      .t_ref = kTamb,
  };
}

}  // namespace

TEST_CASE("line_thermal: linear resistance and rated-rise R_theta")  // NOLINT
{
  SUBCASE("alpha from the inferred zero temperature")
  {
    CHECK(alpha_from_inferred_zero(25.0)
          == doctest::Approx(1.0 / 253.1).epsilon(1e-15));
    // R extrapolates to zero at −T_F.
    const LinearResistance r {
        .r_ref = 0.1,
        .alpha = alpha_from_inferred_zero(25.0),
        .t_ref = 25.0,
    };
    CHECK(resistance_at(r, -kAluminumInferredZero)
          == doctest::Approx(0.0).epsilon(1e-15));
    CHECK(resistance_at(r, 25.0) == doctest::Approx(0.1));
  }

  SUBCASE("rated current heats exactly the rated rise")
  {
    const auto r = case30_resistance(0.02);
    const double rth = thermal_resistance_from_rated_rise(r, 1.3);
    const auto st = steady_state(r, rth, kTamb, 1.3);
    REQUIRE(st.has_value());
    CHECK(st.value_or(ThermalState {}).temperature
          == doctest::Approx(kTamb + kDefaultRatedRise).epsilon(1e-12));
  }

  SUBCASE("zero resistance or rating -> infinite R_theta")
  {
    CHECK(thermal_resistance_from_rated_rise(case30_resistance(0.0), 0.65)
          == kInf);
    CHECK(thermal_resistance_from_rated_rise(case30_resistance(0.1), 0.0)
          == kInf);
  }

  SUBCASE("closed form equals the Picard fixed point")
  {
    const auto r = case30_resistance(0.01);
    const double rth = 222.193427499101 * kSbase;  // branch 6-8, K/pu
    const double i = 0.5;
    double t = kTamb;
    for (int it = 0; it < 200; ++it) {
      t = temperature_from_loss(kTamb, rth, i * i * resistance_at(r, t));
    }
    const auto st = steady_state(r, rth, kTamb, i);
    REQUIRE(st.has_value());
    CHECK(st.value_or(ThermalState {}).temperature
          == doctest::Approx(t).epsilon(1e-12));
  }

  SUBCASE("thermal runaway has no steady state")
  {
    const auto r = case30_resistance(0.01);
    const double rth = 222.193427499101 * kSbase;
    // k = α·R_Θ·I²·R_ref ≥ 1  ⇔  I² ≥ 1/(α·R_Θ·R_ref)
    const double i_crit = std::sqrt(1.0 / (r.alpha * rth * r.r_ref));
    CHECK(steady_state(r, rth, kTamb, 0.999 * i_crit).has_value());
    CHECK_FALSE(steady_state(r, rth, kTamb, i_crit).has_value());
    CHECK_FALSE(steady_state(r, rth, kTamb, 2.0 * i_crit).has_value());
  }

  SUBCASE("zero current stays at ambient")
  {
    const auto st = steady_state(case30_resistance(0.05), 100.0, 30.0, 0.0);
    REQUIRE(st.has_value());
    CHECK(st.value_or(ThermalState {}).temperature == doctest::Approx(30.0));
    CHECK(st.value_or(ThermalState {}).loss == doctest::Approx(0.0));
  }
}

TEST_CASE("line_thermal: Frank 2013 TDPF IEEE case30 per branch")  // NOLINT
{
  REQUIRE(kCase30Rows.size() == 41);

  double max_abs_rtheta = 0.0;  // K/MW
  double max_rel_rtheta = 0.0;
  double max_abs_r = 0.0;  // pu
  double max_rel_r = 0.0;
  double max_abs_loss_mw = 0.0;
  double max_rel_loss = 0.0;
  double max_abs_r_from_loss = 0.0;  // pu (T from the CSV loss)

  for (const auto& row : kCase30Rows) {
    const auto b = to_branch(row);
    CAPTURE(b.from);
    CAPTURE(b.to);
    const auto r = case30_resistance(b.r_pf);
    const double rth_pu =
        thermal_resistance_from_rated_rise(r, b.rate_mva / kSbase);

    if (b.r_pf == 0.0) {
      CHECK(rth_pu == kInf);
      CHECK(b.r_theta == kInf);
      const auto st = steady_state(r, rth_pu, kTamb, b.i_pu);
      REQUIRE(st.has_value());
      CHECK(st.value_or(ThermalState {}).loss == 0.0);
      CHECK(st.value_or(ThermalState {}).resistance == 0.0);
      continue;
    }

    // 1. R_Θ [K/MW] from the rating and R_PF only.
    const double rth_mw = rth_pu / kSbase;
    CHECK(rth_mw == doctest::Approx(b.r_theta).epsilon(1e-12));
    CHECK(std::abs(rth_mw - b.r_theta) < 1e-6);  // pandapower atol

    // 2. Thermal law alone: T = T_amb + R_Θ·PLoss_TDPF reproduces R_TDPF.
    const auto from_loss =
        steady_state_from_loss(r, rth_pu, kTamb, b.ploss_tdpf);
    CHECK(from_loss.resistance == doctest::Approx(b.r_tdpf).epsilon(1e-6));

    // 3. Fixed point at the series current: R_TDPF and PLoss_TDPF.
    const auto st = steady_state(r, rth_pu, kTamb, b.i_pu);
    REQUIRE(st.has_value());
    const auto s = st.value_or(ThermalState {});
    CHECK(s.resistance == doctest::Approx(b.r_tdpf).epsilon(1e-6));
    CHECK(s.loss == doctest::Approx(b.ploss_tdpf).epsilon(1e-6));
    CHECK(std::abs((s.loss - b.ploss_tdpf) * kSbase) < 1e-6);  // MW
    CHECK(s.temperature > kTamb);
    // Thermal law holds at the fixed point.  Every branch stays below
    // the rated rise except 6-8, which TDPF loads to 109 % of rateA.
    CHECK(s.temperature - kTamb
          == doctest::Approx(rth_pu * s.loss).epsilon(1e-12));
    CHECK(s.temperature
          < kTamb + (1.25 * 1.25 * kDefaultRatedRise));  // ≤ 125 % loading

    max_abs_rtheta = std::max(max_abs_rtheta, std::abs(rth_mw - b.r_theta));
    max_rel_rtheta =
        std::max(max_rel_rtheta, std::abs(rth_mw - b.r_theta) / b.r_theta);
    max_abs_r = std::max(max_abs_r, std::abs(s.resistance - b.r_tdpf));
    max_rel_r =
        std::max(max_rel_r, std::abs(s.resistance - b.r_tdpf) / b.r_tdpf);
    max_abs_loss_mw =
        std::max(max_abs_loss_mw, std::abs(s.loss - b.ploss_tdpf) * kSbase);
    max_rel_loss =
        std::max(max_rel_loss, std::abs(s.loss - b.ploss_tdpf) / b.ploss_tdpf);
    max_abs_r_from_loss = std::max(max_abs_r_from_loss,
                                   std::abs(from_loss.resistance - b.r_tdpf));
  }

  MESSAGE("case30 TDPF: R_theta max abs ",
          max_abs_rtheta,
          " K/MW, rel ",
          max_rel_rtheta,
          "; R_TDPF max abs ",
          max_abs_r,
          " pu, rel ",
          max_rel_r,
          "; PLoss max abs ",
          max_abs_loss_mw,
          " MW, rel ",
          max_rel_loss,
          "; R from CSV loss max abs ",
          max_abs_r_from_loss);
  CHECK(max_rel_rtheta < 1e-12);
  CHECK(max_abs_loss_mw < 1e-6);
  CHECK(max_rel_r < 1e-6);
}

TEST_CASE("line_thermal: IEEE 738 heat balance vs linear R_theta")  // NOLINT
{
  // Drake 26/7 ACSR (as in test_ieee738.cpp).
  constexpr ieee738::ConductorParams kDrake {
      .diameter = 0.02814,
      .r25 = 7.283e-5,
      .r75 = 8.688e-5,
      .emissivity = 0.8,
      .absorptivity = 0.8,
  };
  const auto lin = from_ieee738(kDrake);
  CHECK(resistance_at(lin, 75.0) == doctest::Approx(kDrake.r75));
  CHECK(resistance_at(lin, 100.0)
        == doctest::Approx(
            ieee738::resistance_at(100.0, kDrake.r25, kDrake.r75)));

  // `max_dt_below`: bound on |T_738 − T_lin| below the rating [K];
  // `dt_at_zero`: T_738 − T_lin at zero current (the solar rise).
  struct Weather
  {
    const char* name;
    double wind;
    double sun;
    double max_dt_below;
    double dt_at_zero;
  };
  const auto cases = std::to_array<Weather>({
      {
          .name = "wind 0.61 m/s, night",
          .wind = 0.61,
          .sun = 0.0,
          .max_dt_below = 1.0,
          .dt_at_zero = 0.0,
      },
      {
          .name = "still air, night",
          .wind = 0.0,
          .sun = 0.0,
          .max_dt_below = 3.5,
          .dt_at_zero = 0.0,
      },
      {
          .name = "wind 0.61 m/s, sun 1000",
          .wind = 0.61,
          .sun = 1000.0,
          .max_dt_below = 12.5,
          .dt_at_zero = 12.3337,
      },
  });

  for (const auto& c : cases) {
    CAPTURE(c.name);
    const ieee738::SpanWeather wx {
        .ambient_temperature = kTamb,
        .wind_speed = c.wind,
        .attack_angle = 90.0,
        .solar_irradiance = c.sun,
        .elevation = 0.0,
    };
    // Calibrate R_Θ at the IEEE 738 rating for 75 °C (ΔT_rated = 50).
    const double rated_rise = 50.0;
    const double i_rated = ieee738::ampacity(kDrake, kTamb + rated_rise, wx);
    const double rth =
        thermal_resistance_from_rated_rise(lin, i_rated, rated_rise);
    for (const double f : {0.0, 0.25, 0.5, 0.75, 1.0, 1.25}) {
      const double amps = f * i_rated;
      const auto t_ieee = ieee738_conductor_temperature(kDrake, wx, amps);
      const auto t_lin = steady_state(lin, rth, kTamb, amps);
      REQUIRE(t_ieee.has_value());
      REQUIRE(t_lin.has_value());
      const double t738 = t_ieee.value_or(0.0);
      const double tl = t_lin.value_or(ThermalState {}).temperature;
      const double r_ratio =
          ieee738::resistance_at(t738, kDrake.r25, kDrake.r75)
          / t_lin.value_or(ThermalState {}).resistance;
      MESSAGE(c.name,
              " I/I_r=",
              f,
              " T_738=",
              t738,
              " T_lin=",
              tl,
              " R_738/R_lin-1=",
              r_ratio - 1.0);
      CAPTURE(f);
      // R(T) differs by < 5 % over 0..125 % of the rating.
      CHECK(std::abs(r_ratio - 1.0) < 0.05);
      if (f == 0.0) {
        // No solar term in the linear model: it misses the solar rise.
        CHECK(t738 - tl == doctest::Approx(c.dt_at_zero).epsilon(1e-4));
      } else if (f < 1.0) {
        // Below the rating the linear model runs cold.
        CHECK(t738 > tl);
        CHECK(t738 - tl < c.max_dt_below);
      } else if (f == 1.0) {
        CHECK(t738 == doctest::Approx(tl).epsilon(1e-9));  // calibration
      } else {
        CHECK(t738 < tl);  // above the rating it runs hot
      }
    }
  }
}
