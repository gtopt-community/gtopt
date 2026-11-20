/**
 * @file      json_hydrogen_demand.hpp
 * @brief     JSON serialisation for ``HydrogenDemand``
 * @date      Fri Nov 20 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/hydrogen_demand.hpp>
#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/json/json_single_id.hpp>

namespace daw::json
{
using gtopt::HydrogenDemand;

template<>
struct json_data_contract<HydrogenDemand>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_variant_null<"hydrogen_node", OptSingleId, jvtl_SingleId>,
      json_variant_null<"lmax", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"lmin", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"min_energy", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"max_energy", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"min_energy_cost",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_variant_null<"price", OptTBRealFieldSched, jvtl_TBRealFieldSched>>;

  [[nodiscard]] constexpr static auto to_json_data(
      HydrogenDemand const& hd) noexcept
  {
    return std::forward_as_tuple(hd.uid,
                                 hd.name,
                                 hd.active,
                                 hd.type,
                                 hd.hydrogen_node,
                                 hd.lmax,
                                 hd.lmin,
                                 hd.min_energy,
                                 hd.max_energy,
                                 hd.min_energy_cost,
                                 hd.price);
  }
};
}  // namespace daw::json
