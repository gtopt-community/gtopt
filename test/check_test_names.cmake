# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Guard: fail if any doctest test-case name contains a ';'.
#
# doctest_discover_tests() turns the `--list-test-cases` output into a
# CMake list, so a ';' inside a name splits it in two.  The resulting
# ctest entry registers a `--test-case=<fragment>` filter that matches
# nothing, and doctest exits 0 -> ctest reports "Passed" without running
# the test.  Use ',' or ' —' instead (commas are escaped by doctest).
#
# Usage: cmake -DTEST_EXECUTABLE=<path> -P check_test_names.cmake

if(NOT TEST_EXECUTABLE)
  message(FATAL_ERROR "TEST_EXECUTABLE not set")
endif()

execute_process(
  COMMAND "${TEST_EXECUTABLE}" --list-test-cases --no-version
  OUTPUT_VARIABLE output
  RESULT_VARIABLE result
)
if(NOT result EQUAL 0)
  message(FATAL_ERROR "'${TEST_EXECUTABLE} --list-test-cases' failed: ${result}")
endif()

string(FIND "${output}" ";" pos)
if(NOT pos EQUAL -1)
  # Escape ';' so the offending lines print intact.
  string(REPLACE ";" "<SEMICOLON>" escaped "${output}")
  string(REGEX MATCHALL "[^\n]*<SEMICOLON>[^\n]*" bad "${escaped}")
  string(REPLACE ";" "\n  " bad "${bad}")
  message(FATAL_ERROR
    "doctest test-case names must not contain ';' (ctest would silently "
    "skip them; use ',' instead):\n  ${bad}")
endif()
