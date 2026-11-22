#!/usr/bin/env bash
# setup_sandbox.sh — Bootstrap a fresh Ubuntu 24.04 sandbox for building gtopt
#
# Usage:
#   bash tools/setup_sandbox.sh [OPTIONS]
#
# Options:
#   --no-python      Skip pre-installing Python scripts dependencies
#   --configure      Also run cmake configure after deps are installed
#   --build          Also run cmake --build after configure (implies --configure)
#   --build-type T   CMake build type: CIFast (default) | Debug | Release | RelWithDebInfo
#   --debug          Shortcut for --build-type Debug (full symbols, asserts)
#   --no-tidy-db     Skip creating the clang-tidy compile DB (build-tidy/)
#                    (--no-save-ccjson is accepted as an old alias)
#   --help           Show this help and exit
#   --check          Dependency check only: install nothing, report every
#                    missing tool with the exact command that installs it
#                    (exit 1 when a required one is missing)
#   --with-docs      Also install the docs toolchain: Doxygen (+ Pygments
#                    for m.css) and TeX Live for the white paper
#                    (latexmk, pdflatex, bibtex, IEEEtran, siunitx, ...)
#   --with-julia     Also install Julia (juliaup) + the packages of the
#                    tools/*.jl golden generators (optional; CPLEX.jl not
#                    installed -- it needs a licensed CPLEX)
#
# Dependency groups (see also BUILDING.md):
#   required  compilers (Clang 21 / GCC 14 fallback), clang-format-21,
#             clang-tidy-21 + clang-tidy-22 (CI clang-tidy job), ccache,
#             lld, ninja, COIN-OR CBC/CLP, Arrow/Parquet, Boost, spdlog,
#             LAPACK/BLAS, jemalloc, zlib/zstd/lz4/snappy, lcov,
#             Graphviz (dot/neato: gtopt_diagram tests), cairo
#             (cairosvg), mdbtools (cen2gtopt / plexos2gtopt .accdb),
#             uv, Python scripts[dev,lint] (ruff, pylint, mypy, ty,
#             pytest-cov, pytest-xdist, actionlint, shellcheck,
#             pre-commit), ctcache (tools/install_ctcache.sh)
#   optional  --with-docs, --with-julia; commercial / GPU LP solvers
#             (CPLEX, Gurobi, MindOpt, cuOpt) and SCIP/HiGHS plugins are
#             never auto-installed -- see BUILDING.md.
#
# Build-type rationale:
#   CIFast is -O0 -g1 with gc-sections — no optimisation (fast compile) and
#   only minimal line-number debug info (backtraces still work on test
#   failures).  This is what CI and agent-driven iteration want: a pass/fail
#   signal plus a readable stack trace.  Reconfigure with --debug when you
#   actually need to step through code in gdb.
#
# What this script does (in the same order as .github/workflows/ubuntu.yml):
#   1. Install ccache + base APT packages (COIN-OR, Boost, spdlog, LAPACK, etc.)
#   2-3. Install Arrow/Parquet (conda) AND Clang 21 compiler IN PARALLEL.
#      If the LLVM APT repository is unavailable, fall back to GCC 14.
#   4. Pre-install Python scripts dev dependencies (speeds up CTest fixture).
#   5. (Optional) cmake configure — uses whichever compiler was installed.
#   6. (Optional) cmake --build + ctest.
#   7. (With --configure, Clang only) Create the clang-tidy compile DB in
#      build-tidy/ (configure-only, one entry per file, tests included):
#        tools/run_tidy.sh --configure
#      tools/run_tidy.sh then finds it automatically.  Use --no-tidy-db
#      to skip this step.
#
# Environment:
#   REPO_ROOT   Path to the repository root (default: directory containing this
#               script's parent, i.e. the repo root when called as
#               bash tools/setup_sandbox.sh from the repo root).
#
# Notes:
#   • Run from the repository root: bash tools/setup_sandbox.sh
#   • Idempotent: safe to run more than once; already-installed packages are
#     skipped automatically by apt-get / conda.
#   • Clang 21 is the preferred compiler (same as CI).  If the LLVM APT
#     repository is unreachable, GCC 14 is used as a fallback.  Both produce
#     a fully working build; the summary at the end reports which was chosen.
#   • clang-22 packages are not yet available on apt.llvm.org; use version 21.
#   • The clang-tidy DB lives in build-tidy/ (git-ignored, machine-local
#     paths); run the pre-commit tidy step with tools/run_tidy.sh.


