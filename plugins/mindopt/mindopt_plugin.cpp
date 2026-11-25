/**
 * @file      mindopt_plugin.cpp
 * @brief     Plugin entry point for MindOpt solver backend
 * @date      Fri Apr  4 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstring>

#include <Mindopt.h>
#include <MindoptVersion.h>
#include <gtopt/solver_backend.hpp>

#include "mindopt_solver_backend.hpp"

namespace
{

// NOLINTNEXTLINE(cppcoreguidelines-avoid-c-arrays,hicpp-avoid-c-arrays,modernize-avoid-c-arrays)
const char* const k_solver_names[] = {
    "mindopt",
    nullptr,
};

}  // namespace

extern "C"
{
int gtopt_plugin_abi_version()  // NOLINT
{
  return gtopt::k_solver_abi_version;
}

const char* gtopt_plugin_name()  // NOLINT
{
  return "mindopt";
}

const char* const* gtopt_solver_names()  // NOLINT
{
  return k_solver_names;  // NOLINT
}

gtopt::SolverBackend* gtopt_create_backend(  // NOLINT
    const char* solver_name)
{
  if (std::strcmp(solver_name, "mindopt") == 0) {
    return new gtopt::MindOptSolverBackend();  // NOLINT
  }
  return nullptr;
}

// Plugin-level infinity query (no instance allocated).  Shares the
// `MDO_INFINITY` constant with `MindOptSolverBackend::infinity()`.
double gtopt_solver_infinity(const char* solver_name)  // NOLINT
{
  if (std::strcmp(solver_name, "mindopt") == 0) {
    return gtopt::MindOptSolverBackend::plugin_infinity();
  }
  return 0.0;
}

// Solver-library identity: MindoptVersion.h vs MDOversion() of the loaded
// libmindopt.
int gtopt_solver_build_info(const char* solver_name,  // NOLINT
                            gtopt::SolverBuildInfo* out)
{
  if (out == nullptr || std::strcmp(solver_name, "mindopt") != 0) {
    return 0;
  }
  int major = -1;
  int minor = -1;
  int technical = -1;
  MDOversion(&major, &minor, &technical);
  *out = gtopt::SolverBuildInfo {
      .struct_version = gtopt::k_solver_build_info_version,
      .compiled_major = MINDOPT_VERSION_MAJOR,
      .compiled_minor = MINDOPT_VERSION_MINOR,
      .compiled_patch = MINDOPT_VERSION_BUILD,
      .runtime_major = major,
      .runtime_minor = minor,
      .runtime_patch = technical,
      // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
      .library_anchor = reinterpret_cast<const void*>(&MDOversion),
  };
  return gtopt::k_solver_build_info_version;
}
}
