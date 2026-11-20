#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Embed the raster icons referenced by a Graphviz SVG as data URIs.

``gtopt_diagram -f svg`` lets Graphviz reference its node icons by absolute
path in a temporary directory.  Such an SVG only renders on the machine that
made it and leaks a local path, so this helper replaces every
``xlink:href="/path/icon.png"`` with an inline ``data:image/png;base64,...``
URI.  The file is rewritten in place.

Usage::

    python tools/figures/inline_svg_images.py docs/diagrams/ieee14b_topology.svg
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
from pathlib import Path

_HREF = re.compile(r'(xlink:href|href)="(/[^"]+\.(?:png|svg|jpg|jpeg|gif))"')


def inline(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    cache: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        src = match.group(2)
        if src not in cache:
            mime = mimetypes.guess_type(src)[0] or "image/png"
            data = base64.b64encode(Path(src).read_bytes()).decode("ascii")
            cache[src] = f"data:{mime};base64,{data}"
        return f'{match.group(1)}="{cache[src]}"'

    new_text, count = _HREF.subn(repl, text)
    path.write_text(new_text, encoding="utf-8")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("svg", type=Path, nargs="+")
    args = parser.parse_args()
    for svg in args.svg:
        inline(svg)


if __name__ == "__main__":
    main()