set -euo pipefail

# ── Resolve repo root ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(dirname "$SCRIPT_DIR")}"
cd "$REPO_ROOT"

# ── Defaults ──────────────────────────────────────────────────────────────────
INSTALL_PYTHON=true
CHECK_ONLY=false
WITH_DOCS=false
WITH_JULIA=false
DO_CONFIGURE=false
DO_BUILD=false
BUILD_TYPE=CIFast
CLANG_VERSION=21
TIDY_DB=true

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)        CHECK_ONLY=true ;;
    --with-docs)    WITH_DOCS=true ;;
    --with-julia)   WITH_JULIA=true ;;
    --no-python)    INSTALL_PYTHON=false ;;
    --configure)    DO_CONFIGURE=true ;;
    --build)        DO_CONFIGURE=true; DO_BUILD=true ;;
    --build-type)   shift; BUILD_TYPE="$1" ;;
    --debug)        BUILD_TYPE=Debug ;;
    --no-tidy-db|--no-save-ccjson) TIDY_DB=false ;;
    --help|-h)
      sed -n '2,/^# Notes:/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

log()  { echo "▶ $*"; }
ok()   { echo "✓ $*"; }
warn() { echo "⚠ $*" >&2; }

# ── Detect OS codename (needed for LLVM APT) ──────────────────────────────────
CODENAME=$(lsb_release --codename --short 2>/dev/null || \
           awk -F= '/^VERSION_CODENAME/{gsub(/["[:space:]]/, "", $2); print $2}' \
             /etc/os-release)
if [[ -z "$CODENAME" ]]; then
  echo "ERROR: could not determine OS codename (lsb_release and /etc/os-release failed)." >&2
  exit 1
fi

# ── Dependency lists (single source for install AND --check) ─────────────────
# Mirrors .github/actions/install-apt-deps plus what the Python test
# suite needs at runtime (graphviz, cairo, mdbtools).
APT_BASE=(
  ccache lld ninja-build git
  coinor-libcbc-dev
  libboost-container-dev libspdlog-dev
  liblapack-dev libblas-dev
  libjemalloc-dev
  zlib1g-dev libzstd-dev zstd liblz4-dev lz4 libsnappy-dev
  lcov
  graphviz libcairo2-dev
  mdbtools
  ca-certificates lsb-release wget curl
)
# White paper (docs/white_paper/main.tex \usepackage set) + Doxygen.
APT_DOCS=(
  doxygen latexmk
  texlive-latex-base texlive-latex-recommended texlive-latex-extra
  texlive-science texlive-publishers texlive-pictures
  texlive-fonts-recommended
)
# Graphviz >= 14 (Ubuntu 26.04+) ships the neato layout engine -- the
# gtopt_diagram default -- as a separate plugin package; without it the
# `neato` binary exists but every -Kneato render fails.
if apt-cache show libgvplugin-neato-layout8 &>/dev/null; then
  APT_BASE+=(libgvplugin-neato-layout8)
fi
PIP_DOCS=(Pygments jinja2)
# Standalone Python CLIs: `uv tool install` (one isolated env per tool,
# shims in ~/.local/bin -- works where the system Python has no pip).
UV_TOOLS=(ruff ty==0.0.107 actionlint-py shellcheck-py pre-commit)
JULIA_PKGS='["JSON","JuMP","HiGHS","DataFrames","UnitCommitment","PowerSystems","PowerSimulations","PowerSystemCaseBuilder","HydroPowerSimulations","StorageSystemsSimulations"]'
CLANG_TIDY_NEXT=22   # clang-tidy.yml TIDY_VERSION

