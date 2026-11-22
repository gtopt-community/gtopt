#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# GTOpt installer — builds GTOpt from source and installs it.
#
#   curl -fsSL https://raw.githubusercontent.com/gtopt-community/gtopt/master/install.sh | bash
#
# Options and solver notes: see usage() below, or run with --help.

set -euo pipefail

# Everything runs inside main() so a partially downloaded script does nothing.
main() {
  local prefix=/usr/local ref=master python=0 deps=1 dry=0
  local src="${XDG_CACHE_HOME:-$HOME/.cache}/gtopt/src"
  local repo=https://github.com/gtopt-community/gtopt.git
  local jobs
  jobs=$(nproc 2>/dev/null || echo 4)

  while (($#)); do
    case $1 in
      --prefix) prefix=$(need_arg "$@"); shift 2 ;;
      --ref) ref=$(need_arg "$@"); shift 2 ;;
      --src) src=$(need_arg "$@"); shift 2 ;;
      --repo) repo=$(need_arg "$@"); shift 2 ;;
      --python) python=1; shift ;;
      --no-deps) deps=0; shift ;;
      --solvers | --solver) SOLVERS_ARG=$(need_arg "$@"); shift 2 ;;
      --solvers=* | --solver=*) SOLVERS_ARG=${1#*=}; shift ;;
      --without) WITHOUT_ARG+=${WITHOUT_ARG:+,}$(need_arg "$@"); shift 2 ;;
      --without=*) WITHOUT_ARG+=${WITHOUT_ARG:+,}${1#*=}; shift ;;
      --no-highs) NO_HIGHS=1; shift ;;   # deprecated: --without highs
      --no-cuopt) NO_CUOPT=1; shift ;;
      --no-detect) DETECT=0; shift ;;
      --jobs) jobs=$(need_arg "$@"); shift 2 ;;
      --dry-run) dry=1; shift ;;
      -h | --help) usage; return 0 ;;
      *) die "unknown option: $1 (use --help)" ;;
    esac
  done

  DRY=$dry
  local sudo=""
  if ((EUID != 0)); then
    command -v sudo >/dev/null || ((deps == 0)) ||
      die "sudo is required to install system packages (or use --no-deps)"
    sudo=sudo
  fi

  # sudo only for a prefix the user cannot write; a missing prefix under a
  # writable parent (e.g. a fresh ~/.local) is created as the user, so no
  # step leaves root-owned directories in the user's home.
  PSUDO=$(prefix_sudo "$prefix" "$sudo")
  if [[ -z $PSUDO && ! -e $prefix ]]; then
    run mkdir -p "$prefix"
  fi

  say "GTOpt installer: ref=$ref prefix=$prefix"
  resolve_solvers "$prefix"
  print_solver_plan

  ((deps)) && install_deps "$sudo"

  local cmake
  cmake=$(find_cmake)
  # Every build (HiGHS from source too) uses the same compiler: with
  # --no-install-recommends only gcc-14/g++-14 exist, no cc/c++.
  CC=$(pick_cc gcc) CXX=$(pick_cc g++)
  export CC CXX

  if ((deps)); then
    want highs && install_highs "$sudo" "$cmake" "$prefix" "$jobs"
    want scip && install_scip "$sudo"
    want cuopt && [[ -z $CUOPT_DIR ]] && install_cuopt "$sudo" "$prefix"
  fi

  fetch_source "$repo" "$ref" "$src"

  local build="$src/build-install"
  say "Configuring (Release)"
  local -a solver_args=() solver_env=()
  solver_cmake_args
  run env CC="$CC" CXX="$CXX" "${solver_env[@]}" \
    "$cmake" -S "$src/standalone" -B "$build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_PREFIX_PATH="$prefix" "${solver_args[@]}"
  say "Building with $jobs jobs (this takes a while the first time)"
  run "$cmake" --build "$build" -j "$jobs"

  say "Installing into $prefix"
  run $PSUDO "$cmake" --install "$build"
  prune_plugins "$prefix" "$PSUDO"

  ((python)) && install_python "$src"

  if ((DRY == 0)); then
    local bin="$prefix/bin/gtopt"
    say "Installed: $("$bin" --version 2>/dev/null | head -1)"
    say "Solver backends (gtopt --solvers):"
    "$bin" --solvers 2>/dev/null | sed 's/^/  /' || true
    case ":$PATH:" in *":$prefix/bin:"*) ;; *)
      warn "$prefix/bin is not in your PATH; add it to use 'gtopt'." ;;
    esac
  fi
  say "Done. Try:  gtopt --help   (docs: https://github.com/gtopt-community/gtopt/tree/master/docs)"
}

