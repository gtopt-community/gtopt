/**
 * @file      volume_right_lp.cpp
 * @brief     Implementation of VolumeRightLP methods
 * @date      Tue Apr  1 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Implements LP formulation for volume-based water rights.
 * Uses StorageLP base class for the rights-volume balance and
 * SDDP state variable coupling (Tilmant's "dummy reservoir").
 *
 * The volume right is NOT part of the hydrological topology —
 * it tracks accumulated right volumes in a separate ledger.
 *
 * ## Balance equation
 *
 *   efin(b) = efin(b-1) + fcr × dur(b) × saving(b) / escale
 *                        - fcr × dur(b) × extraction(b) / escale
 *
 * The volume starts at emax (full rights entitlement) and decrements
 * as extraction occurs (depletion model).  Economy VolumeRights
 * (purpose="economy") use the saving variable to receive deposits
 * of unused rights (PLP: IVESN/IVERN/IVAPN).
 *
 * At reset_month, eini is re-provisioned:
 *   - With bound_rule: eini = evaluate_bound_rule(reservoir_volume)
 *     (PLP: DerRiego = Base + Σ(Factor_i × Zone_Volume_i))
 *   - Without bound_rule: eini = emax (simple full reprovision)
 *
 * Efficiency is 1.0 — rights are consumed 1:1 without loss.
 */
#include <algorithm>
#include <format>

#include <gtopt/active_ref.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/stage_month_guard.hpp>
#include <gtopt/storage_state_defaults.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/volume_right_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// Read a VolumeRight's INCOMING volume at `stage` — the state its
/// balance would start from before any reset provisioning:
///
///   * the predecessor stage's (`SimulationLP::prev_stage`: same phase
///     or the previous phase's last stage) solved `efin` via
///     `solved_stage_state` — its StateVariable (written after every
///     forward solve, identical under every low_memory mode), else this
///     LP's optimal column for an in-phase predecessor,
///   * horizon start: the config `eini`,
///   * no solve yet: the config `eini` (`initial_state_fallback` warns
///     once if a solve had happened and the value was dropped).
///
/// Numeric (not an LP expression) — mirrors PLP's FijaMaule, which
/// evaluates the compensation recompute from `VarMaulePrev` each
/// stage/sim (genpdmaule.f:942-957).
template<typename SystemLPT>
[[nodiscard]] Real volume_right_incoming_state(SystemLPT& sys,
                                               const ScenarioLP& scenario,
                                               const StageLP& stage,
                                               const VolumeRightLP& vr)
{
  static constexpr auto vr_class_name = VolumeRightLP::Element::class_name;
  const Real def = vr.volume_right().eini.value_or(0.0);
  const auto [prev_stage, prev_phase] =
      sys.system_context().simulation().prev_stage(stage);
  if (prev_stage == nullptr) {
    return def;  // horizon start: the config eini IS the incoming state
  }
  // In-phase predecessor: its efin is a column of this LP.  Across a
  // phase boundary only the state variable carries it.
  // Tolerant: a predecessor stage in which `vr` was inactive has no efin
  // column (nullopt → state-variable / fallback path below).
  std::optional<ColIndex> local_col;
  if (prev_phase == nullptr) {
    local_col = vr.find_efin_col(scenario, *prev_stage);
  }
  if (const auto v = solved_stage_state(sys,
                                        scenario,
                                        *prev_stage,
                                        vr_class_name,
                                        vr.uid(),
                                        "efin",
                                        local_col))
  {
    return *v;
  }
  return initial_state_fallback(
      sys, scenario, stage, vr_class_name, vr.uid(), "efin", def);
}

}  // namespace

Real VolumeRightLP::rule_rate_cap(Real rule_volume,
                                  const StageLP& stage) const noexcept
{
  if (rule_volume >= LinearProblem::DblMax) {
    return LinearProblem::DblMax;
  }
  const Real hours = stage.total_duration();
  const Real denom = flow_conversion_rate() * hours;
  if (denom <= 0.0) {
    return LinearProblem::DblMax;
  }
  return std::max(rule_volume, 0.0) / denom;
}

