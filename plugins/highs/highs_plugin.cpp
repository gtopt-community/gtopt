/**
 * @file      highs_plugin.cpp
 * @brief     Plugin entry point for HiGHS solver backend
 * @date      Sun Mar 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstring>

#include <HConfig.h>
#include <Highs.h>
#include <gtopt/solver_backend.hpp>

#include "highs_solver_backend.hpp"

// The HiGHS release gtopt pins (CMake GTOPT_HIGHS_PIN_VERSION, baked in by
// plugins/highs/CMakeLists.txt) and whether the plugin was built with
// -DGTOPT_HIGHS_ALLOW_UNPINNED=ON.  -1 = not pinned.
#ifndef GTOPT_HIGHS_PIN_MAJOR
#  define GTOPT_HIGHS_PIN_MAJOR -1
#endif
#ifndef GTOPT_HIGHS_PIN_MINOR
#  define GTOPT_HIGHS_PIN_MINOR -1
#endif
#ifndef GTOPT_HIGHS_PIN_PATCH
#  define GTOPT_HIGHS_PIN_PATCH -1
#endif
#ifndef GTOPT_HIGHS_ALLOW_UNPINNED_BUILD
#  define GTOPT_HIGHS_ALLOW_UNPINNED_BUILD 0
#endif

namespace
{

// NOLINTNEXTLINE(cppcoreguidelines-avoid-c-arrays,hicpp-avoid-c-arrays,modernize-avoid-c-arrays)
const char* const k_solver_names[] = {
    "highs",
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
  return "highs";
}

const char* const* gtopt_solver_names()  // NOLINT
{
  return k_solver_names;  // NOLINT
}

gtopt::SolverBackend* gtopt_create_backend(  // NOLINT
    const char* solver_name)
{
  if (std::strcmp(solver_name, "highs") == 0) {
    return new gtopt::HighsSolverBackend();  // NOLINT
  }
  return nullptr;
}

// Plugin-level infinity query (no instance allocated).  Shares the
// `kHighsInf = 1e+30` constant with `HighsSolverBackend::infinity()`.
double gtopt_solver_infinity(const char* solver_name)  // NOLINT
{
  if (std::strcmp(solver_name, "highs") == 0) {
    return gtopt::HighsSolverBackend::plugin_infinity();
  }
  return 0.0;
}

// Solver-library identity: HiGHS version from the headers this plugin was
// compiled with vs the libhighs actually loaded (SolverRegistry refuses a
// major.minor mismatch, e.g. ~/.local/lib/libhighs.so.1 from HiGHS 1.10
// shadowing the 1.16 the plugin was built against).
int gtopt_solver_build_info(const char* solver_name,  // NOLINT
                            gtopt::SolverBuildInfo* out)
{
  if (out == nullptr || std::strcmp(solver_name, "highs") != 0) {
    return 0;
  }
  *out = gtopt::SolverBuildInfo {
      .struct_version = gtopt::k_solver_build_info_version,
      .compiled_major = HIGHS_VERSION_MAJOR,
      .compiled_minor = HIGHS_VERSION_MINOR,
      .compiled_patch = HIGHS_VERSION_PATCH,
      .runtime_major = static_cast<int>(highsVersionMajor()),
      .runtime_minor = static_cast<int>(highsVersionMinor()),
      .runtime_patch = static_cast<int>(highsVersionPatch()),
      // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
      .library_anchor = reinterpret_cast<const void*>(&highsVersionMajor),
  };
  return gtopt::k_solver_build_info_version;
}

// The HiGHS release this gtopt pins: SolverRegistry refuses the plugin
// when the libhighs loaded at runtime is another release (unless built
// with -DGTOPT_HIGHS_ALLOW_UNPINNED=ON or $GTOPT_HIGHS_ALLOW_UNPINNED=1).
int gtopt_solver_version_pin(const char* solver_name,  // NOLINT
                             gtopt::SolverVersionPin* out)
{
  if (out == nullptr || std::strcmp(solver_name, "highs") != 0) {
    return 0;
  }
  *out = gtopt::SolverVersionPin {
      .struct_version = gtopt::k_solver_version_pin_version,
      .pinned_major = GTOPT_HIGHS_PIN_MAJOR,
      .pinned_minor = GTOPT_HIGHS_PIN_MINOR,
      .pinned_patch = GTOPT_HIGHS_PIN_PATCH,
      .allow_unpinned = GTOPT_HIGHS_ALLOW_UNPINNED_BUILD,
  };
  return gtopt::k_solver_version_pin_version;
}
}
