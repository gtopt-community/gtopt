#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# tools/run_tidy.sh — the CLAUDE.md pre-commit clang-tidy step, with an
# optional result cache.
#
# Usage:
#   tools/run_tidy.sh [-p <build-dir|compile_commands.json>] [-j N] [file.cpp ...]
#
# With no files it checks the *.cpp files changed against HEAD (the
# CLAUDE.md policy).  Exit status is non-zero when any warning is
# reported (-warnings-as-errors='*').
#
# Compile database: -p, else $GTOPT_TIDY_DB, else ./build.  It must be a
# PCH-free database (configure with -DCMAKE_DISABLE_PRECOMPILE_HEADERS=ON,
# as CI does) or one whose PCH files exist; the committed
# tools/compile_commands.json is a stale CI snapshot (/home/runner paths)
# and matches no local file.
#
# Result cache (optional): when ctcache
# (https://github.com/matus-chochlik/ctcache) is found, clang-tidy runs
# through its wrapper.  ctcache hashes the preprocessed TU, the
# effective clang-tidy config (--dump-config) and the arguments; a TU
# that was clean before is skipped.  Only clean results are cached, so
# a file with warnings is always re-analysed and still fails.
#   lookup: $CTCACHE_WRAPPER, then ~/opt/ctcache/clang-tidy (the bash
#           wrapper of a `git clone` of ctcache; it reads the real binary
#           from $CTCACHE_CLANG_TIDY, which this script sets).  The older
#           single-file `clang-tidy-cache` script expects the real binary
#           as its first argument and is not auto-detected.
#           GTOPT_TIDY_NO_CACHE=1 disables the cache.
#   cache dir: $CTCACHE_DIR, default ${TMPDIR:-/tmp}/ctcache-$USER.
#           Keep it on local disk (not an NFS home).
# CTCACHE_KEEP_COMMENTS=1 is forced so that removing a NOLINT comment
# (which does not change the preprocessed code) invalidates the entry.
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
db="${GTOPT_TIDY_DB:-${repo_root}/build}"
jobs=$(nproc)
files=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p) db="$2"; shift 2 ;;
    -j) jobs="$2"; shift 2 ;;
    -j*) jobs="${1#-j}"; shift ;;
    -h|--help) sed -n '3,35p' "$0"; exit 0 ;;
    *) files+=("$1"); shift ;;
  esac
done
[[ -f "$db" ]] && db=$(dirname "$db")
if [[ ! -f "$db/compile_commands.json" ]]; then
  echo "run_tidy: no compile_commands.json in '$db' (use -p)" >&2
  exit 2
fi

if [[ ${#files[@]} -eq 0 ]]; then
  mapfile -t files < <(git -C "$repo_root" diff --name-only \
    --diff-filter=d HEAD | grep -E '\.cpp$' || true)
fi
if [[ ${#files[@]} -eq 0 ]]; then
  echo "run_tidy: no changed .cpp files"
  exit 0
fi

# Pick the clang-tidy / run-clang-tidy pair (prefer the newest suffix).
tidy_bin="${CLANG_TIDY:-}"
runner="${RUN_CLANG_TIDY:-}"
for v in "" -23 -22 -21 -20; do
  [[ -z "$tidy_bin" ]] && command -v "clang-tidy$v" > /dev/null \
    && [[ "$(readlink -f "$(command -v "clang-tidy$v")")" != */tools/clang-tidy ]] \
    && tidy_bin=$(command -v "clang-tidy$v")
  [[ -z "$runner" ]] && command -v "run-clang-tidy$v" > /dev/null \
    && runner=$(command -v "run-clang-tidy$v")
done
if [[ -z "$tidy_bin" || -z "$runner" ]]; then
  echo "run_tidy: clang-tidy / run-clang-tidy not found" >&2
  exit 2
fi

binary="$tidy_bin"
if [[ "${GTOPT_TIDY_NO_CACHE:-0}" != 1 ]]; then
  wrapper="${CTCACHE_WRAPPER:-}"
  [[ -z "$wrapper" && -x "$HOME/opt/ctcache/clang-tidy" ]] \
    && wrapper="$HOME/opt/ctcache/clang-tidy"
  if [[ -n "$wrapper" ]]; then
    export CTCACHE_CLANG_TIDY="$tidy_bin"
    export CTCACHE_DIR="${CTCACHE_DIR:-${TMPDIR:-/tmp}/ctcache-${USER:-user}}"
    export CTCACHE_KEEP_COMMENTS=1
    binary="$wrapper"
    echo "run_tidy: ctcache $wrapper (cache $CTCACHE_DIR)"
  fi
fi

# run-clang-tidy takes the files as regexes; anchor each absolute path.
regex=$(for f in "${files[@]}"; do
  realpath "$f" | sed 's/[.[\*^$()+?{|]/\\&/g; s/^/^/; s/$/$/'
done | paste -sd'|' -)

exec "$runner" -p "$db" -j "$jobs" -quiet -clang-tidy-binary "$binary" \
  -header-filter='' -warnings-as-errors='*' "$regex"
