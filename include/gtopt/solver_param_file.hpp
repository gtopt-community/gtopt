/**
 * @file      solver_param_file.hpp
 * @brief     Resolve relative `solver_options.param_file` paths against the
 *            case `input_directory`.
 * @date      2026-11-22
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Cases emitted by plexos2gtopt / plp2gtopt pin
 * `solver_options.param_file = "solvers/cplex.prm"` — a path relative to the
 * case directory, like every other input file the case references.  The
 * solver plugins hand that string verbatim to the backend
 * (`CPXreadcopyparam`, …) and derive sibling files from its directory
 * (`cplex_warmstart.prm`, `cuopt.cfg`, `highs_warmstart.opts`, …), so a run
 * started from any directory other than the case directory silently fell
 * back to solver defaults.  The helpers here turn the relative path into the
 * path that actually exists before any plugin sees it: `input_directory`
 * first (the case directory wins), the working directory only as a
 * fallback when the case has no such file.  The choice is logged.
 */

#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

#include <gtopt/planning_options.hpp>
#include <gtopt/solver_options.hpp>

namespace gtopt
{

/// Where a resolved solver parameter file was found.
enum class ParamFileSource : std::uint8_t
{
  absolute,  ///< the given path was already absolute
  input_directory,  ///< `<input_directory>/<file>` (the case directory)
  working_directory,  ///< `<cwd>/<file>` (fallback only)
};

/// A resolved solver parameter file and where it was found.
struct ResolvedParamFile
{
  std::filesystem::path path {};  ///< absolute, lexically normalised
  ParamFileSource source {ParamFileSource::absolute};
};

/// Resolve a (possibly relative) solver parameter file, reporting where it
/// was found.
///
/// Absolute paths are returned unchanged.  A relative path is tried first as
/// `input_directory / file` (the full relative path, so `solvers/cplex.prm`
/// maps to `<input_directory>/solvers/cplex.prm`): the case directory wins.
/// Only when that file does not exist is the path tried relative to the
/// current working directory.  The first candidate that exists is returned
/// in absolute form; `std::nullopt` when neither exists.
[[nodiscard]] std::optional<ResolvedParamFile> resolve_solver_param_file_source(
    std::string_view file, std::string_view input_directory);

/// Path-only form of resolve_solver_param_file_source().
[[nodiscard]] std::optional<std::filesystem::path> resolve_solver_param_file(
    std::string_view file, std::string_view input_directory);

/// Resolve `param_file` in place on one `SolverOptions` bundle.
///
/// A resolved path replaces the stored string (absolute) and is logged at
/// INFO with @p where as context, naming whether the input_directory copy or
/// the working-directory fallback was used; a file that cannot be found is kept
/// as given and reported at WARN (the plugin will then fail to open it and the
/// solver runs with its defaults).  Returns `false` only in that not-found
/// case; an unset / empty `param_file` is a successful no-op.
bool resolve_solver_param_file(SolverOptions& opts,
                               std::string_view input_directory,
                               std::string_view where);

/// Resolve `param_file` on every `SolverOptions` bundle carried by
/// @p options: the global `solver_options`, the monolithic and MIP-start
/// relaxation overrides, and the SDDP forward / backward overrides (both the
/// top-level `sddp_options` and the cascade's global `sddp_options`).
/// Returns the number of param files that could not be found.
int resolve_solver_param_files(PlanningOptions& options);

}  // namespace gtopt
