#pragma once

#include <gtopt/converter.hpp>
#include <gtopt/json/json_demand.hpp>
#include <gtopt/json/json_generator.hpp>

namespace daw::json
{
using gtopt::Converter;

template<>
struct json_data_contract<Converter>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_variant<"battery", SingleId>,
      json_variant<"generator", SingleId>,
      json_variant<"demand", SingleId>,
      json_variant_null<"conversion_rate",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,

      json_variant_null<"capacity", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"expcap", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"expmod", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"capmax", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"annual_capcost",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_variant_null<"annual_derating",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_bool_null<"integer_expmod", OptBool>,
      json_variant_null<"overnight_capcost",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_variant_null<"economic_life",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_variant_null<"wacc", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"fixed_om_cost",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_bool_null<"commitment", OptBool>,  ///< Gate inner gen/demand
                                              ///< floors with per-block
                                              ///< integer u (MIP)
      json_bool_null<"exclusive_charge_discharge",
                     OptBool>,  ///< u_charge + u_discharge ≤ 1
      json_number_null<"energy_power_ratio", OptReal>>;

  constexpr static auto to_json_data(Converter const& converter)
  {
    return std::forward_as_tuple(converter.uid,
                                 converter.name,
                                 converter.active,
                                 converter.battery,
                                 converter.generator,
                                 converter.demand,
                                 converter.conversion_rate,
                                 converter.capacity,
                                 converter.expcap,
                                 converter.expmod,
                                 converter.capmax,
                                 converter.annual_capcost,
                                 converter.annual_derating,
                                 converter.integer_expmod,
                                 converter.overnight_capcost,
                                 converter.economic_life,
                                 converter.wacc,
                                 converter.fixed_om_cost,
                                 converter.commitment,
                                 converter.exclusive_charge_discharge,
                                 converter.energy_power_ratio);
  }
};
}  // namespace daw::json
