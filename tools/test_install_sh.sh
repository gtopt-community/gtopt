#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Unit tests for install.sh: option parsing, solver selection and the
# commercial-solver / GPU detection, run against fake directory trees and
# a fake nvidia-smi.  Nothing is installed.
#
#   bash tools/test_install_sh.sh
#
# Exit status 0 = all tests passed.

# shellcheck disable=SC2016,SC2001
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
script="$here/install.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pass=0 fail=0

# ---- fixtures -------------------------------------------------------------
root="$work/root" home="$work/home" bin="$work/bin"
mkdir -p "$root/opt" "$home" "$bin"
mk() { mkdir -p "$(dirname "$1")" && : >"$1"; }

# CPLEX: two in /opt, a newer one reachable only through `cplex` on PATH.
mk "$root/opt/ibm/ILOG/CPLEX_Studio129/cplex/include/ilcplex/cplex.h"
mk "$root/opt/ibm/ILOG/CPLEX_Studio2211/cplex/include/ilcplex/cplex.h"
mk "$root/opt/ibm/ILOG/CPLEX_Studio2211/cplex/bin/x86-64_linux/cplex"
cplex_path="$home/apps/CPLEX_Studio2212/cplex"
mk "$cplex_path/include/ilcplex/cplex.h"
mkdir -p "$cplex_path/bin/x86-64_linux" "$work/cplexbin"
printf '#!/bin/sh\n' >"$cplex_path/bin/x86-64_linux/cplex"
chmod +x "$cplex_path/bin/x86-64_linux/cplex"
ln -s "$cplex_path/bin/x86-64_linux/cplex" "$work/cplexbin/cplex"
# A CPLEX dir without headers must be ignored.
mkdir -p "$root/opt/ibm/ILOG/CPLEX_Studio9999/cplex"
# Gurobi: /opt and ~.
mk "$root/opt/gurobi1100/linux64/include/gurobi_c.h"
mk "$home/gurobi1203/linux64/include/gurobi_c.h"
# MindOpt: 2.10.0 must win over 2.3.0 (version sort, not lexical).
mk "$home/mindopt/2.3.0/linux64-x86/include/Mindopt.h"
mk "$root/opt/mindopt/2.10.0/linux64-x86/include/Mindopt.h"

fake_smi() {  # fake_smi none | <cuda major.minor>
  if [[ $1 == none ]]; then
    printf '#!/bin/sh\necho "NVIDIA-SMI has failed" >&2; exit 9\n' >"$bin/nvidia-smi"
  else
    cat >"$bin/nvidia-smi" <<EOF
#!/bin/sh
if [ "\$1" = -L ]; then echo "GPU 0: NVIDIA Fake (UUID: GPU-0)"; exit 0; fi
echo "| NVIDIA-SMI 595.00   Driver Version: 595.00   CUDA Version: $1 |"
EOF
  fi
  chmod +x "$bin/nvidia-smi"
}

# Run install.sh functions in a clean subshell.  Usage:
#   sel [VAR=value ...] -- <installer options>
# Prints SELECTED / CPLEX_DIR / ... after resolve_solvers, or the error.
sel() {
  local -a envs=()
  while [[ $1 != -- ]]; do envs+=("$1"); shift; done
  shift
  env -i HOME="$home" PATH="$bin:/usr/bin:/bin" GTOPT_INSTALL_ROOT="$root" \
    GTOPT_INSTALL_NO_MAIN=1 "${envs[@]}" bash -c '
      source "$0"
      DRY=1
      while (($#)); do
        case $1 in
          --solvers | --solver) SOLVERS_ARG=$2; shift 2 ;;
          --without) WITHOUT_ARG+=${WITHOUT_ARG:+,}$2; shift 2 ;;
          --no-highs) NO_HIGHS=1; shift ;;
          --no-cuopt) NO_CUOPT=1; shift ;;
          --no-detect) DETECT=0; shift ;;
          *) echo "bad test option $1"; exit 3 ;;
        esac
      done
      resolve_solvers /nonexistent-prefix
      echo "SELECTED=[${SELECTED# }]"
      echo "CPLEX=$CPLEX_DIR"
      echo "GUROBI=$GUROBI_DIR"
      echo "MINDOPT=$MINDOPT_DIR"
      echo "GPU=$HAVE_GPU WHEEL=$CUOPT_WHEEL"
      solver_args=() solver_env=()
      solver_cmake_args
      printf "ARG %s\n" "${solver_args[@]}" "${solver_env[@]}"
    ' "$script" "$@" 2>&1
}

