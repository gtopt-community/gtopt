/**
 * @file      json_commitment.hpp
 * @brief     JSON serialization for Commitment
 * @date      Tue Apr  8 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <gtopt/commitment.hpp>
#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/json/json_single_id.hpp>

namespace daw::json
{
using gtopt::Commitment;
using gtopt::OptStartsScope;

// Two-shape lookup for ``Commitment.starts_scope``: either an
// integer hour count (variant alternative 0 — gtopt::Int) or a named
// scope string ("week", "day", ...) (variant alternative 1 —
// gtopt::Name).  daw::json picks the alternative matching the JSON
// token type (number → Int, string → Name).
using jvtl_StartsScope = json_variant_type_list<Int, Name>;
// ``Commitment.commitment_period``: hours (number) or "auto" (string).
using jvtl_CommitmentPeriod = json_variant_type_list<gtopt::Real, Name>;

// Removed key `transition_status_rows` (bool or name): still parsed so
// old inputs load, ignored (see transition_integrality.hpp).
using jvtl_BoolOrName =
    json_variant_type_list<json_bool_no_name<bool>,
                           json_string_no_name<gtopt::Name>>;

template<>
struct json_data_contract<Commitment>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_string_null<"description", OptName>,
      json_variant<"generator", SingleId>,
      json_variant_null<"startup_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"shutdown_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_number_null<"noload_cost", OptReal>,
      json_number_null<"noload_fuel", OptReal>,
      json_variant_null<"pmin", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_number_null<"min_up_time", OptReal>,
      json_number_null<"min_down_time", OptReal>,
      json_variant_null<"ramp_up", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"ramp_down",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"startup_ramp",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"shutdown_ramp",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_number_null<"initial_status", OptReal>,
      json_number_null<"initial_hours", OptReal>,
      json_number_null<"ini_hours_up", OptReal>,
      json_number_null<"ini_hours_down", OptReal>,
      json_number_null<"initial_power", OptReal>,
      json_bool_null<"relax", OptBool>,
      json_bool_null<"must_run", OptBool>,
      json_bool_null<"fast_start", OptBool>,
      json_string_null<"transition_integrality", OptName>,
      json_variant_null<"transition_status_rows",
                        gtopt::OptBoolOrName,
                        jvtl_BoolOrName>,
      json_string_null<"startup_tier_integrality", OptName>,
      json_variant_null<"fixed_status",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"commitment_period",
                        gtopt::OptCommitmentPeriod,
                        jvtl_CommitmentPeriod>,
      json_string_null<"commitment_grid", OptName>,
      json_string_null<"ramp_allowance", OptName>,
      json_array_null<"commitment_breaks",
                      std::vector<gtopt::Real>,
                      json_number_no_name<gtopt::Real>>,
      json_string_null<"cycle", OptName>,
      json_string_null<"long_transition", OptName>,
      json_array_null<"long_transition_schedule",
                      std::vector<Name>,
                      std::string>,
      json_number_null<"hot_start_cost", OptReal>,
      json_number_null<"warm_start_cost", OptReal>,
      json_number_null<"cold_start_cost", OptReal>,
      json_number_null<"hot_start_time", OptReal>,
      json_number_null<"cold_start_time", OptReal>,
      json_number_null<"max_starts", OptInt>,
      json_number_null<"min_starts", OptInt>,
      json_variant_null<"starts_scope", OptStartsScope, jvtl_StartsScope>,
      json_string_null<"uc_formulation", OptName>,
      json_array_null<"startup_trajectory",
                      std::vector<gtopt::Real>,
                      json_number_no_name<gtopt::Real>>,
      json_array_null<"shutdown_trajectory",
                      std::vector<gtopt::Real>,
                      json_number_no_name<gtopt::Real>>,
      json_number_null<"trajectory_step", OptReal>,
      json_variant_null<"startup_wear_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"wear_hour_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"wear_load_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>>;

  constexpr static auto to_json_data(Commitment const& obj)
  {
    return std::forward_as_tuple(obj.uid,
                                 obj.name,
                                 obj.active,
                                 obj.type,
                                 obj.description,
                                 obj.generator,
                                 obj.startup_cost,
                                 obj.shutdown_cost,
                                 obj.noload_cost,
                                 obj.noload_fuel,
                                 obj.pmin,
                                 obj.min_up_time,
                                 obj.min_down_time,
                                 obj.ramp_up,
                                 obj.ramp_down,
                                 obj.startup_ramp,
                                 obj.shutdown_ramp,
                                 obj.initial_status,
                                 obj.initial_hours,
                                 obj.ini_hours_up,
                                 obj.ini_hours_down,
                                 obj.initial_power,
                                 obj.relax,
                                 obj.must_run,
                                 obj.fast_start,
                                 obj.transition_integrality,
                                 obj.transition_status_rows,
                                 obj.startup_tier_integrality,
                                 obj.fixed_status,
                                 obj.commitment_period,
                                 obj.commitment_grid,
                                 obj.ramp_allowance,
                                 obj.commitment_breaks,
                                 obj.cycle,
                                 obj.long_transition,
                                 obj.long_transition_schedule,
                                 obj.hot_start_cost,
                                 obj.warm_start_cost,
                                 obj.cold_start_cost,
                                 obj.hot_start_time,
                                 obj.cold_start_time,
                                 obj.max_starts,
                                 obj.min_starts,
                                 obj.starts_scope,
                                 obj.uc_formulation,
                                 obj.startup_trajectory,
                                 obj.shutdown_trajectory,
                                 obj.trajectory_step,
                                 obj.startup_wear_cost,
                                 obj.wear_hour_cost,
                                 obj.wear_load_cost);
  }
};

}  // namespace daw::json
