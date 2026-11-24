/**
 * @file      gas_supply_lp.hpp
 * @brief     LP wrapper for ``GasSupply`` — priced injection column
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per block: ``flow[b] ∈ [fmin, fmax]`` with cost ``cost · dur``,
 * stamped ``+1`` into the GasNode balance.  Output ``GasSupply/flow_sol``.
 */

#pragma once

#include <gtopt/gas_supply.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class GasSupplyLP : public ObjectLP<GasSupply>
{
public:
  static constexpr std::string_view VolumeName {"volume"};
  static constexpr std::string_view FlowName {"flow"};

  explicit GasSupplyLP(const GasSupply& supply, const InputContext& ic);

  [[nodiscard]] constexpr auto&& gas_supply(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] const auto& flow_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return flow_cols_.at({scenario.uid(), stage.uid()});
  }

private:
  OptTBRealSched fmin_;
  OptTBRealSched fmax_;
  OptTRealSched cost_;

  STBIndexHolder<ColIndex> flow_cols_;
  STIndexHolder<RowIndex> volume_rows_;  ///< Stage `volume` cap rows
};

using GasSupplyLPId = ObjectId<class GasSupplyLP>;
using GasSupplyLPSId = ObjectSingleId<class GasSupplyLP>;

static_assert(GasSupplyLP::Element::class_name == LPClassName {"GasSupply"},
              "GasSupply::class_name must remain \"GasSupply\"");

}  // namespace gtopt
