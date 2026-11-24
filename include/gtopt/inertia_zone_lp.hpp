/**
 * @file      inertia_zone_lp.hpp
 * @brief     LP formulation for inertia zones
 * @date      2026-04-13
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the InertiaZoneLP class, which builds LP variables
 * and constraints for system inertia zone requirements.  Mirrors the
 * ReserveZoneLP pattern with a single requirement direction.
 */

#pragma once

#include <vector>

#include <gtopt/generator_lp.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/inertia_zone.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/stage_lp.hpp>

namespace gtopt
{

class InertiaZoneLP : public ObjectLP<InertiaZone>
{
public:
  static constexpr std::string_view RequirementName {"requirement"};
  /// PAMPL-visible alias for the requirement columns.
  static constexpr std::string_view ReqName {"req"};
  /// Commitment-credit selector column `y` and its link row
  /// `units·y − Σ u ≤ 0` (`requirement_credits`).
  static constexpr std::string_view CreditName {"credit"};

  using Base = ObjectLP<InertiaZone>;

  explicit InertiaZoneLP(const InertiaZone& inertia_zone,
                         const InputContext& ic);

  [[nodiscard]] constexpr auto&& inertia_zone(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(const SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] auto&& requirement_rows() const { return rr.requirement_rows; }
  [[nodiscard]] auto&& requirement_cols() const { return rr.requirement_cols; }

  /// Look up the requirement column for (scenario, stage, block).
  /// Delegates to the shared `lookup_inner` helper (`index_holder.hpp`).
  [[nodiscard]] std::optional<ColIndex> lookup_requirement_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(rr.requirement_cols, scenario, stage, buid);
  }

private:
  /// Resolved InertiaRequirementCredit.
  struct Credit
  {
    std::vector<ElementIndex<GeneratorLP>> generators;
    double units {1.0};
    OptTBRealSched credit;
    bool integer {false};
  };

  /// Adds the credit columns / link rows of one (scenario, stage) and
  /// stamps `credit·y` into the requirement rows.
  void add_credits(const SystemContext& sc,
                   const ScenarioLP& scenario,
                   const StageLP& stage,
                   LinearProblem& lp);

  /// Validates (once) that every credit generator has a commitment
  /// element; strict mode throws, normal mode warns.
  void check_credit_generators(const SystemContext& sc, const StageLP& stage);

  std::vector<Credit> credits_;
  bool credits_checked_ {false};
  STBIndexHolder<std::vector<ColIndex>> credit_cols_;

  struct Requirement
  {
    Requirement(const InputContext& ic,
                std::string_view cname,
                const Id& id,
                auto&& rreq,
                auto&& rcost);

    OptTBRealSched req;
    OptTBRealSched cost;
    STBIndexHolder<ColIndex> requirement_cols;
    STBIndexHolder<RowIndex> requirement_rows;
  } rr;
};

// Pin the data-struct constant value so an accidental rename of the
// `InertiaZone::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"InertiaZone"`).
static_assert(InertiaZoneLP::Element::class_name == LPClassName {"InertiaZone"},
              "InertiaZone::class_name must remain \"InertiaZone\"");

}  // namespace gtopt
