/**
 * @file      json_line_dlr.hpp
 * @brief     JSON mapping for the Line dynamic line rating (`Line.dlr`)
 * @date      2026-11-25
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/line_dlr.hpp>

namespace daw::json
{
using gtopt::CriticalSpan;
using gtopt::DlrConductor;
using gtopt::LineDlr;

template<>
struct json_data_contract<DlrConductor>
{
  using type = json_member_list<json_number_null<"diameter", OptReal>,
                                json_number_null<"r25", OptReal>,
                                json_number_null<"r75", OptReal>,
                                json_number_null<"r20", OptReal>,
                                json_number_null<"alpha_r", OptReal>,
                                json_number_null<"ac_factor", OptReal>,
                                json_number_null<"emissivity", OptReal>,
                                json_number_null<"absorptivity", OptReal>,
                                json_number_null<"subconductors", OptInt>>;

  constexpr static auto to_json_data(DlrConductor const& c)
  {
    return std::forward_as_tuple(c.diameter,
                                 c.r25,
                                 c.r75,
                                 c.r20,
                                 c.alpha_r,
                                 c.ac_factor,
                                 c.emissivity,
                                 c.absorptivity,
                                 c.subconductors);
  }
};

template<>
struct json_data_contract<CriticalSpan>
{
  using type = json_member_list<
      json_number_null<"uid", OptUid>,
      json_string_null<"name", OptName>,
      json_number_null<"azimuth", OptReal>,
      json_number_null<"elevation", OptReal>,
      json_variant_null<"ambient_temperature",
                        OptSTBRealFieldSched,
                        jvtl_STBRealFieldSched>,
      json_variant_null<"wind_speed",
                        OptSTBRealFieldSched,
                        jvtl_STBRealFieldSched>,
      json_variant_null<"wind_direction",
                        OptSTBRealFieldSched,
                        jvtl_STBRealFieldSched>,
      json_variant_null<"solar_irradiance",
                        OptSTBRealFieldSched,
                        jvtl_STBRealFieldSched>,
      json_number_null<"tmax_conductor", OptReal>,
      json_number_null<"tmax_conductor_emergency", OptReal>,
      json_variant_null<"rating", OptSTBRealFieldSched, jvtl_STBRealFieldSched>,
      json_variant_null<"rating_emergency",
                        OptSTBRealFieldSched,
                        jvtl_STBRealFieldSched>>;

  constexpr static auto to_json_data(CriticalSpan const& s)
  {
    return std::forward_as_tuple(s.uid,
                                 s.name,
                                 s.azimuth,
                                 s.elevation,
                                 s.ambient_temperature,
                                 s.wind_speed,
                                 s.wind_direction,
                                 s.solar_irradiance,
                                 s.tmax_conductor,
                                 s.tmax_conductor_emergency,
                                 s.rating,
                                 s.rating_emergency);
  }
};

template<>
struct json_data_contract<LineDlr>
{
  using type = json_member_list<
      json_class_null<"conductor", std::optional<DlrConductor>>,
      json_number_null<"tmax_conductor", OptReal>,
      json_number_null<"tmax_conductor_emergency", OptReal>,
      json_number_null<"voltage", OptReal>,
      json_number_null<"v_pu", OptReal>,
      json_number_null<"power_factor", OptReal>,
      json_number_null<"safety_factor", OptReal>,
      json_string_null<"span_mode", OptName>,
      json_bool_null<"use_emergency", OptBool>,
      json_number_null<"low_wind_speed", OptReal>,
      json_number_null<"low_wind_angle", OptReal>,
      json_string_null<"solar_mode", OptName>,
      json_array_null<"critical_spans", Array<CriticalSpan>, CriticalSpan>,
      json_string_null<"limit_mode", OptName>>;

  constexpr static auto to_json_data(LineDlr const& d)
  {
    return std::forward_as_tuple(d.conductor,
                                 d.tmax_conductor,
                                 d.tmax_conductor_emergency,
                                 d.voltage,
                                 d.v_pu,
                                 d.power_factor,
                                 d.safety_factor,
                                 d.span_mode,
                                 d.use_emergency,
                                 d.low_wind_speed,
                                 d.low_wind_angle,
                                 d.solar_mode,
                                 d.critical_spans,
                                 d.limit_mode);
  }
};

}  // namespace daw::json
