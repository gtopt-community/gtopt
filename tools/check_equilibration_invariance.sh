#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# check_equilibration_invariance.sh — LP-scaling invariance check.
#
# Runs one case under several LP equilibrations (and optionally several
# `scale_objective` values) with the same solver, then compares, in
# PHYSICAL units:
#
#   * SDDP: LB and UB of every iteration (solver_status.json history);
#   * the final objective (solution.csv / solution.parquet obj_value sum);
#   * SDDP: the first-iteration Benders cuts (cuts/sddp_cuts.parquet),
#     normalised by the α coefficient: intercept a = rhs/w and slopes
#     β = c/w per state variable — physical $ and $/unit.
#
# Equilibration and `scale_objective` are pure LP-numerics knobs: every
# one of these quantities must be identical up to solver tolerance.  A
# divergence points at a scaling bug (a cut or bound read in LP units
# instead of physical units), not at the model.
#
# Usage:
#   tools/check_equilibration_invariance.sh [options] CASE
#
#   CASE                    planning JSON file or case directory
#   -g, --gtopt BIN         gtopt binary (default: $GTOPT_BIN, then
#                           build/standalone/gtopt, then `gtopt` on PATH)
#   -s, --solver NAME       LP solver backend (default: gtopt auto-detect)
#   -m, --method M          sddp (default) | monolithic
#   -n, --iterations N      SDDP iterations (min = max = N; default 3)
#   -e, --equilibrations L  comma list (default: none,row_max,ruiz)
#   -o, --scale-objective L comma list of model_options.scale_objective
#                           values (default: the case's own value)
#       --rtol X            relative tolerance for LB/UB/objective
#                           (default 1e-6)
#       --cut-rtol X        relative tolerance for cut a / β (default 1e-6)
#       --set K=V           extra gtopt `--set` (repeatable)
#   -w, --work-dir DIR      run directory (default: a fresh mktemp dir under
#                           ${TMPDIR:-/tmp}; removed on success unless --keep)
#   -k, --keep              keep the run directory
#   -h, --help              this help
#
# Exit status: 0 = PASS, 1 = FAIL (a quantity differs beyond tolerance),
# 2 = usage / run error, 77 = skipped (gtopt binary or pyarrow missing).
#
# Example:
#   tools/check_equilibration_invariance.sh -s highs -n 5 \
#     -o 1,1000 cases/sddp_hydro_3phase

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"
helper="${script_dir}/check_equilibration_invariance.py"

usage() {
  sed -n '/^# Usage:/,/^# Example:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

gtopt_bin="${GTOPT_BIN:-}"
solver=""
method="sddp"
iterations=3
equilibrations="none,row_max,ruiz"
scale_objectives=""
rtol="1e-6"
cut_rtol="1e-6"
work_dir=""
keep=0
extra_sets=()
case_path=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -g | --gtopt) gtopt_bin="$2"; shift 2 ;;
    -s | --solver) solver="$2"; shift 2 ;;
    -m | --method) method="$2"; shift 2 ;;
    -n | --iterations) iterations="$2"; shift 2 ;;
    -e | --equilibrations) equilibrations="$2"; shift 2 ;;
    -o | --scale-objective) scale_objectives="$2"; shift 2 ;;
    --rtol) rtol="$2"; shift 2 ;;
    --cut-rtol) cut_rtol="$2"; shift 2 ;;
    --set) extra_sets+=("$2"); shift 2 ;;
    -w | --work-dir) work_dir="$2"; shift 2 ;;
    -k | --keep) keep=1; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) echo "error: unknown option '$1'" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "${case_path}" ]]; then
        echo "error: more than one CASE given" >&2
        exit 2
      fi
      case_path="$1"
      shift
      ;;
  esac
done

if [[ -z "${case_path}" ]]; then
  echo "error: CASE is required" >&2
  usage >&2
  exit 2
