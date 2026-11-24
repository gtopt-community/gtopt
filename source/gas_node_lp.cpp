/**
 * @file      gas_node_lp.cpp
 * @brief     Implementation of GasNodeLP (π column + gas balance row)
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <cmath>

#include <gtopt/cost_helper.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

GasNodeLP::GasNodeLP(const GasNode& node, const InputContext& ic)
    : ObjectLP<GasNode>(node, ic, Element::class_name)
    , pmin_(ic, Element::class_name, id(), std::move(object().pmin))
    , pmax_(ic, Element::class_name, id(), std::move(object().pmax))
    , pressure_cost_(
          ic, Element::class_name, id(), std::move(object().pressure_cost))
{
}

bool GasNodeLP::add_to_lp(SystemContext& sc,
                          const ScenarioLP& scenario,
                          const StageLP& stage,
                          LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }
  const auto& blocks = stage.blocks();
  if (blocks.empty()) {
    return true;
  }

  const auto stage_uid = stage.uid();
  const auto scen_uid = scenario.uid();
  const double pmin = std::max(0.0, pmin_at(stage_uid));
  const auto pmax = pmax_at(stage_uid);
  const double pi_lo = pmin * pmin;
  const double pi_hi =
      pmax ? std::max(pi_lo, *pmax * *pmax) : LinearProblem::DblMax;
  const double pcost = pressure_cost_.at(stage_uid).value_or(0.0);

  BIndexHolder<ColIndex> pcols;
  BIndexHolder<RowIndex> brows;
  map_reserve(pcols, blocks.size());
  map_reserve(brows, blocks.size());

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto ctx = make_block_context(scen_uid, stage_uid, buid);
    pcols[buid] = lp.add_col(SparseCol {
        .lowb = pi_lo,
        .uppb = pi_hi,
        .cost = CostHelper::block_ecost(scenario, stage, block, pcost),
        .class_name = cname,
        .variable_name = PiName,
        .variable_uid = uid(),
        .context = ctx,
    });
    // Gas balance [km³/h]: Σ injections − Σ withdrawals = Σ loads.  The
    // GasDemand loads shift the right-hand side; the dual is the nodal
    // gas price [$ / km³].
    brows[buid] = lp.add_row(SparseRow {
        .class_name = cname,
        .constraint_name = BalanceName,
        .variable_uid = uid(),
        .context = ctx,
    });
  }

  const auto st_key = std::tuple {scen_uid, stage_uid};
  pi_cols_[st_key] = std::move(pcols);
  balance_rows_[st_key] = std::move(brows);
  sc.add_ampl_variable(
      ampl_name, uid(), PiName, scenario, stage, pi_cols_.at(st_key));
  return true;
}

bool GasNodeLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();
  out.add_col_sol(cname, PiName, pid, pi_cols_);
  out.add_row_dual(cname, BalanceName, pid, balance_rows_);

  // Pressure p = √π [bar abs], reconstructed from the π primal.
  if (!out.emit_solution(cname)) {
    return true;
  }
  STBIndexHolder<double> pressure;
  for (const auto& [st_key, cols] : pi_cols_) {
    BIndexHolder<double> vals;
    map_reserve(vals, cols.size());
    for (const auto& [buid, col] : cols) {
      vals[buid] = std::sqrt(std::max(0.0, out.primal(col)));
    }
    pressure[st_key] = std::move(vals);
  }
  out.add_col_sol_values(cname, PressureName, pid, pressure);
  return true;
}

}  // namespace gtopt
