/**
 * @file      json_sanity_options.hpp
 * @brief     JSON serialization for SanityOptions
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/sanity_options.hpp>

namespace daw::json
{
using gtopt::SanityOptions;

template<>
struct json_data_contract<SanityOptions>
{
  using type =
      json_member_list<json_bool_null<"enable", OptBool>,
                       json_number_null<"reward_fail_cost_ratio", OptReal>,
                       json_number_null<"profile_tolerance", OptReal>,
                       json_number_null<"line_x_outlier_factor", OptReal>,
                       json_number_null<"line_x_min_class_size", OptInt>,
                       json_number_null<"line_x_pu_max", OptReal>,
                       json_number_null<"line_xr_max", OptReal>,
                       json_number_null<"max_offenders", OptInt>>;

  static auto to_json_data(SanityOptions const& opt)
  {
    return std::forward_as_tuple(opt.enable,
                                 opt.reward_fail_cost_ratio,
                                 opt.profile_tolerance,
                                 opt.line_x_outlier_factor,
                                 opt.line_x_min_class_size,
                                 opt.line_x_pu_max,
                                 opt.line_xr_max,
                                 opt.max_offenders);
  }
};

}  // namespace daw::json
