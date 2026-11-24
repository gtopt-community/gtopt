/**
 * @file      update_context.hpp
 * @brief     Self-cached context for HasUpdateLP elements
 * @date      2026-05-05
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Each LP element that satisfies the `HasUpdateLP` concept (`update_lp()`
 * mutates LP coefficients per SDDP iteration) reads a small amount of
 * reservoir state — eini volume, efin volume — at update time.  Routing
 * those reads through `sys.element<ReservoirLP>(...)` couples
 * `update_lp` to the current SystemLP's `Collection<ReservoirLP>`,
 * blocking the Phase-2 collection-drop optimisation under
 * `LowMemoryMode::compress`.
 *
 * `ReservoirRefCache` captures, at `add_to_lp` time, exactly what
 * `update_lp` needs:
 *   * `default_volume` — fallback value for un-solved phases.
 *   * `energy_scale`  — scale factor for warm-solution reads.
 *   * `eini_col` / `efin_col` — current-stage column indices.
 *   * `rsv_uid` — kept for diagnostics and identity.
 *
 * `physical_eini_from_cache` and `physical_efin_from_cache` read the
 * cached column indices directly from `sys.linear_interface()`.  They
 * never call `sys.element<ReservoirLP>` and never traverse
 * `prev_phase_sys`.  This is sufficient because:
 *
 *   * **Non-daily_cycle reservoir** — the LP's state-link constraint
 *     pins `eini_col == prev_phase_efin_col`, so reading current
 *     `eini_col` yields the same value as the predecessor's solved
 *     efin, with no cross-phase traversal.
 *
 *   * **Daily_cycle reservoir** — there's no state link; the in-phase
 *     `efin == eini` constraint pins both columns together; reading
 *     current `eini_col` yields the current phase's optimised volume.
 *
 * In both cases, "read the eini column" is the right answer and there
 * is no special-case for daily_cycle.
 */

#pragma once

#include <cstddef>
#include <optional>
#include <string_view>

#include <gtopt/basic_types.hpp>
#include <gtopt/enum_option.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/stage_lp.hpp>
#include <gtopt/state_variable.hpp>
#include <gtopt/warn_once.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

struct ReservoirRefCache
{
  Uid rsv_uid {};
  Real default_volume {0.0};
  Real energy_scale {1.0};
  ColIndex eini_col {};
  ColIndex efin_col {};
};

/// Per-(scenario, stage) cached state for elements whose `update_lp`
/// evaluates a `RightBoundRule` axis to refresh a column bound.
///
/// Shared by `FlowRightLP` and `VolumeRightLP` — both elements drive
/// `update_lp` from a single `right_bound_rule` and need exactly:
///   * `current_bound`    — last evaluated rule output (axis -> bound)
///                          to skip redundant `set_col_lowb/uppb` calls.
///   * `reservoir_cache`  — cached column indices for reservoir-axis
///                          rules; default-constructed for axes that
///                          do not consume reservoir state.
///
/// The struct is intentionally aggregate-trivial so that
/// `IndexHolder2<ScenarioUid, StageUid, RuleBoundState>` benefits from
/// trivial copy/move during collection rebuilds.
struct RuleBoundState
{
  Real current_bound {0.0};
  ReservoirRefCache reservoir_cache {};
};

/// Populate a `ReservoirRefCache` from a `ReservoirLP` reference.
[[nodiscard]] inline ReservoirRefCache make_reservoir_ref_cache(
    const ReservoirLP& rsv, const ScenarioLP& scenario, const StageLP& stage)
{
  return ReservoirRefCache {
      .rsv_uid = rsv.uid(),
      .default_volume = rsv.reservoir().eini.value_or(0.0),
      .energy_scale = rsv.energy_scale(),
      .eini_col = rsv.eini_col_at(scenario, stage),
      .efin_col = rsv.efin_col_at(scenario, stage),
  };
}

/// Non-throwing `make_reservoir_ref_cache`: nullopt when the reservoir
/// has no eini / efin column for (scenario, stage) — i.e. it is inactive
/// in the stage.  Consumers then omit the volume-dependent term and their
/// `update_lp` finds no cached state (no-op).  See `active_ref.hpp`.
[[nodiscard]] inline std::optional<ReservoirRefCache> find_reservoir_ref_cache(
    const ReservoirLP& rsv, const ScenarioLP& scenario, const StageLP& stage)
{
  const auto eini_col = rsv.find_eini_col(scenario, stage);
  const auto efin_col = rsv.find_efin_col(scenario, stage);
  if (!eini_col || !efin_col) {
    return std::nullopt;
  }
  return ReservoirRefCache {
      .rsv_uid = rsv.uid(),
      .default_volume = rsv.reservoir().eini.value_or(0.0),
      .energy_scale = rsv.energy_scale(),
      .eini_col = *eini_col,
      .efin_col = *efin_col,
  };
}

