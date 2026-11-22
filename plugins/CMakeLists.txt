#[=======================================================================[.rst:
Plugins
-------

Build solver backend plugins for gtopt.  Each plugin is a MODULE library
(dlopen-loaded at runtime) that implements the SolverBackend interface.

Plugins are discovered at runtime by SolverRegistry, which scans for
``libgtopt_solver_*.so`` files in standard paths:

  1. ``$GTOPT_PLUGIN_DIR``
  2. ``<exe>/../lib/gtopt/plugins/``
  3. ``<exe>/plugins/``
  4. ``/usr/local/lib/gtopt/plugins/``

#]=======================================================================]

# ---- OSI plugin (CLP / CBC) ----
# Always attempt to build if COIN-OR is available (it was found by the
# parent project's Solver.cmake).

if(COIN_FOUND)
  message(STATUS "Solver plugin: OSI enabled (COIN-OR at ${COIN_ROOT_DIR})")
  add_subdirectory(osi)
else()
  message(STATUS "Solver plugin: OSI skipped (COIN-OR not found)")
endif()

# ---- CPLEX plugin (direct C Callable Library, no OSI) ----

find_package(CPLEX QUIET)
if(CPLEX_FOUND)
  message(STATUS "Solver plugin: CPLEX enabled (${CPLEX_VERSION} at ${CPLEX_INCLUDE_DIR})")
  add_subdirectory(cplex)
else()
  message(STATUS "Solver plugin: CPLEX skipped (CPLEX not found; set CPLEX_ROOT_DIR)")
endif()

# ---- HiGHS plugin ----
# Look for the header and library directly first (FindHiGHS.cmake).  The
# Debian/Ubuntu libhighs-dev package ships a CMake config and a highs.pc
# with wrong paths (prefix "/", "/usr/usr/include"), and the config even
# aborts the configure when the separate `highs` binary package is
# missing, so the config and pkg-config are only fallbacks.

find_package(HiGHS MODULE QUIET)
if(NOT HIGHS_FOUND)
  find_package(highs CONFIG QUIET)
endif()
if(NOT highs_FOUND AND NOT HIGHS_FOUND)
  find_package(PkgConfig QUIET)
  if(PKG_CONFIG_FOUND)
    pkg_check_modules(HIGHS QUIET IMPORTED_TARGET "highs >= 1.5")
    if(HIGHS_FOUND AND TARGET PkgConfig::HIGHS)
      set(HIGHS_LIBRARIES PkgConfig::HIGHS)
    endif()
  endif()
endif()

# ---- MindOpt plugin (Alibaba DAMO Academy) ----

find_package(MindOpt QUIET)
if(MINDOPT_FOUND)
  message(STATUS "Solver plugin: MindOpt enabled (${MINDOPT_INCLUDE_DIR})")
  add_subdirectory(mindopt)
else()
  message(STATUS "Solver plugin: MindOpt skipped (MindOpt not found; set MINDOPT_HOME or MINDOPT_ROOT_DIR)")
endif()

# ---- Gurobi plugin ----

find_package(Gurobi QUIET)
if(GUROBI_FOUND)
  message(STATUS "Solver plugin: Gurobi enabled (${GUROBI_INCLUDE_DIR})")
  add_subdirectory(gurobi)
else()
  message(STATUS "Solver plugin: Gurobi skipped (Gurobi not found; set GUROBI_HOME or GUROBI_ROOT_DIR)")
endif()

# ---- cuOpt plugin (NVIDIA GPU solver; stable C API only) ----
# Gated like the other optional solvers.  FindCuOpt auto-detects the pip
# wheels via `python -c "import libcuopt_mathopt"` (cuOpt >= 26.10, where
# the C API and headers moved to the `libcuopt_mathopt-cu12/-cu13` wheel)
# and `import libcuopt` (<= 26.08 layout), or honours CUOPT_HOME /
# CUOPT_ROOT_DIR / -D_CUOPT_PYTHON=<venv python>.  Disable with
# -DGTOPT_DISABLE_CUOPT=ON.
#
# Minimum 26.8: MIP starts coexist with presolve from 26.08 (older
# releases hard-reject cuOptAddMIPStart under presolve), and the 26.08
# C API is the one the backend is validated against:
#   pip install --user "libcuopt-cu12>=26.8"     # or libcuopt-cu13
#
# A miss is a WARNING (not a quiet STATUS) when cuOpt was asked for
# explicitly (-DGTOPT_DISABLE_CUOPT=OFF, CUOPT_HOME, CUOPT_ROOT_DIR) or an
# NVIDIA GPU is present, so a GPU box never silently builds without it.