bool VolumeRightLP::matches_reset_month(const StageLP& stage,
                                        std::string_view what) const
{
  const auto& rm = volume_right().reset_month;
  return rm.has_value()
      && require_stage_month(stage,
                             Element::class_name.full_name(),
                             std::format("uid={}", uid()),
                             what)
      == *rm;
}

VolumeRightLP::VolumeRightLP(const VolumeRight& pvol, const InputContext& ic)
    : StorageBase(pvol, ic, Element::class_name)
    , demand(ic, Element::class_name, id(), std::move(volume_right().demand))
    , fmax(ic, Element::class_name, id(), std::move(volume_right().fmax))
    , saving_rate(
          ic, Element::class_name, id(), std::move(volume_right().saving_rate))
    , fail_cost(volume_right().fail_cost.value_or(0.0))
{
}

double VolumeRightLP::resolve_energy_scale(const SystemContext& sc,
                                           int depth) const
{
  // Pure function of the input data (no per-cell scalar): an explicit
  // VolumeRight scale wins; otherwise inherit from the source reservoir's
  // scale map entry, else from the parent right (recursively).  The parent
  // chain is bounded to guard against a malformed `right_reservoir` cycle.
  static constexpr int max_depth = 64;
  const auto& vsm = sc.options().variable_scale_map();
  const auto vs = vsm.lookup(
      Element::class_name.full_name(), StorageBase::EnergyName, uid());
  if (vs != 1.0) {
    return vs;
  }
  if (const auto& r_ref = volume_right().reservoir; r_ref.has_value()) {
    const auto& rsv = sc.element<ReservoirLP>(ReservoirLPSId(*r_ref));
    return vsm.lookup(ReservoirLP::Element::class_name.full_name(),
                      StorageBase::EnergyName,
                      rsv.uid());
  }
  if (const auto& rr_ref = volume_right().right_reservoir;
      rr_ref.has_value() && depth < max_depth)
  {
    return sc.element(VolumeRightLPSId(*rr_ref))
        .resolve_energy_scale(sc, depth + 1);
  }
  return 1.0;
}

