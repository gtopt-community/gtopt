/**
 * @file      decision_variable.hpp
 * @brief     Decision Variable (continuous or integer) referenced by
 * UserConstraints
 * @date      Mon May 19 21:55:00 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Defines the DecisionVariable structure — a per-(scenario, stage, block)
 * free continuous LP column that user constraints can reference via the
 * AMPL-style accessor ``decision_variable("X").value``.  Mirrors the
 * PLEXOS ``Decision Variable`` class (class_id 72) used in the CEN PCP
 * daily bundle for reserve-allocation knobs (BESS_*_LW/RS, CPF_*, …) and
 * generic free parameters.
 *
 * ### JSON Example
 * ```json
 * {
 *   "uid": 1,
 *   "name": "bess_angamos_lw",
 *   "lower_bound": -1000,
 *   "upper_bound":  1000,
 *   "cost": 0
 * }
 * ```
 *
 * The optional ``cost`` adds the column to the LP objective.  How it
 * folds in depends on ``cost_type``; the default is ``"raw"`` (face value,
 * NO probability/discount/duration weighting), matching the PLEXOS penalty
 * DecisionVariables.  When unset, the lower bound defaults to ``0``
 * (non-negative — never a free unbounded-below column, which breaks GPU
 * first-order solvers) and the upper bound to ``+LP_INFINITY``; an
 * α-rebased column (``obj_constant`` set) instead defaults free below.
 */

#pragma once

#include <gtopt/basic_types.hpp>
#include <gtopt/field_sched.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @brief A free continuous LP column with optional bounds and cost.
 *
 * @see DecisionVariableLP for the LP build path
 */
struct DecisionVariable
{
  static constexpr LPClassName class_name {"DecisionVariable"};

  Uid uid {unknown_uid};
  Name name {};
  OptActive active {};
  OptName type {};  ///< Optional element type/category tag
  OptName description {};  ///< Optional free-text description (e.g. conversion
                           ///< provenance)

  /// Lower bound on the LP column.  When unset the LP defaults the column
  /// to ``≥ 0`` (non-negative) so it never emits a *free* unbounded-below
  /// column (which breaks GPU first-order / heuristic solvers); set
  /// explicitly to a negative value (or ``-LP_INFINITY``) to make the
  /// column free below.  EXCEPTION: when ``obj_constant`` is set (an
  /// α-rebased column whose value can be negative), an unset
  /// ``lower_bound`` defaults to ``-LP_INFINITY`` (free below) instead.
  OptReal lower_bound {};

  /// Upper bound on the LP column.  When unset the LP treats the
  /// column as ``≤ LP_INFINITY`` (free above).
  OptReal upper_bound {};

  /// Objective contribution per unit of ``value``.
  ///
  /// How it folds into the objective depends on ``cost_type``:
  ///  - ``"raw"`` (default): ``value`` is a face-value money / unitless
  ///    amount (a PLEXOS penalty knob, reserve VoRS, the FCF cost-to-go
  ///    ``alpha_fcf``, …), so the term is just ``cost · value`` at face
  ///    value — NO probability, discount, or block-duration weighting.
  ///  - ``"power"``: ``value`` is a rate (MW), so the term is
  ///    ``cost · value · block_duration`` (``CostHelper::block_ecost`` —
  ///    same convention as ``Generator.gcost``).
  ///  - ``"energy"``: ``value`` is already a total (MWh), so the term is
  ///    ``cost · value`` with NO block-duration multiply — only
  ///    probability × discount.
  OptReal cost {};

  /// Cost interpretation for the objective: ``"raw"`` (default, face value,
  /// no prob/discount/duration), ``"power"`` (duration-weighted) or
  /// ``"energy"`` (prob × discount, not duration-weighted).  Parsed as
  /// ``ConstraintScaleType``.
  OptName cost_type {};

  /// Time-granularity at which this decision variable's LP column is
  /// instantiated (``"block"`` default, ``"stage"``, ``"phase"``,
  /// ``"global"``).  Parsed as ``ConstraintScope`` (shared with
  /// ``UserConstraint``).  Coarser than the single-``block`` selector below:
  /// ``stage`` → one column per (scenario, stage); ``phase`` / ``global`` →
  /// one column per (scene, phase) cell (the global FCF α, annual knobs).
  ///
  /// Default unset ⇒ ``block`` (every legacy JSON round-trips unchanged).
  OptName scope {};

  /// Optional single-block scope (block ``uid``).  When set, the LP
  /// creates the column ONLY on that block instead of one per block —
  /// used for end-of-horizon quantities like the FCF cost-to-go
  /// ``alpha_fcf``, which must be a single last-block variable (a
  /// per-block column lets the unconstrained blocks distort the
  /// objective).  When unset, the default per-(scenario, stage, block)
  /// column set is created.
  OptUid block {};

