/**
 * @file      bus_lp.hpp
 * @brief     Linear Programming representation of a Bus for optimization
 * problems
 * @date      Mon Mar 24 09:40:04 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * @details
 * The BusLP class provides a linear programming (LP) compatible representation
 * of a Bus, which is a fundamental component for power system optimization.
 * It maintains the bus's electrical properties and provides methods for LP
 * formulation while using modern C++23 features for better ergonomics.
 *
 * @note Uses C++23 features including deducing this and structured bindings
 */

#pragma once

#include <gtopt/bus.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/single_id.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

class BusLP : public ObjectLP<Bus>
{
public:
  static constexpr std::string_view BalanceName {"balance"};
  static constexpr std::string_view ThetaName {"theta"};
  /// LinDistFlow squared voltage magnitude `w = |V|²` [p.u.²].
  static constexpr std::string_view VsqName {"vsq"};
  /// LinDistFlow reactive-power balance row.
  static constexpr std::string_view QBalanceName {"qbalance"};
  /// Metadata key published by `add_to_lp` so user-constraint filters
  /// can match buses by their `type` field (e.g. `bus[type="hydro"]`).
  static constexpr std::string_view TypeKey {"type"};

  /// Constructs a BusLP from a Bus and input context
  /// @param pbus The bus to wrap
  /// @param ic Input context for LP construction
  [[nodiscard]]
  explicit constexpr BusLP(const Bus& pbus, const InputContext& ic)
      : ObjectLP<Bus>(pbus, ic, Element::class_name)
  {
  }

  [[nodiscard]]
  constexpr const auto& bus() const noexcept
  {
    return object();
  }

  [[nodiscard]]
  constexpr auto reference_theta() const noexcept
  {
    return bus().reference_theta;
  }

  [[nodiscard]]
  constexpr auto voltage() const noexcept
  {
    return bus().voltage.value_or(1.0);
  }

  [[nodiscard]]
  constexpr auto use_kirchhoff() const noexcept
  {
    return bus().use_kirchhoff.value_or(true);
  }

  [[nodiscard]]
  auto needs_kirchhoff(const SystemContext& sc) const -> bool;

  [[nodiscard]]
  bool add_to_lp(const SystemContext& sc,
                 const ScenarioLP& scenario,
                 const StageLP& stage,
                 LinearProblem& lp);

  [[nodiscard]]
  bool add_to_output(OutputContext& out) const;

  [[nodiscard]]
  constexpr const auto& balance_rows_at(const ScenarioLP& scenario,
                                        const StageLP& stage) const
  {
    return balance_rows.at({scenario.uid(), stage.uid()});
  }

  /// LinDistFlow `w` columns of (scenario, stage); empty when the
  /// model is off or the bus is inactive.
  [[nodiscard]] const BIndexHolder<ColIndex>& vsq_cols_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(vsq_cols, scenario, stage);
  }

  /// LinDistFlow reactive balance rows of (scenario, stage); empty when
  /// the model is off or the bus is inactive.
  [[nodiscard]] const BIndexHolder<RowIndex>& qbalance_rows_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(qbalance_rows, scenario, stage);
  }

  [[nodiscard]]
  constexpr auto theta_cols_at(const SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp,
                               const std::vector<BlockLP>& blocks) const
      -> const BIndexHolder<ColIndex>&
  {
    const auto key = std::tuple {scenario.uid(), stage.uid()};
    if (const auto it = theta_cols.find(key); it != theta_cols.end()) {
      return it->second;
    }
    return lazy_add_theta(sc, scenario, stage, lp, blocks);
  }

  /// Look up an already-created theta column without lazy initialisation.
  /// Returns std::nullopt if no theta column exists for this (scenario, stage,
  /// block) triple (e.g. single-bus or reference bus).  Delegates to the
  /// shared `lookup_inner` helper (`index_holder.hpp`).
  [[nodiscard]]
  std::optional<ColIndex> lookup_theta_col(const ScenarioLP& scenario,
                                           const StageLP& stage,
                                           BlockUid buid) const noexcept
  {
    return lookup_inner(theta_cols, scenario, stage, buid);
  }

private:
  void add_lindistflow(const ScenarioLP& scenario,
                       const StageLP& stage,
                       LinearProblem& lp);

  [[nodiscard]]
  auto lazy_add_theta(const SystemContext& sc,
                      const ScenarioLP& scenario,
                      const StageLP& stage,
                      LinearProblem& lp,
                      std::span<const BlockLP> blocks) const
      -> const BIndexHolder<ColIndex>&;

  STBIndexHolder<RowIndex> balance_rows;
  STBIndexHolder<ColIndex> vsq_cols;
  STBIndexHolder<RowIndex> qbalance_rows;
  mutable STBIndexHolder<ColIndex> theta_cols;
};

using BusLPId = ObjectId<class BusLP>;
using BusLPSId = ObjectSingleId<class BusLP>;

// Pin the data-struct constant value so an accidental rename of the
// `Bus::class_name` literal fails the build (LP row labels and CSV
// outputs depend on the exact string `"Bus"`).
static_assert(BusLP::Element::class_name == LPClassName {"Bus"},
              "Bus::class_name must remain \"Bus\"");

}  // namespace gtopt
