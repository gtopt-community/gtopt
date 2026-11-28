/**
 * @file      json_stage.hpp
 * @brief     JSON serialization for Stage
 * @date      Sun Mar 30 17:33:28 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module provides the daw::json data contract specialization for
 * serializing and deserializing Stage objects to and from JSON.
 */

#pragma once

#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/json/json_enum_option.hpp>
#include <gtopt/stage.hpp>

namespace daw::json
{
using gtopt::MonthType;
using gtopt::Stage;

/// Custom constructor: converts JSON month string → MonthType enum.
struct StageConstructor
{
  [[nodiscard]] Stage operator()(
      Uid uid,
      OptName name,
      OptBool active,
      Size first_block,
      Size count_block,
      OptReal discount_factor,
      OptName month_str,
      OptBool chronological,
      OptInt cycle_group,
      OptReal cycle_weight,
      OptReal period_hours,
      OptName sampling,
      OptBool periodic,
      OptReal start_hour,
      std::vector<gtopt::Real> commitment_breaks) const
  {
    Stage s;
    s.uid = uid;
    s.name = std::move(name);
    s.active = active;
    s.first_block = first_block;
    s.count_block = count_block;
    s.discount_factor = discount_factor;
    if (month_str) {
      s.month = gtopt::require_enum<MonthType>("month", *month_str);
    }
    s.chronological = chronological;
    s.cycle_group = cycle_group;
    s.cycle_weight = cycle_weight;
    s.period_hours = period_hours;
    s.sampling = std::move(sampling);
    s.periodic = periodic;
    s.start_hour = start_hour;
    s.commitment_breaks = std::move(commitment_breaks);
    return s;
  }
};

template<>
struct json_data_contract<Stage>
{
  using constructor_t = StageConstructor;

  using type =
      json_member_list<json_number<"uid", Uid>,
                       json_string_null<"name", OptName>,
                       json_number_null<"active", OptBool>,
                       json_number<"first_block", Size>,
                       json_number<"count_block", Size>,
                       json_number_null<"discount_factor", OptReal>,
                       json_string_null<"month", OptName>,
                       json_bool_null<"chronological", OptBool>,
                       json_number_null<"cycle_group", OptInt>,
                       json_number_null<"cycle_weight", OptReal>,
                       json_number_null<"period_hours", OptReal>,
                       json_string_null<"sampling", OptName>,
                       json_bool_null<"periodic", OptBool>,
                       json_number_null<"start_hour", OptReal>,
                       json_array_null<"commitment_breaks",
                                       std::vector<gtopt::Real>,
                                       json_number_no_name<gtopt::Real>>>;

  static auto to_json_data(Stage const& stage)
  {
    return std::make_tuple(stage.uid,
                           stage.name,
                           stage.active,
                           stage.first_block,
                           stage.count_block,
                           stage.discount_factor,
                           detail::enum_to_opt_name(stage.month),
                           stage.chronological,
                           stage.cycle_group,
                           stage.cycle_weight,
                           stage.period_hours,
                           stage.sampling,
                           stage.periodic,
                           stage.start_hour,
                           stage.commitment_breaks);
  }
};
}  // namespace daw::json
