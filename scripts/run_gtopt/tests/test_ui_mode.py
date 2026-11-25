# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""--no-color / NO_COLOR and --ui / --no-tui selection."""

from __future__ import annotations

import io
import os

import pytest

from run_gtopt import _tui
from run_gtopt import main as main_mod


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_tui, "_ui_mode", "auto")
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.delenv("NO_COLOR")


def test_is_interactive_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_tui.sys, "stdout", _Tty())
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _tui.is_interactive()
    monkeypatch.setenv("TERM", "dumb")
    assert not _tui.is_interactive()
    _tui.set_ui_mode("tui")
    assert _tui.is_interactive()
    _tui.set_ui_mode("plain")
    monkeypatch.setenv("TERM", "xterm")
    assert not _tui.is_interactive()
    with pytest.raises(ValueError):
        _tui.set_ui_mode("fancy")


def test_is_interactive_auto_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_tui.sys, "stdout", io.StringIO())
    monkeypatch.setenv("TERM", "xterm")
    assert not _tui.is_interactive()


@pytest.mark.parametrize(
    ("argv", "mode"),
    [
        ([], "auto"),
        (["--ui", "tui"], "tui"),
        (["--ui", "plain"], "plain"),
        (["--no-tui"], "plain"),
        (["--plain"], "plain"),
    ],
)
def test_parser_ui_flags(argv: list[str], mode: str) -> None:
    assert main_mod.make_parser().parse_args(argv).ui == mode


def test_main_applies_no_color_and_ui(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(main_mod.signal, "signal", lambda *_a: None)
    main_mod.main(["--no-color", "--no-tui", "--list-checks"])
    assert os.environ["NO_COLOR"] == "1"
    assert _tui._ui_mode == "plain"  # pylint: disable=protected-access
    assert "STAGE  DEFAULT" in capsys.readouterr().out


def test_no_color_reaches_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console  # noqa: PLC0415

    monkeypatch.setenv("NO_COLOR", "1")
    buf = io.StringIO()
    console = Console(force_terminal=True, file=buf)
    console.print("[red]x[/red]")
    assert "\x1b[31m" not in buf.getvalue()
