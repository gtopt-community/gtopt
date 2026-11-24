#pragma once

#include <array>
#include <span>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/block_lp.hpp>
#include <gtopt/bus_lp.hpp>
#include <gtopt/capacity_object_lp.hpp>
#include <gtopt/demand.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/linear_interface.hpp>  // ScaledView

namespace gtopt
{

class DemandLP : public CapacityObjectLP<Demand>
{
public:
  static constexpr std::string_view LoadName {"load"};
  static constexpr std::string_view FailName {"fail"};
  static constexpr std::string_view BalanceName {"balance"};
  static constexpr std::string_view CapacityName {"capacity"};
  static constexpr std::string_view EminName {"emin"};
  static constexpr std::string_view LmanName {"lman"};
  /// LP variable name of the served-load tranche columns k ≥ 2 of a
  /// stepwise failure curve (`fail_segments`).  Tranche 1 keeps the
  /// `load` name so a single-segment curve is LP-identical to `fcost`.
  static constexpr std::string_view LoadSegName {"lseg"};
  /// Per-segment failure output field names (`Demand/fail_seg<k>_sol`).
  static constexpr std::array<std::string_view, max_fail_segments>
      FailSegNames {
          "fail_seg1",
          "fail_seg2",
          "fail_seg3",
          "fail_seg4",
          "fail_seg5",
          "fail_seg6",
          "fail_seg7",
          "fail_seg8",
          "fail_seg9",
          "fail_seg10",
          "fail_seg11",
          "fail_seg12",
          "fail_seg13",
          "fail_seg14",
          "fail_seg15",
          "fail_seg16",
      };
  /// Filter metadata keys published by `add_to_lp` for `sum(...)`
  /// predicate matching.
  static constexpr std::string_view TypeKey {"type"};
  static constexpr std::string_view BusKey {"bus"};

  using CapacityBase = CapacityObjectLP<Demand>;

  [[nodiscard]]
  explicit DemandLP(const Demand& pdemand, const InputContext& ic);

  [[nodiscard]] constexpr auto&& demand(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]]
  constexpr auto bus_sid() const noexcept
  {
    return ObjectSingleId<BusLP> {demand().bus};
  }

  [[nodiscard]]
  bool add_to_lp(SystemContext& sc,
                 const ScenarioLP& scenario,
                 const StageLP& stage,
                 LinearProblem& lp);

  [[nodiscard]]
  bool add_to_output(OutputContext& out) const;

  [[nodiscard]]
  constexpr const auto& load_cols_at(const ScenarioLP& scenario,
                                     const StageLP& stage) const
  {
    return load_cols.at({scenario.uid(), stage.uid()});
  }

  /// Tolerant inner-map lookup over `load_cols`.  When every block of
  /// `(scenario, stage)` is skipped by the P1 zero-lmax optimization,
  /// the outer key is absent and `load_cols_at` would throw.  Returns a
  /// reference to an empty block holder instead, so callers such as
  /// `CapacityProfileLP` can skip the stage safely.
  [[nodiscard]]
  const auto& lookup_load_cols(const ScenarioLP& scenario,
                               const StageLP& stage) const noexcept
  {
    return find_or_empty_inner(load_cols, scenario, stage);
  }

  /// True when the served load is split into K ≥ 2 tranche columns
  /// (stepwise failure curve active).  Consumers that reference the
  /// demand's load column must then use `load_segment_cols`.
  [[nodiscard]] bool has_fail_segments() const noexcept
  {
    return !seg_cols_.empty();
  }

  /// All K tranche columns of (scenario, stage, block) when the demand
  /// uses a stepwise failure curve (K ≥ 2); empty span otherwise (the
  /// single `load_cols` column then carries the whole served load).
  /// The physical served load is the plain sum of these columns
  /// (Option A) — every row that references `load` must stamp each.
  [[nodiscard]] std::span<const ColIndex> load_segment_cols(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    const auto it = seg_cols_.find({scenario.uid(), stage.uid()});
    if (it == seg_cols_.end()) {
      return {};
    }
    const auto bit = it->second.find(buid);
    if (bit == it->second.end()) {
      return {};
    }
    return bit->second;
  }

  /// Reconstructed failure quantity at (scenario, stage, block).
  ///
  /// After the P0 demand-failure substitution (`fail = lmax − load`)
  /// the `fail` LP variable no longer exists.  The value is derived
  /// at read time from the surviving `load_cols` primal solution and
  /// the cached `block_lmax_values_` (post-capacity-clamp `lmax`).
  /// Returns `0.0` when no `load_cols` entry exists for the (s, t, b)
  /// — semantically "no failure because there is no demand to serve
  /// here".  `col_sol` is the LP's primal-solution view (the same
  /// span that `OutputContext::col_sol_span` wraps); callers pass it
  /// in to avoid per-call re-fetch.  Used by
  /// `SystemLP::accumulate_convergence_indicators` to track
  /// `unserved_demand`.
  [[nodiscard]] double fail_sol_at(const ScenarioLP& scenario,
                                   const StageLP& stage,
                                   const BlockLP& block,
                                   const ScaledView& col_sol) const noexcept;

