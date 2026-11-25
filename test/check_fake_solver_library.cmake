# TEST ONLY (ctest gtopt.fake_solver_library.*): run gtopt with a fake
# HiGHS that reports the pinned version and check that the solver-library
# identity check refuses it -- exit code 3, no fallback to another solver.
#
#   cmake -DGTOPT=<gtopt> -DPLUGIN=<libgtopt_solver_highs.so>
#         -DCASE=<case.json> -DFAKE=<fake library file> -DMODE=preload|ldpath
#         -DWORK=<scratch dir> -P check_fake_solver_library.cmake
#
# MODE=preload: LD_PRELOAD=<libgtopt_fake_highs.so> interposes the version
#   functions.  Its soname is not in the plugin's RUNPATH, so the RUNPATH
#   check passes, and it reports the right version, so the version and pin
#   checks pass: ONLY the identity check may refuse it.
# MODE=ldpath: LD_LIBRARY_PATH=<dir> holds a fake libhighs.so.1 (forwarding
#   to the real library through libhighs_real.so.1 -> real libhighs).
foreach(_v GTOPT PLUGIN CASE FAKE MODE WORK)
  if(NOT ${_v})
    message(FATAL_ERROR "check_fake_solver_library: ${_v} is required")
  endif()
endforeach()

file(REMOVE_RECURSE "${WORK}")
file(MAKE_DIRECTORY "${WORK}/lib")
file(COPY "${CASE}" DESTINATION "${WORK}")
get_filename_component(_case_name "${CASE}" NAME)

if(MODE STREQUAL "preload")
  set(_env "LD_PRELOAD=${FAKE}")
  set(_expect "not a library the plugin was linked against")
elseif(MODE STREQUAL "ldpath")
  # The real libhighs the plugin resolves without LD_LIBRARY_PATH.
  execute_process(COMMAND ldd "${PLUGIN}" OUTPUT_VARIABLE _ldd)
  if(NOT _ldd MATCHES "libhighs\\.so[^ \t]* => ([^ \t]+)")
    message(FATAL_ERROR "cannot find the real libhighs of ${PLUGIN}:\n${_ldd}")
  endif()
  set(_real "${CMAKE_MATCH_1}")
  file(COPY_FILE "${FAKE}" "${WORK}/lib/libhighs.so.1")
  file(CREATE_LINK "${_real}" "${WORK}/lib/libhighs_real.so.1" SYMBOLIC)
  set(_env "LD_LIBRARY_PATH=${WORK}/lib")
  set(_expect "not the library the plugin was built against")
else()
  message(FATAL_ERROR "MODE must be preload or ldpath, got '${MODE}'")
endif()

function(_run _name)
  execute_process(
    COMMAND ${CMAKE_COMMAND} -E env --unset=GTOPT_SOLVER
            --unset=GTOPT_PLUGIN_CHECK --unset=GTOPT_HIGHS_ALLOW_UNPINNED
            --unset=GTOPT_SOLVER_FALLBACK_ON_REFUSED ${_env} ${ARGN}
    WORKING_DIRECTORY "${WORK}"
    RESULT_VARIABLE _rc
    OUTPUT_VARIABLE _out
    ERROR_VARIABLE _out
  )
  file(WRITE "${WORK}/${_name}.log" "${_out}")
  set(${_name}_rc "${_rc}" PARENT_SCOPE)
  set(${_name}_out "${_out}" PARENT_SCOPE)
endfunction()

set(_failures "")
function(_require _cond _what)
  cmake_language(EVAL CODE "
    if(NOT (${_cond}))
      string(APPEND _failures \"  - \${_what}\\n\")
    endif()")
  set(_failures "${_failures}" PARENT_SCOPE)
endfunction()

# 1. Explicit --solver highs: refused, exit 3, no other solver.
_run(explicit "${GTOPT}" --solver highs "${_case_name}")
_require("explicit_rc EQUAL 3" "--solver highs exit code is 3 (got ${explicit_rc})")
string(FIND "${explicit_out}" "Solver 'highs' not available." _p1)
_require("_p1 GREATER -1" "--solver highs reports 'Solver 'highs' not available.'")
string(FIND "${explicit_out}" "${_expect}" _p2)
_require("_p2 GREATER -1" "the identity refusal ('${_expect}') is reported")
string(FIND "${explicit_out}" "Refused (plugin check mode 'error'" _p3)
_require("_p3 GREATER -1" "the refusal marker is present")
if(MODE STREQUAL "preload")
  # The fake reports the pinned version and is not on the RUNPATH: the
  # version, pin and shadowing checks all pass -- identity alone refuses.
  foreach(_other "plugin was compiled against" "but this gtopt pins"
                 "RUNPATH points to")
    string(FIND "${explicit_out}" "${_other}" _p)
    _require("_p EQUAL -1" "no '${_other}' refusal (that check must pass)")
  endforeach()
endif()

# 2. GTOPT_SOLVER=highs: same failure (refused plugin => no fallback).
_run(env_solver "${CMAKE_COMMAND}" -E env GTOPT_SOLVER=highs "${GTOPT}"
     "${_case_name}")
_require("env_solver_rc EQUAL 3" "GTOPT_SOLVER=highs exit code is 3 (got ${env_solver_rc})")

# 3. --solvers lists the refused plugin.
_run(solvers "${GTOPT}" --solvers)
string(FIND "${solvers_out}" "Refused plugins:" _p4)
_require("_p4 GREATER -1" "--solvers lists 'Refused plugins:'")

# 4. The documented overrides load it anyway.  Under LD_LIBRARY_PATH the
#    RUNPATH-shadowing check refuses it too, which only --plugin-check
#    overrides; under LD_PRELOAD the per-solver env variable is enough.
if(MODE STREQUAL "preload")
  _run(override "${CMAKE_COMMAND}" -E env GTOPT_HIGHS_ALLOW_UNPINNED=1
       "${GTOPT}" --solver highs "${_case_name}")
  _require("override_rc EQUAL 0"
           "GTOPT_HIGHS_ALLOW_UNPINNED=1 solves (got ${override_rc})")
endif()
_run(warning "${GTOPT}" --plugin-check warning --solver highs
     "${_case_name}")
_require("warning_rc EQUAL 0" "--plugin-check warning solves (got ${warning_rc})")

if(_failures)
  message(FATAL_ERROR "fake solver library (${MODE}) not handled:\n"
                      "${_failures}logs in ${WORK}\n--- explicit run ---\n"
                      "${explicit_out}")
endif()
message(STATUS "fake solver library (${MODE}): refused as expected")
