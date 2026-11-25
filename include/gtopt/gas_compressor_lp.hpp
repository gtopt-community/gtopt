/**
 * @file      gas_compressor_lp.hpp
 * @brief     LP wrapper for ``GasCompressor`` — ratio rows + fuel model
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per block:
 *
 *   flow[b] ∈ [0, F]                     +1 at discharge, −1 at suction
 *   ratio[b]:     π_out − β̄² π_in ≤ 0
 *   noexpand[b]:  π_out − π_in ≥ 0        (unless allow_bypass)
 *
 *   fixed_fraction, gas drive:   −γ_ff flow at suction (no column)
 *   saldarriaga:  fuel[b] ∈ [0, F],
 *                 fuel_plane_j[b]: fuel − c_Ψ flow − c_π π_out
 *                                  + c_π r0 π_in ≥ 0
 *                 gas drive: −fuel at suction
 *   electric drive: power[b] ∈ [0, power_max], −1 at the bus,
 *                 motor[b]: power − κ · (fuel | γ_ff flow) = 0
 *
 * Outputs ``GasCompressor/flow_sol``, ``fuel_sol`` (km³/h, gas or
 * electric-equivalent), ``ratio_sol`` (√(π_out/π_in)) and
 * ``power_sol`` (MW, electric drive).
 */

#pragma once

#include <gtopt/gas_compressor.hpp>
#include <gtopt/gas_physics.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class GasCompressorLP : public ObjectLP<GasCompressor>
{
public:
  static constexpr std::string_view FlowName {"flow"};
  static constexpr std::string_view FuelName {"fuel"};
  static constexpr std::string_view PowerName {"power"};
  static constexpr std::string_view RatioName {"ratio"};
  static constexpr std::string_view NoExpandName {"noexpand"};
  static constexpr std::string_view FuelPlaneName {"fuel_plane"};
  static constexpr std::string_view MotorName {"motor"};
  static constexpr std::string_view GasNodeInKey {"gas_node_in"};
  static constexpr std::string_view GasNodeOutKey {"gas_node_out"};

  /// Kept as a nested alias for existing callers.
  using FuelModel = GasCompressorFuelModel;

  explicit GasCompressorLP(const GasCompressor& cs, const InputContext& ic);

  [[nodiscard]] constexpr auto&& gas_compressor(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] FuelModel fuel_model() const noexcept;
  [[nodiscard]] bool is_electric() const noexcept;
  [[nodiscard]] double max_ratio() const noexcept
  {
    return gas_compressor().max_ratio.value_or(1.0);
  }
  /// Saldarriaga α.
  [[nodiscard]] double alpha() const noexcept
  {
    return gas_compressor().alpha.value_or(gas::kAlpha);
  }
  /// Saldarriaga γ (explicit or adiabatic-head value).
  [[nodiscard]] double gamma() const;
  /// Fixed fuel fraction γ_ff (explicit or γ (β̄^α − 1)).
  [[nodiscard]] double fixed_fraction() const;
  /// Electric conversion κ [MW per km³/h].
  [[nodiscard]] double kappa() const;

  [[nodiscard]] const auto& flow_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return flow_cols_.at({scenario.uid(), stage.uid()});
  }
  [[nodiscard]] const auto& fuel_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return fuel_cols_.at({scenario.uid(), stage.uid()});
  }
  [[nodiscard]] const auto& power_cols_at(const ScenarioLP& scenario,
                                          const StageLP& stage) const
  {
    return power_cols_.at({scenario.uid(), stage.uid()});
  }

  /// @name Parameter accessors for PAMPL user-constraint resolution
  /// @{
  [[nodiscard]] auto param_fmax(StageUid s) const { return fmax_.at(s); }
  [[nodiscard]] std::optional<double> param_max_ratio() const noexcept
  {
    return gas_compressor().max_ratio;
  }
  [[nodiscard]] std::optional<double> param_power_max() const noexcept
  {
    return gas_compressor().power_max;
  }
  /// @}

private:
  OptTRealSched fmax_;

  STBIndexHolder<ColIndex> flow_cols_;
  STBIndexHolder<ColIndex> fuel_cols_;
  STBIndexHolder<ColIndex> power_cols_;
  STBIndexHolder<RowIndex> ratio_rows_;
  /// Suction / discharge π columns (for the ratio output).
  STBIndexHolder<ColIndex> pi_in_cols_;
  STBIndexHolder<ColIndex> pi_out_cols_;
};

using GasCompressorLPId = ObjectId<class GasCompressorLP>;
using GasCompressorLPSId = ObjectSingleId<class GasCompressorLP>;

static_assert(GasCompressorLP::Element::class_name
                  == LPClassName {"GasCompressor"},
              "GasCompressor::class_name must remain \"GasCompressor\"");

}  // namespace gtopt
