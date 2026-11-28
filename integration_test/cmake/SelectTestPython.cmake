#[=======================================================================[.rst:
SelectTestPython
----------------

Pick the Python interpreter that drives the integration tests' Python
validators (``e2e_*_sddp_validate``, ``e2e_*_validate_solution``,
``numstab_*``, conditioning / expected-values checks).  They import
``pandas`` / ``pyarrow`` / ``numpy`` (the ``scripts[dev]`` deps), so a
bare system ``python3`` without them makes every such test fail with
``ModuleNotFoundError`` -- the interpreter must be chosen on what it can
import, not on which ``python3`` comes first on ``PATH``.

``gtopt_select_test_python()`` sets ``Python3_EXECUTABLE`` (cache, so
every later ``find_package(Python3)`` agrees) to the first candidate
that imports the test deps:

1. ``GTOPT_TEST_PYTHON`` (cache variable, or the environment variable of
   the same name at configure time) -- an explicit choice; a missing
   dependency there is a configure error.
2. ``Python3_EXECUTABLE`` already in the cache (``-DPython3_EXECUTABLE=``
   or a previous configure).
3. ``$VIRTUAL_ENV/bin/python`` (an activated venv).
4. ``<repo>/.venv/bin/python`` and ``<repo>/scripts/.venv/bin/python``.
5. ``python3`` / ``python`` on ``PATH``.

When no candidate has the deps the configure prints a WARNING naming the
fix and keeps the ``PATH`` interpreter: the validator tests then FAIL
(never skip) with the import error, as a real check must.
#]=======================================================================]

include_guard(GLOBAL)

set(GTOPT_TEST_PYTHON "$ENV{GTOPT_TEST_PYTHON}"
    CACHE FILEPATH
    "Python for the integration tests' validators (needs pandas, pyarrow, numpy); empty = auto")

# CACHE INTERNAL, not a normal variable: include_guard(GLOBAL) skips the
# body on a second include() from another directory (scripts/), where a
# directory-scoped value would be empty and every candidate would fail.
set(_GTOPT_TEST_PYTHON_IMPORTS "pandas, pyarrow, numpy"
    CACHE INTERNAL "Modules the test Python must import")

# _gtopt_python_has_test_deps(<python> <out_var>)
function(_gtopt_python_has_test_deps python out_var)
  set(${out_var} FALSE PARENT_SCOPE)
  if(NOT python OR NOT EXISTS "${python}")
    return()
  endif()
  execute_process(
    COMMAND "${python}" -c "import ${_GTOPT_TEST_PYTHON_IMPORTS}"
    RESULT_VARIABLE _rc OUTPUT_QUIET ERROR_QUIET)
  if(_rc EQUAL 0)
    set(${out_var} TRUE PARENT_SCOPE)
  endif()
endfunction()

function(gtopt_select_test_python)
  set(_repo "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../..")

  if(GTOPT_TEST_PYTHON)
    _gtopt_python_has_test_deps("${GTOPT_TEST_PYTHON}" _ok)
    if(NOT _ok)
      message(FATAL_ERROR
        "GTOPT_TEST_PYTHON='${GTOPT_TEST_PYTHON}' cannot import "
        "${_GTOPT_TEST_PYTHON_IMPORTS}.  Install the test deps into it "
        "(uv pip install --python '${GTOPT_TEST_PYTHON}' -e "
        "\"${_repo}/scripts[dev]\") or point GTOPT_TEST_PYTHON elsewhere.")
    endif()
    set(Python3_EXECUTABLE "${GTOPT_TEST_PYTHON}"
        CACHE FILEPATH "Python interpreter" FORCE)
    message(STATUS "Integration-test Python: ${GTOPT_TEST_PYTHON} "
                   "(GTOPT_TEST_PYTHON)")
    return()
  endif()

  set(_candidates "")
  if(Python3_EXECUTABLE)
    list(APPEND _candidates "${Python3_EXECUTABLE}")
  endif()
  if(DEFINED ENV{VIRTUAL_ENV} AND NOT "$ENV{VIRTUAL_ENV}" STREQUAL "")
    list(APPEND _candidates "$ENV{VIRTUAL_ENV}/bin/python")
  endif()
  list(APPEND _candidates
       "${_repo}/.venv/bin/python"
       "${_repo}/scripts/.venv/bin/python")
  find_program(_gtopt_path_python NAMES python3 python NO_CACHE)
  if(_gtopt_path_python)
    list(APPEND _candidates "${_gtopt_path_python}")
  endif()

  foreach(_py IN LISTS _candidates)
    _gtopt_python_has_test_deps("${_py}" _ok)
    if(_ok)
      # Keep the given path (a venv's bin/python symlink must not be
      # resolved to the base interpreter, which lacks the venv's deps).
      set(Python3_EXECUTABLE "${_py}"
          CACHE FILEPATH "Python interpreter" FORCE)
      message(STATUS "Integration-test Python: ${_py} (imports "
                     "${_GTOPT_TEST_PYTHON_IMPORTS})")
      return()
    endif()
  endforeach()

  string(REPLACE ";" ", " _tried "${_candidates}")
  message(WARNING
    "No Python with ${_GTOPT_TEST_PYTHON_IMPORTS} found (tried: "
    "${_tried}).  The integration tests' Python validators "
    "(*_sddp_validate, *_validate_solution, numstab_*, ...) will FAIL.  "
    "Install the deps (uv pip install -e \"${_repo}/scripts[dev]\" in a "
    "venv) and reconfigure with VIRTUAL_ENV set, a <repo>/.venv, or "
    "-DGTOPT_TEST_PYTHON=<python>.")
endfunction()
