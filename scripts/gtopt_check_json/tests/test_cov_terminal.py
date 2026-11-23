# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the Rich terminal helpers of gtopt_check_json."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from gtopt_check_json import _terminal as tm


@pytest.fixture(autouse=True)
def _reset_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tm, "_console", None)
    monkeypatch.setattr(tm, "_force_color", None)
    monkeypatch.setattr(tm, "_force_ascii", None)


def test_get_console_modes() -> None:
    assert tm.get_console(force_color=True).is_terminal
    con = tm.get_console(force_color=False, stderr=False)
    assert not con.is_terminal
    assert con.no_color


def test_init_and_console_reuse() -> None:
    con = tm.init(force_color=False)
    assert tm.console() is con  # same stderr -> reused


def test_print_helpers(capsys: pytest.CaptureFixture[str]) -> None:
    tm.print_kv_table([])
    tm.print_kv_table([("a", "1")], title="KV")
    tm.print_status("good", True)
    tm.print_status("bad", False, details="why")
    for sev in ("critical", "WARNING", "note"):
        tm.print_finding(sev, "chk", f"msg-{sev}")
    tm.print_section("Sec")
    tm.print_summary(1, 2, 3)
    err = capsys.readouterr().err
    assert "KV" in err
    assert "why" in err
    assert "[CRITICAL] (chk) msg-critical" in err
    assert "[WARNING] (chk) msg-WARNING" in err
    assert "[NOTE] (chk) msg-note" in err
    assert "Summary: 1 critical, 2 warnings, 3 notes" in err


def test_print_table_options(capsys: pytest.CaptureFixture[str]) -> None:
    tm.init(force_color=True)
    tm.print_table(
        ["A", "B"],
        [["1"], ["2", "3"]],
        aligns=["left", "right"],
        styles=["bold", ""],
        min_widths=[5],
        wraps=[True],
        title="T",
        show_lines=True,
        width=60,
    )
    err = capsys.readouterr().err
    assert "T" in err
    assert "╭" in err  # rounded box on a terminal


def test_connectivity_table(capsys: pytest.CaptureFixture[str]) -> None:
    tm.print_connectivity_table([])
    assert capsys.readouterr().err == ""
    island = SimpleNamespace(
        island_id=2,
        buses=[f"b{i}" for i in range(7)],
        line_names=[],
        generator_names=["g"],
        demand_names=[],
        battery_names=[],
    )
    tm.print_connectivity_table([island])
    err = capsys.readouterr().err
    assert "(+2)" in err
    assert "1 islands, 7 buses" in err


def test_render_kv_table() -> None:
    assert tm.render_kv_table([]) == ""
    text = tm.render_kv_table([("key", "val")], title="Title")
    assert "Title" in text
    assert "key" in text


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.INFO, "hello"),
        (logging.WARNING, "WARNING: hello"),
        (5, "hello"),
    ],
)
def test_clean_formatter(level: int, expected: str) -> None:
    record = logging.LogRecord("x", level, __file__, 1, "hello", None, None)
    assert tm.CleanFormatter().format(record) == expected


def test_clean_formatter_debug() -> None:
    record = logging.LogRecord("x", logging.DEBUG, __file__, 1, "dbg", None, None)
    assert tm.CleanFormatter().format(record).endswith("DEBUG dbg")


def test_fmt_num() -> None:
    assert tm.fmt_num(1395.254) == "1'395.25"
