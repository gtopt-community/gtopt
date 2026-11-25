#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Regenerate the interface screenshots of ``docs/interfaces.md``.

Writes six PNGs to ``docs/images/screenshots/`` (``--dest``):

``run_gtopt_tui.png`` / ``run_gtopt_tui_convergence.png``
    The ``run_gtopt`` dashboard (``tui`` step).  A synthetic 8-scenario
    SDDP hydro case (``tools/figures/make_sddp_demo_case.py``) is solved
    with gtopt; its log is then replayed line by line through
    :class:`run_gtopt._tui.SolverDisplay` on a clock driven by the log
    timestamps (the same controller the live dashboard uses), and the
    frame (:func:`run_gtopt.tui.frame.render_frame`) is recorded with
    ``rich.console.Console(record=True).export_svg()`` and rasterised
    with headless Chrome.  The dashboard is taken mid-run, the convergence view
    after convergence.

``webservice_jobs.png``
    The web service page after submitting ``cases/c0`` (``webservice``).

``gui_topology.png`` / ``gui_case_editor.png`` / ``gui_results.png``
    The GUI service (``gui``): ``cases/ieee_9b`` is uploaded, its
    topology and line table are shown, then it is solved through the
    GUI's *Solve* button (on the same local web service) and the
    generator dispatch is opened in the results viewer.

Both services run on ``127.0.0.1`` (the GUI needs no access token on
loopback) with job / state directories under the work directory.  Every
solve uses an open-source solver (``--solver``, default ``highs``).
Paths are scrubbed from the TUI log (work dir -> ``.``, other absolute
paths dropped), so no local path appears in an image.

Requirements, in one virtualenv: ``rich``, ````playwright``
(Python package only; the system Chrome is used when present, otherwise
``playwright install chromium``) and the GUI service requirements
(``pip install -r guiservice/requirements.txt``); a built web service
(``cd webservice && npm ci && npm run build``); a gtopt binary with the
HiGHS plugin; optionally ``pngquant`` / ``oxipng`` to shrink the PNGs.

Usage::

    python tools/make_interface_screenshots.py --gtopt build/standalone/gtopt
    python tools/make_interface_screenshots.py tui --work /tmp/gtopt-demo
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STEPS = ("tui", "webservice", "gui")
OPEN_SOLVERS = ("highs", "clp", "cbc")

# Terminal size of the TUI frames (>= 40 rows: the full, non-compact layout).
TUI_WIDTH = 120
TUI_HEIGHT = 46
# Browser viewport (CSS pixels) and device pixel ratio of the web shots.
VIEWPORT = {"width": 1440, "height": 900}
SCALE = 2


# -- helpers -------------------------------------------------------------------


def _run_gtopt(gtopt: str, case_json: Path, solver: str, *extra: str) -> Path:
    """Solve *case_json* in its directory; return the output directory."""
    out = case_json.parent / "output"
    shutil.rmtree(out, ignore_errors=True)
    cmd = [
        gtopt,
        case_json.name,
        "--solver",
        solver,
        "--set",
        "output_directory=output",
        *extra,
    ]
    with (case_json.parent / "run.log").open("w", encoding="utf-8") as log:
        subprocess.run(cmd, cwd=case_json.parent, stdout=log, stderr=log, check=True)
    return out


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, proc: subprocess.Popen[bytes], what: str) -> None:
    for _ in range(120):
        if proc.poll() is not None:
            raise SystemExit(f"{what} exited early (rc={proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"{what} did not answer on {url}")


@contextmanager
def _service(
    cmd: list[str], url: str, what: str, env: dict[str, str], cwd: Path, log: Path
) -> Iterator[None]:
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT
        )
        try:
            _wait_http(url, proc, what)
            yield
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@contextmanager
def _browser(viewport: dict[str, int] | None = None) -> Iterator[Any]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except PlaywrightError:
            browser = pw.chromium.launch(headless=True)
        try:
            yield browser.new_page(
                viewport=viewport or VIEWPORT, device_scale_factor=SCALE
            )
        finally:
            browser.close()


def _optimise_png(*paths: Path) -> None:
    if shutil.which("pngquant"):
        subprocess.run(
            [
                "pngquant",
                "--force",
                "--skip-if-larger",
                "--quality",
                "70-95",
                "--strip",
                "--ext",
                ".png",
                *map(str, paths),
            ],
            check=False,
        )
    if shutil.which("oxipng"):
        subprocess.run(
            ["oxipng", "-q", "-o", "4", "--strip", "safe", *map(str, paths)],
            check=False,
        )


