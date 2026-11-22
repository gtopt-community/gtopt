/**
 * @file      solution_csv.hpp
 * @brief     Row model + formatting for the solver-diagnostics `solution.csv`.
 * @date      2026-07-12
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `PlanningLP::write_out()` emits one `solution.csv` per run summarising, for
 * every solved `(scene, phase)` cell, the terminal status and the accumulated
 * solver-effort counters.  The column layout is defined once here so the
 * header string and the per-row format string cannot drift apart (a mismatch
 * would silently shift every column and break the downstream `gtopt_compare`
 * reader).  Keeping the model + formatters header-visible also lets them be
 * unit-tested without standing up a full `PlanningLP`.
 */

#pragma once

#include <cstddef>
#include <format>
#include <string>
#include <string_view>

#include <gtopt/phase.hpp>
#include <gtopt/scene.hpp>

namespace gtopt
{

/// One `solution.csv` row: per-`(scene, phase)` status plus the cumulative
/// solver-effort counters harvested from that cell's `SolverStats`.
struct SolutionRow
{
  SceneUid scene_uid;
  PhaseUid phase_uid;
  int status;  ///< CLP status code, or -1 for a never-solved cell
  /// Cell objective in PHYSICAL cost units (`LinearInterface::
  /// get_obj_value()`: the LP-raw objective × `scale_objective`, constants
  /// included).  Same convention for monolithic, SDDP and cascade — all
  /// three go through `PlanningLP::write_out()`.
  double obj_value;
  double kappa;  ///< Cell condition number, or -1 when unavailable
  double max_kappa;  ///< Run-wide max kappa (SDDP summary; -1 for monolithic)
  double gap;  ///< Final SDDP gap (0.0 for monolithic)
  double gap_change;  ///< Final SDDP stationary gap-change (1.0 for monolithic)
  double solve_ticks;  ///< Cumulative deterministic solver ticks on this cell
  double solve_time_s;  ///< Cumulative solve wall seconds on this cell
  std::size_t solve_calls;  ///< initial_solve + resolve count on this cell
  std::size_t
      infeasible_count;  ///< Non-optimal solves (feasibility-cut hotspots)
  /// Objective scale factor of the cell LP: `obj_value / scale_objective`
  /// is the LP-raw objective the solver reported.  Emitted so consumers
  /// (e.g. the golden comparator `tools/gtopt_compare_csv.py`) can convert
  /// between raw and physical units without re-reading the case options.
  double scale_objective;
  /// The gtopt build that wrote the file (`build_info()`): the version,
  /// e.g. "1.1.0" or "1.2.0-dev", and the `git describe` revision of its
  /// source tree ("unknown" outside a git checkout).  Same on every row.
  std::string_view gtopt_version {};
  std::string_view gtopt_revision {};
};

/// CLP status-code → human name.  Values follow the CLP convention
/// (0=optimal, 1=primal infeasible, 2=dual infeasible/unbounded,
/// 3=iteration limit, 4=error); anything else (incl. the -1 never-solved
/// sentinel) reports "unknown".
[[nodiscard]] constexpr std::string_view solution_status_name(
    int status) noexcept
{
  switch (status) {
    case 0:
      return "optimal";
    case 1:
      return "infeasible";
    case 2:
      return "unbounded";
    case 3:
      return "iteration_limit";
    case 4:
      return "error";
    default:
      return "unknown";
  }
}

/// The single source of truth for the CSV column order/names (trailing
/// newline included).  Its comma count MUST equal the field count emitted by
/// `solution_csv_line`; `test_solution_csv` pins that invariant.
[[nodiscard]] constexpr std::string_view solution_csv_header() noexcept
{
  return "scene,phase,status,status_name,obj_value,kappa,max_kappa,"
         "gap,gap_change,solve_ticks,solve_time_s,solve_calls,"
         "infeasible_count,scale_objective,gtopt_version,gtopt_revision\n";
}

/// Format one @p row as a CSV line (trailing newline included), matching the
/// column order of `solution_csv_header`.
[[nodiscard]] inline std::string solution_csv_line(const SolutionRow& row)
{
  return std::format("{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n",
                     row.scene_uid,
                     row.phase_uid,
                     row.status,
                     solution_status_name(row.status),
                     row.obj_value,
                     row.kappa,
                     row.max_kappa,
                     row.gap,
                     row.gap_change,
                     row.solve_ticks,
                     row.solve_time_s,
                     row.solve_calls,
                     row.infeasible_count,
                     row.scale_objective,
                     row.gtopt_version,
                     row.gtopt_revision);
}

}  // namespace gtopt
