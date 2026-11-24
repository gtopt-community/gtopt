/**
 * @file      element_column_resolver.hpp
 * @brief     Resolve LP column indices for user-constraint element references
 * @date      Mon Mar 24 00:00:00 2026
 * @author    Marcelo Matus
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Provides functions that map symbolic element references (from parsed
 * user-constraint expressions) to concrete LP column indices.  These
 * are used by `UserConstraintLP` to build constraint rows.
 */

#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include <gtopt/linear_problem.hpp>
#include <gtopt/sparse_row.hpp>

namespace gtopt
{

class SystemContext;
class ScenarioLP;
class StageLP;
class BlockLP;
struct ElementRef;
struct SumElementRef;
struct AmplVariable;

/// Memoised resolution of an element reference's *constant* parts — the
/// element-id parse, the name→uid lookup, and the AMPL-variable find —
/// keyed by the `ElementRef`'s address.  All three are constant per
/// (class, id, attribute) within a single `UserConstraintLP::add_to_lp`
/// (fixed scenario, stage), so the resolver computes them once and reuses
/// them across the block loop — mirroring how each element's `add_to_lp`
/// resolves its constant data before iterating blocks.  Only the stable
/// top-level term refs are cached (their address lives for the whole
/// constraint build); transient compound-leg refs bypass the cache.
struct ElementRefResolution
{
  std::optional<Uid> uid {};
  bool element_id_was_name {false};
  const AmplVariable* var {nullptr};  ///< direct registration, or nullptr
};
using AmplResolveCache =
    std::unordered_map<const ElementRef*, ElementRefResolution>;

/// A resolved LP column together with its physical-to-LP scale factor
/// and an optional additive offset.
///
/// The @c scale and @c offset fields satisfy:
///   physical_value = LP_value * scale + offset
///
/// When assembling a user constraint  `coeff * physical_var [op] RHS`  the
/// LP-level coefficient is  `coeff * scale`  so that the constraint remains
/// dimensionally correct regardless of internal LP scaling choices (e.g.
/// reservoir volume in Gm3, theta in milli-radians, ...).
///
/// The @c offset captures a per-(scenario, stage, block) additive shift
/// folded into the LP column representation.  Example: demand's Option C
/// substitution makes the LP column represent `neg_fail = load - lmax`;
/// the offset is `+lmax(s,t,b)` so the resolver can still emit a row that
/// references `demand.load` physically — the offset contribution is
/// shifted onto the row's RHS via `BuildResult::param_shift` (analogous to
/// how `resolve_single_param` already accumulates constants).
struct ResolvedCol
{
  ColIndex col;
  double scale {1.0};
  double offset {0.0};
};

/**
 * @brief Try to look up the LP `ColIndex` for one element reference.
 *
 * Returns `std::nullopt` when the element is not found, the block is not
 * active in the requested (scenario, stage), or the attribute is unknown.
 *
 * The returned @c ResolvedCol::scale converts the LP variable to physical
 * units so that the caller can build correctly-scaled constraint rows.
 *
 * When a column has a non-unit scale stored in SparseCol::scale (set at
 * variable creation time), the scale is retrieved via
 * `lp.get_col_scale(col)` -- providing a uniform mechanism that works for
 * all current and future scaled variables without hardcoding per-element
 * logic.
 */
[[nodiscard]] std::optional<ResolvedCol> resolve_single_col(
    const SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    const BlockLP& block,
    const ElementRef& ref,
    const LinearProblem& lp);

/// Result of `resolve_col_to_row`.
///
/// `emitted` is true when at least one column was added to the row.
/// `offset_shift` accumulates `Σ (leg_coef × leg_offset)` for resolved
/// columns whose AMPL registration carries a per-block additive offset
/// (e.g., demand's Option C `neg_fail = load − lmax` substitution).  The
/// caller folds `offset_shift` into the row's RHS via `BuildResult::
/// param_shift` so user constraints like `demand('d').load <= X` remain
/// physically correct even though the LP column actually stores
/// `neg_fail`.
///
/// `element_known` is true when the (element_type, element_id) pair was
/// found in the AMPL element registry, regardless of whether a column
/// was emitted for this (scenario, stage, block).  This distinguishes
/// the two `emitted = false` cases:
///   * `element_known = true`  — the element exists in the registry
///                                 but has no LP column for this
///                                 specific (scenario, stage, block)
///                                 (e.g. a generator with pmax=0 on
///                                 that block — GeneratorLP omits the
///                                 column).  Caller should treat the
///                                 term's contribution as 0 silently;
///                                 the constraint stays well-formed.
///   * `element_known = false` — the element name or UID is unknown
///                                 to the registry (typo, deleted
///                                 element, suppressed-by-mode, …).
///                                 Strict-mode caller should raise an
///                                 error; lenient-mode caller should
///                                 warn-and-skip.
///
/// Pre-existing callers can ignore `element_known`; only the strict
/// vs lenient branch in `user_constraint_lp.cpp` uses it.
struct ResolveColResult
{
  bool emitted {false};
  double offset_shift {0.0};
  bool element_known {false};
  /// Set (non-null) when nothing was emitted AND the element is
  /// scope-restricted (`SimulationLP::register_ampl_scoped_element` — a
  /// coarse-scope or single-block `DecisionVariable`): its column exists,
  /// but not at this (scenario, stage, block).  Points at the registry's
  /// scope description.  This is NEVER the implicit-zero leniency — the
  /// caller errors (strict) or warns (lax) instead of dropping the term.
  const std::string* unreachable_scope {nullptr};
};

/// One element of a `sum(...)` that contributed no column at the row's
/// (scenario, stage, block).  `scope` is set when the element is
/// scope-restricted (see `ResolveColResult::unreachable_scope`);
/// `element_known` mirrors `ResolveColResult::element_known` (false: the
/// id resolves to no registered element / attribute).
struct UnreachableSumElement
{
  std::string element_id;
  const std::string* scope {nullptr};
  bool element_known {false};
};

/// Why a KNOWN element reference resolved to no LP column at one
/// (scenario, stage, block).  Every kind but `scope_mismatch` is an
/// intentional absence: the term is an implicit 0.
enum class DroppedTermKind : std::uint8_t
{
  suppressed,  ///< class/attribute disabled by the planning mode
  inactive,  ///< element inactive at this stage
  prev_first_block,  ///< `prev(...)` / `_prev` at the stage's first block
  block_omitted,  ///< column registered at this stage, not this block
  zero_capacity,  ///< pmax / lmax / tmax = 0 at this block
  not_materialised,  ///< attribute never built for this element so far
  /// The element is ACTIVE here and its column for this attribute was
  /// registered at an earlier stage of this LP, but not at this one: a
  /// registration / scope mismatch, never an implicit 0.
  scope_mismatch,
};

/// `is_active(stage)` of the element @p ref names; nullopt when the
/// reference does not resolve or the class has no activity probe.
[[nodiscard]] std::optional<bool> element_ref_active(const SystemContext& sc,
                                                     const ScenarioLP& scenario,
                                                     const StageLP& stage,
                                                     const ElementRef& ref);

/// Short label of a `DroppedTermKind` (log summaries).
[[nodiscard]] std::string_view dropped_term_kind_name(
    DroppedTermKind kind) noexcept;

/**
 * @brief Classify a known element reference that produced no column.
 *
 * Called only on the silent-zero path (`!emitted && element_known`, or a
 * planning-mode suppression).  Uses the element's own `is_active(stage)`
 * (via `SimulationLP::find_ampl_active`), the AMPL registry of this
 * (scenario, stage) and of the earlier stages of the same LP, and the
 * capacity parameters `pmax` / `lmax` / `tmax_ab`+`tmax_ba`.
 */
[[nodiscard]] DroppedTermKind classify_dropped_term(const SystemContext& sc,
                                                    const ScenarioLP& scenario,
                                                    const StageLP& stage,
                                                    const BlockLP& block,
                                                    const ElementRef& ref,
                                                    bool suppressed);

/**
 * @brief Resolve an `ElementRef` into one or more `SparseRow` entries.
 *
 * Unlike `resolve_single_col`, this helper is compound-aware: when the
 * referenced attribute is a registered PAMPL compound (e.g. `line.flow`
 * → `flowp − flown`), each leg is looked up and added to @p row with
 * the appropriate scaled coefficient.  Ordinary single-column
 * attributes resolve to exactly one entry.
 *
 * @param sc         System context holding the AMPL element registry.
 * @param scenario   Scenario of the (scenario, stage, block) cell.
 * @param stage      Stage of the cell.
 * @param block      Block of the cell.
 * @param ref        Element reference (type, id, attribute) to resolve.
 * @param base_coeff The outer coefficient of the constraint term; each
 *                   emitted entry contributes `base_coeff * leg_coeff`.
 * @param row        Row that receives the resolved column entries.
 * @param lp         Linear problem the resolved columns belong to.
 * @param resolve_cache Optional per-build lookup cache (may be null).
 * @return `{emitted, offset_shift, element_known}` — see
 *         `ResolveColResult`.
 */
[[nodiscard]] ResolveColResult resolve_col_to_row(
    const SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    const BlockLP& block,
    const ElementRef& ref,
    double base_coeff,
    SparseRow& row,
    const LinearProblem& lp,
    AmplResolveCache* resolve_cache = nullptr);

/**
 * @brief Try to look up a data parameter value for one element reference.
 *
 * Returns `std::nullopt` when the element is not found or the attribute is
 * not a known parameter.  Parameters are fixed data values (not LP columns)
 * such as `pmax`, `gcost`, `fmax`, `emin`, etc.
 *
 * For schedule-valued parameters the value is resolved for the given
 * (scenario, stage, block) context.  If the schedule has no value for the
 * given context, `std::nullopt` is returned.
 */
[[nodiscard]] std::optional<double> resolve_single_param(
    const SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    const BlockLP& block,
    const ElementRef& ref);

/**
 * @brief Collect (coefficient, ColIndex) pairs for a `SumElementRef`.
 *
 * When `sum_ref.all_elements` is true, iterates over every element in the
 * collection of the matching type.  Otherwise iterates over the explicit ID
 * list.  The base_coeff is multiplied into each term's coefficient.
 *
 * Returns the total `offset_shift = Σ_e (coef × offset_e)` across all
 * resolved elements that carry a non-zero AMPL offset.  The caller
 * folds this onto the row's RHS via the existing `param_shift`
 * mechanism so shifted-variable encodings (Option C demand) compose
 * correctly under `sum(demand(all).load)` predicates.
 */
///
/// When @p unreachable is non-null, every summed element that contributed no
/// column at this block is appended to it (instead of vanishing silently),
/// so the caller can classify it: error / warn on a scope mismatch or an
/// unknown explicit id, count it as an implicit 0 otherwise.
[[nodiscard]] double collect_sum_cols(
    const SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    const BlockLP& block,
    const SumElementRef& sum_ref,
    double base_coeff,
    SparseRow& row,
    const LinearProblem& lp,
    std::vector<UnreachableSumElement>* unreachable = nullptr);

}  // namespace gtopt
