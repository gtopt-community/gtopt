# CheckElfSecurity.cmake — CTest script (cmake -P) that inspects built ELF
# files with readelf and fails on missing hardening.
#
#   cmake -DREADELF=<readelf> -DEXE=<gtopt> [-DPLUGINS=<a.so|b.so>]
#         [-DHARDENING=ON] [-DOPTIMIZED=1] -P CheckElfSecurity.cmake
#
# Checks (always): no empty RUNPATH/RPATH entry (ld.so reads it as the
# current directory) and no `$ORIGIN/../..` on the executable (that is
# $HOME for ~/.local/bin/gtopt).
# Checks (HARDENING=ON): PIE executable, BIND_NOW, GNU_RELRO, non-executable
# GNU_STACK and the stack protector on every file; FORTIFY (`__*_chk`
# imports) on the executable when OPTIMIZED.

# Script mode (-P) starts with default (OLD) policies: set them.
cmake_minimum_required(VERSION 3.25)

if(NOT READELF OR NOT EXE)
  message(FATAL_ERROR "CheckElfSecurity: READELF and EXE are required")
endif()

set(_failures "")

function(_elf_dump _file _out)
  execute_process(
    COMMAND "${READELF}" -W -h -l -d --dyn-syms "${_file}"
    OUTPUT_VARIABLE _txt
    RESULT_VARIABLE _rc)
  if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "readelf failed on ${_file}")
  endif()
  set(${_out} "${_txt}" PARENT_SCOPE)
endfunction()

macro(_fail _msg)
  list(APPEND _failures "${_msg}")
  message(STATUS "FAIL: ${_msg}")
endmacro()

set(_files "${EXE}")
if(PLUGINS)
  string(REPLACE "|" ";" _plugins "${PLUGINS}")
  list(APPEND _files ${_plugins})
endif()

foreach(_file IN LISTS _files)
  _elf_dump("${_file}" _txt)
  get_filename_component(_name "${_file}" NAME)
  message(STATUS "checking ${_name}")

  if(_txt MATCHES "\\((RUNPATH|RPATH)\\)[^\n]*\\[([^]\n]*)\\]")
    set(_rpath "${CMAKE_MATCH_2}")
    message(STATUS "  RUNPATH: ${_rpath}")
    if(_rpath MATCHES "(^:|::|:$)" OR _rpath STREQUAL "")
      _fail("${_name}: empty RUNPATH entry (= current directory): ${_rpath}")
    endif()
    if(_file STREQUAL EXE AND _rpath MATCHES "\\$ORIGIN/\\.\\./\\.\\.")
      _fail("${_name}: executable RUNPATH has $ORIGIN/../..: ${_rpath}")
    endif()
  endif()

  if(HARDENING)
    if(NOT _txt MATCHES "BIND_NOW" AND NOT _txt MATCHES "FLAGS_1[^\n]*NOW")
      _fail("${_name}: no BIND_NOW (link with -z now)")
    endif()
    if(NOT _txt MATCHES "GNU_RELRO")
      _fail("${_name}: no GNU_RELRO segment (link with -z relro)")
    endif()
    if(_txt MATCHES "GNU_STACK[^\n]*RWE")
      _fail("${_name}: executable stack")
    endif()
    if(NOT _txt MATCHES "__stack_chk_fail")
      _fail("${_name}: no stack protector (__stack_chk_fail not imported)")
    endif()
    if(_file STREQUAL EXE)
      if(NOT _txt MATCHES "Type:[ ]+DYN")
        _fail("${_name}: not a position-independent executable")
      endif()
      if(OPTIMIZED AND NOT _txt MATCHES "__[a-z0-9_]+_chk@")
        _fail("${_name}: no FORTIFY (__*_chk) imports in an optimised build")
      endif()
    endif()
  endif()
endforeach()

if(_failures)
  list(LENGTH _failures _n)
  message(FATAL_ERROR "CheckElfSecurity: ${_n} problem(s)")
endif()
message(STATUS "CheckElfSecurity: OK")
