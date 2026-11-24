/**
 * @file      gas_turbine_lp.hpp
 * @brief     LP wrapper for ``GasTurbine`` — fuel term + pressure gate
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per block, on the linked generator column ``P`` and the gas node π:
 *
 *   GasNode balance:  −heat_rate · P
 *   pressure_gate[b]: P − s₁ π ≤ −s₁ (p_min − band)²       (min_pressure)
 *   derate[b]:        P − s₂ π ≤ out_min pmax − s₂ p_min²  (nominal_pressure)
 *
 * No columns.  Outputs ``GasTurbine/fuel_sol`` (heat_rate · P, km³/h)
 * and ``pressure_gate_dual`` / ``derate_dual``.
 */

#pragma once

#include <gtopt/gas_turbine.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class GasTurbineLP : public ObjectLP<GasTurbine>
{
public:
  static constexpr std::string_view FuelName {"fuel"};
  static constexpr std::string_view PressureGateName {"pressure_gate"};
  static constexpr std::string_view DerateName {"derate"};

  explicit GasTurbineLP(const GasTurbine& gt, const InputContext& ic);

  [[nodiscard]] constexpr auto&& gas_turbine(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] const auto& gate_rows_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return gate_rows_.at({scenario.uid(), stage.uid()});
  }

private:
  OptTBRealSched heat_rate_;

  STBIndexHolder<ColIndex> gen_cols_;
  STBIndexHolder<double> heat_rates_;
  STBIndexHolder<RowIndex> gate_rows_;
  STBIndexHolder<RowIndex> derate_rows_;
};

using GasTurbineLPId = ObjectId<class GasTurbineLP>;
using GasTurbineLPSId = ObjectSingleId<class GasTurbineLP>;

static_assert(GasTurbineLP::Element::class_name == LPClassName {"GasTurbine"},
              "GasTurbine::class_name must remain \"GasTurbine\"");

}  // namespace gtopt
