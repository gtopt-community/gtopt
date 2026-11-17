#!/usr/bin/env bash
# Launcher for the GTOpt GUI Plus Next.js frontend.
#
# Environment variables:
#   GTOPT_GUISERVICE_URL   – Flask guiservice base URL (default: http://localhost:5000)
#   PORT                   – Port to listen on (default: 5002)
#   HOST                   – Address to bind to (default: 127.0.0.1).  GUI Plus
#                            proxies /flask/* to the guiservice, which has no
#                            authentication: keep it on loopback unless it is
#                            behind an authenticating TLS reverse proxy.
#
# Usage:
#   ./guiservice_plus.sh [dev|start]
#
# On first run, dependencies are installed with `npm install`.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

mode="${1:-start}"

if [[ ! -d node_modules ]]; then
  echo "Installing GUI Plus dependencies…"
  npm install --no-audit --no-fund
fi

export GTOPT_GUISERVICE_URL="${GTOPT_GUISERVICE_URL:-http://localhost:5000}"
export PORT="${PORT:-5002}"
HOST="${HOST:-127.0.0.1}"

echo "GUI Plus mode: $mode"
echo "Flask guiservice proxy: $GTOPT_GUISERVICE_URL"
echo "Listening on: http://$HOST:$PORT"

case "$mode" in
  dev)
    npm run dev -- -p "$PORT" -H "$HOST"
    ;;
  build)
    npm run build
    ;;
  start)
    if [[ ! -d .next ]]; then
      echo "No production build found; running 'npm run build' first…"
      npm run build
    fi
    npm run start -- -p "$PORT" -H "$HOST"
    ;;
  *)
    echo "Unknown mode: $mode (expected: dev|build|start)" >&2
    exit 1
    ;;
esac