find_package(CuOpt 26.8 QUIET)
if(CUOPT_FOUND)
  if(CUOPT_VERSION)
    message(STATUS "Solver plugin: cuOpt enabled (${CUOPT_VERSION_FULL}: ${CUOPT_LIBRARY})")
  else()
    message(STATUS "Solver plugin: cuOpt enabled (${CUOPT_LIBRARY})")
  endif()
  add_subdirectory(cuopt)
elseif(NOT GTOPT_DISABLE_CUOPT)
  set(_cuopt_wanted "")
  if(DEFINED GTOPT_DISABLE_CUOPT)
    set(_cuopt_wanted "requested with -DGTOPT_DISABLE_CUOPT=OFF")
  elseif(DEFINED ENV{CUOPT_HOME} OR CUOPT_ROOT_DIR OR DEFINED ENV{CUOPT_ROOT_DIR})
    set(_cuopt_wanted "requested via CUOPT_HOME/CUOPT_ROOT_DIR")
  elseif(EXISTS "/proc/driver/nvidia/version")
    set(_cuopt_wanted "an NVIDIA GPU is present")
  endif()
  if(CUOPT_VERSION AND CUOPT_INCLUDE_DIR AND CUOPT_LIBRARY)
    set(_cuopt_why "found ${CUOPT_VERSION_FULL}, but >= 26.8 is required; upgrade libcuopt-cu12/-cu13")
  else()
    set(_cuopt_why "cuOpt not found: ${CUOPT_DIAGNOSTIC}. pip install libcuopt-cu12 (or -cu13; >= 26.10 also pulls libcuopt_mathopt), set CUOPT_HOME, or pass -D_CUOPT_PYTHON=<python that imports it>")
  endif()
  if(_cuopt_wanted)
    message(WARNING "Solver plugin: cuOpt skipped although ${_cuopt_wanted} -- ${_cuopt_why}.  Silence with -DGTOPT_DISABLE_CUOPT=ON.")
  else()
    message(STATUS "Solver plugin: cuOpt skipped (${_cuopt_why})")
  endif()
else()
  message(STATUS "Solver plugin: cuOpt disabled (GTOPT_DISABLE_CUOPT=ON)")
endif()

# ---- SCIP plugin (open-source CIP/MIP solver; Apache-2.0 since v9) ----
# Prefer SCIP's own CMake config (the `libscip` imported target); fall back to
# the bundled FindSCIP.cmake module when the config is unavailable or fails
# (its find_dependency(GMP/MPFR) needs dev headers we don't require to link
# libscip.so).  Disable explicitly with -DGTOPT_DISABLE_SCIP=ON.

if(NOT GTOPT_DISABLE_SCIP)
  find_package(SCIP CONFIG QUIET)
  if(SCIP_FOUND AND NOT SCIP_LIBRARIES AND TARGET libscip)
    set(SCIP_LIBRARIES libscip)
  endif()
  if(NOT SCIP_FOUND)
    find_package(SCIP MODULE QUIET)
  endif()
endif()
if(SCIP_FOUND)
  if(SCIP_VERSION)
    message(STATUS "Solver plugin: SCIP enabled (${SCIP_VERSION})")
  else()
    message(STATUS "Solver plugin: SCIP enabled")
  endif()
  add_subdirectory(scip)
else()
  message(STATUS "Solver plugin: SCIP skipped (SCIP not found; set SCIP_ROOT_DIR or -DGTOPT_DISABLE_SCIP=ON to silence)")
endif()

if(highs_FOUND OR HIGHS_FOUND)
  # Config-mode sets the highs::highs imported target.
  # Legacy Find-module mode sets HIGHS_INCLUDE_DIRS / HIGHS_LIBRARIES.
  if(TARGET highs::highs AND NOT HIGHS_LIBRARIES)
    get_target_property(_highs_inc highs::highs INTERFACE_INCLUDE_DIRECTORIES)
    set(HIGHS_INCLUDE_DIRS "${_highs_inc}" CACHE STRING "HiGHS include dirs")
    set(HIGHS_LIBRARIES highs::highs CACHE STRING "HiGHS libraries")
    set(HIGHS_FOUND TRUE)
  endif()
  if(HIGHS_VERSION)
    message(STATUS "Solver plugin: HiGHS enabled (${HIGHS_VERSION})")
  else()
    message(STATUS "Solver plugin: HiGHS enabled")
  endif()
  add_subdirectory(highs)
else()
  message(STATUS "Solver plugin: HiGHS skipped (HiGHS not found; set CMAKE_PREFIX_PATH)")
endif()
