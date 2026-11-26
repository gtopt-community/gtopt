cmake_minimum_required(VERSION 3.31.6)

project(gtoptTests LANGUAGES CXX)

# ---- Shared cmake module path ----
list(APPEND CMAKE_MODULE_PATH
     "${CMAKE_CURRENT_LIST_DIR}/../cmake_local"
     "${CMAKE_CURRENT_LIST_DIR}/../cmake"
)

# ---- Ninja job pools (limit parallelism to avoid OOM) ----
include(ProcessorCount)
ProcessorCount(NPROC)
if(NPROC EQUAL 0)
  set(NPROC 4)
endif()
math(EXPR LINK_JOBS "(${NPROC} + 1) / 2")

set_property(GLOBAL PROPERTY JOB_POOLS
  compile=${NPROC}
  link=${LINK_JOBS}
)
set(CMAKE_JOB_POOL_COMPILE compile)
set(CMAKE_JOB_POOL_LINK    link)

# ---- Build defaults ----
include(BuildTypeDefault)
include(CxxStandard)
include(Ccache)
include(FastLinker)

# ---- Options ----
option(ENABLE_TEST_COVERAGE "Enable test coverage" OFF)
option(TEST_INSTALLED_VERSION "Test the version found by find_package" OFF)
# Cascade/SDDP test files are integration-style tests masquerading as unit
# tests: each TEST_CASE runs a full multi-stage planning solve and takes
# 2-26 s, totalling ~2 min wall in the unit-test binary.  Default ON to
# preserve the current behaviour; turn OFF for a sub-minute ctest sweep
# (the heavier suites still get exercised by the integration tests).
option(GTOPT_BUILD_HEAVY_UNIT_TESTS
       "Include cascade/SDDP integration-style unit tests" ON)

set(CPPCHECK_ARGS
    "--check-level=exhaustive" "--suppress=returnDanglingLifetime" "--suppress=internalAstError"
)

# ---- Compiler checks and tools ----
include(CompilerCheck)
include("${CMAKE_CURRENT_LIST_DIR}/../cmake/tools.cmake")

# ---- Dependencies ----
option(CPM_USE_LOCAL_PACKAGES "Try find_package first" ON)
include("${CMAKE_CURRENT_LIST_DIR}/../cmake/CPM.cmake")

if(TEST_INSTALLED_VERSION)
  find_package(gtopt REQUIRED)
