/**
 * @file      gtopt_lp_runner.hpp
 * @brief     LP build, solve, stats, and output writing
 * @date      2026-04-08
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Separated from gtopt_main.cpp so that the heavy Arrow/Parquet headers
 * (pulled in via planning_lp.hpp) compile in their own translation unit.
 */

#pragma once

#include <expected>
#include <string>

#include <gtopt/planning.hpp>

namespace gtopt
{

struct MainOptions;

/// Resolve the solver the run will use and apply its configuration to
/// @p planning: pin the effective solver name (`--solver` > planning pin >
/// GTOPT_SOLVER / plugin priority) on
/// `planning.options.lp_matrix_options.solver_name`, overlay the
/// `.gtopt.conf [solver.<name>]` section under `solver_options`, and
/// auto-discover `<input_directory>/solvers/<name>.prm` into
/// `solver_options.param_file` when unset.  Idempotent; gtopt_main runs it
/// before writing the pre-solve snapshot (gtopt_state.json) so the
/// snapshot records the solver and the resolved parameter file.
void resolve_solver_setup(Planning& planning, const MainOptions& opts);

/// Build the LP model, solve it, and write output.
///
/// Wraps the full LP pipeline: matrix construction, optional LP-file dump,
/// coefficient stats, solver invocation, post-solve stats, and output
/// writing.  Returns 0 on success, 1 when the solver finds no optimal
/// solution, or an error string on hard failures.
[[nodiscard]] std::expected<int, std::string> build_solve_and_output(
    Planning&& planning, const MainOptions& opts);

}  // namespace gtopt
