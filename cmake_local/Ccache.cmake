#[=======================================================================[.rst:
Ccache
------

Detects and enables `ccache <https://ccache.dev/>`_ as the C/C++ compiler
launcher for the current CMake project.

Usage::

  option(USE_CCACHE "Enable ccache compiler caching" YES)
  include(cmake_local/Ccache.cmake)

or simply::

  include(cmake_local/Ccache.cmake)   # USE_CCACHE defaults to YES

Behaviour
^^^^^^^^^

* If ``USE_CCACHE`` is ``NO`` (or ``OFF``) nothing happens.
* If ``USE_CCACHE`` is ``YES`` (the default) and ``ccache`` is found on
  ``PATH``, the variables ``CMAKE_C_COMPILER_LAUNCHER`` and
  ``CMAKE_CXX_COMPILER_LAUNCHER`` are set to the ccache executable — but
  **only when the caller has not already set them**.  This means an
  externally supplied launcher (e.g. ``-DCMAKE_CXX_COMPILER_LAUNCHER=ccache``
  on the CI command line) is always honoured as-is.
* If ccache is not found, a status message is printed and the build
  continues without a launcher.

The native ``CMAKE_<LANG>_COMPILER_LAUNCHER`` mechanism is used instead of
the ``TheLartians/Ccache.cmake`` CPM package so that:

* No internet download is required.
* The module works in offline / air-gapped environments.
* No CPM dependency is introduced when this file is included from
  ``all/CMakeLists.txt`` (which manages its own CPM bootstrap).

.. note::
   ``CMAKE_<LANG>_COMPILER_LAUNCHER`` is baked into the build system at
   **configure time**.  Install ccache **before** running ``cmake``.  If
   ccache was absent when you last configured, delete the build directory
   and reconfigure.

#]=======================================================================]

include_guard(GLOBAL)

option(USE_CCACHE "Enable ccache compiler caching" YES)

if(NOT USE_CCACHE)
  return()
endif()

find_program(CCACHE_PROGRAM ccache)

