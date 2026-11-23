cmake_minimum_required(VERSION 3.14)

# compare_csv.cmake - Compare two CSV files via the Python gtopt_compare_csv tool
#
# Usage:
#   cmake -DACTUAL_FILE=<path> -DEXPECTED_FILE=<path>
#         [-DCASE_DIR=<path> -DCSV_REL_PATH=<rel>]
#         [-DTOLERANCE=<tol>] -P compare_csv.cmake
#
# Golden resolution (CASE_DIR and CSV_REL_PATH given, GTOPT_SOLVER set):
#
#   * output_<solver>/<csv> exists         -> compared with --strict.
#   * the case has an output_<solver>/ dir but not this <csv>
#                                          -> FAILURE, unless the
#     (case, solver, csv) is listed in integration_test/golden_fallback.txt;
#     a listed file falls back to output/<csv> (non-strict).  This used to
#     fall back silently, which hid stale per-solver goldens for weeks.
#   * the case has no output_<solver>/ dir -> output/<csv>, non-strict
#     (the case declares no solver-specific goldens).
#
# Without GTOPT_SOLVER the generic output/<csv> is used (non-strict).
# Non-strict mode tolerates degenerate LP primal differences.  The chosen
# golden and mode are always printed.

if(NOT DEFINED TOLERANCE)
  set(TOLERANCE "1e-6")
endif()

if(NOT EXISTS "${ACTUAL_FILE}")
  message(FATAL_ERROR "Actual file does not exist: ${ACTUAL_FILE}")
endif()

# Resolve the golden file (see the header for the rules).
set(_expected "${EXPECTED_FILE}")
set(_strict "")
set(_why "generic golden (GTOPT_SOLVER unset)")

if(DEFINED ENV{GTOPT_SOLVER} AND DEFINED CASE_DIR AND DEFINED CSV_REL_PATH)
  set(_solver "$ENV{GTOPT_SOLVER}")
  get_filename_component(_case "${CASE_DIR}" NAME)
  set(_solver_dir "${CASE_DIR}/output_${_solver}")
  set(_solver_expected "${_solver_dir}/${CSV_REL_PATH}")
  if(EXISTS "${_solver_expected}")
    set(_expected "${_solver_expected}")
    set(_strict "--strict")
    set(_why "solver golden output_${_solver}/")
  elseif(IS_DIRECTORY "${_solver_dir}")
    # Explicit opt-out list: "<case> <solver> <csv|*>  # reason" per line.
    get_filename_component(_list_dir "${CMAKE_CURRENT_LIST_FILE}" DIRECTORY)
    set(_fallback_list "${_list_dir}/../golden_fallback.txt")
    set(_allowed FALSE)
    if(EXISTS "${_fallback_list}")
      file(STRINGS "${_fallback_list}" _lines ENCODING UTF-8)
      foreach(_line IN LISTS _lines)
        string(REGEX REPLACE "#.*" "" _line "${_line}")
        string(STRIP "${_line}" _line)
        if(_line STREQUAL "")
          continue()
        endif()
        string(REGEX REPLACE "[ \t]+" ";" _f "${_line}")
        list(LENGTH _f _n)
        if(NOT _n EQUAL 3)
          message(FATAL_ERROR
            "golden_fallback.txt: malformed line '${_line}' "
            "(want '<case> <solver> <csv|*>')")
        endif()
        list(GET _f 0 _fc)
        list(GET _f 1 _fs)
        list(GET _f 2 _ff)
        if(_fc STREQUAL _case AND _fs STREQUAL _solver
           AND (_ff STREQUAL "*" OR _ff STREQUAL CSV_REL_PATH))
          set(_allowed TRUE)
          break()
        endif()
      endforeach()
    endif()
    if(NOT _allowed)
      message(FATAL_ERROR
        "Missing solver golden ${_solver_expected}: case '${_case}' has "
        "output_${_solver}/ but no ${CSV_REL_PATH} in it.  Regenerate the "
        "golden with GTOPT_SOLVER=${_solver}, or list "
        "'${_case} ${_solver} ${CSV_REL_PATH}' in "
        "integration_test/golden_fallback.txt with a reason.")
    endif()
    set(_why "listed fallback to output/ (golden_fallback.txt)")
  else()
    set(_why "generic golden (case has no output_${_solver}/)")
  endif()
endif()

if(NOT EXISTS "${_expected}")
  message(FATAL_ERROR "Expected file does not exist: ${_expected}")
endif()

if(_strict)
  set(_mode "strict")
else()
  set(_mode "non-strict")
endif()
message(STATUS "golden: ${_expected} [${_mode}; ${_why}]")

# Locate the Python comparison tool relative to this script
get_filename_component(_this_dir "${CMAKE_CURRENT_LIST_FILE}" DIRECTORY)
get_filename_component(_repo_root "${_this_dir}/../../" ABSOLUTE)
set(_compare_tool "${_repo_root}/tools/gtopt_compare_csv.py")

if(NOT EXISTS "${_compare_tool}")
  message(FATAL_ERROR "Comparison tool not found: ${_compare_tool}")
endif()

find_program(_python NAMES python3 python)
if(NOT _python)
  message(FATAL_ERROR "Python interpreter not found")
endif()

execute_process(
  COMMAND "${_python}" "${_compare_tool}"
    "${ACTUAL_FILE}" "${_expected}"
    --tolerance "${TOLERANCE}"
    ${_strict}
  RESULT_VARIABLE _rc
  OUTPUT_VARIABLE _stdout
  ERROR_VARIABLE _stderr
)

if(_stdout)
  message(STATUS "${_stdout}")
endif()

if(NOT _rc EQUAL 0)
  message(FATAL_ERROR "${_stderr}")
endif()
