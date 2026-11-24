/**
 * @file      carrier_storage_lp.hpp
 * @brief     Shared LP wrapper for the carrier storages (TES / H₂ / NH₃)
 * @date      Tue Nov 24 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * ``ThermalStorageLP``, ``HydrogenStorageLP`` and ``AmmoniaStorageLP``
 * are one element on three carriers.  Same LP shape as ``BatteryLP``:
 * one ``finp`` (charge) and one ``fout`` (discharge) column per
 * (scenario, stage, block), the ``StorageLP<>`` energy balance, and —
 * when the storage references its carrier node — ``−finp`` / ``+fout``
 * stamped into the node balance row (mirrors a Demand / Generator on a
 * Bus).  Only the data struct, the node type, the node-reference field
 * and the state defaults differ, and they are the template arguments.
 *
 * The member functions are defined in ``carrier_storage_lp.cpp`` and
 * explicitly instantiated for the three storages.
 */

#pragma once

#include <string_view>

#include <gtopt/capacity_object_lp.hpp>
#include <gtopt/storage_lp.hpp>
#include <gtopt/storage_state_defaults.hpp>

namespace gtopt
{

/// @tparam Storage   data struct (``ThermalStorage``, …)
/// @tparam NodeLP    carrier node LP (``ThermalNodeLP``, …)
/// @tparam NodeRef   pointer to the ``OptSingleId`` node field
/// @tparam Defaults  class defaults of use_state_variable / daily_cycle
template<typename Storage,
         typename NodeLP,
         auto NodeRef,
         const StorageStateDefaults& Defaults>
class CarrierStorageLP : public StorageLP<CapacityObjectLP<Storage>>
{
public:
  /// LP column name for the **charging** flow (node → storage).
  static constexpr std::string_view FinpName {"finp"};
  /// LP column name for the **discharging** flow (storage → node).
  static constexpr std::string_view FoutName {"fout"};
  /// PAMPL alias resolving to ``finp_cols``.
  static constexpr std::string_view ChargeName {"charge"};
  /// PAMPL alias resolving to ``fout_cols``.
  static constexpr std::string_view DischargeName {"discharge"};
  /// Filter metadata key published by ``add_to_lp`` for ``sum(...)``
  /// predicate matching (mirrors ``BatteryLP::TypeKey``).
  static constexpr std::string_view TypeKey {"type"};

  using CapacityBase = CapacityObjectLP<Storage>;
  using StorageBase = StorageLP<CapacityObjectLP<Storage>>;
  using typename StorageBase::Element;

  /// Constructs from the data struct + input context.
  explicit CarrierStorageLP(const Storage& pstorage, const InputContext& ic);

  /// Adds the storage variables, energy balance and node coupling.
  bool add_to_lp(SystemContext& sc,
                 const ScenarioLP& scenario,
                 const StageLP& stage,
                 LinearProblem& lp);

  /// Emits primal / dual / cost streams to the output context.
  bool add_to_output(OutputContext& out) const;

  /// Charge-flow columns for (scenario, stage), keyed by block uid.
  /// Empty when the storage is out of service in that stage.
  [[nodiscard]] const auto& finp_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return find_or_empty_inner(finp_cols, scenario, stage);
  }

  /// Discharge-flow columns for (scenario, stage), keyed by block uid.
  /// Empty when the storage is out of service in that stage.
  [[nodiscard]] const auto& fout_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return find_or_empty_inner(fout_cols, scenario, stage);
  }

  /// @name Parameter accessors for user constraint resolution
  /// @{
  [[nodiscard]] auto param_input_efficiency(StageUid s, BlockUid b) const
  {
    return input_efficiency.at(s, b);
  }
  [[nodiscard]] auto param_output_efficiency(StageUid s, BlockUid b) const
  {
    return output_efficiency.at(s, b);
  }
  /// @}

private:
  OptTBRealSched input_efficiency;
  OptTBRealSched output_efficiency;

  STBIndexHolder<ColIndex> finp_cols;
  STBIndexHolder<ColIndex> fout_cols;
};

}  // namespace gtopt