bool VolumeRightLP::add_to_lp(SystemContext& sc,
                              const ScenarioLP& scenario,
                              const StageLP& stage,
                              LinearProblem& lp)
{
  static constexpr const auto& cname = Element::class_name;
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }

  auto&& blocks = stage.blocks();

  const double energy_scale = resolve_energy_scale(sc);

  // Evaluate initial bound rule if present.  Axis dispatch lives in
  // `resolve_bound_rule_axis_value`; the reservoir lookup is wrapped in
  // a callback so it is skipped on non-reservoir axes.
  const auto& opt_rule = volume_right().bound_rule;
  auto initial_rule_bound = LinearProblem::DblMax;
  if (opt_rule.has_value()) {
    const auto axis_value = resolve_bound_rule_axis_value(
        *opt_rule,
        stage.month(),
        [&]() -> Real
        {
          if (!axis_uses_reservoir(opt_rule->axis)) {
            return 0.0;
          }
          const auto rsv_sid = ReservoirLPSId(opt_rule->reservoir);
          const auto& rsv = sc.element<ReservoirLP>(rsv_sid);
          return build_time_reservoir_volume(sc, scenario, stage, rsv);
        });
    initial_rule_bound = evaluate_bound_rule(*opt_rule, axis_value);
  }

  BIndexHolder<ColIndex> extraction_cols;
  map_reserve(extraction_cols, blocks.size());

  // flow_scale is resolved by add_col from VariableScaleMap metadata.
  double flow_scale = 1.0;

  for (auto&& block : blocks) {
    const auto buid = block.uid();

    auto uppb = fmax.at(stage.uid(), buid).value_or(LinearProblem::DblMax);

    // Apply the bound rule cap.  The rule yields a VOLUME [hm³] (the
    // rights provision); the extraction column is a RATE [m³/s], so the
    // cap is the rate that spends the whole volume within the stage:
    // rule / (fcr × stage_duration).
    if (opt_rule.has_value()) {
      uppb = std::min(uppb, rule_rate_cap(initial_rule_bound, stage));
    }

    const auto fcol = lp.add_col(SparseCol {
        .uppb = uppb,
        .class_name = Element::class_name.full_name(),
        .variable_name = ExtractionName,
        .variable_uid = uid(),
        .context = make_block_context(scenario.uid(), stage.uid(), block.uid()),
    });

    extraction_cols[buid] = fcol;
    flow_scale = lp.get_col_scale(fcol);
  }

  // Create saving (inflow) columns for economy VolumeRights.
  // When saving_rate is set, a saving variable is created per block
  // representing deposits of unused rights into this economy.
  BIndexHolder<ColIndex> saving_cols;
  if (volume_right().saving_rate.has_value()) {
    map_reserve(saving_cols, blocks.size());
    for (auto&& block : blocks) {
      const auto buid = block.uid();
      const auto uppb =
          saving_rate.at(stage.uid(), buid).value_or(LinearProblem::DblMax);
      const auto fcol = lp.add_col(SparseCol {
          .uppb = uppb,
          .class_name = Element::class_name.full_name(),
          .variable_name = SavingName,
          .variable_uid = uid(),
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      });
      saving_cols[buid] = fcol;
    }
  }

  // The storage balance tracks remaining rights volume.
  //
  //   vrt_vol = vrt_prev + fcr × dur × saving / escale
  //                       - fcr × dur × extraction / escale
  //
  // Efficiency = 1.0 (rights consumed 1:1, no loss).
  //
  // When reset_month fires at a cross-phase boundary, skip state variable
  // linking: the sini value is independently provisioned, so backward
  // duals and forward trial values from the previous phase are meaningless.
  const auto [prev_stg, prev_ph] = sc.prev_stage(stage);
  const bool is_cross_phase = (prev_stg != nullptr && prev_ph != nullptr);
  // A VR with reset_month requires the stage to carry a calendar month:
  // a silent `nullopt != rm` would skip the provisioning quietly and
  // produce a wrong but feasible LP.  Fail fast instead.
  bool is_reset_stage = matches_reset_month(stage, "reset_month");
  // Monthly reset (PLP TipoEtaDE != INTRAETA, genpdmaule.f:937):
  // re-provision at every stage that starts a new calendar month.
  // The horizon's first stage keeps its config eini (PLP resets only
  // for IEta > 1).
  if (volume_right().reset_monthly.value_or(false) && prev_stg != nullptr) {
    const auto cur_month = require_stage_month(stage,
                                               Element::class_name.full_name(),
                                               std::format("uid={}", uid()),
                                               "reset_monthly");
    const auto prev_month =
        require_stage_month(*prev_stg,
                            Element::class_name.full_name(),
                            std::format("uid={}", uid()),
                            "reset_monthly (previous stage)");
    is_reset_stage = is_reset_stage || (prev_month != cur_month);
  }

  const StorageOptions opts {
      .use_state_variable = volume_right().use_state_variable.value_or(
          storage_state_defaults::volume_right.use_state_variable),
      .daily_cycle = false,
      .skip_state_link = is_reset_stage && is_cross_phase,
      // In-phase reset: decouple this stage's initial state from the
      // previous stage's efin column so the provisioning pin / debit
      // row cannot corrupt the prior balance (storage_lp.hpp shares
      // the column in the same-phase layout).
      .break_stage_chain = is_reset_stage && !is_cross_phase,
      .class_name = Element::class_name.full_name(),
      .variable_uid = uid(),
      .energy_scale = energy_scale,
      .flow_scale = flow_scale,
  };

  if (!StorageBase::add_to_lp(
          cname,
          ampl_name,
          sc,
          scenario,
          stage,
          lp,
          flow_conversion_rate(),
          saving_cols,
          [](BlockUid) { return 1.0; },
          extraction_cols,
          [](BlockUid) { return 1.0; },
          LinearProblem::DblMax,
          std::nullopt,
          std::nullopt,
          std::nullopt,
          opts))
  {
    SPDLOG_CRITICAL("Failed to add storage constraints for volume right {}",
                    uid());
    return false;
  }

  // Dynamic provisioning at the designated month boundary.
  // When the stage's month matches reset_month, re-provision the
  // rights volume for the new period (PLP: FijaLajaMBloA / FijaMaule).
  //
  // With bound_rule: eini = evaluate_bound_rule(reservoir_volume)
  //   → dynamic provisioning based on current reservoir level.
  //   This matches PLP's DerRiego = Base + Σ(Factor_i × Zone_Volume_i).
  //
  // Without bound_rule: eini = emax (simple full reprovision).
  //
  // Note: when this fires at a cross-phase boundary, skip_state_link
  // ensures that the SDDP forward pass does not overwrite this value
  // with the previous phase's efin, and backward duals are not
  // propagated through the reset.
  if (is_reset_stage) {
    // Provision resolution order: explicit ``reset_value`` (PLP
    // up-counter reset, e.g. IVGAF -> 0 at INICIOANTIC), then the
    // bound_rule evaluation, then the first block's ``emax`` (the
    // reset is a per-stage event; block 0 holds the stage's nominal
    // cap).
    const auto stage_cap =
        param_emax(stage.uid(), stage.blocks().front().uid()).value_or(0.0);
    // Provision resolution: reset_value > credit recompute > bound_rule
    // > emax.  The credit path (PLP compensation at INICIOANO) uses
    // build-time config states here; update_lp refreshes it each
    // iteration from the solved/propagated states.
    Real provision = 0.0;
    if (const auto& reset_value = volume_right().reset_value;
        reset_value.has_value())
    {
      provision = *reset_value;
    } else if (const auto& credit_ref = volume_right().reset_credit_right;
               credit_ref.has_value())
    {
      const auto& credit_lp = sc.element(VolumeRightLPSId(*credit_ref));
      provision = std::min(stage_cap,
                           volume_right().eini.value_or(0.0)
                               + credit_lp.volume_right().eini.value_or(0.0));
    } else if (opt_rule.has_value()) {
      // A rule may evaluate above the ledger's capacity (e.g. a reserve
      // provision p·(V − base) past the regime threshold); fixing eini
      // above emax makes the stage infeasible — clamp to [0, emax].
      provision = std::clamp(initial_rule_bound, 0.0, stage_cap);
    } else {
      provision = stage_cap;
    }
    // PLP INICIOTEMP debit (genpdlajam.f:234-239): the December
    // riego provision is reduced by the anticipado spending counter
    // — as a ROW, not a numeric subtraction, so the linkage stays
    // exact within the LP and across SDDP phases (the debit's
    // incoming volume is its state-linked ``eini`` column):
    //
    //     eini + debit_eini = provision,   eini >= 0
    //
    // The referenced VolumeRight must have been added to the LP
    // already (earlier in volume_right_array — same ordering
    // contract as ``right_reservoir``).
    //
    // Tolerant reference (see active_ref.hpp): an inactive debit right
    // has no eini column in this cell — the debit term is omitted and
    // the provision is fixed directly (the `else` form below).
    std::optional<ColIndex> debit_eini;
    if (const auto& debit_ref = volume_right().reset_debit_right;
        debit_ref.has_value())
    {
      const auto& debit_lp = sc.element(VolumeRightLPSId(*debit_ref));
      debit_eini = debit_lp.find_eini_col(scenario, stage);
      if (!debit_eini) {
        warn_ref_once(
            cname.full_name(), uid(), "reset debit term", debit_lp.uid());
      }
    }
    if (debit_eini) {
      const auto eini_idx = eini_col_at(scenario, stage);
      auto& eini_col = lp.col_at(eini_idx);
      eini_col.lowb = 0.0;
      eini_col.uppb = LinearProblem::DblMax;
      auto drow = SparseRow {
          // Stock row (hm³): Energy readback of its dual.
          .cost_scale_type = ConstraintScaleType::Energy,
          .class_name = Element::class_name.full_name(),
          .constraint_name = ResetDebitName,
          .variable_uid = uid(),
          .context = make_stage_context(scenario.uid(), stage.uid()),
      };
      drow[eini_idx] = 1.0;
      drow[*debit_eini] = 1.0;
      reset_debit_rows_[{scenario.uid(), stage.uid()}] =
          lp.add_row(std::move(drow.equal(provision)));
    } else {
      auto& eini_col = lp.col_at(eini_col_at(scenario, stage));
      eini_col.lowb = provision;
      eini_col.uppb = provision;
    }
  }

  // Free disposal (`forfeit`): a zero-cost outflow with the extraction's
  // own balance coefficient, so unused rights can be dropped like
  // spilled water.  It removes the degenerate cap vertex that otherwise
  // lets the solver report a negative state value (see
  // VolumeRight::forfeit).  Not coupled to the physical reservoir or to
  // a parent right.
  if (volume_right().forfeit.value_or(false)) {
    const auto& own_erows = energy_rows_at(scenario, stage);
    BIndexHolder<ColIndex> forfeit_cols;
    map_reserve(forfeit_cols, blocks.size());
    for (auto&& block : blocks) {
      const auto buid = block.uid();
      const auto fcol = lp.add_col(SparseCol {
          .class_name = Element::class_name.full_name(),
          .variable_name = ForfeitName,
          .variable_uid = uid(),
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      });
      auto& erow = lp.row_at(own_erows.at(buid));
      erow[fcol] = erow.get_coeff(extraction_cols.at(buid));
      forfeit_cols[buid] = fcol;
    }
    sc.add_ampl_variable(
        ampl_name, uid(), ForfeitName, scenario, stage, forfeit_cols);
    forfeit_cols_[{scenario.uid(), stage.uid()}] = std::move(forfeit_cols);
  }

  // Store columns for external coupling access
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  extraction_cols_[st_key] = extraction_cols;
  if (!saving_cols.empty()) {
    saving_cols_[st_key] = saving_cols;
  }

  // Register volume_right-specific PAMPL columns.  The canonical name is
  // `extraction`; `flow` is kept as a convenience spelling that matches
  // waterway/flow_right.  Storage-generic variables (energy/eini/efin/
  // soft_emin) are registered centrally by StorageBase::add_to_lp.
  const auto& extr_cols = extraction_cols_.at(st_key);
  if (!extr_cols.empty()) {
    sc.add_ampl_variable(
        ampl_name, uid(), ExtractionName, scenario, stage, extr_cols);
    sc.add_ampl_variable(
        ampl_name, uid(), FlowName, scenario, stage, extr_cols);
  }
  if (const auto it = saving_cols_.find(st_key);
      it != saving_cols_.end() && !it->second.empty())
  {
    sc.add_ampl_variable(
        ampl_name, uid(), SavingName, scenario, stage, it->second);
  }

  // Store bound rule state for update_lp.  When the rule's axis consumes
  // reservoir state, capture the static reservoir refs here so update_lp
  // doesn't need `sys.element<ReservoirLP>(...)` on the current sys.
  //
  // Tolerant reference (see active_ref.hpp): a rule reservoir inactive in
  // this cell has no eini / efin column, so the rule cannot be refreshed
  // from its volume — the build-time bound stays (no state ⇒ `update_lp`
  // no-op for the cell).
  if (opt_rule.has_value()) {
    BoundState bs {.current_bound = initial_rule_bound};
    bool has_state = true;
    if (axis_uses_reservoir(opt_rule->axis)) {
      const auto rsv_sid = ReservoirLPSId(opt_rule->reservoir);
      const auto& rsv = sc.element<ReservoirLP>(rsv_sid);
      if (auto cache = find_reservoir_ref_cache(rsv, scenario, stage)) {
        bs.reservoir_cache = *cache;
      } else {
        warn_ref_once(cname.full_name(), uid(), "bound-rule update", rsv.uid());
        has_state = false;
      }
    }
    if (has_state) {
      m_bound_states_[st_key] = bs;
    }
  }

  // Couple to parent VolumeRight's energy balance if linked
  if (const auto& rr_ref = volume_right().right_reservoir; rr_ref.has_value()) {
    const VolumeRightLPSId vr_sid(*rr_ref);
    const auto& vr_lp = sc.element(vr_sid);
    // Tolerant reference (see active_ref.hpp): an inactive parent right
    // has no energy row in this cell — the parent debit is omitted.
    const auto* vr_erows = vr_lp.find_energy_rows(scenario, stage);
    if (vr_erows == nullptr) {
      warn_ref_once(
          cname.full_name(), uid(), "parent-right debit", vr_lp.uid());
    } else {
      const double dir = volume_right().direction.value_or(-1);
      // one-cycle balance on periodic stages (see balance_time_scale_at)
      const double vr_ts = vr_lp.balance_time_scale_at(scenario, stage);
      for (auto&& block : blocks) {
        const auto buid = block.uid();
        // The parent's energy row follows the storage convention
        // `E_b = E_{b-1} − coef·x`: a POSITIVE coefficient is an outflow
        // (same sign as the physical-reservoir coupling below).  So a
        // withdrawal (`direction = −1`, the default) must enter with
        // `+fcr·dur` and a supply (`direction = +1`) with `−fcr·dur`,
        // i.e. `coef = −direction·fcr·dur`.
        // flatten() applies col_scale to both flow and energy columns.
        const auto coeff =
            -flow_conversion_rate() * block.duration() * vr_ts * dir;
        lp.row_at(vr_erows->at(buid))[extraction_cols.at(buid)] = coeff;
      }
    }
  }

  // Consumptive coupling: extraction removes water from physical Reservoir.
  // Physical coefficient: +fcr × duration.
  // flatten() applies col_scale to both flow and energy columns.
  if (const auto& r_ref = volume_right().reservoir; r_ref.has_value()) {
    const ReservoirLPSId r_sid(*r_ref);
    const auto& r_lp = sc.element(r_sid);
    // A reservoir inactive in this cell has no energy row: withdrawing
    // from it would serve the right with water that is not in the system.
    // Rejected up front by validate_planning (check_water_right_sources);
    // this is the backstop with full context instead of an opaque .at().
    const auto* r_erows = r_lp.find_energy_rows(scenario, stage);
    if (r_erows == nullptr) {
      fail_lp(*this,
              scenario,
              stage,
              {
                  .field = "reservoir",
                  .value = std::format("uid={} (inactive)", r_lp.uid()),
                  .expected = "an active reservoir wherever the right is "
                              "active",
              });
    } else {
      const double r_ts = r_lp.balance_time_scale_at(scenario, stage);
      for (auto&& block : blocks) {
        const auto buid = block.uid();
        const auto coeff = +flow_conversion_rate() * block.duration() * r_ts;
        lp.row_at(r_erows->at(buid))[extraction_cols.at(buid)] = coeff;
      }
    }
  }

  // Add demand satisfaction constraint with deficit penalty.
  // sum_b [duration_b * flow_conversion_rate * extraction(b)] + fail >= demand
  // Implemented per-stage: if demand is specified, create a fail variable
  // and a constraint row.
  const auto stage_demand = demand.at(stage.uid());
  if (stage_demand.has_value() && *stage_demand > 0.0) {
    // Deficit variable [hm³] — a stage stock, priced like every other
    // stage penalty: fail_cost [$/hm³] · probability · discount (no
    // duration) with the Energy readback basis.  flatten() applies
    // col_scale.
    const auto stage_ctx = make_stage_context(scenario.uid(), stage.uid());
    const auto fail_col = lp.add_col(SparseCol {
        .cost = fail_cost
            * CostHelper::cost_factor(scenario.probability_factor(),
                                      stage.discount_factor()),
        .cost_scale_type = ConstraintScaleType::Energy,
        .scale = energy_scale,
        .class_name = Element::class_name.full_name(),
        .variable_name = FailName,
        .variable_uid = uid(),
        .context = stage_ctx,
    });

    // Demand satisfaction constraint in physical units:
    // sum_b [fcr × duration × extraction(b)] + fail >= demand
    // Stage-volume (stock) row: its dual is a $/hm³ right value, read
    // back without the block-duration term (Energy).
    SparseRow drow {
        .cost_scale_type = ConstraintScaleType::Energy,
        .class_name = Element::class_name.full_name(),
        .constraint_name = DemandName,
        .variable_uid = uid(),
        .context = stage_ctx,
    };

    for (auto&& block : blocks) {
      const auto buid = block.uid();
      const auto duration = block.duration();
      // Physical coefficient: flow → demand conversion.
      const auto coeff = flow_conversion_rate() * duration;
      drow[extraction_cols.at(buid)] = coeff;
    }
    drow[fail_col] = 1.0;

    fail_cols_[st_key] = fail_col;
    demand_rows_[st_key] =
        lp.add_row(std::move(drow.greater_equal(*stage_demand)));
  }

  return true;
}