prefix_sudo() {  # prefix_sudo DIR SUDO -> "" if DIR (or its nearest existing parent) is writable
  local d=$1
  while [[ ! -e $d && $d != / ]]; do d=$(dirname "$d"); done
  if [[ -w $d ]]; then echo ""; else echo "$2"; fi
}

need_arg() {
  [[ $# -ge 2 && -n $2 && $2 != --* ]] || die "option $1 needs a value (use --help)"
  printf '%s\n' "$2"
}

# Help text lives in a function: under `curl ... | bash` the script is
# read from stdin, so it cannot re-read its own header comment.
usage() {
  cat <<'USAGE'
GTOpt installer — builds GTOpt from source and installs it.

  curl -fsSL https://raw.githubusercontent.com/gtopt-community/gtopt/master/install.sh | bash

With options (anything after `-s --`):

  curl -fsSL .../install.sh | bash -s -- --prefix "$HOME/.local" --python

Options:
  --prefix DIR     install prefix (default: /usr/local; uses sudo if needed)
  --ref REF        git branch, tag or commit to build (default: master)
  --src DIR        where to keep the source checkout
                   (default: ~/.cache/gtopt/src)
  --repo URL       git repository (default: the gtopt-community repo)
  --python         also install the Python tools (plp2gtopt, plexos2gtopt,
                   gtopt_diagram, run_gtopt, ...) in ~/.local/share/gtopt/venv
                   and link them into ~/.local/bin
  --solvers LIST   build exactly these solver plugins (comma list):
                   highs, cbc (= CBC/CLP), scip, cuopt, cplex, gurobi,
                   mindopt; the others are disabled.  Open-source ones are
                   installed if missing; commercial ones must already be
                   installed (error if not found).  `--solvers all`: every
                   open-source solver that applies (HiGHS, CBC/CLP, SCIP,
                   cuOpt if there is an NVIDIA GPU) plus every commercial
                   solver found.
  --without LIST   drop solvers from the default selection or from `all`
  --no-cuopt       do not install or build cuOpt (same as --without cuopt)
  --no-detect      do not search for commercial solvers; only use
                   CPLEX_ROOT_DIR / GUROBI_HOME / MINDOPT_HOME if set
  --no-highs       deprecated, same as --without highs
  --no-deps        do not install system packages or solvers (you do)
  --jobs N         parallel build jobs (default: number of CPUs)
  --dry-run        print what would be done, change nothing
  -h, --help       show this help

Default solvers (no --solvers): HiGHS and CBC/CLP (installed), every
commercial solver found installed (CPLEX, Gurobi, MindOpt; the newest
version when there are several), and NVIDIA cuOpt when an NVIDIA GPU is
present (pip wheel libcuopt-cu12/-cu13, matching the driver, installed in
PREFIX/lib/gtopt/cuopt).  Commercial solvers are never installed.

Commercial solvers are searched in:
  CPLEX    $CPLEX_ROOT_DIR, $CPLEX_STUDIO_DIR*, /opt/ibm/ILOG/CPLEX_Studio*,
           ~/opt/ibm/ILOG/CPLEX_Studio*, ~/ibm/ILOG/CPLEX_Studio*,
           /opt/cplex, ~/opt/cplex, `cplex`
  Gurobi   $GUROBI_HOME, /opt/gurobi*/linux64, ~/opt/gurobi*/linux64,
           ~/gurobi*/linux64, `gurobi_cl`
  MindOpt  $MINDOPT_HOME, /opt/mindopt/*/linux64-*, ~/opt/mindopt/*/...,
           ~/mindopt/*/linux64-*
Elsewhere, pass the location to bash (not to curl):

  curl -fsSL .../install.sh | CPLEX_ROOT_DIR=/opt/ibm/ILOG/CPLEX_Studio2211/cplex bash
  curl -fsSL .../install.sh | GUROBI_HOME=/opt/gurobi1203/linux64 bash
  curl -fsSL .../install.sh | MINDOPT_HOME=/opt/mindopt/2.3.0/linux64-x86 bash

SCIP (SCIP_ROOT_DIR) and an existing cuOpt (CUOPT_HOME) work the same way.

Supported: Ubuntu 24.04 or newer and Debian 13 or newer (x86_64, arm64).
Other platforms: see BUILDING.md.
USAGE
}

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
run() {
  if ((DRY)); then printf '  + %s\n' "$*"; else "$@"; fi
}

install_deps() {
  local sudo=$1
  [[ -r /etc/os-release ]] || die "cannot detect the OS; install the dependencies by hand (BUILDING.md) and use --no-deps"
  # shellcheck disable=SC1091
  . /etc/os-release
  case ${ID:-} in
    ubuntu | debian) ;;
    *) case " ${ID_LIKE:-} " in
         *" ubuntu "* | *" debian "*) ;;
         *) die "unsupported distribution '${ID:-?}'; see BUILDING.md and use --no-deps" ;;
       esac ;;
  esac

  say "Installing system packages (apt)"
  local -a extra=()
  want cbc && extra+=(coinor-libcbc-dev)
  run $sudo apt-get update -q
  run $sudo apt-get install -y -q --no-install-recommends \
    git ca-certificates wget gnupg pkg-config ninja-build lld \
    gcc-14 g++-14 python3 python3-venv \
    libboost-container-dev zlib1g-dev libzstd-dev liblz4-dev libsnappy-dev \
    "${extra[@]}"

  if ! dpkg -s libarrow-dev libparquet-dev >/dev/null 2>&1; then
    say "Adding the Apache Arrow apt repository"
    local id codename deb tmp
    # From /etc/os-release (sourced above): lsb_release is not installed
    # yet under --dry-run and is absent from minimal images.
    # Derivatives (Mint, Pop!_OS, ...) set UBUNTU_CODENAME.
    if [[ -n ${UBUNTU_CODENAME:-} ]]; then
      id=ubuntu codename=$UBUNTU_CODENAME
    else
      id=$ID codename=${VERSION_CODENAME:-}
    fi
    [[ -n $codename ]] || die "cannot detect the release codename; see BUILDING.md"
    deb="apache-arrow-apt-source-latest-${codename}.deb"
    tmp=$(mktemp -d)
    run wget -q -O "$tmp/$deb" "https://packages.apache.org/artifactory/arrow/${id}/${deb}"
    run $sudo apt-get install -y -q "$tmp/$deb"
    run $sudo apt-get update -q
    run $sudo apt-get install -y -q --no-install-recommends libarrow-dev libparquet-dev
    rm -rf "$tmp"
  fi
}

# CMake >= 3.31.6 is required; fall back to the PyPI wheel in a private venv.
find_cmake() {
  local need=3.31.6 have=""
  if command -v cmake >/dev/null; then
    have=$(cmake --version | awk 'NR==1{print $3}')
    if [[ $(printf '%s\n%s\n' "$need" "$have" | sort -V | head -1) == "$need" ]]; then
      command -v cmake
      return
    fi
  fi
  local venv="${XDG_CACHE_HOME:-$HOME/.cache}/gtopt/cmake-venv"
  local why="CMake not found"
  [[ -n $have ]] && why="CMake $have is older than $need"
  say "$why; installing CMake from PyPI into $venv" >&2
  run python3 -m venv "$venv" >&2
  run "$venv/bin/pip" install -q --upgrade "cmake>=$need" >&2
  echo "$venv/bin/cmake"
}

# HiGHS: apt package where available (Ubuntu 25.04+, Debian 13+),
# otherwise build the release from source into the install prefix.
install_highs() {
  local sudo=$1 cmake=$2 prefix=$3 jobs=$4 tag=v1.12.0
  if apt-cache show libhighs-dev >/dev/null 2>&1; then
    say "Installing HiGHS (apt)"
    # `highs` provides the binary that the libhighs-dev CMake config requires.
    run $sudo apt-get install -y -q --no-install-recommends libhighs-dev highs
    return
  fi
  if [[ -e $prefix/include/highs/Highs.h ]]; then
    say "HiGHS already installed in $prefix"
    return
  fi
  local tmp
  tmp=$(mktemp -d)
  say "Building HiGHS $tag from source (not packaged for this release)"
  run git clone -q --depth 1 --branch "$tag" https://github.com/ERGO-Code/HiGHS.git "$tmp/HiGHS"
  run "$cmake" -S "$tmp/HiGHS" -B "$tmp/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON -DFAST_BUILD=ON -DBUILD_TESTING=OFF \
    -DCMAKE_INSTALL_PREFIX="$prefix"
  run "$cmake" --build "$tmp/build" -j "$jobs"
  run $PSUDO "$cmake" --install "$tmp/build"
  rm -rf "$tmp"
}

# --------------------------------------------------------------------------
# Solver selection
#
# Canonical names: highs cbc scip cuopt cplex gurobi mindopt
# (cbc also stands for CLP/OSI: one plugin serves both).
#
#   default        HiGHS + CBC/CLP, plus every commercial solver found
#                  installed, plus cuOpt when an NVIDIA GPU is present.
#                  Solvers not chosen are left to the build's own probing.
#   --solvers L    exactly the solvers in L; the others are disabled.
#   --solvers all  every open-source solver that applies (HiGHS, CBC/CLP,
#                  SCIP, cuOpt with a GPU) + every commercial one found.
# --without / --no-highs / --no-cuopt remove solvers from default or all.
# --------------------------------------------------------------------------

ALL_SOLVERS="highs cbc scip cuopt cplex gurobi mindopt"
COMMERCIAL="cplex gurobi mindopt"
SOLVERS_ARG="" WITHOUT_ARG="" NO_HIGHS=0 NO_CUOPT=0 DETECT=1
SOLVER_MODE=default         # default | list | all
SELECTED=" "                # space-delimited selected solver names
EXPLICIT=" "                # names listed explicitly with --solvers
CPLEX_DIR="" GUROBI_DIR="" MINDOPT_DIR="" CUOPT_DIR="" CUOPT_PY=""
HAVE_GPU=0 CUOPT_WHEEL=""
# Test hook: prefix for the system paths probed (/opt, /proc, /usr).
_ROOT=${GTOPT_INSTALL_ROOT:-}

want() { [[ $SELECTED == *" $1 "* ]]; }
is_commercial() { [[ " $COMMERCIAL " == *" $1 "* ]]; }

canon_solver() {
  case ${1,,} in
    highs) echo highs ;;
    cbc | clp | osi | coin) echo cbc ;;
    scip) echo scip ;;
    cuopt) echo cuopt ;;
    cplex) echo cplex ;;
    gurobi) echo gurobi ;;
    mindopt) echo mindopt ;;
    *) die "unknown solver '$1' (known: ${ALL_SOLVERS// /, }, all)" ;;
  esac
}

# Parse a comma list into canonical, de-duplicated, space-delimited names.
parse_solver_list() {
  local item out=" " IFS=,
  for item in $1; do
    item=${item// /}
    [[ -n $item ]] || continue
    [[ ${item,,} == all ]] && die "'all' cannot be combined with other solver names"
    item=$(canon_solver "$item") || exit 1
    [[ $out == *" $item "* ]] || out+="$item "
  done
  printf '%s\n' "$out"
}

resolve_solvers() {
  local prefix=$1 without s
  without=$(parse_solver_list "$WITHOUT_ARG") || exit 1
  ((NO_HIGHS)) && without+="highs "
  ((NO_CUOPT)) && without+="cuopt "

  if [[ -n $SOLVERS_ARG ]]; then
    if [[ ${SOLVERS_ARG,,} == all ]]; then
      SOLVER_MODE=all
    else
      SOLVER_MODE=list
      EXPLICIT=$(parse_solver_list "$SOLVERS_ARG") || exit 1
      [[ $EXPLICIT != " " ]] || die "--solvers needs at least one solver name"
      for s in $EXPLICIT; do
        [[ $without == *" $s "* ]] &&
          die "--solvers lists '$s' but --without/--no-$s excludes it"
      done
      [[ -n $WITHOUT_ARG ]] && die "--without only applies to the default selection or --solvers all"
      ((NO_HIGHS)) && die "--no-highs conflicts with --solvers (just leave highs out)"
      ((NO_CUOPT)) && die "--no-cuopt conflicts with --solvers (just leave cuopt out)"
    fi
  fi
  ((NO_HIGHS)) && warn "--no-highs is deprecated; use --without highs or --solvers"

  detect_gpu && HAVE_GPU=1
  CPLEX_DIR=$(find_cplex) || CPLEX_DIR=""
  GUROBI_DIR=$(find_gurobi) || GUROBI_DIR=""
  MINDOPT_DIR=$(find_mindopt) || MINDOPT_DIR=""
  find_cuopt "$prefix" || true

  local -a cand=()
  case $SOLVER_MODE in
    default) cand=(highs cbc cplex gurobi mindopt cuopt) ;;
    all) cand=(highs cbc scip cuopt cplex gurobi mindopt) ;;
    list) read -ra cand <<<"$EXPLICIT" ;;
  esac
  SELECTED=" "
  for s in "${cand[@]}"; do
    [[ $without == *" $s "* ]] && continue
    if is_commercial "$s" && [[ -z $(solver_dir "$s") ]]; then
      [[ $SOLVER_MODE == list ]] && die "$(not_found_msg "$s")"
      continue
    fi
    if [[ $s == cuopt ]] && ((HAVE_GPU == 0)) && [[ -z $CUOPT_DIR ]]; then
      if [[ $SOLVER_MODE == list ]]; then
        warn "cuOpt requested but no NVIDIA GPU was found; skipping cuOpt"
      else
        say "No NVIDIA GPU found; skipping cuOpt" >&2
      fi
      continue
    fi
    SELECTED+="$s "
  done
  [[ $SELECTED != " " ]] || die "no solver left to build (check --solvers/--without)"
  if ((HAVE_GPU)) && want cuopt && [[ -z $CUOPT_DIR ]]; then
    CUOPT_WHEEL=$(cuopt_wheel)
  fi
}

solver_dir() {
  case $1 in
    cplex) echo "$CPLEX_DIR" ;;
    gurobi) echo "$GUROBI_DIR" ;;
    mindopt) echo "$MINDOPT_DIR" ;;
    cuopt) echo "$CUOPT_DIR" ;;
  esac
}

not_found_msg() {
  case $1 in
    cplex) echo "CPLEX requested but not found.  Looked at \$CPLEX_ROOT_DIR, \$CPLEX_STUDIO_DIR*, /opt/ibm/ILOG/CPLEX_Studio*/cplex, ~/opt/ibm/ILOG/CPLEX_Studio*/cplex, ~/ibm/ILOG/CPLEX_Studio*/cplex, /opt/cplex, ~/opt/cplex and 'cplex' on PATH.  Point to it with:  CPLEX_ROOT_DIR=/path/to/CPLEX_Studio*/cplex" ;;
    gurobi) echo "Gurobi requested but not found.  Looked at \$GUROBI_HOME, /opt/gurobi*/linux64, ~/opt/gurobi*/linux64, ~/gurobi*/linux64 and 'gurobi_cl' on PATH.  Point to it with:  GUROBI_HOME=/path/to/gurobi*/linux64" ;;
    mindopt) echo "MindOpt requested but not found.  Looked at \$MINDOPT_HOME, /opt/mindopt/*/linux64-*, ~/opt/mindopt/*/linux64-* and ~/mindopt/*/linux64-*.  Point to it with:  MINDOPT_HOME=/path/to/mindopt/<version>/linux64-x86" ;;
  esac
  ((DETECT)) || echo "(--no-detect: only the environment variable was checked)"
}

