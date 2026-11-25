#[=======================================================================[.rst:
GtoptPlugin
-----------

Utility function to define a solver backend plugin with consistent
build settings, output location, RPATH, and install rules.

Usage::

  gtopt_add_solver_plugin(
    NAME       gtopt_solver_cplex
    SOURCES    cplex_solver_backend.cpp cplex_plugin.cpp
    INCLUDES   "${CPLEX_INCLUDE_DIRS}"
    LIBRARIES  ${CPLEX_LIBRARIES}
  )

All plugins are MODULE libraries placed in ``${CMAKE_BINARY_DIR}/plugins``
at build time and installed to ``lib/gtopt/plugins``.

#]=======================================================================]

include_guard(GLOBAL)
include(InstallRpath)

# GTOPT_SOLVER_LIB_FULL_HASH (option of the library project, CMakeLists.txt)
# makes _gtopt_plugin_linked_libraries record the SHA-256 of every library.

# Directory of this file, for functions run from other directories /
# deferred calls (CMAKE_CURRENT_FUNCTION_LIST_DIR is per function).
set(GTOPT_PLUGIN_CMAKE_DIR_GLOBAL "${CMAKE_CURRENT_LIST_DIR}"
    CACHE INTERNAL "cmake_local directory of GtoptPlugin.cmake")

#[=======================================================================[
Helper: verify that a library comes from the same directory as a
reference directory (ABI consistency).  Sets ``<out_var>`` to TRUE/FALSE.

Usage::

  gtopt_check_coin_abi(
    REFERENCE_DIR  "/opt/coinor/lib"
    LIBRARY        "${COIN_OSICBC_LIBRARY}"
    LABEL          "OsiCbc"
    RESULT         _cbc_abi_ok
  )

#]=======================================================================]
function(gtopt_check_coin_abi)
  cmake_parse_arguments(PARSE_ARGV 0 _A "" "REFERENCE_DIR;LIBRARY;LABEL;RESULT" "")

  set(${_A_RESULT} TRUE PARENT_SCOPE)
  if(NOT _A_REFERENCE_DIR OR NOT _A_LIBRARY)
    return()
  endif()
  get_filename_component(_ref "${_A_REFERENCE_DIR}" REALPATH)
  get_filename_component(_lib_dir "${_A_LIBRARY}" DIRECTORY)
  get_filename_component(_lib_dir "${_lib_dir}" REALPATH)
  if(NOT _lib_dir STREQUAL _ref)
    message(WARNING
      "COIN-OR ABI mismatch: ${_A_LABEL} at ${_A_LIBRARY} "
      "but core Osi/CoinUtils at ${_A_REFERENCE_DIR}. "
      "Skipping ${_A_LABEL}. Fix: install all COIN-OR from the same source."
    )
    set(${_A_RESULT} FALSE PARENT_SCOPE)
  endif()
endfunction()

