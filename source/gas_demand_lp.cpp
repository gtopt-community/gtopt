/**
 * @file      gas_demand_lp.cpp
 * @brief     Implementation of GasDemandLP
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>

#include <gtopt/ampl_variable.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/gas_demand_lp.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

GasDemandLP::GasDemandLP(const GasDemand& demand, const InputContext& ic)
    : ObjectLP<GasDemand>(demand, ic, Element::class_name)
    , load_(ic, Element::class_name, id(), std::move(object().load))
    , fail_cost_(ic, Element::class_name, id(), std::move(object().fail_cost))
{
}

bool GasDemandLP::add_to_lp(SystemContext& sc,
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
      sc.element<GasNodeLP>(GasNodeLPSId {gas_demand().gas_node});
  if (!node.is_active(stage)) {
    return true;
  }
  const auto& brows = node.balance_rows_at(scenario, stage);
  const auto& blocks = stage.blocks();
  const auto stage_uid = stage.uid();
  const auto scen_uid = scenario.uid();
  const auto fail_cost = fail_cost_.at(stage_uid);

  BIndexHolder<ColIndex> cols;
  BIndexHolder<double> loads;
  map_reserve(loads, blocks.size());
  // PAMPL `gas_demand.served = load − fail`: the scheduled load rides as
  // the per-block offset, the fail column (when built) as a −1 leg.
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> served_terms;
  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const double load = std::max(0.0, load_.at(stage_uid, buid).value_or(0.0));
    loads[buid] = load;
    auto& brow = lp.row_at(brows.at(buid));
    // The balance row is an equality: shift both bounds by the load.
    brow.lowb += load;
    brow.uppb += load;
    if (fail_cost) {
      const auto col = lp.add_col(SparseCol {
          .lowb = 0.0,
          .uppb = load,
          .cost = CostHelper::block_ecost(scenario, stage, block, *fail_cost),
          .class_name = cname,
          .variable_name = FailName,
          .variable_uid = uid(),
          .context = make_block_context(scen_uid, stage_uid, buid),
      });
      cols[buid] = col;
      brow[col] = 1.0;
      served_terms[buid] = {{col, -1.0}};
    }
  }

  const auto st_key = std::tuple {scen_uid, stage_uid};
  sc.add_ampl_variable(
      ampl_name, uid(), ServedName, scenario, stage, served_terms, loads);
  {
    AmplElementMetadata metadata;
    metadata.reserve(2);
    if (const auto& t = gas_demand().type) {
      metadata.emplace_back(GasNodeLP::TypeKey, *t);
    }
    metadata.emplace_back(GasNodeLP::GasNodeKey, ampl_metadata_uid(node.uid()));
    sc.register_ampl_element_metadata(ampl_name, uid(), std::move(metadata));
  }
  load_values_[st_key] = std::move(loads);
  if (!cols.empty()) {
    fail_cols_[st_key] = std::move(cols);
    sc.add_ampl_variable(
        ampl_name, uid(), FailName, scenario, stage, fail_cols_.at(st_key));
  }
  return true;
}

bool GasDemandLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  out.add_col_sol_values(cname, LoadName, id(), load_values_);
  if (!fail_cols_.empty()) {
    out.add_col_sol(cname, FailName, id(), fail_cols_);
    out.add_col_cost(cname, FailName, id(), fail_cols_);
  }
  return true;
}

}  // namespace gtopt
