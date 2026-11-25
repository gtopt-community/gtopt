#[=======================================================================[.rst:
FindHiGHS
---------

Find the HiGHS LP/MIP solver.

Imported variables
^^^^^^^^^^^^^^^^^^

``HIGHS_FOUND``
  True if HiGHS was found.

``HIGHS_INCLUDE_DIRS``
  Include directories.

``HIGHS_LIBRARIES``
  Libraries to link against.

Cache variables
^^^^^^^^^^^^^^^

``HIGHS_ROOT_DIR``
  Hint for the HiGHS installation prefix.  Defaults to ``$ENV{HIGHS_ROOT_DIR}``,
  else ``$HOME/opt/highs-<GTOPT_HIGHS_PIN_VERSION>`` when it exists, else
  ``/opt/highs``.

``GTOPT_HIGHS_PIN_VERSION``
  The HiGHS release the goldens are generated with (CI builds exactly this
  one, ``.github/actions/install-highs``).  ``plugins/CMakeLists.txt``
  warns when another version is found: HiGHS may return another optimal
  vertex, so dual and cost goldens of degenerate cases can differ.

#]=======================================================================]

include_guard(GLOBAL)

set(GTOPT_HIGHS_PIN_VERSION
    "1.16.0"
    CACHE STRING "HiGHS release the goldens are generated with"
)

if(DEFINED ENV{HIGHS_ROOT_DIR})
  set(_highs_root_default "$ENV{HIGHS_ROOT_DIR}")
elseif(EXISTS "$ENV{HOME}/opt/highs-${GTOPT_HIGHS_PIN_VERSION}/include")
  set(_highs_root_default "$ENV{HOME}/opt/highs-${GTOPT_HIGHS_PIN_VERSION}")
else()
  set(_highs_root_default "/opt/highs")
endif()

set(HIGHS_ROOT_DIR
    "${_highs_root_default}"
    CACHE PATH "HiGHS installation prefix"
)

find_path(
  HIGHS_INCLUDE_DIR Highs.h
  HINTS ${HIGHS_ROOT_DIR}/include/highs ${HIGHS_ROOT_DIR}/include
  PATH_SUFFIXES highs
  PATHS ENV C_INCLUDE_PATH ENV C_PLUS_INCLUDE_PATH ENV INCLUDE_PATH
        /usr/local/include/highs /usr/include/highs
)

find_library(
  HIGHS_LIBRARY
  NAMES highs
  HINTS ${HIGHS_ROOT_DIR}/lib ${HIGHS_ROOT_DIR}/lib64
  PATHS ENV LIBRARY_PATH ENV LD_LIBRARY_PATH
        /usr/local/lib /usr/lib
)

if(HIGHS_INCLUDE_DIR AND EXISTS "${HIGHS_INCLUDE_DIR}/HConfig.h")
  file(STRINGS "${HIGHS_INCLUDE_DIR}/HConfig.h" _highs_ver
       REGEX "#define HIGHS_VERSION_(MAJOR|MINOR|PATCH) ")
  foreach(_part MAJOR MINOR PATCH)
    string(REGEX REPLACE ".*HIGHS_VERSION_${_part} ([0-9]+).*" "\\1"
           _highs_${_part} "${_highs_ver}")
  endforeach()
  set(HIGHS_VERSION "${_highs_MAJOR}.${_highs_MINOR}.${_highs_PATCH}")
endif()

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(
  HiGHS
  REQUIRED_VARS HIGHS_LIBRARY HIGHS_INCLUDE_DIR
  VERSION_VAR HIGHS_VERSION
)

if(HIGHS_FOUND)
  set(HIGHS_INCLUDE_DIRS ${HIGHS_INCLUDE_DIR})
  set(HIGHS_LIBRARIES ${HIGHS_LIBRARY})
endif()

mark_as_advanced(HIGHS_INCLUDE_DIR HIGHS_LIBRARY)
