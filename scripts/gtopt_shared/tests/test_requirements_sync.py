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


# ---------------------------------------------------------------------------
# Hash-pinned lock files (tools/update_python_locks.sh)
# ---------------------------------------------------------------------------

_REPO = _SCRIPTS.parent
_LOCK_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)")


def _read_lock(path: Path) -> dict[str, str]:
    """Return ``{normalised name: pinned version}``; assert every pin is hashed."""
    pins: dict[str, str] = {}
    current: str | None = None
    hashed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LOCK_RE.match(line)
        if match:
            current = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            pins[current] = match.group(2)
        elif current and line.strip().startswith("--hash=sha256:"):
            hashed.add(current)
    assert pins, f"{path} holds no pinned requirement"
    assert set(pins) == hashed, f"unhashed pins in {path}: {set(pins) - hashed}"
    return pins


def _assert_lock_covers(lock: Path, requirements: list[str]) -> None:
    """Every direct requirement is pinned in *lock* to an allowed version."""
    specifiers = pytest.importorskip("packaging.requirements")
    pins = _read_lock(lock)
    for raw in requirements:
        req = specifiers.Requirement(raw.split("#", 1)[0].strip())
        name = re.sub(r"[-_.]+", "-", req.name).lower()
        assert name in pins, f"{req.name} missing from {lock.name}"
        assert req.specifier.contains(pins[name], prereleases=True), (
            f"{lock.name}: {name}=={pins[name]} violates '{raw}'"
            " (run tools/update_python_locks.sh)"
        )


def test_scripts_lock_covers_runtime_and_dev_dependencies() -> None:
    """scripts/requirements-lock.txt pins every runtime and dev dependency."""
    project = _pyproject()["project"]
    _assert_lock_covers(
        _SCRIPTS / "requirements-lock.txt",
        list(project["dependencies"]) + list(project["optional-dependencies"]["dev"]),
    )


def test_guiservice_lock_covers_requirements_txt() -> None:
    """guiservice/requirements-lock.txt pins every guiservice requirement."""
    reqs = [
        line.split("#", 1)[0].strip()
        for line in (_REPO / "guiservice" / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    _assert_lock_covers(
        _REPO / "guiservice" / "requirements-lock.txt", [r for r in reqs if r]
    )


def test_read_lock_rejects_unhashed_pins(tmp_path: Path) -> None:
    """A pin without ``--hash`` lines fails the lock check."""
    lock = tmp_path / "lock.txt"
    lock.write_text("a==1.0 \\\n    --hash=sha256:00\nb==2.0\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="unhashed"):
        _read_lock(lock)
