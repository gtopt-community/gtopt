/**
 * @file      scip_plugin.cpp
 * @brief     Plugin entry point for the SCIP solver backend
 * @date      2026-06-28
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstring>

#include <gtopt/solver_backend.hpp>
#include <scip/scip.h>

#include "scip_solver_backend.hpp"

namespace
{

// NOLINTNEXTLINE(cppcoreguidelines-avoid-c-arrays,hicpp-avoid-c-arrays,modernize-avoid-c-arrays)
const char* const k_solver_names[] = {
    "scip",
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
  return "scip";
}

const char* const* gtopt_solver_names()  // NOLINT
{
  return k_solver_names;  // NOLINT
}

gtopt::SolverBackend* gtopt_create_backend(  // NOLINT
    const char* solver_name)
{
  if (std::strcmp(solver_name, "scip") == 0) {
    return new gtopt::ScipSolverBackend();  // NOLINT
  }
  return nullptr;
}

double gtopt_solver_infinity(const char* solver_name)  // NOLINT
{
  if (std::strcmp(solver_name, "scip") == 0) {
    return gtopt::ScipSolverBackend::plugin_infinity();
  }
  return 0.0;
}

// Solver-library identity: compiled SCIP version (config.h of SCIP >= 9, or
// the packed SCIP_VERSION of older releases) vs SCIP{major,minor,tech}Version()
// of the loaded libscip.
int gtopt_solver_build_info(const char* solver_name,  // NOLINT
                            gtopt::SolverBuildInfo* out)
{
  if (out == nullptr || std::strcmp(solver_name, "scip") != 0) {
    return 0;
  }
#if defined(SCIP_VERSION_MAJOR) && defined(SCIP_VERSION_MINOR) \
    && defined(SCIP_VERSION_PATCH)
  constexpr int c_major = SCIP_VERSION_MAJOR;
  constexpr int c_minor = SCIP_VERSION_MINOR;
  constexpr int c_patch = SCIP_VERSION_PATCH;
#else
  constexpr int c_major = SCIP_VERSION / 100;
  constexpr int c_minor = (SCIP_VERSION / 10) % 10;
  constexpr int c_patch = SCIP_VERSION % 10;
#endif
  *out = gtopt::SolverBuildInfo {
      .struct_version = gtopt::k_solver_build_info_version,
      .compiled_major = c_major,
      .compiled_minor = c_minor,
      .compiled_patch = c_patch,
      .runtime_major = SCIPmajorVersion(),
      .runtime_minor = SCIPminorVersion(),
      .runtime_patch = SCIPtechVersion(),
      // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
      .library_anchor = reinterpret_cast<const void*>(&SCIPmajorVersion),
  };
  return gtopt::k_solver_build_info_version;
}
}
