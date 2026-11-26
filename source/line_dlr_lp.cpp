// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial

#include <algorithm>
#include <format>
#include <ranges>
#include <stdexcept>
#include <utility>

#include <gtopt/input_context.hpp>
#include <gtopt/line_dlr_lp.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

double dlr_span_rating_mw(const ieee738::ConductorParams& conductor,
                          double conductor_temperature,
                          const ieee738::SpanWeather& weather,
                          double voltage_kv,
                          double v_pu,
                          double power_factor,
                          int subconductors,
                          double safety_factor) noexcept
{
  const double amps =
      ieee738::ampacity(conductor, conductor_temperature, weather);
  return ieee738::ampacity_to_mw(
      amps, voltage_kv, v_pu, power_factor, subconductors, safety_factor);
}

LineDlrLP::LineDlrLP(LineDlr dlr,
                     const InputContext& ic,
                     std::string_view class_name,
                     Uid line_uid,
                     const Name& line_name)
    : m_line_name_(line_name)
    , m_mode_(dlr.span_mode_enum())
    , m_voltage_(dlr.voltage)
    , m_v_pu_(dlr.v_pu.value_or(1.0))
    , m_power_factor_(dlr.power_factor.value_or(1.0))
    , m_safety_factor_(dlr.safety_factor.value_or(1.0))
    , m_low_wind_speed_(dlr.low_wind_speed.value_or(ieee738::kLowWindSpeed))
    , m_low_wind_angle_(
          dlr.low_wind_angle.value_or(ieee738::kLowWindAttackAngle))
    , m_solar_mode_(dlr.solar_mode_enum())
{
  if (dlr.limit_mode) {
    m_limit_mode_ = enum_from_name<LineDlrLimitMode>(*dlr.limit_mode);
  }
  if (dlr.conductor) {
    const auto& c = *dlr.conductor;
    // Resistance: tabulated r25/r75, or r20 + alpha_r (+ ac_factor).
    auto rp = ieee738::ResistancePoints {
        .r25 = c.r25.value_or(0.0),
        .r75 = c.r75.value_or(0.0),
    };
    if (c.r20 && !c.r25 && !c.r75) {
      rp = ieee738::resistance_from_r20(
          *c.r20, c.alpha_r.value_or(0.0), c.ac_factor.value_or(1.0));
    }
    m_conductor_ = ieee738::ConductorParams {
        .diameter = c.diameter.value_or(0.0),
        .r25 = rp.r25,
        .r75 = rp.r75,
        .emissivity = c.emissivity.value_or(0.8),
        .absorptivity = c.absorptivity.value_or(0.8),
    };
    m_subconductors_ = c.subconductors.value_or(1);
  }

  m_spans_.reserve(dlr.critical_spans.size());
  int position = 0;
  for (auto& cs : dlr.critical_spans) {
    ++position;
    const Uid suid = cs.uid.value_or(dlr_default_span_uid(line_uid, position));
    Name sname = cs.name.value_or(std::format("{}_s{}", line_name, position));
    const Id sid {suid, sname};
    const auto tmax_e = cs.tmax_conductor_emergency
        ? cs.tmax_conductor_emergency
        : dlr.tmax_conductor_emergency;
    Span span {
        .uid = suid,
        .name = std::move(sname),
        .azimuth = cs.azimuth.value_or(0.0),
        .elevation = cs.elevation.value_or(0.0),
        .tmax_normal =
            cs.tmax_conductor.value_or(dlr.tmax_conductor.value_or(75.0)),
        .tmax_emergency = tmax_e,
        .ambient_temperature =
            OptSTBRealSched {
                ic, class_name, sid, std::move(cs.ambient_temperature)},
        .wind_speed =
            OptSTBRealSched {ic, class_name, sid, std::move(cs.wind_speed)},
        .wind_direction =
            OptSTBRealSched {ic, class_name, sid, std::move(cs.wind_direction)},
        .solar_irradiance =
            OptSTBRealSched {
                ic, class_name, sid, std::move(cs.solar_irradiance)},
        .rating = OptSTBRealSched {ic, class_name, sid, std::move(cs.rating)},
        .rating_emergency =
            OptSTBRealSched {
                ic, class_name, sid, std::move(cs.rating_emergency)},
    };
    if (!dlr.use_emergency.value_or(true)) {
      span.tmax_emergency.reset();
      span.rating_emergency = OptSTBRealSched {};
    }
    m_has_emergency_ = m_has_emergency_
        || (span.rating.has_value() ? span.rating_emergency.has_value()
                                    : span.tmax_emergency.has_value());
    m_spans_.push_back(std::move(span));
  }
}

