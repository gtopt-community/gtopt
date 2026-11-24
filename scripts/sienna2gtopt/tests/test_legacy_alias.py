"""The deprecated ``sienna_to_gtopt`` console script still works."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from sienna2gtopt import __main__ as cli

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_legacy_console_script_registered():
    scripts = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["sienna2gtopt"] == "sienna2gtopt.__main__:main"
    assert scripts["sienna_to_gtopt"] == "sienna2gtopt.__main__:legacy_main"


def test_legacy_main_warns_and_delegates(monkeypatch, capsys):
    seen: list[list[str] | None] = []

    def fake_main(argv: list[str] | None = None) -> int:
        seen.append(argv)
        return 7

    monkeypatch.setattr(cli, "main", fake_main)
    assert cli.legacy_main(["hvdc", "-o", "x.json"]) == 7
    assert seen == [["hvdc", "-o", "x.json"]]
    err = capsys.readouterr().err
    assert "sienna_to_gtopt is deprecated" in err
    assert "sienna2gtopt" in err


def test_legacy_main_runs_real_parser(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.legacy_main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "variant" in captured.out