# -- TUI -----------------------------------------------------------------------

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
_ITER_DONE_RE = re.compile(r"─── iter (\d+) ───")
_ABS_PATH_RE = re.compile(r"(?<![\w.])/(?:home|usr|opt|tmp|var|root|mnt|srv)/")


def _sanitize(line: str, work: Path) -> str | None:
    """*line* with the work dir as ``.`` and no date; None to drop it."""
    for root in (str(work), str(work.resolve())):
        line = line.replace(root + "/", "./").replace(root, ".")
    if _ABS_PATH_RE.search(line):
        return None  # plugin / config paths of the local install
    return re.sub(r"^\[\d{4}-\d{2}-\d{2} ", "[", line)


def _status_at(final: dict[str, Any], iteration: int) -> dict[str, Any]:
    """The status the live poller saw right after iteration *iteration*."""
    st = copy.deepcopy(final)
    st["history"] = st["history"][:iteration]
    last = st["history"][-1]
    st.update(
        iteration=iteration,
        lower_bound=last["lower_bound"],
        upper_bound=last["upper_bound"],
        gap=last["gap"],
        converged=bool(last["converged"]),
        status="converged" if last["converged"] else "running",
        current_pass=0 if last["converged"] else 1,
    )
    return st


def _replay(case_json: Path, out_dir: Path, until_iter: int | None) -> Any:
    """Feed the run's log into a SolverDisplay; stop after *until_iter*."""
    from run_gtopt._tui import SolverDisplay

    class _Clock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    final = json.loads((out_dir / "solver_status.json").read_text(encoding="utf-8"))
    log = max((out_dir / "logs").glob("gtopt_*.log"))
    lines = log.read_text(encoding="utf-8").splitlines()
    t0 = None
    m0 = _TS_RE.match(lines[0])
    if m0:
        t0 = datetime.fromisoformat(m0.group(1))
    clock.now = 1000.0
    display = SolverDisplay(
        case_name=case_json.stem,
        case_dir=case_json.parent,
        output_dir=out_dir,
        clock=clock,
        console=None,
    )
    display.load_planning_stats()
    display.set_log_path(Path("output/logs") / log.name)
    work = case_json.parent
    stop_after: int | None = None
    for raw in lines:
        m = _TS_RE.match(raw)
        if m and t0 is not None:
            clock.now = (
                1000.0 + (datetime.fromisoformat(m.group(1)) - t0).total_seconds()
            )
        line = _sanitize(raw, work)
        if line is None:
            continue
        display.add_log_line(line)
        it = _ITER_DONE_RE.search(raw)
        if it:
            n = int(it.group(1))
            display.update_status(_status_at(final, min(n, len(final["history"]))))
            if until_iter is not None and n >= until_iter:
                stop_after = 4  # a few lines of the next iteration
        elif stop_after is not None:
            stop_after -= 1
            if stop_after == 0:
                break
        if "SDDP: converged" in raw:
            display.update_status(final)
            break
    return display


def _frame_svg(display: Any, mode: int) -> str:
    from rich.console import Console
    from rich.terminal_theme import MONOKAI
    from run_gtopt.tui.frame import render_frame
    from run_gtopt.tui.model import layout_for

    display._display_mode = mode  # pylint: disable=protected-access
    console = Console(
        record=True,
        width=TUI_WIDTH,
        height=TUI_HEIGHT,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        file=io.StringIO(),
        _environ={},
    )
    console.print(render_frame(display.snapshot(), layout_for(TUI_WIDTH, TUI_HEIGHT)))
    return console.export_svg(title="run_gtopt", theme=MONOKAI)


def _svg_to_png(svg: Path, png: Path) -> None:
    """Rasterise a Rich SVG frame with Chrome.

    Chrome renders the frame's web font (Fira Code), box-drawing
    characters and emoji the way GitHub's browser does; cairosvg falls
    back to whatever local font it finds.
    """
    with _browser(viewport={"width": 2400, "height": 2400}) as page:
        page.goto(svg.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(500)
        # Pin the SVG to its viewBox size ("width=100%" would stretch it).
        page.evaluate(
            """() => {
              const svg = document.querySelector('svg');
              const vb = svg.viewBox && svg.viewBox.baseVal;
              if (vb && vb.width) {
                svg.setAttribute('width', vb.width);
                svg.setAttribute('height', vb.height);
              }
            }"""
        )
        page.locator("svg").first.screenshot(path=str(png), omit_background=True)


def step_tui(args: argparse.Namespace) -> list[Path]:
    from run_gtopt.tui.model import MODE_CONVERGENCE, MODE_DASHBOARD

    case_dir = args.work / "sddp_hydro_valley_demo"
    case_dir.mkdir(parents=True, exist_ok=True)
    case_json = case_dir / "sddp_hydro_valley_demo.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/figures/make_sddp_demo_case.py"),
            str(ROOT / "cases/hydro_valley_sddpjl/system_hydro_valley_sddpjl.json"),
            str(case_json),
            "--scenarios",
            "8",
        ],
        check=True,
    )
    out = _run_gtopt(args.gtopt, case_json, args.solver, "--sddp-num-apertures", "-1")
    shots = [
        (args.tui_dashboard_iter, MODE_DASHBOARD, "run_gtopt_tui.png"),
        (None, MODE_CONVERGENCE, "run_gtopt_tui_convergence.png"),
    ]
    written = []
    for until, mode, name in shots:
        display = _replay(case_json, out, until)
        svg = args.work / name.replace(".png", ".svg")
        svg.write_text(_frame_svg(display, mode), encoding="utf-8")
        _svg_to_png(svg, args.dest / name)
        written.append(args.dest / name)
    return written


