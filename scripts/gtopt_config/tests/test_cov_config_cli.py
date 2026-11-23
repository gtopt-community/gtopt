# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case tests for gtopt_config._config and gtopt_config._cli."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pytest

from gtopt_config import _cli, _config
from gtopt_config._cli import (
    add_color_argument,
    add_common_arguments,
    add_log_level_argument,
    configure_logging,
    get_version,
    resolve_color,
)
from gtopt_config._config import get_section, load_config, save_section


# ---------------------------------------------------------------------------
# _config
# ---------------------------------------------------------------------------


def test_load_config_parse_error_returns_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad.conf"
    bad.write_text("no section header here\nkey = value\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = load_config(bad)
    assert cfg.get("global", "ai_provider") == "claude"
    assert "error reading" in caplog.text


def test_load_config_fills_missing_global_keys(tmp_path: Path) -> None:
    path = tmp_path / "c.conf"
    path.write_text("[global]\nai_provider = openai\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.get("global", "ai_provider") == "openai"
    assert cfg.get("global", "color") == "auto"


def test_load_config_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "home.conf"
    path.write_text("[tool]\nx = 1\n", encoding="utf-8")
    monkeypatch.setattr(_config, "DEFAULT_CONFIG_PATH", path)
    cfg = load_config()
    assert get_section(cfg, "tool", "x") == "1"
    assert get_section(cfg, "tool", "missing", "fb") == "fb"


def test_save_section_existing_section_merges(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "c.conf"
    save_section(path, "tool", {"a": "1"})
    save_section(path, "tool", {"b": "2"})
    cfg = load_config(path)
    assert get_section(cfg, "tool", "a") == "1"
    assert get_section(cfg, "tool", "b") == "2"
    assert path.read_text(encoding="utf-8").startswith("# gtopt unified")


def test_save_section_write_error_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Parent "directory" is a regular file -> mkdir raises OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        save_section(blocker / "c.conf", "tool", {"a": "1"})
    assert "cannot write config" in caplog.text


# ---------------------------------------------------------------------------
# _cli
# ---------------------------------------------------------------------------


def test_get_version_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata as md  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(md, "version", lambda _name: "9.9.9")
    assert get_version() == "9.9.9"


def test_get_version_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata as md  # pylint: disable=import-outside-toplevel

    def _raise(_name: str) -> str:
        raise md.PackageNotFoundError(_name)

    monkeypatch.setattr(md, "version", _raise)
    assert get_version() == "dev"


def test_add_common_arguments_and_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_cli, "get_version", lambda: "1.2.3")
    parser = argparse.ArgumentParser(prog="tool")
    add_common_arguments(parser, default_log_level="WARNING")
    args = parser.parse_args([])
    assert args.log_level == "WARNING"
    assert args.no_color is False
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])
    assert "tool 1.2.3" in capsys.readouterr().out


def test_add_log_level_rejects_unknown_level() -> None:
    parser = argparse.ArgumentParser()
    add_log_level_argument(parser)
    assert parser.parse_args(["-l", "DEBUG"]).log_level == "DEBUG"
    with pytest.raises(SystemExit):
        parser.parse_args(["-l", "LOUD"])


def test_resolve_color_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = argparse.ArgumentParser()
    add_color_argument(parser)
    assert resolve_color(parser.parse_args(["--no-color"])) is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_color(argparse.Namespace()) is False

    monkeypatch.delenv("NO_COLOR")

    class _Tty:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(_cli.sys, "stdout", _Tty())
    assert resolve_color(argparse.Namespace(no_color=False)) is True


@pytest.mark.parametrize(
    ("ns", "expected"),
    [
        (argparse.Namespace(log_level="INFO", verbose=True), logging.DEBUG),
        (argparse.Namespace(log_level="ERROR", verbose=True), logging.ERROR),
        (argparse.Namespace(), logging.INFO),
        (argparse.Namespace(log_level="bogus"), logging.INFO),
    ],
)
def test_configure_logging_levels(
    monkeypatch: pytest.MonkeyPatch, ns: argparse.Namespace, expected: int
) -> None:
    captured: dict[str, object] = {}

    def _fake_basic_config(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(_cli.logging, "basicConfig", _fake_basic_config)
    configure_logging(ns)
    assert captured["level"] == expected
    assert captured["format"] == "%(levelname)s: %(message)s"

    configure_logging(ns, fmt="%(message)s")
    assert captured["format"] == "%(message)s"
