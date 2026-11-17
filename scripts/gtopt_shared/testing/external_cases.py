# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Locate optional external study cases that are not shipped with gtopt.

Some large reference cases (for example the 2-year SEN cases
``gtopt_case_2y`` / ``gtopt_case_2y_fs`` or the PLP source case
``plp_case_2y``) live outside the gtopt repository.  Point the
``GTOPT_CASES_DIR`` environment variable at the directory that contains
them to enable the tests that use them; when the case cannot be found
those tests skip.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Environment variable naming the directory holding external cases.
CASES_DIR_ENV = "GTOPT_CASES_DIR"

_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
_REPO_DIR = _SCRIPTS_DIR.parent


def external_case_dir(name: str) -> Path | None:
    """Return the directory of external case *name*, or ``None``.

    Search order: ``$GTOPT_CASES_DIR/<name>``, then the in-repo
    ``cases/<name>`` and ``scripts/cases/<name>`` (for developers who
    drop or symlink the case into the source tree).
    """
    candidates: list[Path] = []
    env = os.environ.get(CASES_DIR_ENV)
    if env:
        candidates.append(Path(env).expanduser() / name)
    candidates.append(_REPO_DIR / "cases" / name)
    candidates.append(_SCRIPTS_DIR / "cases" / name)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def missing_case_reason(name: str) -> str:
    """Return a skip reason explaining how to enable tests on *name*."""
    return f"external case {name!r} not found (set {CASES_DIR_ENV} to enable)"
