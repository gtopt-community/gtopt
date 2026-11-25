#[=======================================================================[.rst:
gtopt_solver_highs
------------------

MODULE library implementing the SolverBackend interface via the native
HiGHS C++ API.  Requires HiGHS >= 1.5.0.

#]=======================================================================]

include(GtoptPlugin)

gtopt_add_solver_plugin(
  NAME       gtopt_solver_highs
  SOURCES    highs_solver_backend.cpp highs_plugin.cpp
  INCLUDES   "${HIGHS_INCLUDE_DIRS}"
  LIBRARIES  ${HIGHS_LIBRARIES}
)

# Bake the HiGHS pin into the plugin: SolverRegistry refuses it at load
# time when the runtime libhighs is not GTOPT_HIGHS_PIN_VERSION, unless the
# plugin was built with GTOPT_HIGHS_ALLOW_UNPINNED=ON (then it warns).  An
# empty GTOPT_HIGHS_PIN_VERSION leaves the plugin unpinned.
if(GTOPT_HIGHS_PIN_VERSION MATCHES "^([0-9]+)\\.([0-9]+)(\\.([0-9]+))?$")
  set(_highs_pin_patch -1)
  if(NOT "${CMAKE_MATCH_4}" STREQUAL "")
    set(_highs_pin_patch "${CMAKE_MATCH_4}")
  endif()
  target_compile_definitions(
    gtopt_solver_highs
    PRIVATE GTOPT_HIGHS_PIN_MAJOR=${CMAKE_MATCH_1}
            GTOPT_HIGHS_PIN_MINOR=${CMAKE_MATCH_2}
            GTOPT_HIGHS_PIN_PATCH=${_highs_pin_patch}
  )
elseif(GTOPT_HIGHS_PIN_VERSION)
  message(FATAL_ERROR "GTOPT_HIGHS_PIN_VERSION must be MAJOR.MINOR[.PATCH], "
                      "got '${GTOPT_HIGHS_PIN_VERSION}'")
endif()
if(GTOPT_HIGHS_ALLOW_UNPINNED)
  target_compile_definitions(gtopt_solver_highs
                             PRIVATE GTOPT_HIGHS_ALLOW_UNPINNED_BUILD=1)
endif()
