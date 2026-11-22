#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# tools/run_tidy.sh — the CLAUDE.md pre-commit clang-tidy step, with an
# optional result cache.  Also used by the CI clang-tidy job.
#
# Usage:
#   tools/run_tidy.sh [-p DB] [-j N] [file.cpp ...]   changed / given files
#   tools/run_tidy.sh [-p DB] [-j N] --base REF       files changed since
#                                                     merge-base(REF, HEAD)
#   tools/run_tidy.sh [-p DB] [-j N] --all            every project TU
#   tools/run_tidy.sh --configure [DIR]               create a tidy DB
#
# With no files it checks the *.cpp files changed against HEAD (the
# CLAUDE.md policy), test files included, plus <stem>.cpp and
# test_<stem>.cpp for every changed <stem>.hpp (headers themselves are
# never analysed).  Exit status is non-zero when any warning is reported
# (-warnings-as-errors='*').
#
# Compile database (DB): -p <build-dir|compile_commands.json>, else
# $GTOPT_TIDY_DB, else ./build-tidy when it exists, else ./build.
# The right DB is a *tidy DB*: configure-only (nothing is compiled),
# with -DGTOPT_TIDY_NON_UNITY=ON so that every source file, test files
# included, has its own entry and no PCH is referenced.  Create one with
#   tools/run_tidy.sh --configure            # -> ./build-tidy (~10 s)
# and re-run it after adding or removing .cpp files.  A normal build dir
# works for library files, but its test suite is a unity build: test
# files have no entry there and are reported as "not in the compile DB".
# Extra cmake arguments for --configure: $GTOPT_TIDY_CMAKE_ARGS.
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
#
# Tool selection: $CLANG_TIDY / $RUN_CLANG_TIDY, else the newest
# versioned clang-tidy-N / run-clang-tidy-N pair, else the unversioned
# one.  --configure uses the matching clang++-N (override with $CXX).
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
db="${GTOPT_TIDY_DB:-}"
jobs=$(nproc)
mode=changed
base=""
configure_dir=""
files=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p) db="$2"; shift 2 ;;
    -j) jobs="$2"; shift 2 ;;
    -j*) jobs="${1#-j}"; shift ;;
    --all) mode=all; shift ;;
    --base) mode=base; base="$2"; shift 2 ;;
    --configure)
      mode=configure
      if [[ $# -gt 1 && "$2" != -* ]]; then
        configure_dir="$2"; shift
      fi
      shift ;;
    -h|--help) sed -n '3,48p' "$0"; exit 0 ;;
    *) files+=("$(realpath -m "$1")"); shift ;;
  esac
done

# ---- clang-tidy / run-clang-tidy pair (newest version first) ----
tidy_bin="${CLANG_TIDY:-}"
runner="${RUN_CLANG_TIDY:-}"
tidy_ver=""
for v in -23 -22 -21 -20 ""; do
  [[ -n "$tidy_bin" && -n "$runner" ]] && break
  command -v "clang-tidy$v" > /dev/null || continue
  command -v "run-clang-tidy$v" > /dev/null || continue
  # Skip a ctcache wrapper installed as `clang-tidy` on PATH.
  [[ "$(readlink -f "$(command -v "clang-tidy$v")")" == */tools/clang-tidy ]] \
    && continue
  [[ -z "$tidy_bin" ]] && tidy_bin=$(command -v "clang-tidy$v")
  [[ -z "$runner" ]] && runner=$(command -v "run-clang-tidy$v")
  tidy_ver="$v"
done

