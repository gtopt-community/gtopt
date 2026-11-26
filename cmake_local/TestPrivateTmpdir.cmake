#[=======================================================================[.rst:
TestPrivateTmpdir
-----------------

Gives every CTest test a private temporary directory, so tests that write
fixed names under ``$TMPDIR`` (``std::filesystem::temp_directory_path()``,
``tempfile.gettempdir()``, pytest's ``tmp_path`` base) can never clobber
each other -- neither two tests of one ``ctest -j`` run nor two ctest runs
from different build dirs on the same host.

Usage::

  include(TestPrivateTmpdir)

Include it from every project that registers tests (it is safe to include
many times).  Options:

``GTOPT_TEST_PRIVATE_TMPDIR`` (default ``ON``)
  ``OFF`` restores the old behaviour: tests inherit the caller's
  ``TMPDIR`` (or ``/tmp``).

``GTOPT_TEST_TMP_BASE`` (default empty)
  Parent of the per-test dirs.  Empty: ``$TMPDIR`` at configure time,
  else ``$RUNNER_TEMP``, else ``/tmp``.

Where the dirs live
^^^^^^^^^^^^^^^^^^^

``<root> = <base>/gtopt-tests-<hash8 of the build dir>``: one root per
build dir (runs from different build dirs never share) and never inside
the source tree.  Several tests walk up from ``$TMPDIR`` / ``tmp_path``
looking for repo markers (``include/gtopt``, ``.git``); CI builds in
``<checkout>/build``, so a root under the build tree would let them find
the checkout.  A base inside the source tree is refused with a warning
and ``/tmp`` is used instead.

Effect (when ``ON``)
^^^^^^^^^^^^^^^^^^^^

* At the end of the top-level directory (``cmake_language(DEFER)``), every
  test registered at configure time anywhere in the tree gets
  ``TMPDIR=<root>/<sanitised test name>-<hash8>`` via
  ``ENVIRONMENT_MODIFICATION``.  A test that already sets ``TMPDIR`` in its
  own ``ENVIRONMENT`` / ``ENVIRONMENT_MODIFICATION`` is left alone.
* The directories are created at configure time and again every time
  ctest loads a test directory (``TEST_INCLUDE_FILES`` script
  ``<build>/gtopt_test_tmpdirs.cmake``), so removing ``<root>`` (or a
  ``/tmp`` reaper doing it) between runs is always safe.
* The doctest-discovered unit tests (registered at ctest time, not visible
  here) are isolated by ``test/source/main.cpp`` instead: it creates a
  per-process directory under ``GTOPT_TEST_TMPDIR_BASE`` (``<root>/gtoptTests``,
  set by ``test/CMakeLists.txt``) and points ``TMPDIR`` at it.

Cleanup: the per-test dirs under ``<root>`` are left in place (inspect
them after a failure); ``rm -rf <root>`` reclaims the space (the configure
log prints it).  Nothing outside ``<root>`` is ever removed.

#]=======================================================================]

include_guard(GLOBAL)

option(GTOPT_TEST_PRIVATE_TMPDIR
       "Give every CTest test its own TMPDIR outside the source tree" ON)
set(GTOPT_TEST_TMP_BASE "" CACHE PATH
    "Parent of the per-test TMPDIRs (empty: $TMPDIR, $RUNNER_TEMP or /tmp)")

# The gtopt checkout this module belongs to (cmake_local/..).
get_filename_component(_gtopt_test_src_root "${CMAKE_CURRENT_LIST_DIR}/.."
                       REALPATH)

# TRUE when PATH is SRC_ROOT or below it.
function(_gtopt_test_tmpdir_inside PATH SRC_ROOT OUT_VAR)
  file(RELATIVE_PATH _rel "${SRC_ROOT}" "${PATH}")
  if(_rel STREQUAL "" OR NOT _rel MATCHES "^\\.\\.(/|$)")
    set(${OUT_VAR} TRUE PARENT_SCOPE)
  else()
    set(${OUT_VAR} FALSE PARENT_SCOPE)
  endif()
endfunction()

# Base dir: never inside a checkout -- tests that walk up from $TMPDIR
# looking for repo markers (include/gtopt, .git) must not find this one,
# and CI builds in <checkout>/build.
if(NOT GTOPT_TEST_TMP_BASE STREQUAL "")
  set(_gtopt_test_base "${GTOPT_TEST_TMP_BASE}")
elseif(NOT "$ENV{TMPDIR}" STREQUAL "")
  set(_gtopt_test_base "$ENV{TMPDIR}")
elseif(NOT "$ENV{RUNNER_TEMP}" STREQUAL "")
  set(_gtopt_test_base "$ENV{RUNNER_TEMP}")
else()
  set(_gtopt_test_base "/tmp")
endif()
get_filename_component(_gtopt_test_base "${_gtopt_test_base}" REALPATH)
foreach(_root "${_gtopt_test_src_root}" "${CMAKE_SOURCE_DIR}")
  _gtopt_test_tmpdir_inside("${_gtopt_test_base}" "${_root}" _inside)
  if(_inside)
    message(WARNING
      "GTOPT_TEST_PRIVATE_TMPDIR: base ${_gtopt_test_base} is inside the "
      "source tree ${_root}; using /tmp instead")
    set(_gtopt_test_base "/tmp")
  endif()
endforeach()

# One root per build dir, so runs from different build dirs never share.
string(MD5 _gtopt_test_build_hash "${CMAKE_BINARY_DIR}")
string(SUBSTRING "${_gtopt_test_build_hash}" 0 8 _gtopt_test_build_hash)
set(GTOPT_TEST_TMP_ROOT
    "${_gtopt_test_base}/gtopt-tests-${_gtopt_test_build_hash}"
    CACHE INTERNAL "Root of the per-test private TMPDIRs")

# Per-test directory for test name NAME -> OUT_VAR.
function(gtopt_test_tmpdir_for NAME OUT_VAR)
  string(MAKE_C_IDENTIFIER "${NAME}" _clean)
  string(LENGTH "${_clean}" _len)
  if(_len GREATER 80)
    string(SUBSTRING "${_clean}" 0 80 _clean)
  endif()
  # The hash keeps two names that sanitise to the same string apart.
  string(MD5 _hash "${NAME}")
  string(SUBSTRING "${_hash}" 0 8 _hash)
  set(${OUT_VAR} "${GTOPT_TEST_TMP_ROOT}/${_clean}-${_hash}" PARENT_SCOPE)
endfunction()

function(_gtopt_test_tmpdir_has_tmpdir TEST_NAME DIR OUT_VAR)
  set(_found FALSE)
  foreach(_prop ENVIRONMENT ENVIRONMENT_MODIFICATION)
    get_property(_env TEST "${TEST_NAME}" DIRECTORY "${DIR}" PROPERTY ${_prop})
    foreach(_entry IN LISTS _env)
      if(_entry MATCHES "^TMPDIR=")
        set(_found TRUE)
      endif()
    endforeach()
  endforeach()
  set(${OUT_VAR} ${_found} PARENT_SCOPE)
endfunction()

function(_gtopt_test_tmpdir_apply_dir DIR SCRIPT)
  get_property(_tests DIRECTORY "${DIR}" PROPERTY TESTS)
  if(_tests)
    foreach(_t IN LISTS _tests)
      _gtopt_test_tmpdir_has_tmpdir("${_t}" "${DIR}" _has)
      if(_has)
        continue()
      endif()
      gtopt_test_tmpdir_for("${_t}" _dir)
      set_property(TEST "${_t}" DIRECTORY "${DIR}" APPEND
                   PROPERTY ENVIRONMENT_MODIFICATION "TMPDIR=set:${_dir}")
      set_property(GLOBAL APPEND PROPERTY _GTOPT_TEST_TMPDIRS "${_dir}")
    endforeach()
    # ctest runs this before scheduling the directory's tests: recreate
    # the dirs if someone removed <build>/test_tmp since configure.
    set_property(DIRECTORY "${DIR}" APPEND PROPERTY TEST_INCLUDE_FILES
                 "${SCRIPT}")
  endif()
  get_property(_subdirs DIRECTORY "${DIR}" PROPERTY SUBDIRECTORIES)
  foreach(_sub IN LISTS _subdirs)
    _gtopt_test_tmpdir_apply_dir("${_sub}" "${SCRIPT}")
  endforeach()
endfunction()

function(_gtopt_test_tmpdir_apply)
  # The script stays in the build tree: the root may be in a reaped /tmp.
  set(_script "${CMAKE_BINARY_DIR}/gtopt_test_tmpdirs.cmake")
  _gtopt_test_tmpdir_apply_dir("${CMAKE_SOURCE_DIR}" "${_script}")
  get_property(_dirs GLOBAL PROPERTY _GTOPT_TEST_TMPDIRS)
  set(_body "# Generated by cmake_local/TestPrivateTmpdir.cmake\n")
  string(APPEND _body "file(MAKE_DIRECTORY \"${GTOPT_TEST_TMP_ROOT}\")\n")
  foreach(_d IN LISTS _dirs)
    string(APPEND _body "file(MAKE_DIRECTORY \"${_d}\")\n")
  endforeach()
  file(CONFIGURE OUTPUT "${_script}" CONTENT "${_body}" @ONLY)
  file(MAKE_DIRECTORY "${GTOPT_TEST_TMP_ROOT}" ${_dirs})
  list(LENGTH _dirs _n)
  message(STATUS "GTOPT_TEST_PRIVATE_TMPDIR: ${_n} tests get a private "
                 "TMPDIR under ${GTOPT_TEST_TMP_ROOT}")
endfunction()

if(GTOPT_TEST_PRIVATE_TMPDIR)
  cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}"
                 CALL _gtopt_test_tmpdir_apply)
endif()
