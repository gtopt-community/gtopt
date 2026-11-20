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

# ---- Ensure solver plugins are rebuilt before running tests ----
include(GtoptPlugin)
gtopt_require_solver_plugins(${PROJECT_NAME})

# ---- Test registration ----
enable_testing()
include(TestParallelism)
include("${doctest_SOURCE_DIR}/scripts/cmake/doctest.cmake")
# TIMEOUT backstop: a wedged solver call (e.g. a NaN row making CPLEX
# iterate forever on the elastic-mode fixture) becomes a bounded failure
# instead of holding the whole ctest run hostage.  3600 s clears the
# slowest legitimate SDDP integration tests with margin.
doctest_discover_tests(${PROJECT_NAME} PROPERTIES TIMEOUT 3600)

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
