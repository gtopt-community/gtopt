/**
 * @file      carrier_node_lp.hpp
 * @brief     Shared LP wrapper for the carrier balance nodes
 * @date      Tue Nov 24 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * ``ThermalNodeLP``, ``HydrogenNodeLP`` and ``AmmoniaNodeLP`` are the
 * same element on different carriers: one balance row per (scenario,
 * stage, block) into which storages, demands and converters stamp their
 * flows.  Sign convention (mirrors ``BusLP``):
 *   * +1 × (injection into the node)
 *   * −1 × (withdrawal from the node)
 *
 * The member functions are defined in ``carrier_node_lp.cpp`` and
 * explicitly instantiated for the three node types.
 *
 * @see junction_lp.hpp       water-carrier analog (with optional drain)
 */

#pragma once

#include <string_view>

#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/stage_lp.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

template<typename Node>
class CarrierNodeLP : public ObjectLP<Node>
{
public:
  using Base = ObjectLP<Node>;
  using typename Base::Element;

  static constexpr std::string_view BalanceName {"balance"};

  explicit CarrierNodeLP(const Node& pnode, const InputContext& ic)
      : Base(pnode, ic, Element::class_name)
  {
  }

  /// One balance row per block of an active (scenario, stage).
  bool add_to_lp(const SystemContext& sc,
                 const ScenarioLP& scenario,
                 const StageLP& stage,
                 LinearProblem& lp);

  bool add_to_output(OutputContext& out) const;

  [[nodiscard]] const auto& balance_rows_at(const ScenarioLP& scenario,
                                            const StageLP& stage) const
  {
    return balance_rows.at({scenario.uid(), stage.uid()});
  }

  /// Non-throwing `balance_rows_at`: nullptr when (scenario, stage) was
  /// not built (node inactive in the stage).  See `active_ref.hpp`.
  [[nodiscard]] const BIndexHolder<RowIndex>* find_balance_rows(
      const ScenarioLP& scenario, const StageLP& stage) const noexcept
  {
    const auto it = balance_rows.find({scenario.uid(), stage.uid()});
    return it != balance_rows.end() ? &it->second : nullptr;
  }

private:
  STBIndexHolder<RowIndex> balance_rows;
};

}  // namespace gtopt