if [[ "$mode" == configure ]]; then
  dir="${configure_dir:-${repo_root}/build-tidy}"
  cxx="${CXX:-}"
  if [[ -z "$cxx" ]]; then
    for c in "clang++$tidy_ver" clang++; do
      if command -v "$c" > /dev/null; then
        cxx=$(command -v "$c")
        break
      fi
    done
  fi
  if [[ -z "$cxx" ]]; then
    echo "run_tidy: no clang++ found (set CXX)" >&2
    exit 2
  fi
  cc="${CC:-${cxx/clang++/clang}}"
  launcher=()
  if command -v ccache > /dev/null; then
    launcher=(-DCMAKE_C_COMPILER_LAUNCHER=ccache
              -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
  fi
  # Configure only: clang-tidy needs the compile commands, not objects.
  # CIFast keeps the flags identical to the normal agent build.
  # shellcheck disable=SC2086  # GTOPT_TIDY_CMAKE_ARGS is a word list
  cmake -S "$repo_root/all" -B "$dir" -G Ninja \
    -DCMAKE_BUILD_TYPE=CIFast \
    -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx" \
    "${launcher[@]}" \
    -DGTOPT_TIDY_NON_UNITY=ON \
    -DGTOPT_BUILD_HEAVY_UNIT_TESTS=ON \
    -DGTOPT_BUILD_INTEGRATION_TESTS=OFF \
    -DGTOPT_BUILD_SCRIPTS=OFF \
    -DGTOPT_BUILD_WEBSERVICE=OFF \
    -DGTOPT_BUILD_GUISERVICE=OFF \
    ${GTOPT_TIDY_CMAKE_ARGS:-} > /dev/null
  echo "run_tidy: tidy DB at $dir/compile_commands.json ($cxx)"
  exit 0
fi

if [[ -z "$tidy_bin" || -z "$runner" ]]; then
  echo "run_tidy: clang-tidy / run-clang-tidy not found" >&2
  exit 2
fi

if [[ -z "$db" ]]; then
  db="${repo_root}/build"
  if [[ -f "${repo_root}/build-tidy/compile_commands.json" ]]; then
    db="${repo_root}/build-tidy"
  fi
fi
[[ -f "$db" ]] && db=$(dirname "$db")
if [[ ! -f "$db/compile_commands.json" ]]; then
  echo "run_tidy: no compile_commands.json in '$db'" \
       "(use -p, or create one with: $0 --configure)" >&2
  exit 2
fi

changed_cpp() {
  git -C "$repo_root" diff --name-only --diff-filter=d "$1" \
    | grep -E '\.(cpp|hpp)$' | sed "s|^|${repo_root}/|" || true
}
if [[ "$mode" == base ]]; then
  mapfile -t files < <(changed_cpp \
    "$(git -C "$repo_root" merge-base "$base" HEAD)")
elif [[ "$mode" == changed && ${#files[@]} -eq 0 ]]; then
  mapfile -t files < <(changed_cpp HEAD)
fi

# Resolve the request against the DB.  run-clang-tidy silently skips a
# regex that matches no entry, so report such files explicitly.  --all
# selects every in-repo entry except sources generated in build dirs
# and third-party packages.
mapfile -t files < <(
  python3 - "$repo_root" "$db/compile_commands.json" "$mode" \
    "${files[@]}" <<'EOF'
import json, os, sys
root, db_json, mode, *want = sys.argv[1:]
root = os.path.realpath(root)
with open(db_json, encoding="utf-8") as fh:
    entries = {
        os.path.realpath(os.path.join(e.get("directory", ""), e["file"]))
        for e in json.load(fh)
    }
if mode == "all":
    for f in sorted(entries):
        rel = os.path.relpath(f, root)
        if not rel.startswith(("..", "build", "cpm_modules")):
            print(f)
    sys.exit(0)
unity = any("/unity_" in f for f in entries)
by_name = {}
for f in entries:
    by_name.setdefault(os.path.basename(f), []).append(f)
seen = set()
for w in want:
    if w.endswith(".hpp"):
        # A changed header: analyse its implementation and unit-test TUs.
        stem = os.path.basename(w)[: -len(".hpp")]
        for name in (f"{stem}.cpp", f"test_{stem}.cpp"):
            for f in by_name.get(name, []):
                if f not in seen and not os.path.relpath(f, root).startswith(
                    ("..", "build", "cpm_modules")
                ):
                    seen.add(f)
                    print(f)
        continue
    f = os.path.realpath(w)
    if f in entries:
        if f not in seen:
            seen.add(f)
            print(f)
        continue
    rel = os.path.relpath(f, root)
    hint = ""
    if unity and rel.startswith("test/"):
        hint = " (unity test build; use a tidy DB: run_tidy.sh --configure)"
    print(f"run_tidy: not in the compile DB, skipped: {rel}{hint}",
          file=sys.stderr)
EOF
)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "run_tidy: no .cpp files to check"
  exit 0
fi
echo "run_tidy: ${#files[@]} file(s), $(basename "$tidy_bin"), DB $db"

binary="$tidy_bin"
if [[ "${GTOPT_TIDY_NO_CACHE:-0}" != 1 ]]; then
  wrapper="${CTCACHE_WRAPPER:-}"
  if [[ -z "$wrapper" && -x "$HOME/opt/ctcache/clang-tidy" ]]; then
    wrapper="$HOME/opt/ctcache/clang-tidy"
  fi
  if [[ -n "$wrapper" ]]; then
    export CTCACHE_CLANG_TIDY="$tidy_bin"
    export CTCACHE_DIR="${CTCACHE_DIR:-${TMPDIR:-/tmp}/ctcache-${USER:-user}}"
    export CTCACHE_KEEP_COMMENTS=1
    binary="$wrapper"
    echo "run_tidy: ctcache $wrapper (cache $CTCACHE_DIR)"
  fi
fi

# run-clang-tidy takes the files as regexes; anchor each absolute path.
regex=$(printf '%s\n' "${files[@]}" \
  | sed 's/[.[\*^$()+?{|]/\\&/g; s/^/^/; s/$/$/' | paste -sd'|' -)

exec "$runner" -p "$db" -j "$jobs" -quiet -clang-tidy-binary "$binary" \
  -header-filter='' -warnings-as-errors='*' "$regex"
