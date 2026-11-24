/**
 * @file      carrier_node_lp.cpp
 * @brief     CarrierNodeLP balance rows (thermal / hydrogen / ammonia)
 * @date      Tue Nov 24 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <gtopt/ammonia_node_lp.hpp>
#include <gtopt/carrier_node_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/thermal_node_lp.hpp>

namespace gtopt
{

template<typename Node>
bool CarrierNodeLP<Node>::add_to_lp([[maybe_unused]] const SystemContext& sc,
                                    const ScenarioLP& scenario,
                                    const StageLP& stage,
                                    LinearProblem& lp)
{
  if (!this->is_active(stage)) {
    return true;
  }

  const auto& blocks = stage.blocks();
  if (blocks.empty()) {
    return true;  // nothing to build (not a failure)
  }

  BIndexHolder<RowIndex> brows;
  map_reserve(brows, blocks.size());

  for (auto&& block : blocks) {
    const auto buid = block.uid();
    auto brow = SparseRow {
        .class_name = Element::class_name.full_name(),
        .constraint_name = BalanceName,
        .variable_uid = this->uid(),
        .context = make_block_context(scenario.uid(), stage.uid(), block.uid()),
    };
    brows[buid] = lp.add_row(std::move(brow));
  }

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  balance_rows[st_key] = std::move(brows);

  return true;
}

template<typename Node>
bool CarrierNodeLP<Node>::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  out.add_row_dual(cname, BalanceName, this->id(), balance_rows);
  return true;
}

template class CarrierNodeLP<ThermalNode>;
template class CarrierNodeLP<HydrogenNode>;
template class CarrierNodeLP<AmmoniaNode>;

}  // namespace gtopt
