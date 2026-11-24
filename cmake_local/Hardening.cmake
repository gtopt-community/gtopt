#[=======================================================================[.rst:
Hardening
---------

Toolchain-independent hardening flags for every gtopt binary (the core
library, ``gtopt``, the solver plugins and the tests).  Without this module
the hardening of a build depended on the host compiler's defaults (Ubuntu's
GCC enables most of these, Clang and conda toolchains do not).

Options:

``GTOPT_HARDENING`` (default ``ON``)
  Master switch.  When ``ON``:

  * ``-fstack-protector-strong``, ``-fstack-clash-protection`` and, on
    x86, ``-fcf-protection=full`` (each only when the compiler accepts it);
  * ``-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3`` in optimised configurations
    only (``Release``, ``RelWithDebInfo``, ``MinSizeRel``): FORTIFY needs
    optimisation, and ``CIFast`` / ``Debug`` are ``-O0``.  It is skipped
    under ``USE_SANITIZER=Address`` / ``Memory`` (the sanitizer runtime
    intercepts the same calls);
  * position-independent executables (``CMAKE_POSITION_INDEPENDENT_CODE``
    + ``check_pie_supported``);
  * linker: ``-z relro -z now -z noexecstack``.  ``-z nodlopen`` is
    deliberately NOT used: the solver plugins are dlopen'ed modules.

``GTOPT_GLIBCXX_ASSERTIONS`` (``AUTO`` | ``ON`` | ``OFF``, default ``AUTO``)
  libstdc++ bounds/precondition checks (``operator[]``, ``std::span``,
  ``front()`` on empty containers, ...): an out-of-range access aborts
  instead of silently reading memory.  ``AUTO`` enables it in every
  configuration except ``Release``: the CI / developer builds (``CIFast``,
  ``Debug``) and ``RelWithDebInfo`` catch indexing bugs, while ``Release``
  (the installed solver) keeps the LP-assembly inner loops free of the
  extra branches; there, malformed input is rejected by explicit
  validation (``validate_planning``) instead.  ``ON`` forces it everywhere
  (e.g. for a service build), ``OFF`` disables it.  It does not change the
  ABI, so objects built with and without it link together.

A ``GTOPT_HARDENING=1`` compile definition is added when the module is
active (``GTOPT_GLIBCXX_ASSERTIONS_ACTIVE=1`` when the assertions are on) so
``test_build_hardening.cpp`` can check the flags reach the compiler.
#]=======================================================================]

include_guard(GLOBAL)

include(CheckCXXCompilerFlag)
include(CheckPIESupported)

option(GTOPT_HARDENING "Enable compiler/linker hardening flags" ON)
set(GTOPT_GLIBCXX_ASSERTIONS
    "AUTO"
    CACHE STRING
          "libstdc++ assertions: AUTO (all but Release), ON or OFF")
set_property(CACHE GTOPT_GLIBCXX_ASSERTIONS PROPERTY STRINGS AUTO ON OFF)

if(NOT GTOPT_HARDENING)
  add_compile_definitions(GTOPT_HARDENING_DISABLED=1)
  message(STATUS "gtopt: hardening flags disabled (GTOPT_HARDENING=OFF)")
  return()
endif()

if(MSVC)
  message(STATUS "gtopt: hardening flags are GCC/Clang only; skipped")
  return()
endif()

add_compile_definitions(GTOPT_HARDENING=1)

# ---- Code generation ------------------------------------------------------
set(_gtopt_hardening_flags "")
foreach(_flag IN ITEMS -fstack-protector-strong -fstack-clash-protection
                       -fcf-protection=full)
  string(MAKE_C_IDENTIFIER "GTOPT_HAS${_flag}" _var)
  check_cxx_compiler_flag("${_flag}" ${_var})
  if(${_var})
    add_compile_options("${_flag}")
    list(APPEND _gtopt_hardening_flags "${_flag}")
  endif()
endforeach()

# _FORTIFY_SOURCE needs optimisation; -U first because some toolchains
# (Ubuntu GCC) predefine it and a plain -D would be a redefinition warning
# (an error under -Werror).
set(_gtopt_fortify_ok TRUE)
if(USE_SANITIZER MATCHES "Address|Memory")
  set(_gtopt_fortify_ok FALSE)
endif()
if(_gtopt_fortify_ok)
  set(_gtopt_opt_configs "$<CONFIG:Release,RelWithDebInfo,MinSizeRel>")
  add_compile_options("$<${_gtopt_opt_configs}:-U_FORTIFY_SOURCE>"
                      "$<${_gtopt_opt_configs}:-D_FORTIFY_SOURCE=3>")
  list(APPEND _gtopt_hardening_flags "_FORTIFY_SOURCE=3 (optimised configs)")
endif()

# libstdc++ assertions (see the module documentation for the trade-off).
string(TOUPPER "${GTOPT_GLIBCXX_ASSERTIONS}" _gtopt_assert_mode)
if(_gtopt_assert_mode STREQUAL "AUTO")
  set(_gtopt_assert_cond "$<NOT:$<CONFIG:Release>>")
elseif(_gtopt_assert_mode)  # ON / TRUE / 1 ...
  set(_gtopt_assert_cond "1")
else()
  set(_gtopt_assert_cond "0")
endif()
add_compile_definitions(
  "$<${_gtopt_assert_cond}:_GLIBCXX_ASSERTIONS>"
  "$<${_gtopt_assert_cond}:GTOPT_GLIBCXX_ASSERTIONS_ACTIVE=1>")
list(APPEND _gtopt_hardening_flags
     "_GLIBCXX_ASSERTIONS (${GTOPT_GLIBCXX_ASSERTIONS})")

# ---- Position-independent executables -------------------------------------
check_pie_supported(OUTPUT_VARIABLE _gtopt_pie_msg LANGUAGES CXX)
if(CMAKE_CXX_LINK_PIE_SUPPORTED)
  set(CMAKE_POSITION_INDEPENDENT_CODE ON)
  list(APPEND _gtopt_hardening_flags "PIE")
endif()

# ---- Linker ----------------------------------------------------------------
if(NOT APPLE)
  add_link_options("LINKER:-z,relro" "LINKER:-z,now" "LINKER:-z,noexecstack")
  list(APPEND _gtopt_hardening_flags "-z relro -z now -z noexecstack")
endif()

list(JOIN _gtopt_hardening_flags ", " _gtopt_hardening_msg)
message(STATUS "gtopt: hardening: ${_gtopt_hardening_msg}")
