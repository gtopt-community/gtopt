/**
 * @file      json_ammonia_node.hpp
 * @brief     JSON serialisation for ``AmmoniaNode``
 * @date      Fri May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Mirror of ``json_thermal_node.hpp`` for the ammonia carrier.
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/ammonia_node.hpp>
#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/json/json_field_sched.hpp>

namespace daw::json
{
using gtopt::AmmoniaNode;

template<>
struct json_data_contract<AmmoniaNode>
{
  using type =
      json_member_list<json_number<"uid", Uid>,
                       json_string<"name", Name>,
                       json_variant_null<"active", OptActive, jvtl_Active>>;

  [[nodiscard]] constexpr static auto to_json_data(
      AmmoniaNode const& an) noexcept
  {
    return std::forward_as_tuple(an.uid, an.name, an.active);
  }
};
}  // namespace daw::json
