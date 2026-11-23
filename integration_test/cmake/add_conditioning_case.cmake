# add_conditioning_case.cmake - Register LP-conditioning (kappa) tests
#
# Two functions, both driving tools/check_case_conditioning.py:
#
#   add_conditioning_variant(<prefix> <variant>
#     CASE_DIR <dir> CASE_JSON <file> SOLVER <solver>
#     [PHASES <n>] [TIMEOUT <s>] [SET <k=v>...] [LABELS <l>...])
#
#     Registers `<prefix>_<variant>_solve`: solve the case under one solver
#     variant into ${CMAKE_CURRENT_BINARY_DIR}/test_output/<prefix>/<variant>.
#     PHASES n keeps only the first n phases active (0/unset = all).
#     Skips (exit 77) when the case directory or the solver plugin is
#     missing.  All variants of one prefix share a RESOURCE_LOCK so they run
#     one at a time (each is a large, multi-threaded LP).
#
#   add_conditioning_check(<prefix> VARIANTS <v>... [LABELS <l>...]
#     ARGS <check args>...)
#
#     Registers `<prefix>_check`, which depends on every variant's solve
#     step and runs `check_case_conditioning.py check` with ARGS (variant
#     names in ARGS refer to the VARIANTS listed here).

function(add_conditioning_variant prefix variant)
  cmake_parse_arguments(ARG "" "CASE_DIR;CASE_JSON;SOLVER;PHASES;TIMEOUT"
                        "SET;LABELS" ${ARGN})
  if(NOT DEFINED ARG_PHASES)
    set(ARG_PHASES 0)
  endif()
  if(NOT DEFINED ARG_TIMEOUT)
    set(ARG_TIMEOUT 1800)
  endif()

  set(_out "${CMAKE_CURRENT_BINARY_DIR}/test_output/${prefix}/${variant}")
  set(_set_args "")
  foreach(_kv IN LISTS ARG_SET)
    list(APPEND _set_args --set "${_kv}")
  endforeach()

  add_test(
    NAME ${prefix}_${variant}_solve
    COMMAND "${Python3_EXECUTABLE}" "${CHECK_CONDITIONING_PY}" run
      --gtopt "${GTOPT_EXECUTABLE_FILE}"
      --case-dir "${ARG_CASE_DIR}"
      --case-json "${ARG_CASE_JSON}"
      --solver "${ARG_SOLVER}"
      --output-dir "${_out}"
      --phases "${ARG_PHASES}"
      ${_set_args}
  )
  set_tests_properties(${prefix}_${variant}_solve PROPERTIES
    TIMEOUT ${ARG_TIMEOUT}
    SKIP_RETURN_CODE 77
    RESOURCE_LOCK ${prefix}
    LABELS "${ARG_LABELS}"
  )
endfunction()

function(add_conditioning_check prefix)
  cmake_parse_arguments(ARG "" "" "VARIANTS;LABELS;ARGS" ${ARGN})

  set(_variant_args "")
  set(_depends "")
  foreach(_v IN LISTS ARG_VARIANTS)
    list(APPEND _variant_args
         --variant "${_v}=${CMAKE_CURRENT_BINARY_DIR}/test_output/${prefix}/${_v}")
    list(APPEND _depends ${prefix}_${_v}_solve)
  endforeach()

  add_test(
    NAME ${prefix}_check
    COMMAND "${Python3_EXECUTABLE}" "${CHECK_CONDITIONING_PY}" check
      ${_variant_args} ${ARG_ARGS}
  )
  set_tests_properties(${prefix}_check PROPERTIES
    DEPENDS "${_depends}"
    SKIP_RETURN_CODE 77
    LABELS "${ARG_LABELS}"
  )
endfunction()
