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
from pathlib import Path, PurePosixPath

#: Environment variable naming the case-repository checkout.
CASES_DIR_ENV = "GTOPT_CASES_DIR"


def cases_root() -> Path | None:
    """Return ``$GTOPT_CASES_DIR`` as a directory, or ``None`` if unset."""
    env = os.environ.get(CASES_DIR_ENV, "").strip()
    if not env:
        return None
    root = Path(env).expanduser()
    return root if root.is_dir() else None


def external_case_path(relpath: str) -> Path | None:
    """Return ``$GTOPT_CASES_DIR/<relpath>`` if it exists, else ``None``.

    ``relpath`` is a POSIX path relative to the case-repository root.
    When the full path is missing, its trailing components are tried
    directly below ``GTOPT_CASES_DIR`` (longest first), so a variable
    that points at an application folder still resolves.
    """
    root = cases_root()
    if root is None:
        return None
    parts = PurePosixPath(relpath).parts
    for start in range(len(parts)):
        candidate = root.joinpath(*parts[start:])
        if candidate.exists():
            return candidate
    return None


_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
_REPO_DIR = _SCRIPTS_DIR.parent


def external_case_dir(name: str) -> Path | None:
    """Return the directory of external case *name*, or ``None``.

    Compatibility lookup by bare case name.  Search order:
    ``$GTOPT_CASES_DIR/<name>``, then ``$GTOPT_CASES_DIR/*/*/<name>``
    (the ``<country>/<application>/<case>`` layout of gtopt-cases),
    then the in-repo ``cases/<name>`` and ``scripts/cases/<name>``.
    """
    candidates: list[Path] = []
    root = cases_root()
    if root is not None:
        candidates.append(root / name)
        candidates.extend(sorted(root.glob(f"*/*/{name}")))
    candidates.append(_REPO_DIR / "cases" / name)
    candidates.append(_SCRIPTS_DIR / "cases" / name)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def missing_case_reason(relpath: str) -> str:
    """Return a skip reason explaining how to enable tests on *relpath*."""
    root = os.environ.get(CASES_DIR_ENV, "")
    where = f"{CASES_DIR_ENV}={root!r}" if root else f"{CASES_DIR_ENV} is unset"
    return (
        f"external case data {relpath!r} not found ({where}); "
        "run tools/get_cases.sh and set GTOPT_CASES_DIR to enable"
    )
