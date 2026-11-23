/**
 * @file      future_cost.hpp
 * @brief     Future Cost Function (FCF) element — the cost-to-go / α
 * @date      Sat Jun 21 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Defines the FutureCost structure — a first-class element that owns the
 * end-of-horizon **cost-to-go** (the future-cost function, FCF: α /
 * `varphi_s`) and the boundary cuts that linearise it.  It is the ONE
 * method-neutral place where the FCF boundary cuts are configured: both
 * `monolithic_method` and `sddp_method` (and cascade, which runs SDDP) read
 * only the resolved FutureCost element — never `sddp_options` /
 * `monolithic_options` — for:
 *
 *   - the boundary cuts (`cuts_file`: rows `iteration,scene,rhs,<state...>`;
 *     each row is `α ≥ rhs − Σ wvᵣ·Vfinᵣ`, the per-reservoir `-water_value`
 *     slopes on the terminal storage state variables), how they are loaded
 *     (`mode`, `sharing`, `iteration_limit`, `missing_cut_var_mode`,
 *     `valuation`, `single_cut_equality`),
 *   - the α / `varphi_s` decision variable(s) (one per source scene under
 *     multicut, priced `1/N`), whose optimal value is the realised
 *     cost-to-go (`scale_alpha`),
 *   - the α-rebase (`mean_shift`): shifting the cut intercepts so α' ≈ 0
 *     when the reservoirs hit their `efin` end-of-horizon targets, with
 *     the removed constant added back as an objective constant `c̄`.  When
 *     more water than target is saved, α' < 0 (a future-cost credit); when
 *     less, α' > 0,
 *   - the user-overridable FCF (`use_user_alpha` / `user_alpha_uid`).
 *
 * A boundary cut always involves α.  Any other generic linear constraint on
 * the terminal state belongs in a `UserConstraint` (AMPL/PAMPL), not here.
 *
 * The legacy keys `sddp_options.boundary_*` / `missing_cut_var_mode` (SDDP /
 * cascade only), `monolithic_options.boundary_*` (monolithic only) and
 * `simulation.boundary_cuts_file` / `boundary_cuts_valuation` (any method)
 * are deprecated aliases: `resolve_future_cost_aliases` translates them into
 * this element once, after all JSON files and `--set` overrides merge.
 *
 * Both methods build/output this element through the standard
 * `FutureCostLP::add_to_lp` / `add_to_output` lifecycle, so α, the rebase
 * constant `c̄`, and the un-rebased value `α + c̄` are saved to the solution
 * like any other element's outputs.
 *
 * ### JSON Example
 * ```json
 * {
 *   "uid": 1,
 *   "name": "fcf",
 *   "cuts_file": "boundary_cuts.csv",
 *   "scale_alpha": 100000,
 *   "mean_shift": true,
 *   "sharing": "multicut",
 *   "iteration_limit": 10
 * }
 * ```
 */

#pragma once

#include <optional>

#include <gtopt/basic_types.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/planning_enums.hpp>
#include <gtopt/sddp_enums.hpp>

namespace gtopt
{

/**
 * @brief The future cost function (cost-to-go) as a first-class element.
 *
 * @see FutureCostLP for the LP build / output path.
 */
struct FutureCost
{
  static constexpr LPClassName class_name {"FutureCost"};

  Uid uid {unknown_uid};
  Name name {};
  OptActive active {};
  OptName description {};  ///< Optional free-text provenance.

  /// Boundary-cut file that linearises the FCF: rows
  /// `iteration,scene,rhs,<state...>` with `rhs` the FCF intercept and each
  /// reservoir coefficient `-water_value` ($/CMD) on that reservoir's
  /// terminal-volume state variable.  A relative path resolves against
  /// `options.input_directory` (identically for every method).  Deprecated
  /// aliases: `sddp_options.boundary_cuts_file`,
  /// `monolithic_options.boundary_cuts_file`, `simulation.boundary_cuts_file`.
  OptName cuts_file {};

  /// Numerical scale applied to the α / `varphi_s` columns (the LP
  /// conditioning factor on the cost-to-go variable).  When unset the LP
  /// auto-derives it from the cut coefficients (`boundary_cut_scale_alpha`);
  /// an explicit value overrides.  Monolithic reads only this field.  Under
  /// SDDP, `sddp_options.scale_alpha` remains the SDDP algorithm's α scale for
  /// every phase (it is NOT an alias); when this field is set it wins.
  OptReal scale_alpha {};

  /// Rebase the α at the reservoirs' `efin` end-of-horizon targets so α'
  /// is centred on ~0 when the targets are met (α' < 0 when more water is
  /// saved, > 0 when less).  Default `true`.  The removed constant is the
  /// per-scene `c̄` added back as an objective constant.  Deprecated alias:
  /// `sddp_options.boundary_cuts_mean_shift`.
  OptBool mean_shift {};