function(gtopt_add_solver_plugin)
  cmake_parse_arguments(
    PARSE_ARGV 0 _P ""
    "NAME"
    "SOURCES;INCLUDES;LIBRARIES;DEFINITIONS"
  )

  if(NOT _P_NAME)
    message(FATAL_ERROR "gtopt_add_solver_plugin: NAME is required")
  endif()
  if(NOT _P_SOURCES)
    message(FATAL_ERROR "gtopt_add_solver_plugin(${_P_NAME}): SOURCES is required")
  endif()

  # Paths relative to this file (the include_guard makes a file-scope
  # variable visible only in the first includer's directory).
  set(GTOPT_PLUGIN_CMAKE_DIR "${CMAKE_CURRENT_FUNCTION_LIST_DIR}")
  get_filename_component(GTOPT_PLUGIN_COMMON_DIR
    "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../plugins/common" ABSOLUTE)

  # Every plugin also gets the shared `gtopt_plugin_build_tag` entry point
  # (identity check against the host, see SolverRegistry).
  # ... and `gtopt_plugin_linked_libraries` (identity of the solver
  # libraries it links, generated below by _gtopt_plugin_linked_libraries).
  add_library(${_P_NAME} MODULE ${_P_SOURCES}
    "${GTOPT_PLUGIN_COMMON_DIR}/plugin_build_tag.cpp"
    "${GTOPT_PLUGIN_COMMON_DIR}/plugin_linked_libraries.cpp"
  )
  set_source_files_properties(
    "${GTOPT_PLUGIN_COMMON_DIR}/plugin_build_tag.cpp"
    "${GTOPT_PLUGIN_COMMON_DIR}/plugin_linked_libraries.cpp"
    TARGET_DIRECTORY ${_P_NAME}
    PROPERTIES SKIP_UNITY_BUILD_INCLUSION ON
  )
  # The table is generated once the directory has added every library the
  # plugin links (the osi plugin adds Clp/Cbc after this call).
  set(_inc_dir "${CMAKE_CURRENT_BINARY_DIR}/${_P_NAME}_linked_libraries")
  target_include_directories(${_P_NAME} PRIVATE "${_inc_dir}")
  # (DEFER evaluates variable references when the call runs: bake the
  # values in now.)
  cmake_language(EVAL CODE
    "cmake_language(DEFER CALL _gtopt_plugin_linked_libraries [[${_P_NAME}]] [[${_inc_dir}]])")

  # Private include path for plugin-local headers.
  target_include_directories(${_P_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
  )

  # External solver headers as SYSTEM to suppress warnings.
  if(_P_INCLUDES)
    target_include_directories(${_P_NAME} SYSTEM PRIVATE
      ${_P_INCLUDES}
    )
  endif()

  # Link gtopt core + solver libraries.
  target_link_libraries(${_P_NAME} PRIVATE
    gtopt::gtopt
    ${_P_LIBRARIES}
  )

  # Coverage builds: the plugin embeds instrumented gtopt objects, so it
  # must link the gcov runtime itself (otherwise dlopen fails with an
  # undefined __gcov_merge_add and every solver test loses its plugins).
  if(ENABLE_TEST_COVERAGE AND NOT MSVC)
    target_link_options(${_P_NAME} PRIVATE --coverage)
  endif()

  # Optional compile definitions.
  if(_P_DEFINITIONS)
    target_compile_definitions(${_P_NAME} PRIVATE ${_P_DEFINITIONS})
  endif()

  # Consistent output directory and naming across all plugins.
  set_target_properties(${_P_NAME} PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    PREFIX "lib"
  )

  # Always emit an ELF GNU build-id note: SolverRegistry identifies plugins
  # by it (most toolchains add it by default; make it explicit for lld).
  if(NOT APPLE AND NOT WIN32)
    target_link_options(${_P_NAME} PRIVATE "LINKER:--build-id=sha1")
  endif()

  # Preserve RPATH to non-system solver libraries.
  configure_install_rpath(${_P_NAME})

  # Install to the standard plugin directory (not install(TARGETS): see
  # gtopt_install_with_rpath() in InstallRpath.cmake).
  gtopt_install_with_rpath(${_P_NAME} lib/gtopt/plugins)

  # Build-id manifest: `<plugin>.so.build-id` next to the plugin, written
  # after every link from the ELF note (readelf -n).  One sidecar per plugin
  # (not one shared file) so parallel plugin links never race on it.
  # SolverRegistry compares the plugin's build-id with it BEFORE dlopen and
  # refuses a mismatch (a .so swapped or copied without its manifest).
  # Installed with the plugin: install-time RPATH_SET / strip keep the note.
  if(CMAKE_READELF AND NOT APPLE AND NOT WIN32)
    add_custom_command(TARGET ${_P_NAME} POST_BUILD
      COMMAND ${CMAKE_COMMAND} "-DREADELF=${CMAKE_READELF}"
              "-DFILE=$<TARGET_FILE:${_P_NAME}>"
              -P "${GTOPT_PLUGIN_CMAKE_DIR}/PluginBuildIdManifest.cmake"
      COMMENT "Writing build-id manifest for ${_P_NAME}"
      VERBATIM
    )
    install(FILES "$<TARGET_FILE:${_P_NAME}>.build-id"
            DESTINATION lib/gtopt/plugins)
  elseif(NOT APPLE AND NOT WIN32
         AND GTOPT_PLUGIN_CHECK_DEFAULT STREQUAL "error")
    # No manifest = an unidentified plugin, which the default plugin check
    # mode refuses: say so now rather than at the first run.
    message(WARNING
      "${_P_NAME}: no readelf, so no build-id manifest; with "
      "GTOPT_PLUGIN_CHECK_DEFAULT=error gtopt refuses this plugin.  "
      "Install binutils, or configure with "
      "-DGTOPT_PLUGIN_CHECK_DEFAULT=warning.")
  else()
    message(STATUS "${_P_NAME}: no readelf, plugin build-id manifest skipped")
  endif()
endfunction()

#[=======================================================================[
_gtopt_plugin_linked_libraries(<target> <inc_dir>)  (internal, deferred)

Generate ``<inc_dir>/gtopt_plugin_linked_libraries.inc`` -- soname, GNU
build-id (else SHA-256) and real path of every shared library ``<target>``
links -- with PluginLinkedLibraries.cmake, as a build step that reruns when
one of those libraries changes.  Imported targets are resolved to their
files (INTERFACE targets one level through INTERFACE_LINK_LIBRARIES).
#]=======================================================================]
function(_gtopt_plugin_linked_libraries _target _inc_dir)
  get_target_property(_links ${_target} LINK_LIBRARIES)
  set(_files "")
  foreach(_l IN LISTS _links)
    set(_candidates "${_l}")
    if(TARGET "${_l}")
      get_target_property(_type "${_l}" TYPE)
      get_target_property(_imported "${_l}" IMPORTED)
      if(_type STREQUAL "INTERFACE_LIBRARY")
        get_target_property(_candidates "${_l}" INTERFACE_LINK_LIBRARIES)
        if(NOT _candidates)
          set(_candidates "")
        endif()
      elseif(_imported AND _type MATCHES "SHARED_LIBRARY|UNKNOWN_LIBRARY")
        set(_candidates "$<TARGET_FILE:${_l}>")
      else()
        set(_candidates "")  # gtopt::gtopt, static archives, ...
      endif()
    endif()
    foreach(_c IN LISTS _candidates)
      string(FIND "${_c}" "$<TARGET_FILE:" _genex_pos)
      if(_genex_pos EQUAL 0 OR (IS_ABSOLUTE "${_c}" AND EXISTS "${_c}"))
        list(APPEND _files "${_c}")
      elseif(TARGET "${_c}")
        get_target_property(_t2 "${_c}" TYPE)
        get_target_property(_i2 "${_c}" IMPORTED)
        if(_i2 AND _t2 MATCHES "SHARED_LIBRARY|UNKNOWN_LIBRARY")
          list(APPEND _files "$<TARGET_FILE:${_c}>")
        endif()
      endif()
    endforeach()
  endforeach()
  list(REMOVE_DUPLICATES _files)
  list(JOIN _files "|" _joined)

  set(_out "${_inc_dir}/gtopt_plugin_linked_libraries.inc")
  get_filename_component(_script
    "${GTOPT_PLUGIN_CMAKE_DIR_GLOBAL}/PluginLinkedLibraries.cmake" ABSOLUTE)
  set(_readelf "")
  if(CMAKE_READELF AND NOT APPLE AND NOT WIN32)
    set(_readelf "${CMAKE_READELF}")
  endif()
  file(MAKE_DIRECTORY "${_inc_dir}")
  add_custom_command(
    OUTPUT "${_out}"
    COMMAND ${CMAKE_COMMAND} "-DREADELF=${_readelf}" "-DLIBS=${_joined}"
            "-DOUT=${_out}" "-DFULL_HASH=${GTOPT_SOLVER_LIB_FULL_HASH}"
            -P "${_script}"
    DEPENDS ${_files} "${_script}"
    COMMENT "Recording the solver libraries linked by ${_target}"
    VERBATIM
  )
  target_sources(${_target} PRIVATE "${_out}")
endfunction()

#[=======================================================================[
gtopt_require_solver_plugins(<target>)

Add build dependencies from ``<target>`` to all available solver plugin
targets and create a symlink so ``<exe_dir>/plugins`` always points to
the freshly-built plugins in ``${CMAKE_BINARY_DIR}/plugins``.

This ensures:
1. Plugins are rebuilt before the target runs (ABI safety).
2. The binary always loads the development plugins, not stale copies.

Safe to call even if some plugin targets don't exist.

Usage::

  gtopt_require_solver_plugins(gtoptTests)

#]=======================================================================]
function(gtopt_require_solver_plugins _target)
  foreach(_plugin
      gtopt_solver_osi gtopt_solver_cplex gtopt_solver_highs
      gtopt_solver_mindopt gtopt_solver_gurobi gtopt_solver_scip
      gtopt_solver_cuopt)
    if(TARGET ${_plugin})
      add_dependencies(${_target} ${_plugin})
    endif()
  endforeach()

  # Symlink <exe_dir>/plugins → <build_root>/plugins so the binary
  # always finds freshly-built plugins regardless of build layout.
  # Only possible when the target was defined in this directory scope
  # (CMake requires add_custom_command(TARGET ...) to be in the same dir).
  get_target_property(_target_dir ${_target} SOURCE_DIR)
  if(_target_dir STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    set(_plugin_src "${CMAKE_BINARY_DIR}/plugins")
    get_target_property(_exe_dir ${_target} RUNTIME_OUTPUT_DIRECTORY)
    if(NOT _exe_dir)
      set(_exe_dir "${CMAKE_CURRENT_BINARY_DIR}")
    endif()
    set(_plugin_link "${_exe_dir}/plugins")
    if(NOT _plugin_src STREQUAL _plugin_link)
      add_custom_command(TARGET ${_target} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E rm -rf "${_plugin_link}"
        COMMAND ${CMAKE_COMMAND} -E create_symlink
                "${_plugin_src}" "${_plugin_link}"
        COMMENT "Symlinking plugins: ${_plugin_link} -> ${_plugin_src}"
        VERBATIM
      )
    endif()
  endif()
endfunction()
