/**
 * @file      future_cost_lp.hpp
 * @brief     LP representation of a FutureCost (FCF / cost-to-go) element
 * @date      Sun Jun 21 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Planning-level element: it owns the end-of-horizon cost-to-go α
 * (`varphi_s`) column(s) and the boundary cuts that linearise it.  Unlike the
 * operational elements it has NO per-(scenario, stage) `add_to_lp`; it
 * participates only in the GLOBAL planning pass (`add_to_global_lp`, run once
 * per (scene, phase) cell after the operational build) and the output pass.
 */

#pragma once

#include <optional>
#include <string>
#include <string_view>

#include <gtopt/future_cost.hpp>
#include <gtopt/object_lp.hpp>

namespace gtopt
{
class SystemContext;
class SceneLP;
class PhaseLP;
class LinearProblem;
class OutputContext;
class PlanningLP;
struct SDDPOptions;

using FutureCostLPSId = ObjectSingleId<class FutureCostLP>;

class FutureCostLP : public ObjectLP<FutureCost>
{
public:
  /// Output stream names (no magic strings — see feedback_no_magic_strings).
  static constexpr std::string_view AlphaName {"alpha"};
  static constexpr std::string_view RebaseName {"rebase"};
  static constexpr std::string_view ApproxFcfName {"approx_fcf"};

  explicit FutureCostLP(const FutureCost& pfc, const InputContext& ic)
      : ObjectLP<FutureCost>(pfc, ic, Element::class_name)
  {
  }

  [[nodiscard]] constexpr auto&& future_cost(this auto&& self) noexcept
  {
    return self.object();
  }

  /// Planning-level GLOBAL scope: registers the α / `varphi_s` columns, loads
  /// the boundary cuts, applies the `mean_shift` rebase (anchored at the
  /// reservoirs' `efin` targets), and stores the α col handles + per-scene c̄.
  /// Runs once per (scene, phase) cell after the operational `add_to_lp` loop
  /// (see `system_lp.cpp::add_to_planning_lp`).
  ///
  /// Piece-2 step A: inert plumbing.  The α / boundary-cut / rebase logic
  /// migrates here from `sddp_method_alpha.cpp` + `sddp_boundary_cuts.cpp` in
  /// step D, guarded by the mean_shift bound-consistency regression test.
  [[nodiscard]] static bool add_to_global_lp(const SystemContext& sc,
                                             const SceneLP& scene,
                                             const PhaseLP& phase,
                                             LinearProblem& lp);

  /// Emits the α / `varphi_s` solution(s) + the per-scene rebase constant c̄ to
  /// `FutureCost/{alpha|alpha_<s>, rebase}`.  SELF-FINDS its data at write
  /// time: the α columns are read from the persistent `SimulationLP`
  /// state-variable registry (via `alpha_cols_on_cell`) and the rebase
  /// constant from `SimulationLP::alpha_offset`, both reached through
  /// `OutputContext::system_context()`.  Because these registries persist
  /// across per-cell LP rebuilds, the output works under ALL `low_memory`
  /// modes — no per-cell resident stash is needed.  `alpha + rebase`
  /// reconstructs the un-rebased FCF.
  [[nodiscard]] bool add_to_output(OutputContext& out) const;
};

/// Apply the boundary-cut / FCF fields of a `FutureCost` element onto the
/// internal `SDDPOptions` carrier that `load_boundary_cuts_csv` consumes —
/// the ONE read-site mapping used by BOTH `MonolithicMethod` (onto a fresh,
/// default-constructed `SDDPOptions`) and `SDDPMethod` (onto its runtime
/// options).  Only fields the element explicitly authors overwrite the
/// target, so a programmatic `SDDPOptions` (test fixtures) without an element
/// is left unchanged.  When the element sets `mode` but not `sharing`, the
/// terminal sharing is derived from `mode` (`combined` → `shared`, otherwise
/// `per_scene`), the legacy scope rule.
///
/// Mapping: `cuts_file` → `boundary_cuts_file`, `mode` → `boundary_cuts_mode`,
/// `sharing` → `boundary_cut_sharing`, `mean_shift` →
/// `boundary_cuts_mean_shift`, `iteration_limit` → `boundary_max_iterations`,
/// `missing_cut_var_mode` → `missing_cut_var_mode`, `valuation` →
/// `boundary_cuts_valuation`, `scale_alpha` → `scale_alpha`.
/// `single_cut_equality` / `use_user_alpha` are read off the element itself.
void apply_future_cost_config(const FutureCost& fc, SDDPOptions& opts);

/// Resolve a boundary-cut file path the same way for every method: an
/// absolute path is returned unchanged; a relative one is joined onto
/// `options.input_directory`.  Empty stays empty.
[[nodiscard]] std::string resolve_future_cost_path(
    const PlanningLP& planning_lp, const std::string& path);

/// Pointer to the first ACTIVE `FutureCost` element in @p planning_lp, or
/// `nullptr` when there is none.  Reads the element straight from the System
/// INPUT data (`future_cost_array`) of the representative `(scene 0, phase 0)`
/// cell — NOT the LP collection, which the SDDP default `low_memory = compress`
/// drops for planning-only elements.  Used by both methods to source the
/// user-α uid + the boundary-cut configuration.
[[nodiscard]] const FutureCost* active_future_cost(
    const PlanningLP& planning_lp);

/// Whether the active `FutureCost` element in @p planning_lp has
/// `use_user_alpha = true` (piece 5 step 2a) — the user-overridable FCF flag
/// that suppresses the built-in α (`register_alpha_variables` runs with
/// `register_as_state_variable = false`).  Returns `false` when there is no
/// active FutureCost element (the legacy boundary-cut / no-FCF paths).
[[nodiscard]] bool has_active_use_user_alpha(const PlanningLP& planning_lp);

/// The active user-α column's uid when a FutureCost element has
/// use_user_alpha=true AND a user_alpha_uid set; std::nullopt otherwise.
/// Single source of truth for the "user α is the FCF recourse column"
/// guard used across the SDDP backward/forward/alpha passes.
[[nodiscard]] std::optional<Uid> active_user_alpha_uid(
    const PlanningLP& planning_lp) noexcept;

}  // namespace gtopt
