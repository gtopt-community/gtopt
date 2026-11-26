/**
 * @file      json_solver_options.hpp
 * @brief     JSON serialization for LP solver options
 * @date      Fri May  3 12:30:00 2025
 * @author    Marcelo Matus
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module provides JSON serialization/deserialization for the SolverOptions
 * structure, allowing LP solver options to be saved and loaded in JSON format.
 *
 * All fields are nullable in JSON (missing ≡ use C++ default).  A custom
 * constructor_t converts the std::optional parse results back to non-optional
 * SolverOptions members via value_or().
 */

#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <variant>

#include <daw/json/daw_json_link.h>
#include <gtopt/enum_option.hpp>
#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/solver_options.hpp>

namespace daw::json
{
using gtopt::BarrierSolver;
using gtopt::CrossoverMode;
using gtopt::KappaMode;
using gtopt::LPAlgo;
using gtopt::OptName;
using gtopt::OptReal;
using gtopt::SolverLogMode;
using gtopt::SolverOptions;
using gtopt::SolverScaling;

using OptSolverLogMode = std::optional<SolverLogMode>;
using OptSolverScaling = std::optional<SolverScaling>;
using OptInt = std::optional<int>;
using OptBool = std::optional<bool>;

/// Enum-valued solver option given either by name (`"detailed"`) or by its
/// legacy numeric code (`1`).  daw::json picks the alternative matching the
/// JSON token type (number → Int, string → Name).
using IntOrName = std::variant<gtopt::Int, gtopt::Name>;
using OptIntOrName = std::optional<IntOrName>;
using jvtl_IntOrName = json_variant_type_list<json_number_no_name<gtopt::Int>,
                                              json_string_no_name<gtopt::Name>>;

/// Resolve an @ref IntOrName to enum @p E: a name via `require_enum`, a
/// number must be the code of a listed enumerator.  Throws
/// `std::invalid_argument` naming the option and the valid values.
template<typename E>
[[nodiscard]] std::optional<E> enum_from_int_or_name(std::string_view option,
                                                     const OptIntOrName& v)
{
  if (!v) {
    return std::nullopt;
  }
  if (const auto* name = std::get_if<gtopt::Name>(&*v)) {
    return gtopt::require_enum<E>(option, *name);
  }
  const auto code = std::get<gtopt::Int>(*v);
  for (const auto& e : gtopt::enum_entries(E {})) {
    if (std::cmp_equal(static_cast<int>(e.value), code)) {
      return e.value;
    }
  }
  // Out of range: report it with the valid names.
  return gtopt::require_enum<E>(option, std::to_string(code));
}

/// Serialise an enum option as its numeric code (the historical JSON form,
/// still read by external tools).
template<typename E>
[[nodiscard]] OptIntOrName int_or_name_from_enum(const std::optional<E>& v)
{
  if (!v) {
    return std::nullopt;
  }
  return IntOrName {static_cast<gtopt::Int>(*v)};
}

/// Constructs SolverOptions from nullable JSON fields, applying defaults.
struct SolverOptionsConstructor
{
  SolverOptions operator()(const OptName& algorithm_str,
                           OptInt threads,
                           OptBool presolve,
                           OptReal optimal_eps,
                           OptReal feasible_eps,
                           OptReal barrier_eps,
                           OptReal mip_gap,
                           OptReal mip_gap_abs,
                           OptInt log_level,
                           const OptIntOrName& log_mode_raw,
                           OptReal time_limit,
                           const OptIntOrName& scaling_raw,
                           const OptName& crossover_str,
                           OptInt max_fallbacks,
                           OptBool memory_emphasis,
                           const OptName& param_file,
                           OptBool advanced_basis,
                           OptBool mip_polish,
                           const OptName& barrier_solver_str,
                           const OptName& mip_lp_algorithm_str,
                           OptReal mip_heuristic_effort,
                           const OptName& kappa_mode_str,
                           OptBool cold_solve_without_basis,
                           OptBool deterministic_parallel) const
  {
    LPAlgo algorithm = LPAlgo::barrier;
    if (algorithm_str.has_value()) {
      algorithm = gtopt::require_enum<LPAlgo>("algorithm", *algorithm_str);
    }
    const auto log_mode =
        enum_from_int_or_name<SolverLogMode>("log_mode", log_mode_raw);
    const auto scaling =
        enum_from_int_or_name<SolverScaling>("scaling", scaling_raw);
    CrossoverMode crossover = CrossoverMode::automatic;
    if (crossover_str.has_value()) {
      crossover =
          gtopt::require_enum<CrossoverMode>("crossover", *crossover_str);
    }
    std::optional<BarrierSolver> barrier_solver;
    if (barrier_solver_str.has_value()) {
      barrier_solver = gtopt::require_enum<BarrierSolver>("barrier_solver",
                                                          *barrier_solver_str);
    }
    std::optional<LPAlgo> mip_lp_algorithm;
    if (mip_lp_algorithm_str.has_value()) {
      mip_lp_algorithm = gtopt::require_enum<LPAlgo>("mip_lp_algorithm",
                                                     *mip_lp_algorithm_str);
    }
    std::optional<KappaMode> kappa_mode;
    if (kappa_mode_str.has_value()) {
      kappa_mode =
          gtopt::require_enum<KappaMode>("kappa_mode", *kappa_mode_str);
    }
    return SolverOptions {
        .algorithm = algorithm,
        .threads = threads.value_or(2),
        .presolve = presolve.value_or(true),
        .optimal_eps = optimal_eps,
        .feasible_eps = feasible_eps,
        .barrier_eps = barrier_eps,
        .mip_gap = mip_gap,
        .mip_gap_abs = mip_gap_abs,
        .log_level = log_level.value_or(0),
        .log_mode = log_mode,
        .time_limit = time_limit,
        .scaling = scaling.has_value()
            ? scaling
            : OptSolverScaling {SolverScaling::automatic},
        .crossover = crossover,
        .advanced_basis = advanced_basis.value_or(false),
        .param_file = param_file,
        .max_fallbacks = max_fallbacks.value_or(2),
        .memory_emphasis = memory_emphasis,
        .mip_polish = mip_polish,
        .barrier_solver = barrier_solver,
        .mip_lp_algorithm = mip_lp_algorithm,
        .mip_heuristic_effort = mip_heuristic_effort,
        .kappa_mode = kappa_mode,
        .cold_solve_without_basis = cold_solve_without_basis,
        .deterministic_parallel = deterministic_parallel,
    };
  }
};

template<>
struct json_data_contract<SolverOptions>
{
  using constructor_t = SolverOptionsConstructor;

