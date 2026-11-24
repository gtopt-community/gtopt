/**
 * @file      gas_demand_lp.hpp
 * @brief     LP wrapper for ``GasDemand`` — load RHS + shedding column
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per block: the GasNode balance right-hand side grows by ``load[b]``;
 * with ``fail_cost`` set a shedding column ``fail[b] ∈ [0, load[b]]``
 * (cost ``fail_cost · dur``) enters the balance with ``+1``.
 * Outputs ``GasDemand/load_sol`` and ``GasDemand/fail_sol``.
 */

#pragma once

#include <gtopt/gas_demand.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class GasDemandLP : public ObjectLP<GasDemand>
{
public:
  static constexpr std::string_view LoadName {"load"};
  static constexpr std::string_view FailName {"fail"};

  explicit GasDemandLP(const GasDemand& demand, const InputContext& ic);

  [[nodiscard]] constexpr auto&& gas_demand(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] const auto& fail_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return fail_cols_.at({scenario.uid(), stage.uid()});
  }

private:
  OptTBRealSched load_;
  OptTRealSched fail_cost_;

  STBIndexHolder<ColIndex> fail_cols_;
  STBIndexHolder<double> load_values_;
};

using GasDemandLPId = ObjectId<class GasDemandLP>;
using GasDemandLPSId = ObjectSingleId<class GasDemandLP>;

static_assert(GasDemandLP::Element::class_name == LPClassName {"GasDemand"},
              "GasDemand::class_name must remain \"GasDemand\"");

}  // namespace gtopt