/// Physical value of a state column at the END of `stage` (a storage's
/// `efin`) as produced by the last solve of the LP that owns `stage`.
///
/// Resolution order:
///   1. The column's `StateVariable` (`col_sol_physical()`), written by
///      `SDDPMethod::capture_state_variable_values` after EVERY forward
///      solve, for every stage of the phase.  It lives in the simulation
///      registry, so it survives `release_backend()`, a compress
///      reconstruct and any LP mutation — it is the same value under
///      every `LowMemoryMode`.  Only used once a solve has written it
///      (`has_col_sol()`), so a genuine 0 is not mistaken for "unset".
///   2. `local_col` of this LP when the LP is optimal (monolithic and
///      other non-SDDP paths; elements without a registered state
///      variable, e.g. daily-cycle reservoirs).
///   3. `nullopt` — no solved value exists.  Callers decide the initial
///      estimate through `initial_state_fallback`, never silently.
///
/// Before this helper the readers tried (2) only and substituted the case
/// `eini` otherwise.  Under `--memory-saving compress` the LI's optimal
/// flag and solution cache are cleared by the first mutation after a
/// solve (`propagate_trial_values` pins the eini column right before
/// `update_lp_for_phase`; backward cut rows; a reconstruct), so every
/// `update_lp` read fell back to the case `eini` while `off` read the
/// live backend: a volume rule saw `(v0 + eini) / 2` instead of the
/// stage's own trajectory and the two modes diverged.
template<typename SystemLPT>
[[nodiscard]] std::optional<Real> solved_stage_state(
    const SystemLPT& sys,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LPClassName class_name,
    Uid uid,
    std::string_view col_name,
    std::optional<ColIndex> local_col = std::nullopt)
{
  if (stage.phase_index() != PhaseIndex {unknown_index}) {
    const auto svar = sys.system_context().simulation().state_variable(
        StateVariable::key(scenario, stage, class_name, uid, col_name));
    if (svar.has_value() && svar->get().has_col_sol()) {
      return svar->get().col_sol_physical();
    }
  }
  if (local_col.has_value()) {
    const auto& li = sys.linear_interface();
    if (li.is_optimal()) {
      return li.get_col_sol()[*local_col];
    }
  }
  return std::nullopt;
}

/// The initial estimate a state reader uses when `solved_stage_state`
/// found nothing.  `initial` (the case `eini`) is the correct value only
/// while the LP has never been solved (SDDP iteration 0 before the first
/// forward solve, build time).  Once the LP HAS been solved the solved
/// value existed and was lost, which is a bug in the caller's data flow:
/// warn once with context instead of silently substituting `initial`.
template<typename SystemLPT>
[[nodiscard]] Real initial_state_fallback(const SystemLPT& sys,
                                          const ScenarioLP& scenario,
                                          const StageLP& stage,
                                          LPClassName class_name,
                                          Uid uid,
                                          std::string_view col_name,
                                          Real initial)
{
  const auto& li = sys.linear_interface();
  const auto& stats = li.solver_stats();
  if (stats.initial_solve_calls + stats.resolve_calls > 0) {
    static WarnOnceFlag warned;
    warn_once(warned,
              "state reader: no solved '{}' for {}:{} (scenario {}, stage "
              "{}) although the LP was solved (low_memory={}); using the "
              "initial value {} — the solved state was dropped",
              col_name,
              class_name.full_name(),
              uid,
              scenario.uid(),
              stage.uid(),
              enum_name(li.low_memory_mode()),
              initial);
  }
  return initial;
}

/// Physical efin of `stage` for the reservoir cached in `rc` — see
/// `solved_stage_state` for the resolution order.  Falls back to the case
/// `eini` (`rc.default_volume`) only when no solve produced it.
template<typename SystemLPT>
[[nodiscard]] Real physical_efin_from_cache(const SystemLPT& sys,
                                            const ScenarioLP& scenario,
                                            const StageLP& stage,
                                            const ReservoirRefCache& rc)
{
  static constexpr auto reservoir_class_name = ReservoirLP::Element::class_name;
  if (const auto v = solved_stage_state(sys,
                                        scenario,
                                        stage,
                                        reservoir_class_name,
                                        rc.rsv_uid,
                                        "efin",
                                        rc.efin_col))
  {
    return *v;
  }
  return initial_state_fallback(sys,
                                scenario,
                                stage,
                                reservoir_class_name,
                                rc.rsv_uid,
                                "efin",
                                rc.default_volume);
}

