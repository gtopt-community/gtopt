/**
 * @file      line_dlr.hpp
 * @brief     Dynamic line rating (DLR) through critical spans — schema
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A Line may carry an optional `dlr` object.  It lists one or more
 * CRITICAL SPANS — the spans whose conductor temperature limits the line
 * (Matus et al., "Identification of Critical Spans for Monitoring Systems
 * in Dynamic Thermal Rating", IEEE Trans. Power Delivery 27(2), 2012,
 * DOI 10.1109/TPWRD.2012.2185254).  A single representative span is a
 * valid and common case.  How the spans are SELECTED is out of scope
 * here (Python tooling); gtopt only consumes the list.
 *
 * @see M. Matus et al., IEEE Trans. Power Delivery 27(2):1002–1009,
 *      2012, https://doi.org/10.1109/TPWRD.2012.2185254
 *      (open access:
 * https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)
 * @see IEEE Std 738-2012 (ampacity, `ieee738.hpp`)
 *
 * Each span carries a per-(scenario, stage, block) weather forecast
 * (ambient temperature, wind speed and direction, solar irradiance on
 * the conductor).  At LP build gtopt computes, per block, each span's
 * steady-state ampacity with the IEEE Std 738 heat balance
 * (`ieee738.hpp`), converts it to MW and limits the line flow by the
 * minimum (`span_mode = "min"`) or by one row per span (`"rows"`).
 * Weather is exogenous, so the rating is a per-block right-hand side and
 * the LP stays linear.  A precomputed per-span MW schedule (`rating`)
 * bypasses IEEE 738.
 *
 * ### JSON Example
 * ```json
 * "dlr": {
 *   "conductor": {"diameter": 0.02814, "r25": 7.283e-5, "r75": 8.688e-5,
 *                 "emissivity": 0.8, "absorptivity": 0.8,
 *                 "subconductors": 1},
 *   "tmax_conductor": 75,
 *   "tmax_conductor_emergency": 100,
 *   "voltage": 220,
 *   "critical_spans": [
 *     {"uid": 1, "name": "s1", "azimuth": 35, "elevation": 420,
 *      "ambient_temperature": 25, "wind_speed": "dlr_wind_speed",
 *      "wind_direction": 270, "solar_irradiance": [[[0, 800]]]}
 *   ]
 * }
 * ```
 *
 * Weather fields (and `rating`) are STB schedules: a scalar, a 3-D
 * inline array `[scenario][stage][block]`, or a file name resolved as
 * `input_directory/Line/<name>.{parquet,csv}` in long layout (`scenario,
 * stage, block, uid, value`) where `uid` is the SPAN uid.
 */

#pragma once

#include <optional>

#include <gtopt/field_sched.hpp>
#include <gtopt/line_enums.hpp>

namespace gtopt
{

/// Conductor thermal parameters (IEEE 738, SI).
///
/// Resistance: give EITHER the two tabulated AC values `r25` / `r75`
/// (linear between them, extrapolated beyond) OR the DC value `r20` with
/// its temperature coefficient `alpha_r` and an `ac_factor`:
/// `R(T) = ac_factor · r20 · (1 + alpha_r·(T − 20))` (also linear, so
/// both forms feed the same IEEE 738 evaluation).
struct DlrConductor
{
  OptReal diameter {};  ///< Outside diameter [m]
  OptReal r25 {};  ///< AC resistance at 25 °C [Ω/m]
  OptReal r75 {};  ///< AC resistance at 75 °C [Ω/m]
  OptReal r20 {};  ///< DC resistance at 20 °C [Ω/m] (alternative form)
  OptReal alpha_r {};  ///< Temperature coefficient of r20 [1/K]
  OptReal ac_factor {};  ///< AC/DC resistance ratio (default 1)
  OptReal emissivity {};  ///< Emissivity ε ∈ [0, 1] (default 0.8)
  OptReal absorptivity {};  ///< Solar absorptivity α ∈ [0, 1] (default 0.8)
  OptInt subconductors {};  ///< Conductors per phase bundle (default 1)
};

/// One critical span of a DLR line.
struct CriticalSpan
{
  /// Span uid.  Keys the long-layout weather files and the per-span
  /// outputs (`Line/span_thermal_dual`), so it must be unique across
  /// every span of the case.  Default: `1000 · line.uid + position`
  /// (1-based position in `critical_spans`).
  OptUid uid {};
  OptName name {};  ///< Span name (default `"<line>_s<position>"`)
  OptReal azimuth {};  ///< Span axis bearing [deg from North] (default 0)
  OptReal elevation {};  ///< Altitude above sea level [m] (default 0)

