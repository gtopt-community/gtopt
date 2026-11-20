"""Keep ``requirements*.txt`` in sync with ``scripts/pyproject.toml``.

``[project].dependencies`` and the ``dev`` extra in ``pyproject.toml``
are the authoritative dependency lists; ``requirements.txt`` and
``requirements-dev.txt`` mirror them for ``pip install -r`` users and
for the CMake ``scripts`` target.  This test fails when they drift.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    tomllib = pytest.importorskip("tomli")

_SCRIPTS = Path(__file__).resolve().parents[2]


def _normalise(req: str) -> str:
    """Strip comments/whitespace and lower-case the distribution name."""
    req = req.split("#", 1)[0].strip().replace(" ", "")
    match = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?(.*)", req)
    assert match, req
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    return f"{name}{match.group(2) or ''}{match.group(3)}"


def _read_requirements(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-r"):
            continue
        out.add(_normalise(line))
    return out


def _pyproject() -> dict:
    with (_SCRIPTS / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_requirements_txt_matches_pyproject_dependencies() -> None:
    """requirements.txt == [project].dependencies."""
    expected = {_normalise(d) for d in _pyproject()["project"]["dependencies"]}
    assert _read_requirements(_SCRIPTS / "requirements.txt") == expected


def test_requirements_dev_txt_matches_dev_extra() -> None:
    """requirements-dev.txt == the ``dev`` extra (plus ``-r requirements.txt``)."""
    project = _pyproject()["project"]
    expected = {_normalise(d) for d in project["optional-dependencies"]["dev"]}
    dev_txt = _SCRIPTS / "requirements-dev.txt"
    assert _read_requirements(dev_txt) == expected
    assert "-r requirements.txt" in dev_txt.read_text(encoding="utf-8")
