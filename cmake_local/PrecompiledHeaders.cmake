#[=======================================================================[.rst:
PrecompiledHeaders
------------------

Applies the project-wide precompiled header (PCH) set shared by the
gtopt library and its test targets.

Usage::

  include(PrecompiledHeaders)
  target_gtopt_pch(<target>)

The header list covers the most frequently included standard library
headers and spdlog.  All headers are added as ``PRIVATE`` so they do
not leak to downstream consumers of the target.

#]=======================================================================]

function(target_gtopt_pch target)
  # A PCH is not relocatable: clang stores the absolute path of every
  # input file (cmake_pch.hxx / cmake_pch.hxx.cxx live in the build dir)
  # and refuses the PCH when they are missing ("could not find file ...
  # referenced by AST file").  When Ccache.cmake turns on cross-build-dir
  # sharing (a base_dir=... override in the launcher), ccache keys the
  # PCH compile on base_dir-relative paths, so a second build dir gets
  # the first dir's .pch, which points into the first dir -- and fails
  # as soon as that dir is deleted (or silently reads its files while it
  # exists).  Sharing every object across build dirs is worth more than
  # the PCH there, so skip the PCH.  CI (CCACHE_BASEDIR env, fixed
  # workspace path) and -DGTOPT_CCACHE_TUNING=OFF keep it.
  get_target_property(_launcher ${target} CXX_COMPILER_LAUNCHER)
  if(_launcher MATCHES "ccache" AND _launcher MATCHES "base_dir=")
    message(STATUS "${target}: PCH off (ccache base_dir sharing; "
                   "-DGTOPT_CCACHE_TUNING=OFF keeps it)")
    return()
  endif()
  target_precompile_headers(${target} PRIVATE
    # --- containers & data structures ---
    <array>
    <map>
    <set>
    <span>
    <string>
    <string_view>
    <tuple>
    <unordered_map>
    <variant>
    <vector>
    # --- algorithms & numerics ---
    <algorithm>
    <charconv>
    <cmath>
    <cstdint>
    <limits>
    <numeric>
    <ranges>
    # --- utilities ---
    <expected>
    <functional>
    <memory>
    <optional>
    <type_traits>
    <utility>
    # --- I/O & filesystem ---
    <filesystem>
    <format>
    <fstream>
    <sstream>
    # --- concurrency ---
    <atomic>
    <chrono>
    <future>
    <mutex>
    <thread>
    # --- error handling ---
    <stdexcept>
    # --- third-party ---
    <spdlog/spdlog.h>
  )
  # Clang records the mtime of every header in the PCH and rejects the
  # PCH ("file has been modified since the precompiled header was
  # built") once any of them is touched.  That breaks ccache: a cached
  # PCH restored into a build dir whose headers have a newer mtime (a
  # fresh checkout, a second worktree) is unusable, and ccache docs
  # require -fno-pch-timestamp for Clang PCH caching.  Content changes
  # are still caught: ccache hashes the headers and Ninja's depfile
  # rebuilds the PCH.  The flag is a cc1 option that only affects
  # -emit-pch, so passing it to every TU of the target is harmless.
  target_compile_options(${target} PRIVATE
    "$<$<COMPILE_LANG_AND_ID:CXX,Clang>:SHELL:-Xclang -fno-pch-timestamp>"
  )
endfunction()
