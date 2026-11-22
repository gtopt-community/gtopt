#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# tools/run_tidy.sh — the pre-commit clang-tidy step (CONTRIBUTING.md), with an
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
# project policy), test files included, plus <stem>.cpp and
# test_<stem>.cpp for every changed <stem>.hpp (headers themselves are
# never analysed).  Exit status is non-zero when any warning is reported
# (-warnings-as-errors='*').
#
# Compile database (DB): -p <build-dir|compile_commands.json>, else
# $GTOPT_TIDY_DB, else ./build-tidy -- created automatically on first
# use, and refreshed once when a requested file is not in it (a new
# .cpp).  It is a *tidy DB*: configure-only (nothing is compiled), with
# -DGTOPT_TIDY_NON_UNITY=ON so that every source file, test files
# included, has its own entry and no PCH is referenced.  --configure
# [DIR] (re)creates one explicitly.  A normal build dir works for
# library files, but its test suite is a unity build: test files have
# no entry there and are reported as "not in the compile DB".
# Extra cmake arguments for --configure: $GTOPT_TIDY_CMAKE_ARGS.
# Creating ./build-tidy also adds a ./compile_commands.json symlink to it
# (for clangd / IDEs) when the checkout has none; both are git-ignored.
#
# Result cache (optional): when ctcache
# (https://github.com/matus-chochlik/ctcache) is found, clang-tidy runs
# through its wrapper.  ctcache hashes the preprocessed TU, the
# effective clang-tidy config (--dump-config) and the arguments; a TU
# that was clean before is skipped.  Only clean results are cached, so
# a file with warnings is always re-analysed and still fails.
#   install: bash tools/install_ctcache.sh [DIR]  (pinned git checkout,
#           default $CTCACHE_HOME or ~/.local/opt/ctcache).
#   lookup: $CTCACHE_WRAPPER, $CTCACHE_HOME/clang-tidy,
#           ~/.local/opt/ctcache/clang-tidy, ~/opt/ctcache/clang-tidy, then
#           a ctcache wrapper installed as `clang-tidy` on PATH (the bash
#           wrapper of the checkout; it reads the real binary from
#           $CTCACHE_CLANG_TIDY, which this script sets).  The older
#           single-file `clang-tidy-cache` script expects the real binary
#           as its first argument and is not supported.
#           Missing: a warning, and the run goes on uncached;
#           GTOPT_TIDY_REQUIRE_CACHE=1 (CI) makes it an error (rc 2).
#           GTOPT_TIDY_NO_CACHE=1 disables the cache.
#   cache dir: $CTCACHE_DIR, default ${TMPDIR:-/tmp}/ctcache-$USER.
#           Keep it on local disk (not an NFS home).
#           Point TMPDIR (or CTCACHE_DIR) at a large, persistent local
#           disk: the cache is meant to live across runs.
#   sharing: the checkout and DB paths are stripped from the hashed
#           inputs (CTCACHE_STRIP + CTCACHE_STRIP_SRC=1), so one cache
#           serves every worktree / clone / build-tidy dir of the repo.
# CTCACHE_KEEP_COMMENTS=1 is forced so that removing a NOLINT comment
# (which does not change the preprocessed code) invalidates the entry.
# After the run the script prints this run's ctcache hits / misses.
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
    -h|--help) sed -n '3,64p' "$0"; exit 0 ;;
    *) files+=("$(realpath -m "$1")"); shift ;;
  esac
done

# A ctcache checkout's bash wrapper (named clang-tidy).
is_ctcache_wrapper() {
  [[ -f "$1" ]] && head -c 512 "$1" | grep -q 'clang-tidy cache wrapper'
}

# ---- clang-tidy / run-clang-tidy pair (newest version first) ----
tidy_bin="${CLANG_TIDY:-}"
runner="${RUN_CLANG_TIDY:-}"
tidy_ver=""
for v in -23 -22 -21 -20 ""; do
  [[ -n "$tidy_bin" && -n "$runner" ]] && break
  command -v "clang-tidy$v" > /dev/null || continue
  command -v "run-clang-tidy$v" > /dev/null || continue
  # Skip a ctcache wrapper installed as `clang-tidy` on PATH.
  is_ctcache_wrapper "$(command -v "clang-tidy$v")" && continue
  [[ -z "$tidy_bin" ]] && tidy_bin=$(command -v "clang-tidy$v")
  [[ -z "$runner" ]] && runner=$(command -v "run-clang-tidy$v")
  tidy_ver="$v"
done

