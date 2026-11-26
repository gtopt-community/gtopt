# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Discover the gtopt binary and the converter scripts.

Shared by :mod:`run_gtopt` and :mod:`gtopt_check_solvers`.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path


def ci_bin_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Per-user directory for the downloaded CI ``gtopt`` artifact.

    ``$XDG_CACHE_HOME/gtopt/ci-bin`` (default ``~/.cache/gtopt/ci-bin``):
    never a shared, world-writable location such as ``/tmp``.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gtopt" / "ci-bin"


def _standard_build_paths() -> list[Path]:
    """Return standard build paths relative to the repo root."""
    # Walk up from this file to the repo root (scripts/gtopt_shared/gtopt_binary.py)
    repo = Path(__file__).resolve().parent.parent.parent
    return [
        repo / "build" / "standalone" / "gtopt",
        repo / "build" / "test" / "_deps" / "gtopt-build" / "standalone" / "gtopt",
        ci_bin_dir() / "gtopt",
    ]


def find_gtopt_binary() -> str | None:
    """Locate the gtopt binary.

    Search order:
    1. ``GTOPT_BIN`` environment variable.
    2. ``gtopt`` on ``PATH``.
    3. Standard build directories.
    """
    env_bin = os.environ.get("GTOPT_BIN")
    if env_bin and Path(env_bin).is_file():
        return env_bin

    on_path = shutil.which("gtopt")
    if on_path:
        return on_path

    for candidate in _standard_build_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def find_plp2gtopt() -> str | None:
    """Locate the plp2gtopt script on PATH."""
    return shutil.which("plp2gtopt")


def find_plexos2gtopt() -> str | None:
    """Locate the plexos2gtopt script on PATH."""
    return shutil.which("plexos2gtopt")
