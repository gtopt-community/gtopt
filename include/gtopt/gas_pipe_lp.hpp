/**
 * @file      gas_pipe_lp.hpp
 * @brief     LP wrapper for ``GasPipe`` — Weymouth tangents + line pack
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Steady state, per block:
 *
 *   flow[b] ∈ [0, F]            −1 at node a, +1 at node b
 *   weymouth_k[b]:  π_a − π_b − 2 K t_k flow ≥ −K t_k²   (k = 1..N)
 *
 * With line pack, per block:
 *
 *   flow_in[b], flow_out[b] ∈ [0, F]   (−flow_in at a, +flow_out at b)
 *   weymouth_k[b] on f̄ = (flow_in + flow_out)/2
 *   linepack_def[b]:  LP − α (π_a + π_b) = β,  α = c/(4 p_r), β = c p_r/2
 *   linepack_balance[b]:  Δt (flow_in − flow_out) − LP[b] + LP[b−1] = 0
 *                         (LP[0] = linepack_ini)
 *   linepack_end:  LP[last] ≥ linepack_ini
 *
 * ``weymouth_mode: linearized`` (meshed networks): flows are signed in
 * [−F, F] and the N tangents become one equality per block,
 *
 *   weymouth[b]:  π_a − e^s π_b − c f̄ = r
 *     secant:  c = K_e |f₀|,   r = 0
 *     tangent: c = 2 K_e |f₀|, r = −K_e f₀|f₀|
 *
 * re-linearised at the last solution by ``update_lp`` (the SLP of
 * ``gas_options.slp_iterations``, see ``gas_slp.hpp``).
 *
 * Outputs ``GasPipe/flow_sol`` (steady) or ``flow_in_sol``,
 * ``flow_out_sol``, ``linepack_sol``; ``linepack_balance_dual``; the
 * exactness report ``weymouth_gap_sol`` [bar²] and
 * ``pressure_error_sol`` [bar].
 */

#pragma once

#include <vector>

#include <gtopt/gas_options.hpp>
#include <gtopt/gas_pipe.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

class InputContext;
class LinearInterface;
class LinearProblem;
class SystemLP;
class OutputContext;
class SystemContext;

class GasPipeLP : public ObjectLP<GasPipe>
{
public:
  static constexpr std::string_view FlowName {"flow"};
  static constexpr std::string_view FlowInName {"flow_in"};
  static constexpr std::string_view FlowOutName {"flow_out"};
  static constexpr std::string_view LinepackName {"linepack"};
  static constexpr std::string_view WeymouthName {"weymouth"};
  static constexpr std::string_view LinepackDefName {"linepack_def"};
  static constexpr std::string_view LinepackBalanceName {"linepack_balance"};
  static constexpr std::string_view LinepackEndName {"linepack_end"};
  static constexpr std::string_view ProximalName {"slp_proximal"};
  static constexpr std::string_view ProxUpName {"slp_dev_up"};
  static constexpr std::string_view ProxDownName {"slp_dev_down"};
  static constexpr std::string_view WeymouthGapName {"weymouth_gap"};
  static constexpr std::string_view PressureErrorName {"pressure_error"};

  explicit GasPipeLP(const GasPipe& pipe, const InputContext& ic);

