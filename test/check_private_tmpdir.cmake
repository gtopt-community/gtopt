# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Two gtoptTests processes running the fixed-name probe test case at the
# same time must not see each other's file: each process gets a private
# TMPDIR from main.cpp (option GTOPT_TEST_PRIVATE_TMPDIR).
#
# A control run with the private TMPDIR switched off and both processes
# pointed at one shared TMPDIR shows the probe does detect a collision.
# The control is informational only (its outcome depends on scheduling),
# so it never fails this test.
#
# Usage: cmake -DTEST_EXECUTABLE=<path> -DWORK=<dir>
#              -P check_private_tmpdir.cmake

if(NOT TEST_EXECUTABLE OR NOT WORK)
  message(FATAL_ERROR "TEST_EXECUTABLE and WORK must be set")
endif()

set(_tc "--test-case=private TMPDIR - fixed-name probe is not clobbered")
set(_probe_ms 3000)

# execute_process() with several COMMANDs runs them concurrently (as a
# pipeline; the probe ignores stdin).  Each process writes its report to
# its own --out file: writing it to the pipe would SIGPIPE the first
# process whenever the second one has already exited.
function(run_pair OUT_RESULTS)
  file(MAKE_DIRECTORY "${WORK}")
  set(_out_a "${WORK}/probe_a.txt")
  set(_out_b "${WORK}/probe_b.txt")
  file(REMOVE "${_out_a}" "${_out_b}")
  execute_process(
    COMMAND ${CMAKE_COMMAND} -E env ${ARGN} GTOPT_TEST_PROBE_MS=${_probe_ms}
            "${TEST_EXECUTABLE}" "${_tc}" --no-version "--out=${_out_a}"
    COMMAND ${CMAKE_COMMAND} -E env ${ARGN} GTOPT_TEST_PROBE_MS=${_probe_ms}
            "${TEST_EXECUTABLE}" "${_tc}" --no-version "--out=${_out_b}"
    RESULTS_VARIABLE _results
    OUTPUT_VARIABLE _out
    ERROR_VARIABLE _err
  )
  set(_reports "")
  foreach(_f "${_out_a}" "${_out_b}")
    if(EXISTS "${_f}")
      file(READ "${_f}" _txt)
      string(APPEND _reports "${_txt}")
    endif()
  endforeach()
  set(${OUT_RESULTS} "${_results}" PARENT_SCOPE)
  set(LAST_OUTPUT "${_reports}${_out}${_err}" PARENT_SCOPE)
endfunction()

# 1. Default (private TMPDIR): both processes must pass.
run_pair(results)
message(STATUS "private TMPDIR: exit codes ${results}")
if(NOT results STREQUAL "0;0")
  message(FATAL_ERROR
    "two concurrent probe processes collided with the private TMPDIR "
    "enabled (exit codes ${results}):\n${LAST_OUTPUT}")
endif()

# 2. Control: shared TMPDIR, private TMPDIR disabled.
set(_shared "${WORK}/shared")
file(REMOVE_RECURSE "${_shared}")
file(MAKE_DIRECTORY "${_shared}")
run_pair(results GTOPT_TEST_PRIVATE_TMPDIR=0 "TMPDIR=${_shared}")
if(results STREQUAL "0;0")
  message(STATUS "control (shared TMPDIR): no collision observed this time")
else()
  message(STATUS
    "control (shared TMPDIR): collision detected as expected (${results})")
endif()
file(REMOVE_RECURSE "${_shared}")