bool VolumeRightLP::add_to_output(OutputContext& out) const
{
  static constexpr const auto& cname = Element::class_name;

  StorageBase::add_to_output(out, cname);

  const auto pid = id();
  out.add_col_sol(cname, ExtractionName, pid, extraction_cols_);
  out.add_col_cost(cname, ExtractionName, pid, extraction_cols_);
  out.add_col_sol(cname, SavingName, pid, saving_cols_);
  out.add_col_sol(cname, ForfeitName, pid, forfeit_cols_);
  out.add_col_sol(cname, FailName, pid, fail_cols_);
  out.add_col_cost(cname, FailName, pid, fail_cols_);
  out.add_row_dual(cname, DemandName, pid, demand_rows_);
  out.add_row_dual(cname, ResetDebitName, pid, reset_debit_rows_);

  return true;
}

int VolumeRightLP::update_lp(SystemLP& sys,
                             const ScenarioLP& scenario,
                             const StageLP& stage)
{
  const auto& opt_rule = volume_right().bound_rule;
  const auto& credit_ref = volume_right().reset_credit_right;
  if (!opt_rule.has_value() && !credit_ref.has_value()) {
    return 0;
  }
  // `SystemLP::update_lp` visits every (scenario, stage), including the
  // stages in which this right is inactive (nothing was built there).
  if (!is_active(stage)) {
    return 0;
  }

  auto& li = sys.linear_interface();

  // ── Credit recompute (PLP FijaMaule INICIOANO, genpdmaule.f:942-957):
  //    provision = min(emax, own_incoming + credit_incoming), read
  //    numerically from the solved/propagated states each iteration.
  //    Independent of the bound_rule machinery below.
  if (credit_ref.has_value()) {
    const bool is_credit_reset =
        matches_reset_month(stage, "reset_month (credit update)");
    if (is_credit_reset
        && (stage.phase_stage_index() > first_phase_stage_index()
            || sys.prev_phase_sys() != nullptr))
    {
      const auto& credit_lp =
          sys.system_context().template element<VolumeRightLP>(
              VolumeRightLPSId(*credit_ref));
      const Real own_in =
          volume_right_incoming_state(sys, scenario, stage, *this);
      const Real credit_in =
          volume_right_incoming_state(sys, scenario, stage, credit_lp);
      const auto cap =
          param_emax(stage.uid(), stage.blocks().front().uid()).value_or(0.0);
      const Real provision = std::min(cap, own_in + credit_in);
      const auto eini_col = eini_col_at(scenario, stage);
      li.set_col_low(eini_col, provision);
      li.set_col_upp(eini_col, provision);
      if (!opt_rule.has_value()) {
        return 1;
      }
    }
    if (!opt_rule.has_value()) {
      return 0;
    }
  }

  // No cached state ⇒ the cell was not built or its rule reservoir was
  // inactive (see add_to_lp): keep the build-time bound.
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto state_it = m_bound_states_.find(st_key);
  const auto extr_it = extraction_cols_.find(st_key);
  if (state_it == m_bound_states_.end() || extr_it == extraction_cols_.end()) {
    return 0;
  }
  auto& state = state_it->second;

  // Resolve the rule's axis input via the dispatcher.  Reservoir axes
  // route through `state.reservoir_cache` (populated in add_to_lp);
  // axes that don't consume reservoir state never invoke the callback,
  // so the cache stays untouched.
  const auto axis_value = resolve_bound_rule_axis_value(
      *opt_rule,
      stage.month(),
      [&]() -> Real
      {
        // Stage-start volume, as PLP's FijaMaule regime test
        // (`VolMauleUtil = VarMaulePrev(IVMUTIL)`, genpdmaule.f:757).
        // The (vini + vfin) / 2 average read the stage's own previous
        // vfin — or the case eini when the LP was not optimal (e.g.
        // reconstructed under memory_saving) — and opened a zone gate
        // the stage-start state does not reach.
        return physical_eini_from_cache(
            sys, scenario, stage, state.reservoir_cache);
      });

  const auto new_bound = evaluate_bound_rule(*opt_rule, axis_value);

  // No in-memory short-circuit (cf. reservoir_discharge_limit_lp.cpp):
  // under `LowMemoryMode::compress` / `snapshot` the LP bounds revert to
  // construction time on every reload while `state.current_bound`
  // survives, so an equality skip would silently keep stale bounds.
  int total = 0;
  const auto& my_extraction_cols = extr_it->second;
  const auto rate_cap = rule_rate_cap(new_bound, stage);

  for (const auto& [buid, col] : my_extraction_cols) {
    const auto fmax_b =
        fmax.at(stage.uid(), buid).value_or(LinearProblem::DblMax);
    li.set_col_upp(col, std::min(fmax_b, rate_cap));
    ++total;
  }

  // Dynamic provisioning: at reset_month, update eini to the new
  // bound_rule value (PLP: DerRiego recomputed from reservoir volume).
  const bool is_reset_stage_update =
      matches_reset_month(stage, "reset_month (update_lp)");
  if (is_reset_stage_update) {
    // ``reset_value`` provisions are static — nothing to refresh.
    if (!volume_right().reset_value.has_value()) {
      // PLP provisions from the volume at the START of the reset stage
      // (VolLajaM = VolIni(IVol), genpdlajam.f:575-576, evaluated at
      // INICIOTEMP, genpdlajam.f:624-649) — not the (vini + vfin)/2
      // average used for the per-stage rate cap above, whose vfin is
      // the stage's own previous solution.
      const auto provision_axis = resolve_bound_rule_axis_value(
          *opt_rule,
          stage.month(),
          [&]() -> Real
          {
            return physical_eini_from_cache(
                sys, scenario, stage, state.reservoir_cache);
          });
      const auto provision_bound =
          evaluate_bound_rule(*opt_rule, provision_axis);
      // Same [0, emax] clamp as the build-time provision.
      const auto stage_cap =
          param_emax(stage.uid(), stage.blocks().front().uid()).value_or(0.0);
      const auto provision = std::clamp(provision_bound, 0.0, stage_cap);
      if (const auto it = reset_debit_rows_.find({scenario.uid(), stage.uid()});
          it != reset_debit_rows_.end())
      {
        // Debit-row provisioning: refresh the row RHS
        // (eini + debit_eini = provision).
        li.set_row_equal_to(it->second, provision);
      } else {
        const auto eini_col = eini_col_at(scenario, stage);
        li.set_col_low(eini_col, provision);
        li.set_col_upp(eini_col, provision);
      }
      ++total;
    }
  }

  SPDLOG_TRACE(
      "VolumeRightLP uid={}: updated bounds "
      "(axis_value={:.1f}, bound={:.1f})",
      uid(),
      axis_value,
      new_bound);

  state.current_bound = new_bound;

  return total;
}

double VolumeRightLP::reward_objective(const ScenarioLP& scenario,
                                       const StageLP& stage,
                                       const LinearInterface& li) const
{
  const auto obj = li.get_obj_coeff();
  const auto sol = li.get_col_sol();
  const double scale_obj = li.scale_objective();
  double total = 0.0;
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  for (const auto* holder : {&extraction_cols_, &saving_cols_}) {
    const auto it = holder->find(st_key);
    if (it == holder->end()) {
      continue;
    }
    for (const auto& [buid, col] : it->second) {
      const double c_raw = obj[to_size(col)];
      if (c_raw < 0.0) {
        total += scale_obj * c_raw * sol[col] / li.get_col_scale(col);
      }
    }
  }
  return total;
}

}  // namespace gtopt