check() {  # check <name> <output> <grep -E pattern> [!]
  local name=$1 out=$2 pat=$3 neg=${4:-}
  if { [[ -z $neg ]] && grep -qE -- "$pat" <<<"$out"; } ||
    { [[ -n $neg ]] && ! grep -qE -- "$pat" <<<"$out"; }; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf 'FAIL: %s\n  expected %s/%s/\n  got:\n%s\n' "$name" "${neg:+not }" "$pat" \
      "$(sed 's/^/    /' <<<"$out")"
  fi
}

# ---- no GPU, no commercial solvers on PATH -------------------------------
fake_smi none

out=$(sel -- )
check "default selection" "$out" '^SELECTED=\[highs cbc cplex gurobi mindopt \]$'
check "default: newest CPLEX in /opt" "$out" "^CPLEX=$root/opt/ibm/ILOG/CPLEX_Studio2211/cplex$"
check "default: newest Gurobi (home 1203 > opt 1100)" "$out" "^GUROBI=$home/gurobi1203/linux64$"
check "default: MindOpt version sort 2.10.0 > 2.3.0" "$out" "^MINDOPT=$root/opt/mindopt/2.10.0/linux64-x86$"
check "default: no GPU -> no cuopt" "$out" '^GPU=0 WHEEL=$'
check "default: CPLEX_ROOT_DIR passed to cmake" "$out" "^ARG -DCPLEX_ROOT_DIR=$root/opt/ibm/ILOG/CPLEX_Studio2211/cplex$"
check "default: GUROBI_HOME exported" "$out" "^ARG GUROBI_HOME=$home/gurobi1203/linux64$"
check "default: nothing disabled" "$out" '=TRUE$|DISABLE_(SCIP|CUOPT)=ON' !

out=$(sel PATH="$bin:$work/cplexbin:/usr/bin:/bin" -- )
check "cplex on PATH is newest" "$out" "^CPLEX=$cplex_path$"

out=$(sel CPLEX_ROOT_DIR="$root/opt/ibm/ILOG/CPLEX_Studio129/cplex" -- )
check "CPLEX_ROOT_DIR wins over newer installs" "$out" "^CPLEX=$root/opt/ibm/ILOG/CPLEX_Studio129/cplex$"

out=$(sel CPLEX_STUDIO_DIR2212="$home/apps/CPLEX_Studio2212" -- )
check "CPLEX_STUDIO_DIR* is probed" "$out" "^CPLEX=$cplex_path$"

# Flat CPLEX installs (/opt/cplex, ~/opt/cplex): version from cpxconst.h.
cpx_ver() {  # cpx_ver <cplex dir> <version> <release> <modification>
  mk "$1/include/ilcplex/cplex.h"
  printf '#define CPX_VERSION_VERSION       %s\n#define CPX_VERSION_RELEASE       %s\n#define CPX_VERSION_MODIFICATION  %s\n' \
    "$2" "$3" "$4" >"$1/include/ilcplex/cpxconst.h"
}
root2="$work/root2" home2="$work/home2"
cpx_ver "$root2/opt/cplex" 12 10 0
out=$(sel GTOPT_INSTALL_ROOT="$root2" HOME="$home2" -- )
check "flat /opt/cplex found" "$out" "^CPLEX=$root2/opt/cplex$"
mk "$root2/opt/ibm/ILOG/CPLEX_Studio2211/cplex/include/ilcplex/cplex.h"
out=$(sel GTOPT_INSTALL_ROOT="$root2" HOME="$home2" -- )
check "Studio2211 newer than flat 12.10" "$out" "^CPLEX=$root2/opt/ibm/ILOG/CPLEX_Studio2211/cplex$"
cpx_ver "$home2/opt/cplex/cplex" 22 1 2
out=$(sel GTOPT_INSTALL_ROOT="$root2" HOME="$home2" -- )
check "home opt/cplex/cplex 22.1.2 is newest" "$out" "^CPLEX=$home2/opt/cplex/cplex$"

