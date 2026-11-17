#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# GTOpt installer — builds GTOpt from source and installs it.
#
#   curl -fsSL https://raw.githubusercontent.com/gtopt-community/gtopt/master/install.sh | bash
#
# With options (anything after `-s --`):
#
#   curl -fsSL .../install.sh | bash -s -- --prefix "$HOME/.local" --python
#
# Options:
#   --prefix DIR   install prefix (default: /usr/local; uses sudo if needed)
#   --ref REF      git branch, tag or commit to build (default: master)
#   --src DIR      where to keep the source checkout
#                  (default: ~/.cache/gtopt/src)
#   --repo URL     git repository (default: the gtopt-community repo)
#   --python       also install the Python tools (plp2gtopt, plexos2gtopt,
#                  gtopt_diagram, run_gtopt, ...) in ~/.local/share/gtopt/venv
#                  and link them into ~/.local/bin
#   --no-highs     do not install the HiGHS solver (CBC/CLP is always installed)
#   --no-deps      do not install system packages or HiGHS (you install them)
#   --jobs N       parallel build jobs (default: number of CPUs)
#   --dry-run      print what would be done, change nothing
#   -h, --help     show this help
#
# Supported: Ubuntu 24.04 or newer and Debian 13 or newer (x86_64, arm64).
# Other platforms: see BUILDING.md.
#
# Solvers: the open-source CBC/CLP and HiGHS are installed by default.
# Commercial solvers are built in when they are already installed and
# visible to the build; pass their location to bash, not to curl:
#
#   curl -fsSL .../install.sh | CPLEX_ROOT_DIR=/opt/ibm/ILOG/CPLEX_Studio2211/cplex bash
#   curl -fsSL .../install.sh | GUROBI_HOME=/opt/gurobi1203/linux64 bash
#   curl -fsSL .../install.sh | MINDOPT_HOME=/opt/mindopt/2.3.0/linux64-x86 bash
#
# SCIP (SCIP_ROOT_DIR) and NVIDIA cuOpt (CUOPT_HOME) work the same way.

set -euo pipefail

# Everything runs inside main() so a partially downloaded script does nothing.
main() {
  local prefix=/usr/local ref=master python=0 deps=1 dry=0 highs=1
  local src="${XDG_CACHE_HOME:-$HOME/.cache}/gtopt/src"
  local repo=https://github.com/gtopt-community/gtopt.git
  local jobs
  jobs=$(nproc 2>/dev/null || echo 4)

  while (($#)); do
    case $1 in
      --prefix) prefix=$2; shift 2 ;;
      --ref) ref=$2; shift 2 ;;
      --src) src=$2; shift 2 ;;
      --repo) repo=$2; shift 2 ;;
      --python) python=1; shift ;;
      --no-deps) deps=0; shift ;;
      --no-highs) highs=0; shift ;;
      --jobs) jobs=$2; shift 2 ;;
      --dry-run) dry=1; shift ;;
      -h | --help) sed -n '3,38p' "${BASH_SOURCE[0]:-/dev/stdin}" 2>/dev/null |
          sed 's/^# \{0,1\}//' || true; return 0 ;;
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

  say "GTOpt installer: ref=$ref prefix=$prefix"
  ((deps)) && install_deps "$sudo"

  local cmake
  cmake=$(find_cmake)

  ((deps && highs)) && install_highs "$sudo" "$cmake" "$prefix" "$jobs"

  fetch_source "$repo" "$ref" "$src"

  local build="$src/build-install"
  say "Configuring (Release)"
  local -a solver_args=()
  [[ -n ${CPLEX_ROOT_DIR:-} ]] && solver_args+=("-DCPLEX_ROOT_DIR=$CPLEX_ROOT_DIR")
  [[ -n ${SCIP_ROOT_DIR:-} ]] && solver_args+=("-DSCIP_ROOT_DIR=$SCIP_ROOT_DIR")
  run env CC="$(pick_cc gcc)" CXX="$(pick_cc g++)" \
    "$cmake" -S "$src/standalone" -B "$build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_PREFIX_PATH="$prefix" "${solver_args[@]}"
  say "Building with $jobs jobs (this takes a while the first time)"
  run "$cmake" --build "$build" -j "$jobs"

  say "Installing into $prefix"
  if ((DRY)) || [[ -w $prefix ]] ||
    { [[ ! -e $prefix ]] && mkdir -p "$prefix" 2>/dev/null; }; then
    run "$cmake" --install "$build"
  else
    run $sudo "$cmake" --install "$build"
  fi

  ((python)) && install_python "$src"

  if ((DRY == 0)); then
    local bin="$prefix/bin/gtopt"
    say "Installed: $("$bin" --version 2>/dev/null | head -1)"
    say "Solver backends:"
    "$bin" --solvers 2>/dev/null | sed 's/^/  /' || true
    echo "  Commercial solvers (CPLEX, Gurobi, MindOpt) and SCIP/cuOpt are added by"
    echo "  re-running this installer with their location set; see --help."
    case ":$PATH:" in *":$prefix/bin:"*) ;; *)
      warn "$prefix/bin is not in your PATH; add it to use 'gtopt'." ;;
    esac
  fi
  say "Done. Try:  gtopt --help   (docs: https://github.com/gtopt-community/gtopt/tree/master/docs)"
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
  run $sudo apt-get update -q
  run $sudo apt-get install -y -q --no-install-recommends \
    git ca-certificates wget lsb-release gnupg pkg-config ninja-build lld \
    gcc-14 g++-14 python3 python3-venv \
    libboost-container-dev zlib1g-dev libzstd-dev liblz4-dev libsnappy-dev \
    coinor-libcbc-dev

  if ! dpkg -s libarrow-dev libparquet-dev >/dev/null 2>&1; then
    say "Adding the Apache Arrow apt repository"
    local id codename deb tmp
    id=$(lsb_release --id --short | tr '[:upper:]' '[:lower:]')
    codename=$(lsb_release --codename --short)
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
  say "CMake ${have:-not found} is older than $need; installing CMake from PyPI into $venv" >&2
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
    run $sudo apt-get install -y -q --no-install-recommends libhighs-dev
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
  if ((DRY)) || [[ -w $prefix ]]; then
    run "$cmake" --install "$tmp/build"
  else
    run $sudo "$cmake" --install "$tmp/build"
  fi
  rm -rf "$tmp"
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

DRY=0
main "$@"