configure_db() {
  local dir="$1" cxx="${CXX:-}" cc c
  local -a launcher generator
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
  if command -v ccache > /dev/null; then
    launcher=(-DCMAKE_C_COMPILER_LAUNCHER=ccache
              -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
  fi
  command -v ninja > /dev/null && generator=(-G Ninja)
  # Configure only: clang-tidy needs the compile commands, not objects.
  # CIFast keeps the flags identical to the normal agent build.
  # shellcheck disable=SC2086  # GTOPT_TIDY_CMAKE_ARGS is a word list
  cmake -S "$repo_root/all" -B "$dir" "${generator[@]}" \
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
  # The only build-time generated header (git_revision.hpp, included by
  # build_info.cpp); without it that TU fails with clang-diagnostic-error.
  cmake --build "$dir" --target gtopt_git_revision > /dev/null
  echo "run_tidy: tidy DB at $dir/compile_commands.json ($cxx)"
  # clangd / IDEs look for ./compile_commands.json; point it at the
  # per-file tidy DB unless the user already has one (git-ignored).
  if [[ "$(realpath -m "$dir")" == "$(realpath "$repo_root")/build-tidy" \
        && ! -e "$repo_root/compile_commands.json" \
        && ! -L "$repo_root/compile_commands.json" ]]; then
    ln -s build-tidy/compile_commands.json "$repo_root/compile_commands.json"
  fi
}

if [[ "$mode" == configure ]]; then
  configure_db "${configure_dir:-${repo_root}/build-tidy}"
  exit 0
fi

if [[ -z "$tidy_bin" || -z "$runner" ]]; then
  echo "run_tidy: clang-tidy / run-clang-tidy not found" >&2
  exit 2
fi

# Default DB: ./build-tidy, created on first use (configure only).
auto_db=0
if [[ -z "$db" ]]; then
  db="${repo_root}/build-tidy"
  auto_db=1
  if [[ ! -f "$db/compile_commands.json" ]]; then
    echo "run_tidy: creating the tidy DB in $db (first use)"
    configure_db "$db"
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
resolve_py=$(cat <<'EOF'
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
resolve() {
  python3 -c "$resolve_py" "$repo_root" "$db/compile_commands.json" "$mode" \
    "${requested[@]}"
}
requested=("${files[@]}")
missing_log=$(mktemp)
mapfile -t files < <(resolve 2> "$missing_log")
if [[ -s "$missing_log" && $auto_db == 1 ]]; then
  # New .cpp files since the DB was configured: refresh it once.
  echo "run_tidy: refreshing the tidy DB (files not in it)"
  configure_db "$db"
  mapfile -t files < <(resolve)
else
  cat "$missing_log" >&2
fi
rm -f "$missing_log"
if [[ ${#files[@]} -eq 0 ]]; then
  echo "run_tidy: no .cpp files to check"
  exit 0
fi
echo "run_tidy: ${#files[@]} file(s), $(basename "$tidy_bin"), DB $db"

binary="$tidy_bin"
if [[ "${GTOPT_TIDY_NO_CACHE:-0}" != 1 ]]; then
  wrapper="${CTCACHE_WRAPPER:-}"
  if [[ -z "$wrapper" ]]; then
    for w in ${CTCACHE_HOME:+"$CTCACHE_HOME/clang-tidy"} \
             "$HOME/.local/opt/ctcache/clang-tidy" \
             "$HOME/opt/ctcache/clang-tidy" \
             "$(command -v clang-tidy || true)"; do
      if [[ -x "$w" ]] && is_ctcache_wrapper "$w"; then
        wrapper="$w"
        break
      fi
    done
  fi
  if [[ -z "$wrapper" ]]; then
    echo "run_tidy: ctcache not found -- running uncached." \
         "Install it with: bash tools/install_ctcache.sh" \
         "(or set CTCACHE_HOME / CTCACHE_WRAPPER)" >&2
    if [[ "${GTOPT_TIDY_REQUIRE_CACHE:-0}" == 1 ]]; then
      exit 2
    fi
  else
    export CTCACHE_CLANG_TIDY="$tidy_bin"
    export CTCACHE_DIR="${CTCACHE_DIR:-${TMPDIR:-/tmp}/ctcache-${USER:-user}}"
    export CTCACHE_KEEP_COMMENTS=1
    export CTCACHE_STRIP="${CTCACHE_STRIP:-$(realpath "$db"):${repo_root}}"
    export CTCACHE_STRIP_SRC="${CTCACHE_STRIP_SRC:-1}"
    binary="$wrapper"
    echo "run_tidy: ctcache $wrapper (cache $CTCACHE_DIR)"
  fi
fi

# run-clang-tidy takes the files as regexes; anchor each absolute path.
regex=$(printf '%s\n' "${files[@]}" \
  | sed 's/[.[\*^$()+?{|]/\\&/g; s/^/^/; s/$/$/' | paste -sd'|' -)

# ctcache keeps cumulative local hit/miss counters; report this run's
# delta (the CI log shows how much the restored cache saved).
ctcache_py=""
if [[ "$binary" != "$tidy_bin" ]]; then
  ctcache_py="$(dirname "$(realpath "$binary")")/src/ctcache/clang_tidy_cache.py"
  [[ -f "$ctcache_py" ]] || ctcache_py=""
fi
ctcache_counts() {
  python3 "$ctcache_py" --print-stats 2> /dev/null | python3 -c \
    'import json,sys; l=json.load(sys.stdin)["local"]; print(l["hit_count"], l["miss_count"])' \
    2> /dev/null || echo "0 0"
}
[[ -n "$ctcache_py" ]] && read -r hits0 miss0 < <(ctcache_counts)

rc=0
"$runner" -p "$db" -j "$jobs" -quiet -clang-tidy-binary "$binary" \
  -header-filter='' -warnings-as-errors='*' "$regex" || rc=$?

if [[ -n "$ctcache_py" ]]; then
  read -r hits1 miss1 < <(ctcache_counts)
  hits=$((hits1 - hits0))
  # run-clang-tidy's own `-list-checks` probe goes through the wrapper
  # and is counted as one miss; it is not a TU.
  miss=$((miss1 - miss0 - 1))
  ((miss < 0)) && miss=0
  total=$((hits + miss))
  echo "run_tidy: ctcache ${hits} hit(s), ${miss} miss(es)" \
       "($(( total > 0 ? 100 * hits / total : 0 ))% hit rate)"
fi
exit "$rc"