  /// When a SINGLE boundary cut is loaded for a scene, install it as the
  /// equality `α + Σ wvᵣ·efinᵣ = FCF` with α freed (`set_col_low_raw(-inf)`)
  /// — the continuous PLEXOS-style terminal value: α is pinned to
  /// `FCF − Σ wvᵣ·efinᵣ` so every reservoir's terminal storage is priced at
  /// `wvᵣ` on BOTH sides of the target.  Default (unset) = `true` = the
  /// current behaviour.  Set `false` to keep the slack-able `≥` Benders
  /// lower-bound form (α never freed: it keeps its column floor — the
  /// cut-derived box floor, raised by `sddp_options.alpha_min` when set) even
  /// for a single cut — e.g. for a faithful comparison against a
  /// user-authored `≥` FCF cut.  Without `alpha_min` the `≥` row on a
  /// minimised α binds at the optimum, so the two forms coincide.  With MANY
  /// cuts the `≥` max-envelope form is ALWAYS used regardless of this flag (the
  /// value function is the max of its supporting cuts).
  OptBool single_cut_equality {};

  /// Cross-scene cut sharing: `per_scene` (each scene keeps its own cut),
  /// `shared` (broadcast every cut onto every scene's terminal α), or
  /// `multicut` (N dedicated `varphi_s`, one per source scene, priced
  /// `1/N`).  Typed enum (no magic strings).  When unset it is derived from
  /// `mode` (`combined` → `shared`, otherwise `per_scene`).  Deprecated
  /// aliases: `{sddp,monolithic}_options.boundary_cut_sharing_mode`.
  std::optional<BoundaryCutSharingMode> sharing {};

  /// Boundary-cut load mode: `noload`, `separated` (per-scene, default),
  /// `combined` (broadcast all cuts to all scenes) or `phi_expectation`
  /// (PLP-literal NVarPhi plane-hydrology φ_j columns).  Typed enum.
  /// Deprecated aliases: `{sddp,monolithic}_options.boundary_cuts_mode`.
  std::optional<BoundaryCutsMode> mode {};

  /// Keep only the cuts of the last N iterations of the SOURCE run that
  /// produced `cuts_file` (distinct values of the CSV `iteration` column —
  /// PLP's `IPDNumIte`).  0 / unset = load every cut.  Named after PLEXOS'
  /// `FCFIterationLimit`.  Deprecated aliases:
  /// `{sddp,monolithic}_options.boundary_max_iterations`.
  OptInt iteration_limit {};

  /// How a cut row naming a state variable that is not in the model is
  /// handled: `skip_coeff` (default — drop that coefficient, keep the cut)
  /// or `skip_cut` (drop the whole cut when the missing coefficient is
  /// non-zero).  Only governs the loading of `cuts_file`.  Deprecated
  /// alias: `sddp_options.missing_cut_var_mode`.
  std::optional<MissingCutVarMode> missing_cut_var_mode {};

  /// Valuation basis of the cut RHS / coefficients: `end_of_horizon`
  /// (default, no discounting) or `present_value` (apply the last stage's
  /// effective discount factor on load).  Typed enum.  Deprecated alias:
  /// `simulation.boundary_cuts_valuation`.
  std::optional<BoundaryCutsValuation> valuation {};

  /// User-overridable FCF (piece 5 step 2a).  When `true`, the FCF cost-to-go
  /// α is NOT the built-in `varphi_s` column priced `1/N`; instead the modeller
  /// authors a global `state`/`link` `DecisionVariable` (the user α, with
  /// `cost = 1.0`, `cost_type = "raw"`) plus global `UserConstraint` cut row(s)
  /// over it, and points `user_alpha_uid` at that variable.  The built-in α
  /// column is then registered INERT (pinned `lowb = uppb = 0`, `cost = 0`) so
  /// it neither prices nor floors anything — the user's recourse fully replaces
  /// the boundary-cut FCF.  Mutually exclusive with `cuts_file`
  /// (or any of its deprecated aliases): supplying both is a runtime error.
  /// Default unset ⇒ `false` (the byte-for-byte legacy boundary-cut path).
  OptBool use_user_alpha {};

  /// UID of the user-authored α `DecisionVariable` (piece 5 step 2a).  Required
  /// when `use_user_alpha` is `true`: identifies the global `state`/`link`
  /// column that holds the cost-to-go so `add_to_output` can emit it as
  /// `FutureCost/alpha` and the forward-pass UB estimator can subtract its
  /// realised value.  Typed `Uid` (not a name) so it resolves through the
  /// state-variable registry without a string lookup.
  OptUid user_alpha_uid {};
};

}  // namespace gtopt