if(CCACHE_PROGRAM)
  if(NOT CMAKE_C_COMPILER_LAUNCHER)
    set(CMAKE_C_COMPILER_LAUNCHER
        "${CCACHE_PROGRAM}"
        CACHE STRING "C compiler launcher" FORCE
    )
  endif()
  if(NOT CMAKE_CXX_COMPILER_LAUNCHER)
    set(CMAKE_CXX_COMPILER_LAUNCHER
        "${CCACHE_PROGRAM}"
        CACHE STRING "CXX compiler launcher" FORCE
    )
  endif()
  # Configure ccache for PCH + coverage cacheability.  Without these
  # settings ccache marks compilations that use precompiled headers or
  # coverage instrumentation as "uncacheable".
  #   pch_defines, time_macros: ignore __DATE__/__TIME__ differences in
  #     PCH and source files — required for TUs using the project PCH
  #     (cmake_local/PrecompiledHeaders.cmake).
  #   gcno_cwd: don't hash the build cwd captured in the .gcno file —
  #     required for -ftest-coverage so coverage TUs cache across
  #     different build dirs.  NOTE: the legacy name gcno_file_location
  #     that lived in our CI env was silently ignored by ccache (unknown
  #     token); the correct modern token is gcno_cwd.
  # Written to ccache.conf so settings persist at build time.  The
  # CCACHE_SLOPPINESS env var would override this file if set, so CI
  # workflows must NOT export CCACHE_SLOPPINESS — this conf is the single
  # source of truth (see ubuntu/coverage/profile workflows).
  set(_ccache_conf_dir "$ENV{HOME}/.config/ccache")
  set(_ccache_conf "${_ccache_conf_dir}/ccache.conf")
  set(_ccache_marker "${_ccache_conf}.gtopt_marker_v3")
  if(NOT EXISTS "${_ccache_marker}")
    file(MAKE_DIRECTORY "${_ccache_conf_dir}")
    # Strip any stanza previously written by an earlier gtopt CMake so we
    # can bump sloppiness cleanly.  History of prior stanzas:
    #   v1: sloppiness = pch_defines,time_macros
    #   v2: sloppiness = gcno_file_location,pch_defines,time_macros  (wrong token)
    if(EXISTS "${_ccache_conf}")
      file(READ "${_ccache_conf}" _existing_conf)
      string(REGEX REPLACE
        "# Added by gtopt CMake[^\n]*\nsloppiness *=[^\n]*\npch_external_checksum *=[^\n]*\n"
        ""
        _cleaned_conf "${_existing_conf}")
      file(WRITE "${_ccache_conf}" "${_cleaned_conf}")
    endif()
    file(APPEND "${_ccache_conf}"
      "# Added by gtopt CMake for PCH + coverage cacheability\n"
      "sloppiness = gcno_cwd,pch_defines,time_macros\n"
      "pch_external_checksum = true\n"
    )
    foreach(_old_marker gtopt_marker gtopt_marker_v2)
      if(EXISTS "${_ccache_conf}.${_old_marker}")
        file(REMOVE "${_ccache_conf}.${_old_marker}")
      endif()
    endforeach()
    file(TOUCH "${_ccache_marker}")
    message(STATUS "ccache: wrote PCH + coverage sloppiness to ${_ccache_conf}")
  endif()
  # ---- Per-invocation ccache settings (ccache >= 4.8) ----
  # ccache >= 4.8 accepts `KEY=VALUE` config overrides between `ccache`
  # and the compiler, so the settings below travel with the build tree
  # instead of depending on each host's ~/.config/ccache/ccache.conf:
  #   base_dir   common ancestor of the source tree, the build tree and
  #              the CPM source cache.  ccache rewrites absolute paths
  #              under it to cwd-relative ones, so two build dirs at the
  #              same depth (two tools/mk_scratch_build.sh dirs, CI runs)
  #              share hits.  Without it every -I/-o/source path differs
  #              and a fresh build dir misses on every TU.
  #   hash_dir   false: do not hash the cwd (it only lands in the DWARF
  #              comp_dir of the -g1 CIFast / -g Debug objects).
  #   sloppiness the PCH/coverage tokens above plus include_file_mtime/
  #              ctime, so the generated headers (git_revision.hpp,
  #              *_builtin.hpp) do not drop their TUs out of direct mode.
  # Only applied when the launcher is plain ccache (ours, or the
  # `-DCMAKE_<LANG>_COMPILER_LAUNCHER=ccache` used by CI / CLAUDE.md); a
  # launcher that already carries arguments is left alone.  The result
  # is a normal (non-cache) variable: it shadows the cache entry for
  # this directory and every add_subdirectory() below it.
  # Disable with -DGTOPT_CCACHE_TUNING=OFF.
  option(GTOPT_CCACHE_TUNING
         "Pass base_dir/hash_dir/sloppiness overrides to ccache" ON)
  execute_process(
    COMMAND "${CCACHE_PROGRAM}" --version
    OUTPUT_VARIABLE _ccache_ver_out
    ERROR_QUIET
  )
  string(REGEX MATCH "version ([0-9]+\\.[0-9]+)" _ccache_ver_m
               "${_ccache_ver_out}")
  set(_ccache_ver "${CMAKE_MATCH_1}")
  if(GTOPT_CCACHE_TUNING AND _ccache_ver VERSION_GREATER_EQUAL 4.8)
    set(_cc_paths "${CMAKE_SOURCE_DIR}/.." "${CMAKE_BINARY_DIR}")
    if(CPM_SOURCE_CACHE AND IS_ABSOLUTE "${CPM_SOURCE_CACHE}")
      list(APPEND _cc_paths "${CPM_SOURCE_CACHE}")
    elseif(DEFINED ENV{CPM_SOURCE_CACHE})
      list(APPEND _cc_paths "$ENV{CPM_SOURCE_CACHE}")
    elseif(DEFINED ENV{HOME})
      list(APPEND _cc_paths "$ENV{HOME}/.cache/cpm")
    endif()
    set(_cc_base "")
    foreach(_p IN LISTS _cc_paths)
      cmake_path(ABSOLUTE_PATH _p NORMALIZE)
      string(REGEX REPLACE "/$" "" _p "${_p}")
      if(_cc_base STREQUAL "")
        set(_cc_base "${_p}")
      endif()
      string(FIND "${_p}/" "${_cc_base}/" _pos)
      while(NOT _pos EQUAL 0 AND NOT _cc_base STREQUAL "/"
            AND NOT _cc_base STREQUAL "")
        cmake_path(GET _cc_base PARENT_PATH _cc_base)
        string(FIND "${_p}/" "${_cc_base}/" _pos)
      endwhile()
    endforeach()
    set(_cc_args
        "hash_dir=false"
        "sloppiness=gcno_cwd,pch_defines,time_macros,include_file_mtime,include_file_ctime"
    )
    # base_dir=/ would also rewrite system header paths; skip it then.
    # An explicit CCACHE_BASEDIR in the environment (CI sets it to the
    # workspace) wins: command-line settings override the environment.
    if(DEFINED ENV{CCACHE_BASEDIR})
      set(_cc_base "")
    endif()
    if(NOT _cc_base STREQUAL "/" AND NOT _cc_base STREQUAL "")
      list(PREPEND _cc_args "base_dir=${_cc_base}")
    endif()
    foreach(_lang C CXX)
      list(LENGTH CMAKE_${_lang}_COMPILER_LAUNCHER _l_len)
      get_filename_component(_l_name "${CMAKE_${_lang}_COMPILER_LAUNCHER}"
                             NAME)
      if(_l_len EQUAL 1 AND _l_name STREQUAL "ccache")
        set(CMAKE_${_lang}_COMPILER_LAUNCHER
            "${CMAKE_${_lang}_COMPILER_LAUNCHER};${_cc_args}")
      endif()
    endforeach()
    message(STATUS "ccache ${_ccache_ver} overrides: ${_cc_args}")
  endif()
  message(STATUS "ccache enabled: ${CCACHE_PROGRAM}")
else()
  message(STATUS "USE_CCACHE=YES but ccache not found in PATH — building without cache")
endif()
