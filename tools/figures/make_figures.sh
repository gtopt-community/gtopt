#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Regenerate the README / documentation figures.
#
#   bash tools/figures/make_figures.sh [STEP ...]
#
# Steps (default: all): brand architecture topology dispatch battery sddp gui webservice
#
# Every solve uses an open-source solver (SOLVER=highs by default; clp/cbc
# are also accepted) and writes only under $FIGS_WORK, never into cases/.
#
# Requirements (install the Python ones in a virtualenv):
#   - gtopt with the HiGHS (or COIN-OR) plugin        GTOPT_BIN or PATH
#   - Python: matplotlib, cairosvg, playwright, rich, flask, pandas and the
#     gtopt scripts:  pip install -e "scripts[figures]"
#                     pip install -r guiservice/requirements.txt
#   - Chrome/Chromium for Playwright (system Chrome is used when present,
#     otherwise run `playwright install chromium`)
#   - Graphviz `dot` (gtopt_diagram)
#   - optional: pngquant (PNG size optimisation)
#
# Environment:
#   PYTHON       Python interpreter of the virtualenv      (default: python3)
#   GTOPT_BIN    gtopt binary                              (default: gtopt)
#   SOLVER       highs | clp | cbc                         (default: highs)
#   FIGS_WORK    scratch directory for solver runs   (default: mktemp -d)
#   ORG_BANNER   also write the org banner PNG to this path (optional)
#   WEBSRV_URL   URL of a running gtopt_websrv for the "webservice" step;
#                the step is skipped when unset.
set -euo pipefail
ulimit -c unlimited

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
FIG=$ROOT/tools/figures
IMG=$ROOT/docs/images
SHOTS=$IMG/screenshots
DIAG=$ROOT/docs/diagrams
PY=${PYTHON:-python3}
GTOPT=${GTOPT_BIN:-gtopt}
SOLVER=${SOLVER:-highs}
WORK=${FIGS_WORK:-$(mktemp -d "${TMPDIR:-/tmp}/gtopt-figs-XXXX")}

case "$SOLVER" in
  highs | clp | cbc) ;;
  *)
    echo "SOLVER must be an open-source solver (highs, clp, cbc)" >&2
    exit 2
    ;;
esac

mkdir -p "$WORK" "$IMG" "$SHOTS" "$DIAG"
echo "work directory: $WORK"

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(brand architecture topology dispatch battery sddp gui webservice)

optimise_png() {
  if command -v pngquant > /dev/null; then
    pngquant --force --skip-if-larger --quality 70-95 --strip --ext .png "$@" || true
  fi
}

step_brand() {
  "$PY" "$FIG/make_brand.py" --out "$IMG"
  cp "$FIG/social-preview.svg" "$IMG/social-preview.svg"
  "$PY" "$FIG/render_svg.py" "$FIG/social-preview.svg" "$IMG/social-preview.png" --scale 1
  if [ -n "${ORG_BANNER:-}" ]; then
    "$PY" "$FIG/render_svg.py" "$FIG/banner.svg" "$ORG_BANNER" --scale 1
  fi
}

step_battery() {
  mkdir -p "$WORK/battery"
  "$PY" "$FIG/make_battery_demo_case.py" "$ROOT/cases/bat_4b_24/bat_4b_24.json" \
    "$WORK/battery/battery_demo.json"
  (cd "$WORK/battery" && "$GTOPT" "$WORK/battery/battery_demo.json" \
    --solver "$SOLVER" --set output_directory="$WORK/battery/output" \
    --set output_format=csv > run.log 2>&1)
  "$PY" "$FIG/plot_battery.py" "$WORK/battery/battery_demo.json" \
    "$WORK/battery/output" "$IMG/battery_arbitrage.svg"
}

step_architecture() {
  # docs/images/architecture.svg is a hand-drawn static SVG (no generator);
  # edit it directly.  Nothing to do here.
  :
}