/// Read the physical eini of the current stage — the predecessor
/// phase's solved efin propagated via gtopt's existing
/// ``StateVariable`` channel.
///
/// Resolution order:
///
///   1. **Predecessor's efin StateVariable** (cross-phase).  When
///      ``sys.prev_phase_sys()`` is set (production path —
///      ``SDDPMethod::update_lp_for_phase`` wires it at
///      ``sddp_method_iteration.cpp:281``), look up the predecessor's
///      ``efin`` ``StateVariable`` by key in the simulation registry
///      and return ``col_sol_physical()``.  ``set_col_sol`` is called
///      on every ``StateVariable`` immediately after the predecessor's
///      forward solve, so by the time this phase's ``update_lp`` runs
///      the value is already correct — no LI query, no
///      ``is_optimal()`` check on the current phase's still-unsolved
///      LP.  Daily-cycle reservoirs have no state link and are not
///      reached here (their predecessor lookup returns no match).
///   2. **Current LI's eini_col solution** (in-phase).  Daily-cycle
///      reservoirs pin ``eini == efin`` via the close constraint, so
///      reading the current ``eini_col`` after the LP is solved is the
///      right answer.  Also covers test paths that don't set
///      ``prev_phase_sys``.
///   3. **Default volume**.  Iter-0 first phase, before any solve.
///
/// **Why the StateVariable channel** (and not the propagated col
/// bound, or ``prev_li.get_col_sol()`` directly):
///   * The current LI's ``eini_col`` solution is unavailable when
///     ``update_lp`` runs — that's BEFORE the solve.  The pre-existing
///     fallback to ``rc.default_volume`` (= JSON ``eini``) selected the
///     wrong piecewise segment for ReservoirSeepage/RDL/ProductionFactor
///     /FlowRight/VolumeRight whenever the predecessor's actual trial
///     differed from the JSON ``eini``.  On the large hydrothermal case scen 51
///     phase 38 ELTORO ``eini = 1731 hm³`` (segment 2 ``constant = 15.09
///     m³/s``) vs predecessor's drained-to-0 trial → row forced 15.09 m³/s
///     seepage at empty storage → cascade infeasibility through
///     p38→p1.
///   * ``StateVariable::col_sol_physical()`` is gtopt's canonical
///     cross-phase value channel: it's updated post-solve and consumed
///     by ``propagate_trial_values`` and ``BendersCut`` exactly the
///     same way.  Reading it here makes the segment-selection axis
///     consistent with the rest of the SDDP machinery.
///   * Restores the design from commit ``7a446444 refactor(update_lp):
///     cache reservoir refs + use StateVariable channel`` that
///     ``aeeb1c98 simplify(update_context): read eini_col directly,
///     drop StateVariable detour`` removed under the (incorrect)
///     assumption that ``current_eini_col == prev_phase_efin_col``
///     algebraically when ``update_lp`` runs.
template<typename SystemLPT>
[[nodiscard]] Real physical_eini_from_cache(const SystemLPT& sys,
                                            const ScenarioLP& scenario,
                                            const StageLP& stage,
                                            const ReservoirRefCache& rc)
{
  if (stage.is_horizon_first()) {
    return rc.default_volume;
  }

  const auto& li = sys.linear_interface();

  // 0. (2026-05-12) Pinned-bound short-circuit.  When
  //    `propagate_trial_values` has fixed the eini column to the
  //    forward-pass trial value (lb == ub via the 'B' setter), that
  //    bound IS the authoritative state for the current phase — and
  //    much more reliable than the downstream `state_variable` lookup
  //    or `is_optimal()` check, both of which miss in the SDDP
  //    backward / aperture path that this helper feeds:
  //     * aperture clones whose own ScenarioLP differs from the
  //       scene's central scenario the source SV was registered under
  //       → step 1 lookup misses → falls through to `default_volume`
  //       (the static annual eini, e.g. 1731 Hm³ for ELTORO).
  //     * freshly built / reconstructed LPs (compress mode after
  //       `release_backend()`) where `is_optimal()` is false even
  //       though the pinned bound carries the correct state →
  //       step 2 in-phase fallback misses → same wrong fallback.
  //    The end-state observed on the large hydrothermal case p51 INFEAS-PROBE
  //    2026-05-12: ELTORO drained to 0 in the forward pass, but
  //    `physical_eini_from_cache` returned 1731 Hm³ (annual eini) →
  //    seepage segment 1 selected (`constant = 15.088 m³/s`) → forced
  //    outflow ≈ 75 m³/s over 5 blocks → aperture LP structurally
  //    infeasible.  Reading the pinned bound directly bypasses both
  //    failure modes and uses the LP itself as the single source of
  //    truth for "what state was propagated into this phase".
  const auto col_low = li.get_col_low()[rc.eini_col];
  const auto col_upp = li.get_col_upp()[rc.eini_col];
  if (col_low == col_upp) {
    return col_low;
  }

  // 1. The predecessor stage's solved efin (`solved_stage_state`: its
  //    StateVariable, written after every forward solve).  The
  //    predecessor is `SimulationLP::prev_stage`: the previous stage of
  //    the SAME phase for an intra-phase stage, the previous phase's
  //    last stage at a phase boundary.  (This step used to read the
  //    previous PHASE's last efin for every stage of a phase > 0 —
  //    wrong for the second and later stages of a multi-stage phase —
  //    and returned an unset StateVariable's 0.0 as a volume.)
  static constexpr auto reservoir_class_name = ReservoirLP::Element::class_name;
  if (const auto* prev_stage =
          sys.system_context().simulation().prev_stage(stage).first;
      prev_stage != nullptr)
  {
    if (const auto v = solved_stage_state(sys,
                                          scenario,
                                          *prev_stage,
                                          reservoir_class_name,
                                          rc.rsv_uid,
                                          "efin"))
    {
      return *v;
    }
  }

  // 2. In-phase fallback (daily-cycle reservoirs: no state variable;
  //    test paths without a predecessor): this LP's own eini solution.
  if (li.is_optimal()) {
    return li.get_col_sol()[rc.eini_col];
  }

  // 3. No solved value: the case eini — correct only before any solve.
  return initial_state_fallback(sys,
                                scenario,
                                stage,
                                reservoir_class_name,
                                rc.rsv_uid,
                                "eini",
                                rc.default_volume);
}

