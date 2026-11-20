#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Generate the GTOpt logo.

The logo is a provisional placeholder: a small network mark (four buses,
one generator node) next to the wordmark "GTOpt".  The wordmark is drawn
as outlined paths (DejaVu Sans Bold via matplotlib's ``TextPath``) so the
SVG renders identically everywhere, without depending on installed fonts.

Outputs (under ``--out``, default ``docs/images``)::

    logo/gtopt-logo.svg        wordmark + mark, for light backgrounds
    logo/gtopt-logo-dark.svg   wordmark + mark, for dark backgrounds
    logo/gtopt-mark.svg        mark only (favicon, avatar source)
    logo/gtopt-mark-512.png    512x512 avatar

The repository social preview and the organisation banner are hand-drawn
SVGs (``tools/figures/social-preview.svg``, ``tools/figures/banner.svg``),
rasterised by ``make_figures.sh brand`` with ``render_svg.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cairosvg
from matplotlib.font_manager import FontProperties
from matplotlib.path import Path as MplPath
from matplotlib.textpath import TextPath

HERE = Path(__file__).resolve().parent

BLUE = "#2a78d6"
BLUE_LIGHT = "#5a9de9"
ORANGE = "#eb6834"
INK = "#0b0b0b"
INK_DARK = "#f0efec"

FONT = FontProperties(family="DejaVu Sans", weight="bold")

# Mark geometry in a 64x64 box: four buses on a meshed network plus one
# generator node feeding bus 0.
_NODES = ((17.0, 21.0), (45.0, 15.0), (47.0, 43.0), (21.0, 47.0))
_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (0, 2))
_GEN = (32.0, 31.0)


def _mark(x: float = 0.0, y: float = 0.0, size: float = 64.0) -> str:
    """SVG group for the network mark at (x, y) with the given size."""
    s = size / 64.0
    lines = "".join(
        f'<line x1="{_NODES[a][0]}" y1="{_NODES[a][1]}" '
        f'x2="{_NODES[b][0]}" y2="{_NODES[b][1]}"/>'
        for a, b in _EDGES
    )
    nodes = "".join(f'<circle cx="{cx}" cy="{cy}" r="5.2"/>' for cx, cy in _NODES)
    return (
        f'<g transform="translate({x} {y}) scale({s})">'
        f'<rect width="64" height="64" rx="14" fill="{BLUE}"/>'
        f'<g stroke="#ffffff" stroke-width="3" stroke-linecap="round" '
        f'opacity="0.9">{lines}</g>'
        f'<g fill="#ffffff">{nodes}</g>'
        f'<circle cx="{_GEN[0]}" cy="{_GEN[1]}" r="6.4" fill="{ORANGE}" '
        f'stroke="#ffffff" stroke-width="2.4"/>'
        "</g>"
    )


def _text_path(text: str, size: float) -> tuple[str, float]:
    """Return (SVG path data, advance width) for ``text`` at ``size`` px.

    The path is in a y-down frame with the baseline at y = 0.
    """
    tp = TextPath((0, 0), text, size=size, prop=FONT)
    parts: list[str] = []
    for verts, code in tp.iter_segments(curves=True, simplify=False):
        pts = [(verts[i], -verts[i + 1]) for i in range(0, len(verts), 2)]
        fmt = " ".join(f"{px:.2f} {py:.2f}" for px, py in pts)
        if code == MplPath.MOVETO:
            parts.append(f"M{fmt}")
        elif code == MplPath.LINETO:
            parts.append(f"L{fmt}")
        elif code == MplPath.CURVE3:
            parts.append(f"Q{fmt}")
        elif code == MplPath.CURVE4:
            parts.append(f"C{fmt}")
        elif code == MplPath.CLOSEPOLY:
            parts.append("Z")
    return " ".join(parts), tp.get_extents().x1


def _wordmark(
    x: float, baseline: float, size: float, ink: str, accent: str = BLUE
) -> tuple[str, float]:
    """ "GT" in ink, "Opt" in blue; returns (svg, right edge)."""
    gt, gt_w = _text_path("GT", size)
    opt, opt_w = _text_path("Opt", size)
    gap = size * 0.02
    svg = (
        f'<path transform="translate({x:.2f} {baseline:.2f})" d="{gt}" '
        f'fill="{ink}"/>'
        f'<path transform="translate({x + gt_w + gap:.2f} {baseline:.2f})" '
        f'd="{opt}" fill="{accent}"/>'
    )
    return svg, x + gt_w + gap + opt_w


def logo_svg(ink: str, accent: str = BLUE) -> str:
    body, right = _wordmark(78.0, 49.0, 46.0, ink, accent)
    width = right + 4.0
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.0f} 64" width="{width * 2:.0f}" height="128" '
        'role="img" aria-label="GTOpt">'
        f"<title>GTOpt</title>{_mark()}{body}</svg>\n"
    )


def mark_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        'width="512" height="512" role="img" aria-label="GTOpt">'
        f"<title>GTOpt</title>{_mark()}</svg>\n"
    )


def _write_png(svg: str, dst: Path, width: int, height: int) -> None:
    cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        write_to=str(dst),
        output_width=width,
        output_height=height,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("docs/images"))
    args = parser.parse_args()

    logo_dir = args.out / "logo"
    logo_dir.mkdir(parents=True, exist_ok=True)
    (logo_dir / "gtopt-logo.svg").write_text(logo_svg(INK), encoding="utf-8")
    (logo_dir / "gtopt-logo-dark.svg").write_text(
        logo_svg(INK_DARK, BLUE_LIGHT), encoding="utf-8"
    )
    (logo_dir / "gtopt-mark.svg").write_text(mark_svg(), encoding="utf-8")
    _write_png(mark_svg(), logo_dir / "gtopt-mark-512.png", 512, 512)


if __name__ == "__main__":
    main()
