/**
 * @file      gas_supply_lp.cpp
 * @brief     Implementation of GasSupplyLP
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>

#include <gtopt/ampl_variable.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_supply_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

GasSupplyLP::GasSupplyLP(const GasSupply& supply, const InputContext& ic)
    : ObjectLP<GasSupply>(supply, ic, Element::class_name)
    , fmin_(ic, Element::class_name, id(), std::move(object().fmin))
    , fmax_(ic, Element::class_name, id(), std::move(object().fmax))
    , cost_(ic, Element::class_name, id(), std::move(object().cost))
{
}

bool GasSupplyLP::add_to_lp(SystemContext& sc,
                            const ScenarioLP& scenario,
                            const StageLP& stage,
                            LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }
  const auto& node =
      sc.element<GasNodeLP>(GasNodeLPSId {gas_supply().gas_node});
  if (!node.is_active(stage)) {
    return true;
  }
  const auto& brows = node.balance_rows_at(scenario, stage);
  const auto& blocks = stage.blocks();
  const auto stage_uid = stage.uid();
  const auto scen_uid = scenario.uid();
  const double cost = cost_.at(stage_uid).value_or(0.0);

  BIndexHolder<ColIndex> cols;
  map_reserve(cols, blocks.size());
  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const double lo = std::max(0.0, fmin_.at(stage_uid, buid).value_or(0.0));
    const double hi = fmax_.at(stage_uid, buid).value_or(LinearProblem::DblMax);
    const auto col = lp.add_col(SparseCol {
        .lowb = lo,
        .uppb = std::max(lo, hi),
        .cost = CostHelper::block_ecost(scenario, stage, block, cost),
        .class_name = cname,
        .variable_name = FlowName,
        .variable_uid = uid(),
        .context = make_block_context(scen_uid, stage_uid, buid),
    });
    cols[buid] = col;
    lp.row_at(brows.at(buid))[col] = 1.0;
  }

  if (const auto& vol = gas_supply().volume) {
    // volume: Σ_b Δt_b f_b ≤ volume over the stage.
    // Stage-volume (stock) row: its dual is a $/volume-unit commodity
    // price, read back without the block-duration term (Energy).
    SparseRow row {
        .cost_scale_type = ConstraintScaleType::Energy,
        .class_name = cname,
        .constraint_name = VolumeName,
        .variable_uid = uid(),
        .context = make_stage_context(scen_uid, stage_uid),
    };
    for (const auto& block : blocks) {
      row[cols.at(block.uid())] = block.duration();
    }
    volume_rows_[{scen_uid, stage_uid}] =
        lp.add_row(std::move(row).less_equal(*vol));
  }

  const auto st_key = std::tuple {scen_uid, stage_uid};
  flow_cols_[st_key] = std::move(cols);
  sc.add_ampl_variable(
      ampl_name, uid(), FlowName, scenario, stage, flow_cols_.at(st_key));
  {
    AmplElementMetadata metadata;
    metadata.reserve(2);
    if (const auto& t = gas_supply().type) {
      metadata.emplace_back(GasNodeLP::TypeKey, *t);
    }
    metadata.emplace_back(GasNodeLP::GasNodeKey,
                          static_cast<double>(node.uid()));
    sc.register_ampl_element_metadata(ampl_name, uid(), std::move(metadata));
  }
  return true;
}

bool GasSupplyLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  out.add_col_sol(cname, FlowName, id(), flow_cols_);
  out.add_col_cost(cname, FlowName, id(), flow_cols_);
  out.add_row_dual(cname, VolumeName, id(), volume_rows_);
  return true;
}

}  // namespace gtopt
