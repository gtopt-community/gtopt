/**
 * @file      hydrogen_demand_lp.cpp
 * @brief     Implementation of HydrogenDemandLP (hydrogen offtake)
 * @date      Fri Nov 20 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * LP shape, per (scenario s, stage t):
 *
 *   load[b] ∈ [lmin(t,b), lmax(t,b)]            (MW_LHV, one col per block)
 *   cost(load[b]) = −price(t,b) · dur_b · prob · disc
 *   HydrogenNode balance row b:  … − load[b] = 0
 *   Σ_b dur_b · load[b] + slack ≥ min_energy(t)   (slack cost min_energy_cost)
 *   Σ_b dur_b · load[b]         ≤ max_energy(t)
 *
 * Equal ``min_energy`` and ``max_energy`` give an equality target.
 */

#include <algorithm>

#include <gtopt/cost_helper.hpp>
#include <gtopt/hydrogen_demand_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

HydrogenDemandLP::HydrogenDemandLP(const HydrogenDemand& hd,
                                   const InputContext& ic)
    : Base(hd, ic, Element::class_name)
    , lmax_(ic, Element::class_name, id(), std::move(object().lmax))
    , lmin_(ic, Element::class_name, id(), std::move(object().lmin))
    , price_(ic, Element::class_name, id(), std::move(object().price))
    , min_energy_(ic, Element::class_name, id(), std::move(object().min_energy))
    , max_energy_(ic, Element::class_name, id(), std::move(object().max_energy))
    , min_energy_cost_(
          ic, Element::class_name, id(), std::move(object().min_energy_cost))
{
}

bool HydrogenDemandLP::add_to_lp(SystemContext& sc,
                                 const ScenarioLP& scenario,
                                 const StageLP& stage,
                                 LinearProblem& lp)
{
  if (!is_active(stage)) {
    return true;
  }
  const auto& blocks = stage.blocks();
  if (blocks.empty()) {
    return true;
  }

  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();
  const auto stage_uid = stage.uid();
  const auto scen_uid = scenario.uid();
  const auto st_key = std::tuple {scen_uid, stage_uid};

  const auto& node_ref = hydrogen_demand().hydrogen_node;
  const BIndexHolder<RowIndex>* brows = nullptr;
  if (node_ref.has_value()) {
    brows = &sc.element<HydrogenNodeLP>(HydrogenNodeLPSId {node_ref.value()})
                 .balance_rows_at(scenario, stage);
  }

  BIndexHolder<ColIndex> cols;
  map_reserve(cols, blocks.size());
  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const double lo = std::max(0.0, lmin_.at(stage_uid, buid).value_or(0.0));
    const double hi = lmax_.at(stage_uid, buid).value_or(LinearProblem::DblMax);
    const double price = price_.at(stage_uid, buid).value_or(0.0);
    const auto col = lp.add_col(SparseCol {
        .lowb = lo,
        .uppb = std::max(lo, hi),
        .cost = -CostHelper::block_ecost(scenario, stage, block, price),
        .class_name = cname,
        .variable_name = LoadName,
        .variable_uid = uid(),
        .context = make_block_context(scen_uid, stage_uid, buid),
    });
    cols[buid] = col;
    if (brows != nullptr) {
      auto& brow = lp.row_at(brows->at(buid));
      brow[col] = -1.0;
    }
  }

  const auto stage_ctx = make_stage_context(scen_uid, stage_uid);
  const auto make_energy_row = [&](std::string_view cons_name)
  {
    SparseRow row {
        // Dual = marginal cost of hydrogen over the stage [$/MWh_LHV].
        .cost_scale_type = ConstraintScaleType::Energy,
        .class_name = cname,
        .constraint_name = cons_name,
        .variable_uid = uid(),
        .context = stage_ctx,
    };
    for (const auto& block : blocks) {
      row[cols.at(block.uid())] = block.duration();
    }
    return row;
  };

  if (const auto emin = min_energy_.at(stage_uid)) {
    auto row = make_energy_row(MinEnergyName);
    const double pen = min_energy_cost_.at(stage_uid).value_or(0.0);
    if (pen > 0.0) {
      const auto slack = lp.add_col(SparseCol {
          .lowb = 0.0,
          .cost = pen
              * CostHelper::cost_factor(scenario.probability_factor(),
                                        stage.discount_factor()),
          .class_name = cname,
          .variable_name = MinEnergySlackName,
          .variable_uid = uid(),
          .context = stage_ctx,
      });
      row[slack] = 1.0;
      min_energy_slack_cols_[st_key] = slack;
    }
    min_energy_rows_[st_key] = lp.add_row(std::move(row).greater_equal(*emin));
  }
  if (const auto emax = max_energy_.at(stage_uid)) {
    auto row = make_energy_row(MaxEnergyName);
    max_energy_rows_[st_key] = lp.add_row(std::move(row).less_equal(*emax));
  }

  load_cols_[st_key] = std::move(cols);
  sc.add_ampl_variable(
      ampl_name, uid(), LoadName, scenario, stage, load_cols_.at(st_key));
  return true;
}

bool HydrogenDemandLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();
  out.add_col_sol(cname, LoadName, pid, load_cols_);
  out.add_col_cost(cname, LoadName, pid, load_cols_);
  if (!min_energy_rows_.empty()) {
    out.add_row_dual(cname, MinEnergyName, pid, min_energy_rows_);
  }
  if (!max_energy_rows_.empty()) {
    out.add_row_dual(cname, MaxEnergyName, pid, max_energy_rows_);
  }
  if (!min_energy_slack_cols_.empty()) {
    out.add_col_sol(cname, MinEnergySlackName, pid, min_energy_slack_cols_);
    out.add_col_cost(cname, MinEnergySlackName, pid, min_energy_slack_cols_);
  }
  return true;
}

}  // namespace gtopt
