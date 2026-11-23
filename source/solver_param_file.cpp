/**
 * @file      solver_param_file.cpp
 * @brief     Resolve relative `solver_options.param_file` paths against the
 *            case `input_directory`.
 * @date      2026-11-22
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>

#include <gtopt/solver_param_file.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

[[nodiscard]] bool is_existing_file(const std::filesystem::path& p) noexcept
{
  std::error_code ec;
  return std::filesystem::is_regular_file(p, ec) && !ec;
}

[[nodiscard]] std::filesystem::path to_absolute(
    const std::filesystem::path& p) noexcept
{
  std::error_code ec;
  auto abs = std::filesystem::absolute(p, ec);
  return ec ? p : abs.lexically_normal();
}

}  // namespace

std::optional<ResolvedParamFile> resolve_solver_param_file_source(
    std::string_view file, std::string_view input_directory)
{
  if (file.empty()) {
    return std::nullopt;
  }
  const std::filesystem::path p {file};
  if (p.is_absolute()) {
    return is_existing_file(p) ? std::optional {ResolvedParamFile {
                                     .path = p,
                                     .source = ParamFileSource::absolute,
                                 }}
                               : std::nullopt;
  }
  // The case directory wins: a relative param_file is case data, like every
  // other input file the case references.  The working directory is only a
  // fallback for a file that does not exist under input_directory.
  if (!input_directory.empty()) {
    const auto alt = std::filesystem::path {input_directory} / p;
    if (is_existing_file(alt)) {
      return ResolvedParamFile {
          .path = to_absolute(alt),
          .source = ParamFileSource::input_directory,
      };
    }
  }
  if (is_existing_file(p)) {
    return ResolvedParamFile {
        .path = to_absolute(p),
        .source = ParamFileSource::working_directory,
    };
  }
  return std::nullopt;
}

std::optional<std::filesystem::path> resolve_solver_param_file(
    std::string_view file, std::string_view input_directory)
{
  if (auto r = resolve_solver_param_file_source(file, input_directory)) {
    return std::move(r->path);
  }
  return std::nullopt;
}

bool resolve_solver_param_file(SolverOptions& opts,
                               std::string_view input_directory,
                               std::string_view where)
{
  if (!opts.param_file.has_value() || opts.param_file->empty()) {
    return true;
  }
  const std::string given = *opts.param_file;
  if (const auto resolved =
          resolve_solver_param_file_source(given, input_directory))
  {
    opts.param_file = resolved->path.string();
    switch (resolved->source) {
      case ParamFileSource::absolute:
        spdlog::info("{}.param_file: '{}'", where, given);
        break;
      case ParamFileSource::input_directory:
        spdlog::info(
            "{}.param_file '{}' resolved against input_directory '{}': '{}'",
            where,
            given,
            input_directory,
            *opts.param_file);
        break;
      case ParamFileSource::working_directory:
        spdlog::info(
            "{}.param_file '{}' not found under input_directory '{}'; using "
            "the working-directory copy '{}'",
            where,
            given,
            input_directory,
            *opts.param_file);
        break;
    }
    return true;
  }
  spdlog::warn(
      "{}.param_file '{}' NOT FOUND (tried it relative to input_directory "
      "'{}' and to the working directory '{}') — the solver will run "
      "with its DEFAULT parameters, not the case's tuning",
      where,
      given,
      input_directory,
      std::filesystem::current_path().string());
  return false;
}

int resolve_solver_param_files(PlanningOptions& options)
{
  const std::string input_dir =
      options.input_directory.value_or(std::string {"input"});
  int missing = 0;
  const auto resolve = [&](SolverOptions& so, std::string_view where)
  {
    if (!resolve_solver_param_file(so, input_dir, where)) {
      ++missing;
    }
  };
  const auto resolve_opt =
      [&](std::optional<SolverOptions>& so, std::string_view where)
  {
    if (so.has_value()) {
      resolve(*so, where);
    }
  };

  resolve(options.solver_options, "solver_options");
  auto& mono = options.monolithic_options;
  resolve_opt(mono.solver_options, "monolithic_options.solver_options");
  if (mono.mip_start.has_value()) {
    resolve_opt(mono.mip_start->relax.solver_options,
                "monolithic_options.mip_start.relax.solver_options");
  }
  resolve_opt(options.sddp_options.forward_solver_options,
              "sddp_options.forward_solver_options");
  resolve_opt(options.sddp_options.backward_solver_options,
              "sddp_options.backward_solver_options");
  resolve_opt(options.cascade_options.sddp_options.forward_solver_options,
              "cascade_options.sddp_options.forward_solver_options");
  resolve_opt(options.cascade_options.sddp_options.backward_solver_options,
              "cascade_options.sddp_options.backward_solver_options");
  return missing;
}

}  // namespace gtopt
