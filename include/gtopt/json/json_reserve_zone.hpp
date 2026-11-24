#pragma once

#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/json/json_single_id.hpp>
#include <gtopt/reserve_zone.hpp>

namespace daw::json
{
using gtopt::ReserveContingency;
using gtopt::ReserveRequirementPlane;
using gtopt::ReserveZone;

template<>
struct json_data_contract<ReserveRequirementPlane>
{
  using type = json_member_list<
      json_variant_null<"alpha", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"demand_coef",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"inertia_coef",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"contingency_coef",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"flow_coef",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>>;

  constexpr static auto to_json_data(ReserveRequirementPlane const& plane)
  {
    return std::forward_as_tuple(plane.alpha,
                                 plane.demand_coef,
                                 plane.inertia_coef,
                                 plane.contingency_coef,
                                 plane.flow_coef);
  }
};

template<>
struct json_data_contract<ReserveContingency>
{
  using type = json_member_list<
      json_string_null<"name", OptName>,
      json_array_null<"generators",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_array_null<"generator_coefs",
                      gtopt::Array<Real>,
                      json_number_no_name<Real>>,
      json_array_null<"demands",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_array_null<"demand_coefs",
                      gtopt::Array<Real>,
                      json_number_no_name<Real>>,
      json_array_null<"lines",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_array_null<"line_coefs",
                      gtopt::Array<Real>,
                      json_number_no_name<Real>>,
      json_variant_null<"offset", OptTBRealFieldSched, jvtl_TBRealFieldSched>>;

  constexpr static auto to_json_data(ReserveContingency const& obj)
  {
    return std::forward_as_tuple(obj.name,
                                 obj.generators,
                                 obj.generator_coefs,
                                 obj.demands,
                                 obj.demand_coefs,
                                 obj.lines,
                                 obj.line_coefs,
                                 obj.offset);
  }
};

template<>
struct json_data_contract<ReserveZone>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_string_null<"description", OptName>,
      json_variant_null<"urreq", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"drreq", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"urmin", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"drmin", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"urcost", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"drcost", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_array_null<"urreq_planes",
                      gtopt::Array<ReserveRequirementPlane>,
                      ReserveRequirementPlane>,
      json_array_null<"drreq_planes",
                      gtopt::Array<ReserveRequirementPlane>,
                      ReserveRequirementPlane>,
      json_string_null<"load_term", OptName>,
      json_variant_null<"system_load",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_number_null<"demand_load_factor", OptReal>,
      json_array_null<"demands",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_variant_null<"inertia_zone", OptSingleId, jvtl_SingleId>,
      json_string_null<"inertia_term", OptName>,
      json_variant_null<"system_inertia",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_array_null<"contingency_generators",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_array_null<"contingency_demands",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_array_null<"flow_lines",
                      gtopt::Array<SingleId>,
                      json_variant_no_name<SingleId, jvtl_SingleId>>,
      json_array_null<"urcontingencies",
                      gtopt::Array<ReserveContingency>,
                      ReserveContingency>,
      json_array_null<"drcontingencies",
                      gtopt::Array<ReserveContingency>,
                      ReserveContingency>,
      json_bool_null<"contingency_includes_reserve", OptBool>,
      json_string_null<"requirement_sense", OptName>,
      json_variant_null<"ursurplus_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>,
      json_variant_null<"drsurplus_cost",
                        OptTBRealFieldSched,
                        jvtl_TBRealFieldSched>>;

  constexpr static auto to_json_data(ReserveZone const& obj)
  {
    return std::forward_as_tuple(obj.uid,
                                 obj.name,
                                 obj.active,
                                 obj.type,
                                 obj.description,
                                 obj.urreq,
                                 obj.drreq,
                                 obj.urmin,
                                 obj.drmin,
                                 obj.urcost,
                                 obj.drcost,
                                 obj.urreq_planes,
                                 obj.drreq_planes,
                                 obj.load_term,
                                 obj.system_load,
                                 obj.demand_load_factor,
                                 obj.demands,
                                 obj.inertia_zone,
                                 obj.inertia_term,
                                 obj.system_inertia,
                                 obj.contingency_generators,
                                 obj.contingency_demands,
                                 obj.flow_lines,
                                 obj.urcontingencies,
                                 obj.drcontingencies,
                                 obj.contingency_includes_reserve,
                                 obj.requirement_sense,
                                 obj.ursurplus_cost,
                                 obj.drsurplus_cost);
  }
};

}  // namespace daw::json
