# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for the gtopt_check_pampl CLI driver and interactive config."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from gtopt_check_pampl import _config as cfgmod
from gtopt_check_pampl import gtopt_check_pampl as cli
from gtopt_check_pampl._checks import Finding, Severity


def test_interactive_setup_scripted_answers(tmp_path, monkeypatch, capsys):
    answers = iter(
        ["sometimes"]  # invalid color → auto
        + ["maybe"]  # first check: invalid → default
        + ["false"] * (len(cfgmod.CHECK_DEFAULTS) - 1)
        + ["perhaps", "nonexistent", "my-model"]
    )
    monkeypatch.setattr(cfgmod, "_prompt", lambda _t, _d="": next(answers))
    conf = tmp_path / "gtopt.conf"
    cfg = cfgmod.run_interactive_setup(conf, use_color=True)
    first = next(iter(cfgmod.CHECK_DEFAULTS))
    assert cfg["color"] == "auto"
    assert cfg[f"check_{first}"] == cfgmod.CHECK_DEFAULTS[first]
    assert cfg["ai_enabled"] == "false"
    assert cfg["ai_provider"] == cfgmod._AI_DEFAULT_PROVIDER
    assert cfg["ai_model"] == "my-model"
    assert "Unknown provider" in capsys.readouterr().out
    reloaded = cfgmod.load_config(conf)
    assert reloaded["ai_model"] == "my-model"


def test_save_config_tool_only(tmp_path):
    conf = tmp_path / "c.conf"
    cfgmod.save_config(conf, {"check_syntax": "false"})
    assert not cfgmod.is_check_enabled(cfgmod.load_config(conf), "syntax")
    cfgmod.save_config(conf, {"color": "never"})
    assert cfgmod.load_config(conf)["color"] == "never"


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_prompt_paths(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert cfgmod._prompt("q", "dflt") == "dflt"
    monkeypatch.setattr("sys.stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda _p: "  typed ")
    assert cfgmod._prompt("q", "dflt") == "typed"
    monkeypatch.setattr("builtins.input", lambda _p: "")
    assert cfgmod._prompt("q") == ""

    def _eof(_p):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert cfgmod._prompt("q", "d") == "d"


def test_check_pampl_disabled_checks_and_unreadable(tmp_path, capsys):
    conf = tmp_path / "c.conf"
    cfgmod.save_config(conf, {"check_syntax": "false"})
    assert cli.check_pampl([str(tmp_path)], config_path=conf) == 2
    assert "cannot read file" in capsys.readouterr().err


def test_check_pampl_finding_formatting(tmp_path, monkeypatch, capsys):
    src = tmp_path / "a.pampl"
    src.write_text("# empty\n", encoding="utf-8")
    findings = [
        Finding("x", Severity.CRITICAL, "crit", file="a.pampl"),
        Finding("y", Severity.WARNING, "warn", action="fix it"),
        Finding("z", Severity.NOTE, "note", file="a.pampl", line=3),
    ]
    monkeypatch.setattr(cli, "run_all_checks", lambda *_a, **_k: findings)
    monkeypatch.setattr(cli, "USE_COLOR", False)
    rc = cli.check_pampl([str(src)], config_path=tmp_path / "none.conf")
    out = capsys.readouterr().out
    assert rc == 1
    assert "[CRITICAL] a.pampl — crit" in out
    assert "[WARNING] warn" in out
    assert "→ fix it" in out
    assert "[NOTE] a.pampl:3 — note" in out


def test_main_init_config(tmp_path, monkeypatch):
    called: list[Path] = []
    monkeypatch.setattr(
        cli, "run_interactive_setup", lambda p, use_color: called.append(p) or {}
    )
    conf = tmp_path / "x.conf"
    assert cli.main(["--init-config", "--config", str(conf), "--no-color"]) == 0
    assert called == [conf]


def test_print_stats_without_lists(capsys):
    stats = cli.compute_stats("", "empty.pampl")
    cli.print_stats(stats)
    out = capsys.readouterr().out
    assert "Element types" not in out
    assert "LP variables" not in out


@pytest.mark.parametrize("flag", ["--show-config"])
def test_main_show_config(tmp_path, capsys, flag):
    assert cli.main([flag, "--config", str(tmp_path / "c.conf")]) == 0
    assert "Active configuration" in capsys.readouterr().out


def test_interactive_setup_valid_answers(tmp_path, monkeypatch):
    answers = iter(
        ["never"] + ["true"] * len(cfgmod.CHECK_DEFAULTS) + ["true", "openai", ""]
    )
    monkeypatch.setattr(cfgmod, "_prompt", lambda _t, _d="": next(answers))
    cfg = cfgmod.run_interactive_setup(tmp_path / "g.conf", use_color=False)
    assert cfg["color"] == "never"
    assert cfg["ai_enabled"] == "true"
    assert cfg["ai_provider"] == "openai"
