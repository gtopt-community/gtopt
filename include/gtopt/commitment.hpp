/**
 * @file      commitment.hpp
 * @brief     Unit commitment data for generator on/off scheduling
 * @date      Tue Apr  8 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Defines the Commitment structure which specifies unit commitment parameters
 * for a generator: startup/shutdown costs, no-load cost, minimum up/down
 * times, and initial conditions.  Each Commitment entry links to exactly one
 * Generator via a foreign key.
 *
 * Commitment constraints are only enforced on stages marked as chronological.
 * On non-chronological stages (e.g. duration-weighted representative blocks)
 * the commitment is silently skipped and the generator dispatches normally.
 *
 * ### JSON Example
 * ```json
 * {
 *   "uid": 1,
 *   "name": "thermal1_uc",
 *   "generator": "thermal1",
 *   "startup_cost": 5000,
 *   "shutdown_cost": 1000,
 *   "noload_cost": 50,
 *   "min_up_time": 4,
 *   "min_down_time": 2,
 *   "initial_status": 1,
 *   "initial_hours": 8
 * }
 * ```
 *
 * @see CommitmentLP for the LP formulation (three-bin u/v/w)
 * @see Generator for the linked generation unit
 */

#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include <gtopt/enum_option.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/transition_integrality.hpp>

