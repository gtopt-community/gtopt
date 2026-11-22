#[=======================================================================[.rst:
FindCuOpt
---------

Find the NVIDIA cuOpt GPU optimization solver (its stable C API).

cuOpt ships its native library inside pip wheels.  gtopt's plugin uses
**only the stable C API** declared in
``cuopt/mathematical_optimization/cuopt_c.h`` — no CUDA toolkit headers, no
RAFT/RMM C++ templates — so a plain ``g++`` plugin links the C API library
directly (``cuOptCreateRangedProblem`` / ``cuOptSolve`` /
``cuOptGetDualSolution`` / ``cuOptGetReducedCosts`` ...).

Two wheel layouts are supported:

* **cuOpt <= 26.08** — everything in the ``libcuopt-cu12/-cu13`` wheel::

    site-packages/libcuopt/include/cuopt/mathematical_optimization/cuopt_c.h
    site-packages/libcuopt/lib64/libcuopt.so            (ELF, C API)

* **cuOpt >= 26.10** — the library was split into component wheels; the
  LP/MIP solver and its C API live in ``libcuopt_mathopt-cu12/-cu13``, and
  the headers ``cuopt_c.h`` includes (``constants.h``, ``export.hpp``,
  ``status_codes.h``) in ``libcuopt_client``::

    site-packages/libcuopt_mathopt/include/cuopt/mathematical_optimization/cuopt_c.h
    site-packages/libcuopt_mathopt/lib64/libcuopt_mathopt.so   (ELF, C API)
    site-packages/libcuopt_client/include/cuopt/mathematical_optimization/constants.h
    site-packages/libcuopt_client/include/cuopt/export.hpp
    site-packages/libcuopt/lib64/libcuopt.so    (GNU ld script, compat shim)

  ``libcuopt_mathopt.so`` is preferred over ``libcuopt.so`` (the latter is
  a text linker script that drags in routing + client and cannot be
  dlopen'ed).  Its own RPATH resolves libcuopt_client / RMM / CUDA libs.

Search order for the include dirs and library (each root is also probed
for sibling ``libcuopt_mathopt`` / ``libcuopt_client`` wheel directories,
so pointing ``CUOPT_HOME`` at a >= 26.10 ``libcuopt`` directory works):

1. ``CUOPT_HOME`` environment variable      (root containing include/ + lib64/)
2. ``CUOPT_ROOT_DIR`` CMake variable
3. ``$ENV{CUOPT_ROOT_DIR}``
4. The pip wheel locations, auto-detected via ``import libcuopt_mathopt``
   and ``import libcuopt`` with ``-D_CUOPT_PYTHON=<python>`` if given, else
   the first of python3, python3.14 ... python3.10, python on PATH that
   imports one of them
5. Common install prefixes (/opt/cuopt, /usr/local).

Result variables::

  ``CUOPT_FOUND``        — TRUE if cuOpt was found
  ``CUOPT_INCLUDE_DIRS`` — Header include dirs (cuopt_c.h + constants.h)
  ``CUOPT_LIBRARIES``    — Library to link (libcuopt_mathopt.so or libcuopt.so)
  ``CUOPT_VERSION``      — Numeric X.Y.Z version when discoverable
  ``CUOPT_VERSION_FULL`` — Raw VERSION file string (keeps nightly suffixes)
  ``CUOPT_DIAGNOSTIC``   — When not found: why (for the caller's message)

``find_package(CuOpt <min>)`` version requirements are honoured: the wheel's
VERSION file is the source (nightly wheels carry an alpha suffix such as
``26.08.00a98`` that CMake's comparator cannot parse, so the numeric prefix
feeds the version check and the raw string is kept for display).

Disable explicitly with ``-DGTOPT_DISABLE_CUOPT=ON``.

#]=======================================================================]

if(GTOPT_DISABLE_CUOPT)
  set(CUOPT_FOUND FALSE)
  return()
endif()

set(_CUOPT_ROOTS)

if(DEFINED ENV{CUOPT_HOME})
  list(APPEND _CUOPT_ROOTS "$ENV{CUOPT_HOME}")
endif()
if(CUOPT_ROOT_DIR)
  list(APPEND _CUOPT_ROOTS "${CUOPT_ROOT_DIR}")
endif()
if(DEFINED ENV{CUOPT_ROOT_DIR})
  list(APPEND _CUOPT_ROOTS "$ENV{CUOPT_ROOT_DIR}")
endif()

# Auto-detect the pip wheel install locations.  ``libcuopt_mathopt``
# (>= 26.10) and ``libcuopt`` are packages whose __file__ sits next to
# include/ and lib64/.  The import of the package only runs its
# __init__ (no CUDA library is loaded).
# A `pip install --user` lands in one interpreter's site-packages (e.g.
# ~/.local/lib/python3.12) that the default python3 may not see, so when
# _CUOPT_PYTHON is not given, try the versioned interpreters on PATH too.
if(_CUOPT_PYTHON)
  set(_CUOPT_PYTHONS "${_CUOPT_PYTHON}")
else()
  set(_CUOPT_PYTHONS)
  foreach(_py IN ITEMS python3 python3.14 python3.13 python3.12 python3.11
                       python3.10 python)
    find_program(_CUOPT_PY_${_py} NAMES ${_py})
    mark_as_advanced(_CUOPT_PY_${_py})
    if(_CUOPT_PY_${_py})
      list(APPEND _CUOPT_PYTHONS "${_CUOPT_PY_${_py}}")
    endif()
  endforeach()
endif()
set(_CUOPT_WHEEL_DIRS)
foreach(_py IN LISTS _CUOPT_PYTHONS)
  foreach(_mod IN ITEMS libcuopt_mathopt libcuopt)
    execute_process(
      COMMAND "${_py}" -c
              "import ${_mod}, os; print(os.path.dirname(${_mod}.__file__))"
      OUTPUT_VARIABLE _CUOPT_WHEEL_DIR
      OUTPUT_STRIP_TRAILING_WHITESPACE
      ERROR_QUIET
      RESULT_VARIABLE _CUOPT_PY_RC
    )
    if(_CUOPT_PY_RC EQUAL 0 AND _CUOPT_WHEEL_DIR)
      list(APPEND _CUOPT_ROOTS "${_CUOPT_WHEEL_DIR}")
      list(APPEND _CUOPT_WHEEL_DIRS "${_CUOPT_WHEEL_DIR}")
    endif()
  endforeach()
  if(_CUOPT_WHEEL_DIRS)
    break()
  endif()
endforeach()

list(APPEND _CUOPT_ROOTS "/opt/cuopt" "/usr/local")

# Every root also contributes sibling libcuopt_mathopt/ and
# libcuopt_client/ dirs (>= 26.10 split wheels), so
# CUOPT_HOME=<site-packages>/libcuopt keeps working.  The mathopt sibling
# goes first: it holds cuopt_c.h and the real ELF.
set(_CUOPT_SEARCH_PATHS)
foreach(_root IN LISTS _CUOPT_ROOTS)
  get_filename_component(_parent "${_root}" DIRECTORY)
  if(IS_DIRECTORY "${_parent}/libcuopt_mathopt")
    list(APPEND _CUOPT_SEARCH_PATHS "${_parent}/libcuopt_mathopt")
  endif()
  list(APPEND _CUOPT_SEARCH_PATHS "${_root}")
  if(IS_DIRECTORY "${_parent}/libcuopt_client")
    list(APPEND _CUOPT_SEARCH_PATHS "${_parent}/libcuopt_client")
  endif()
endforeach()
list(REMOVE_DUPLICATES _CUOPT_SEARCH_PATHS)

find_path(CUOPT_INCLUDE_DIR
  NAMES cuopt/mathematical_optimization/cuopt_c.h
  HINTS ${_CUOPT_SEARCH_PATHS}
  PATH_SUFFIXES include
)

# cuopt_c.h includes <cuopt/mathematical_optimization/constants.h> and
# <cuopt/export.hpp>: next to it up to 26.08, in libcuopt_client from 26.10.
set(_CUOPT_CONSTANTS_HINTS ${_CUOPT_SEARCH_PATHS})
if(CUOPT_INCLUDE_DIR)
  list(PREPEND _CUOPT_CONSTANTS_HINTS "${CUOPT_INCLUDE_DIR}")
endif()
find_path(CUOPT_CONSTANTS_INCLUDE_DIR
  NAMES cuopt/mathematical_optimization/constants.h
  HINTS ${_CUOPT_CONSTANTS_HINTS}
  PATH_SUFFIXES include
)

# Prefer the component library (>= 26.10) over the legacy name.  Without
# NAMES_PER_DIR, CMake tries each name across all directories in turn, so
# libcuopt_mathopt.so wins over the >= 26.10 libcuopt.so linker script.
find_library(CUOPT_LIBRARY
  NAMES cuopt_mathopt cuopt
  HINTS ${_CUOPT_SEARCH_PATHS}
  PATH_SUFFIXES lib64 lib
)

# Best-effort version string from the wheel's VERSION file, read next to
# the header that was found (falls back to the detected wheel dirs).
# Nightly wheels read e.g. "26.08.00a98" — CMake version comparison needs
# the numeric X.Y.Z prefix, so strip the pre-release suffix for
# CUOPT_VERSION and keep the raw string in CUOPT_VERSION_FULL for display.
set(CUOPT_VERSION "")
set(CUOPT_VERSION_FULL "")
set(_CUOPT_VERSION_DIRS ${_CUOPT_WHEEL_DIRS})
if(CUOPT_INCLUDE_DIR)
  get_filename_component(_inc_root "${CUOPT_INCLUDE_DIR}" DIRECTORY)
  list(PREPEND _CUOPT_VERSION_DIRS "${_inc_root}")
endif()
foreach(_dir IN LISTS _CUOPT_VERSION_DIRS)
  if(EXISTS "${_dir}/VERSION")
    file(READ "${_dir}/VERSION" CUOPT_VERSION_FULL)
    string(STRIP "${CUOPT_VERSION_FULL}" CUOPT_VERSION_FULL)
    string(REGEX MATCH "^[0-9]+\\.[0-9]+(\\.[0-9]+)?" CUOPT_VERSION
           "${CUOPT_VERSION_FULL}")
    break()
  endif()
endforeach()

# Explain a miss: the classic trap is a >= 26.10 libcuopt wheel without
# the libcuopt_mathopt wheel (headers + C API), or vice versa.
set(CUOPT_DIAGNOSTIC "")
if(NOT CUOPT_INCLUDE_DIR OR NOT CUOPT_LIBRARY OR NOT CUOPT_CONSTANTS_INCLUDE_DIR)
  if(NOT CUOPT_INCLUDE_DIR AND NOT CUOPT_LIBRARY)
    set(CUOPT_DIAGNOSTIC "no cuopt_c.h header and no libcuopt_mathopt/libcuopt library")
  elseif(NOT CUOPT_INCLUDE_DIR)
    set(CUOPT_DIAGNOSTIC "library ${CUOPT_LIBRARY} found but no cuopt/mathematical_optimization/cuopt_c.h (cuOpt >= 26.10 needs the libcuopt_mathopt-cu12/-cu13 wheel)")
  elseif(NOT CUOPT_LIBRARY)
    set(CUOPT_DIAGNOSTIC "header in ${CUOPT_INCLUDE_DIR} found but no libcuopt_mathopt.so / libcuopt.so")
  else()
    set(CUOPT_DIAGNOSTIC "cuopt_c.h found but not cuopt/mathematical_optimization/constants.h (cuOpt >= 26.10 needs the libcuopt_client wheel)")
  endif()
  if(_CUOPT_WHEEL_DIRS)
    string(APPEND CUOPT_DIAGNOSTIC "; wheel dirs probed: ${_CUOPT_WHEEL_DIRS}")
  elseif(_CUOPT_PYTHONS)
    string(APPEND CUOPT_DIAGNOSTIC "; no python imports libcuopt_mathopt or libcuopt (tried ${_CUOPT_PYTHONS})")
  endif()
endif()

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(CuOpt
  REQUIRED_VARS CUOPT_LIBRARY CUOPT_INCLUDE_DIR CUOPT_CONSTANTS_INCLUDE_DIR
  VERSION_VAR   CUOPT_VERSION
)

if(CUOPT_FOUND)
  set(CUOPT_INCLUDE_DIRS "${CUOPT_INCLUDE_DIR}" "${CUOPT_CONSTANTS_INCLUDE_DIR}")
  list(REMOVE_DUPLICATES CUOPT_INCLUDE_DIRS)
  set(CUOPT_LIBRARIES    "${CUOPT_LIBRARY}")
  mark_as_advanced(CUOPT_INCLUDE_DIR CUOPT_CONSTANTS_INCLUDE_DIR CUOPT_LIBRARY)
endif()
