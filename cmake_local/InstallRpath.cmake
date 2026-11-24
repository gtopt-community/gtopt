#[=======================================================================[.rst:
InstallRpath
------------

Configure ``INSTALL_RPATH`` for a target so that non-system shared libraries
it links against (directly or transitively) can be found at runtime after
``cmake --install``.

Usage::

  include(InstallRpath)
  configure_install_rpath(<target> [<lib_target> ...])
  gtopt_install_with_rpath(<target> <destination>)

``<target>``
  The executable (or MODULE / shared-library plugin) target whose
  ``INSTALL_RPATH`` / ``BUILD_RPATH`` will be set.

``<lib_target>``
  One or more library targets whose ``LINK_LIBRARIES`` property is inspected
  to discover non-system shared-library directories.  If omitted, only the
  direct link libraries of ``<target>`` are considered.

The function walks every item in the ``LINK_LIBRARIES`` property of each
*lib_target* (falling back to *target* itself), resolves imported-target
locations and filters out well-known system directories.

Search paths (never an empty entry, which ld.so treats as the current
working directory):

* executable (``<prefix>/bin``): ``$ORIGIN``, ``$ORIGIN/../lib``;
* plugin (``<prefix>/lib/gtopt/plugins``): ``$ORIGIN``, ``$ORIGIN/../..``
  (= ``<prefix>/lib``);

followed by the absolute non-system library directories.  The executable
deliberately has no ``$ORIGIN/../..`` entry: for ``~/.local/bin/gtopt`` it
would make ``$HOME`` a library search directory.

``gtopt_install_with_rpath()`` installs the target with ``install(PROGRAMS)``
and sets the install ``RUNPATH`` with ``file(RPATH_SET)``.  It replaces
``install(TARGETS)`` because CMake's install-time RPATH rewrite pads the
build-tree RPATH with a trailing ``:`` (an empty entry, i.e. the current
directory) for every installable target.  The build-tree ``BUILD_RPATH`` is
therefore kept at least as long as the install value.

#]=======================================================================]

include_guard(GLOBAL)

# Helper: try to resolve the library path for an imported target.
# Checks IMPORTED_LOCATION, then every IMPORTED_LOCATION_<CONFIG>, and
# finally falls back to the generic LOCATION property.
function(_resolve_imported_location _target _out_var)
  set(_loc "")

  # 1. IMPORTED_LOCATION (generic)
  get_target_property(_loc ${_target} IMPORTED_LOCATION)

  # 2. IMPORTED_LOCATION_<CONFIG> for each known configuration
  if(NOT _loc)
    get_target_property(_configs ${_target} IMPORTED_CONFIGURATIONS)
    if(_configs)
      foreach(_cfg ${_configs})
        string(TOUPPER "${_cfg}" _uc)
        get_target_property(_loc ${_target} IMPORTED_LOCATION_${_uc})
        if(_loc)
          break()
        endif()
      endforeach()
    endif()
  endif()

  # 3. LOCATION fallback — only for IMPORTED targets.  Reading the LOCATION
  #    property on a target built in THIS project (e.g. a from-source shared
  #    spdlog) is disallowed (CMP0026) and aborts configure.  Such targets land
  #    in the build / install tree that CMake's own (build-time) rpath already
  #    covers, and a plugin dlopen'd into the standalone resolves their symbols
  #    from the instance the standalone already loaded — so resolving them here
  #    is both unnecessary and an error.  Leave _loc empty for them.
  if(NOT _loc)
    get_target_property(_imported ${_target} IMPORTED)
    if(_imported)
      get_property(_loc TARGET ${_target} PROPERTY LOCATION)
    endif()
  endif()

  set(${_out_var} "${_loc}" PARENT_SCOPE)
endfunction()

