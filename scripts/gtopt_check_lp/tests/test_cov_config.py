# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_lp._config`` (git, migration, wizard)."""

from __future__ import annotations

import configparser
import io
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_check_lp import _config as cf


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(name="home")
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cf, "_OLD_CONFIG_PATH", tmp_path / "old.conf")
    monkeypatch.setattr(cf, "DEFAULT_CONFIG_PATH", tmp_path / "gtopt.conf")
    return tmp_path


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return parser


# ---------------------------------------------------------------------------
# read_git_email
# ---------------------------------------------------------------------------


def test_git_email_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf.shutil, "which", lambda _n: None)
    assert cf.read_git_email() == ""

    monkeypatch.setattr(cf.shutil, "which", lambda n: f"/x/{n}")
    monkeypatch.setattr(
        cf.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=" a@b.c \n")
    )
    assert cf.read_git_email() == "a@b.c"

    def _timeout(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(cf.subprocess, "run", _timeout)
    assert cf.read_git_email() == ""


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


class TestMigration:
    """Legacy ``~/.gtopt_check_lp.conf`` migration."""

    def test_no_old_file(self, home: Path) -> None:
        cf._migrate_old_config()  # noqa: SLF001
        assert not (home / "gtopt.conf").exists()

    def test_bad_old_file(self, home: Path) -> None:
        (home / "old.conf").write_text("garbage without header\n")
        cf._migrate_old_config()  # noqa: SLF001
        assert not (home / "gtopt.conf").exists()

    def test_no_section(self, home: Path) -> None:
        (home / "old.conf").write_text("[x]\na = 1\n")
        cf._migrate_old_config()  # noqa: SLF001
        assert not (home / "gtopt.conf").exists()

    def test_empty_section(self, home: Path) -> None:
        (home / "old.conf").write_text("[gtopt_check_lp]\n")
        cf._migrate_old_config()  # noqa: SLF001
        assert not (home / "gtopt.conf").exists()

    def test_migrates_via_load_config(self, home: Path) -> None:
        (home / "old.conf").write_text(
            "[gtopt_check_lp]\nemail = a@b\nai_provider = openai\n"
        )
        cfg = cf.load_config(home / "gtopt.conf")
        assert cfg["email"] == "a@b"
        assert cfg["ai_provider"] == "openai"

    def test_only_global(self, home: Path) -> None:
        (home / "old.conf").write_text("[gtopt_check_lp]\ncolor = never\n")
        cf._migrate_old_config()  # noqa: SLF001
        parser = _read(home / "gtopt.conf")
        assert parser.get("global", "color") == "never"
        assert not parser.has_section("gtopt_check_lp")


def test_save_config_partitions(tmp_path: Path) -> None:
    only_tool = tmp_path / "a.conf"
    cf.save_config(only_tool, {"email": "x@y"})
    assert _read(only_tool).get("gtopt_check_lp", "email") == "x@y"
    only_global = tmp_path / "b.conf"
    cf.save_config(only_global, {"color": "never"})
    parser = _read(only_global)
    assert parser.get("global", "color") == "never"
    assert not parser.has_section("gtopt_check_lp")


# ---------------------------------------------------------------------------
# solver status
# ---------------------------------------------------------------------------


def test_solver_status_highspy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf.shutil, "which", lambda _n: None)
    monkeypatch.setattr("gtopt_check_lp._solvers.find_cplex_binary", lambda: None)
    monkeypatch.setitem(sys.modules, "highspy", types.ModuleType("highspy"))
    status = {b: (inst, hint) for b, _l, inst, hint in cf.get_solver_status()}
    assert status["highs"] == (True, None)
    assert status["cbc"][0] is False
    assert status["cplex"] == (False, None)


@pytest.mark.parametrize("use_color", [True, False])
def test_print_solver_status_all_installed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    use_color: bool,
) -> None:
    monkeypatch.setattr(
        cf, "get_solver_status", lambda: [("cbc", "COIN-OR CBC", True, None)]
    )
    cf.print_solver_status(use_color=use_color)
    out = capsys.readouterr().out
    assert "COIN-OR CBC" in out
    assert "To install" not in out
    assert ("\033[" in out) is use_color


# ---------------------------------------------------------------------------
# _prompt and wizard
# ---------------------------------------------------------------------------


class TestPrompt:
    """``_prompt`` on tty stdin."""

    def test_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())
        seen: list[str] = []
        answers = iter(["  hi ", ""])

        def _input(p: str) -> str:
            seen.append(p)
            return next(answers)

        monkeypatch.setattr("builtins.input", _input)
        assert cf._prompt("Q", "d") == "hi"  # noqa: SLF001
        assert cf._prompt("Q") == ""  # noqa: SLF001
        assert seen == ["Q [d]: ", "Q: "]

    @pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
    def test_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
    ) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())

        def _raise(_p: str) -> str:
            raise exc

        monkeypatch.setattr("builtins.input", _raise)
        assert cf._prompt("Q", "d") == "d"  # noqa: SLF001


def _wizard(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    answers: list[str],
    git_email: str,
) -> dict[str, str]:
    it = iter(answers)
    monkeypatch.setattr(cf, "_prompt", lambda _t, d="": next(it) or d)
    monkeypatch.setattr(cf, "read_git_email", lambda: git_email)
    monkeypatch.setattr(cf, "print_solver_status", lambda use_color: None)
    return cf.run_interactive_setup(path, use_color=False)


def test_wizard_defaults_with_git_email(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = home / "w.conf"
    cfg = _wizard(monkeypatch, path, [""] * 7, "git@x")
    assert cfg["email"] == "git@x"
    assert cfg["solver"] == "auto"
    assert cfg["timeout"] == "5"
    assert cfg["ai_enabled"] in ("true", "false")
    out = capsys.readouterr().out
    assert "Found git user.email: git@x" in out
    assert "Configuration saved to" in out
    assert cf.load_config(path)["email"] == "git@x"


def test_wizard_invalid_answers(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = home / "w.conf"
    cf.save_config(path, {"email": "cur@x"})
    answers = ["", "bogus", "ten", "rainbow", "maybe", "nobody", "my-model"]
    cfg = _wizard(monkeypatch, path, answers, "git@x")
    assert cfg["email"] == "cur@x"
    assert cfg["solver"] == "all"
    assert cfg["timeout"] == "5"
    assert cfg["color"] == "auto"
    # An invalid answer falls back to "false": AI stays opt-in.
    assert cfg["ai_enabled"] == "false"
    assert cfg["ai_provider"] == cf._AI_DEFAULT_PROVIDER  # noqa: SLF001
    assert cfg["ai_model"] == "my-model"
    out = capsys.readouterr().out
    assert "Current email: cur@x" in out
    assert "Unknown solver 'bogus'" in out
    assert "Invalid timeout 'ten'" in out
    assert "Unknown provider 'nobody'" in out


def test_wizard_no_email_anywhere(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _wizard(monkeypatch, home / "w.conf", ["", "cplex", "30"] + [""] * 4, "")
    assert cfg["email"] == ""
    assert cfg["solver"] == "cplex"
    assert cfg["timeout"] == "30"
    out = capsys.readouterr().out
    assert "git user.email" not in out
    assert "Current email" not in out
