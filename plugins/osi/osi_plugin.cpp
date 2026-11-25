/**
 * @file      osi_plugin.cpp
 * @brief     Plugin entry point for OSI solver backends (CLP/CBC)
 * @date      Sun Mar 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstring>
#include <typeinfo>

#include <coin/ClpConfig.h>
#include <coin/Clp_C_Interface.h>
#include <gtopt/solver_backend.hpp>

#include "osi_solver_backend.hpp"
#ifdef GTOPT_OSI_HAS_CBC
#  include <coin/CbcConfig.h>
#  include <coin/CbcModel.hpp>
#endif

namespace
{

// Null-terminated array of solver names this plugin provides
// NOLINTNEXTLINE(cppcoreguidelines-avoid-c-arrays,hicpp-avoid-c-arrays,modernize-avoid-c-arrays)
const char* const k_solver_names[] = {
    "clp",
#ifdef GTOPT_OSI_HAS_CBC
    "cbc",
#endif
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
  return "osi";
}

const char* const* gtopt_solver_names()  // NOLINT
{
  return k_solver_names;  // NOLINT
}

gtopt::SolverBackend* gtopt_create_backend(  // NOLINT
    const char* solver_name)
{
  using gtopt::OsiSolverBackend;

  if (std::strcmp(solver_name, "clp") == 0) {
    return new OsiSolverBackend(  // NOLINT
        OsiSolverBackend::OsiSolverType::clp);
  }
#ifdef GTOPT_OSI_HAS_CBC
  if (std::strcmp(solver_name, "cbc") == 0) {
    return new OsiSolverBackend(  // NOLINT
        OsiSolverBackend::OsiSolverType::cbc);
  }
#endif
  return nullptr;
}

// Plugin-level infinity query (no instance allocated).  COIN-OR's
// `OsiSolverInterface::getInfinity()` returns 1e+30 for both CLP
// and CBC; `OsiSolverBackend::plugin_infinity()` reports the same
// constant so callers using `is_infinity(v, plugin_infinity)` see
// identical behaviour against an instance-level call.
double gtopt_solver_infinity(const char* solver_name)  // NOLINT
{
  if (std::strcmp(solver_name, "clp") == 0
#ifdef GTOPT_OSI_HAS_CBC
      || std::strcmp(solver_name, "cbc") == 0
#endif
  )
  {
    return gtopt::OsiSolverBackend::plugin_infinity();
  }
  return 0.0;
}

// Solver-library identity: CLP compiled (ClpConfig.h) vs the libClp loaded
// (Clp_Version* from its C interface).  CBC has no runtime version query in
// libCbc; its compiled version and the loaded libCbc path (via CbcModel's
// typeinfo, which lives in libCbc) are reported, the version left unchecked.
int gtopt_solver_build_info(const char* solver_name,  // NOLINT
                            gtopt::SolverBuildInfo* out)
{
  if (out == nullptr) {
    return 0;
  }
  if (std::strcmp(solver_name, "clp") == 0) {
    *out = gtopt::SolverBuildInfo {
        .struct_version = gtopt::k_solver_build_info_version,
        .compiled_major = CLP_VERSION_MAJOR,
        .compiled_minor = CLP_VERSION_MINOR,
        .compiled_patch = CLP_VERSION_RELEASE,
        .runtime_major = Clp_VersionMajor(),
        .runtime_minor = Clp_VersionMinor(),
        .runtime_patch = Clp_VersionRelease(),
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
        .library_anchor = reinterpret_cast<const void*>(&Clp_VersionMajor),
    };
    return gtopt::k_solver_build_info_version;
  }
#ifdef GTOPT_OSI_HAS_CBC
  if (std::strcmp(solver_name, "cbc") == 0) {
    *out = gtopt::SolverBuildInfo {
        .struct_version = gtopt::k_solver_build_info_version,
        .compiled_major = CBC_VERSION_MAJOR,
        .compiled_minor = CBC_VERSION_MINOR,
        .compiled_patch = CBC_VERSION_RELEASE,
        .library_anchor = static_cast<const void*>(&typeid(CbcModel)),
    };
    return gtopt::k_solver_build_info_version;
  }
#endif
  return 0;
}
}
