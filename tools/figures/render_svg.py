#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Rasterise SVG files to PNG with headless Chrome (Playwright).

Chrome renders web fonts, emoji and box-drawing characters the same way
GitHub's browser does, which matters for the Rich terminal frames.  The
system Chrome is used when present (``channel="chrome"``); otherwise the
Playwright-managed Chromium.

Usage::

    python tools/figures/render_svg.py IN.svg OUT.png [--scale 2]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def render(src: Path, dst: Path, scale: float) -> None:
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except PlaywrightError:
            browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 2000, "height": 2000}, device_scale_factor=scale
        )
        page.goto(src.resolve().as_uri())
        page.wait_for_timeout(500)
        # Pin the SVG to its viewBox size so "width=100%" files do not
        # stretch to the viewport.
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
        dst.parent.mkdir(parents=True, exist_ok=True)
        page.locator("svg").first.screenshot(path=str(dst), omit_background=True)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path)
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()
    render(args.src, args.dst, args.scale)


if __name__ == "__main__":
    main()
