#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Capture screenshots of the GTOpt GUI service with Playwright.

Expects a running ``gtopt_guisrv`` (default ``http://127.0.0.1:5001``).
Uploads a case JSON (zipped on the fly) and a gtopt CSV output directory,
then writes three PNGs at 2x device pixel ratio:

* ``gui_topology.png``     - the case editor's network topology view,
* ``gui_case_editor.png``  - a spreadsheet view of one element table,
* ``gui_results.png``      - the results viewer with a time-series chart.

gtopt writes solution CSVs in long form ``(scenario, stage, block, uid,
value)``; the GUI results viewer charts the wide ``uid:N`` layout, so the
results are pivoted to that layout before upload.

Usage::

    gtopt_guisrv --port 5001 &
    python tools/figures/capture_gui.py cases/ieee_9b/ieee_9b.json \\
        /tmp/ieee9b docs/images/screenshots
"""

from __future__ import annotations

import argparse
import csv
import io
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

VIEWPORT = {"width": 1440, "height": 900}


def _wide_csv(path: Path) -> str:
    """Pivot a long gtopt CSV to the ``uid:N`` wide layout."""
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        if "uid" not in fields or "value" not in fields:
            return path.read_text(encoding="utf-8")
        keys = [f for f in fields if f not in ("uid", "value")]
        table: dict[tuple[str, ...], dict[str, str]] = defaultdict(dict)
        uids: set[int] = set()
        for row in reader:
            table[tuple(row[k] for k in keys)][row["uid"]] = row["value"]
            uids.add(int(row["uid"]))
    cols = [f"uid:{u}" for u in sorted(uids)]
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_NONNUMERIC)
    writer.writerow([*keys, *cols])
    for key in sorted(table, key=lambda k: tuple(int(x) for x in k)):
        vals = table[key]
        writer.writerow([*map(int, key), *(float(vals.get(c[4:], 0.0)) for c in cols)])
    return out.getvalue()


def _zip_case(case: Path, dst: Path) -> None:
    with zipfile.ZipFile(dst, "w") as zf:
        zf.write(case, case.name)


def _zip_results(out_dir: Path, dst: Path) -> None:
    with zipfile.ZipFile(dst, "w") as zf:
        for path in sorted(out_dir.rglob("*.csv")):
            rel = path.relative_to(out_dir)
            if rel.parts[0] == "logs":
                continue
            data = path.read_text(encoding="utf-8")
            if rel.name != "solution.csv":
                data = _wide_csv(path)
            zf.writestr(str(rel), data)


def _shoot(page: Page, path: Path) -> None:
    page.screenshot(path=str(path))


def _shoot_box(
    page: Page, path: Path, left: str, top: str, bottom: str, pad: float = 16
) -> None:
    """Full-page screenshot clipped to the box spanned by three elements."""

    def box(selector: str) -> dict:
        found = page.evaluate(
            "s => { const r = document.querySelector(s).getBoundingClientRect();"
            " return {x: r.left + scrollX, y: r.top + scrollY,"
            " b: r.bottom + scrollY, r: r.right + scrollX}; }",
            selector,
        )
        if found is None:
            raise SystemExit(f"selector not found: {selector}")
        return found

    x0 = box(left)["x"]
    y0 = box(top)["y"] - 4
    y1 = box(bottom)["b"] + pad
    page.screenshot(
        path=str(path),
        full_page=True,
        clip={
            "x": x0,
            "y": max(0.0, y0),
            "width": VIEWPORT["width"] - x0,
            "height": y1 - max(0.0, y0),
        },
    )


def capture(url: str, case: Path, out_dir: Path, dest: Path, table: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        case_zip = Path(tmp) / f"{case.stem}.zip"
        results_zip = Path(tmp) / f"{case.stem}_results.zip"
        _zip_case(case, case_zip)
        _zip_results(out_dir, results_zip)

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except PlaywrightError:
                browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.evaluate("typeof closeAssistant === 'function' && closeAssistant()")

            page.set_input_files("#uploadCaseInput", str(case_zip))
            page.wait_for_timeout(1500)

            page.get_by_text("Topology", exact=False).first.click()
            page.wait_for_timeout(3000)
            _shoot(page, dest / "gui_topology.png")

            page.click(f".nav-link[data-tab='{table}']")
            page.wait_for_timeout(600)
            page.click("#btnSpreadsheetView")
            page.wait_for_timeout(800)
            table_box = page.evaluate(
                "() => { const t = [...document.querySelectorAll('table, .jexcel')]"
                ".filter(e => e.offsetParent !== null).pop();"
                " return t ? t.getBoundingClientRect().bottom : 560; }"
            )
            page.screenshot(
                path=str(dest / "gui_case_editor.png"),
                clip={
                    "x": 0,
                    "y": 0,
                    "width": VIEWPORT["width"],
                    "height": min(VIEWPORT["height"], table_box + 20),
                },
            )

            page.set_input_files("#uploadResultsInput", str(results_zip))
            page.wait_for_timeout(2500)
            option = page.evaluate(
                "Array.from(document.querySelectorAll('#resultsSelector option'))"
                ".map(o => o.value)"
                ".find(v => v.startsWith('Generator/generation_sol'))"
            )
            page.select_option("#resultsSelector", option)
            page.wait_for_timeout(1500)
            page.wait_for_timeout(800)
            _shoot_box(
                page,
                dest / "gui_results.png",
                left="#mainContent",
                top="#resultsOutputs",
                bottom="#resultsChart",
            )
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path, help="case JSON to load in the editor")
    parser.add_argument("out_dir", type=Path, help="gtopt CSV output directory")
    parser.add_argument("dest", type=Path, help="directory for the PNGs")
    parser.add_argument("--url", default="http://127.0.0.1:5001/")
    parser.add_argument(
        "--table", default="line", help="element tab to show (data-tab name)"
    )
    args = parser.parse_args()
    capture(args.url, args.case, args.out_dir, args.dest, args.table)


if __name__ == "__main__":
    main()