  [[nodiscard]] constexpr auto&& gas_pipe(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  /// Linearised mode: re-issue the flow coefficient and RHS of every
  /// block's equality and of its proximal row from the stored reference
  /// flows f₀ (always
  /// written, so a low-memory reconstruct that restores the
  /// construction-time matrix is corrected before the next solve; the
  /// writes are recorded by the LP replay buffer).  Radial pipes: no-op.
  [[nodiscard]] int update_lp(SystemLP& sys,
                              const ScenarioLP& scenario,
                              const StageLP& stage);

  /// Linearised mode: move every f₀ of (scenario, stage) towards the
  /// solved flow, f₀ ← f₀ + ω (f − f₀) with |f₀| ≥ f_min · fmax, and
  /// return the largest relative change |f − f₀| / max(|f₀|, f_min ·
  /// fmax) (0 for a flow inside ±f_min · fmax on both passes).  Reads
  /// the solution only (call before any coefficient write, which drops
  /// the solution cache).  Same per-(scenario, stage) shape as
  /// ``update_lp``.  Radial pipes and cells without the pipe return 0.
  [[nodiscard]] double capture_slp_point(const LinearInterface& li,
                                         const ScenarioLP& scenario,
                                         const StageLP& stage,
                                         const GasOptions& go);

  [[nodiscard]] bool is_linearized() const noexcept { return linearized_; }

  /// Reference flows f₀ per block of (scenario, stage) (linearised).
  [[nodiscard]] const auto& reference_flows_at(const ScenarioLP& scenario,
                                               const StageLP& stage) const
  {
    return lin_states_.at({scenario.uid(), stage.uid()}).reference_flows;
  }

  /// Weymouth constant K [bar² / (km³/h)²] (explicit or from geometry,
  /// gas properties defaulting to ``gas_options``).
  [[nodiscard]] double weymouth_k(const GasOptions& go = {}) const;

  /// Line-pack coefficient c [km³ / bar] (explicit or from geometry).
  [[nodiscard]] double linepack_coeff(const GasOptions& go = {}) const;

  /// Elevation exponent ``s`` of the pipe (0 on flat ground).
  [[nodiscard]] double elevation_exponent(double h_a,
                                          double h_b,
                                          const GasOptions& go = {}) const;

  /// ``e^s`` and ``K_e = K (e^s − 1)/s`` set by ``add_to_lp``.
  [[nodiscard]] double outlet_factor() const noexcept { return exp_s_; }
  [[nodiscard]] double effective_k() const noexcept { return k_eff_; }

  [[nodiscard]] bool has_linepack() const noexcept
  {
    return gas_pipe().linepack.value_or(false);
  }

  [[nodiscard]] const auto& flow_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return flow_cols_.at({scenario.uid(), stage.uid()});
  }
  [[nodiscard]] const auto& flow_in_cols_at(const ScenarioLP& scenario,
                                            const StageLP& stage) const
  {
    return flow_in_cols_.at({scenario.uid(), stage.uid()});
  }
  [[nodiscard]] const auto& flow_out_cols_at(const ScenarioLP& scenario,
                                             const StageLP& stage) const
  {
    return flow_out_cols_.at({scenario.uid(), stage.uid()});
  }
  [[nodiscard]] const auto& linepack_cols_at(const ScenarioLP& scenario,
                                             const StageLP& stage) const
  {
    return linepack_cols_.at({scenario.uid(), stage.uid()});
  }
  /// Tangent rows (k = 1..N) per block.
  [[nodiscard]] const auto& weymouth_rows_at(const ScenarioLP& scenario,
                                             const StageLP& stage) const
  {
    return weymouth_rows_.at({scenario.uid(), stage.uid()});
  }
  /// Tangent points t_k used for (scenario, stage).
  [[nodiscard]] const auto& tangent_points_at(const ScenarioLP& scenario,
                                              const StageLP& stage) const
  {
    return tangents_.at({scenario.uid(), stage.uid()});
  }

private:
  OptTRealSched fmax_;
  double exp_s_ {1.0};
  double k_eff_ {0.0};
  /// π columns of the end nodes (for the exactness report).
  STBIndexHolder<ColIndex> pi_a_cols_;
  STBIndexHolder<ColIndex> pi_b_cols_;

  /// Linearised-mode state of one (scenario, stage), written by
  /// ``add_to_lp`` and read / advanced by ``update_lp`` and
  /// ``capture_slp_point`` (cf. ``ReservoirSeepageLP``'s state).
  struct LinearizedState
  {
    /// π_a − e^s π_b − c f̄ = r per block.
    BIndexHolder<RowIndex> weymouth_rows;
    /// SLP proximal rows f̄ − d⁺ + d⁻ = f₀ (empty without SLP).
    BIndexHolder<RowIndex> proximal_rows;
    /// Reference flows f₀ per block.
    BIndexHolder<double> reference_flows;
    /// Pipe capacity of the stage (scales the f₀ floor).
    double fmax {0.0};
  };

  // Linearised mode.
  bool linearized_ {false};
  bool tangent_lin_ {false};
  STIndexHolder<LinearizedState> lin_states_;

  STBIndexHolder<ColIndex> flow_cols_;
  STBIndexHolder<ColIndex> flow_in_cols_;
  STBIndexHolder<ColIndex> flow_out_cols_;
  STBIndexHolder<ColIndex> linepack_cols_;
  STBIndexHolder<std::vector<RowIndex>> weymouth_rows_;
  STBIndexHolder<RowIndex> linepack_balance_rows_;
  STIndexHolder<std::vector<double>> tangents_;
};

using GasPipeLPId = ObjectId<class GasPipeLP>;
using GasPipeLPSId = ObjectSingleId<class GasPipeLP>;

static_assert(GasPipeLP::Element::class_name == LPClassName {"GasPipe"},
              "GasPipe::class_name must remain \"GasPipe\"");

}  // namespace gtopt
