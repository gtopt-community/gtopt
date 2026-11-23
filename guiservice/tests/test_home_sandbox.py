"""Guard: guiservice tests never resolve HOME outside the temp sandbox."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_home_points_at_temp_sandbox(fake_home: Path, real_home: Path) -> None:
    """HOME and every XDG base dir live in the per-process temp sandbox."""
    assert Path.home() == fake_home
    assert fake_home != real_home
    assert fake_home.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    for var in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert Path(os.environ[var]).is_relative_to(fake_home), var


def test_subprocesses_inherit_sandbox(fake_home: Path) -> None:
    """Subprocesses spawned by tests (e.g. the server launcher) see it too."""
    out = subprocess.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path.home())"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(out) == fake_home