  /// Post-capacity-clamp `lmax` [MW] of (scenario, stage, block), i.e.
  /// the exogenous demand the LP must serve; nullopt when the block has
  /// no load path (zero lmax).  Read by `ReserveZoneLP` to build the
  /// exogenous system demand of a plane-based reserve requirement.
  [[nodiscard]] std::optional<double> block_lmax_at(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(block_lmax_values_, scenario, stage, buid);
  }

  /// Constant offset of the served load over its LP column:
  /// `load = col + offset`.  Non-zero only under the Option C failure
  /// substitution (the column then carries `load − lmax`).
  [[nodiscard]] double load_col_offset(const ScenarioLP& scenario,
                                       const StageLP& stage,
                                       BlockUid buid) const noexcept
  {
    return lookup_inner(block_offset_values_, scenario, stage, buid)
        .value_or(0.0);
  }

  /// @name Parameter accessors for user constraint resolution
  /// @{
  [[nodiscard]] auto param_lmax(StageUid s, BlockUid b) const
  {
    return lmax.at(s, b);
  }
  [[nodiscard]] auto param_fcost(StageUid s, BlockUid b) const
  {
    return fcost.at(s, b);
  }
  [[nodiscard]] auto param_lossfactor(StageUid s, BlockUid b) const
  {
    return lossfactor.at(s, b);
  }
  /// @}

private:
  OptTBRealSched lmax;
  OptTBRealSched lmin;
  OptTBRealSched lossfactor;
  OptTBRealSched fcost;

  OptTRealSched emin;
  OptTRealSched ecost;

  /// Own stepwise failure curve (`Demand.fail_segments`): resolved
  /// widths (fractions of lmax) + firm remainder, and the per-segment
  /// per-stage cost schedules.  Empty when the demand has no own curve.
  FailSegmentWidths own_seg_widths_ {};
  std::vector<OptTRealSched> seg_costs_ {};

  /// Tranche columns per (scenario, stage, block) when K ≥ 2 (index 0
  /// is the `load_cols` column).  Empty for unsegmented demands.
  STBIndexHolder<std::vector<ColIndex>> seg_cols_;
  /// Tranche upper bounds `u_k` [MW] per (scenario, stage, block),
  /// parallel to `seg_cols_`; used to reconstruct per-segment failure
  /// `fail_k = u_k − served_k` (Option A).
  STBIndexHolder<std::vector<double>> seg_caps_;

  STBIndexHolder<ColIndex> load_cols;
  STBIndexHolder<RowIndex> capacity_rows;

  /// Stage-level emin-balance constraint row index (one per
  /// (scenario, stage) when emin is active).  Post-collapse this
  /// is `sum_b bdur*mcol ≤ stage_emin` (soft, ecost set) or
  /// `sum_b bdur*mcol = stage_emin` (hard, no ecost) — direct on
  /// the surviving `mcol` (`lman`) per-block columns; the
  /// pre-collapse stage-aggregator `emin_col` is gone.  Kept for
  /// potential dual-value diagnostics.
  STIndexHolder<RowIndex> emin_rows;

  STBIndexHolder<ColIndex> lman_cols;

  /// Cached `block_lmax` post-capacity-clamp values populated during
  /// `add_to_lp` and consumed by `fail_sol_at` / `add_to_output` to
  /// reconstruct `Demand/fail_sol.csv` after the P0 demand-failure
  /// substitution removed the `fail` LP column.  One entry per
  /// (scenario, stage, block) that participates in the load path.
  STBIndexHolder<double> block_lmax_values_;

  /// Cached AMPL offset values for Option C blocks.  Only populated
  /// for (scenario, stage, block) cells where the column represents
  /// `neg_fail = load − lmax` (i.e. `fail_substituted == true` in
  /// `add_to_lp`).  Used by `add_to_output` to distinguish Option C
  /// reconstruction (`load = col_primal + offset`,
  /// `fail = −col_primal`) from the forced/non-substituted path where
  /// the column already represents `load` directly.
  STBIndexHolder<double> block_offset_values_;

  /// True once any (scenario, stage, block) cell carries a strictly
  /// positive failure cost (effective `fcost` after the global
  /// `demand_fail_cost` fallback).  Set in `add_to_lp`.  When it stays
  /// false the demand's failure is unpenalized — e.g. a battery-charge
  /// `<name>_dem` pinned to `fcost == 0` by `System::expand_batteries`:
  /// its reconstructed `fail = lmax − load` is just unused dispatchable
  /// load capacity, not curtailment, so `add_to_output` skips emitting
  /// `Demand/fail_sol` for it (avoids e.g. 211 TWh of battery
  /// non-charging polluting the failure output).
  bool fail_penalized_ {false};
};

using DemandLPId = ObjectId<DemandLP>;
using DemandLPSId = ObjectSingleId<DemandLP>;

// Pin the data-struct constant value so an accidental rename of the
// `Demand::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"Demand"`).
static_assert(DemandLP::Element::class_name == LPClassName {"Demand"},
              "Demand::class_name must remain \"Demand\"");

}  // namespace gtopt
