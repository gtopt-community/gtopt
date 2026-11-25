/**
 * @file      gas_compressor_lp.cpp
 * @brief     Implementation of GasCompressorLP (ratio rows + fuel model)
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <cmath>
#include <format>
#include <ranges>
#include <stdexcept>

#include <gtopt/ampl_variable.hpp>
#include <gtopt/bus_lp.hpp>
#include <gtopt/gas_compressor_lp.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

namespace
{
constexpr double kDefaultEfficiency = 0.80;
constexpr double kDefaultDriverEfficiency = 0.33;
constexpr double kDefaultMotorEfficiency = 0.95;
/// Default operating flow as a fraction of fmax.
constexpr double kDefaultOpFlowFraction = 0.6;
}  // namespace

GasCompressorLP::GasCompressorLP(const GasCompressor& cs,
                                 const InputContext& ic)
    : ObjectLP<GasCompressor>(cs, ic, Element::class_name)
    , fmax_(ic, Element::class_name, id(), std::move(object().fmax))
{
}

GasCompressorLP::FuelModel GasCompressorLP::fuel_model() const noexcept
{
  return gas_compressor().fuel_model_enum().value_or(FuelModel::none);
}

bool GasCompressorLP::is_electric() const noexcept
{
  return gas_compressor().drive_enum() == GasCompressorDrive::electric;
}

double GasCompressorLP::gamma() const
{
  const auto& c = gas_compressor();
  if (c.gamma) {
    return *c.gamma;
  }
  return gas::adiabatic_gamma(
      c.efficiency.value_or(kDefaultEfficiency),
      c.driver_efficiency.value_or(kDefaultDriverEfficiency),
      alpha());
}

double GasCompressorLP::fixed_fraction() const
{
  const auto& c = gas_compressor();
  if (c.fuel_fraction) {
    return *c.fuel_fraction;
  }
  return gamma() * (std::pow(max_ratio(), alpha()) - 1.0);
}

double GasCompressorLP::kappa() const
{
  const auto& c = gas_compressor();
  return gas::electric_kappa(
      c.driver_efficiency.value_or(kDefaultDriverEfficiency),
      c.motor_efficiency.value_or(kDefaultMotorEfficiency));
}

bool GasCompressorLP::add_to_lp(SystemContext& sc,
                                const ScenarioLP& scenario,
                                const StageLP& stage,
                                LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }
  const auto& cs = gas_compressor();
  if (!cs.fuel_model_enum()) {
    throw std::runtime_error(
        std::format("GasCompressor '{}': unknown fuel_model '{}' (none, "
                    "fixed_fraction, saldarriaga)",
                    cs.name,
                    cs.fuel_model.value_or("")));
  }
  if (!cs.drive_enum()) {
    throw std::runtime_error(
        std::format("GasCompressor '{}': unknown drive '{}' (gas, electric)",
                    cs.name,
                    cs.drive.value_or("")));
  }
  const auto& node_in = sc.element<GasNodeLP>(GasNodeLPSId {cs.gas_node_in});
  const auto& node_out = sc.element<GasNodeLP>(GasNodeLPSId {cs.gas_node_out});
  if (!node_in.is_active(stage) || !node_out.is_active(stage)) {
    return true;
  }
  {
    AmplElementMetadata metadata;
    metadata.reserve(3);
    if (cs.type) {
      metadata.emplace_back(GasNodeLP::TypeKey, *cs.type);
    }
    metadata.emplace_back(GasNodeInKey, static_cast<double>(node_in.uid()));
    metadata.emplace_back(GasNodeOutKey, static_cast<double>(node_out.uid()));
    sc.register_ampl_element_metadata(ampl_name, uid(), std::move(metadata));
  }
  const auto& blocks = stage.blocks();
  if (blocks.empty()) {
    return true;
  }
  const auto stage_uid = stage.uid();
  const auto scen_uid = scenario.uid();
  const auto st_key = std::tuple {scen_uid, stage_uid};

  const auto fmax_opt = fmax_.at(stage_uid);
  if (!fmax_opt) {
    throw std::runtime_error(
        std::format("GasCompressor '{}': fmax is required", cs.name));
  }
  const double fmax = std::max(0.0, *fmax_opt);
  const double cr = max_ratio();
  const auto model = fuel_model();
  const bool electric = is_electric() && model != FuelModel::none;
  const bool bidirectional = cs.bidirectional.value_or(false);
  if (bidirectional && (cr > 1.0 + 1e-12 || model != FuelModel::none)) {
    throw std::runtime_error(
        std::format("GasCompressor '{}': bidirectional is an open valve; "
                    "it needs max_ratio 1 and fuel_model 'none'",
                    cs.name));
  }
  // A bidirectional valve is π_out = π_in (ratio row + no-expand row).
  const bool allow_bypass = !bidirectional
      && cs.allow_bypass.value_or(model != FuelModel::saldarriaga);

  // Saldarriaga Taylor planes at the operating point(s).
  std::vector<gas::SaldarriagaPlane> planes;
  if (model == FuelModel::saldarriaga) {
    const double g = gamma();
    const double a = alpha();
    const double psi0 = cs.op_flow.value_or(kDefaultOpFlowFraction * fmax);
    const double p_in0 = cs.op_suction_pressure.value_or(
        0.5
        * (node_in.pmin_at(stage_uid)
           + node_in.pmax_at(stage_uid).value_or(node_in.pmin_at(stage_uid))));
    std::vector<double> betas = cs.op_ratio;
    if (betas.empty()) {
      betas.push_back(1.0 + (0.5 * (cr - 1.0)));
    }
    planes.reserve(betas.size());
    for (const double b : betas) {
      planes.push_back(gas::saldarriaga_plane(g, a, psi0, b, p_in0));
    }
  }
  const double gamma_ff =
      model == FuelModel::fixed_fraction ? fixed_fraction() : 0.0;
  const double kap = electric ? kappa() : 0.0;

  const BIndexHolder<RowIndex>* bus_rows = nullptr;
  if (electric) {
    if (!cs.bus) {
      throw std::runtime_error(std::format(
          "GasCompressor '{}': drive 'electric' needs a bus", cs.name));
    }
    const auto& bus = sc.element<BusLP>(BusLPSId {*cs.bus});
    if (bus.is_active(stage)) {
      bus_rows = &bus.balance_rows_at(scenario, stage);
    }
  }

  const auto& pin_cols = node_in.pi_cols_at(scenario, stage);
  const auto& pout_cols = node_out.pi_cols_at(scenario, stage);
  const auto& bal_in = node_in.balance_rows_at(scenario, stage);
  const auto& bal_out = node_out.balance_rows_at(scenario, stage);

  BIndexHolder<ColIndex> fcols;
  BIndexHolder<ColIndex> fuel_cols;
  BIndexHolder<ColIndex> pcols;
  BIndexHolder<RowIndex> rrows;
  BIndexHolder<ColIndex> pin_holder;
  BIndexHolder<ColIndex> pout_holder;

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto ctx = make_block_context(scen_uid, stage_uid, buid);
    const auto pin = pin_cols.at(buid);
    const auto pout = pout_cols.at(buid);
    pin_holder[buid] = pin;
    pout_holder[buid] = pout;

    const auto f = lp.add_col(SparseCol {
        .lowb = bidirectional ? -fmax : 0.0,
        .uppb = fmax,
        .class_name = cname,
        .variable_name = FlowName,
        .variable_uid = uid(),
        .context = ctx,
    });
    fcols[buid] = f;
    auto& brow_in = lp.row_at(bal_in.at(buid));
    // Gas drive with a fixed fraction draws (1 + γ_ff) Ψ at suction.
    brow_in[f] = -(1.0 + (electric ? 0.0 : gamma_ff));
    lp.row_at(bal_out.at(buid))[f] = 1.0;

    // ratio: π_out − β̄² π_in ≤ 0
    SparseRow ratio {
        .class_name = cname,
        .constraint_name = RatioName,
        .variable_uid = uid(),
        .context = ctx,
    };
    ratio[pout] = 1.0;
    ratio[pin] = -(cr * cr);
    rrows[buid] = lp.add_row(std::move(ratio).less_equal(0.0));

    if (!allow_bypass) {
      SparseRow noexp {
          .class_name = cname,
          .constraint_name = NoExpandName,
          .variable_uid = uid(),
          .context = ctx,
      };
      noexp[pout] = 1.0;
      noexp[pin] = -1.0;
      std::ignore = lp.add_row(std::move(noexp).greater_equal(0.0));
    }

    // Fuel column (Saldarriaga) and its Taylor planes.
    std::optional<ColIndex> lc;
    if (model == FuelModel::saldarriaga) {
      lc = lp.add_col(SparseCol {
          .lowb = 0.0,
          .uppb = fmax,
          .class_name = cname,
          .variable_name = FuelName,
          .variable_uid = uid(),
          .context = ctx,
      });
      fuel_cols[buid] = *lc;
      if (!electric) {
        lp.row_at(bal_in.at(buid))[*lc] = -1.0;
      }
      for (const auto& [j, pl] : std::views::enumerate(planes)) {
        SparseRow plane {
            .class_name = cname,
            .constraint_name = FuelPlaneName,
            .variable_uid = uid(),
            .context = make_block_context(
                scen_uid, stage_uid, buid, static_cast<int>(j) + 1),
        };
        plane[*lc] = 1.0;
        plane[f] = -pl.c_psi;
        plane[pout] = -pl.c_pi;
        plane[pin] = pl.c_pi * pl.r0;
        std::ignore = lp.add_row(std::move(plane).greater_equal(0.0));
      }
    }

    if (electric) {
      const auto pw = lp.add_col(SparseCol {
          .lowb = 0.0,
          .uppb = cs.power_max.value_or(LinearProblem::DblMax),
          .class_name = cname,
          .variable_name = PowerName,
          .variable_uid = uid(),
          .context = ctx,
      });
      pcols[buid] = pw;
      if (bus_rows != nullptr) {
        lp.row_at(bus_rows->at(buid))[pw] = -1.0;
      } else {
        // Fail-closed coupler (R2): with the motor bus inactive there is
        // no electricity to draw — pin the motor power at 0 (the motor
        // row then forbids any compression work) instead of letting the
        // station run on free power.
        lp.col_at(pw).uppb = 0.0;
      }
      // motor: power − κ · LC = 0  (LC = fuel column or γ_ff Ψ)
      SparseRow motor {
          .class_name = cname,
          .constraint_name = MotorName,
          .variable_uid = uid(),
          .context = ctx,
      };
      motor[pw] = 1.0;
      if (lc) {
        motor[*lc] = -kap;
      } else {
        motor[f] = -kap * gamma_ff;
      }
      std::ignore = lp.add_row(std::move(motor).equal(0.0));
    }
  }

  flow_cols_[st_key] = std::move(fcols);
  ratio_rows_[st_key] = std::move(rrows);
  pi_in_cols_[st_key] = std::move(pin_holder);
  pi_out_cols_[st_key] = std::move(pout_holder);
  sc.add_ampl_variable(
      ampl_name, uid(), FlowName, scenario, stage, flow_cols_.at(st_key));
  if (!fuel_cols.empty()) {
    fuel_cols_[st_key] = std::move(fuel_cols);
    sc.add_ampl_variable(
        ampl_name, uid(), FuelName, scenario, stage, fuel_cols_.at(st_key));
  }
  if (!pcols.empty()) {
    power_cols_[st_key] = std::move(pcols);
    sc.add_ampl_variable(
        ampl_name, uid(), PowerName, scenario, stage, power_cols_.at(st_key));
  }
  return true;
}

bool GasCompressorLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();
  out.add_col_sol(cname, FlowName, pid, flow_cols_);
  out.add_row_dual(cname, RatioName, pid, ratio_rows_);
  if (!power_cols_.empty()) {
    out.add_col_sol(cname, PowerName, pid, power_cols_);
  }
  if (!out.emit_solution(cname)) {
    return true;
  }

  const auto model = fuel_model();
  const double gamma_ff =
      model == FuelModel::fixed_fraction ? fixed_fraction() : 0.0;

  // Fuel [km³/h] (Saldarriaga column or γ_ff Ψ) and achieved ratio
  // β = √(π_out / π_in).
  STBIndexHolder<double> fuel;
  STBIndexHolder<double> ratio;
  for (const auto& [st_key, fcols] : flow_cols_) {
    const auto lc_it = fuel_cols_.find(st_key);
    const auto& pins = pi_in_cols_.at(st_key);
    const auto& pouts = pi_out_cols_.at(st_key);
    BIndexHolder<double> fv;
    BIndexHolder<double> rv;
    map_reserve(fv, fcols.size());
    map_reserve(rv, fcols.size());
    for (const auto& [buid, fcol] : fcols) {
      double lc = gamma_ff * out.primal(fcol);
      if (lc_it != fuel_cols_.end()) {
        lc = out.primal(lc_it->second.at(buid));
      }
      fv[buid] = std::max(0.0, lc);
      const double pi_in = out.primal(pins.at(buid));
      const double pi_out = out.primal(pouts.at(buid));
      rv[buid] = pi_in > 0.0 ? std::sqrt(std::max(0.0, pi_out / pi_in)) : 0.0;
    }
    fuel[st_key] = std::move(fv);
    ratio[st_key] = std::move(rv);
  }
  out.add_col_sol_values(cname, FuelName, pid, fuel);
  out.add_col_sol_values(cname, RatioName, pid, ratio);
  return true;
}

}  // namespace gtopt