out=$(sel -- --no-detect)
check "--no-detect: nothing found" "$out" '^SELECTED=\[highs cbc \]$'
out=$(sel GUROBI_HOME="$root/opt/gurobi1100/linux64" -- --no-detect)
check "--no-detect: env var still honoured" "$out" "^GUROBI=$root/opt/gurobi1100/linux64$"

out=$(sel GUROBI_HOME=/nowhere -- )
check "bad GUROBI_HOME falls back to detection" "$out" "^GUROBI=$home/gurobi1203/linux64$"

out=$(sel -- --without cplex,gurobi)
check "--without removes solvers" "$out" '^SELECTED=\[highs cbc mindopt \]$'
check "--without disables the package in cmake" "$out" '^ARG -DCMAKE_DISABLE_FIND_PACKAGE_CPLEX=TRUE$'
out=$(sel -- --no-highs)
check "--no-highs (deprecated alias)" "$out" '^SELECTED=\[cbc cplex gurobi mindopt \]$'

# ---- --solvers lists ------------------------------------------------------
out=$(sel -- --solvers highs,CLP,osi)
check "--solvers list, aliases, de-dup" "$out" '^SELECTED=\[highs cbc \]$'
check "--solvers: others disabled" "$out" '^ARG -DCMAKE_DISABLE_FIND_PACKAGE_Gurobi=TRUE$'
check "--solvers: scip disabled" "$out" '^ARG -DGTOPT_DISABLE_SCIP=ON$'
check "--solvers: listed stays enabled" "$out" '^ARG -DCMAKE_DISABLE_FIND_PACKAGE_COIN=FALSE$'
check "--solvers: detected-but-unlisted CPLEX not passed" "$out" '^ARG -DCPLEX_ROOT_DIR' !

out=$(sel -- --solvers highs,cplex)
check "--solvers with detected commercial" "$out" '^SELECTED=\[highs cplex \]$'

out=$(sel HOME="$work/empty" GTOPT_INSTALL_ROOT="$work/empty" -- --solvers highs,cplex)
check "--solvers: missing commercial is an error" "$out" 'CPLEX requested but not found'
check "--solvers: error names the env var" "$out" 'CPLEX_ROOT_DIR='

out=$(sel HOME="$work/empty" GTOPT_INSTALL_ROOT="$work/empty" -- --solvers all)
check "all: open-source set, no commercial, no error" "$out" '^SELECTED=\[highs cbc scip \]$'
out=$(sel -- --solvers all)
check "all: + detected commercial" "$out" '^SELECTED=\[highs cbc scip cplex gurobi mindopt \]$'
out=$(sel -- --solvers all --without scip,mindopt)
check "all minus --without" "$out" '^SELECTED=\[highs cbc cplex gurobi \]$'

out=$(sel -- --solvers cuopt,highs)
check "--solvers cuopt without GPU: skipped" "$out" '^SELECTED=\[highs \]$'
check "--solvers cuopt without GPU: cuopt disabled" "$out" '^ARG -DGTOPT_DISABLE_CUOPT=ON$'

for bad in "--solvers foo" "--solvers all,highs" "--solvers highs --no-highs" \
  "--solvers cplex --without cplex" "--solvers highs --without scip" \
  "--solvers highs --no-cuopt" "--solvers ,"; do
  # shellcheck disable=SC2086
  out=$(sel -- $bad)
  check "conflict/invalid: $bad" "$out" '^error|error:'
  check "conflict/invalid stops: $bad" "$out" '^SELECTED=' !
done

