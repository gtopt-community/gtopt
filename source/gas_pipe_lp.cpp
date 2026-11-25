/**
 * @file      gas_pipe_lp.cpp
 * @brief     Implementation of GasPipeLP (Weymouth tangents + line pack)
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
#include <utility>

#include <gtopt/ampl_variable.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_physics.hpp>
#include <gtopt/gas_pipe_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/warn_once.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// (flow coefficient c, rhs r) of the linearised row
/// π_a − e^s π_b − c f̄ = r at the reference flow f₀.
[[nodiscard]] std::pair<double, double> gas_lin_coeffs(double k_e,
                                                       double f0,
                                                       bool tangent) noexcept
{
  const double a = std::abs(f0);
  if (tangent) {
    return {2.0 * k_e * a, -k_e * f0 * a};
  }
  return {k_e * a, 0.0};
}

/// Clamp |f₀| ≥ f_min, keeping its sign (+ for zero).
[[nodiscard]] double gas_clamp_ref_flow(double f0, double fmin) noexcept
{
  if (std::abs(f0) >= fmin) {
    return f0;
  }
  return f0 < 0.0 ? -fmin : fmin;
}

constexpr int kDefaultSegments = 8;

}  // namespace

GasPipeLP::GasPipeLP(const GasPipe& pipe, const InputContext& ic)
    : ObjectLP<GasPipe>(pipe, ic, Element::class_name)
    , fmax_(ic, Element::class_name, id(), std::move(object().fmax))
{
}

double GasPipeLP::weymouth_k(const GasOptions& go) const
{
  const auto& p = gas_pipe();
  if (p.weymouth_k) {
    return *p.weymouth_k;
  }
  return gas::weymouth_k(gas::PipeGeometry {
      .length_km = p.length.value_or(0.0),
      .diameter_mm = p.diameter.value_or(0.0),
      .efficiency = p.efficiency.value_or(0.92),
      .gravity = p.gravity.value_or(go.specific_gravity()),
      .temperature = p.temperature.value_or(go.temperature()),
      .z = p.z.value_or(go.z()),
  });
}

double GasPipeLP::linepack_coeff(const GasOptions& go) const
{
  const auto& p = gas_pipe();
  if (p.linepack_coeff) {
    return *p.linepack_coeff;
  }
  return gas::linepack_coeff(p.length.value_or(0.0),
                             p.diameter.value_or(0.0),
                             p.z.value_or(go.z()),
                             p.temperature.value_or(go.temperature()));
}

double GasPipeLP::elevation_exponent(double h_a,
                                     double h_b,
                                     const GasOptions& go) const
{
  const auto& p = gas_pipe();
  if (p.elevation_exponent) {
    return *p.elevation_exponent;
  }
  if (h_a == h_b) {
    return 0.0;
  }
  return gas::elevation_exponent(h_b - h_a,
                                 p.gravity.value_or(go.specific_gravity()),
                                 p.z.value_or(go.z()),
                                 p.temperature.value_or(go.temperature()));
}

bool GasPipeLP::add_to_lp(SystemContext& sc,
                          const ScenarioLP& scenario,
                          const StageLP& stage,
                          LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }
  const auto& pipe = gas_pipe();
  const auto& node_a = sc.element<GasNodeLP>(GasNodeLPSId {pipe.gas_node_a});
  const auto& node_b = sc.element<GasNodeLP>(GasNodeLPSId {pipe.gas_node_b});
  if (!node_a.is_active(stage) || !node_b.is_active(stage)) {
    return true;
  }
  {
    AmplElementMetadata metadata;
    metadata.reserve(3);
    if (pipe.type) {
      metadata.emplace_back(GasNodeLP::TypeKey, *pipe.type);
    }
    metadata.emplace_back(GasNodeAKey, static_cast<double>(node_a.uid()));
    metadata.emplace_back(GasNodeBKey, static_cast<double>(node_b.uid()));
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
        std::format("GasPipe '{}': fmax is required", pipe.name));
  }
  // Auto-fix (behaviour kept; validate_planning should reject it): a
  // negative fmax is clamped to 0 — warn once per element.
  if (*fmax_opt < 0.0) {
    warn_once_per_key(std::format("GasPipe:{}:negative_fmax", uid()),
                      "GasPipe '{}': fmax = {} < 0 is clamped to 0 (the pipe "
                      "carries no flow).  Logged once per element.",
                      pipe.name,
                      *fmax_opt);
  }
  const double fmax = std::max(0.0, *fmax_opt);
  const auto& go = sc.options().gas_options();
  const auto mode = pipe.weymouth_mode_enum();
  if (!mode) {
    throw std::runtime_error(
        std::format("GasPipe '{}': unknown weymouth_mode '{}' (radial, "
                    "linearized, none)",
                    pipe.name,
                    pipe.weymouth_mode.value_or("")));
  }
  if (*mode == GasWeymouthMode::none) {
    // Capacity-only arc a → b (a transfer through a parallel system):
    // 0 ≤ f ≤ fmax in the two balances, no pressure coupling.
    BIndexHolder<ColIndex> cols;
    map_reserve(cols, blocks.size());
    const auto& bal_a = node_a.balance_rows_at(scenario, stage);
    const auto& bal_b = node_b.balance_rows_at(scenario, stage);
    for (const auto& block : blocks) {
      const auto buid = block.uid();
      const auto col = lp.add_col(SparseCol {
          .lowb = 0.0,
          .uppb = fmax,
          .class_name = cname,
          .variable_name = FlowName,
          .variable_uid = uid(),
          .context = make_block_context(scen_uid, stage_uid, buid),
      });
      cols[buid] = col;
      lp.row_at(bal_a.at(buid))[col] = -1.0;
      lp.row_at(bal_b.at(buid))[col] = 1.0;
    }
    flow_cols_[st_key] = std::move(cols);
    sc.add_ampl_variable(
        ampl_name, uid(), FlowName, scenario, stage, flow_cols_.at(st_key));
    return true;
  }
  // Elevation: π_a − e^s π_b = K_e f|f|, K_e = K (e^s − 1)/s.
  const double s_elev =
      elevation_exponent(node_a.elevation(), node_b.elevation(), go);
  exp_s_ = std::exp(s_elev);
  k_eff_ = weymouth_k(go) * gas::elevation_k_factor(s_elev);
  const double kw = k_eff_;
  linearized_ = *mode == GasWeymouthMode::linearized;
  const auto linearization = pipe.linearization_enum();
  if (!linearization) {
    throw std::runtime_error(
        std::format("GasPipe '{}': unknown linearization '{}' (secant, "
                    "tangent)",
                    pipe.name,
                    pipe.linearization.value_or("")));
  }
  tangent_lin_ = *linearization == GasLinearization::tangent;
  const double fmin = go.slp_min_flow_fraction() * fmax;
  const double f0_init = gas_clamp_ref_flow(
      pipe.reference_flow.value_or(go.slp_initial_flow_fraction() * fmax),
      fmin);
  if (linearized_ && !pipe.reference_flow && go.slp_max_iterations() <= 0) {
    warn_once_per_key(
        std::format("GasPipe:{}:no_reference_flow", uid()),
        "GasPipe '{}': linearized without reference_flow and without "
        "gas_options.slp_iterations: f0 = {:.4g} is a guess, set either.  "
        "Logged once per element.",
        pipe.name,
        f0_init);
  }

  const int nseg = std::max(1, pipe.segments.value_or(kDefaultSegments));
  const auto layout = pipe.tangent_layout_enum();
  if (!layout) {
    throw std::runtime_error(
        std::format("GasPipe '{}': unknown tangent_layout '{}' (uniform, "
                    "relative_pressure)",
                    pipe.name,
                    pipe.tangent_layout.value_or("")));
  }
  const double p_in = node_a.pmax_at(stage_uid).value_or(0.0);
  auto tpts = linearized_ ? std::vector<double> {}
                          : gas::tangent_points(kw, fmax, nseg, *layout, p_in);
  const double flow_low = linearized_ ? -fmax : 0.0;

  const auto& pa_cols = node_a.pi_cols_at(scenario, stage);
  const auto& pb_cols = node_b.pi_cols_at(scenario, stage);
  const auto& bal_a = node_a.balance_rows_at(scenario, stage);
  const auto& bal_b = node_b.balance_rows_at(scenario, stage);

  const bool linepack = has_linepack();

  // Line-pack linearisation: LP = c (p_a + p_b)/2 with
  // p ≈ (π + p_r²)/(2 p_r)  ⇒  LP = α (π_a + π_b) + β.
  double lp_alpha = 0.0;
  double lp_beta = 0.0;
  double lp_ini = 0.0;
  if (linepack) {
    const double c = linepack_coeff(go);
    const double pmax_a = node_a.pmax_at(stage_uid).value_or(0.0);
    const double pmax_b = node_b.pmax_at(stage_uid).value_or(0.0);
    const double pr = pipe.linepack_pressure_ref.value_or(
        0.25
        * (node_a.pmin_at(stage_uid) + pmax_a + node_b.pmin_at(stage_uid)
           + pmax_b));
    // P0-13: pr = 0 (nodes without pmax / pmin and no explicit
    // linepack_pressure_ref) would put c / 0 = inf in the matrix.
    if (!(pr > 0.0)) {
      fail_lp(*this,
              scenario,
              stage,
              {
                  .field = "linepack_pressure_ref",
                  .value = std::format("{}", pr),
                  .expected = "> 0 (set it, or give the end nodes a "
                              "pressure range)",
              });
    }
    lp_alpha = c / (4.0 * pr);
    lp_beta = c * pr / 2.0;
    if (pipe.linepack_ini) {
      lp_ini = *pipe.linepack_ini;
    } else {
      const double pia = node_a.p_init().value_or(pr);
      const double pib = node_b.p_init().value_or(pr);
      lp_ini = (lp_alpha * ((pia * pia) + (pib * pib))) + lp_beta;
    }
  }

  BIndexHolder<ColIndex> fcols;
  BIndexHolder<ColIndex> fin_cols;
  BIndexHolder<ColIndex> fout_cols;
  BIndexHolder<ColIndex> lp_cols;
  BIndexHolder<std::vector<RowIndex>> wrows;
  BIndexHolder<ColIndex> pa_keep;
  BIndexHolder<ColIndex> pb_keep;
  BIndexHolder<RowIndex> lp_bal_rows;
  BIndexHolder<RowIndex> lin_rows;
  BIndexHolder<RowIndex> prox_rows;
  const double slp_prox =
      go.slp_max_iterations() > 0 ? go.slp_proximal_cost_value() : 0.0;
  BIndexHolder<double> ref_flows;

  std::optional<ColIndex> prev_lp;
  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto ctx = make_block_context(scen_uid, stage_uid, buid);
    const auto make_flow = [&](std::string_view name)
    {
      return lp.add_col(SparseCol {
          .lowb = flow_low,
          .uppb = fmax,
          .class_name = cname,
          .variable_name = name,
          .variable_uid = uid(),
          .context = ctx,
      });
    };

    // (coefficient, column) terms of the flow f̄ in the Weymouth rows.
    std::vector<std::pair<double, ColIndex>> flow_terms;
    if (linepack) {
      const auto fin = make_flow(FlowInName);
      const auto fout = make_flow(FlowOutName);
      fin_cols[buid] = fin;
      fout_cols[buid] = fout;
      lp.row_at(bal_a.at(buid))[fin] = -1.0;
      lp.row_at(bal_b.at(buid))[fout] = 1.0;
      flow_terms = {{0.5, fin}, {0.5, fout}};

      const auto lpc = lp.add_col(SparseCol {
          .lowb = 0.0,
          .class_name = cname,
          .variable_name = LinepackName,
          .variable_uid = uid(),
          .context = ctx,
      });
      lp_cols[buid] = lpc;

      // linepack_def: LP − α π_a − α π_b = β
      SparseRow def {
          .class_name = cname,
          .constraint_name = LinepackDefName,
          .variable_uid = uid(),
          .context = ctx,
      };
      def[lpc] = 1.0;
      def[pa_cols.at(buid)] = -lp_alpha;
      def[pb_cols.at(buid)] = -lp_alpha;
      std::ignore = lp.add_row(std::move(def).equal(lp_beta));

      // linepack_balance: Δt fin − Δt fout − LP[b] + LP[b−1] = 0
      SparseRow bal {
          .class_name = cname,
          .constraint_name = LinepackBalanceName,
          .variable_uid = uid(),
          .context = ctx,
      };
      const double dur = block.duration();
      bal[fin] = dur;
      bal[fout] = -dur;
      bal[lpc] = -1.0;
      double rhs = 0.0;
      if (prev_lp) {
        bal[*prev_lp] = 1.0;
      } else {
        rhs = -lp_ini;
      }
      lp_bal_rows[buid] = lp.add_row(std::move(bal).equal(rhs));
      prev_lp = lpc;
    } else {
      const auto f = make_flow(FlowName);
      fcols[buid] = f;
      lp.row_at(bal_a.at(buid))[f] = -1.0;
      lp.row_at(bal_b.at(buid))[f] = 1.0;
      flow_terms = {{1.0, f}};
    }

    if (linearized_) {
      // π_a − e^s π_b − c f̄ = r at the reference flow f₀.
      const auto [c, r] = gas_lin_coeffs(kw, f0_init, tangent_lin_);
      SparseRow row {
          .class_name = cname,
          .constraint_name = WeymouthName,
          .variable_uid = uid(),
          .context = ctx,
      };
      row[pa_cols.at(buid)] = 1.0;
      row[pb_cols.at(buid)] = -exp_s_;
      for (const auto& [coef, col] : flow_terms) {
        row[col] += -c * coef;
      }
      lin_rows[buid] = lp.add_row(std::move(row).equal(r));

      if (slp_prox > 0.0) {
        // Proximal term f̄ − d⁺ + d⁻ = f₀ with cost ρ (d⁺ + d⁻): among
        // equal-cost vertices the LP keeps the one nearest f₀, so a
        // degenerate SLP settles; d± → 0 at the fixed point.
        SparseRow prox {
            .class_name = cname,
            .constraint_name = ProximalName,
            .variable_uid = uid(),
            .context = ctx,
        };
        for (const auto& [coef, col] : flow_terms) {
          prox[col] += coef;
        }
        const double pcost =
            CostHelper::block_ecost(scenario, stage, block, slp_prox);
        for (const double sign : {-1.0, 1.0}) {
          const auto d = lp.add_col(SparseCol {
              .lowb = 0.0,
              .uppb = 2.0 * fmax,
              .cost = pcost,
              .class_name = cname,
              .variable_name = sign < 0.0 ? ProxUpName : ProxDownName,
              .variable_uid = uid(),
              .context = ctx,
          });
          prox[d] = sign;
        }
        prox_rows[buid] = lp.add_row(std::move(prox).equal(f0_init));
      }
      ref_flows[buid] = f0_init;
      pa_keep[buid] = pa_cols.at(buid);
      pb_keep[buid] = pb_cols.at(buid);
      continue;
    }

    // Weymouth outer approximation: π_a − e^s π_b − 2 K_e t_k f̄
    // ≥ −K_e t_k².
    std::vector<RowIndex> rows;
    rows.reserve(tpts.size());
    for (const auto& [k, t_k] : std::views::enumerate(tpts)) {
      SparseRow row {
          .class_name = cname,
          .constraint_name = WeymouthName,
          .variable_uid = uid(),
          .context = make_block_context(
              scen_uid, stage_uid, buid, static_cast<int>(k) + 1),
      };
      row[pa_cols.at(buid)] = 1.0;
      row[pb_cols.at(buid)] = -exp_s_;
      for (const auto& [coef, col] : flow_terms) {
        row[col] += -2.0 * kw * t_k * coef;
      }
      rows.push_back(lp.add_row(std::move(row).greater_equal(-kw * t_k * t_k)));
    }
    wrows[buid] = std::move(rows);
    pa_keep[buid] = pa_cols.at(buid);
    pb_keep[buid] = pb_cols.at(buid);
  }

  if (linepack && prev_lp && go.linepack_is_cyclic()) {
    // Cycle (gas_options.linepack_cyclic, default true): the stage
    // closes with at least its initial line pack.  Off: the pack may be
    // drawn down to the pressure bounds (outage studies).
    SparseRow end {
        .class_name = cname,
        .constraint_name = LinepackEndName,
        .variable_uid = uid(),
        .context = make_stage_context(scen_uid, stage_uid),
    };
    end[*prev_lp] = 1.0;
    std::ignore = lp.add_row(std::move(end).greater_equal(lp_ini));
  }

  tangents_[st_key] = std::move(tpts);
  if (linearized_) {
    lin_states_[st_key] = LinearizedState {
        .weymouth_rows = std::move(lin_rows),
        .proximal_rows = std::move(prox_rows),
        .reference_flows = std::move(ref_flows),
        .fmax = fmax,
    };
  }
  weymouth_rows_[st_key] = std::move(wrows);
  pi_a_cols_[st_key] = std::move(pa_keep);
  pi_b_cols_[st_key] = std::move(pb_keep);
  if (linepack) {
    flow_in_cols_[st_key] = std::move(fin_cols);
    flow_out_cols_[st_key] = std::move(fout_cols);
    linepack_cols_[st_key] = std::move(lp_cols);
    linepack_balance_rows_[st_key] = std::move(lp_bal_rows);
    sc.add_ampl_variable(ampl_name,
                         uid(),
                         FlowInName,
                         scenario,
                         stage,
                         flow_in_cols_.at(st_key));
    sc.add_ampl_variable(ampl_name,
                         uid(),
                         FlowOutName,
                         scenario,
                         stage,
                         flow_out_cols_.at(st_key));
    sc.add_ampl_variable(ampl_name,
                         uid(),
                         LinepackName,
                         scenario,
                         stage,
                         linepack_cols_.at(st_key));
    // With line pack the pipe has no single flow column: `flow` is the
    // mean (f_in + f_out)/2 — the f̄ the Weymouth rows are written on.
    BIndexHolder<std::vector<std::pair<ColIndex, double>>> mean_flow;
    map_reserve(mean_flow, blocks.size());
    const auto& fin_at = flow_in_cols_.at(st_key);
    const auto& fout_at = flow_out_cols_.at(st_key);
    for (const auto& [buid, fin] : fin_at) {
      mean_flow[buid] = {{fin, 0.5}, {fout_at.at(buid), 0.5}};
    }
    sc.add_ampl_variable(
        ampl_name, uid(), FlowName, scenario, stage, mean_flow);
  } else {
    flow_cols_[st_key] = std::move(fcols);
    sc.add_ampl_variable(
        ampl_name, uid(), FlowName, scenario, stage, flow_cols_.at(st_key));
  }
  return true;
}

int GasPipeLP::update_lp(SystemLP& sys,
                         const ScenarioLP& scenario,
                         const StageLP& stage)
{
  if (!linearized_) {
    return 0;
  }
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto it = lin_states_.find(st_key);
  if (it == lin_states_.end()) {
    return 0;
  }
  const auto& state = it->second;
  auto& li = sys.linear_interface();
  const auto& f0s = state.reference_flows;
  const bool lpk = flow_in_cols_.contains(st_key);
  int total = 0;
  // Always re-issue (no "unchanged" short-circuit): a low-memory
  // reconstruct restores the construction-time row, see
  // ReservoirSeepageLP::update_lp.
  for (const auto& [buid, row] : state.weymouth_rows) {
    const auto [c, r] = gas_lin_coeffs(k_eff_, f0s.at(buid), tangent_lin_);
    if (lpk) {
      li.set_coeff(row, flow_in_cols_.at(st_key).at(buid), -0.5 * c);
      li.set_coeff(row, flow_out_cols_.at(st_key).at(buid), -0.5 * c);
    } else {
      li.set_coeff(row, flow_cols_.at(st_key).at(buid), -c);
    }
    li.set_row_equal_to(row, r);
    ++total;
  }
  for (const auto& [buid, row] : state.proximal_rows) {
    li.set_row_equal_to(row, f0s.at(buid));
    ++total;
  }
  return total;
}

double GasPipeLP::capture_slp_point(const LinearInterface& li,
                                    const ScenarioLP& scenario,
                                    const StageLP& stage,
                                    const GasOptions& go)
{
  if (!linearized_ || !li.is_optimal()) {
    return 0.0;
  }
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto it = lin_states_.find(st_key);
  if (it == lin_states_.end()) {
    return 0.0;
  }
  auto& state = it->second;
  const auto sol = li.get_col_sol();
  const double omega = go.slp_omega();
  const double fmin = go.slp_min_flow_fraction() * state.fmax;
  const bool lpk = flow_in_cols_.contains(st_key);
  double worst = 0.0;
  // Keys come from the (const) row map; f₀ is updated through `at`, so
  // no flat_map value is mutated through an iteration proxy.
  for (const auto& [buid, row] : state.weymouth_rows) {
    auto& f0 = state.reference_flows.at(buid);
    const double f = lpk ? 0.5
            * (sol[flow_in_cols_.at(st_key).at(buid)]
               + sol[flow_out_cols_.at(st_key).at(buid)])
                         : sol[flow_cols_.at(st_key).at(buid)];
    // A flow inside the ±fmin floor on both passes has settled: its
    // sign is degenerate (the pressures do not pin it), and any value
    // there moves the Weymouth row by at most K_e (2 fmin)².
    if (std::abs(f) > fmin || std::abs(f0) > fmin) {
      worst = std::max(worst, std::abs(f - f0) / std::max(std::abs(f0), fmin));
    }
    f0 = gas_clamp_ref_flow(f0 + (omega * (f - f0)), fmin);
  }
  return worst;
}

bool GasPipeLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  // Exactness report: gap = π_a − e^s π_b − K_e f̄|f̄| [bar²] and the
  // outlet-pressure error p_b − √((π_a − K_e f̄|f̄|)/e^s) [bar] (> 0:
  // the LP outlet pressure is optimistic; the tangent error is at most
  // K_e (F/2N)²).
  STBIndexHolder<double> gaps;
  STBIndexHolder<double> perr;
  double worst = 0.0;
  double worst_gap = 0.0;
  if (out.emit_solution(cname)) {
    for (const auto& [st_key, pa_map] : pi_a_cols_) {
      const auto& pb_map = pi_b_cols_.at(st_key);
      const bool lpk = flow_in_cols_.contains(st_key);
      BIndexHolder<double> gv;
      BIndexHolder<double> ev;
      map_reserve(gv, pa_map.size());
      map_reserve(ev, pa_map.size());
      for (const auto& [buid, pa_col] : pa_map) {
        const double f = lpk ? 0.5
                * (out.primal(flow_in_cols_.at(st_key).at(buid))
                   + out.primal(flow_out_cols_.at(st_key).at(buid)))
                             : out.primal(flow_cols_.at(st_key).at(buid));
        const double pi_a = out.primal(pa_col);
        const double pi_b = out.primal(pb_map.at(buid));
        const double drop = k_eff_ * f * std::abs(f);
        const double gap = pi_a - (exp_s_ * pi_b) - drop;
        const double p_b = std::sqrt(std::max(0.0, pi_b));
        const double p_b_exact =
            std::sqrt(std::max(0.0, (pi_a - drop) / exp_s_));
        const double err = p_b - p_b_exact;
        gv[buid] = gap;
        ev[buid] = err;
        worst = std::max(worst, std::abs(err));
        worst_gap = std::max(worst_gap, std::abs(gap));
      }
      if (!gv.empty()) {
        gaps[st_key] = std::move(gv);
        perr[st_key] = std::move(ev);
      }
    }
  }
  if (!gaps.empty()) {
    out.add_col_sol_values(cname, WeymouthGapName, pid, gaps);
    out.add_col_sol_values(cname, PressureErrorName, pid, perr);
    constexpr double kWarnBar = 0.1;
    if (worst > kWarnBar) {
      SPDLOG_WARN(
          "GasPipe '{}': max Weymouth gap {:.4g} bar², outlet-pressure "
          "error {:.4f} bar (not exact: see GasPipe/pressure_error_sol)",
          gas_pipe().name,
          worst_gap,
          worst);
    } else {
      SPDLOG_DEBUG(
          "GasPipe '{}': max Weymouth gap {:.4g} bar², outlet-pressure "
          "error {:.4f} bar",
          gas_pipe().name,
          worst_gap,
          worst);
    }
  }
  if (!flow_cols_.empty()) {
    out.add_col_sol(cname, FlowName, pid, flow_cols_);
  }
  if (!flow_in_cols_.empty()) {
    out.add_col_sol(cname, FlowInName, pid, flow_in_cols_);
    out.add_col_sol(cname, FlowOutName, pid, flow_out_cols_);
    out.add_col_sol(cname, LinepackName, pid, linepack_cols_);
    out.add_row_dual(cname, LinepackBalanceName, pid, linepack_balance_rows_);
  }
  return true;
}

}  // namespace gtopt