namespace gtopt
{

/// Scope of ``Commitment::max_starts`` — defines the rolling time window
/// over which the LP cap ``Σ startup ≤ max_starts`` is enforced.  Mirrors
/// PLEXOS's per-Generator ``Max Starts {Hour|Day|Week|Month|Year}``
/// properties (prop_id 203..207); the longer per-month / per-year scopes
/// collapse to ``Horizon`` here because a typical gtopt stage is shorter
/// than a month.  See ``Commitment::max_starts`` docstring for the full
/// LP-row emission rule.
enum class StartsScope : std::uint8_t
{
  Hour = 0,  ///< One LP row per single-period window
  Day = 1,  ///< One LP row per 24h cumulative-duration window
  Week = 2,  ///< One LP row per 7×24h window (CEN PCP target)
  Horizon = 3,  ///< One LP row per stage; default + collapse-target
                ///< for per-month / per-year PLEXOS scopes
};

inline constexpr auto starts_scope_entries =
    std::to_array<EnumEntry<StartsScope>>({
        {.name = "hour", .value = StartsScope::Hour},
        {.name = "day", .value = StartsScope::Day},
        {.name = "week", .value = StartsScope::Week},
        {.name = "horizon", .value = StartsScope::Horizon},
        // Aliases for the longer PLEXOS scopes that we collapse to
        // Horizon — kept so a JSON faithfully transcribed from PLEXOS
        // parses without surfacing a misleading "expected: hour, day,
        // week, horizon" error.
        {.name = "month", .value = StartsScope::Horizon, .is_alias = true},
        {.name = "year", .value = StartsScope::Horizon, .is_alias = true},
    });

[[nodiscard]] constexpr auto enum_entries(StartsScope) noexcept
{
  return std::span {starts_scope_entries};
}

/// Two-shape scope value for ``Commitment::starts_scope``:
///
///   * ``Name``  — a NamedEnum entry: ``"hour" | "day" | "week" |
///                 "horizon"`` (also ``"month" | "year"`` aliasing to
///                 Horizon).  Convenient + PLEXOS-faithful.
///   * ``Int``   — an explicit window length in HOURS (e.g. ``48`` for
///                 a 2-day window, ``336`` for a fortnight, ``720`` for
///                 a calendar month).  Lets users express arbitrary
///                 rolling windows the NamedEnum doesn't cover.
///
/// Resolved to a window length [hours] at LP-build time by
/// ``Commitment::starts_window_hours()`` — a value of 0 (or unset,
/// or any unrecognised name) means "horizon = never flush until stage
/// end".
// Int FIRST: keeps variant alternative ordering aligned with the
// ``json_variant_type_list<Int, Name>`` in ``json_commitment.hpp``
// (daw::json maps alternative index → type list index 1:1 during
// serialisation, so a mismatch corrupts the JSON-out path even though
// JSON-in still parses).  See ``json_single_id.hpp`` for the same
// precedent (``variant<Uid, Name>`` ↔ ``json_variant_type_list<Uid, Name>``).
using StartsScopeValue = std::variant<Int, Name>;
using OptStartsScope = std::optional<StartsScopeValue>;

/// ``Commitment.commitment_period``: a length in hours (Real, variant
/// alternative 0 — keep aligned with ``json_variant_type_list<Real, Name>``
/// in ``json_commitment.hpp``) or ``"auto"`` (Name).
using CommitmentPeriodValue = std::variant<Real, Name>;
using OptCommitmentPeriod = std::optional<CommitmentPeriodValue>;

/**
 * @struct Commitment
 * @brief Unit commitment parameters for a generator
 *
 * Links to a Generator and defines the three-bin UC model parameters:
 * - u (status): binary, 1 = online
 * - v (startup): binary, 1 = started up this block
 * - w (shutdown): binary, 1 = shut down this block
 *
 * Constraints:
 * - C1 (logic): u[t] - u[t-1] = v[t] - w[t]
 * - C2 (gen limits): Pmin*u <= p <= Pmax*u
 * - C3 (exclusion): v[t] + w[t] <= 1
 */
struct Commitment
{
  /// Canonical class-name constant used in LP row labels and config
  /// fields like `VariableScale::class_name`.  Single source of truth —
  /// `CommitmentLP` exposes no separate `ClassName` member; callers
  /// reach the constant via `Commitment::class_name` directly (or
  /// `CommitmentLP::Element::class_name` in generic contexts).
  static constexpr LPClassName class_name {"Commitment"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< Optional element type/category tag
  OptName description {};  ///< Optional free-text description (e.g. conversion
                           ///< provenance)

  SingleId generator {unknown_uid};  ///< FK to the Generator

  // ``fuel`` / ``pmax_segments`` / ``heat_rate_segments`` / ``fuel_cost``
  // / ``fuel_emission_factor`` were removed from Commitment on
  // 2026-05-20 — those fields are dispatch-cost properties of the
  // *Generator*, not of the commitment binary.  Use
  // ``Generator.fuel`` / ``Generator.pmax_segments`` /
  // ``Generator.heat_rate_segments`` instead; ``GeneratorLP`` builds
  // the piecewise cost as a pure-LP convex-slack formulation that
  // works with or without a Commitment binary (see
  // ``source/generator_lp.cpp:272+``).

  /// Startup cost [$/start], **per-(stage, block)** schedulable.
  ///
  /// Accepts the same value forms as `Generator.pmax`: a scalar
  /// constant, a `[stage]` 1-D array, a `[stage][block]` 2-D matrix, or
  /// a filename string referencing a schedule.  Scalar / per-stage
  /// values broadcast to every block (back-compat with the legacy
  /// per-stage `startup_cost`).  This lets a single-stage PLEXOS
  /// conversion carry the per-period Gen_StartCost profile across the
  /// horizon instead of collapsing it to one value.  Applied to the
  /// startup (`v`) variable in the objective, resolved block-by-block.
  OptTBRealFieldSched startup_cost {};  ///< Startup cost [$/start]
  /// Shutdown cost [$/stop], **per-(stage, block)** schedulable — same
  /// value forms and broadcast semantics as `startup_cost`.  Applied to
  /// the shutdown (`w`) variable in the objective, resolved per block.
  OptTBRealFieldSched shutdown_cost {};  ///< Shutdown cost [$/stop]
  OptReal noload_cost {};  ///< No-load cost when committed [$/hr]
  /// No-load fuel burn while committed [fuel-unit / h] (PLEXOS
  /// ``Heat Rate Base``).  Accounting only: it adds
  /// ``noload_fuel · u · dt`` to the generator's fuel burn seen by
  /// ``Fuel`` offtake limits and fuel-basis emission sources
  /// (``EmissionSource.fuel_rate``).  Its fuel *cost* stays in
  /// ``noload_cost`` (not re-priced from ``Fuel.price``).
  OptReal noload_fuel {};

  /// Minimum stable level when committed [MW].  Distinct from
  /// ``Generator.pmin`` (which is the *always-on* hard floor that
  /// applies regardless of commitment).  When this field is set,
  /// ``commitment_lp.cpp`` uses it as the per-unit minimum
  /// (``gen ≥ pmin × u_commit``) and leaves ``Generator.pmin`` alone
  /// as the unconditional dispatch floor.  Defaults to falling back
  /// on ``lp.get_col_lowb(gcol)`` (legacy behaviour) when unset.
  /// PLEXOS analogue: ``Generator.Min Stable Level`` is
  /// commitment-conditional per the official Energy Exemplar docs,
  /// so the plexos2gtopt converter writes Min Stable Level here
  /// (not into Generator.pmin) and keeps Generator.pmin = 0.
  ///
  /// Per-(stage, block) schedule: PLEXOS ``Min Stable Level`` is a
  /// time series (e.g. CEN PCP coal units carry 98.53 MW for most of
  /// the week and 170.53 MW for a few hours), so this is a
  /// ``FieldSched`` accepting either a scalar (constant floor, the
  /// common case) or a per-block vector.  ``commitment_lp.cpp``
  /// resolves it block-by-block.
  OptTBRealFieldSched pmin {};

  OptReal min_up_time {};  ///< Minimum up time [hours]
  OptReal min_down_time {};  ///< Minimum down time [hours]

  /// Per-(stage, block) ramp limits / startup-shutdown output envelopes.
  /// Each accepts either a scalar (constant rate, the common case) or a
  /// per-block vector; ``commitment_lp.cpp`` resolves them block-by-block.
  /// Scalar inputs produce identical LP rows to the legacy ``OptReal``
  /// fields (the schedule resolves to the same constant on every block).
  OptTBRealFieldSched ramp_up {};  ///< Ramp-up limit [MW/hr] while online
  OptTBRealFieldSched ramp_down {};  ///< Ramp-down limit [MW/hr] while online
  OptTBRealFieldSched startup_ramp {};  ///< Max output in startup block [MW]
  OptTBRealFieldSched shutdown_ramp {};  ///< Max output in shutdown block [MW]

  OptReal initial_status {};  ///< Initial on/off (1.0 = online, 0.0 = offline)
  OptReal initial_hours {};  ///< Hours in current state at t=0

  /// Raw PLEXOS ``Gen_IniHoursUp.csv`` value [h]: hours the unit has
  /// been continuously online at t = 0.  Informational round-trip
  /// field for the plexos2gtopt converter — the LP uses the collapsed
  /// signed ``initial_hours`` (positive when ``initial_status = 1``)
  /// for min-up / min-down enforcement.  Carrying the raw pair lets
  /// downstream tooling (audits, PLEXOS diffs) inspect the original
  /// values without re-reading the CSV.  Unset → not published.
  OptReal ini_hours_up {};
  /// Raw PLEXOS ``Gen_IniHoursDown.csv`` value [h]: hours the unit
  /// has been continuously offline at t = 0.  See ``ini_hours_up``
  /// for the round-trip rationale.
  OptReal ini_hours_down {};
  /// Initial power output [MW] at ``t = -1``.  When set, the
  /// first-block ramp-up / ramp-down rows include the
  /// ``p_prev = initial_power`` term:
  ///
  ///   p[0] − initial_power ≤ RU·u_init + SU·(1 − u_init)
  ///   initial_power − p[0] ≤ RD·u[0] + SD·w[0]
  ///
  /// instead of the looser ``p_prev = 0`` form that the previous
  /// "for simplicity" stub used (commit ID predates this field).
  ///
  /// When ``std::nullopt`` (default), the legacy ``p_prev = 0``
  /// behaviour is preserved — correct for cold starts and any
  /// generator where the converter / user didn't supply a value.
  ///
  /// Required to round-trip UC.jl's ``Initial power (MW)`` on
  /// hot-start generators with ``pmin > 0`` (e.g. RTS-GMLC
  /// ``216_STEAM_1``: pmin = 62, ramp_up = 60, initial_power = 62 —
  /// without this field the first-block LP is infeasible because
  /// the pmin floor of 62 collides with the ramp-up cap of 60).
  /// Unset → ``Generator.initial_generation`` at the horizon start
  /// (first stage of the first phase), else 0.
  OptReal initial_power {};

  OptBool relax {};  ///< LP relaxation: u/v/w continuous in [0,1]
  OptBool must_run {};  ///< Force committed: u = 1 always

  /// Fast-start unit for `model_options.pricing_mode = "fast_start"`:
  /// its u/v/w columns are relaxed to continuous [0,1] in the pricing LP
  /// (the rest of the integers stay pinned at the MIP incumbent), so its
  /// pmin / no-load / startup costs become divisible and can set the
  /// price.  Explicit `true`/`false` wins; unset applies the auto rule
  /// `min_up_time <= H && min_down_time <= H` with
  /// `H = model_options.fast_start_max_hours` (default 1 h; unset min
  /// up/down count as 0).  No effect on the MIP or the dispatch.
  OptBool fast_start {};

  /// Integrality of this unit's startup / shutdown columns (v / w):
  /// `"auto"` (binary), `"continuous"` or `"binary"`.  Unset →
  /// `model_options.transition_integrality` (default `"auto"`).  See
  /// `TransitionIntegrality`.  `relax` still relaxes u, v, w together.
  OptName transition_integrality {};

  /// REMOVED 2026-11-26 (see `ModelOptions::transition_status_rows`):
  /// parsed so old inputs load, ignored with a warning.
  OptBoolOrName transition_status_rows {};

  /// Integrality of this unit's hot / warm / cold startup-tier
  /// selectors: `"continuous"` (TU tier rows) or `"integer"`.  Unset →
  /// `model_options.startup_tier_integrality` (default `"continuous"`).
  OptName startup_tier_integrality {};

  /// Per-(stage, block) forced commitment status — pins the ``u`` variable
  /// to a specific value at each (stage, block) where it is set.  Values
  /// are interpreted as ``1.0 → committed``, ``0.0 → not committed``;
  /// blocks where the schedule has no entry leave ``u`` free.  Generalises
  /// the all-or-nothing ``must_run`` flag to schedules like UC.jl's
  /// per-block ``Commitment status: [true, false, true, ...]`` field
  /// (case14/fixed.json), and is also useful for hot-start / scenario-
  /// tracing replay where a previous solve's commitment is pinned.
  /// Overrides ``must_run`` per block — if both are set, the per-block
  /// ``fixed_status`` value wins for the blocks it covers and
  /// ``must_run`` covers the remainder.
  OptTBRealFieldSched fixed_status {};

  /// Binary variable period [hours].  When set, u/v/w variables are created
  /// at a coarser time resolution than the generation blocks.  For example,
  /// `commitment_period = 2.0` with 15-minute blocks yields one binary
  /// variable per 2-hour window (8 blocks), while generation variables
  /// remain at 15-minute resolution.  Default (nullopt) = one per block.
  ///
  /// Periods are unions of whole cells of the stage partition (blocks;
  /// quadrature cells on `Stage.sampling = "point"` stages): see
  /// `duration_groups` in cell_partition.hpp.  Uniform block stages keep
  /// the legacy accumulated grouping.
  ///
  /// `"auto"`: P = max(smallest cell, ½·min(min_up_time,
  /// min_down_time)); per-block binaries when neither min_up_time nor
  /// min_down_time is set.
  OptCommitmentPeriod commitment_period {};

  /// Commitment grid: `"block"` — one status per block / cell (min up /
  /// down, starts and hours-on in the real cell durations, also for
  /// variable durations); `"overlay"` — a UC grid of `commitment_period`
  /// periods laid over the blocks (each block / cell joins its period by
  /// `duration_groups`, the status is shared by the blocks of a period).
  /// Default: `"block"` when `commitment_period` is unset, `"overlay"`
  /// when set.  `"block"` with a `commitment_period`, or `"overlay"`
  /// without one, is a validation error.
  ///
  /// Point stages (`Stage.sampling = "point"`): unset / `"overlay"` = the
  /// unit's uniform grid of max(commitment_period, 1 h) cells, nodes
  /// mapped by their time (never one status per node).  The previous
  /// behaviour stays reachable: `"block"` = one status per node, `"cells"`
  /// (needs `commitment_period`) = periods as unions of the quadrature
  /// cells (midpoint rule, see cell_partition.hpp).  On block stages
  /// `"cells"` equals `"overlay"`.
  OptName commitment_grid {};

  /// Start-up / shut-down allowance of the ramp rows: `"boundary"`
  /// (default, master's behaviour since the commitment tightening) —
  /// SU·v / SD·w only on the ramp rows that cross a commitment-period
  /// boundary (inside a period the plain ramp limit holds); `"period"` —
  /// every block of a commitment period gets the allowance of its
  /// period's v / w (the older, looser form).  Without
  /// `commitment_period` every block is its own period and both are
  /// identical.
  OptName ramp_allowance {};

  /// Resolved commitment grid: true for the overlay grid.
  [[nodiscard]] bool uses_overlay_grid() const noexcept
  {
    return commitment_grid
        ? (*commitment_grid == "overlay" || *commitment_grid == "cells")
        : commitment_period.has_value();
  }

  /// Point stages keep the previous grid (`"block"` / `"cells"`).
  [[nodiscard]] bool uses_legacy_point_grid() const noexcept
  {
    return commitment_grid
        && (*commitment_grid == "block" || *commitment_grid == "cells");
  }

  /// True for the default boundary-only ramp allowance.
  [[nodiscard]] bool ramp_allowance_at_boundary() const noexcept
  {
    return ramp_allowance.value_or("boundary") != "period";
  }

  /// Cyclic commitment: `"none"` (default, chronological from
  /// `initial_status`) or `"stage"` — the stage is a typical day / week
  /// repeated in a cycle: the first period's transition wraps to the
  /// last (u_1 − u_P = v_1 − w_1, no initial state), min up/down and
  /// hot/warm/cold windows wrap around the cycle, ramps wrap (backward
  /// from the last block; central2 skipping the duplicated t_N ≡ t_0,
  /// which shares the status of t_0), and start / shutdown costs per
  /// cycle are scaled by the stage weight `duration / period_hours`.
  /// `initial_status` / `initial_hours` / `initial_power` are ignored.
  OptName cycle {};

  /// True when `cycle = "stage"`.
  [[nodiscard]] bool is_cyclic() const noexcept
  {
    return cycle.value_or("none") == "stage";
  }

  /// Explicit handling of a cyclic unit whose time constraints do not
  /// fit the typical period (min_up_time, min_down_time or a finite
  /// start window longer than the cycle length T = Σ real cell
  /// durations of the stage).  The constraints that cannot be honoured
  /// within the cycle are NOT emitted (one warning per unit):
  ///  - `"on"`: committed (u = 1) for the whole typical period: pmin ..
  ///    pmax, no starts / stops inside it;
  ///  - `"off"`: unavailable (u = 0, output 0);
  ///  - `"no_uc"`: dispatched without commitment — no u/v/w, output
  ///    0 .. pmax as a continuous generator (pmin relaxed to 0, no
  ///    start costs, ramps kept).
  /// Unset: `model_options.long_transition`; when neither is set a long
  /// unit is a validation error (no silent dropping).
  OptName long_transition {};

  /// Per-stage override of `long_transition` ("on" / "off" / "no_uc",
  /// by stage position; an empty entry or a short list falls back to
  /// `long_transition`) — e.g. on in winter months, off in summer.
  std::vector<Name> long_transition_schedule {};

  /// Declared long-transition mode at stage position @p stage_pos
  /// (schedule, unit, then planning default @p dflt); empty when
  /// undeclared.
  [[nodiscard]] std::string_view long_transition_at(
      std::size_t stage_pos, const OptName& dflt) const noexcept
  {
    if (stage_pos < long_transition_schedule.size()
        && !long_transition_schedule[stage_pos].empty())
    {
      return long_transition_schedule[stage_pos];
    }
    if (long_transition) {
      return *long_transition;
    }
    if (dflt) {
      return *dflt;
    }
    return {};
  }

  /// Time constraints of this unit longer than a cycle of @p cycle_hours:
  /// (name, hours) pairs.  Empty for non-cyclic units.
  [[nodiscard]] std::vector<std::pair<std::string_view, double>>
  long_constraints(double cycle_hours) const
  {
    std::vector<std::pair<std::string_view, double>> out;
    if (!is_cyclic() || cycle_hours <= 0.0) {
      return out;
    }
    const auto add = [&](std::string_view what, double hours)
    {
      if (hours > cycle_hours + 1e-9) {
        out.emplace_back(what, hours);
      }
    };
    add("min_up_time", min_up_time.value_or(0.0));
    add("min_down_time", min_down_time.value_or(0.0));
    if (max_starts || min_starts) {
      add("starts_scope", starts_window_hours());
    }
    return out;
  }

  /// Resolved `commitment_period` [h] for a stage whose smallest time
  /// step is @p resolution (0 = one period per block).
  [[nodiscard]] double commitment_period_hours(double resolution) const
  {
    if (!commitment_period || !uses_overlay_grid()) {
      return 0.0;
    }
    if (const auto* hours = std::get_if<Real>(&*commitment_period)) {
      return std::max(*hours, 0.0);
    }
    if (std::get<Name>(*commitment_period) != "auto") {
      return 0.0;
    }
    double mut_mdt = 0.0;
    if (min_up_time && min_down_time) {
      mut_mdt = std::min(*min_up_time, *min_down_time);
    } else if (min_up_time) {
      mut_mdt = *min_up_time;
    } else if (min_down_time) {
      mut_mdt = *min_down_time;
    }
    if (mut_mdt <= 0.0) {
      return 0.0;
    }
    return std::max(resolution, 0.5 * mut_mdt);
  }

  // Piecewise heat-rate curve (``pmax_segments`` /
  // ``heat_rate_segments``) and the legacy ``fuel_cost`` /
  // ``fuel_emission_factor`` schedules were removed from Commitment
  // on 2026-05-20.  They are dispatch-cost properties belonging on
  // ``Generator``; see the comment near ``generator`` above for
  // migration guidance.

  /// @name Startup cost tiers (hot/warm/cold)
  /// When all five fields are present, the single startup_cost is replaced
  /// by three tier-dependent costs based on offline duration.
  /// hot_start_time < cold_start_time; offline < hot → hot cost, etc.
  /// @{
  OptReal hot_start_cost {};  ///< Startup cost when recently offline [$/start]
  OptReal warm_start_cost {};  ///< Startup cost at medium offline [$/start]
  OptReal cold_start_cost {};  ///< Startup cost when long offline [$/start]
  OptReal hot_start_time {};  ///< Max offline hours for hot start [h]
  OptReal cold_start_time {};  ///< Min offline hours for cold start [h]
  /// @}

  /// @name Startup-count bounds (PLEXOS ``Max Starts {Hour|Day|Week|...}``
  /// + symmetric ``Min Starts`` floor for forced-commitment).
  ///
  /// Two-sided bound on the number of startup events within a rolling
  /// time window.  The LP emits at most TWO rows per (commitment,
  /// window) pair — one for each side that's set:
  ///
  ///     min_starts  ≤  Σ_{block in window} startup_col[block]  ≤  max_starts
  ///
  /// where ``window`` is determined by the SHARED ``starts_scope``:
  ///   - ``"hour"``    → 1 row per block (effectively startup ≤/≥ N/h)
  ///   - ``"day"``     → 1 row per 24 h window (cumulative-duration
  ///                     boundary flush, like ``UserConstraint.daily_sum``)
  ///   - ``"week"``    → 1 row per 7×24 h window
  ///   - ``"horizon"`` (default) → 1 row per stage, Σ over all blocks
  ///   - integer hour count (e.g. 48, 336, 720) → per-window with that
  ///     length, for arbitrary scopes the NamedEnum doesn't cover
  ///
  /// PLEXOS exposes the cap family on its Generator class (prop 202..207:
  /// per-horizon / hour / day / week / month / year) — month/year alias
  /// to Horizon here because a typical gtopt stage is shorter than a
  /// month.  PLEXOS doesn't ship a symmetric ``Min Starts`` property,
  /// but the same per-window mechanism naturally supports a floor too —
  /// useful for forced-commitment patterns and as a complement when
  /// adapting ``Min Energy {Day|Week}`` / ``Min CF`` to a startup-count
  /// surrogate.
  ///
  /// Defaults (unset semantics):
  ///   * ``max_starts`` unset → +∞ (no cap; no upper row emitted)
  ///   * ``min_starts`` unset → 0  (no floor; no lower row emitted)
  /// When BOTH are set to the same value the constraint pins the
  /// per-window startup count to exactly that value (two opposing
  /// inequality rows; CPLEX presolve detects the equality).
  ///
  /// Both bounds are HARD (no soft-slack tier).  PLEXOS exposes
  /// ``Max Starts Penalty`` (prop 208) but CEN PCP never populates it;
  /// surface it here only if a future case ships non-zero values.
  OptInt max_starts {};
  OptInt min_starts {};
  /// Window-scope for ``max_starts``.  Accepts EITHER a named
  /// StartsScope (``"hour" | "day" | "week" | "horizon"``, plus
  /// ``"month" | "year"`` aliasing to Horizon) OR a positive integer
  /// number of HOURS (e.g. ``48`` for a 2-day window, ``720`` for a
  /// calendar month).  See ``StartsScopeValue`` for the variant
  /// definition; ``starts_window_hours()`` resolves both shapes to
  /// the LP-side window length.
  OptStartsScope starts_scope {};

  /// Per-unit UC formulation: `"energy_block"` or `"power_based"`
  /// (see `ModelOptions::uc_formulation`, which it overrides).
  OptName uc_formulation {};
  /// Power-based UC only: start-up power trajectory
  /// `[P^SU_1, ..., P^SU_S]` [MW] — the power at the successive
  /// `trajectory_step` instants of the start-up process, before the
  /// unit reaches its minimum output at the start of the up period
  /// (Morales-España et al., OR Spectrum 2015, eq. 12).  Empty: a
  /// quick-start unit (one block from 0 to pmin..startup_ramp).
  std::vector<Real> startup_trajectory {};
  /// Power-based UC only: shut-down power trajectory
  /// `[P^SD_2, ..., P^SD_{S+1}]` [MW] — the power at the successive
  /// `trajectory_step` instants after the unit leaves its minimum
  /// output (the last entry is normally 0).
  std::vector<Real> shutdown_trajectory {};
  /// Spacing of the trajectory samples [h] (default 1).
  OptReal trajectory_step {};

  /// Resolved per-unit formulation: true for power-based, given the
  /// planning-wide default @p global_power_based.
  [[nodiscard]] bool is_power_based(bool global_power_based) const noexcept
  {
    return uc_formulation ? *uc_formulation == "power_based"
                          : global_power_based;
  }

  /// Resolve ``starts_scope`` (either a named StartsScope or an
  /// integer hour count) to the rolling window length in HOURS used by
  /// ``CommitmentLP::add_to_lp`` to decide when to flush the
  /// ``Σ_{p ∈ window} v[p] ≤ max_starts`` accumulator row.
  ///
  ///   * unset → ``0.0`` (treated as Horizon — single row per stage)
  ///   * Int variant → that many hours (clamped to 0 if non-positive)
  ///   * Name variant → enum-resolved hours:
  ///       Hour=1, Day=24, Week=168, Horizon=0
  ///       month/year aliases → Horizon (0)
  ///       unrecognised string → Horizon (0)
  [[nodiscard]] constexpr double starts_window_hours() const noexcept
  {
    if (!starts_scope.has_value()) {
      return 0.0;
    }
    if (std::holds_alternative<Int>(*starts_scope)) {
      const auto h = std::get<Int>(*starts_scope);
      return h > 0 ? static_cast<double>(h) : 0.0;
    }
    // Name variant — resolve through the NamedEnum table.
    const auto& name = std::get<Name>(*starts_scope);
    const auto resolved = enum_from_name<StartsScope>(name);
    if (!resolved.has_value()) {
      return 0.0;  // unrecognised → Horizon
    }
    switch (*resolved) {
      case StartsScope::Hour:
        return 1.0;
      case StartsScope::Day:
        return 24.0;
      case StartsScope::Week:
        return 7.0 * 24.0;
      case StartsScope::Horizon:
        return 0.0;
    }
    return 0.0;
  }

  /// Symbolic view of ``starts_scope`` for diagnostic / test use.
  /// Returns ``Horizon`` when unset, when the variant holds an Int
  /// (the explicit hour count has no enum entry), or when the Name
  /// doesn't match an enum entry.  Use ``starts_window_hours()``
  /// for the LP-side resolved window length.
  [[nodiscard]] constexpr StartsScope starts_scope_enum() const noexcept
  {
    if (!starts_scope.has_value() || std::holds_alternative<Int>(*starts_scope))
    {
      return StartsScope::Horizon;
    }
    if (const auto e =
            enum_from_name<StartsScope>(std::get<Name>(*starts_scope)))
    {
      return *e;
    }
    return StartsScope::Horizon;
  }
};

}  // namespace gtopt
