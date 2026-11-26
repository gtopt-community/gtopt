/**
 * @file      json_model_options.hpp
 * @brief     JSON serialization for ModelOptions
 * @date      2026-03-24
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/json/json_fail_segment.hpp>
#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/model_options.hpp>

namespace daw::json
{
using gtopt::ModelOptions;

template<>
struct json_data_contract<ModelOptions>
{
  using type = json_member_list<
      json_bool_null<"use_single_bus", OptBool>,
      json_bool_null<"use_kirchhoff", OptBool>,
      json_string_null<"kirchhoff_mode", OptName>,
      json_bool_null<"use_line_losses", OptBool>,
      json_string_null<"line_losses_mode", OptName>,
      json_number_null<"kirchhoff_threshold", OptReal>,
      json_number_null<"dc_line_reactance_threshold", OptReal>,
      json_string_null<"tiny_reactance_mode", OptName>,
      json_number_null<"dc_line_resistance_threshold", OptReal>,
      json_number_null<"loss_segments", OptInt>,
      json_number_null<"scale_objective", OptReal>,
      json_number_null<"scale_theta", OptReal>,
      json_number_null<"scale_loss_link", OptReal>,
      json_number_null<"loss_cost_eps", OptReal>,
      json_number_null<"loss_flow_abs_cost", OptReal>,
      json_number_null<"loss_overflow_cost", OptReal>,
      json_number_null<"loss_secant_segments", OptInt>,
      json_bool_null<"loss_use_sos2", OptBool>,
      json_number_null<"theta_max", OptReal>,
      json_bool_null<"auto_scale", OptBool>,
      json_number_null<"demand_fail_cost", OptReal>,
      json_array_null<"demand_fail_segments",
                      std::optional<std::vector<gtopt::ScalarFailSegment>>,
                      gtopt::ScalarFailSegment>,
      json_number_null<"reserve_shortage_cost", OptReal>,
      json_number_null<"hydro_spill_cost", OptReal>,
      json_number_null<"hydro_use_value", OptReal>,
      json_number_null<"state_violation_cost", OptReal>,
      json_bool_null<"demand_fail_rhs_shift", OptBool>,
      json_string_null<"continuous_phases", OptName>,
      json_string_null<"naming_dialect", OptName>,
      json_string_null<"objective_mode", OptName>,
      json_bool_null<"strict_storage_emin", OptBool>,
      json_bool_null<"lp_reduction", OptBool>,
      json_bool_null<"allow_oversupply", OptBool>,
      json_number_null<"battery_annual_standing_loss", OptReal>,
      json_bool_null<"battery_power_envelope", OptBool>,
      json_string_null<"reserve_coupling", OptName>,
      json_string_null<"pricing_mode", OptName>,
      json_number_null<"fast_start_max_hours", OptReal>,
      json_string_null<"transition_integrality", OptName>,
      json_bool_null<"transition_status_rows", OptBool>,
      json_string_null<"startup_tier_integrality", OptName>,
      json_string_null<"line_thermal_limit_mode", OptName>,
      json_string_null<"prev_stage_link", OptName>,
      json_bool_null<"use_line_dlr", OptBool>,
      json_string_null<"dlr_limit_mode", OptName>,
      json_string_null<"overload_slack_bound", OptName>,
      json_number_null<"max_overload_ratio", OptReal>>;

  constexpr static auto to_json_data(ModelOptions const& opt)
  {
    return std::forward_as_tuple(opt.use_single_bus,
                                 opt.use_kirchhoff,
                                 opt.kirchhoff_mode,
                                 opt.use_line_losses,
                                 opt.line_losses_mode,
                                 opt.kirchhoff_threshold,
                                 opt.dc_line_reactance_threshold,
                                 opt.tiny_reactance_mode,
                                 opt.dc_line_resistance_threshold,
                                 opt.loss_segments,
                                 opt.scale_objective,
                                 opt.scale_theta,
                                 opt.scale_loss_link,
                                 opt.loss_cost_eps,
                                 opt.loss_flow_abs_cost,
                                 opt.loss_overflow_cost,
                                 opt.loss_secant_segments,
                                 opt.loss_use_sos2,
                                 opt.theta_max,
                                 opt.auto_scale,
                                 opt.demand_fail_cost,
                                 opt.demand_fail_segments,
                                 opt.reserve_shortage_cost,
                                 opt.hydro_spill_cost,
                                 opt.hydro_use_value,
                                 opt.state_violation_cost,
                                 opt.demand_fail_rhs_shift,
                                 opt.continuous_phases,
                                 opt.naming_dialect,
                                 opt.objective_mode,
                                 opt.strict_storage_emin,
                                 opt.lp_reduction,
                                 opt.allow_oversupply,
                                 opt.battery_annual_standing_loss,
                                 opt.battery_power_envelope,
                                 opt.reserve_coupling,
                                 opt.pricing_mode,
                                 opt.fast_start_max_hours,
                                 opt.transition_integrality,
                                 opt.transition_status_rows,
                                 opt.startup_tier_integrality,
                                 opt.line_thermal_limit_mode,
                                 opt.prev_stage_link,
                                 opt.use_line_dlr,
                                 opt.dlr_limit_mode,
                                 opt.overload_slack_bound,
                                 opt.max_overload_ratio);
  }
};

}  // namespace daw::json
