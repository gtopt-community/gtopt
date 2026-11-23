#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Clone or update the gtopt case repository (gtopt-cases) at the
# commit pinned in tools/cases.lock, and print the export line for
# GTOPT_CASES_DIR.
#
# The large study cases (CEN PLP sources, CEN PCP PLEXOS bundles, the
# 2-year gtopt_case_2y* cases, cen_long_term_1y, LP dumps, ...) are not
# shipped with gtopt.  Tests that need them look them up under
# $GTOPT_CASES_DIR and skip (pytest) or are not registered (CTest) when it
# is unset.
#
# Usage:
#   tools/get_cases.sh [DEST]          # default DEST: $GTOPT_CASES_DIR,
#                                      #   else ../gtopt-cases
#   tools/get_cases.sh --latest [DEST] # check out the remote default
#                                      #   branch instead of the pin
#   eval "$(tools/get_cases.sh --print-env)"   # only print the export
#
# Lock file format (tools/cases.lock, `key=value`, `#` comments):
#   url=<git URL>    commit=<full sha>
# Override the URL with GTOPT_CASES_URL (e.g. a local mirror or an SSH
# remote).  The repository is private: cloning needs `gh auth login` or a
# configured git credential / SSH key.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
lock_file="${script_dir}/cases.lock"

latest=0
print_env=0
dest=""
for arg in "$@"; do
  case "${arg}" in
    --latest) latest=1 ;;
    --print-env) print_env=1 ;;
    -h | --help)
      sed -n '3,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "get_cases.sh: unknown option ${arg}" >&2
      exit 2
      ;;
    *) dest="${arg}" ;;
  esac
done

lock_get() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "${lock_file}" \
    | sed 's/[[:space:]]*#.*$//' | head -n 1
}

url="${GTOPT_CASES_URL:-$(lock_get url)}"
commit="$(lock_get commit)"
if [[ -z "${url}" || -z "${commit}" ]]; then
  echo "get_cases.sh: ${lock_file} must define url= and commit=" >&2
  exit 1
fi

if [[ -z "${dest}" ]]; then
  dest="${GTOPT_CASES_DIR:-${repo_root}/../gtopt-cases}"
fi

if [[ "${print_env}" -eq 1 ]]; then
  echo "export GTOPT_CASES_DIR=$(cd "${dest}" 2>/dev/null && pwd || echo "${dest}")"
  exit 0
fi

if [[ -d "${dest}/.git" ]]; then
  echo "Updating ${dest}" >&2
  if ! git -C "${dest}" diff --quiet HEAD; then
    echo "get_cases.sh: ${dest} has local changes; not updating" >&2
    exit 1
  fi
  git -C "${dest}" fetch --quiet origin
else
  echo "Cloning ${url} into ${dest}" >&2
  git clone --quiet --no-checkout "${url}" "${dest}"
fi

if [[ "${latest}" -eq 1 ]]; then
  target="origin/HEAD"
  git -C "${dest}" remote set-head origin --auto >/dev/null 2>&1 || true
else
  target="${commit}"
fi

git -C "${dest}" -c advice.detachedHead=false checkout --quiet "${target}"
echo "gtopt-cases at $(git -C "${dest}" rev-parse --short HEAD)" >&2

echo "export GTOPT_CASES_DIR=$(cd "${dest}" && pwd)"
