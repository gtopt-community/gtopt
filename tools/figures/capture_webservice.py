#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Capture a screenshot of the GTOpt web service job page with Playwright.

Expects a running ``gtopt_websrv`` (default ``http://127.0.0.1:3000``)
configured with an open-source solver (for example
``GTOPT_EXTRA_ARGS="--solver highs"``).  Zips a case directory, submits it
through the upload form, waits for the job to finish and writes
``webservice_jobs.png`` at 2x device pixel ratio.

Usage::

    gtopt_websrv --port 3000 &   # with an empty job directory
    python tools/figures/capture_webservice.py cases/c0 system_c0.json \\
        docs/images/screenshots
"""

from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

# Hide the Next.js development-mode badge if the server runs in dev mode.
_HIDE_DEV_UI = "nextjs-portal { display: none !important; }"


def _zip_case(case_dir: Path, dst: Path) -> None:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(case_dir.rglob("*")):
            rel = path.relative_to(case_dir)
            if path.is_file() and rel.parts[0] not in ("output", "logs"):
                zf.write(path, str(rel))


def capture(url: str, case_dir: Path, planning: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{case_dir.name}.zip"
        _zip_case(case_dir, archive)
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except PlaywrightError:
                browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1440, "height": 900}, device_scale_factor=2
            )
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.add_style_tag(content=_HIDE_DEV_UI)
            page.set_input_files("input[type=file]", str(archive))
            page.fill("#systemFile", planning)
            page.get_by_role("button", name="Submit Job").click()
            page.wait_for_function(
                "() => { const t = document.body.innerText;"
                " return /completed|failed/i.test(t)"
                " && !/running|pending/i.test(t); }",
                timeout=120_000,
            )
            page.wait_for_timeout(1500)
            page.add_style_tag(content=_HIDE_DEV_UI)
            page.screenshot(path=str(dest / "webservice_jobs.png"), full_page=True)
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_dir", type=Path, help="case directory to upload")
    parser.add_argument("planning", help="planning JSON file name in the case")
    parser.add_argument("dest", type=Path, help="directory for the PNG")
    parser.add_argument("--url", default="http://127.0.0.1:3000/")
    args = parser.parse_args()
    capture(args.url, args.case_dir, args.planning, args.dest)


if __name__ == "__main__":
    main()
