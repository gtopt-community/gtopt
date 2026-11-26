# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Number / time / sparkline formatting shared by the dashboard panels."""

from __future__ import annotations

SPARKLINE_WIDTH = 24
PROGRESS_BAR_WIDTH = 40


def sparkline(
    values: list[float],
    width: int = SPARKLINE_WIDTH,
    lo: float | None = None,
    hi: float | None = None,
) -> str:
    """Render a Unicode sparkline for the last *width* values.

    Scaled to the window's min..max, or to the fixed *lo*..*hi* (e.g.
    0..100 for percentages, so 5 % never draws as a full block).  A
    single sample is no trend and renders as ""; a flat series as a
    mid-height line.
    """
    vals = values[-width:]
    if len(vals) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    mn = min(vals) if lo is None else lo
    mx = max(vals) if hi is None else hi
    if mx <= mn:
        return "▄" * len(vals)
    top = len(blocks) - 1
    return "".join(
        blocks[max(0, min(top, round((v - mn) / (mx - mn) * top)))] for v in vals
    )


def spark_range(values: list[float], fmt: str = "{:.0f}") -> str:
    """ " min–max" label for a sparkline window."""
    vals = values[-SPARKLINE_WIDTH:]
    if len(vals) < 2:
        return ""
    return f"  {fmt.format(min(vals))}–{fmt.format(max(vals))}"


def format_elapsed(seconds: float) -> str:
    """Format seconds as ``MM:SS`` or ``H:MM:SS``."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


_NUMBER_SUFFIXES = ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"))


def format_number(val: float) -> str:
    """Objective values with an SI suffix and 6 significant digits.

    ``1234567890.0`` -> ``1.23457G``; ``0.123`` -> ``0.123``.  Six digits
    keep LB / UB distinguishable near convergence while the column stays
    ~9 characters instead of 13+ with thousands separators.
    """
    for scale, suffix in _NUMBER_SUFFIXES:
        if abs(val) >= scale:
            return f"{val / scale:.6g}{suffix}"
    return f"{val:.6g}"


def format_mb(mb: float) -> str:
    """Render a megabyte count as MB or GB depending on magnitude."""
    if mb >= 1024.0:
        return f"{mb / 1024.0:.1f} GB"
    return f"{mb:.0f} MB"


def progress_bar(fraction: float, width: int = PROGRESS_BAR_WIDTH) -> str:
    """A Rich-markup progress bar *fraction* full (clamped to 0..1)."""
    filled = max(0, min(width, round(fraction * width)))
    return f"[cyan]{'━' * filled}[/cyan][dim]{'─' * (width - filled)}[/dim]"
