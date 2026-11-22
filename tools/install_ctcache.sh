#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# tools/install_ctcache.sh — install ctcache, the clang-tidy result cache
# used by tools/run_tidy.sh (https://github.com/matus-chochlik/ctcache).
#
# Usage:
#   bash tools/install_ctcache.sh [DIR]
#       DIR default: $CTCACHE_HOME, else ~/.local/opt/ctcache (one of the
#       locations tools/run_tidy.sh searches)
#
# ctcache is not published on PyPI, so this is a pinned git checkout.
# The pin below is the single source of truth: the CI clang-tidy
# workflow calls this script, and local setup should too.  The local
# (non-server) mode tools/run_tidy.sh uses needs only the Python 3
# standard library.  Idempotent: an existing checkout is moved to the
# pinned commit.
set -euo pipefail

CTCACHE_REPO="https://github.com/matus-chochlik/ctcache.git"
CTCACHE_REF="2cb1c2bdd86f5e56a9044908f7c36a8a04a30ca2"

dir="${1:-${CTCACHE_HOME:-$HOME/.local/opt/ctcache}}"
if [[ ! -d "$dir/.git" ]]; then
  mkdir -p "$(dirname "$dir")"
  git clone --quiet "$CTCACHE_REPO" "$dir"
fi
if ! git -C "$dir" cat-file -e "${CTCACHE_REF}^{commit}" 2> /dev/null; then
  git -C "$dir" fetch --quiet origin
fi
git -C "$dir" -c advice.detachedHead=false checkout --quiet "$CTCACHE_REF"
echo "ctcache ${CTCACHE_REF:0:12} at $dir (wrapper: $dir/clang-tidy)"
