/**
 * @file      cplex_plugin.cpp
 * @brief     Plugin entry point for CPLEX solver backend
 * @date      Tue Mar 25 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstring>

#include <gtopt/solver_backend.hpp>
#include <ilcplex/cplex.h>

#include "cplex_solver_backend.hpp"

namespace
{

// NOLINTNEXTLINE(cppcoreguidelines-avoid-c-arrays,hicpp-avoid-c-arrays,modernize-avoid-c-arrays)
const char* const k_solver_names[] = {
    "cplex",
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
  return "cplex";
}

const char* const* gtopt_solver_names()  // NOLINT
{
  return k_solver_names;  // NOLINT
}

gtopt::SolverBackend* gtopt_create_backend(  // NOLINT
    const char* solver_name)
{
  if (std::strcmp(solver_name, "cplex") == 0) {
    return new gtopt::CplexSolverBackend();  // NOLINT
  }
  return nullptr;
}

// Plugin-level infinity query (no SolverBackend instance allocated).
// Delegates to `CplexSolverBackend::plugin_infinity()` so the value
// is single-source-of-truth shared with the instance method
// `CplexSolverBackend::infinity()`.  CPLEX uses CPX_INFBOUND = 1e+20.
double gtopt_solver_infinity(const char* solver_name)  // NOLINT
{
  if (std::strcmp(solver_name, "cplex") == 0) {
    return gtopt::CplexSolverBackend::plugin_infinity();
  }
  return 0.0;
}

// Solver-library identity.  CPLEX is linked statically (static_pic
// libcplex.a), so the anchor resolves to this plugin and the runtime
// version cannot differ from CPX_VERSION (VVRRMMFF, e.g. 22010100).
int gtopt_solver_build_info(const char* solver_name,  // NOLINT
                            gtopt::SolverBuildInfo* out)
{
  if (out == nullptr || std::strcmp(solver_name, "cplex") != 0) {
    return 0;
  }
  constexpr int ver = CPX_VERSION;
  *out = gtopt::SolverBuildInfo {
      .struct_version = gtopt::k_solver_build_info_version,
      .compiled_major = ver / 1000000,
      .compiled_minor = (ver / 10000) % 100,
      .compiled_patch = (ver / 100) % 100,
      // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
      .library_anchor = reinterpret_cast<const void*>(&CPXopenCPLEX),
  };
  return gtopt::k_solver_build_info_version;
}
}
