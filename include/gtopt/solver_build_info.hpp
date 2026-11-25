/**
 * @file      solver_build_info.hpp
 * @brief     Plugin-exported identity: solver-library versions and build tag
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Two OPTIONAL C entry points a solver plugin may export so that
 * `SolverRegistry` can verify it is loading the plugin it expects, linked
 * to the solver library it was compiled against:
 *
 *  - `int gtopt_solver_build_info(const char* solver, SolverBuildInfo* out)`
 *    — the solver version from the headers the plugin was COMPILED with,
 *    the version reported by the library actually loaded at RUNTIME, and
 *    an address inside that library (the registry resolves it with
 *    `dladdr` / `dl_iterate_phdr` into a path and an ELF build-id).
 *  - `const char* gtopt_plugin_build_tag()` — the gtopt build the plugin
 *    belongs to (`<git describe>+<build-tree id>`), compared with the
 *    host's own tag.
 *  - `int gtopt_solver_version_pin(const char* solver, SolverVersionPin*)`
 *    — the solver release this gtopt is PINNED to (CMake
 *    `GTOPT_<SOLVER>_PIN_VERSION`, e.g. HiGHS 1.16.0: the goldens are
 *    generated with it) and whether the plugin was built with
 *    `-DGTOPT_<SOLVER>_ALLOW_UNPINNED=ON`.  The registry refuses a solver
 *    whose RUNTIME library is not the pinned release (see
 *    `check_solver_version_pin`).  A separate entry point rather than new
 *    `SolverBuildInfo` fields, so an older host never receives a struct
 *    larger than the one it allocated.
 *  - `int gtopt_plugin_linked_libraries(int* struct_version,
 *    const SolverLinkedLibrary** out)` — the identity (soname, GNU
 *    build-id, else SHA-256, and path) of every shared library the plugin
 *    was LINKED against, recorded at build time and compiled into the
 *    plugin (cmake_local/PluginLinkedLibraries.cmake, every plugin gets it
 *    from `gtopt_add_solver_plugin`).  The registry compares the solver
 *    library actually mapped in the process with it, so a foreign library
 *    that reports the right version (LD_LIBRARY_PATH / LD_PRELOAD) is
 *    refused (see `check_solver_library_identity`).
 *
 * All are optional: plugins that predate them still load (with a
 * warning), so adding them does not bump `k_solver_abi_version`.  The POD
 * carries its own `struct_version` so it can grow the same way
 * `SolverResourceDescriptor` does.
 */

#pragma once

namespace gtopt
{

/// Current layout version of @ref SolverBuildInfo.
inline constexpr int k_solver_build_info_version = 1;

/// Solver-library identity reported by a plugin (C-compatible POD).
/// Version components are `-1` when unknown.
struct SolverBuildInfo
{
  int struct_version {0};
  int compiled_major {-1};
  int compiled_minor {-1};
  int compiled_patch {-1};
  int runtime_major {-1};
  int runtime_minor {-1};
  int runtime_patch {-1};
  /// Address of a function or object that lives in the solver library
  /// (e.g. `&highsVersionMajor`).  When the library is linked statically
  /// into the plugin it resolves to the plugin itself.  May be nullptr.
  const void* library_anchor {nullptr};
};

/// `gtopt_solver_build_info`: fills @p out and returns the
/// `struct_version` it wrote, or 0 when @p solver_name is unknown.
using solver_plugin_build_info_fn = int (*)(const char* solver_name,
                                            SolverBuildInfo* out);

/// Current layout version of @ref SolverVersionPin.
inline constexpr int k_solver_version_pin_version = 1;

/// Solver release a plugin is pinned to (C-compatible POD).  Version
/// components are `-1` when the plugin is not pinned.
struct SolverVersionPin
{
  int struct_version {0};
  int pinned_major {-1};
  int pinned_minor {-1};
  int pinned_patch {-1};
  /// 1 when the plugin was built with `-DGTOPT_<SOLVER>_ALLOW_UNPINNED=ON`:
  /// a runtime library other than the pinned one is then only a warning.
  int allow_unpinned {0};
};

/// `gtopt_solver_version_pin`: fills @p out and returns the
/// `struct_version` it wrote, or 0 when @p solver_name is unknown.
using solver_plugin_version_pin_fn = int (*)(const char* solver_name,
                                             SolverVersionPin* out);

/// Current layout version of @ref SolverLinkedLibrary.
inline constexpr int k_solver_linked_library_version = 1;

/// Identity of one shared library a plugin was linked against, recorded at
/// build time (C-compatible POD; empty strings when unknown).
struct SolverLinkedLibrary
{
  const char* soname;  ///< DT_SONAME (else the file name)
  const char* build_id;  ///< lowercase hex GNU build-id, "" if none
  const char* sha256;  ///< SHA-256 of the file, only when it has no build-id
  const char* path;  ///< real path of the file the plugin was linked with
};

/// `gtopt_plugin_linked_libraries`: sets @p *out to the plugin's array of
/// linked libraries and @p *struct_version to its layout version, and
/// returns the number of entries.
using solver_plugin_linked_libraries_fn =
    int (*)(int* struct_version, const SolverLinkedLibrary** out);

/// `gtopt_plugin_build_tag`: NUL-terminated build tag of the plugin.
using solver_plugin_build_tag_fn = const char* (*)();

}  // namespace gtopt
