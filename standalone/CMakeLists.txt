cmake_minimum_required(VERSION 3.31.6)

project(gtoptStandalone LANGUAGES CXX)

# ---- Shared cmake module path ----
list(APPEND CMAKE_MODULE_PATH
     "${CMAKE_CURRENT_LIST_DIR}/../cmake_local"
     "${CMAKE_CURRENT_LIST_DIR}/../cmake"
)

# ---- Build defaults ----
include(DefaultInstallPrefix)
include(BuildTypeDefault)
include(CxxStandard)
include(FastLinker)
include(Ccache)

# ---- Options ----
option(ENABLE_TEST_COVERAGE "Enable coverage instrumentation for the gtopt binary" OFF)

# ---- Compiler checks and tools ----
include(CompilerCheck)
include("${CMAKE_CURRENT_LIST_DIR}/../cmake/tools.cmake")
include(Hardening)

# ---- Dependencies ----
option(CPM_USE_LOCAL_PACKAGES "Try find_package first" ON)
include("${CMAKE_CURRENT_LIST_DIR}/../cmake/CPM.cmake")

if(NOT TARGET gtopt::gtopt)
  CPMAddPackage(NAME gtopt SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/..")
endif()

include(ClangFormat)

# ---- Executable ----
file(GLOB sources CONFIGURE_DEPENDS
     "${CMAKE_CURRENT_SOURCE_DIR}/source/[a-zA-Z]*.cpp"
)

add_executable(${PROJECT_NAME} ${sources})
set_target_properties(${PROJECT_NAME} PROPERTIES OUTPUT_NAME "gtopt")
# NOTE: the install RPATH (incl. `$ORIGIN/../lib` so bin/gtopt resolves the
# shipped libspdlog.so.1.15 in <prefix>/lib, plus the non-system Arrow/Parquet
# … directories) is set by configure_install_rpath() below — do NOT set
# INSTALL_RPATH here, it would be overwritten.

target_link_libraries(
  ${PROJECT_NAME}
  gtopt::gtopt
)

# ---- jemalloc (optional) ----
# Replace glibc malloc with jemalloc on multi-threaded SDDP runs.  IMPORTANT:
# simply *linking* jemalloc is not enough — with its stock defaults
# (background_thread:false, dirty_decay_ms:10000) peak RSS stays at glibc-like
# levels.  The win comes from `background_thread:true`, which
# main.cpp bakes into the binary via the `malloc_conf` symbol (guarded by the
# GTOPT_HAVE_JEMALLOC define set below): a dedicated purge thread returns pages
# to the OS off the hot path, substantially lowering peak RSS on large SDDP
# runs.  Narenas/percpu/decay tuning added
# nothing.  Override at runtime with the MALLOC_CONF env var.
# Opt-in via -DGTOPT_USE_JEMALLOC=ON (default ON when jemalloc is found).
option(GTOPT_USE_JEMALLOC "Link jemalloc as the malloc replacement" ON)
if(GTOPT_USE_JEMALLOC)
  # Prefer the `-dev` package (libjemalloc.so symlink).  Fall back to the
  # runtime SONAME `libjemalloc.so.2` so that systems which only ship the
  # runtime (e.g. distroless containers, or boxes where libjemalloc-dev
  # was not installed) still get the benefit.
  find_library(JEMALLOC_LIB NAMES jemalloc jemalloc.so.2 libjemalloc.so.2)
  if(JEMALLOC_LIB)
    target_link_libraries(${PROJECT_NAME} ${JEMALLOC_LIB})
    # Enables the `malloc_conf = "background_thread:true"` symbol in main.cpp.
    target_compile_definitions(${PROJECT_NAME} PRIVATE GTOPT_HAVE_JEMALLOC=1)
    message(STATUS "gtopt: linking jemalloc: ${JEMALLOC_LIB} "
                   "(malloc_conf=background_thread:true)")
  else()
    message(WARNING
      "gtopt: jemalloc not found (install libjemalloc-dev); "
      "falling back to glibc malloc — expect ~2x higher peak RSS on "
      "multi-threaded SDDP runs due to per-thread arena fragmentation")
  endif()
endif()

# ---- Coverage ----
include(Coverage)
if(ENABLE_TEST_COVERAGE)
  target_enable_coverage(${PROJECT_NAME})
endif()

# ---- RPATH for installed binary ----
include(InstallRpath)
configure_install_rpath(${PROJECT_NAME} gtopt)

# ---- Install ----
# Not install(TARGETS): see gtopt_install_with_rpath() in InstallRpath.cmake.
gtopt_install_with_rpath(${PROJECT_NAME} bin)

# ---- ELF security check (ctest) ----
# Inspects the built binary and solver plugins with readelf: RUNPATH (no
# empty entry, no $ORIGIN/../.. on the executable), PIE, RELRO,
# BIND_NOW, non-executable stack, stack protector and (optimised builds)
# FORTIFY.  See cmake_local/CheckElfSecurity.cmake.
if(CMAKE_READELF AND NOT APPLE AND NOT WIN32)
  set(_elf_plugins "")
  foreach(plugin IN ITEMS osi cplex highs mindopt gurobi scip cuopt)
    if(TARGET gtopt_solver_${plugin})
      list(APPEND _elf_plugins "$<TARGET_FILE:gtopt_solver_${plugin}>")
    endif()
  endforeach()
  list(JOIN _elf_plugins "|" _elf_plugins)
  set(_elf_optimized "$<CONFIG:Release,RelWithDebInfo,MinSizeRel>")
  if(USE_SANITIZER MATCHES "Address|Memory")
    set(_elf_optimized 0)
  endif()
  add_test(
    NAME gtopt_elf_security
    COMMAND
      ${CMAKE_COMMAND} "-DREADELF=${CMAKE_READELF}"
      "-DEXE=$<TARGET_FILE:${PROJECT_NAME}>" "-DPLUGINS=${_elf_plugins}"
      "-DHARDENING=${GTOPT_HARDENING}" "-DOPTIMIZED=${_elf_optimized}" -P
      "${CMAKE_CURRENT_LIST_DIR}/../cmake_local/CheckElfSecurity.cmake")
endif()

# ---- Plugin build dependency ----
# Ensure solver plugins are built alongside the standalone binary so that
# the binary can discover them at runtime (in the build tree or after install).
foreach(plugin IN ITEMS osi cplex highs)
  if(TARGET gtopt_solver_${plugin})
    add_dependencies(${PROJECT_NAME} gtopt_solver_${plugin})
  endif()
endforeach()