# Print "<version>\t<dir>" candidates on stdin; output the newest dir.
newest() { sort -t$'\t' -k1,1V | tail -n 1 | cut -f2; }

# Version of a CPLEX dir without CPLEX_Studio<NNN> in its path, from
# cpxconst.h, in the Studio naming (22.1.1 -> 2211, 12.10 -> 1210) so it
# sorts together with Studio installs.
cplex_header_version() {
  awk '/define CPX_VERSION_VERSION /{a=$3} /define CPX_VERSION_RELEASE /{b=$3}
       /define CPX_VERSION_MODIFICATION /{c=$3}
       END{ if (a != "") printf "%s%s%s\n", a, b, (c > 0 ? c : "") }' \
    "$1/include/ilcplex/cpxconst.h" 2>/dev/null
}

find_cplex() {
  local d v line
  if [[ -n ${CPLEX_ROOT_DIR:-} ]]; then
    [[ -e $CPLEX_ROOT_DIR/include/ilcplex/cplex.h ]] && { echo "$CPLEX_ROOT_DIR"; return; }
    warn "CPLEX_ROOT_DIR=$CPLEX_ROOT_DIR has no include/ilcplex/cplex.h; ignoring it"
  fi
  ((DETECT)) || return 1
  d=$({
    for v in $(compgen -v CPLEX_STUDIO_DIR || true); do
      printf '%s\n' "${!v}/cplex"
    done
    printf '%s\n' "$_ROOT"/opt/ibm/ILOG/CPLEX_Studio*/cplex \
      "$HOME"/opt/ibm/ILOG/CPLEX_Studio*/cplex "$HOME"/ibm/ILOG/CPLEX_Studio*/cplex
    # Flat installs: /opt/cplex or ~/opt/cplex, either the `cplex` dir
    # itself (include/ilcplex inside) or a Studio tree with cplex/ below.
    printf '%s\n' "$_ROOT"/opt/cplex "$_ROOT"/opt/cplex/cplex \
      "$HOME"/opt/cplex "$HOME"/opt/cplex/cplex
    if command -v cplex >/dev/null 2>&1; then
      line=$(readlink -f "$(command -v cplex)")
      dirname "$(dirname "$(dirname "$line")")"
    fi
  } | while read -r line; do
    [[ -e $line/include/ilcplex/cplex.h ]] || continue
    v=$(grep -o 'CPLEX_Studio[0-9]*' <<<"$line" | tail -n 1)
    v=${v#CPLEX_Studio}
    [[ -n $v ]] || v=$(cplex_header_version "$line")
    printf '%s\t%s\n' "$v" "$line"
  done | newest)
  [[ -n $d ]] && echo "$d"
}

find_gurobi() {
  local d v line
  if [[ -n ${GUROBI_HOME:-} ]]; then
    [[ -e $GUROBI_HOME/include/gurobi_c.h ]] && { echo "$GUROBI_HOME"; return; }
    warn "GUROBI_HOME=$GUROBI_HOME has no include/gurobi_c.h; ignoring it"
  fi
  ((DETECT)) || return 1
  d=$({
    printf '%s\n' "$_ROOT"/opt/gurobi*/linux64 "$HOME"/opt/gurobi*/linux64 \
      "$HOME"/gurobi*/linux64
    if command -v gurobi_cl >/dev/null 2>&1; then
      line=$(readlink -f "$(command -v gurobi_cl)")
      dirname "$(dirname "$line")"
    fi
  } | while read -r line; do
    [[ -e $line/include/gurobi_c.h ]] || continue
    v=$(grep -o 'gurobi[0-9]*' <<<"$line" | tail -n 1)
    printf '%s\t%s\n' "${v#gurobi}" "$line"
  done | newest)
  [[ -n $d ]] && echo "$d"
}

find_mindopt() {
  local d line
  if [[ -n ${MINDOPT_HOME:-} ]]; then
    [[ -e $MINDOPT_HOME/include/Mindopt.h ]] && { echo "$MINDOPT_HOME"; return; }
    warn "MINDOPT_HOME=$MINDOPT_HOME has no include/Mindopt.h; ignoring it"
  fi
  ((DETECT)) || return 1
  d=$(printf '%s\n' "$_ROOT"/opt/mindopt/*/linux64-* \
    "$HOME"/opt/mindopt/*/linux64-* "$HOME"/mindopt/*/linux64-* |
    while read -r line; do
      [[ -e $line/include/Mindopt.h ]] || continue
      printf '%s\t%s\n' "$(basename "$(dirname "$line")")" "$line"
    done | newest)
  [[ -n $d ]] && echo "$d"
}

detect_gpu() {
  if command -v nvidia-smi >/dev/null 2>&1 &&
    nvidia-smi -L 2>/dev/null | grep -q '^GPU'; then
    return 0
  fi
  compgen -G "$_ROOT/proc/driver/nvidia/gpus/*" >/dev/null
}

# libcuopt-cu13 needs a driver for CUDA 13; older drivers get cu12.
cuopt_wheel() {
  local cuda
  cuda=$(nvidia-smi 2>/dev/null | grep -o 'CUDA Version: *[0-9]*' |
    grep -o '[0-9]*$' | head -n 1)
  if [[ -n $cuda ]] && ((cuda >= 13)); then echo libcuopt-cu13; else echo libcuopt-cu12; fi
}

# >= 26.8: first C API the plugin supports.  From 26.10 the headers and
# the C API live in the libcuopt_mathopt wheel (pulled in by libcuopt);
# FindCuOpt.cmake searches both layouts.
CUOPT_SPEC=">=26.8"
cuopt_venv() { echo "$1/lib/gtopt/cuopt"; }

# Sets CUOPT_DIR (root with include/ + lib64/) and CUOPT_PY (a python that
# imports libcuopt, used by the build to read the wheel version).
# cuOpt >= 26.10 keeps cuopt_c.h in libcuopt_mathopt, <= 26.08 in libcuopt.
find_cuopt() {
  local prefix=$1 py d m
  local hdr=include/cuopt/mathematical_optimization/cuopt_c.h
  if [[ -n ${CUOPT_HOME:-} ]]; then
    [[ -e $CUOPT_HOME/$hdr ]] && { CUOPT_DIR=$CUOPT_HOME; return 0; }
    warn "CUOPT_HOME=$CUOPT_HOME has no $hdr; ignoring it"
  fi
  for py in "$(cuopt_venv "$prefix")/bin/python3" python3; do
    command -v "$py" >/dev/null 2>&1 || continue
    for m in libcuopt_mathopt libcuopt; do
      d=$("$py" -c "import $m, os; print(os.path.dirname($m.__file__))" 2>/dev/null) || continue
      [[ -e $d/$hdr ]] || continue
      CUOPT_DIR=$d CUOPT_PY=$(command -v "$py")
      return 0
    done
  done
  return 1
}

print_solver_plan() {
  local s line
  say "Solvers ($SOLVER_MODE):"
  for s in $ALL_SOLVERS; do
    want "$s" || continue
    case $s in
      highs) line="HiGHS        open source; installed if missing" ;;
      cbc) line="CBC/CLP      open source (apt coinor-libcbc-dev)" ;;
      scip) line="SCIP         open source; installed if missing (scipopt .deb)" ;;
      cuopt)
        if [[ -n $CUOPT_DIR ]]; then line="cuOpt        found: $CUOPT_DIR"
        else line="cuOpt        NVIDIA GPU found; will pip-install $CUOPT_WHEEL$CUOPT_SPEC into $(cuopt_venv "${prefix:-/usr/local}")"; fi ;;
      cplex) line="CPLEX        found: $CPLEX_DIR  (CPLEX_ROOT_DIR)" ;;
      gurobi) line="Gurobi       found: $GUROBI_DIR  (GUROBI_HOME)" ;;
      mindopt) line="MindOpt      found: $MINDOPT_DIR  (MINDOPT_HOME)" ;;
    esac
    printf '  %s\n' "$line"
  done
  if [[ $SOLVER_MODE == list ]]; then
    local off=""
    for s in $ALL_SOLVERS; do want "$s" || off+="$s "; done
    [[ -n $off ]] && printf '  disabled: %s\n' "$off"
  fi
  ((DETECT)) || printf '  (--no-detect: commercial solvers only via their env variables)\n'
}

# CMake arguments/environment for the selected solvers (fills the caller's
# solver_args / solver_env arrays).  In --solvers mode the others are
# disabled with CMAKE_DISABLE_FIND_PACKAGE_<Pkg> / GTOPT_DISABLE_*; every
# switch is always passed so a re-run overrides the cached value.
solver_cmake_args() {
  local s off pkg
  want cplex && solver_args+=("-DCPLEX_ROOT_DIR=$CPLEX_DIR")
  want gurobi && solver_env+=("GUROBI_HOME=$GUROBI_DIR")
  want mindopt && solver_env+=("MINDOPT_HOME=$MINDOPT_DIR")
  [[ -n ${SCIP_ROOT_DIR:-} ]] && solver_args+=("-DSCIP_ROOT_DIR=$SCIP_ROOT_DIR")
  if want cuopt && [[ -n $CUOPT_DIR ]]; then
    solver_env+=("CUOPT_HOME=$CUOPT_DIR")
    [[ -n $CUOPT_PY ]] && solver_args+=("-D_CUOPT_PYTHON=$CUOPT_PY")
  fi
  for s in $ALL_SOLVERS; do
    off=FALSE
    if ! want "$s" && { [[ $SOLVER_MODE == list ]] || is_without "$s"; }; then
      off=TRUE
    fi
    for pkg in $(cmake_packages "$s"); do
      solver_args+=("-DCMAKE_DISABLE_FIND_PACKAGE_$pkg=$off")
    done
    case $s in
      scip) solver_args+=("-DGTOPT_DISABLE_SCIP=$([[ $off == TRUE ]] && echo ON || echo OFF)") ;;
      cuopt) solver_args+=("-DGTOPT_DISABLE_CUOPT=$([[ $off == TRUE ]] && echo ON || echo OFF)") ;;
    esac
  done
}

is_without() {
  local w
  w=$(parse_solver_list "$WITHOUT_ARG")
  ((NO_HIGHS)) && w+="highs "
  ((NO_CUOPT)) && w+="cuopt "
  [[ $w == *" $1 "* ]]
}

cmake_packages() {
  case $1 in
    highs) echo "HiGHS highs" ;;
    cbc) echo COIN ;;
    scip) echo SCIP ;;
    cuopt) echo CuOpt ;;
    cplex) echo CPLEX ;;
    gurobi) echo Gurobi ;;
    mindopt) echo MindOpt ;;
  esac
}

plugin_file() {
  case $1 in
    cbc) echo libgtopt_solver_osi.so ;;
    *) echo "libgtopt_solver_$1.so" ;;
  esac
}

# A re-run with a smaller --solvers list must not leave plugins from an
# earlier run behind in the prefix.
prune_plugins() {
  local prefix=$1 psudo=$2 s f
  [[ $SOLVER_MODE == list ]] || return 0
  for s in $ALL_SOLVERS; do
    want "$s" && continue
    f="$prefix/lib/gtopt/plugins/$(plugin_file "$s")"
    if ((DRY)) || [[ -e $f ]]; then
      run $psudo rm -f "$f"
    fi
  done
}

# SCIP: the official SCIP Opt Suite .deb for this release (scipopt.org).
SCIP_VERSION=10.1.0
install_scip() {
  local sudo=$1 codename arch deb tmp
  if [[ -n ${SCIP_ROOT_DIR:-} ]] || dpkg -s scipoptsuite >/dev/null 2>&1 ||
    compgen -G "/usr/lib/cmake/scip/*onfig.cmake" >/dev/null ||
    compgen -G "/usr/local/lib/cmake/scip/*onfig.cmake" >/dev/null; then
    say "SCIP already installed"
    return
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  codename=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}
  case $(uname -m) in
    x86_64) arch=amd64 ;;
    aarch64) arch=aarch64 ;;
    *) arch=$(uname -m) ;;
  esac
  deb="scipoptsuite_${SCIP_VERSION}-1+${codename}_${arch}.deb"
  local url="https://github.com/scipopt/scip/releases/download/v${SCIP_VERSION}/$deb"
  say "Installing SCIP $SCIP_VERSION ($deb)"
  if ((DRY)); then
    run wget -q -O "/tmp/$deb" "$url"
    run $sudo apt-get install -y -q "/tmp/$deb"
    return
  fi
  tmp=$(mktemp -d)
  chmod 755 "$tmp"   # apt's _apt sandbox user must read the .deb
  if ! wget -q -O "$tmp/$deb" "$url"; then
    rm -rf "$tmp"
    local msg="no SCIP $SCIP_VERSION package for '$codename/$arch' ($url)"
    [[ $SOLVER_MODE == list ]] && die "$msg; install SCIP by hand and set SCIP_ROOT_DIR"
    warn "$msg; skipping SCIP"
    SELECTED=${SELECTED/ scip / }
    return
  fi
  $sudo apt-get install -y -q "$tmp/$deb"
  rm -rf "$tmp"
}

# cuOpt (NVIDIA, Apache-2.0): the libcuopt wheel from PyPI in a dedicated
# venv under the prefix; the build links its stable C API.
install_cuopt() {
  local prefix=$2 venv psudo=$PSUDO
  venv=$(cuopt_venv "$prefix")
  say "Installing NVIDIA cuOpt ($CUOPT_WHEEL$CUOPT_SPEC) into $venv"
  if ((DRY)); then
    run $psudo python3 -m venv "$venv"
    run $psudo "$venv/bin/pip" install -q "$CUOPT_WHEEL$CUOPT_SPEC"
    CUOPT_DIR="$venv/lib/python3/site-packages/libcuopt" CUOPT_PY="$venv/bin/python3"
    return
  fi
  if $psudo mkdir -p "$(dirname "$venv")" && $psudo python3 -m venv "$venv" &&
    $psudo "$venv/bin/pip" install -q --upgrade pip &&
    $psudo "$venv/bin/pip" install -q "$CUOPT_WHEEL$CUOPT_SPEC" &&
    find_cuopt "$prefix"; then
    say "cuOpt installed: $CUOPT_DIR"
  else
    [[ $SOLVER_MODE == list ]] && die "installing cuOpt failed (see above)"
    warn "installing cuOpt failed; building without it"
    SELECTED=${SELECTED/ cuopt / }
  fi
}

pick_cc() {
  if command -v "$1-14" >/dev/null; then echo "$1-14"; else echo "$1"; fi
}

fetch_source() {
  local repo=$1 ref=$2 src=$3
  if [[ -d $src/.git ]]; then
    say "Updating source in $src"
    run git -C "$src" fetch -q --tags origin "$ref"
    run git -C "$src" checkout -q FETCH_HEAD
  else
    say "Cloning $repo ($ref) into $src"
    run mkdir -p "$(dirname "$src")"
    run git clone -q --filter=blob:none "$repo" "$src"
    run git -C "$src" checkout -q "$ref"
  fi
}

install_python() {
  local src=$1
  local venv="${XDG_DATA_HOME:-$HOME/.local/share}/gtopt/venv"
  local bindir="$HOME/.local/bin"
  say "Installing the Python tools into $venv"
  run python3 -m venv "$venv"
  run "$venv/bin/pip" install -q --upgrade pip
  run "$venv/bin/pip" install -q "$src/scripts"
  run mkdir -p "$bindir"
  if ((DRY == 0)); then
    local exe n=0
    for exe in "$venv"/bin/*; do
      case $(basename "$exe") in
        python* | pip* | activate* | Activate* | *.py | cmake* | ctest | cpack) continue ;;
      esac
      if head -c 200 "$exe" 2>/dev/null | grep -q "$venv"; then
        ln -sf "$exe" "$bindir/$(basename "$exe")"
        n=$((n + 1))
      fi
    done
    say "Linked $n Python commands into $bindir"
  fi
}

DRY=0 PSUDO=""
# GTOPT_INSTALL_NO_MAIN=1: define the functions only (used by the tests).
[[ -n ${GTOPT_INSTALL_NO_MAIN:-} ]] || main "$@"
