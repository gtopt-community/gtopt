/**
 * @file      plugin_linked_libraries.cpp
 * @brief     `gtopt_plugin_linked_libraries` entry point shared by every
 *            plugin
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Compiled into each solver plugin by `gtopt_add_solver_plugin`.  The
 * table comes from `gtopt_plugin_linked_libraries.inc`, generated at build
 * time for THIS plugin (cmake_local/PluginLinkedLibraries.cmake) from the
 * shared libraries it links: soname, GNU build-id (else SHA-256) and path.
 * SolverRegistry compares the solver library mapped at run time with it.
 */

#include <array>

#include <gtopt/solver_build_info.hpp>

namespace
{

// The generated file holds zero or more `{soname, build_id, sha256, path},`
// rows; the sentinel keeps the array non-empty.
constexpr auto k_linked_libraries = std::to_array<gtopt::SolverLinkedLibrary>({
// Generated at build time (a source of every plugin target); absent only
// in configure-only analysis databases (clang-tidy).
#if __has_include("gtopt_plugin_linked_libraries.inc")
#  include "gtopt_plugin_linked_libraries.inc"
#endif
    {
        .soname = nullptr,
        .build_id = nullptr,
        .sha256 = nullptr,
        .path = nullptr,
    },
});

}  // namespace

extern "C"
{
int gtopt_plugin_linked_libraries(  // NOLINT
    int* struct_version,
    const gtopt::SolverLinkedLibrary** out)
{
  if (struct_version != nullptr) {
    *struct_version = gtopt::k_solver_linked_library_version;
  }
  if (out != nullptr) {
    *out = k_linked_libraries.data();
  }
  return static_cast<int>(k_linked_libraries.size()) - 1;
}
}
