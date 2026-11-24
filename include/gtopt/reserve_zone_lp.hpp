/**
 * @file      reserve_zone_lp.hpp
 * @brief     LP formulation for reserve zones
 * @date      Mon Apr 21 22:23:18 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the ReserveZoneLP class, which builds LP variables
 * and constraints for operating reserve zone requirements.
 *
 * Two requirement forms per direction (up / down):
 *
 * - **fixed** (`urreq` / `urmin`): `Σ pf·prov ≥ max(req, min)` — soft
 *   via a bounded "served" column priced at `−urcost`, or hard.
 * - **dynamic** (`urreq_planes` non-empty): an LP column `R` with
 *   `R ≥ plane_k` for every plane and `R ≥ max(0, req, min)`, plus the
 *   provision row `Σ pf·prov + s − R ≥ 0` whose shortage `s ≥ 0` is
 *   priced at `urcost` (hard `Σ pf·prov ≥ R` without a cost).
 *
 * Plane terms: the exogenous demand `D` goes to the row RHS; inertia
 * `I` (an equality-defined column fed by `InertiaProvisionLP`) and the
 * largest contingency `C ≥ p_g` (up) / `C ≥ load_d` (down) are columns.
 */

#pragma once

#include <array>
#include <optional>
#include <utility>
#include <vector>

#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/reserve_zone.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/stage_lp.hpp>

