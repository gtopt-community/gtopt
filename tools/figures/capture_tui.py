#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Render frames of the ``run_gtopt`` terminal dashboard to SVG.

``run_gtopt`` draws a Rich live dashboard while gtopt runs.  This helper
rebuilds the same panels (it calls ``run_gtopt._tui.SolverDisplay``) from a
finished run's ``solver_status.json`` and log file, and records each view
with ``rich.console.Console(record=True)`` so the screenshot needs no
terminal recorder.  It writes one SVG per view (dashboard, grid,
convergence).

Paths under the user's home directory are shortened to ``~``, the run
directory to ``.`` and the date is dropped from log timestamps, so that no
local path appears in the image and log lines fit the panel.

Usage::

    python tools/figures/capture_tui.py CASE.json OUT_DIR docs/images/run_gtopt_tui
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI
from run_gtopt import _tui

VIEWS = {
    "dashboard": _tui._MODE_DASHBOARD,  # pylint: disable=protected-access
    "grid": _tui._MODE_GRID,  # pylint: disable=protected-access
    "convergence": _tui._MODE_CONVERGENCE,  # pylint: disable=protected-access
}


def _latest_log(out_dir: Path) -> Path | None:
    logs = sorted(
        (out_dir / "logs").glob("gtopt_*.log"), key=lambda p: p.stat().st_mtime
    )
    return logs[-1] if logs else None


def _sanitize(line: str, roots: list[str]) -> str:
    for root, repl in ((r, ".") for r in roots):
        line = line.replace(root + "/", "./").replace(root, repl)
    line = re.sub(r"/home/[^/\s]+", "~", line)
    # Drop the date from "[YYYY-MM-DD hh:mm:ss.mmm]" so lines fit the panel.
    return re.sub(r"^\[\d{4}-\d{2}-\d{2} ", "[", line)


def render(
    case: Path,
    out_dir: Path,
    prefix: Path,
    width: int,
    height: int,
    log_until: str,
) -> None:
    status = json.loads((out_dir / "solver_status.json").read_text(encoding="utf-8"))
    roots = [str(out_dir.resolve()), str(case.resolve().parent)]

    display = _tui.SolverDisplay(
        case_name=case.stem, case_dir=case, solver_hint=status.get("solver", "")
    )
    # pylint: disable=protected-access
    display._system_stats = _tui._load_system_stats(case)
    display._system_stats["solver"] = status.get("solver", "")
    log_path = _latest_log(out_dir)
    if log_path is not None:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            display.add_log_line(_sanitize(line, roots))
            if log_until and re.search(log_until, line):
                break
    display._status = status
    display._sync_stats_from_status()
    display._phase_tracker.finish_all()
    display._start_time = time.monotonic() - float(status.get("elapsed_s", 0.0))

    prefix.parent.mkdir(parents=True, exist_ok=True)
    for name, mode in VIEWS.items():
        display._display_mode = mode
        console = Console(
            record=True,
            width=width,
            height=height,
            force_terminal=True,
            color_system="truecolor",
            file=io.StringIO(),
        )
        console.print(display._build_display())
        svg = console.export_svg(title="run_gtopt", theme=MONOKAI)
        Path(f"{prefix}_{name}.svg").write_text(svg, encoding="utf-8")
    # pylint: enable=protected-access


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path, help="case JSON that was solved")
    parser.add_argument("out_dir", type=Path, help="gtopt output directory")
    parser.add_argument("prefix", type=Path, help="output prefix (no extension)")
    parser.add_argument("--width", type=int, default=110)
    parser.add_argument("--height", type=int, default=46)
    parser.add_argument(
        "--log-until",
        default=r"SDDP: converged",
        help="stop feeding log lines after the first line matching this regex",
    )
    args = parser.parse_args()
    render(
        args.case, args.out_dir, args.prefix, args.width, args.height, args.log_until
    )


if __name__ == "__main__":
    main()
