/**
 * @file      gas_turbine_lp.cpp
 * @brief     Implementation of GasTurbineLP (fuel term + pressure gate)
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>

#include <gtopt/coupler_fail_closed.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_physics.hpp>
#include <gtopt/gas_turbine_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/warn_once.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{
constexpr double kDefaultPressureBand = 0.5;
}  // namespace

GasTurbineLP::GasTurbineLP(const GasTurbine& gt, const InputContext& ic)
    : ObjectLP<GasTurbine>(gt, ic, Element::class_name)
    , heat_rate_(ic, Element::class_name, id(), std::move(object().heat_rate))
{
}

bool GasTurbineLP::add_to_lp(SystemContext& sc,
                             const ScenarioLP& scenario,
                             const StageLP& stage,
                             LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();

  const auto& gt = gas_turbine();
  const auto& gen = sc.element<GeneratorLP>(GeneratorLPSId {gt.generator});
  const auto& node = sc.element<GasNodeLP>(GasNodeLPSId {gt.gas_node});
  // Fail-closed coupler (R2): the generator burns gas only through this
  // turbine; with the turbine or its gas node inactive the fuel row
  // cannot be written, so the generation is closed (no gas-free power).
  if (!is_active(stage) || !node.is_active(stage)) {
    close_producing_cols(lp, gen.lookup_generation_cols(scenario, stage));
    return true;
  }
  if (!gen.is_active(stage)) {
    return true;
  }
  const auto& blocks = stage.blocks();
  const auto stage_uid = stage.uid();
  const auto scen_uid = scenario.uid();
  const auto st_key = std::tuple {scen_uid, stage_uid};
  const auto& bal = node.balance_rows_at(scenario, stage);
  const auto& pi_cols = node.pi_cols_at(scenario, stage);

  const auto p_min = gt.min_pressure;
  const double band = gt.pressure_band.value_or(kDefaultPressureBand);
  const double p_nom = gt.nominal_pressure.value_or(0.0);
  const double out_min = gt.derate_min_output.value_or(0.0);

  BIndexHolder<ColIndex> gcols;
  BIndexHolder<double> hrs;
  BIndexHolder<RowIndex> grows;
  BIndexHolder<RowIndex> drows;

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto gcol = gen.lookup_generation_col(scenario, stage, buid);
    if (!gcol) {
      continue;  // zero-pmax block: no generation column
    }
    gcols[buid] = *gcol;
    const double hr = heat_rate_.at(stage_uid, buid).value_or(0.0);
    hrs[buid] = hr;
    if (hr != 0.0) {
      lp.row_at(bal.at(buid))[*gcol] = -hr;
    }
    if (!p_min) {
      continue;
    }

    // Fuel-gas pressure gate: P ≤ s π + c (concave PWL in π).
    const double pmax =
        gen.param_pmax(stage_uid, buid).value_or(lp.col_at(*gcol).uppb);
    const auto cap =
        gas::plant_capability_rows(pmax, *p_min, band, p_nom, out_min);
    if (cap.derate_dropped) {
      warn_once_per_key(
          std::format("GasTurbine:{}:derate_dropped", uid()),
          "GasTurbine '{}': derate line steeper than the minimum-pressure "
          "ramp; derate row dropped (first at stage={} block={}).  Logged "
          "once per element.",
          gt.name,
          stage_uid,
          buid);
    }
    const auto ctx = make_block_context(scen_uid, stage_uid, buid);
    for (std::size_t i = 0; i < cap.rows.size(); ++i) {
      const auto& r = cap.rows[i];
      SparseRow row {
          .class_name = cname,
          .constraint_name = i == 0 ? PressureGateName : DerateName,
          .variable_uid = uid(),
          .context = ctx,
      };
      row[*gcol] = 1.0;
      row[pi_cols.at(buid)] = -r.slope;
      const auto ridx = lp.add_row(std::move(row).less_equal(r.intercept));
      if (i == 0) {
        grows[buid] = ridx;
      } else {
        drows[buid] = ridx;
      }
    }
  }

  gen_cols_[st_key] = std::move(gcols);
  heat_rates_[st_key] = std::move(hrs);
  if (!grows.empty()) {
    gate_rows_[st_key] = std::move(grows);
  }
  if (!drows.empty()) {
    derate_rows_[st_key] = std::move(drows);
  }
  return true;
}

bool GasTurbineLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();
  if (!gate_rows_.empty()) {
    out.add_row_dual(cname, PressureGateName, pid, gate_rows_);
  }
  if (!derate_rows_.empty()) {
    out.add_row_dual(cname, DerateName, pid, derate_rows_);
  }
  if (!out.emit_solution(cname)) {
    return true;
  }
  // Fuel burnt [km³/h] = heat_rate · P.
  STBIndexHolder<double> fuel;
  for (const auto& [st_key, gcols] : gen_cols_) {
    const auto& hrs = heat_rates_.at(st_key);
    BIndexHolder<double> fv;
    map_reserve(fv, gcols.size());
    for (const auto& [buid, gcol] : gcols) {
      fv[buid] = hrs.at(buid) * out.primal(gcol);
    }
    fuel[st_key] = std::move(fv);
  }
  out.add_col_sol_values(cname, FuelName, pid, fuel);
  return true;
}

}  // namespace gtopt