namespace gtopt
{

class ReserveZoneLP : public ObjectLP<ReserveZone>
{
public:
  static constexpr std::string_view UrequirementName {"urequirement"};
  static constexpr std::string_view DrequirementName {"drequirement"};
  /// Dynamic-requirement shortage columns (planes mode only).
  static constexpr std::string_view UrshortageName {"urshortage"};
  static constexpr std::string_view DrshortageName {"drshortage"};
  /// Surplus columns of an `requirement_sense: "eq"` balance.
  static constexpr std::string_view UrsurplusName {"ursurplus"};
  static constexpr std::string_view DrsurplusName {"drsurplus"};
  /// Accepted `ReserveZone::requirement_sense` values.
  static constexpr std::string_view SenseGe {"ge"};
  static constexpr std::string_view SenseEq {"eq"};
  /// Dynamic-requirement plane rows (`R − terms ≥ alpha + dc·D`).
  static constexpr std::string_view UrplaneName {"urplane"};
  static constexpr std::string_view DrplaneName {"drplane"};
  /// Output: index (1-based) of the plane that sets `R` at the solution
  /// (`urplane_binding_sol`), 0 when the floor `max(0, req, min)` does.
  static constexpr std::string_view UrplaneBindingName {"urplane_binding"};
  static constexpr std::string_view DrplaneBindingName {"drplane_binding"};
  /// Output stems of the per-plane row duals (`urplane<k>_dual`); planes
  /// beyond `max_output_planes` are solved but not written.
  static constexpr std::size_t max_output_planes = 24;
  static constexpr std::array<std::string_view, max_output_planes>
      UrplaneNames {
          "urplane1",  "urplane2",  "urplane3",  "urplane4",  "urplane5",
          "urplane6",  "urplane7",  "urplane8",  "urplane9",  "urplane10",
          "urplane11", "urplane12", "urplane13", "urplane14", "urplane15",
          "urplane16", "urplane17", "urplane18", "urplane19", "urplane20",
          "urplane21", "urplane22", "urplane23", "urplane24",
      };
  static constexpr std::array<std::string_view, max_output_planes>
      DrplaneNames {
          "drplane1",  "drplane2",  "drplane3",  "drplane4",  "drplane5",
          "drplane6",  "drplane7",  "drplane8",  "drplane9",  "drplane10",
          "drplane11", "drplane12", "drplane13", "drplane14", "drplane15",
          "drplane16", "drplane17", "drplane18", "drplane19", "drplane20",
          "drplane21", "drplane22", "drplane23", "drplane24",
      };
  /// Largest-contingency columns / rows (`C − p ≥ 0`).
  static constexpr std::string_view UrcontingencyName {"urcontingency"};
  static constexpr std::string_view DrcontingencyName {"drcontingency"};
  /// System inertia column / definition row (`I − Σ … = 0`).
  static constexpr std::string_view InertiaName {"inertia"};
  /// Fixed (data) part of the inertia term: a column with equal bounds,
  /// raised by every forced-on / must-run unit (`inertia_term`
  /// `"commitment"` and the `"exogenous"` must-run fallback).
  static constexpr std::string_view InertiaFixedName {"inertia_fixed"};
  /// Accepted `ReserveZone::inertia_term` values.
  static constexpr std::string_view InertiaTermCommitment {"commitment"};
  static constexpr std::string_view InertiaTermExogenous {"exogenous"};
  static constexpr std::string_view InertiaTermEndogenous {"endogenous"};
  enum class InertiaTerm : std::uint8_t
  {
    commitment,
    exogenous,
    endogenous,
  };
  /// Default plane inertia term (see `ReserveZone::inertia_term`):
  /// `commitment`, the CEN definition I = Σ I_c·u_c (I_c capped at the
  /// unit's pmax).  On the CEN PCP 2026-11-07 day it gives the realistic
  /// requirement (the must-run-only `exogenous` fallback inflates it,
  /// 505 vs 195 MW in PLEXOS) with the same negative-price hours as
  /// `exogenous` (515 vs 509 bus-hours, none below −0.1 $/MWh; the credit
  /// comes from the load term).  Known risk: with a RELAXED Commitment a
  /// cheap unit can raise u at pmin·u generation and earn I_c/pmin per
  /// MW, which can price a congested bus negative
  /// (test_reserve_inertia_term_default); validate_planning warns and
  /// points to `inertia_term: "exogenous"` as the remedy.
  static constexpr InertiaTerm DefaultInertiaTerm = InertiaTerm::commitment;
  /// LP scale of the inertia / system-load columns (`physical = LP ·
  /// scale`): the LP works in GWs / GW so the plane coefficients
  /// (~1e-5 per MWs) and the definition-row entries (~1..1e3) stay in a
  /// narrow range.  Invisible in the output (primal / duals unscaled).
  static constexpr double TermColScale = 1e3;
  /// Endogenous system-load column / definition row (PLEXOS-compat
  /// `load_term: "endogenous_generation"` only).
  static constexpr std::string_view DsysName {"dsys"};
  /// Accepted `ReserveZone::load_term` values.
  static constexpr std::string_view LoadTermExogenous {"exogenous"};
  /// Default `demand_load_factor` of the exogenous load term: 1.05, a
  /// conservative choice that over- rather than under-sizes the reserve
  /// (CEN PCP generation / demand ratio 1.025–1.05; recent days
  /// 1.044–1.052).  The PLEXOS-compat endogenous form defaults to 1.
  static constexpr std::string_view LoadTermEndogenousGeneration {
      "endogenous_generation"};
  static constexpr double DefaultDemandLoadFactor = 1.05;
  /// Output: requirement credit in the bus LMPs [$/MWh] — the uniform
  /// LMP shift `Σ_k demand_coef_k · lf · μ_k` of the endogenous load term
  /// (0 with the default exogenous term); `flow_credit` is the LMP spread
  /// `Σ_k flow_coef_k · μ_k` added across the `flow_lines` (B side minus
  /// A side); `credit_transfer` [$] = `reserve_credit · (D / lf) · Δt`,
  /// the money moved through the credit in the block.
  static constexpr std::string_view ReserveCreditName {"reserve_credit"};
  static constexpr std::string_view FlowCreditName {"flow_credit"};
  static constexpr std::string_view CreditTransferName {"credit_transfer"};
  /// PAMPL-visible aliases for the up/down requirement columns.
  /// Exposed under canonical short names so user constraints can write
  /// `reserve_zone("Z").up` instead of the verbose `urequirement`.
  static constexpr std::string_view UpName {"up"};
  static constexpr std::string_view DnName {"dn"};

  using Base = ObjectLP<ReserveZone>;

  explicit ReserveZoneLP(const ReserveZone& preserve_zone,
                         const InputContext& ic);

  [[nodiscard]] constexpr auto&& reserve_zone(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] bool add_to_lp(const SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  [[nodiscard]] auto&& urequirement_rows() const { return ur.requirement_rows; }
  [[nodiscard]] auto&& urequirement_cols() const { return ur.requirement_cols; }
  [[nodiscard]] auto&& drequirement_rows() const { return dr.requirement_rows; }
  [[nodiscard]] auto&& drequirement_cols() const { return dr.requirement_cols; }

  /// Look up the up-requirement column for (scenario, stage, block).
  /// Fixed mode: the "served requirement" column of a priced zone.
  /// Planes mode: the dynamic requirement `R` itself.  Returns
  /// std::nullopt when no such column exists for this period.
  [[nodiscard]] std::optional<ColIndex> lookup_urequirement_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(ur.requirement_cols, scenario, stage, buid);
  }

