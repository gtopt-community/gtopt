#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# build_tsan_snappy.sh — build a ThreadSanitizer-instrumented libsnappy.so.1
# matching the system snappy, for TSan runs of gtopt.
#
# Parquet's snappy codec runs inside the uninstrumented system libsnappy,
# whose memory accesses TSan sees without their synchronisation (false
# reports in snappy::internal::CompressFragment from parallel
# OutputContext::write shards).  An instrumented build with the same SONAME
# replaces it at run time through LD_LIBRARY_PATH:
#
#   LD_LIBRARY_PATH=$HOME/opt/snappy-1.2.2-tsan/lib ctest ...
#
# A TSan build script can do that automatically when the
# prefix exists (override: GTOPT_TSAN_EXTRA_LIBPATH).
#
# Usage: tools/build_tsan_snappy.sh [--version 1.2.2] [--prefix DIR]
#                                   [--src DIR] [--cc clang-22]
#                                   [--cxx clang++-22] [-j N]
# Defaults: version = the system libsnappy's (dpkg), else 1.2.2;
#           prefix  = $HOME/opt/snappy-<version>-tsan;
#           src     = ${TMPDIR:-/tmp}/snappy-tsan-src-<version>.
set -euo pipefail

sys_version() {
  dpkg-query -W -f='${Version}' libsnappy1v5 2>/dev/null \
    | sed -E 's/^([0-9]+\.[0-9]+\.[0-9]+).*/\1/'
}

VERSION=$(sys_version || true)
VERSION=${VERSION:-1.2.2}
PREFIX=""
SRC=""
CC_BIN=clang-22
CXX_BIN=clang++-22
JOBS=$(nproc)
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION=$2; shift ;;
    --prefix) PREFIX=$2; shift ;;
    --src) SRC=$2; shift ;;
    --cc) CC_BIN=$2; shift ;;
    --cxx) CXX_BIN=$2; shift ;;
    -j) JOBS=$2; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
PREFIX=${PREFIX:-${HOME}/opt/snappy-${VERSION}-tsan}
SRC=${SRC:-${TMPDIR:-/tmp}/snappy-tsan-src-${VERSION}}

if [ ! -d "${SRC}/.git" ]; then
  git clone --depth 1 --branch "${VERSION}" \
    https://github.com/google/snappy.git "$SRC"
fi
git -C "$SRC" checkout -q "${VERSION}"

flags="-fsanitize=thread -O1 -g"
cmake -S "$SRC" -B "${SRC}/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=None \
  -DCMAKE_C_COMPILER="$CC_BIN" -DCMAKE_CXX_COMPILER="$CXX_BIN" \
  -DCMAKE_C_FLAGS="$flags" -DCMAKE_CXX_FLAGS="$flags" \
  -DBUILD_SHARED_LIBS=ON \
  -DSNAPPY_BUILD_TESTS=OFF -DSNAPPY_BUILD_BENCHMARKS=OFF \
  -DSNAPPY_INSTALL=ON \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib
cmake --build "${SRC}/build" -j "$JOBS"
cmake --install "${SRC}/build"

lib="${PREFIX}/lib/libsnappy.so.1"
if ! nm -D "$lib" | grep -q ' U __tsan_'; then
  echo "ERROR: ${lib} carries no __tsan_ references (not instrumented)" >&2
  exit 1
fi
echo "Instrumented snappy ${VERSION}: ${lib}"
echo "Use: LD_LIBRARY_PATH=${PREFIX}/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
