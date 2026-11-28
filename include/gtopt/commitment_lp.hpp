/**
 * @file      commitment_lp.hpp
 * @brief     LP formulation for unit commitment (three-bin u/v/w model)
 * @date      Tue Apr  8 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Defines CommitmentLP which creates binary (or relaxed) status/startup/
 * shutdown variables for a generator and adds the three core UC constraints:
 * - C1 (logic): u[t] − u[t−1] = v[t] − w[t]
 * - C2 (generation limits): Pmin·u ≤ p ≤ Pmax·u
 * - C3 (exclusion): v[t] + w[t] ≤ 1
 *
 * Also u-gates the generator's piecewise heat-rate kink rows.  Emission
 * prices and caps are not handled here: they are wired by
 * EmissionSourceLP / EmissionZoneLP (see emission_source_lp.cpp).
 */

#pragma once

#include <optional>
#include <span>
#include <string_view>
#include <utility>
#include <vector>

#include <gtopt/commitment.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/schedule.hpp>

namespace gtopt
{

class CommitmentLP : public ObjectLP<Commitment>
{
public:
  static constexpr std::string_view StatusName {"status"};
  /// Fixed start / shutdown between consecutive stages declared
  /// `long_transition` off → on / on → off.
  static constexpr std::string_view StartupTransitionName {
      "startup_transition"};
  static constexpr std::string_view ShutdownTransitionName {
      "shutdown_transition"};
  static constexpr std::string_view StartupName {"startup"};
  static constexpr std::string_view ShutdownName {"shutdown"};
  static constexpr std::string_view GenUpperName {"gen_upper"};
  static constexpr std::string_view GenLowerName {"gen_lower"};
  static constexpr std::string_view LogicName {"logic"};
  static constexpr std::string_view ExclusionName {"exclusion"};
  static constexpr std::string_view RampUpName {"ramp_up"};
  static constexpr std::string_view RampDownName {"ramp_down"};
  static constexpr std::string_view SegmentName {"segment"};
  static constexpr std::string_view MinUpTimeName {"min_up_time"};
  static constexpr std::string_view MinDownTimeName {"min_down_time"};
  static constexpr std::string_view HotStartName {"hot_start"};
  static constexpr std::string_view WarmStartName {"warm_start"};
  static constexpr std::string_view ColdStartName {"cold_start"};
  static constexpr std::string_view StartupTypeName {"startup_type"};
  static constexpr std::string_view MaxStartsName {"max_starts"};
  static constexpr std::string_view MinStartsName {"min_starts"};
  /// power_based: total power at the end of each block [MW]
  static constexpr std::string_view PowerName {"power"};
  /// Usage-accounting output streams (`options.output_usage_metrics`).
  static constexpr std::string_view UsagePminName {"usage_pmin"};
  static constexpr std::string_view UsageAtPminName {"usage_at_pmin"};
  /// Wear cost outputs ($ per start event, $/h per block).
  static constexpr std::string_view StartupWearCostName {"startup_wear_cost"};
  static constexpr std::string_view OperationWearCostName {
      "operation_wear_cost"};
  /// "At pmin" tolerance: p ≤ Pmin·u + abs + rel·Pmin [MW].
  static constexpr double usage_at_pmin_abs_tol = 1e-6;
  static constexpr double usage_at_pmin_rel_tol = 1e-3;
  /// Status above which a (relaxed) unit counts as committed.
  static constexpr double usage_on_status = 1e-6;

  using Base = ObjectLP<Commitment>;

  explicit CommitmentLP(const Commitment& commitment, const InputContext& ic);

  [[nodiscard]] constexpr auto&& commitment(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] constexpr auto generator_sid() const noexcept
  {
    return GeneratorLPSId {commitment().generator};
  }

  /// Tolerant lookup of the per-block status (u) columns for a given
  /// (scenario, stage).  Returns nullptr when the commitment is
  /// inactive for that cell or has no status columns yet (e.g.,
  /// add_to_lp not yet called on this scenario/stage).  Used by
  /// ``ReserveProvisionLP::add_to_lp`` to inject the
  /// ``provision_col - urmin × u_commit ≥ 0`` linkage row that
  /// gates PLEXOS-style ``Min Provision`` by the commit binary.
  [[nodiscard]] const BIndexHolder<ColIndex>* find_status_cols(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    const auto it = status_cols_.find({scenario.uid(), stage.uid()});
    return it != status_cols_.end() ? &it->second : nullptr;
  }

