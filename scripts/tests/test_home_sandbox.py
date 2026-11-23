"""Guard: tests never resolve per-user state outside the temp sandbox.

``scripts/conftest.py`` points ``HOME`` / ``XDG_*`` at a per-process temp
directory before any gtopt module is imported.  These tests pin that
contract, including the module-level path constants that are computed at
import time and would otherwise write into the real ``~/.cache/gtopt``
(e.g. the plexos2gtopt run registry appended by every conversion).
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# (module, attribute) of every import-time per-user path constant.
_IMPORT_TIME_PATHS = [
    ("plexos2gtopt.plexos2gtopt", "PLEXOS_RUN_REGISTRY"),
    ("plp2gtopt._plexos_overlay", "_PLEXOS_RUN_REGISTRY"),
    ("gtopt_config._config", "DEFAULT_CONFIG_PATH"),
    ("gtopt_config", "DEFAULT_CONFIG_PATH"),
    ("plp2gtopt.main", "DEFAULT_CONFIG_PATH"),
    ("gtopt_check_json._config", "_OLD_CONFIG_PATH"),
    ("gtopt_check_lp._config", "_OLD_CONFIG_PATH"),
    ("gtopt_compress_lp._config", "_OLD_CONFIG_PATH"),
    ("cen2gtopt.pcp_archive", "DEFAULT_INDEX_PATH"),
    ("cen2gtopt.pcp_archive", "DEFAULT_DOWNLOAD_ROOT"),
    ("cen2gtopt._cached_extractor", "_DEFAULT_CACHE_ROOT"),
    ("nrel118_to_gtopt._data", "DEFAULT_CACHE_DIR"),
]


def _inside(path: Path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def test_home_points_at_temp_sandbox(fake_home: Path, real_home: Path) -> None:
    """HOME and every XDG base dir live in the per-process temp sandbox."""
    assert Path.home() == fake_home
    assert Path("~").expanduser() == fake_home
    assert _inside(fake_home, Path(tempfile.gettempdir()))
    assert fake_home != real_home
    for var in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert _inside(Path(os.environ[var]), fake_home), var


@pytest.mark.parametrize(("module", "attr"), _IMPORT_TIME_PATHS)
def test_import_time_paths_are_sandboxed(
    module: str, attr: str, fake_home: Path
) -> None:
    """Module-level per-user paths captured at import resolve in the sandbox."""
    value = getattr(importlib.import_module(module), attr)
    assert _inside(Path(value), fake_home), f"{module}.{attr} = {value}"


def test_subprocesses_inherit_sandbox(fake_home: Path) -> None:
    """CLI subprocesses spawned by tests see the sandboxed HOME too."""
    out = subprocess.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path.home())"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(out) == fake_home