# -- web service ---------------------------------------------------------------

# Hide the Next.js development-mode badge if the server runs in dev mode.
_HIDE_DEV_UI = "nextjs-portal { display: none !important; }"


def _zip_dir(case_dir: Path, dst: Path) -> None:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(case_dir.rglob("*")):
            rel = path.relative_to(case_dir)
            if path.is_file() and rel.parts[0] not in ("output", "logs"):
                zf.write(path, str(rel))


@contextmanager
def _webservice(args: argparse.Namespace) -> Iterator[str]:
    """A local gtopt web service; yields its URL."""
    web = args.webservice_dir
    if not (web / ".next").is_dir():
        raise SystemExit(f"{web} is not built: cd {web} && npm ci && npm run build")
    port = _free_port()
    data = args.work / "websrv"
    data.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PORT": str(port),
        "GTOPT_BIN": str(Path(shutil.which(args.gtopt) or args.gtopt).resolve()),
        "GTOPT_DATA_DIR": str(data / "jobs"),
        "GTOPT_LOG_DIR": str(data / "logs"),
        "GTOPT_EXTRA_ARGS": f"--solver {args.solver}",
    }
    env.pop("GTOPT_WEBSERVICE_API_TOKEN", None)
    cmd = ["npx", "next", "start", "--hostname", "127.0.0.1", "-p", str(port)]
    url = f"http://127.0.0.1:{port}/"
    with _service(cmd, url + "api/ping", "web service", env, web, data / "srv.log"):
        yield url


def capture_webservice(url: str, dest: Path, work: Path) -> Path:
    archive = work / "c0.zip"
    _zip_dir(ROOT / "cases/c0", archive)
    shot = dest / "webservice_jobs.png"
    with _browser() as page:
        page.goto(url)
        page.wait_for_load_state("networkidle")
        page.add_style_tag(content=_HIDE_DEV_UI)
        page.set_input_files("input[type=file]", str(archive))
        page.fill("#systemFile", "system_c0.json")
        page.get_by_role("button", name="Submit Job").click()
        page.wait_for_function(
            "() => { const t = document.body.innerText;"
            " return /completed|failed/i.test(t)"
            " && !/running|pending/i.test(t); }",
            timeout=0,
        )
        page.wait_for_timeout(1500)
        page.add_style_tag(content=_HIDE_DEV_UI)
        page.screenshot(path=str(shot), full_page=True)
    return shot


# -- GUI -----------------------------------------------------------------------


@contextmanager
def _guiservice(args: argparse.Namespace, websrv_url: str) -> Iterator[str]:
    port = _free_port()
    state = args.work / "guisrv"
    state.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GTOPT_WEBSERVICE_URL": websrv_url.rstrip("/"),
        "XDG_STATE_HOME": str(state),
        "GTOPT_GUI_LOG_FILE": str(state / "gui.log"),
    }
    for var in ("GTOPT_GUI_AUTH", "GTOPT_GUI_AUTH_TOKEN", "GTOPT_GUI_HOST"):
        env.pop(var, None)
    cmd = [
        sys.executable,
        str(ROOT / "guiservice/gtopt_guisrv.py"),
        "--port",
        str(port),
    ]
    url = f"http://127.0.0.1:{port}/"
    with _service(cmd, url, "GUI service", env, state, state / "srv.log"):
        yield url