  using type = json_member_list<
      json_string_null<"algorithm", OptName>,
      json_number_null<"threads", OptInt>,
      json_bool_null<"presolve", OptBool>,
      json_number_null<"optimal_eps", OptReal>,
      json_number_null<"feasible_eps", OptReal>,
      json_number_null<"barrier_eps", OptReal>,
      json_number_null<"mip_gap", OptReal>,
      json_number_null<"mip_gap_abs", OptReal>,
      json_number_null<"log_level", OptInt>,
      json_variant_null<"log_mode", OptIntOrName, jvtl_IntOrName>,
      json_number_null<"time_limit", OptReal>,
      json_variant_null<"scaling", OptIntOrName, jvtl_IntOrName>,
      json_string_null<"crossover", OptName>,
      json_number_null<"max_fallbacks", OptInt>,
      json_bool_null<"memory_emphasis", OptBool>,
      json_string_null<"param_file", OptName>,
      json_bool_null<"advanced_basis", OptBool>,
      json_bool_null<"mip_polish", OptBool>,
      json_string_null<"barrier_solver", OptName>,
      json_string_null<"mip_lp_algorithm", OptName>,
      json_number_null<"mip_heuristic_effort", OptReal>,
      json_string_null<"kappa_mode", OptName>,
      json_bool_null<"cold_solve_without_basis", OptBool>,
      json_bool_null<"deterministic_parallel", OptBool>>;

  static auto to_json_data(SolverOptions const& opt)
  {
    return std::make_tuple(
        OptName {std::string {gtopt::enum_name(opt.algorithm)}},
        OptInt {opt.threads},
        OptBool {opt.presolve},
        opt.optimal_eps,
        opt.feasible_eps,
        opt.barrier_eps,
        opt.mip_gap,
        opt.mip_gap_abs,
        OptInt {opt.log_level},
        int_or_name_from_enum(opt.log_mode),
        opt.time_limit,
        int_or_name_from_enum(opt.scaling),
        OptName {gtopt::enum_name(opt.crossover)},
        OptInt {opt.max_fallbacks},
        opt.memory_emphasis,
        opt.param_file,
        OptBool {opt.advanced_basis},
        opt.mip_polish,
        opt.barrier_solver
            ? OptName {std::string {gtopt::enum_name(*opt.barrier_solver)}}
            : OptName {},
        opt.mip_lp_algorithm
            ? OptName {std::string {gtopt::enum_name(*opt.mip_lp_algorithm)}}
            : OptName {},
        opt.mip_heuristic_effort,
        opt.kappa_mode
            ? OptName {std::string {gtopt::enum_name(*opt.kappa_mode)}}
            : OptName {},
        opt.cold_solve_without_basis,
        opt.deterministic_parallel);
  }
};

}  // namespace daw::json
