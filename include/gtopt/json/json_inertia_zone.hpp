/**
 * @file      json_inertia_zone.hpp
 * @brief     JSON serialization for InertiaZone
 * @date      2026-04-13
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <gtopt/inertia_zone.hpp>
#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/json/json_single_id.hpp>

namespace daw::json
{
using gtopt::InertiaRequirementCredit;
using gtopt::InertiaZone;

template<>
struct json_data_contract<InertiaRequirementCredit>
{
  using type = json_member_list<
      json_string_null<"name", OptName>,
      json_array_null<"generators",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_number_null<"units", OptReal>,
      json_variant_null<"credit", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_bool_null<"integer", OptBool>>;

  constexpr static auto to_json_data(InertiaRequirementCredit const& obj)
  {
    return std::forward_as_tuple(
        obj.name, obj.generators, obj.units, obj.credit, obj.integer);
  }
};

template<>
struct json_data_contract<InertiaZone>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_variant_null<"requirement",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"cost", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_array_null<"requirement_credits",
                      gtopt::Array<InertiaRequirementCredit>,
                      InertiaRequirementCredit>>;

  constexpr static auto to_json_data(InertiaZone const& obj)
  {
    return std::forward_as_tuple(obj.uid,
                                 obj.name,
                                 obj.active,
                                 obj.requirement,
                                 obj.cost,
                                 obj.requirement_credits);
  }
};

}  // namespace daw::json