double LineDlrLP::weather_rating(const Span& span,
                                 double tmax,
                                 ScenarioUid scenario,
                                 StageUid stage,
                                 BlockUid block,
                                 double voltage_kv) const
{
  const auto need = [&](const OptSTBRealSched& sched,
                        std::string_view field) -> double
  {
    if (auto v = sched.at(scenario, stage, block)) {
      return *v;
    }
    throw std::runtime_error(
        std::format("Line '{}' dlr span '{}': no '{}' value at scenario {} "
                    "stage {} block {} (and no precomputed 'rating')",
                    m_line_name_,
                    span.name,
                    field,
                    scenario,
                    stage,
                    block));
  };
  const double ta = need(span.ambient_temperature, "ambient_temperature");
  const double ws = need(span.wind_speed, "wind_speed");
  const double wd = need(span.wind_direction, "wind_direction");
  const double si = m_solar_mode_ == LineDlrSolarMode::none
      ? 0.0
      : need(span.solar_irradiance, "solar_irradiance");
  const ieee738::SpanWeather weather {
      .ambient_temperature = ta,
      .wind_speed = ws,
      .attack_angle = ieee738::attack_angle(
          wd, span.azimuth, ws, m_low_wind_speed_, m_low_wind_angle_),
      .solar_irradiance = si,
      .elevation = span.elevation,
  };
  return dlr_span_rating_mw(m_conductor_,
                            tmax,
                            weather,
                            voltage_kv,
                            m_v_pu_,
                            m_power_factor_,
                            m_subconductors_,
                            m_safety_factor_);
}

void LineDlrLP::ratings_at(ScenarioUid scenario,
                           StageUid stage,
                           BlockUid block,
                           double voltage_kv,
                           BlockRatings& out) const
{
  out.normal.assign(m_spans_.size(), 0.0);
  out.emergency.assign(m_spans_.size(), 0.0);
  for (const auto& [k, span] : std::views::enumerate(m_spans_)) {
    const auto idx = static_cast<std::size_t>(k);
    if (span.rating.has_value()) {
      const auto r = span.rating.at(scenario, stage, block);
      if (!r) {
        throw std::runtime_error(std::format(
            "Line '{}' dlr span '{}': no 'rating' value at scenario {} stage "
            "{} block {}",
            m_line_name_,
            span.name,
            scenario,
            stage,
            block));
      }
      out.normal[idx] = std::max(*r, 0.0);
      const auto re = span.rating_emergency.at(scenario, stage, block);
      out.emergency[idx] = std::max(re.value_or(out.normal[idx]), 0.0);
      continue;
    }
    if (!(voltage_kv > 0.0)) {
      throw std::runtime_error(
          std::format("Line '{}' dlr: the A→MW conversion needs a positive "
                      "voltage in kV (dlr.voltage or Line.voltage), got {}",
                      m_line_name_,
                      voltage_kv));
    }
    out.normal[idx] = weather_rating(
        span, span.tmax_normal, scenario, stage, block, voltage_kv);
    out.emergency[idx] = span.tmax_emergency
        ? weather_rating(
              span, *span.tmax_emergency, scenario, stage, block, voltage_kv)
        : out.normal[idx];
  }
}

}  // namespace gtopt