# ---- GPU ------------------------------------------------------------------
fake_smi 12.4
out=$(sel -- )
check "GPU + CUDA 12 -> cu12 wheel" "$out" '^GPU=1 WHEEL=libcuopt-cu12$'
check "GPU: cuopt selected by default" "$out" '^SELECTED=\[highs cbc cplex gurobi mindopt cuopt \]$'
out=$(sel -- --no-cuopt)
check "--no-cuopt" "$out" '^SELECTED=\[highs cbc cplex gurobi mindopt \]$'
check "--no-cuopt disables plugin" "$out" '^ARG -DGTOPT_DISABLE_CUOPT=ON$'
fake_smi 13.2
out=$(sel -- --solvers cuopt)
check "GPU + CUDA 13 -> cu13 wheel" "$out" '^GPU=1 WHEEL=libcuopt-cu13$'
check "--solvers cuopt with GPU" "$out" '^SELECTED=\[cuopt \]$'

# GPU visible only through /proc (no nvidia-smi).
fake_smi none
mk "$root/proc/driver/nvidia/gpus/0000:01:00.0/information"
out=$(sel -- )
check "GPU via /proc/driver/nvidia" "$out" '^GPU=1 WHEEL=libcuopt-cu12$'
rm -rf "$root/proc"

# Existing cuOpt (CUOPT_HOME) is used as is, no install.
mk "$work/cuopt/include/cuopt/mathematical_optimization/cuopt_c.h"
out=$(sel CUOPT_HOME="$work/cuopt" -- )
check "CUOPT_HOME used even without GPU" "$out" '^SELECTED=\[.*cuopt \]$'
check "CUOPT_HOME exported to the build" "$out" "^ARG CUOPT_HOME=$work/cuopt$"

# ---- prefix ownership -------------------------------------------------------
psudo() { GTOPT_INSTALL_NO_MAIN=1 bash -c 'source "$0"; echo "[$(prefix_sudo "$1" sudo)]"' "$script" "$1"; }
check "missing prefix under writable home: no sudo" "$(psudo "$home/.local/x/y")" '^\[\]$'
mkdir -p "$work/ro" && chmod 555 "$work/ro"
if ((EUID != 0)); then
  check "prefix under read-only dir: sudo" "$(psudo "$work/ro/pfx")" '^\[sudo\]$'
fi
chmod 755 "$work/ro"

# ---- whole script, --help and --dry-run -----------------------------------
out=$(bash "$script" --help)
check "--help shows --solvers" "$out" '--solvers'
out=$(bash "$script" < /dev/null --help; cat "$script" | bash -s -- --help)
check "--help works when piped (curl | bash)" "$out" 'Options:'
out=$(env HOME="$home" PATH="$bin:/usr/bin:/bin" GTOPT_INSTALL_ROOT="$root" \
  bash "$script" --dry-run --no-deps --prefix "$work/pfx" --solvers highs,cbc 2>&1)
check "dry-run --solvers prunes stale plugins" "$out" 'rm -f .*/libgtopt_solver_cplex.so'
check "dry-run keeps selected plugins" "$out" 'rm -f .*libgtopt_solver_(osi|highs).so' !
check "dry-run changed nothing" "$(ls "$work")" 'pfx' !
out=$(env HOME="$home" PATH="$bin:/usr/bin:/bin" GTOPT_INSTALL_ROOT="$root" \
  bash "$script" --dry-run --no-deps --prefix "$work/pfx2" --solvers highs,cplex 2>&1)
check "dry-run links the cplex CLI into PREFIX/bin" "$out" \
  "ln -sfn $root/opt/ibm/ILOG/CPLEX_Studio2211/cplex/bin/x86-64_linux/cplex $work/pfx2/bin/cplex"
check "dry-run: no cuopt_cli link without cuopt" "$out" 'bin/cuopt_cli' !
out=$(bash "$script" --prefix 2>&1)
check "missing option value" "$out" 'needs a value'

printf '%d passed, %d failed\n' "$pass" "$fail"
((fail == 0))
