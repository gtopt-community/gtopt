/**
 * @file      mip_start_dive.hpp
 * @brief     Relaxation-guided propagation dive + verify/repair MIP start
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The default MIP-start generator.  It turns the solved LP relaxation into a
 * start that is FEASIBLE for the MIP (not merely "near-feasible"), in three
 * generic, solver-agnostic stages:
 *
 *   1. **Dive** (`propagation_dive`): fix the integer columns one at a time
 *      in relaxation-confidence order (integral cells first, then fractional
 *      cells by decreasing distance from 1/2) to their rounded relaxation
 *      value, running activity-based bound propagation over EVERY row of the
 *      flat LP after each fixing.  A fixing that propagates to a conflict is
 *      undone and the opposite value tried; a cell where both values conflict
 *      is deferred.  Pure-integer structure — configuration exclusivity
 *      (Σ u ≤ 1), plant-config ⇒ unit rows, min up/down windows, u/v/w
 *      logic, fixed status, `u ≤ 0` operational rows — is thus satisfied by
 *      construction, with no element names or PAMPL knowledge.  Mixed rows
 *      (reserve gated by u, Pmin·u ≤ p) propagate through the continuous
 *      bounds.
 *   2. **Verify** (`solve_fixed_integer_lp`): fix every integer column to the
 *      candidate and solve the continuous LP.  Optimal ⇒ the start is a
 *      feasible MIP solution and its objective is the start's TRUE cost.
 *   3. **Repair** when the fixed LP is infeasible: an *elastic* LP (integer
 *      columns relaxed back to their bounds, objective + M·|x − candidate|)
 *      names the integer cells that must move; they are re-targeted and
 *      re-dived first, for a bounded number of rounds.  If that still fails,
 *      a bounded sub-MIP frees only those cells and the integer columns of
 *      the still-violated pure-integer rows (+ their short-row neighbours)
 *      and keeps the rest fixed.
 *
 * Every LP/MIP solve goes through `LinearInterface` (bounds, objective,
 * `resolve`), so the stage works on every backend; the propagation needs the
 * retained flat LP (`LinearInterface::flat_lp_snapshot`) and degrades to the
 * legacy round + domain rules when it is absent.
 */

#pragma once

#include <cstddef>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace gtopt
{

class LinearInterface;
struct FlatLinearProblem;
struct MipStartContext;
struct SolverOptions;

/// Input of `propagation_dive`.  All dense spans are in RAW LP space.
struct DiveInput
{
  const FlatLinearProblem* flat {nullptr};  ///< CSC matrix (required)
  std::span<const double> col_low {};  ///< live column lower bounds
  std::span<const double> col_upp {};  ///< live column upper bounds
  std::span<const double> row_low {};  ///< live row lower bounds
  std::span<const double> row_upp {};  ///< live row upper bounds
  std::span<const int> int_cols {};  ///< integer column indices
  /// Dense target vector (size ncols): integer entries are the values the
  /// dive tries first; continuous entries are copied to the output.
  std::span<const double> target {};
  /// Dense priority (size ncols): integer columns are fixed in DECREASING
  /// priority order (ties by index).
  std::span<const double> priority {};
  /// Solver infinity: |bound| >= this is treated as unbounded.
  double infinity {1e20};
  /// Rows longer than this are not propagated (aggregate rows — propagating
  /// them per fixing is quadratic and they rarely carry combinatorial
  /// structure).  The fixed-LP verify still checks them.
  std::size_t max_row_length {2000};
  /// Propagation work budget in matrix-entry visits; 0 ⇒ 200 × nnz.
  std::size_t work_limit {0};
};

/// Outcome of `propagation_dive`.
struct DiveOutput
{
  /// Dense candidate (size ncols): integer columns fixed, continuous copied
  /// from `DiveInput::target`.
  std::vector<double> values {};
  int fixed {0};  ///< cells fixed to their target by the dive
  int flipped {0};  ///< cells fixed to the opposite value (target conflicted)
  int forced {0};  ///< cells fixed by propagation of other fixings
  int deferred {0};  ///< cells where both values conflicted (set blindly)
  int ignored_rows {0};  ///< rows dropped (root conflict / too long)
  bool budget_exhausted {false};  ///< propagation stopped on the work limit
  std::size_t work {0};  ///< matrix-entry visits spent
};

/// Relaxation-guided fix-and-propagate dive (stage 1).  See the file header.
[[nodiscard]] DiveOutput propagation_dive(const DiveInput& in);

/// Rows of `flat` whose nonzeros are ALL integer columns ("pure-integer"
/// rows: exclusivity, min up/down, logic, …) violated by `values` beyond
/// `tol · max(1, |rhs|)`.  A cheap exact check of the combinatorial part of
/// a candidate — the rows the legacy rounded start broke on PCP.
[[nodiscard]] std::vector<int> violated_integer_rows(
    const FlatLinearProblem& flat,
    std::span<const double> row_low,
    std::span<const double> row_upp,
    std::span<const int> int_cols,
    std::span<const double> values,
    double infinity = 1e20,
    double tol = 1e-6);

/// Group `rows` by their label family (`class:constraint`), for logging.
/// Rows without a label land in `"unlabeled"`.
[[nodiscard]] std::map<std::string, int> row_families(
    const LinearInterface& li, std::span<const int> rows);

/// Result of a fixed-integer LP solve.
struct FixedLpResult
{
  bool feasible {false};  ///< the fixed LP solved to optimality
  std::optional<double> obj {};  ///< physical objective when feasible
  std::vector<double> solution {};  ///< complete raw primal when feasible
};

/// Fix every `int_cols` column of `li` to `round(candidate[j])` (clamped to
/// its bounds), solve the continuous LP with `opts`, and restore the original
/// bounds.  Precondition: integrality is RELAXED on `li`.
[[nodiscard]] FixedLpResult solve_fixed_integer_lp(
    LinearInterface& li,
    std::span<const int> int_cols,
    std::span<const double> candidate,
    const SolverOptions& opts);

/// Elastic LP: keep integrality relaxed, add `penalty · |x_j − candidate_j|`
/// for every integer column at a bound in the candidate (as a linear
/// objective term toward that bound) to the original objective, solve, and
/// restore the objective.  `penalty <= 0` solves a PURE feasibility LP
/// instead (original costs zeroed, unit deviation weights) — the default:
/// better conditioned, and primal feasible from the relaxation basis.
/// @return the raw primal of the elastic LP, or empty on failure.
[[nodiscard]] std::vector<double> solve_elastic_lp(
    LinearInterface& li,
    std::span<const int> int_cols,
    std::span<const double> candidate,
    double penalty,
    const SolverOptions& opts);

/// Summary of a dive run, for the pipeline report and logs.
struct DiveSummary
{
  bool verified {false};  ///< the returned start's fixed LP is feasible
  std::optional<double> start_obj {};  ///< its physical objective
  int repair_rounds {0};  ///< elastic repair rounds run
  bool sub_mip_used {false};  ///< the bounded sub-MIP produced the start
  int violated_int_rows_rounded {-1};  ///< plain rounding (−1 = unchecked)
  int violated_int_rows_dive {-1};  ///< after the dive (−1 = unchecked)
  bool propagated {false};  ///< the dive ran with propagation
};

/// Run the full dive → verify → repair stage on a solved, integrality-relaxed
/// LP relaxation.  @return the dense raw start (verified feasible when
/// `summary.verified`), or `std::nullopt` when the relaxation has no primal.
[[nodiscard]] std::optional<std::vector<double>> dive_mip_start(
    MipStartContext& ctx, DiveSummary& summary);

}  // namespace gtopt
