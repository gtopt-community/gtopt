/**
 * @file      hydrogen_demand_lp.hpp
 * @brief     LP wrapper for ``HydrogenDemand`` — hydrogen offtake columns
 * @date      Fri Nov 20 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per (scenario, stage, block): one ``load`` column in
 * ``[lmin, lmax]`` (MW_LHV) with cost ``−price · dur`` stamped with
 * coefficient ``−1`` into the HydrogenNode balance row.
 * Per (scenario, stage): optional ``min_energy`` (≥, with an optional
 * shortfall slack priced at ``min_energy_cost``) and ``max_energy``
 * (≤) rows on ``Σ_b dur_b · load_b``.
 */

#pragma once

#include <gtopt/hydrogen_demand.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/schedule.hpp>
#include <gtopt/stage_lp.hpp>

namespace gtopt
{

class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class HydrogenDemandLP : public ObjectLP<HydrogenDemand>
{
public:
  using Base = ObjectLP<HydrogenDemand>;

  static constexpr std::string_view LoadName {"load"};
  static constexpr std::string_view MinEnergyName {"min_energy"};
  static constexpr std::string_view MaxEnergyName {"max_energy"};
  static constexpr std::string_view MinEnergySlackName {"min_energy_slack"};

  explicit HydrogenDemandLP(const HydrogenDemand& hd, const InputContext& ic);

  [[nodiscard]] constexpr auto&& hydrogen_demand(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] const auto& load_cols_at(const ScenarioLP& scenario,
                                         const StageLP& stage) const
  {
    return load_cols_.at({scenario.uid(), stage.uid()});
  }

  /// @name Parameter accessors for PAMPL user-constraint resolution
  /// @{
  [[nodiscard]] auto param_lmax(StageUid s, BlockUid b) const
  {
    return lmax_.at(s, b);
  }
  [[nodiscard]] auto param_lmin(StageUid s, BlockUid b) const
  {
    return lmin_.at(s, b);
  }
  [[nodiscard]] auto param_price(StageUid s, BlockUid b) const
  {
    return price_.at(s, b);
  }
  [[nodiscard]] auto param_min_energy(StageUid s) const
  {
    return min_energy_.at(s);
  }
  [[nodiscard]] auto param_max_energy(StageUid s) const
  {
    return max_energy_.at(s);
  }
  [[nodiscard]] auto param_min_energy_cost(StageUid s) const
  {
    return min_energy_cost_.at(s);
  }
  /// @}

private:
  OptTBRealSched lmax_;
  OptTBRealSched lmin_;
  OptTBRealSched price_;
  OptTRealSched min_energy_;
  OptTRealSched max_energy_;
  OptTRealSched min_energy_cost_;

  STBIndexHolder<ColIndex> load_cols_;
  STIndexHolder<RowIndex> min_energy_rows_;
  STIndexHolder<RowIndex> max_energy_rows_;
  STIndexHolder<ColIndex> min_energy_slack_cols_;
};

using HydrogenDemandLPId = ObjectId<class HydrogenDemandLP>;
using HydrogenDemandLPSId = ObjectSingleId<class HydrogenDemandLP>;

static_assert(HydrogenDemandLP::Element::class_name
                  == LPClassName {"HydrogenDemand"},
              "HydrogenDemand::class_name must remain \"HydrogenDemand\"");

}  // namespace gtopt