function(configure_install_rpath _target)
  set(_system_lib_dirs "^(/usr/lib|/usr/lib64|/lib|/lib64|/usr/lib/x86_64-linux-gnu|/usr/lib/aarch64-linux-gnu|/usr/lib/arm-linux-gnueabihf|/usr/lib/powerpc64le-linux-gnu|/usr/lib/s390x-linux-gnu)$")
  set(_install_rpaths "")

  # Determine which targets to inspect for link libraries
  set(_lib_targets ${ARGN})
  if(NOT _lib_targets)
    set(_lib_targets ${_target})
  endif()

  # Collect all items to inspect (direct + transitive INTERFACE_LINK_LIBRARIES)
  set(_all_items "")
  foreach(_lib_target ${_lib_targets})
    if(NOT TARGET ${_lib_target})
      continue()
    endif()
    get_target_property(_libs ${_lib_target} LINK_LIBRARIES)
    if(_libs)
      list(APPEND _all_items ${_libs})
    endif()
    get_target_property(_ilibs ${_lib_target} INTERFACE_LINK_LIBRARIES)
    if(_ilibs)
      list(APPEND _all_items ${_ilibs})
    endif()
  endforeach()

  # Walk collected items and resolve shared-library directories
  foreach(_item ${_all_items})
    if(TARGET ${_item})
      get_target_property(_type ${_item} TYPE)
      if(_type MATCHES "SHARED_LIBRARY|UNKNOWN_LIBRARY")
        _resolve_imported_location(${_item} _loc)
        if(_loc AND EXISTS "${_loc}")
          get_filename_component(_dir "${_loc}" DIRECTORY)
          if(NOT _dir MATCHES "${_system_lib_dirs}")
            list(APPEND _install_rpaths "${_dir}")
          endif()
        endif()
      elseif(_type STREQUAL "INTERFACE_LIBRARY")
        # Interface libraries may pull in shared libraries transitively
        get_target_property(_ilibs ${_item} INTERFACE_LINK_LIBRARIES)
        if(_ilibs)
          foreach(_ilib ${_ilibs})
            if(TARGET ${_ilib})
              get_target_property(_itype ${_ilib} TYPE)
              if(_itype MATCHES "SHARED_LIBRARY|UNKNOWN_LIBRARY")
                _resolve_imported_location(${_ilib} _iloc)
                if(_iloc AND EXISTS "${_iloc}")
                  get_filename_component(_idir "${_iloc}" DIRECTORY)
                  if(NOT _idir MATCHES "${_system_lib_dirs}")
                    list(APPEND _install_rpaths "${_idir}")
                  endif()
                endif()
              endif()
            endif()
          endforeach()
        endif()
      endif()
    elseif(_item AND EXISTS "${_item}")
      # Plain library path (e.g. /opt/coinor/lib/libOsiClp.so)
      get_filename_component(_dir "${_item}" DIRECTORY)
      if(NOT _dir MATCHES "${_system_lib_dirs}")
        list(APPEND _install_rpaths "${_dir}")
      endif()
    endif()
  endforeach()

  # Filter empty entries and duplicates.
  list(FILTER _install_rpaths EXCLUDE REGEX "^$")
  list(REMOVE_DUPLICATES _install_rpaths)

  # Layout-relative entries: `$ORIGIN` (sibling libraries, e.g. the CI
  # artifact that ships libspdlog / libhighs next to the binary and plugins)
  # plus the path from the install location to <prefix>/lib, where the
  # shared libspdlog.so.1.15 is installed.
  get_target_property(_type ${_target} TYPE)
  if(_type STREQUAL "EXECUTABLE")
    set(_relative "$ORIGIN" "$ORIGIN/../lib")  # <prefix>/bin
  else()
    set(_relative "$ORIGIN" "$ORIGIN/../..")  # <prefix>/lib/gtopt/plugins
  endif()

  # Install value: build-tree directories (e.g. a from-source spdlog) are
  # meaningless after install and are dropped.
  set(_absolute_install "")
  foreach(_dir IN LISTS _install_rpaths)
    cmake_path(IS_PREFIX CMAKE_BINARY_DIR "${_dir}" NORMALIZE _in_build)
    if(NOT _in_build)
      list(APPEND _absolute_install "${_dir}")
    endif()
  endforeach()
  set(_install_value ${_relative} ${_absolute_install})

  # Build-tree value: `$ORIGIN` plus absolute directories only, padded with
  # build-tree directories so file(RPATH_SET) at install time always fits.
  set(_build_value "$ORIGIN" "${CMAKE_BINARY_DIR}/lib" ${_install_rpaths})
  list(JOIN _install_value ":" _install_str)
  list(JOIN _build_value ":" _build_str)
  string(LENGTH "${_install_str}" _install_len)
  string(LENGTH "${_build_str}" _build_len)
  set(_pad 0)
  while(_build_len LESS _install_len)
    math(EXPR _pad "${_pad} + 1")
    list(APPEND _build_value "${CMAKE_BINARY_DIR}/lib/rpath-pad-${_pad}")
    list(JOIN _build_value ":" _build_str)
    string(LENGTH "${_build_str}" _build_len)
  endwhile()

  set_target_properties(${_target} PROPERTIES
    INSTALL_RPATH "${_install_value}"
    BUILD_RPATH "${_build_value}"
  )
endfunction()

# Install <target> into <destination> (relative to CMAKE_INSTALL_PREFIX) and
# set its RUNPATH to the INSTALL_RPATH computed by configure_install_rpath().
# See the module documentation for why install(TARGETS) is not used.
function(gtopt_install_with_rpath _target _destination)
  install(PROGRAMS "$<TARGET_FILE:${_target}>" DESTINATION "${_destination}")

  get_target_property(_rpath ${_target} INSTALL_RPATH)
  if(NOT _rpath)
    return()
  endif()
  list(JOIN _rpath ":" _rpath_str)
  string(REPLACE "$" "\\$" _rpath_esc "${_rpath_str}")
  set(_file "\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${_destination}/$<TARGET_FILE_NAME:${_target}>")
  install(CODE "file(RPATH_SET FILE \"${_file}\" NEW_RPATH \"${_rpath_esc}\")")
  if(CMAKE_STRIP)
    install(CODE "
      if(CMAKE_INSTALL_DO_STRIP)
        execute_process(COMMAND \"${CMAKE_STRIP}\" \"${_file}\")
      endif()")
  endif()
endfunction()