  /// Look up the down-requirement column for (scenario, stage, block).
  [[nodiscard]] std::optional<ColIndex> lookup_drequirement_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(dr.requirement_cols, scenario, stage, buid);
  }

  /// Planes mode: the up/down shortage column `s` (priced zones only).
  [[nodiscard]] std::optional<ColIndex> lookup_urshortage_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(ur.shortage_cols, scenario, stage, buid);
  }
  [[nodiscard]] std::optional<ColIndex> lookup_drshortage_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(dr.shortage_cols, scenario, stage, buid);
  }

  /// Planes mode: the up/down largest-contingency column `C`.
  [[nodiscard]] std::optional<ColIndex> lookup_urcontingency_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(ur.contingency_cols, scenario, stage, buid);
  }
  [[nodiscard]] std::optional<ColIndex> lookup_drcontingency_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(dr.contingency_cols, scenario, stage, buid);
  }

  /// Planes mode: the system inertia column `I` and its definition row
  /// `I − Σ pf·r = 0` (InertiaProvisionLP stamps `−pf` into the row).
  [[nodiscard]] std::optional<ColIndex> lookup_inertia_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(inertia_cols_, scenario, stage, buid);
  }
  [[nodiscard]] std::optional<RowIndex> lookup_inertia_row(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(inertia_rows_, scenario, stage, buid);
  }

  /// Fixed-inertia column of (scenario, stage, block) — present for the
  /// `"commitment"` term and the `"exogenous"` must-run fallback.
  [[nodiscard]] std::optional<ColIndex> lookup_inertia_fixed_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(inertia_fixed_cols_, scenario, stage, buid);
  }

  [[nodiscard]] constexpr InertiaTerm inertia_term() const noexcept
  {
    return inertia_term_;
  }

  /// Planes mode: the `K` plane rows of (scenario, stage, block), in
  /// `urreq_planes` / `drreq_planes` order (empty when absent).
  [[nodiscard]] std::vector<RowIndex> urplane_rows(const ScenarioLP& scenario,
                                                   const StageLP& stage,
                                                   BlockUid buid) const
  {
    return lookup_inner(ur.plane_rows, scenario, stage, buid)
        .value_or(std::vector<RowIndex> {});
  }
  [[nodiscard]] std::vector<RowIndex> drplane_rows(const ScenarioLP& scenario,
                                                   const StageLP& stage,
                                                   BlockUid buid) const
  {
    return lookup_inner(dr.plane_rows, scenario, stage, buid)
        .value_or(std::vector<RowIndex> {});
  }

  /// A contingency-row membership of one generator: the provisions of
  /// that generator into this zone stamp `−coef` on `row` when
  /// `contingency_includes_reserve` is set.
  struct GenRowRef
  {
    ElementIndex<GeneratorLP> generator;
    RowIndex row;
    double coef;
  };

  /// Generator memberships of the up (down) contingency rows of a block.
  [[nodiscard]] std::vector<GenRowRef> contingency_gen_refs(
      bool upward,
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const
  {
    return lookup_inner(
               (upward ? ur : dr).contingency_gen_refs, scenario, stage, buid)
        .value_or(std::vector<GenRowRef> {});
  }

  /// `C ≥ a·(p + r)`: provisions add their column to contingency rows.
  [[nodiscard]] constexpr bool contingency_includes_reserve() const noexcept
  {
    return contingency_includes_reserve_;
  }

  /// InertiaZone referenced by the plane inertia term (nullopt = none,
  /// or `add_to_lp` has not run yet — the reference is resolved there).
  [[nodiscard]] const auto& inertia_zone_index() const noexcept
  {
    return inertia_zone_index_;
  }

private:
  /// Planes-mode build (both directions) for one (scenario, stage).
  [[nodiscard]] bool add_dynamic_requirements(const SystemContext& sc,
                                              const ScenarioLP& scenario,
                                              const StageLP& stage,
                                              LinearProblem& lp);

  /// Resolved per-(stage, block) schedules of one requirement plane.
  struct Plane
  {
    Plane(const InputContext& ic,
          std::string_view cname,
          const Id& id,
          ReserveRequirementPlane plane);

    OptTBRealSched alpha;
    OptTBRealSched demand_coef;
    OptTBRealSched inertia_coef;
    OptTBRealSched contingency_coef;
    OptTBRealSched flow_coef;
  };

  /// Plane value `rhs + Σ coef·x` at one block (everything but `R`),
  /// kept to report the binding plane.
  struct PlaneEval
  {
    double rhs {0.0};
    std::vector<std::pair<ColIndex, double>> terms;
  };

  /// Output helper: per-plane duals and the binding-plane index.
  void add_plane_output(OutputContext& out,
                        const auto& rr,
                        const auto& names,
                        std::string_view binding_name) const;

  /// One resolved contingency candidate (see ReserveContingency).
  struct ContGroup
  {
    std::vector<std::pair<ElementIndex<GeneratorLP>, double>> generators;
    std::vector<std::pair<ElementIndex<DemandLP>, double>> demands;
    std::vector<std::pair<ElementIndex<LineLP>, double>> lines;
    OptTBRealSched offset;
  };

  struct Requirement
  {
    Requirement(const InputContext& ic,
                std::string_view cname,
                const Id& id,
                auto&& rreq,
                auto&& rmin,
                auto&& rcost,
                auto&& rscost,
                Array<ReserveRequirementPlane> rplanes);

    OptTBRealSched req;
    OptTBRealSched min;  ///< Min-Provision floor; effective RHS = max(req, min)
    OptTBRealSched cost;
    OptTBRealSched surplus_cost;  ///< `"eq"` only: surplus penalty
    bool equality {false};  ///< `requirement_sense: "eq"`
    STBIndexHolder<ColIndex> surplus_cols;
    std::vector<Plane> planes;  ///< empty = fixed requirement
    STBIndexHolder<ColIndex> requirement_cols;
    STBIndexHolder<RowIndex> requirement_rows;
    STBIndexHolder<ColIndex> shortage_cols;
    STBIndexHolder<ColIndex> contingency_cols;
    STBIndexHolder<std::vector<RowIndex>> plane_rows;
    STBIndexHolder<std::vector<PlaneEval>> plane_evals;
    STBIndexHolder<double> floors;  ///< `max(0, req, min)` per block
    std::vector<ContGroup> contingencies;
    STBIndexHolder<std::vector<GenRowRef>> contingency_gen_refs;
  } ur, dr;

  OptTBRealSched system_load_;
  double demand_load_factor_ {DefaultDemandLoadFactor};
  bool endogenous_generation_ {false};
  std::vector<ElementIndex<DemandLP>> demand_indexes_;
  bool contingency_includes_reserve_ {false};
  std::vector<ElementIndex<LineLP>> flow_line_indexes_;
  OptSingleId inertia_zone_sid_;
  std::optional<ElementIndex<InertiaZoneLP>> inertia_zone_index_;

  InertiaTerm inertia_term_ {DefaultInertiaTerm};
  OptTBRealSched system_inertia_;
  bool inertia_fallback_warned_ {false};
  bool support_warned_ {false};

  /// Plane terms set without their support (review P0-1).
  struct PlaneSupportIssues
  {
    bool inertia {false};
    bool contingency {false};
    bool flow {false};
    bool negative_contingency {false};
  };
  /// Strict / debug `constraint_mode` throws; normal warns once per zone.
  void check_plane_support(const SystemContext& sc,
                           int plane,
                           PlaneSupportIssues issues);

  STBIndexHolder<ColIndex> inertia_cols_;
  STBIndexHolder<RowIndex> inertia_rows_;
  STBIndexHolder<ColIndex> inertia_fixed_cols_;
  STBIndexHolder<ColIndex> dsys_cols_;
  STBIndexHolder<double> block_durations_;  ///< Δt per block [h]

  /// Output helper: `reserve_credit`, `flow_credit`, `credit_transfer`.
  void add_credit_output(OutputContext& out) const;
};

// Pin the data-struct constant value so an accidental rename of the
// `ReserveZone::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"ReserveZone"`).
static_assert(ReserveZoneLP::Element::class_name == LPClassName {"ReserveZone"},
              "ReserveZone::class_name must remain \"ReserveZone\"");

}  // namespace gtopt