fi
if [[ ! -e "${case_path}" ]]; then
  echo "error: case '${case_path}' not found" >&2
  exit 2
fi
case "${method}" in
  sddp | monolithic) ;;
  *) echo "error: --method must be sddp or monolithic" >&2; exit 2 ;;
esac
if ! [[ "${iterations}" =~ ^[0-9]+$ ]] || [[ "${iterations}" -lt 1 ]]; then
  echo "error: --iterations must be a positive integer" >&2
  exit 2
fi

if [[ -z "${gtopt_bin}" ]]; then
  if [[ -x "${repo_dir}/build/standalone/gtopt" ]]; then
    gtopt_bin="${repo_dir}/build/standalone/gtopt"
  else
    gtopt_bin="$(command -v gtopt || true)"
  fi
fi
if [[ -z "${gtopt_bin}" || ! -x "${gtopt_bin}" ]]; then
  echo "SKIP: gtopt binary not found (set GTOPT_BIN or --gtopt)" >&2
  exit 77
fi

python_bin="${PYTHON:-python3}"
if ! "${python_bin}" -c 'import pyarrow' 2>/dev/null; then
  echo "SKIP: ${python_bin} has no pyarrow (needed to read the cut files)" >&2
  exit 77
fi

case_path="$(cd "$(dirname "${case_path}")" && pwd)/$(basename "${case_path}")"

created_work_dir=0
if [[ -z "${work_dir}" ]]; then
  work_dir="$(mktemp -d -p "${TMPDIR:-/tmp}" gtopt-eqinv-XXXX)"
  created_work_dir=1
fi
mkdir -p "${work_dir}"
work_dir="$(cd "${work_dir}" && pwd)"

IFS=',' read -r -a eq_list <<<"${equilibrations}"
so_list=("")
if [[ -n "${scale_objectives}" ]]; then
  IFS=',' read -r -a so_list <<<"${scale_objectives}"
fi

variant_args=()
for eq in "${eq_list[@]}"; do
  for so in "${so_list[@]}"; do
    name="eq=${eq}"
    if [[ -n "${so}" ]]; then
      name+=",so=${so}"
    fi
    out="${work_dir}/${name//[=,]/_}"
    rm -rf "${out}"
    mkdir -p "${out}"
    cmd=("${gtopt_bin}" "${case_path}"
      -d "${out}"
      --cut-directory "${out}/cuts"
      --log-directory "${out}/logs"
      --set "method=${method}"
      --set "lp_matrix_options.equilibration_method=${eq}")
    if [[ -n "${solver}" ]]; then
      cmd+=(--solver "${solver}")
    fi
    if [[ -n "${so}" ]]; then
      cmd+=(--set "model_options.scale_objective=${so}")
    fi
    if [[ "${method}" == "sddp" ]]; then
      cmd+=(--sddp-max-iterations "${iterations}"
        --sddp-min-iterations "${iterations}"
        --set "sddp_options.save_per_iteration=true")
    fi
    for kv in "${extra_sets[@]+"${extra_sets[@]}"}"; do
      cmd+=(--set "${kv}")
    done
    echo "== ${name}: ${cmd[*]}"
    if ! (ulimit -c unlimited && cd "${out}" && "${cmd[@]}" >"${out}/run.log" 2>&1)
    then
      echo "error: gtopt failed for ${name} (log: ${out}/run.log)" >&2
      tail -n 20 "${out}/run.log" >&2 || true
      exit 2
    fi
    variant_args+=(--variant "${name}=${out}")
  done
done

rc=0
"${python_bin}" "${helper}" --method "${method}" --rtol "${rtol}" \
  --cut-rtol "${cut_rtol}" "${variant_args[@]}" || rc=$?

if [[ ${rc} -eq 0 && ${keep} -eq 0 && ${created_work_dir} -eq 1 ]]; then
  rm -rf "${work_dir}"
else
  echo "run directory: ${work_dir}"
fi
exit "${rc}"
