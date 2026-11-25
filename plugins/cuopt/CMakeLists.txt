#[=======================================================================[.rst:
gtopt_solver_cuopt
------------------

MODULE library implementing the SolverBackend interface via the NVIDIA
cuOpt GPU solver's stable C API (``cuopt/mathematical_optimization/cuopt_c.h``;
``libcuopt_mathopt.so`` from cuOpt >= 26.10, ``libcuopt.so`` before).
Uses ONLY the C API — no CUDA toolkit headers, no RAFT/RMM C++ templates — so it builds with the project's normal compiler.

cuOpt has no live-mutation API, so this backend keeps its own mutable
host model and rebuilds the immutable cuOpt problem on every solve
(buffer-and-replay).  See cuopt_solver_backend.hpp for the design.

#]=======================================================================]

include(GtoptPlugin)

gtopt_add_solver_plugin(
  NAME       gtopt_solver_cuopt
  SOURCES    cuopt_solver_backend.cpp cuopt_plugin.cpp
  INCLUDES   "${CUOPT_INCLUDE_DIRS}"
  LIBRARIES  ${CUOPT_LIBRARIES}
)

# The cuOpt headers carry no version macros: hand the plugin the version
# FindCuOpt read from the wheel's VERSION file, so gtopt_solver_build_info
# can compare it with cuOptGetVersion() of the library loaded at runtime.
if(CUOPT_VERSION MATCHES "^([0-9]+)\\.([0-9]+)(\\.([0-9]+))?")
  set(_cuopt_patch "${CMAKE_MATCH_4}")
  if(_cuopt_patch STREQUAL "")
    set(_cuopt_patch -1)
  endif()
  target_compile_definitions(gtopt_solver_cuopt PRIVATE
    GTOPT_CUOPT_COMPILED_VERSION_MAJOR=${CMAKE_MATCH_1}
    GTOPT_CUOPT_COMPILED_VERSION_MINOR=${CMAKE_MATCH_2}
    GTOPT_CUOPT_COMPILED_VERSION_PATCH=${_cuopt_patch}
  )
endif()
