# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Single-key input: cbreak mode and escape-sequence decoding (POSIX)."""

from __future__ import annotations

import sys
from typing import Any


def enter_cbreak() -> Any:
    """Set terminal to cbreak mode (single char, no echo).

    Returns the original settings for restoration, or None if
    cbreak mode is not available.
    """
    try:
        import termios  # noqa: PLC0415
        import tty  # noqa: PLC0415

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        return old
    except (ImportError, termios.error, OSError, AttributeError):
        return None


def exit_cbreak(old_settings: Any) -> None:
    """Restore terminal settings from cbreak mode."""
    if old_settings is None:
        return
    try:
        import termios  # noqa: PLC0415

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
    except (ImportError, termios.error, OSError, AttributeError):
        pass


#: CSI / SS3 sequences (after ESC) for the navigation keys we map.
ESC_KEYS = {
    "[A": "UP",
    "[B": "DOWN",
    "OA": "UP",
    "OB": "DOWN",
    "[5~": "PGUP",
    "[6~": "PGDN",
    "[H": "HOME",
    "[F": "END",
    "OH": "HOME",
    "OF": "END",
}


#: Navigation keys mapped onto the vim-style grid scroll keys.
NAV_KEY_ALIASES = {
    "UP": "k",
    "DOWN": "j",
    "PGUP": "K",
    "PGDN": "J",
    "HOME": "g",
    "END": "G",
}


def _read_ready(sel: Any, timeout: float = 0.0) -> str | None:
    if sel.select([sys.stdin], [], [], timeout)[0]:
        return sys.stdin.read(1)
    return None


def poll_key(cbreak_active: bool) -> str | None:
    """Non-blocking single-key read.  Returns the key or None.

    Escape sequences are decoded so an arrow key is one key (``"UP"``,
    ``"DOWN"``, ``"PGUP"``, ``"PGDN"``, ``"HOME"``, ``"END"``) instead of
    ``[`` + ``A``; a lone ESC is returned as ``"ESC"`` and an unknown
    sequence is swallowed (``""``).
    """
    if not cbreak_active:
        return None
    try:
        import select as sel  # noqa: PLC0415

        key = _read_ready(sel)
        if key != "\x1b":
            return key
        seq = ""
        while len(seq) < 3:
            nxt = _read_ready(sel, 0.01)
            if nxt is None:
                break
            seq += nxt
            if seq in ESC_KEYS:
                return ESC_KEYS[seq]
        return "ESC" if not seq else ""
    except (OSError, ValueError):
        pass
    return None