elseif(NOT TARGET gtopt::gtopt)
  CPMAddPackage(NAME gtopt SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/..")
endif()

CPMAddPackage(
  NAME doctest
  GITHUB_REPOSITORY doctest/doctest
  VERSION 2.5.0
  GIT_TAG d44d4f6e66232d716af82f00a063759e9d0e50d6 # v2.5.0
  DOWNLOAD_ONLY YES
)
add_library(doctest::doctest INTERFACE IMPORTED)
target_include_directories(doctest::doctest INTERFACE "${doctest_SOURCE_DIR}")
# Force __COUNTER__ for anonymous names: with unity builds, multiple .cpp files
# are batched into single TUs, so __LINE__ collisions occur when two files have
# TEST_CASE on the same line number.  Doctest 2.5 switched to __LINE__ for
# clang >= 22, but __COUNTER__ is required for unity builds.
# This also works with doctest 2.4 (which already defaults to __COUNTER__).
target_compile_definitions(doctest::doctest INTERFACE "DOCTEST_COUNTER=__COUNTER__")
message(STATUS "Using doctest ${CPM_PACKAGE_doctest_VERSION}")

include(ClangFormat)

# ---- Test executable ----
file(GLOB sources CONFIGURE_DEPENDS
     "${CMAKE_CURRENT_SOURCE_DIR}/source/[a-zA-Z]*.cpp"
     "${CMAKE_CURRENT_SOURCE_DIR}/source/json/[a-zA-Z]*.cpp"
)
list(FILTER sources EXCLUDE REGEX "test_benchmark_")

if(NOT GTOPT_BUILD_HEAVY_UNIT_TESTS)
  # Exclude the cascade/SDDP families entirely.  They are uniformly heavy
  # (every TEST_CASE runs a full planning solve), so file-level gating is
  # the right granularity.  Note: shared helper headers
  # (cascade_helpers.hpp, sddp_helpers.hpp, aperture_data_cache.hpp) are
  # header-only and not in the glob, so dropping the .cpp files is safe.
  list(FILTER sources EXCLUDE REGEX "test_cascade_")
  list(FILTER sources EXCLUDE REGEX "test_sddp_")
  message(STATUS
          "GTOPT_BUILD_HEAVY_UNIT_TESTS=OFF: dropping cascade/SDDP tests")
endif()

# Move main.cpp to the front of the source list so that Ninja schedules
# its compilation before the unity batches.  It defines
# DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN (standalone TU) and benefits from
# starting first to reduce critical-path build time.
set(_priority_src "${CMAKE_CURRENT_SOURCE_DIR}/source/main.cpp")
list(REMOVE_ITEM sources "${_priority_src}")
list(PREPEND sources "${_priority_src}")

add_executable(${PROJECT_NAME} ${sources})

target_link_libraries(
  ${PROJECT_NAME}
  doctest::doctest
  gtopt::gtopt
)

# ---- Unity build ----
# main.cpp is treated the same as every other test file: it participates
# in unity batching.  Its DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN works because
# the list(PREPEND) above makes it the first file in the source list and
# therefore the first #include in its unity batch, so the macro is defined
# before <doctest/doctest.h> is first processed.
#
# GTOPT_TEST_UNITY_BATCH_SIZE = number of test .cpp files per unity batch.
# The default (11) suits a local 20+-core host and a cold build: with
# ~416 files in source/ and ~46 in source/json/ it gives 42 batches
# (4 -> 116 batches).  Larger batches
# re-parse the gtopt headers fewer times, so a cold build is cheapest,
# but a change to one test file (or to a header it includes) recompiles
# its whole batch.  CI on a 4-core runner with a warm ccache passes a
# smaller value (see .github/workflows/ubuntu.yml) so a PR recompiles
# only the batches it actually touches.  0 disables unity for the tests
# (one object per test file).
#
# -DGTOPT_TIDY_NON_UNITY=ON overrides this (one TU per test file, for the
# clang-tidy compile database; see cmake_local/UnityBuild.cmake).
set(GTOPT_TEST_UNITY_BATCH_SIZE 11 CACHE STRING
    "Test files per gtoptTests unity batch (0 = no unity build)")
if(GTOPT_TEST_UNITY_BATCH_SIZE GREATER 0)
  set(GTOPT_UNITY_BUILD ON)
  include(UnityBuild)
  target_enable_unity_build(${PROJECT_NAME}
                            BATCH_SIZE ${GTOPT_TEST_UNITY_BATCH_SIZE})
else()
  message(STATUS "Unity build disabled for ${PROJECT_NAME} "
                 "(GTOPT_TEST_UNITY_BATCH_SIZE=0)")
endif()

# ---- Precompiled headers ----
include(PrecompiledHeaders)
target_gtopt_pch(${PROJECT_NAME})

if(NOT TEST_INSTALLED_VERSION)
  include(CompilerWarnings)
  target_set_warnings(${PROJECT_NAME})
  target_compile_definitions(
    ${PROJECT_NAME}
    PUBLIC "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:DOCTEST_CONFIG_USE_STD_HEADERS>"
  )
endif()

# In-tree `share/gtopt` (shipped dialect dictionaries) for tests that
# read the source copy explicitly; the library itself never probes it.
target_compile_definitions(
  ${PROJECT_NAME}
  PRIVATE GTOPT_TEST_SHARE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../share/gtopt"
)

# In-tree `cases/` for tests that build the canonical cases directly
# (test_numerical_stability.cpp).
target_compile_definitions(
  ${PROJECT_NAME}
  PRIVATE GTOPT_TEST_CASES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../cases"
)

# The configured GTOPT_PLUGIN_CHECK_DEFAULT (cache option of the library),
# so test_plugin_integrity.cpp can check it reached the compiled code.
if(DEFINED GTOPT_PLUGIN_CHECK_DEFAULT)
  string(TOLOWER "${GTOPT_PLUGIN_CHECK_DEFAULT}" _gtopt_test_plugin_check)
  target_compile_definitions(
    ${PROJECT_NAME}
    PRIVATE GTOPT_TEST_PLUGIN_CHECK_DEFAULT="${_gtopt_test_plugin_check}"
  )
endif()

# The HiGHS pin baked into the HiGHS plugin (cmake_local/FindHiGHS.cmake),
# so test_solver_version_pin.cpp can check it reached the plugin.
if(GTOPT_HIGHS_PIN_VERSION)
  target_compile_definitions(
    ${PROJECT_NAME}
    PRIVATE GTOPT_TEST_HIGHS_PIN_VERSION="${GTOPT_HIGHS_PIN_VERSION}"
  )
endif()

# ---- Ensure solver plugins are rebuilt before running tests ----
include(GtoptPlugin)
gtopt_require_solver_plugins(${PROJECT_NAME})

# ---- Test registration ----
enable_testing()
include(TestParallelism)
# Private TMPDIR per test (option GTOPT_TEST_PRIVATE_TMPDIR, default ON):
# the configure-time tests get TMPDIR=<root>/<name> from the module (root
# outside the source tree, one per build dir); the doctest-discovered ones
# get GTOPT_TEST_TMPDIR_BASE below and main.cpp makes a per-process
# directory under it (see main.cpp).
include(TestPrivateTmpdir)
set(_gtopt_doctest_env)
if(GTOPT_TEST_PRIVATE_TMPDIR)
  target_compile_definitions(${PROJECT_NAME} PRIVATE GTOPT_TEST_PRIVATE_TMPDIR=1
    "GTOPT_TEST_SOURCE_ROOT=\"${CMAKE_CURRENT_SOURCE_DIR}/..\"")
  set(_gtopt_doctest_env
      ENVIRONMENT "GTOPT_TEST_TMPDIR_BASE=${GTOPT_TEST_TMP_ROOT}/gtoptTests")
endif()
include("${doctest_SOURCE_DIR}/scripts/cmake/doctest.cmake")
# TIMEOUT backstop: a wedged solver call (e.g. a NaN row making CPLEX
# iterate forever — observed 2026-07-02 as a 3.7 h hang on the
# elastic-mode fixture) becomes a bounded failure instead of holding the
# whole ctest run hostage.  3600 s clears the slowest legitimate SDDP
# integration tests (~2400 s under a fully loaded -j20 run) with margin.
doctest_discover_tests(${PROJECT_NAME} PROPERTIES TIMEOUT 3600 ${_gtopt_doctest_env})

# Guard: a ';' in a test-case name makes doctest_discover_tests register
# a filter that matches nothing, so ctest reports "Passed" without running
# the test.  Fail loudly instead (cheap: one --list-test-cases call).
add_test(
  NAME gtoptTests.test_names_no_semicolon
  COMMAND ${CMAKE_COMMAND} -DTEST_EXECUTABLE=$<TARGET_FILE:${PROJECT_NAME}>
          -P "${CMAKE_CURRENT_LIST_DIR}/check_test_names.cmake"
)
set_tests_properties(gtoptTests.test_names_no_semicolon PROPERTIES TIMEOUT 120)

# $GTOPT_PLUGIN_DIR is read once, when the SolverRegistry singleton is
# built, so its search-order canary needs the variable set at process
# start: run that one test case in its own process with a canary dir.
set(GTOPT_PLUGIN_DIR_CANARY "${CMAKE_CURRENT_BINARY_DIR}/plugin_dir_canary")
file(MAKE_DIRECTORY "${GTOPT_PLUGIN_DIR_CANARY}")
add_test(
  NAME gtoptTests.plugin_dir_env_canary
  COMMAND $<TARGET_FILE:${PROJECT_NAME}>
          "--test-case=SolverRegistry - GTOPT_PLUGIN_DIR canary*"
)
set_tests_properties(
  gtoptTests.plugin_dir_env_canary
  PROPERTIES ENVIRONMENT "GTOPT_PLUGIN_DIR=${GTOPT_PLUGIN_DIR_CANARY}"
             TIMEOUT 120
)

# Two concurrent gtoptTests processes running a fixed-name probe must not
# collide: proves the per-process private TMPDIR made by main.cpp.
if(GTOPT_TEST_PRIVATE_TMPDIR)
  add_test(
    NAME gtoptTests.private_tmpdir_concurrent
    COMMAND ${CMAKE_COMMAND} -DTEST_EXECUTABLE=$<TARGET_FILE:${PROJECT_NAME}>
            "-DWORK=${CMAKE_CURRENT_BINARY_DIR}/private_tmpdir_check"
            -P "${CMAKE_CURRENT_LIST_DIR}/check_private_tmpdir.cmake"
  )
  set_tests_properties(gtoptTests.private_tmpdir_concurrent
                       PROPERTIES TIMEOUT 300)
endif()

# ---- Fake solver library: the identity check refuses it ----
# A fake HiGHS that reports the pinned version (LD_PRELOAD interposer and a
# libhighs.so.1 replacement for LD_LIBRARY_PATH), built only here, in the
# build tree.  check_fake_solver_library.cmake runs the real gtopt with it
# and requires exit code 3 with the solver-library identity refusal.
if(TARGET gtopt_solver_highs AND TARGET gtoptStandalone AND NOT APPLE
   AND NOT WIN32 AND GTOPT_HIGHS_PIN_VERSION MATCHES
                     "^([0-9]+)\\.([0-9]+)\\.([0-9]+)$")
  set(_fake_defs GTOPT_FAKE_HIGHS_MAJOR=${CMAKE_MATCH_1}
                 GTOPT_FAKE_HIGHS_MINOR=${CMAKE_MATCH_2}
                 GTOPT_FAKE_HIGHS_PATCH=${CMAKE_MATCH_3})
  set(_fake_dir "${CMAKE_CURRENT_BINARY_DIR}/fake_solver_lib")
  set(_fake_src "${CMAKE_CURRENT_SOURCE_DIR}/fake_solver_lib")

  add_library(gtopt_fake_highs_preload SHARED
              "${_fake_src}/fake_highs_version.cpp")
  add_library(gtopt_fake_highs_real_stub SHARED
              "${_fake_src}/highs_real_stub.cpp")
  add_library(gtopt_fake_highs_libhighs SHARED
              "${_fake_src}/fake_highs_version.cpp")
  # Keep the DT_NEEDED although no stub symbol is used (--as-needed).
  target_link_options(gtopt_fake_highs_libhighs PRIVATE
                      "LINKER:--no-as-needed")
  target_link_libraries(gtopt_fake_highs_libhighs
                        PRIVATE gtopt_fake_highs_real_stub)
  foreach(_t gtopt_fake_highs_preload gtopt_fake_highs_libhighs)
    target_compile_definitions(${_t} PRIVATE ${_fake_defs})
    target_include_directories(${_t} SYSTEM PRIVATE
      "$<TARGET_PROPERTY:gtopt_solver_highs,INCLUDE_DIRECTORIES>")
  endforeach()
  set_target_properties(gtopt_fake_highs_preload PROPERTIES
    OUTPUT_NAME gtopt_fake_highs
    LIBRARY_OUTPUT_DIRECTORY "${_fake_dir}/preload")
  set_target_properties(gtopt_fake_highs_real_stub PROPERTIES
    OUTPUT_NAME highs_real VERSION 1 SOVERSION 1
    LIBRARY_OUTPUT_DIRECTORY "${_fake_dir}/stub")
  set_target_properties(gtopt_fake_highs_libhighs PROPERTIES
    OUTPUT_NAME highs VERSION 1 SOVERSION 1 SKIP_BUILD_RPATH ON
    LIBRARY_OUTPUT_DIRECTORY "${_fake_dir}/libhighs")
  add_dependencies(${PROJECT_NAME} gtopt_fake_highs_preload
                   gtopt_fake_highs_libhighs)

  foreach(_mode preload ldpath)
    if(_mode STREQUAL "preload")
      set(_fake "$<TARGET_FILE:gtopt_fake_highs_preload>")
    else()
      set(_fake "$<TARGET_FILE:gtopt_fake_highs_libhighs>")
    endif()
    add_test(
      NAME gtopt.fake_solver_library.${_mode}
      COMMAND ${CMAKE_COMMAND} "-DGTOPT=$<TARGET_FILE:gtoptStandalone>"
              "-DPLUGIN=$<TARGET_FILE:gtopt_solver_highs>"
              "-DCASE=${CMAKE_CURRENT_SOURCE_DIR}/../cases/ieee_4b_ori/ieee_4b_ori.json"
              "-DFAKE=${_fake}" "-DMODE=${_mode}"
              "-DWORK=${_fake_dir}/run_${_mode}"
              -P "${CMAKE_CURRENT_SOURCE_DIR}/check_fake_solver_library.cmake"
    )
    set_tests_properties(gtopt.fake_solver_library.${_mode}
                         PROPERTIES TIMEOUT 300 LABELS plugin_integrity)
    # Same check with ONLY the HiGHS plugin visible (a CI runner without
    # COIN-OR): once HiGHS is refused no plugin loads at all.
    add_test(
      NAME gtopt.fake_solver_library.${_mode}.highs_only
      COMMAND ${CMAKE_COMMAND} "-DGTOPT=$<TARGET_FILE:gtoptStandalone>"
              "-DPLUGIN=$<TARGET_FILE:gtopt_solver_highs>"
              "-DCASE=${CMAKE_CURRENT_SOURCE_DIR}/../cases/ieee_4b_ori/ieee_4b_ori.json"
              "-DFAKE=${_fake}" "-DMODE=${_mode}" -DHIGHS_ONLY=ON
              "-DWORK=${_fake_dir}/run_${_mode}_highs_only"
              -P "${CMAKE_CURRENT_SOURCE_DIR}/check_fake_solver_library.cmake"
    )
    set_tests_properties(gtopt.fake_solver_library.${_mode}.highs_only
                         PROPERTIES TIMEOUT 300 LABELS plugin_integrity)
  endforeach()

  # A modified copy of the real libhighs with its build-id note intact:
  # refused only under -DGTOPT_SOLVER_LIB_FULL_HASH=ON, accepted otherwise.
  if(CMAKE_OBJCOPY AND CMAKE_READELF)
    add_test(
      NAME gtopt.fake_solver_library.tampered
      COMMAND ${CMAKE_COMMAND} "-DGTOPT=$<TARGET_FILE:gtoptStandalone>"
              "-DPLUGIN=$<TARGET_FILE:gtopt_solver_highs>"
              "-DCASE=${CMAKE_CURRENT_SOURCE_DIR}/../cases/ieee_4b_ori/ieee_4b_ori.json"
              "-DOBJCOPY=${CMAKE_OBJCOPY}" "-DREADELF=${CMAKE_READELF}"
              "-DFULL_HASH=${GTOPT_SOLVER_LIB_FULL_HASH}"
              "-DWORK=${_fake_dir}/run_tampered"
              -P "${CMAKE_CURRENT_SOURCE_DIR}/check_tampered_solver_library.cmake"
    )
    set_tests_properties(gtopt.fake_solver_library.tampered
                         PROPERTIES TIMEOUT 300 LABELS plugin_integrity)
  endif()
endif()

# ---- Coverage ----
include(Coverage)
if(ENABLE_TEST_COVERAGE)
  target_enable_coverage(${PROJECT_NAME})
  # Also instrument the gtopt library itself so that source/*.cpp files are
  # included in the coverage report (not just the headers).
  if(NOT TEST_INSTALLED_VERSION AND TARGET gtopt)
    target_enable_coverage(gtopt PRIVATE)
  endif()
endif()