def _shoot_box(page: Any, path: Path, left: str, top: str, bottom: str) -> None:
    """Full-page screenshot clipped to the box spanned by three elements."""

    def box(selector: str) -> dict[str, float]:
        found = page.evaluate(
            "s => { const e = document.querySelector(s); if (!e) return null;"
            " const r = e.getBoundingClientRect();"
            " return {x: r.left + scrollX, y: r.top + scrollY,"
            " b: r.bottom + scrollY}; }",
            selector,
        )
        if found is None:
            raise SystemExit(f"selector not found: {selector}")
        return found

    x0 = box(left)["x"]
    y0 = max(0.0, box(top)["y"] - 4)
    y1 = box(bottom)["b"] + 16
    page.screenshot(
        path=str(path),
        full_page=True,
        clip={"x": x0, "y": y0, "width": VIEWPORT["width"] - x0, "height": y1 - y0},
    )


def capture_gui(url: str, dest: Path, work: Path, table: str = "line") -> list[Path]:
    case = ROOT / "cases/ieee_9b/ieee_9b.json"
    case_zip = work / "ieee_9b.zip"
    with zipfile.ZipFile(case_zip, "w") as zf:
        zf.write(case, case.name)
    shots = [dest / f"gui_{n}.png" for n in ("topology", "case_editor", "results")]
    with _browser() as page:
        page.goto(url)
        page.wait_for_load_state("networkidle")
        page.evaluate("typeof closeAssistant === 'function' && closeAssistant()")

        page.set_input_files("#uploadCaseInput", str(case_zip))
        page.wait_for_timeout(1500)

        page.get_by_text("Topology", exact=False).first.click()
        page.wait_for_timeout(3000)
        page.screenshot(path=str(shots[0]))

        page.click(f".nav-link[data-tab='{table}']")
        page.wait_for_timeout(600)
        page.click("#btnSpreadsheetView")
        page.wait_for_timeout(800)
        bottom = page.evaluate(
            "() => { const t = [...document.querySelectorAll('table, .jexcel')]"
            ".filter(e => e.offsetParent !== null).pop();"
            " return t ? t.getBoundingClientRect().bottom : 560; }"
        )
        page.screenshot(
            path=str(shots[1]),
            clip={
                "x": 0,
                "y": 0,
                "width": VIEWPORT["width"],
                "height": min(VIEWPORT["height"], bottom + 20),
            },
        )

        # Solve through the GUI (on the local web service), then load the
        # results into the viewer.
        page.evaluate("solveCase()")
        page.wait_for_selector("#solverActions", state="visible", timeout=0)
        page.evaluate("loadSolverResults()")
        page.wait_for_selector("#resultsOutputs", state="visible")
        option = page.evaluate(
            "Array.from(document.querySelectorAll('#resultsSelector option'))"
            ".map(o => o.value)"
            ".find(v => v.startsWith('Generator/generation_sol'))"
        )
        if not option:
            raise SystemExit("no Generator/generation_sol output in the results")
        page.select_option("#resultsSelector", option)
        page.wait_for_timeout(2000)
        _shoot_box(
            page,
            shots[2],
            left="#mainContent",
            top="#resultsOutputs",
            bottom="#resultsChart",
        )
    return shots


# -- main ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "steps", nargs="*", metavar="STEP", help=f"{', '.join(STEPS)} (default: all)"
    )
    parser.add_argument("--gtopt", default=os.environ.get("GTOPT_BIN", "gtopt"))
    parser.add_argument("--solver", default="highs", choices=OPEN_SOLVERS)
    parser.add_argument(
        "--work", type=Path, help="scratch directory (default: a new temp dir)"
    )
    parser.add_argument("--dest", type=Path, default=ROOT / "docs/images/screenshots")
    parser.add_argument(
        "--webservice-dir",
        type=Path,
        default=ROOT / "webservice",
        help="built web service (npm ci && npm run build)",
    )
    parser.add_argument(
        "--tui-dashboard-iter",
        type=int,
        default=6,
        help="SDDP iteration at which the dashboard frame is taken",
    )
    args = parser.parse_args()
    steps = args.steps or list(STEPS)
    unknown = sorted(set(steps) - set(STEPS))
    if unknown:
        parser.error(f"unknown step(s): {', '.join(unknown)}")
    if args.work is None:
        args.work = Path(tempfile.mkdtemp(prefix="gtopt-screens-"))
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    args.dest.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "scripts"))
    print(f"work directory: {args.work}")

    written: list[Path] = []
    if "tui" in steps:
        written += step_tui(args)
    if "webservice" in steps or "gui" in steps:
        with _webservice(args) as websrv:
            if "webservice" in steps:
                written.append(capture_webservice(websrv, args.dest, args.work))
            if "gui" in steps:
                with _guiservice(args, websrv) as gui:
                    written += capture_gui(gui, args.dest, args.work)
    _optimise_png(*written)
    for path in written:
        print(
            f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}"
            f"  {path.stat().st_size // 1024} KB"
        )


if __name__ == "__main__":
    main()
