# add_expected_values_case.cmake - e2e case checked against derived values
#
# Usage:
#   add_expected_values_case(<test_name> <system_json>
#     CASE_DIR <dir>          # relative to CASES_DIR
#     [EXPECTED <file>]       # default expected.json in CASE_DIR
#     [KEY <key>]             # default: <system_json> without .json
#     [LABELS <list>])
#
# Registers two CTest tests:
#   e2e_<test_name>_solve  - run gtopt on <system_json> (run_gtopt.cmake:
#                            --write-out all, shards reassembled)
#   e2e_<test_name>_check  - tools/check_expected_values.py asserts the
#                            objective, primal values and duals listed
#                            under <KEY> in EXPECTED (explicit tolerance,
#                            fails on any mismatch — unlike the golden-CSV
#                            comparison, which only warns in default mode)

include_guard(GLOBAL)

function(add_expected_values_case test_name system_json)
  cmake_parse_arguments(ARG "" "CASE_DIR;EXPECTED;KEY" "LABELS" ${ARGN})

  if(NOT DEFINED GTOPT_EXECUTABLE_FILE)
    if(NOT DEFINED GTOPT_EXECUTABLE_TARGET)
      set(GTOPT_EXECUTABLE_TARGET "${PROJECT_NAME}")
    endif()
    set(GTOPT_EXECUTABLE_FILE "$<TARGET_FILE:${GTOPT_EXECUTABLE_TARGET}>")
  endif()

  set(case_dir "${CASES_DIR}/${ARG_CASE_DIR}")
  set(input_file "${case_dir}/${system_json}")
  if(NOT ARG_EXPECTED)
    set(ARG_EXPECTED "expected.json")
  endif()
  set(expected_file "${case_dir}/${ARG_EXPECTED}")
  if(NOT ARG_KEY)
    string(REGEX REPLACE "\\.json$" "" ARG_KEY "${system_json}")
  endif()
  set(test_output "${CMAKE_CURRENT_BINARY_DIR}/test_output/${test_name}")
  set(check_py "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../../tools/check_expected_values.py")

  foreach(_f "${input_file}" "${expected_file}" "${check_py}")
    if(NOT EXISTS "${_f}")
      message(WARNING "Skipping e2e case '${test_name}': not found: ${_f}")
      return()
    endif()
  endforeach()

  add_test(
    NAME e2e_${test_name}_solve
    COMMAND ${CMAKE_COMMAND}
      -DGTOPT_BINARY=${GTOPT_EXECUTABLE_FILE}
      -DINPUT_FILE=${input_file}
      -DOUTPUT_DIR=${test_output}
      -DWORKING_DIR=${case_dir}
      -P ${CMAKE_SCRIPTS_DIR}/run_gtopt.cmake
    WORKING_DIRECTORY "${case_dir}"
  )
  add_test(
    NAME e2e_${test_name}_check
    COMMAND ${Python3_EXECUTABLE} "${check_py}"
      --output-dir "${test_output}"
      --expected "${expected_file}"
      --case "${ARG_KEY}"
  )
  set_tests_properties(e2e_${test_name}_check
    PROPERTIES DEPENDS e2e_${test_name}_solve)
  if(ARG_LABELS)
    set_tests_properties(e2e_${test_name}_solve e2e_${test_name}_check
      PROPERTIES LABELS "${ARG_LABELS}")
  endif()
endfunction()
