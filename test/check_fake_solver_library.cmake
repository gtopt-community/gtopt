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
#
# -DHIGHS_ONLY=ON: run a hard link of gtopt from <WORK>/bin with only the
#   HiGHS plugin next to it (bin/plugins/, a symlink), so no other plugin
#   is found -- the layout of a CI runner without COIN-OR.  Once HiGHS is
#   refused NO plugin loads: the errors must still name the refusal
#   ("Solver 'highs' not available."), not "No solver plugins found", and
#   --solvers must still list the refused plugin.

# Script mode (-P) starts with default (OLD) policies: set them.
cmake_minimum_required(VERSION 3.25)
foreach(_v GTOPT PLUGIN CASE FAKE MODE WORK)
  if(NOT ${_v})
    message(FATAL_ERROR "check_fake_solver_library: ${_v} is required")
  endif()
endforeach()

file(REMOVE_RECURSE "${WORK}")
file(MAKE_DIRECTORY "${WORK}/lib")
file(COPY "${CASE}" DESTINATION "${WORK}")
get_filename_component(_case_name "${CASE}" NAME)

if(HIGHS_ONLY)
  # gtopt searches <exe_dir>/plugins (and a few dirs relative to the
  # executable, plus $GTOPT_PLUGIN_DIR, unset below), so a hard link of
  # the binary in its own dir sees only what is put there.  The plugin is
  # a symlink: the registry loads its canonical path, where its build-id
  # manifest lives.
  file(MAKE_DIRECTORY "${WORK}/bin/plugins")
  file(CREATE_LINK "${GTOPT}" "${WORK}/bin/gtopt" COPY_ON_ERROR)
  get_filename_component(_plugin_name "${PLUGIN}" NAME)
  file(CREATE_LINK "${PLUGIN}" "${WORK}/bin/plugins/${_plugin_name}"
       SYMBOLIC)
  set(GTOPT "${WORK}/bin/gtopt")
endif()

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
            --unset=GTOPT_SOLVER_FALLBACK_ON_REFUSED
            --unset=GTOPT_PLUGIN_DIR ${_env} ${ARGN}
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
set(_layout "")
if(HIGHS_ONLY)
  set(_layout ", HiGHS-only")
endif()
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

# 3b. With no other plugin, nothing loads once HiGHS is refused: the
#     no-solver paths must report the refusal, not a missing install.
if(HIGHS_ONLY)
  string(FIND "${solvers_out}" "Available LP solvers:" _p)
  _require("_p EQUAL -1"
           "HiGHS-only layout: no plugin other than the refused HiGHS loads")
  foreach(_r explicit env_solver solvers)
    string(FIND "${${_r}_out}" "No solver plugins found" _p)
    _require("_p EQUAL -1" "${_r}: no 'No solver plugins found' message")
  endforeach()
  # No --solver / GTOPT_SOLVER: the automatic choice finds only refused
  # plugins -- exit 3 and the refusal, not the install hints.
  _run(auto "${GTOPT}" "${_case_name}")
  _require("auto_rc EQUAL 3" "automatic choice exit code is 3 (got ${auto_rc})")
  string(FIND "${auto_out}" "every one was refused" _p)
  _require("_p GREATER -1" "automatic choice reports that every plugin was refused")
  string(FIND "${auto_out}" "${_expect}" _p)
  _require("_p GREATER -1" "automatic choice reports the identity refusal")
  string(FIND "${auto_out}" "No solver plugins found" _p)
  _require("_p EQUAL -1" "automatic choice: no 'No solver plugins found' message")
endif()

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
  message(FATAL_ERROR "fake solver library (${MODE}${_layout}) not handled:\n"
                      "${_failures}logs in ${WORK}\n--- explicit run ---\n"
                      "${explicit_out}")
endif()
message(STATUS "fake solver library (${MODE}${_layout}): refused as expected")
