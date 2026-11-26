# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Terminal capabilities: ``--ui`` mode, the Rich console, ASCII fallback."""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import Any

#: ``--ui`` selection: ``auto`` (TUI on a capable TTY), ``tui``, ``plain``.
UI_MODES = ("auto", "tui", "plain")
_ui_mode = "auto"


def set_ui_mode(mode: str) -> None:
    """Select the display mode (``run_gtopt --ui`` / ``--no-tui``)."""
    global _ui_mode  # noqa: PLW0603  # pylint: disable=global-statement
    if mode not in UI_MODES:
        raise ValueError(f"unknown UI mode {mode!r}")
    _ui_mode = mode


def ui_mode() -> str:
    """The mode chosen by :func:`set_ui_mode` (default ``auto``)."""
    return _ui_mode


def is_interactive() -> bool:
    """Return True when the live Rich dashboard should be used.

    ``--ui plain`` / ``--no-tui`` always disable it and ``--ui tui``
    forces it; ``auto`` uses it when stdout is a terminal that is not
    ``TERM=dumb`` (emacs shell buffers, some CI ptys).
    """
    if _ui_mode == "plain":
        return False
    if _ui_mode == "tui":
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def make_console(**kwargs: Any) -> Any:
    """The Rich Console used by the dashboard and the banners.

    ``GTOPT_TUI_WIDTH`` / ``GTOPT_TUI_HEIGHT`` override the detected
    terminal size (handy under tmux / script(1) / screenshots).
    """
    from rich.console import Console  # noqa: PLC0415

    for env, key in (("GTOPT_TUI_WIDTH", "width"), ("GTOPT_TUI_HEIGHT", "height")):
        raw = os.environ.get(env, "")
        if raw.isdigit() and int(raw) > 0 and key not in kwargs:
            kwargs[key] = int(raw)
    return Console(**kwargs)


# --- ASCII fallback ---------------------------------------------------------
# Glyphs the dashboard draws -> one ASCII stand-in of the same cell width.
_ASCII_GLYPHS = {
    "✓": "v",
    "✗": "x",
    "⚠": "!",
    "⏸": "|",
    "↪": ">",
    "○": "o",
    "━": "=",
    "░": ".",
    "…": "~",
    "→": ">",
    "·": ".",
    "–": "-",
    "—": "-",
    "×": "x",
    "Δ": "d",
    "⚡": "**",
    **dict(zip("▁▂▃▄▅▆▇█", "._-=+*#@", strict=True)),
    **{ch: "|/-\\"[i % 4] for i, ch in enumerate("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")},
}


def _box_ascii(ch: str) -> str:
    name = unicodedata.name(ch, "")
    horizontal, vertical = "HORIZONTAL" in name, "VERTICAL" in name
    if horizontal and not vertical:
        return "-"
    if vertical and not horizontal:
        return "|"
    return "+"


_ASCII_TABLE = str.maketrans(
    {
        **{chr(c): _box_ascii(chr(c)) for c in range(0x2500, 0x2580)},
        **_ASCII_GLYPHS,
    }
)


def ascii_wanted(encoding: str | None = None) -> bool:
    """True when the dashboard should draw ASCII only.

    ``--ascii`` / ``GTOPT_TUI_ASCII=1`` force it; otherwise it is used
    when the output encoding or the locale (``LC_ALL`` > ``LC_CTYPE`` >
    ``LANG``, when set) is not UTF-8 — e.g. ``LANG=C`` over ssh, where
    braille spinners and block glyphs print as garbage.
    """
    forced = os.environ.get("GTOPT_TUI_ASCII", "").lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced in ("0", "false", "no", "off"):
        return False
    if encoding and not encoding.lower().replace("-", "").startswith("utf"):
        return True
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(var)
        if value:
            return "utf" not in value.lower().replace("-", "")
    return False


class AsciiRender:
    """Render *renderable* with every non-ASCII glyph replaced."""

    def __init__(self, renderable: Any) -> None:
        self.renderable = renderable

    def __rich_console__(self, console: Any, options: Any) -> Any:
        from rich.segment import Segment  # noqa: PLC0415

        for seg in console.render(self.renderable, options):
            if seg.control or not seg.text:
                yield seg
            else:
                yield Segment(seg.text.translate(_ASCII_TABLE), seg.style)
