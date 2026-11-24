cmake_minimum_required(VERSION 3.19)

# compare_sddp_bounds.cmake - assert that several SDDP runs report the same
# lower / upper bound (e.g. the same case under different LP equilibration).
#
# Usage:
#   cmake -DOUTPUT_DIRS="<dir1>;<dir2>;..." -DREL_TOL=<tol> \
#         -P compare_sddp_bounds.cmake
#
# Reads `<dir>/solver_status.json` of every run and fails when any
# `lower_bound` / `upper_bound` differs from the first run's by more than
# REL_TOL relative (floor 1.0 on the magnitude).

if(NOT OUTPUT_DIRS)
  message(FATAL_ERROR "OUTPUT_DIRS is required")
endif()
if(NOT REL_TOL)
  set(REL_TOL 1e-6)
endif()

set(_ref_dir "")
foreach(_dir IN LISTS OUTPUT_DIRS)
  set(_file "${_dir}/solver_status.json")
  if(NOT EXISTS "${_file}")
    message(FATAL_ERROR "missing ${_file}")
  endif()
  file(READ "${_file}" _json)
  foreach(_key lower_bound upper_bound)
    string(JSON _v GET "${_json}" ${_key})
    if(NOT _ref_dir)
      set(_ref_${_key} "${_v}")
    else()
      # CMake has no floating-point arithmetic: delegate the relative
      # comparison to python3 (already required by the validate steps).
      execute_process(
        COMMAND python3 -c
          "import sys; a=float(sys.argv[1]); b=float(sys.argv[2]); t=float(sys.argv[3]); sys.exit(0 if abs(a-b) <= t*max(1.0,abs(a)) else 1)"
          "${_ref_${_key}}" "${_v}" "${REL_TOL}"
        RESULT_VARIABLE _rc)
      if(NOT _rc EQUAL 0)
        message(FATAL_ERROR
          "${_key} differs: ${_ref_dir} = ${_ref_${_key}}, "
          "${_dir} = ${_v} (rel_tol ${REL_TOL})")
      endif()
    endif()
    message(STATUS "${_dir}: ${_key} = ${_v}")
  endforeach()
  if(NOT _ref_dir)
    set(_ref_dir "${_dir}")
  endif()
endforeach()
