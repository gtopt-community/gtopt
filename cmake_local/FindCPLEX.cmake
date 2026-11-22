#[=======================================================================[.rst:
FindCPLEX
---------

Find the IBM ILOG CPLEX Optimizer.

Imported variables
^^^^^^^^^^^^^^^^^^

``CPLEX_FOUND``
  True if CPLEX was found.

``CPLEX_INCLUDE_DIRS``
  Include directories.

``CPLEX_LIBRARIES``
  Libraries to link against.

``CPLEX_VERSION``
  Version string extracted from cpxconst.h (e.g. "22.1.1").

Cache variables
^^^^^^^^^^^^^^^

``CPLEX_ROOT_DIR``
  CPLEX installation prefix (the directory holding ``include/ilcplex``).
  When empty, searched in ``$CPLEX_ROOT_DIR``, ``$CPLEX_HOME``,
  ``/opt/cplex``, ``~/opt/cplex`` and the newest ``CPLEX_Studio*`` under
  ``/opt/ibm/ILOG``, ``~/opt/ibm/ILOG`` and ``~/ibm/ILOG``.

#]=======================================================================]

include_guard(GLOBAL)

set(CPLEX_ROOT_DIR
    ""
    CACHE PATH "IBM ILOG CPLEX installation prefix (the dir holding include/ilcplex)"
)

# Resolve one root so headers and library always come from the same
# install.  An explicit CPLEX_ROOT_DIR (cache or environment) or
# $CPLEX_HOME wins; otherwise the NEWEST (CPX_VERSION in cpxconst.h) of
# the flat installs /opt/cplex and ~/opt/cplex (the `cplex` dir itself or
# a Studio tree with cplex/ below) and every CPLEX_Studio*/cplex under
# /opt/ibm/ILOG, ~/opt/ibm/ILOG and ~/ibm/ILOG -- the same rule as
# install.sh.
set(_cplex_root "${CPLEX_ROOT_DIR}")
if(NOT _cplex_root)
  foreach(_cplex_env IN ITEMS CPLEX_ROOT_DIR CPLEX_HOME)
    if(DEFINED ENV{${_cplex_env}}
       AND EXISTS "$ENV{${_cplex_env}}/include/ilcplex/cplex.h"
    )
      set(_cplex_root "$ENV{${_cplex_env}}")
      break()
    endif()
  endforeach()
endif()
if(NOT _cplex_root)
  set(_cplex_candidates /opt/cplex /opt/cplex/cplex)
  if(DEFINED ENV{HOME})
    list(APPEND _cplex_candidates "$ENV{HOME}/opt/cplex"
         "$ENV{HOME}/opt/cplex/cplex"
    )
  endif()
  file(GLOB _cplex_studios LIST_DIRECTORIES true
       /opt/ibm/ILOG/CPLEX_Studio*/cplex
       "$ENV{HOME}/opt/ibm/ILOG/CPLEX_Studio*/cplex"
       "$ENV{HOME}/ibm/ILOG/CPLEX_Studio*/cplex"
  )
  list(APPEND _cplex_candidates ${_cplex_studios})
  set(_cplex_best_ver -1)
  foreach(_cplex_dir IN LISTS _cplex_candidates)
    set(_cplex_hdr "${_cplex_dir}/include/ilcplex/cpxconst.h")
    if(NOT EXISTS "${_cplex_dir}/include/ilcplex/cplex.h")
      continue()
    endif()
    set(_cplex_ver 0)
    if(EXISTS "${_cplex_hdr}")
      file(STRINGS "${_cplex_hdr}" _cplex_line
           REGEX "#define[ \t]+CPX_VERSION[ \t]+[0-9]+"
      )
      if(_cplex_line MATCHES "CPX_VERSION[ \t]+([0-9]+)")
        set(_cplex_ver "${CMAKE_MATCH_1}")
      endif()
    endif()
    if(_cplex_ver GREATER _cplex_best_ver)
      set(_cplex_best_ver "${_cplex_ver}")
      set(_cplex_root "${_cplex_dir}")
    endif()
  endforeach()
endif()

find_path(
  CPLEX_INCLUDE_DIR ilcplex/cplex.h
  HINTS ${_cplex_root}/include
  PATHS ENV CPLEX_HOME ENV C_INCLUDE_PATH ENV C_PLUS_INCLUDE_PATH
)

# CPLEX library layout: lib/<arch>/static_pic/libcplex.a
# Detect the host architecture subdirectory automatically.
set(_cplex_arch_hints
    ${_cplex_root}/lib/x86-64_linux/static_pic
    ${_cplex_root}/lib/x86_linux/static_pic
    ${_cplex_root}/lib/arm64_linux/static_pic
    ${_cplex_root}/lib/aarch64_linux/static_pic
    ${_cplex_root}/lib/ppc64le_linux/static_pic
    ${_cplex_root}/lib  # fallback: flat layout or dynamic lib
)

find_library(
  CPLEX_LIBRARY
  NAMES cplex
  HINTS ${_cplex_arch_hints}
  PATHS ENV LIBRARY_PATH ENV LD_LIBRARY_PATH
)

# Extract version from CPX_VERSION macro (format VVRRMMFF).
if(CPLEX_INCLUDE_DIR)
  file(STRINGS "${CPLEX_INCLUDE_DIR}/ilcplex/cpxconst.h" _cpx_ver_line
       REGEX "#define[ \t]+CPX_VERSION[ \t]+"
  )
  if(_cpx_ver_line MATCHES "#define[ \t]+CPX_VERSION[ \t]+([0-9]+)")
    math(EXPR _cpx_major "${CMAKE_MATCH_1} / 1000000")
    math(EXPR _cpx_minor "(${CMAKE_MATCH_1} / 10000) % 100")
    math(EXPR _cpx_patch "(${CMAKE_MATCH_1} / 100) % 100")
    set(CPLEX_VERSION "${_cpx_major}.${_cpx_minor}.${_cpx_patch}")
  endif()
endif()

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(
  CPLEX
  REQUIRED_VARS CPLEX_LIBRARY CPLEX_INCLUDE_DIR
  VERSION_VAR CPLEX_VERSION
)

if(CPLEX_FOUND)
  set(CPLEX_INCLUDE_DIRS ${CPLEX_INCLUDE_DIR})
  set(CPLEX_LIBRARIES ${CPLEX_LIBRARY})
  if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    list(APPEND CPLEX_LIBRARIES m pthread)
  endif()
endif()

mark_as_advanced(CPLEX_INCLUDE_DIR CPLEX_LIBRARY)