  /// Status (u) column of block @p buid — every block of a commitment
  /// period maps to the period's column — or nullopt when the element
  /// built none there.
  [[nodiscard]] std::optional<ColIndex> lookup_status_col(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid) const noexcept
  {
    return lookup_inner(block_status_cols_, scenario, stage, buid);
  }

  /// No-load fuel burn leg ``(u_col, noload_fuel)`` [fuel-unit / h per
  /// unit of status] for block @p buid, or ``std::nullopt`` when
  /// ``noload_fuel`` is unset / zero or the status column does not exist.
  [[nodiscard]] std::optional<std::pair<ColIndex, double>> noload_fuel_leg(
      const ScenarioLP& scenario, const StageLP& stage, BlockUid buid) const
  {
    const auto burn = commitment().noload_fuel.value_or(0.0);
    // Both formulations burn no-load fuel while committed (u per block on
    // a non-chronological stage, per period on a chronological one).
    if (burn == 0.0) {
      return std::nullopt;
    }
    const auto* ucols = find_status_cols(scenario, stage);
    if (ucols == nullptr) {
      return std::nullopt;
    }
    const auto it = ucols->find(buid);
    if (it == ucols->end()) {
      return std::nullopt;
    }
    return std::pair {it->second, burn};
  }

  /// Per-block startup (v) columns for a (scenario, stage); nullptr when the
  /// commitment is inactive there.  Used by the MIP-start `CommitmentLogicRule`
  /// to derive v from the repaired status (u) transitions so the C1 logic rows
  /// (`u[p] - u[p-1] - v[p] + w[p] = 0`) hold for the injected integer start.
  /// power_based: P̂ (total power at the end of each block), or null.
  [[nodiscard]] const BIndexHolder<ColIndex>* find_power_cols(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    const auto it = power_cols_.find({scenario.uid(), stage.uid()});
    return it != power_cols_.end() ? &it->second : nullptr;
  }

  [[nodiscard]] const BIndexHolder<ColIndex>* find_startup_cols(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    const auto it = startup_cols_.find({scenario.uid(), stage.uid()});
    return it != startup_cols_.end() ? &it->second : nullptr;
  }

  /// Per-block shutdown (w) columns for a (scenario, stage); nullptr when the
  /// commitment is inactive there.  Companion to `find_startup_cols`.
  [[nodiscard]] const BIndexHolder<ColIndex>* find_shutdown_cols(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    const auto it = shutdown_cols_.find({scenario.uid(), stage.uid()});
    return it != shutdown_cols_.end() ? &it->second : nullptr;
  }

  /// Per-node ramp-up (C4) rows for a (scenario, stage), keyed by block
  /// uid; nullptr when the commitment has no ramp rows there.
  [[nodiscard]] const BIndexHolder<RowIndex>* find_ramp_up_rows(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    const auto it = ramp_up_rows_.find({scenario.uid(), stage.uid()});
    return it != ramp_up_rows_.end() ? &it->second : nullptr;
  }

