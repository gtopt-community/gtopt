/**
 * @file      json_gas_options.hpp
 * @brief     JSON serialization for GasOptions
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/gas_options.hpp>
#include <gtopt/json/json_basic_types.hpp>

namespace daw::json
{
using gtopt::GasOptions;

template<>
struct json_data_contract<GasOptions>
{
  using type =
      json_member_list<json_number_null<"gas_specific_gravity", OptReal>,
                       json_number_null<"gas_temperature", OptReal>,
                       json_number_null<"compressibility", OptReal>,
                       json_number_null<"slp_iterations", OptInt>,
                       json_number_null<"slp_tolerance", OptReal>,
                       json_number_null<"slp_damping", OptReal>,
                       json_number_null<"slp_min_flow", OptReal>,
                       json_number_null<"slp_initial_flow", OptReal>,
                       json_number_null<"slp_proximal_cost", OptReal>,
                       json_bool_null<"linepack_cyclic", OptBool>>;

  constexpr static auto to_json_data(GasOptions const& opt)
  {
    return std::forward_as_tuple(opt.gas_specific_gravity,
                                 opt.gas_temperature,
                                 opt.compressibility,
                                 opt.slp_iterations,
                                 opt.slp_tolerance,
                                 opt.slp_damping,
                                 opt.slp_min_flow,
                                 opt.slp_initial_flow,
                                 opt.slp_proximal_cost,
                                 opt.linepack_cyclic);
  }
};

}  // namespace daw::json