# ── --check: report only, install nothing ────────────────────────────────────
if $CHECK_ONLY; then
  missing_apt=() missing_other=() req_missing=0
  apt_ok() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null \
               | grep -q 'install ok installed'; }
  need_cmd() {  # need_cmd <required|optional> <cmd> <how-to-install>
    if command -v "$2" &>/dev/null; then ok "$2"; return; fi
    warn "missing ($1): $2  -> $3"
    if [[ "$1" == required ]]; then
      missing_other+=("$2: $3")
      req_missing=1
    fi
    return 0
  }
  echo "== APT packages (required)"
  for p in "${APT_BASE[@]}"; do
    apt_ok "$p" || { missing_apt+=("$p"); req_missing=1; }
  done
  if apt_ok libarrow-dev && apt_ok libparquet-dev; then
    ok "Arrow/Parquet (APT)"
  elif command -v conda &>/dev/null && \
       find "$(conda info --base)" -name ArrowConfig.cmake -print -quit \
         2>/dev/null | grep -q .; then
    ok "Arrow/Parquet (conda)"
  else
    missing_other+=("Arrow/Parquet: APT (BUILDING.md) or conda install -c conda-forge libarrow libparquet")
    req_missing=1
  fi
  echo "== Tools (required)"
  if ! command -v "clang-${CLANG_VERSION}" &>/dev/null && \
     ! command -v g++-14 &>/dev/null; then
    missing_other+=("clang-${CLANG_VERSION} (or g++-14): bash tools/setup_sandbox.sh")
    req_missing=1
  fi
  for t in "clang-format-${CLANG_VERSION}" "clang-tidy-${CLANG_VERSION}" \
           "clang-tidy-${CLANG_TIDY_NEXT}"; do
    need_cmd required "$t" "apt.llvm.org llvm-toolchain-${CODENAME}-${t##*-} (bash tools/setup_sandbox.sh)"
  done
  for t in cmake dot mdb-export python3; do
    need_cmd required "$t" "see the APT list above"
  done
  need_cmd required uv "curl -LsSf https://astral.sh/uv/install.sh | sh"
  if command -v dot &>/dev/null; then
    if echo 'graph { a }' | dot -Kneato -Tsvg > /dev/null 2>&1; then
      ok "graphviz neato layout engine"
    else
      [[ " ${missing_apt[*]} " == *" libgvplugin-neato-layout8 "* ]] \
        || missing_apt+=(libgvplugin-neato-layout8)
      req_missing=1
      warn "missing (required): graphviz neato layout engine"
    fi
  fi
  need_cmd required ruff "uv tool install ruff  (fallback: pip install --user ruff)"
  need_cmd required ty "uv tool install ty==0.0.107  (pin: scripts/pyproject.toml)"
  need_cmd required actionlint "uv tool install actionlint-py"
  need_cmd required shellcheck "uv tool install shellcheck-py"
  need_cmd required pre-commit "uv tool install pre-commit"
  for t in pylint mypy pytest; do
    need_cmd required "$t" 'uv pip install -e "./scripts[dev]" (needs the scripts deps in the same env)'
  done
  ctc="${CTCACHE_HOME:-$HOME/.local/opt/ctcache}"
  if [[ -x "$ctc/clang-tidy" || -x "$HOME/opt/ctcache/clang-tidy" ]]; then
    ok "ctcache"
  else
    missing_other+=("ctcache: bash tools/install_ctcache.sh")
    req_missing=1
  fi
  echo "== Optional: --with-docs"
  for p in "${APT_DOCS[@]}"; do apt_ok "$p" || warn "missing (optional docs): $p"; done
  command -v tectonic &>/dev/null && ok "tectonic (white paper, CI engine)"
  echo "== Optional: --with-julia"
  need_cmd optional julia "bash tools/setup_sandbox.sh --with-julia"
  echo "== Optional: LP solvers (never auto-installed)"
  for t in highs cbc clp cplex gurobi_cl scip; do
    command -v "$t" &>/dev/null && ok "$t" || echo "  - $t not on PATH"
  done
  echo ""
  if ((${#missing_apt[@]})); then
    echo "Install missing required APT packages with:"
    echo "  sudo apt-get install -y --no-install-recommends ${missing_apt[*]}"
  fi
  for m in "${missing_other[@]}"; do echo "  * $m"; done
  if ((req_missing)); then
    echo "RESULT: required dependencies missing"; exit 1
  fi
  echo "RESULT: all required dependencies present"; exit 0
fi

# ── Step 1: ccache + base APT packages ────────────────────────────────────────
# ccache MUST be installed before cmake configure; cmake bakes the launcher
# path into the build system at configure time.  Installing it later requires
# deleting the build directory and reconfiguring from scratch.
log "Installing ccache and base APT packages..."
sudo apt-get update -q
sudo apt-get install -y --no-install-recommends "${APT_BASE[@]}"
ok "ccache and base packages installed"

# ccache sizing: one full CIFast build is ~0.2 GB, but each worktree /
# build type / compiler adds its own set and the 5 GB default thrashes
# with several concurrent builds.  Only raise the limit while it is
# still the built-in default (a user setting wins).  base_dir /
# hash_dir / sloppiness are passed per invocation by
# cmake_local/Ccache.cmake; keep the cache dir on local disk (export
# CCACHE_DIR per host when $HOME is an NFS mount).
if ccache -p 2>/dev/null | grep -q '^(default) max_size'; then
  ccache --max-size=30G > /dev/null && ok "ccache max_size set to 30G"
fi

# ── Steps 2-3: Arrow/Parquet + Compiler (in parallel) ────────────────────────
# These two installations are independent and can run concurrently, saving
# ~1-2 minutes on fresh setups.

# --- Arrow / Parquet via conda-forge ---
install_arrow() {
  # Always use conda-forge.  The APT Arrow packages (packages.apache.org) are
  # NOT used in sandbox/agent environments because the APT libarrow version can
  # conflict with the conda libarrow at link time.
  # Conda gives a self-consistent Arrow+Parquet+Boost set that always matches.

  # Ensure conda itself works.  Some sandbox images ship a broken certifi
  # package (ImportError: cannot import name 'where') or a libmamba solver
  # that cannot load.  Fix both before proceeding.
  if command -v conda &>/dev/null; then
    if ! python3 -c "import certifi; certifi.where()" &>/dev/null; then
      warn "certifi is broken in conda's Python -- reinstalling..."
      pip install certifi --force-reinstall --upgrade --quiet 2>/dev/null || true
    fi
    if ! conda info --base &>/dev/null 2>&1; then
      warn "conda default solver is broken -- switching to classic solver"
      conda config --set solver classic 2>/dev/null || true
    fi
  fi

  # Check that the *real* Arrow library is installed (not just the dummy
  # arrow-cpp transitional package v0.2.post).
  local CONDA_PREFIX_DIR
  CONDA_PREFIX_DIR="$(conda info --base 2>/dev/null || echo "")"
  if [[ -n "$CONDA_PREFIX_DIR" ]] && \
     find "$CONDA_PREFIX_DIR" -name "ArrowConfig.cmake" -print -quit 2>/dev/null | grep -q .; then
    ok "Arrow/Parquet already installed via conda (ArrowConfig.cmake found)"
    return 0
  fi

  if ! command -v conda &>/dev/null; then
    echo "ERROR: conda not found.  Install Miniconda/Anaconda first." >&2
    return 1
  fi
  log "Installing real Arrow/Parquet libraries via conda-forge..."
  conda remove -y --force arrow-cpp parquet-cpp 2>/dev/null || true
  if ! conda install -y -c conda-forge libarrow libparquet boost-cpp 2>/dev/null; then
    warn "conda install failed with default solver -- retrying with classic solver..."
    conda install -y --solver=classic -c conda-forge libarrow libparquet boost-cpp
  fi
  ok "Arrow/Parquet installed via conda-forge"
}

# --- Compiler: Clang 21 preferred, GCC 14 fallback ---
install_compiler() {
  local VER=$CLANG_VERSION

  if (
    set -e

    for attempt in 1 2 3; do
      wget -qO /tmp/llvm-snapshot.gpg.key \
        https://apt.llvm.org/llvm-snapshot.gpg.key && break
      echo "Attempt ${attempt}/3: wget gpg key failed; retrying in 15 s..." >&2
      if [[ $attempt -lt 3 ]]; then sleep 15; else exit 1; fi
    done

    sudo gpg --yes --dearmor -o /usr/share/keyrings/llvm-snapshot.gpg \
      /tmp/llvm-snapshot.gpg.key

    echo "deb [signed-by=/usr/share/keyrings/llvm-snapshot.gpg] \
    https://apt.llvm.org/${CODENAME}/ llvm-toolchain-${CODENAME}-${VER} main" \
      | sudo tee /etc/apt/sources.list.d/llvm-${VER}.list

    for attempt in 1 2 3; do
      sudo apt-get update -q \
        -o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/llvm-${VER}.list" \
        -o "Dir::Etc::sourceparts=-" && break
      echo "Attempt ${attempt}/3: apt-get update failed; retrying in 15 s..." >&2
      if [[ $attempt -lt 3 ]]; then sleep 15; else exit 1; fi
    done

    sudo apt-get install -y --no-install-recommends \
      clang-${VER} clang-tools-${VER} clang-format-${VER} clang-tidy-${VER} \
      lld-${VER} \
      llvm-${VER}-dev llvm-${VER}-tools libomp-${VER}-dev \
      libc++-${VER}-dev libc++abi-${VER}-dev \
      libclang-common-${VER}-dev libclang-${VER}-dev libclang-cpp${VER}-dev
  ); then
    for versioned in /usr/bin/clang*-"${VER}" /usr/bin/llvm*-"${VER}" /usr/bin/lld-"${VER}" /usr/bin/ld.lld-"${VER}" /usr/bin/wasm-ld-"${VER}" /usr/bin/lld-link-"${VER}"; do
      [ -e "$versioned" ] || continue
      base=$(basename "$versioned" "-${VER}")
      sudo update-alternatives --remove-all "$base" 2>/dev/null || true
      sudo update-alternatives --install /usr/bin/"$base" "$base" \
        "$versioned" 100
    done
    ok "Clang ${VER} installed and registered as default 'clang'/'clang++'"
    echo "clang" > /tmp/_sandbox_compiler
  else
    warn "Clang ${VER} installation failed (LLVM APT repo unreachable?)."
    warn "Falling back to GCC 14."
    sudo apt-get install -y --no-install-recommends gcc-14 g++-14
    ok "GCC 14 installed as fallback compiler"
    echo "gcc" > /tmp/_sandbox_compiler
  fi
}

log "Installing Arrow/Parquet and compiler in parallel..."
install_arrow &
ARROW_PID=$!
install_compiler &
COMPILER_PID=$!

ARROW_OK=true
COMPILER_OK=true
wait $ARROW_PID || ARROW_OK=false
wait $COMPILER_PID || COMPILER_OK=false

if ! $ARROW_OK; then
  echo "ERROR: Arrow/Parquet installation failed." >&2
  exit 1
fi
if ! $COMPILER_OK; then
  echo "ERROR: Compiler installation failed." >&2
  exit 1
fi

ARROW_INSTALLED_VIA="conda"
if [[ -f /tmp/_sandbox_compiler ]] && [[ "$(cat /tmp/_sandbox_compiler)" == "clang" ]]; then
  CLANG_INSTALLED=true
  CC=clang
  CXX=clang++
else
  CLANG_INSTALLED=false
  CC=gcc-14
  CXX=g++-14
fi
rm -f /tmp/_sandbox_compiler

# ── Step 4: Python scripts dependencies ──────────────────────────────────────
# Pre-installing BEFORE cmake configure ensures cmake's
# find_program(PYTHON_EXECUTABLE) picks the same Python that already has all
# packages, reducing the scripts-install-deps CTest fixture from ~35 s to ~3 s.
if $INSTALL_PYTHON; then
  if ! command -v uv &>/dev/null; then
    log "uv not found -- installing..."
    pip install --break-system-packages uv -q 2>/dev/null \
      || pip install uv -q 2>/dev/null \
      || { curl -LsSf https://astral.sh/uv/install.sh | sh \
           && export PATH="$HOME/.local/bin:$PATH"; }
    ok "uv installed"
  fi
  log "Pre-installing Python scripts dev dependencies..."
  uv pip install --system --break-system-packages -q -e "./scripts[dev,lint]" 2>/dev/null \
    || uv pip install --system -q -e "./scripts[dev,lint]" 2>/dev/null \
    || pip install --break-system-packages -q -e "./scripts[dev,lint]" 2>/dev/null \
    || { warn "Python scripts dep install failed -- CTest fixture will install them (slower)."; }
  ok "Python scripts dev dependencies installed"
  for t in "${UV_TOOLS[@]}"; do
    uv tool install -q "$t" 2>/dev/null || warn "uv tool install $t failed"
  done
  ok "standalone Python CLIs installed with uv tool (~/.local/bin)"
fi

# ── Step 4b: clang-tidy-22 (CI clang-tidy job) + ctcache ─────────────────────
# Best effort: clang-tidy-${CLANG_TIDY_NEXT} is installed versioned only (no
# update-alternatives), so the default clang/clang-format stay at 21.
if command -v "clang-tidy-${CLANG_TIDY_NEXT}" &>/dev/null; then
  ok "clang-tidy-${CLANG_TIDY_NEXT} already installed"
elif (
  set -e
  V=$CLANG_TIDY_NEXT
  [[ -f /usr/share/keyrings/llvm-snapshot.gpg ]]
  echo "deb [signed-by=/usr/share/keyrings/llvm-snapshot.gpg] \
  https://apt.llvm.org/${CODENAME}/ llvm-toolchain-${CODENAME}-${V} main" \
    | sudo tee "/etc/apt/sources.list.d/llvm-${V}.list" > /dev/null
  sudo apt-get update -q \
    -o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/llvm-${V}.list" \
    -o "Dir::Etc::sourceparts=-"
  sudo apt-get install -y --no-install-recommends \
    "clang-tidy-${V}" "clang-format-${V}"
); then
  ok "clang-tidy-${CLANG_TIDY_NEXT} installed"
else
  warn "clang-tidy-${CLANG_TIDY_NEXT} not installed (LLVM repo unavailable?)"
fi

# ctcache: pinned checkout, the pin lives in tools/install_ctcache.sh
# (shared with .github/workflows/clang-tidy.yml).
if [[ -f tools/install_ctcache.sh ]]; then
  bash tools/install_ctcache.sh || warn "ctcache install failed"
else
  warn "tools/install_ctcache.sh not present -- ctcache not installed"
fi

# ── Step 4c: optional groups ─────────────────────────────────────────────────
if $WITH_DOCS; then
  log "Installing docs toolchain (Doxygen + TeX Live for the white paper)..."
  sudo apt-get install -y --no-install-recommends "${APT_DOCS[@]}"
  uv pip install --system --break-system-packages -q "${PIP_DOCS[@]}" 2>/dev/null \
    || pip install --break-system-packages -q "${PIP_DOCS[@]}"
  ok "docs toolchain installed (white paper: cmake --build build --target white_paper)"
fi

if $WITH_JULIA; then
  if ! command -v julia &>/dev/null && [[ ! -x "$HOME/.juliaup/bin/julia" ]]; then
    log "Installing Julia via juliaup..."
    curl -fsSL https://install.julialang.org | sh -s -- --yes
  fi
  JULIA_BIN="$(command -v julia || echo "$HOME/.juliaup/bin/julia")"
  log "Installing Julia packages for tools/*.jl goldens..."
  "$JULIA_BIN" -e "using Pkg; Pkg.add(${JULIA_PKGS})" \
    || warn "Julia package install failed"
  ok "Julia ready (ucjl_solve_golden.jl also needs CPLEX.jl + a CPLEX licence)"
fi

# ── Steps 5-6: Configure and build (optional) ─────────────────────────────────
if $DO_CONFIGURE; then
  # Determine the cmake prefix path – always conda for Arrow in sandboxes
  CMAKE_PREFIX_ARG="-DCMAKE_PREFIX_PATH=$(conda info --base)"

  # CPM package cache – reuse downloaded CMake packages across repeated runs.
  # Matches the CPM_SOURCE_CACHE env variable used by ubuntu.yml CI.
  CPM_CACHE="${HOME}/.cache/cpm_modules"
  mkdir -p "${CPM_CACHE}"

  log "Configuring cmake (${BUILD_TYPE}, ${CC}/${CXX})..."
  CMAKE_ARGS=(
    -S all -B build
    "-DCMAKE_BUILD_TYPE=${BUILD_TYPE}"
    "-DCMAKE_C_COMPILER=${CC}"
    "-DCMAKE_CXX_COMPILER=${CXX}"
    -DCMAKE_C_COMPILER_LAUNCHER=ccache
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
    "-DCPM_SOURCE_CACHE=${CPM_CACHE}"
  )
  if [[ -n "${CMAKE_PREFIX_ARG}" ]]; then
    CMAKE_ARGS+=("${CMAKE_PREFIX_ARG}")
  fi
  cmake "${CMAKE_ARGS[@]}"
  ok "cmake configure done"

  # clang-tidy compile DB (configure-only, non-unity, no PCH) — see
  # tools/run_tidy.sh.  Needs Clang: a GCC command line does not parse.
  if $TIDY_DB && $CLANG_INSTALLED; then
    log "Configuring the clang-tidy compile DB (build-tidy/)..."
    CXX="${CXX}" CC="${CC}" \
      GTOPT_TIDY_CMAKE_ARGS="${CMAKE_PREFIX_ARG} -DCPM_SOURCE_CACHE=${CPM_CACHE}" \
      bash tools/run_tidy.sh --configure "${REPO_ROOT}/build-tidy" \
      && ok "clang-tidy DB ready: tools/run_tidy.sh [files]" \
      || warn "clang-tidy DB configure failed (tools/run_tidy.sh --configure)"
  fi
fi

if $DO_BUILD; then
  log "Building..."
  cmake --build build -j"$(nproc)"
  ok "Build complete"

  log "Running tests..."
  (cd build && ctest --output-on-failure -j"$(nproc)")
  ok "All tests passed"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
if $CLANG_INSTALLED; then
  COMPILER_LINE=" Compiler     : Clang ${CLANG_VERSION} ($(clang --version 2>/dev/null | head -1))"
else
  COMPILER_LINE=" Compiler     : GCC 14 fallback ($(${CXX} --version 2>/dev/null | head -1))"
fi
echo ""
echo "======================================================="
echo " gtopt sandbox setup complete"
echo " Arrow source : ${ARROW_INSTALLED_VIA}"
echo "${COMPILER_LINE}"
echo " ccache       : $(ccache --version 2>/dev/null | head -1)"
if $DO_BUILD; then
  echo " Build        : build/"
  if [[ -f build-tidy/compile_commands.json ]]; then
    echo " clang-tidy DB: build-tidy/ (tools/run_tidy.sh)"
  fi
fi
if ! $CLANG_INSTALLED; then
  echo ""
  echo " NOTE: CI always uses Clang 21.  GCC 14 is a local-only fallback."
  echo "       Re-run this script when the LLVM APT repository is reachable"
  echo "       to switch to Clang 21."
fi
echo "======================================================="
echo ""
echo "Next steps:"
if ! $DO_CONFIGURE; then
  if [[ "$ARROW_INSTALLED_VIA" == "conda" ]]; then
    # shellcheck disable=SC2016  # printed literally for the user
    PREFIX_HINT='  -DCMAKE_PREFIX_PATH="$(conda info --base)"'
  else
    PREFIX_HINT=""
  fi
  echo "  cmake -S all -B build \\"
  echo "    -DCMAKE_BUILD_TYPE=CIFast \\"
  echo "    -DCMAKE_C_COMPILER=${CC} \\"
  echo "    -DCMAKE_CXX_COMPILER=${CXX} \\"
  if [[ -n "$PREFIX_HINT" ]]; then
    echo "    -DCMAKE_C_COMPILER_LAUNCHER=ccache \\"
    echo "    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \\"
    echo "    -DCPM_SOURCE_CACHE=\${HOME}/.cache/cpm_modules \\"
    echo "    ${PREFIX_HINT}"
  else
    echo "    -DCMAKE_C_COMPILER_LAUNCHER=ccache \\"
    echo "    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \\"
    echo "    -DCPM_SOURCE_CACHE=\${HOME}/.cache/cpm_modules"
  fi
  echo "  cmake --build build -j\$(nproc)"
  echo "  cd build && ctest --output-on-failure"
fi
