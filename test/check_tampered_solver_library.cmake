# TEST ONLY (ctest gtopt.fake_solver_library.tampered): a modified copy of
# the real libhighs that keeps its GNU build-id note (objcopy adds a
# section; the build-id is not recomputed) and so passes the build-id
# identity check.  Only the full-library hash (GTOPT_SOLVER_LIB_FULL_HASH)
# can tell it apart:
#   FULL_HASH=ON  -> refused: exit 3, "SHA256 MISMATCH", no fallback;
#   FULL_HASH=OFF -> accepted: solves (exit 0), identity "verified".
#
#   cmake -DGTOPT=<gtopt> -DPLUGIN=<libgtopt_solver_highs.so>
#         -DCASE=<case.json> -DOBJCOPY=<objcopy> -DREADELF=<readelf>
#         -DFULL_HASH=ON|OFF -DWORK=<scratch dir>
#         -P check_tampered_solver_library.cmake
#
# Layout: WORK/plugins holds a copy of the plugin (its RUNPATH starts with
# $ORIGIN) and the tampered libhighs.so.1 next to it, so the plugin's own
# RUNPATH resolves to the tampered file: no LD_LIBRARY_PATH, no RUNPATH
# shadowing, the right version and pin -- identity alone may refuse it.

# Script mode (-P) starts with default (OLD) policies: set them.
cmake_minimum_required(VERSION 3.25)
foreach(_v GTOPT PLUGIN CASE OBJCOPY READELF WORK)
  if(NOT ${_v})
    message(FATAL_ERROR "check_tampered_solver_library: ${_v} is required")
  endif()
endforeach()

file(REMOVE_RECURSE "${WORK}")
file(MAKE_DIRECTORY "${WORK}/plugins")
file(COPY "${CASE}" DESTINATION "${WORK}")
get_filename_component(_case_name "${CASE}" NAME)

execute_process(COMMAND ldd "${PLUGIN}" OUTPUT_VARIABLE _ldd)
if(NOT _ldd MATCHES "libhighs\\.so[^ \t]* => ([^ \t]+)")
  message(FATAL_ERROR "cannot find the real libhighs of ${PLUGIN}:\n${_ldd}")
endif()
file(REAL_PATH "${CMAKE_MATCH_1}" _real)

get_filename_component(_plugin_name "${PLUGIN}" NAME)
file(COPY_FILE "${PLUGIN}" "${WORK}/plugins/${_plugin_name}")
if(EXISTS "${PLUGIN}.build-id")
  file(COPY_FILE "${PLUGIN}.build-id" "${WORK}/plugins/${_plugin_name}.build-id")
endif()

set(_tampered "${WORK}/plugins/libhighs.so.1")
file(WRITE "${WORK}/tamper.txt" "gtopt test: not the linked libhighs\n")
execute_process(
  COMMAND "${OBJCOPY}" --add-section ".gtopt_tamper=${WORK}/tamper.txt"
          "${_real}" "${_tampered}"
  RESULT_VARIABLE _rc ERROR_VARIABLE _err)
if(NOT _rc EQUAL 0)
  message(FATAL_ERROR "objcopy failed: ${_err}")
endif()

# Precondition: same build-id, other content.
function(_build_id _file _out)
  execute_process(COMMAND "${READELF}" -n "${_file}" OUTPUT_VARIABLE _n)
  string(REGEX MATCH "Build ID: ([0-9a-f]+)" _ "${_n}")
  set(${_out} "${CMAKE_MATCH_1}" PARENT_SCOPE)
endfunction()
_build_id("${_real}" _id_real)
_build_id("${_tampered}" _id_tampered)
file(SHA256 "${_real}" _sha_real)
file(SHA256 "${_tampered}" _sha_tampered)
if(_id_real STREQUAL "" OR NOT _id_real STREQUAL _id_tampered
   OR _sha_real STREQUAL _sha_tampered)
  message(FATAL_ERROR "tampered copy is not a same-build-id fake: "
                      "build-id ${_id_real} vs ${_id_tampered}, "
                      "sha256 ${_sha_real} vs ${_sha_tampered}")
endif()

execute_process(
  COMMAND ${CMAKE_COMMAND} -E env --unset=GTOPT_SOLVER
          --unset=GTOPT_PLUGIN_CHECK --unset=GTOPT_HIGHS_ALLOW_UNPINNED
          --unset=GTOPT_SOLVER_FALLBACK_ON_REFUSED --unset=LD_LIBRARY_PATH
          --unset=LD_PRELOAD "GTOPT_PLUGIN_DIR=${WORK}/plugins"
          "${GTOPT}" --solver highs "${_case_name}"
  WORKING_DIRECTORY "${WORK}"
  RESULT_VARIABLE _rc
  OUTPUT_VARIABLE _out
  ERROR_VARIABLE _out
)
file(WRITE "${WORK}/run.log" "${_out}")

set(_failures "")
string(FIND "${_out}" "SHA256 MISMATCH" _mismatch)
string(FIND "${_out}" "its content differs" _differs)
string(FIND "${_out}" "${_tampered}" _named)
if(FULL_HASH)
  if(NOT _rc EQUAL 3)
    string(APPEND _failures "  - exit code is 3 (got ${_rc})\n")
  endif()
  if(_differs EQUAL -1 OR _named EQUAL -1)
    string(APPEND _failures
           "  - the refusal names ${_tampered} and 'its content differs'\n")
  endif()
  # The copied plugin fails the hash alone.  (The build-tree plugin, tried
  # next, reuses the already-mapped libhighs.so.1 and is refused for
  # RUNPATH shadowing too: expected, and it must not rescue 'highs'.)
  foreach(_other "plugin was compiled against" "but this gtopt pins"
                 "${WORK}/plugins/${_plugin_name}: Solver 'highs': loaded"
                 "not the library the plugin was built"
                 "not a library the plugin was linked against")
    string(FIND "${_out}" "${_other}" _p)
    if(NOT _p EQUAL -1)
      string(APPEND _failures "  - no '${_other}' refusal\n")
    endif()
  endforeach()
else()
  if(NOT _rc EQUAL 0)
    string(APPEND _failures "  - solves without the full hash (got ${_rc})\n")
  endif()
  if(NOT _mismatch EQUAL -1)
    string(APPEND _failures "  - no SHA-256 check without the full hash\n")
  endif()
endif()

if(_failures)
  message(FATAL_ERROR "tampered solver library (FULL_HASH=${FULL_HASH}):\n"
                      "${_failures}log in ${WORK}/run.log\n${_out}")
endif()
message(STATUS "tampered solver library (FULL_HASH=${FULL_HASH}): as expected")
