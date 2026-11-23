"""Tests for :mod:`plp2gtopt._progress` (conversion checklist + file logging)."""

from __future__ import annotations

import builtins
import io
import logging
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from plp2gtopt import _progress
from plp2gtopt._progress import ConversionProgress, setup_file_logging


def _force_console(monkeypatch: pytest.MonkeyPatch, *, terminal: bool) -> io.StringIO:
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=terminal, width=100)
    monkeypatch.setattr("gtopt_check_json._terminal.console", lambda: con)
    return buf


def test_terminal_live_display(monkeypatch: pytest.MonkeyPatch) -> None:
    buf = _force_console(monkeypatch, terminal=True)
    with ConversionProgress(steps=[("a", "Step A"), ("b", "Step B"), ("c", "C")]) as p:
        p.skip("c")
        p.skip("unknown")
        p.step("a")
        text = p._render().plain  # pylint: disable=protected-access
        assert "Step A..." in text and "○ Step B" in text
        p.step("b")
        p.step("missing")  # unknown key: previous step completes, nothing active
        p.done()
        p.done()  # idempotent
        text = p._render().plain  # pylint: disable=protected-access
        assert "✓ Step A" in text and "C" not in text.split("Converting")[0]
    final = buf.getvalue()
    assert "Step A" in final and "Step B" in final


def test_final_render_lists_pending_steps() -> None:
    p = ConversionProgress(steps=[("a", "Step A"), ("b", "Step B")])
    p.step("a")
    p.done()
    text = p._render_final().plain  # pylint: disable=protected-access
    assert "✓ Step A" in text and "○ Step B" in text
    assert _progress._StepState(label="x").elapsed == 0.0  # pylint: disable=protected-access


def test_plain_output_when_not_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _force_console(monkeypatch, terminal=False)
    with pytest.raises(RuntimeError):
        with ConversionProgress(steps=[("a", "Step A")]) as p:
            p.step("a")
            raise RuntimeError("boom")
    assert "* Step A..." in capsys.readouterr().err


def test_plain_output_without_terminal_helper(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "gtopt_check_json._terminal":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with ConversionProgress(steps=[("a", "Step A")]) as p:
        p.step("a")
        p.done()
    assert "* Step A..." in capsys.readouterr().err


@pytest.fixture(name="root_logger")
def fixture_root_logger():  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield root
    for h in root.handlers[:]:
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)


def test_setup_file_logging_default_dir(tmp_path: Path, root_logger) -> None:  # type: ignore[no-untyped-def]
    root_logger.setLevel(logging.WARNING)
    console = logging.StreamHandler(io.StringIO())
    root_logger.addHandler(console)
    handler = setup_file_logging(tmp_path)
    assert handler is not None
    assert (tmp_path / "logs" / "conversion.log").exists()
    assert root_logger.level == logging.DEBUG
    assert console.level == logging.WARNING
    root_logger.removeHandler(console)


def test_setup_file_logging_explicit_file(tmp_path: Path, root_logger) -> None:  # type: ignore[no-untyped-def]
    root_logger.setLevel(logging.DEBUG)
    log = tmp_path / "sub" / "run.log"
    handler = setup_file_logging(tmp_path, log)
    assert handler is not None and log.exists()


def test_setup_file_logging_unwritable(tmp_path: Path) -> None:
    blocker = tmp_path / "out"
    blocker.write_text("not a dir", encoding="utf-8")
    assert setup_file_logging(blocker) is None
