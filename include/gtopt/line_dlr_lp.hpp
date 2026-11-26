/**
 * @file      line_dlr_lp.hpp
 * @brief     Per-block span ratings of a Line dynamic line rating (DLR)
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `LineDlrLP` resolves the `Line.dlr` input (`line_dlr.hpp`) once per
 * LineLP — defaults applied, STB weather schedules indexed — and
 * evaluates the per-(scenario, stage, block) MW rating of every critical
 * span (IEEE Std 738, `ieee738.hpp`, or the precomputed `rating`).
 * `LineLP::add_to_lp` turns those ratings into flow limits.
 */

#pragma once

#include <optional>
#include <span>
#include <string>
#include <vector>

#include <gtopt/ieee738.hpp>
#include <gtopt/line_dlr.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

/// MW rating of one span at one conductor temperature (IEEE 738 ampacity
/// converted with `ieee738::ampacity_to_mw`).
[[nodiscard]] double dlr_span_rating_mw(
    const ieee738::ConductorParams& conductor,
    double conductor_temperature,
    const ieee738::SpanWeather& weather,
    double voltage_kv,
    double v_pu,
    double power_factor,
    int subconductors,
    double safety_factor) noexcept;

/// Default uid of the `position`-th (1-based) span of line `line_uid`.
[[nodiscard]] constexpr Uid dlr_default_span_uid(Uid line_uid,
                                                 int position) noexcept
{
  return (1000 * line_uid) + position;
}

class LineDlrLP
{
public:
  /// One resolved critical span.
  struct Span
  {
    Uid uid {unknown_uid};
    Name name {};
    double azimuth {0.0};
    double elevation {0.0};
    double tmax_normal {75.0};
    std::optional<double> tmax_emergency {};
    OptSTBRealSched ambient_temperature;
    OptSTBRealSched wind_speed;
    OptSTBRealSched wind_direction;
    OptSTBRealSched solar_irradiance;
    OptSTBRealSched rating;
    OptSTBRealSched rating_emergency;
  };

  /// Ratings of every span at one block [MW].  `emergency[k]` equals
  /// `normal[k]` for spans without an emergency tier.
  struct BlockRatings
  {
    std::vector<double> normal;
    std::vector<double> emergency;
  };

  LineDlrLP(LineDlr dlr,
            const InputContext& ic,
            std::string_view class_name,
            Uid line_uid,
            const Name& line_name);

  [[nodiscard]] constexpr LineDlrSpanMode mode() const noexcept
  {
    return m_mode_;
  }

  /// True when any span has an emergency tier.
  [[nodiscard]] constexpr bool has_emergency() const noexcept
  {
    return m_has_emergency_;
  }

  [[nodiscard]] constexpr std::span<const Span> spans() const noexcept
  {
    return m_spans_;
  }

  /// `dlr.limit_mode` when set (else the caller uses
  /// `model_options.dlr_limit_mode`).
  [[nodiscard]] constexpr const std::optional<LineDlrLimitMode>& limit_mode()
      const noexcept
  {
    return m_limit_mode_;
  }

  /// `dlr.voltage` [kV] when set (else the caller uses `Line.voltage`).
  [[nodiscard]] constexpr const std::optional<double>& voltage() const noexcept
  {
    return m_voltage_;
  }

  /// Evaluate every span's rating at `(scenario, stage, block)`.
  /// Throws `std::runtime_error` when a weather value is missing or the
  /// voltage is not positive (data error the validator could not see,
  /// e.g. a file-backed schedule without that cell).
  void ratings_at(ScenarioUid scenario,
                  StageUid stage,
                  BlockUid block,
                  double voltage_kv,
                  BlockRatings& out) const;

private:
  [[nodiscard]] double weather_rating(const Span& span,
                                      double tmax,
                                      ScenarioUid scenario,
                                      StageUid stage,
                                      BlockUid block,
                                      double voltage_kv) const;

  std::string m_line_name_;
  LineDlrSpanMode m_mode_ {LineDlrSpanMode::min};
  bool m_has_emergency_ {false};
  ieee738::ConductorParams m_conductor_ {};
  int m_subconductors_ {1};
  std::optional<double> m_voltage_ {};
  double m_v_pu_ {1.0};
  double m_power_factor_ {1.0};
  double m_safety_factor_ {1.0};
  double m_low_wind_speed_ {ieee738::kLowWindSpeed};
  double m_low_wind_angle_ {ieee738::kLowWindAttackAngle};
  LineDlrSolarMode m_solar_mode_ {LineDlrSolarMode::ghi};
  std::optional<LineDlrLimitMode> m_limit_mode_ {};
  std::vector<Span> m_spans_;
};

}  // namespace gtopt
