"""Smoke test: ``python -m plp2gtopt`` runs the CLI."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2]


def test_python_m_help_in_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running the package as __main__ dispatches to main()."""
    monkeypatch.setattr(sys, "argv", ["plp2gtopt", "--help"])
    monkeypatch.delitem(sys.modules, "plp2gtopt.__main__", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("plp2gtopt", run_name="__main__")
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_python_m_help_subprocess() -> None:
    """``python -m plp2gtopt --help`` exits 0 in a fresh interpreter."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_SCRIPTS_DIR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    proc = subprocess.run(
        [sys.executable, "-m", "plp2gtopt", "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout
