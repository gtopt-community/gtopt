#!/usr/bin/env bash
# Regenerate (or check) the hash-pinned Python lock files.
#
#   scripts/requirements-lock.txt     <- scripts/pyproject.toml (+ dev extra)
#   guiservice/requirements-lock.txt  <- guiservice/requirements.txt
#
# The pyproject.toml / requirements.txt files stay the authoritative,
# loosely-specified dependency lists (minimum versions).  The lock files
# pin every transitive dependency to an exact version with sha256 hashes
# so CI and release installs are reproducible and tamper-evident:
#
#   uv pip install --require-hashes -r scripts/requirements-lock.txt
#   uv pip install --no-deps -e ./scripts
#
# Usage:
#   tools/update_python_locks.sh            # refresh, keeping current pins
#                                           # unless a spec forces a change
#   tools/update_python_locks.sh --upgrade  # move every pin to the newest
#                                           # allowed version
#   tools/update_python_locks.sh --check    # exit 1 when a lock no longer
#                                           # matches its sources
#
# Needs uv (https://docs.astral.sh/uv/) and network access to PyPI.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYVER=${GTOPT_LOCK_PYTHON:-3.12}
MODE=update
case "${1:-}" in
  "") ;;
  --upgrade) MODE=upgrade ;;
  --check) MODE=check ;;
  -h | --help)
    sed -n '2,24p' "$0"
    exit 0
    ;;
  *)
    echo "unknown option: $1" >&2
    exit 2
    ;;
esac

if ! command -v uv >/dev/null; then
  echo "error: uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2
  exit 2
fi

# compile <output> <source> [extra uv args...]
compile() {
  local out=$1 src=$2
  shift 2
  local args=(pip compile "$src" --universal --python-version "$PYVER"
    --generate-hashes --quiet --no-header --annotation-style line "$@")
  [ "$MODE" = upgrade ] && args+=(--upgrade)
  if [ "$MODE" = check ]; then
    local tmp
    tmp=$(mktemp)
    cp "$out" "$tmp"
    (cd "$ROOT" && uv "${args[@]}" -o "$tmp")
    if ! diff -q "$out" "$tmp" >/dev/null; then
      echo "stale lock: ${out#"$ROOT"/} (run tools/update_python_locks.sh)" >&2
      diff -u "$out" "$tmp" | head -40 >&2 || true
      rm -f "$tmp"
      return 1
    fi
    rm -f "$tmp"
    echo "ok: ${out#"$ROOT"/}"
  else
    (cd "$ROOT" && uv "${args[@]}" -o "$out")
    echo "wrote ${out#"$ROOT"/}"
  fi
}

rc=0
compile "$ROOT/scripts/requirements-lock.txt" scripts/pyproject.toml \
  --extra dev || rc=1
compile "$ROOT/guiservice/requirements-lock.txt" guiservice/requirements.txt \
  || rc=1
exit $rc
