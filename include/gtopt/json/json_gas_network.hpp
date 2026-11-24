/**
 * @file      json_gas_network.hpp
 * @brief     JSON serialisation for the gas-network elements
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * ``GasNode``, ``GasSupply``, ``GasDemand``, ``GasPipe``,
 * ``GasCompressor`` and ``GasTurbine`` (``gas_*_array`` in ``system``).
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/gas_compressor.hpp>
#include <gtopt/gas_demand.hpp>
#include <gtopt/gas_node.hpp>
#include <gtopt/gas_pipe.hpp>
#include <gtopt/gas_supply.hpp>
#include <gtopt/gas_turbine.hpp>
#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/json/json_field_sched.hpp>
#include <gtopt/json/json_single_id.hpp>

namespace daw::json
{
using gtopt::GasCompressor;
using gtopt::GasDemand;
using gtopt::GasNode;
using gtopt::GasPipe;
using gtopt::GasSupply;
using gtopt::GasTurbine;

template<>
struct json_data_contract<GasNode>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_variant_null<"pmin", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"pmax", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_variant_null<"pressure_cost",
                        OptTRealFieldSched,
                        jvtl_TRealFieldSched>,
      json_number_null<"p_init", OptReal>,
      json_number_null<"elevation", OptReal>>;

  [[nodiscard]] constexpr static auto to_json_data(GasNode const& o) noexcept
  {
    return std::forward_as_tuple(o.uid,
                                 o.name,
                                 o.active,
                                 o.type,
                                 o.pmin,
                                 o.pmax,
                                 o.pressure_cost,
                                 o.p_init,
                                 o.elevation);
  }
};

template<>
struct json_data_contract<GasSupply>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_variant<"gas_node", SingleId>,
      json_variant_null<"fmin", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"fmax", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"cost", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_number_null<"volume", OptReal>>;

  [[nodiscard]] constexpr static auto to_json_data(GasSupply const& o) noexcept
  {
    return std::forward_as_tuple(o.uid,
                                 o.name,
                                 o.active,
                                 o.type,
                                 o.gas_node,
                                 o.fmin,
                                 o.fmax,
                                 o.cost,
                                 o.volume);
  }
};

template<>
struct json_data_contract<GasDemand>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_variant<"gas_node", SingleId>,
      json_variant_null<"load", OptTBRealFieldSched, jvtl_TBRealFieldSched>,
      json_variant_null<"fail_cost", OptTRealFieldSched, jvtl_TRealFieldSched>>;

  [[nodiscard]] constexpr static auto to_json_data(GasDemand const& o) noexcept
  {
    return std::forward_as_tuple(
        o.uid, o.name, o.active, o.type, o.gas_node, o.load, o.fail_cost);
  }
};

template<>
struct json_data_contract<GasPipe>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_variant<"gas_node_a", SingleId>,
      json_variant<"gas_node_b", SingleId>,
      json_number_null<"weymouth_k", OptReal>,
      json_number_null<"length", OptReal>,
      json_number_null<"diameter", OptReal>,
      json_number_null<"efficiency", OptReal>,
      json_number_null<"gravity", OptReal>,
      json_number_null<"temperature", OptReal>,
      json_number_null<"z", OptReal>,
      json_number_null<"elevation_exponent", OptReal>,
      json_variant_null<"fmax", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_number_null<"segments", OptInt>,
      json_string_null<"tangent_layout", OptName>,
      json_string_null<"weymouth_mode", OptName>,
      json_string_null<"linearization", OptName>,
      json_number_null<"reference_flow", OptReal>,
      json_bool_null<"linepack", OptBool>,
      json_number_null<"linepack_coeff", OptReal>,
      json_number_null<"linepack_pressure_ref", OptReal>,
      json_number_null<"linepack_ini", OptReal>>;

  [[nodiscard]] constexpr static auto to_json_data(GasPipe const& o) noexcept
  {
    return std::forward_as_tuple(o.uid,
                                 o.name,
                                 o.active,
                                 o.type,
                                 o.gas_node_a,
                                 o.gas_node_b,
                                 o.weymouth_k,
                                 o.length,
                                 o.diameter,
                                 o.efficiency,
                                 o.gravity,
                                 o.temperature,
                                 o.z,
                                 o.elevation_exponent,
                                 o.fmax,
                                 o.segments,
                                 o.tangent_layout,
                                 o.weymouth_mode,
                                 o.linearization,
                                 o.reference_flow,
                                 o.linepack,
                                 o.linepack_coeff,
                                 o.linepack_pressure_ref,
                                 o.linepack_ini);
  }
};

template<>
struct json_data_contract<GasCompressor>
{
  using type = json_member_list<
      json_number<"uid", Uid>,
      json_string<"name", Name>,
      json_variant_null<"active", OptActive, jvtl_Active>,
      json_string_null<"type", OptName>,
      json_variant<"gas_node_in", SingleId>,
      json_variant<"gas_node_out", SingleId>,
      json_variant_null<"fmax", OptTRealFieldSched, jvtl_TRealFieldSched>,
      json_number_null<"max_ratio", OptReal>,
      json_string_null<"fuel_model", OptName>,
      json_number_null<"fuel_fraction", OptReal>,
      json_number_null<"gamma", OptReal>,
      json_number_null<"alpha", OptReal>,
      json_number_null<"efficiency", OptReal>,
      json_number_null<"driver_efficiency", OptReal>,
      json_number_null<"op_flow", OptReal>,
      json_array_null<"op_ratio", Array<Real>, json_number_no_name<Real>>,
      json_number_null<"op_suction_pressure", OptReal>,
      json_bool_null<"allow_bypass", OptBool>,
      json_bool_null<"bidirectional", OptBool>,
      json_string_null<"drive", OptName>,
      json_variant_null<"bus", OptSingleId, jvtl_SingleId>,
      json_number_null<"motor_efficiency", OptReal>,
      json_number_null<"power_max", OptReal>>;

  [[nodiscard]] constexpr static auto to_json_data(
      GasCompressor const& o) noexcept
  {
    return std::forward_as_tuple(o.uid,
                                 o.name,
                                 o.active,
                                 o.type,
                                 o.gas_node_in,
                                 o.gas_node_out,
                                 o.fmax,
                                 o.max_ratio,
                                 o.fuel_model,
                                 o.fuel_fraction,
                                 o.gamma,
                                 o.alpha,
                                 o.efficiency,
                                 o.driver_efficiency,
                                 o.op_flow,
                                 o.op_ratio,
                                 o.op_suction_pressure,
                                 o.allow_bypass,
                                 o.bidirectional,
                                 o.drive,
                                 o.bus,
                                 o.motor_efficiency,
                                 o.power_max);
  }
};

template<>
struct json_data_contract<GasTurbine>
{
  using type =
      json_member_list<json_number<"uid", Uid>,
                       json_string<"name", Name>,
                       json_variant_null<"active", OptActive, jvtl_Active>,
                       json_string_null<"type", OptName>,
                       json_variant<"generator", SingleId>,
                       json_variant<"gas_node", SingleId>,
                       json_variant_null<"heat_rate",
                                         OptTBRealFieldSched,
                                         jvtl_TBRealFieldSched>,
                       json_number_null<"min_pressure", OptReal>,
                       json_number_null<"pressure_band", OptReal>,
                       json_number_null<"nominal_pressure", OptReal>,
                       json_number_null<"derate_min_output", OptReal>>;

  [[nodiscard]] constexpr static auto to_json_data(GasTurbine const& o) noexcept
  {
    return std::forward_as_tuple(o.uid,
                                 o.name,
                                 o.active,
                                 o.type,
                                 o.generator,
                                 o.gas_node,
                                 o.heat_rate,
                                 o.min_pressure,
                                 o.pressure_band,
                                 o.nominal_pressure,
                                 o.derate_min_output);
  }
};

}  // namespace daw::json