  /// Scheduled ``Commitment.pmin`` [MW] at (stage, block), or nullopt
  /// when unset (the LP then uses ``Generator.pmin``).  See the free
  /// function ``committed_pmin``.
  [[nodiscard]] std::optional<double> committed_pmin(StageUid stage_uid,
                                                     BlockUid block_uid) const
  {
    return pmin_.optval(stage_uid, block_uid);
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

private:
  /// `options.output_usage_metrics` streams (see `add_to_output`).
  void add_usage_to_output(OutputContext& out) const;
  void add_wear_to_output(OutputContext& out) const;
  /// Endpoints of one block's operation wear terms.
  struct WearBlock
  {
    ColIndex ucol;
    ColIndex gcol;
    double pmin;
    double pmax;
  };
  /// `startup_wear_cost` at @p rep_buid, validated (>= 0); recorded in
  /// @p coefs when non-zero.
  [[nodiscard]] double resolve_start_wear(const ScenarioLP& scenario,
                                          const StageLP& stage,
                                          BlockUid rep_buid,
                                          BIndexHolder<double>& coefs) const;
  /// Adds `a·u + b·(p − Pmin·u)` of one block to the u / p costs.
  void add_operation_wear(LinearProblem& lp,
                          const ScenarioLP& scenario,
                          const StageLP& stage,
                          const BlockLP& block,
                          const WearBlock& wb,
                          BIndexHolder<double>& wear_a,
                          BIndexHolder<double>& wear_b,
                          BIndexHolder<double>& wear_pmin) const;

  ElementIndex<GeneratorLP> generator_index_;
  /// Per-(stage, block) startup / shutdown costs applied to the v / w
  /// columns in the objective.  Back-compat: a scalar or per-stage JSON
  /// value broadcasts to every block, reproducing the legacy per-stage
  /// cost byte-for-byte.  Resolved block-by-block in ``add_to_lp``.
  OptTBRealSched startup_cost_;
  OptTBRealSched shutdown_cost_;
  /// One warning per element for an unusable startup-tier set.
  bool tier_warned_ {false};
  /// One info note per element when chronological-only data is ignored
  /// on a non-chronological (block-formulation) stage.
  bool block_note_logged_ {false};

  /// True when the element carries data only the chronological
  /// formulation uses (startup / shutdown costs, ramps, min up / down
  /// times, start tiers or counts, commitment_period).  No-load cost and
  /// fuel apply in both formulations.
  [[nodiscard]] bool has_chronological_data() const noexcept;
  /// Per-(stage, block) forced commitment schedule.  When set at a
  /// given block, the u column's bounds are pinned to that value
  /// (interpreted as 0 = off, 1 = on); blocks where the schedule has
  /// no entry leave u free (or governed by ``must_run``).
  OptTBRealSched fixed_status_;
  /// Per-(stage, block) minimum stable level when committed [MW]
  /// (PLEXOS ``Min Stable Level`` time series).  Resolved block-by-
  /// block; unset blocks fall back to the gen column lower bound.
  OptTBRealSched pmin_;
  /// Wear coefficients by (scenario, stage) → block: start wear [$/start]
  /// at the period's representative block; a [$/h], b [$/MWh] and the
  /// committed floor Pmin [MW] per block (outputs only).
  STBIndexHolder<double> startup_wear_coefs_;
  STBIndexHolder<double> wear_a_;
  STBIndexHolder<double> wear_b_;
  STBIndexHolder<double> wear_pmin_;
  /// Per-(stage, block) ramp limits / startup-shutdown output envelopes,
  /// resolved block-by-block exactly like ``pmin_``.  A scalar JSON input
  /// resolves to the same constant on every block (back-compat with the
  /// legacy scalar ``OptReal`` fields); unset blocks fall back to
  /// ``gen_pmax`` ("no limit") in the ramp rows.
  OptTBRealSched ramp_up_;  ///< Ramp-up limit [MW/hr] while online
  OptTBRealSched ramp_down_;  ///< Ramp-down limit [MW/hr] while online
  OptTBRealSched startup_ramp_;  ///< Max output in startup block [MW]
  OptTBRealSched shutdown_ramp_;  ///< Max output in shutdown block [MW]
  OptTBRealSched startup_wear_cost_;
  OptTBRealSched wear_hour_cost_;
  OptTBRealSched wear_load_cost_;

  STBIndexHolder<ColIndex> status_cols_;
  STBIndexHolder<ColIndex> block_status_cols_;  ///< every block → its u
  STBIndexHolder<ColIndex> startup_cols_;
  STBIndexHolder<ColIndex> shutdown_cols_;
  STBIndexHolder<RowIndex> logic_rows_;
  STBIndexHolder<RowIndex> gen_upper_rows_;
  STBIndexHolder<RowIndex> gen_lower_rows_;
  STBIndexHolder<RowIndex> exclusion_rows_;
  STBIndexHolder<RowIndex> ramp_up_rows_;
  STBIndexHolder<RowIndex> ramp_down_rows_;

  // Piecewise-heat-rate cols and linking rows were removed from
  // CommitmentLP on 2026-05-20 — that responsibility lives in
  // GeneratorLP (Generator.pmax_segments + heat_rate_segments +
  // fuel) as a pure-LP convex-slack formulation.
  STBIndexHolder<RowIndex> min_up_time_rows_;
  STBIndexHolder<RowIndex> min_down_time_rows_;

  /// Hot/warm/cold startup type variables and constraints
  STBIndexHolder<ColIndex> hot_start_cols_;
  STBIndexHolder<ColIndex> warm_start_cols_;
  STBIndexHolder<ColIndex> cold_start_cols_;
  STBIndexHolder<RowIndex> startup_type_rows_;
  STBIndexHolder<RowIndex> hot_start_rows_;
  STBIndexHolder<RowIndex> warm_start_rows_;

  /// Per-(stage, block) ``Σ startup ≤ max_starts`` cap rows — one row
  /// per scope window (hour / day / week / horizon).  Indexed by the
  /// window-ending block uid so the dual on each row is recoverable.
  STBIndexHolder<RowIndex> max_starts_rows_;

  /// Declared `long_transition` mode of a cyclic unit whose time
  /// constraints @p long_list exceed the typical period (empty when none
  /// apply); warns once per unit and adds the fixed start / shutdown of a
  /// declared off ↔ on change from the previous stage.
  [[nodiscard]] std::string_view setup_long_transition(
      SystemContext& sc,
      const ScenarioLP& scenario,
      const StageLP& stage,
      LinearProblem& lp,
      double cycle_len,
      std::span<const std::pair<std::string_view, double>> long_list);

  /// One warning per unit for dropped long-transition constraints.
  bool m_long_warned_ {false};

  // ── power_based (docs/unit-commitment.md §4.6) ──
  bool power_based_warned_ {false};
  STBIndexHolder<ColIndex> power_cols_;  ///< P̂ at the end of each block
  /// False (warned once) on a point stage or an expandable generator,
  /// which keep the energy-block rows.
  [[nodiscard]] bool power_based_applies(const StageLP& stage,
                                         bool point,
                                         bool expandable);
  void add_power_based(SystemContext& sc,
                       const ScenarioLP& scenario,
                       const StageLP& stage,
                       LinearProblem& lp,
                       const BIndexHolder<ColIndex>& generation_cols,
                       std::span<const std::size_t> block_period,
                       std::span<const std::size_t> period_starts,
                       std::span<const double> period_real,
                       const BIndexHolder<ColIndex>& ucols,
                       std::span<const ColIndex> period_vcol,
                       std::span<const ColIndex> period_wcol,
                       bool cyclic,
                       double initial_u,
                       bool horizon_start);
};

// Pin the data-struct constant value so an accidental rename of the
// `Commitment::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"Commitment"`).
static_assert(CommitmentLP::Element::class_name == LPClassName {"Commitment"},
              "Commitment::class_name must remain \"Commitment\"");

/// The Commitment of generator @p gen_index active at @p stage, or
/// nullptr.
[[nodiscard]] const CommitmentLP* find_unit_commitment(
    const SystemContext& sc,
    ElementIndex<GeneratorLP> gen_index,
    const StageLP& stage);

/// Committed minimum stable output of @p gen at (stage, block) [MW]: the
/// floor the unit holds while ON, ``max(Generator.pmin,
/// Commitment.pmin)``, or ``Generator.pmin`` without a commitment
/// (@p cmt null).  Read from the input parameters, never from the
/// generation column's lower bound: with the documented UC pattern
/// ``Generator.pmin = 0`` that bound carries no committed minimum.
/// Unset values read 0.  Same value on chronological and block stages.
[[nodiscard]] double committed_pmin(const CommitmentLP* cmt,
                                    const GeneratorLP& gen,
                                    StageUid stage_uid,
                                    BlockUid block_uid);

/// The CommitmentLP of @p gen that carries a no-load fuel burn
/// (``Commitment.noload_fuel > 0``), or nullptr.  CommitmentLP is built
/// before FuelLP / EmissionSourceLP, so its status columns exist when
/// they collect fuel-burn legs.
[[nodiscard]] const CommitmentLP* find_noload_fuel_commitment(
    const SystemContext& sc, const GeneratorLP& gen);

/// Every fuel-burn leg of @p gen in block @p buid [fuel-unit / h per unit
/// of column]: ``GeneratorLP::fuel_burn_legs`` (dispatch column and
/// heat-rate slack columns) plus the no-load burn ``noload_fuel · u`` of
/// @p cmt when non-null.  Shared by FuelLP offtake rows and fuel-basis
/// EmissionSourceLP rows so both see the same fuel.
[[nodiscard]] std::vector<std::pair<ColIndex, double>> generator_fuel_burn_legs(
    const GeneratorLP& gen,
    const CommitmentLP* cmt,
    const ScenarioLP& scenario,
    const StageLP& stage,
    BlockUid buid);

}  // namespace gtopt
