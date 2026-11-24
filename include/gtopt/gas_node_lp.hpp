/**
 * @file      gas_node_lp.hpp
 * @brief     LP wrapper for ``GasNode`` — π column + gas balance row
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per (scenario, stage, block):
 *
 *   pi[b] ∈ [pmin², pmax²]           cost pressure_cost · dur  [bar²]
 *   balance[b]:  Σ inflows − Σ outflows = Σ loads   (dual: gas price)
 *
 * Outputs: ``GasNode/pi_sol``, ``GasNode/pressure_sol`` (√π, bar) and
 * ``GasNode/balance_dual`` [$ / km³].
 */

#pragma once

#include <gtopt/gas_node.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/schedule.hpp>
#include <gtopt/single_id.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class GasNodeLP : public ObjectLP<GasNode>
{
public:
  static constexpr std::string_view PiName {"pi"};
  static constexpr std::string_view PressureName {"pressure"};
  static constexpr std::string_view BalanceName {"balance"};

  explicit GasNodeLP(const GasNode& node, const InputContext& ic);

  [[nodiscard]] constexpr auto&& gas_node(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] const auto& balance_rows_at(const ScenarioLP& scenario,
                                            const StageLP& stage) const
  {
    return balance_rows_.at({scenario.uid(), stage.uid()});
  }

  [[nodiscard]] const auto& pi_cols_at(const ScenarioLP& scenario,
                                       const StageLP& stage) const
  {
    return pi_cols_.at({scenario.uid(), stage.uid()});
  }

  /// Minimum pressure [bar] at a stage (default 0).
  [[nodiscard]] double pmin_at(StageUid s) const
  {
    return pmin_.at(s).value_or(0.0);
  }
  /// Maximum pressure [bar] at a stage (nullopt = unbounded).
  [[nodiscard]] std::optional<double> pmax_at(StageUid s) const
  {
    return pmax_.at(s);
  }
  /// Elevation [m] (default 0).
  [[nodiscard]] double elevation() const noexcept
  {
    return gas_node().elevation.value_or(0.0);
  }

  /// Initial pressure [bar] (nullopt = unset).
  [[nodiscard]] std::optional<double> p_init() const noexcept
  {
    return gas_node().p_init;
  }

  /// @name Parameter accessors for PAMPL user-constraint resolution
  /// @{
  [[nodiscard]] auto param_pmin(StageUid s) const { return pmin_.at(s); }
  [[nodiscard]] auto param_pmax(StageUid s) const { return pmax_.at(s); }
  /// @}

private:
  OptTRealSched pmin_;
  OptTRealSched pmax_;
  OptTRealSched pressure_cost_;

  STBIndexHolder<ColIndex> pi_cols_;
  STBIndexHolder<RowIndex> balance_rows_;
};

using GasNodeLPId = ObjectId<class GasNodeLP>;
using GasNodeLPSId = ObjectSingleId<class GasNodeLP>;

static_assert(GasNodeLP::Element::class_name == LPClassName {"GasNode"},
              "GasNode::class_name must remain \"GasNode\"");

}  // namespace gtopt