  OptSTBRealFieldSched ambient_temperature {};  ///< T_a [°C]
  OptSTBRealFieldSched wind_speed {};  ///< V_w [m/s]
  OptSTBRealFieldSched wind_direction {};  ///< Wind bearing [deg]
  /// Solar irradiance [W/m²].  Under `dlr.solar_mode = "ghi"` (default)
  /// the global horizontal irradiance, used directly as the absorbed
  /// flux `q_s = α·S·D` (conservative; no sun-geometry model).
  OptSTBRealFieldSched solar_irradiance {};

  OptReal tmax_conductor {};  ///< Per-span override of `dlr.tmax_conductor`
  /// Per-span override of `dlr.tmax_conductor_emergency`.
  OptReal tmax_conductor_emergency {};

  /// Precomputed normal rating [MW] (bypasses IEEE 738 and the weather
  /// fields; used verbatim — no safety factor or v_pu/pf scaling).
  OptSTBRealFieldSched rating {};
  /// Precomputed emergency rating [MW] (with `rating`).
  OptSTBRealFieldSched rating_emergency {};
};

/// Dynamic line rating of a Line through its critical spans.
struct LineDlr
{
  std::optional<DlrConductor> conductor {};  ///< Required for IEEE 738
  OptReal tmax_conductor {};  ///< Normal max conductor temp [°C] (def. 75)
  /// Emergency max conductor temperature [°C].  When set (or when a span
  /// has `rating_emergency`) the emergency rating is the hard cap and the
  /// normal rating the soft threshold of the `overload_penalty` band.
  OptReal tmax_conductor_emergency {};
  OptReal voltage {};  ///< kV for A→MW (default: `Line.voltage`)
  OptReal v_pu {};  ///< Operating voltage [p.u.] (default 1)
  OptReal power_factor {};  ///< Power factor (default 1)
  OptReal safety_factor {};  ///< η ∈ (0, ∞) (default 1 = off)
  OptName span_mode {};  ///< `"min"` (default) or `"rows"`
  /// Use the emergency tier when one is configured (default `true`);
  /// `false` keeps the normal rating as the hard cap.
  OptBool use_emergency {};
  /// Low-wind rule: below this speed [m/s] the wind direction is ignored
  /// and `low_wind_angle` is used (default 0.5; `0` disables the rule).
  OptReal low_wind_speed {};
  OptReal low_wind_angle {};  ///< Low-wind attack angle [deg] (default 45)
  OptName solar_mode {};  ///< `"ghi"` (default) or `"none"`
  Array<CriticalSpan> critical_spans {};
  /// How the DLR rating combines with the static limits: `"replace"`
  /// (it replaces `conductor_rating_*`, or — without one — `max_rating`
  /// / `min_rating`) or `"min"` (`min(static, DLR)`).  Unset →
  /// `model_options.dlr_limit_mode`; both unset → `replace` with a
  /// conductor rating, else `min`.  See `LineDlrLimitMode`.
  OptName limit_mode {};

  [[nodiscard]] constexpr LineDlrSolarMode solar_mode_enum() const noexcept
  {
    if (solar_mode.has_value()) {
      return enum_from_name<LineDlrSolarMode>(*solar_mode)
          .value_or(LineDlrSolarMode::ghi);
    }
    return LineDlrSolarMode::ghi;
  }

  [[nodiscard]] constexpr LineDlrSpanMode span_mode_enum() const noexcept
  {
    if (span_mode.has_value()) {
      return enum_from_name<LineDlrSpanMode>(*span_mode)
          .value_or(LineDlrSpanMode::min);
    }
    return LineDlrSpanMode::min;
  }
};

using OptLineDlr = std::optional<LineDlr>;

}  // namespace gtopt
