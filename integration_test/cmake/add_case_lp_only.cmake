# add_case_lp_only.cmake - Build the LP of every committed case JSON
#
# Usage:
#   add_case_lp_only_tests(<prefix> <cases_root> [LABELS <list>]
#                          [TIMEOUT <seconds>])
#
# Globs `<cases_root>/*/*.json`, keeps the planning files (those that carry
# a `"system"` object next to `"simulation"` or `"options"`), and registers
# one CTest per file:
#
#   <prefix>_<case_dir>_<json_stem>
#
# Each test runs `gtopt <json> --lp-only` from the case directory, so the
# JSON is parsed with the current schema and every input table it points at
# is read and turned into LP rows/columns, without solving.  A case left
# behind by a schema change (e.g. a stale converter output that fails with
# "Could not find member in JSON class") therefore fails CI instead of
# rotting silently.
#
# Skipped files: converter state snapshots (`*_state.json`), golden
# reference outputs (`golden*.json`) and pandapower exports (`*_pp.json`).

function(add_case_lp_only_tests prefix cases_root)
  cmake_parse_arguments(ARG "" "TIMEOUT" "LABELS" ${ARGN})
  if(NOT DEFINED ARG_TIMEOUT)
    set(ARG_TIMEOUT 300)
  endif()

  if(NOT DEFINED GTOPT_EXECUTABLE_FILE)
    if(NOT DEFINED GTOPT_EXECUTABLE_TARGET)
      set(GTOPT_EXECUTABLE_TARGET "${PROJECT_NAME}")
    endif()
    set(GTOPT_EXECUTABLE_FILE "$<TARGET_FILE:${GTOPT_EXECUTABLE_TARGET}>")
  endif()

  get_filename_component(_root "${cases_root}" ABSOLUTE)
  file(GLOB _jsons CONFIGURE_DEPENDS "${_root}/*/*.json")
  list(SORT _jsons)

  set(_count 0)
  foreach(_json IN LISTS _jsons)
    get_filename_component(_name "${_json}" NAME)
    if(_name MATCHES "_state\\.json$" OR _name MATCHES "^golden"
       OR _name MATCHES "_pp\\.json$")
      continue()
    endif()

    file(READ "${_json}" _content)
    string(FIND "${_content}" "\"system\"" _has_system)
    string(FIND "${_content}" "\"simulation\"" _has_simulation)
    string(FIND "${_content}" "\"options\"" _has_options)
    if(_has_system EQUAL -1
       OR (_has_simulation EQUAL -1 AND _has_options EQUAL -1))
      continue()
    endif()

    get_filename_component(_case_dir "${_json}" DIRECTORY)
    get_filename_component(_case "${_case_dir}" NAME)
    get_filename_component(_stem "${_json}" NAME_WE)
    set(_test "${prefix}_${_case}_${_stem}")
    set(_out "${CMAKE_CURRENT_BINARY_DIR}/test_output/${_test}")

    # Unit-commitment cases carry integer columns, which an LP-only
    # backend refuses even under --lp-only (CI exports GTOPT_SOLVER=clp):
    # load them into a MIP-capable solver.
    set(_solver_args "")
    string(FIND "${_content}" "\"commitment_array\"" _has_uc)
    if(NOT _has_uc EQUAL -1)
      set(_solver_args --solver cbc)
    endif()

    add_test(
      NAME "${_test}"
      COMMAND "${GTOPT_EXECUTABLE_FILE}" "${_name}"
        --lp-only
        --quiet
        ${_solver_args}
        --output-directory "${_out}"
      WORKING_DIRECTORY "${_case_dir}"
    )
    set_tests_properties("${_test}" PROPERTIES
      TIMEOUT ${ARG_TIMEOUT}
      LABELS "cases;${ARG_LABELS}")
    math(EXPR _count "${_count} + 1")
  endforeach()

  message(STATUS "add_case_lp_only_tests(${prefix}): ${_count} case(s) "
                 "under ${_root}")
endfunction()