  /// AMPL **state variable** flag (piece 5).  When ``true`` this column is
  /// registered as a cross-phase ``StateVariable`` (via
  /// ``SystemContext::add_state_col``) so the SDDP backward pass couples it
  /// across phases and includes it in every optimality / feasibility cut —
  /// the same machinery the reservoir ``efin`` and the built-in FCF α use,
  /// exposed to the AMPL author.  A state variable MUST be coarse-scoped
  /// (``scope`` = ``stage`` / ``phase`` / ``global``) — one column per
  /// (scene, phase) cell — NEVER per ``block`` (a per-block column has no
  /// single end-of-phase value to propagate).  It also REQUIRES ``link``:
  /// see below.  Default unset ⇒ ``false`` (a plain free column) —
  /// EXCEPT for ``scope = global`` in a run with more than one phase, where
  /// unset means an implicit ``state: true, link: true`` (one value for the
  /// whole horizon, cost charged in the first phase only); an explicit
  /// ``false`` there opts out (independent per-phase copies, with a
  /// warning).  See docs/user-constraints.md.
  OptBool state {};

  /// Cross-phase **link** flag for a ``state`` variable (piece 5).  When
  /// ``true``, this phase's column is linked to the SAME variable's column
  /// in the PREVIOUS phase (this phase's start value = previous phase's end
  /// value), via ``SystemContext::defer_state_link``.  The previous-phase
  /// ``StateVariable::Key`` is synthesized from this variable's own identity
  /// (uid + ``value`` col + a dedicated user-state ``class_name`` prefix) so
  /// it round-trips through cut I/O without colliding with engine state
  /// (reservoir efin, built-in α).  ``state: true`` WITHOUT ``link`` is a
  /// hard error at build time — an un-linked state variable would silently
  /// decouple the phases.  Ignored when ``state`` is unset / ``false``.
  OptBool link {};

  /// **Nonanticipative** flag for a ``scope = global`` variable.  gtopt
  /// builds one LP per (scene, phase) cell, so a global column is shared
  /// by the scenarios of a scene but may differ between scenes.  When
  /// ``true`` the value is ONE decision for every scene (taken before the
  /// scene — e.g. the hydrology — is known): the active scenes are solved
  /// as ONE extensive-form LP per phase in which this variable is a
  /// single column (each scenario keeps its own data, probability and
  /// state).  Its probability-weighted cost uses the total probability
  /// mass, charged once.  Requires ``scope = global`` (hard error
  /// otherwise).  Default unset ⇒ ``false``.  See
  /// docs/user-constraints.md ("Nonanticipative across scenes").
  OptBool nonanticipative {};

  /// Per-block storage-state flag — makes this `block`-scoped variable behave
  /// like a reservoir volume: the per-block columns are storage volumes, a
  /// cross-phase INCOMING column (`value_in`) is created and pinned by the
  /// SDDP forward pass to the previous phase's end-of-phase value, and the
  /// last block's column is registered as the cross-phase state.  Combined
  /// with a `prev(...)` reference in a user constraint this expresses a
  /// per-block volume balance `vol[b] = prev(vol) + inflow - release`.
  /// Requires `link: true`; must be block-scoped; mutually exclusive with the
  /// coarse `state` flag.  Default unset ⇒ false.
  OptBool block_state {};

  /// Initial value (`eini`-equivalent) for a `block_state` variable: the
  /// incoming column on the first stage of the first phase is fixed to this.
  /// Unset ⇒ the variable's `lower_bound` (0 by default).
  OptReal initial_value {};

  /// Optional objective constant for a mean-shifted (rebased) variable.
  /// When the variable is α-rebased as ``value = value' + obj_constant``
  /// (the LP column holds ``value'``), the objective loses a constant
  /// term.  When set (and ``cost != 0``), the LP adds ``obj_constant``
  /// back AS GIVEN via ``LinearProblem::add_obj_constant`` — a physical $
  /// amount, NOT multiplied by ``cost`` nor by the scenario probability /
  /// discount factor (the caller passes the already-weighted value), once
  /// per created column; a ``global`` column over several phases adds it
  /// in the first phase only.  The FCF cost-to-go ``alpha_fcf`` sets it to
  /// the mean cut RHS.
  OptReal obj_constant {};

  /// Integrality flag.  When ``true`` every ``value`` column this variable
  /// creates is an INTEGER LP column (``SparseCol::is_integer``), created
  /// through ``SystemContext::add_integer_col`` so it is also registered in
  /// the integer-variable registry (cut audits, MIP-start, integer checks).
  /// The domain is ``Binary`` when the resolved bounds are exactly
  /// ``[0, 1]`` and ``Integer`` otherwise.  Applies to every scope: one
  /// ``Block``-scoped integer per block, one ``Stage``-scoped integer per
  /// (scenario, stage), one ``Phase``-scoped integer per (scene, phase)
  /// cell; a coarse ``state`` column is registered as BOTH a state and an
  /// integer variable (like a capacity-expansion ``expmod``).  For a
  /// ``block_state`` variable only the per-block ``value`` (volume) columns
  /// are integer — the cross-phase INCOMING ``value_in`` column, pinned by
  /// the SDDP forward pass, stays continuous.  Default unset ⇒ ``false``
  /// (a continuous column, the legacy behaviour).
  OptBool integer {};

  /// LP-relaxation flag for an ``integer`` variable (mirrors
  /// ``Commitment.relax``).  When ``true`` the columns stay continuous over
  /// their bounds (registered with domain ``Relaxed``).  A continuous phase
  /// (``Phase.continuous`` / ``model_options.continuous_phases`` / the
  /// ``--no-mip`` CLI shortcut) relaxes them too: the effective relax is
  /// ``relax || phase.is_continuous()``.  Ignored when ``integer`` is unset
  /// / ``false``.
  OptBool relax {};
};

}  // namespace gtopt