/// Build-time estimate of a reservoir's volume at `stage` for the
/// `RightBoundRule` evaluated in `add_to_lp` (before any solve).
///
/// At a phase boundary the predecessor phase's `efin` StateVariable is
/// the correct per-phase state once a forward pass has set it (e.g.
/// when the phase LP is rebuilt under `low_memory`); an UNSET state
/// variable (no forward solve yet) falls back to the case `eini`.  In-phase
/// stages have no solved state at build time and use the case `eini`;
/// `update_lp` refreshes the bound from the solved/propagated state before
/// every solve.
template<typename SystemContextT>
[[nodiscard]] Real build_time_reservoir_volume(const SystemContextT& sc,
                                               const ScenarioLP& scenario,
                                               const StageLP& stage,
                                               const ReservoirLP& rsv)
{
  const Real def = rsv.reservoir().eini.value_or(0.0);
  const auto [prev_stg, prev_ph] = sc.prev_stage(stage);
  if (prev_stg == nullptr || prev_ph == nullptr) {
    return def;
  }
  static constexpr auto reservoir_class_name = ReservoirLP::Element::class_name;
  const auto svar_opt = sc.simulation().state_variable(StateVariable::key(
      scenario, *prev_stg, reservoir_class_name, rsv.uid(), "efin"));
  // `has_col_sol()`, not `v > 0`: a predecessor drained to exactly 0 is a
  // solved state, not "unset" (the old guard rebuilt it from the case
  // eini under low_memory).
  if (svar_opt.has_value() && svar_opt->get().has_col_sol()) {
    return svar_opt->get().col_sol_physical();
  }
  return def;
}

/// Convenience: return the (vini + vfin) / 2 average that all four
/// reservoir-driven `update_lp` impls (FlowRight, RDL, ProductionFactor,
/// VolumeRight) use as the bound-rule axis input.  ReservoirSeepageLP
/// uses `physical_eini_from_cache` directly (vini-only, by design).
template<typename SystemLPT>
[[nodiscard]] Real average_volume_from_cache(const SystemLPT& sys,
                                             const ScenarioLP& scenario,
                                             const StageLP& stage,
                                             const ReservoirRefCache& rc)
{
  const auto vini = physical_eini_from_cache(sys, scenario, stage, rc);
  const auto vfin = physical_efin_from_cache(sys, scenario, stage, rc);
  return (vini + vfin) / 2.0;
}

}  // namespace gtopt