step_topology() {
  gtopt_diagram "$ROOT/cases/ieee_14b/ieee_14b.json" -t topology -s electrical \
    -L sfdp -f svg -o "$DIAG/ieee14b_topology.svg" --palette colorblind
  gtopt_diagram "$ROOT/cases/hydro_valley_sddpjl/system_hydro_valley_sddpjl.json" \
    -L dot -f svg -o "$DIAG/hydro_valley_topology.svg" --palette colorblind
  "$PY" "$FIG/inline_svg_images.py" "$DIAG/ieee14b_topology.svg" \
    "$DIAG/hydro_valley_topology.svg"
}

step_dispatch() {
  mkdir -p "$WORK/ieee_9b"
  (cd "$WORK/ieee_9b" && "$GTOPT" "$ROOT/cases/ieee_9b/ieee_9b.json" \
    --solver "$SOLVER" --set output_directory="$WORK/ieee_9b/output" \
    --set output_format=csv > run.log 2>&1)
  "$PY" "$FIG/plot_dispatch.py" "$ROOT/cases/ieee_9b/ieee_9b.json" \
    "$WORK/ieee_9b/output" "$IMG/dispatch_ieee9b.svg"
}

step_sddp() {
  local dir=$WORK/sddp_demo
  mkdir -p "$dir"
  "$PY" "$FIG/make_sddp_demo_case.py" \
    "$ROOT/cases/hydro_valley_sddpjl/system_hydro_valley_sddpjl.json" \
    "$dir/sddp_hydro_valley_demo.json" --scenarios 8
  (cd "$dir" && "$GTOPT" sddp_hydro_valley_demo.json --solver "$SOLVER" \
    --sddp-num-apertures -1 --set output_directory=output > run.log 2>&1)
  "$PY" "$FIG/plot_sddp_convergence.py" "$dir/output/solver_status.json" \
    "$IMG/sddp_convergence.svg"
  "$PY" "$FIG/capture_tui.py" "$dir/sddp_hydro_valley_demo.json" "$dir/output" \
    "$WORK/run_gtopt_tui" --width 120
  "$PY" "$FIG/render_svg.py" "$WORK/run_gtopt_tui_dashboard.svg" \
    "$SHOTS/run_gtopt_tui.png"
  "$PY" "$FIG/render_svg.py" "$WORK/run_gtopt_tui_convergence.svg" \
    "$SHOTS/run_gtopt_tui_convergence.png"
  optimise_png "$SHOTS/run_gtopt_tui.png" "$SHOTS/run_gtopt_tui_convergence.png"
}

step_gui() {
  [ -d "$WORK/ieee_9b/output" ] || step_dispatch
  local port=${GUI_PORT:-5091}
  "$PY" "$ROOT/guiservice/gtopt_guisrv.py" --port "$port" > "$WORK/guisrv.log" 2>&1 &
  local pid=$!
  for _ in $(seq 1 30); do
    curl -fs "http://127.0.0.1:$port/" > /dev/null && break
    sleep 1
  done
  local rc=0
  "$PY" "$FIG/capture_gui.py" "$ROOT/cases/ieee_9b/ieee_9b.json" \
    "$WORK/ieee_9b/output" "$SHOTS" --url "http://127.0.0.1:$port/" || rc=$?
  kill "$pid" 2> /dev/null || true
  [ "$rc" -eq 0 ] || return "$rc"
  optimise_png "$SHOTS"/gui_*.png
}

step_webservice() {
  if [ -z "${WEBSRV_URL:-}" ]; then
    echo "webservice: WEBSRV_URL not set, skipping"
    return
  fi
  "$PY" "$FIG/capture_webservice.py" "$ROOT/cases/c0" system_c0.json "$SHOTS" \
    --url "$WEBSRV_URL"
  optimise_png "$SHOTS/webservice_jobs.png"
}

for s in "${steps[@]}"; do
  echo "== $s"
  "step_$s"
done
