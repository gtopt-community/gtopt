# SPDX-License-Identifier: BSD-3-Clause
"""Fast checks for bundled package data (gtopt_shared.resources).

The slow end-to-end counterpart, which builds and installs a real wheel,
is ``test_wheel_package_data.py`` (``pytest -m wheel``).
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

import pytest

from gtopt_shared import resources
from gtopt_shared.cen_cogen import (
    is_cen_cogen,
    load_cen_cogen_reference,
    read_cen_cogen_csv,
)
from gtopt_shared.resources import (
    REQUIRED_DATA_FILES,
    MissingDataFileError,
    missing_required_data_files,
    package_data_dir,
    package_data_file,
)

SCRIPTS_DIR = Path(__file__).resolve().parents[2]

#: Runtime data directories inside a package; every file in them must be
#: covered by ``[tool.setuptools.package-data]``.
_DATA_DIR_NAMES = ("data", "solvers", "templates")


def test_all_required_data_files_present() -> None:
    assert not missing_required_data_files()


def test_missing_file_raises_with_reinstall_hint() -> None:
    with pytest.raises(MissingDataFileError, match="pip install") as exc:
        package_data_file("gtopt_shared", "data", "nope.json", purpose="test")
    assert isinstance(exc.value, FileNotFoundError)
    assert "(test)" in str(exc.value)


def test_missing_dir_and_package_raise() -> None:
    with pytest.raises(MissingDataFileError):
        package_data_dir("gtopt_shared", "no_such_dir")
    with pytest.raises(MissingDataFileError, match="cannot locate package"):
        package_data_file("no_such_gtopt_package_xyz", "x.txt")


def test_package_data_dir_resolves() -> None:
    assert package_data_dir("plexos2gtopt", "solvers").is_dir()


def _package_data_globs() -> dict[str, list[str]]:
    with open(SCRIPTS_DIR / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    return cfg["tool"]["setuptools"]["package-data"]


def _covered(rel_to_scripts: Path, globs: dict[str, list[str]]) -> bool:
    parts = rel_to_scripts.parts
    for key, patterns in globs.items():
        pkg_parts = tuple(key.split("."))
        if parts[: len(pkg_parts)] != pkg_parts:
            continue
        rel = "/".join(parts[len(pkg_parts) :])
        # setuptools globs do not cross "/" with "*"; mimic that.
        if any(
            fnmatch.fnmatch(rel, pat) and rel.count("/") == pat.count("/")
            for pat in patterns
        ):
            return True
    return False


def test_every_runtime_data_file_is_declared_package_data() -> None:
    """Static guard: a new file under <pkg>/{data,solvers,templates}/ must
    be matched by a package-data glob, or wheels silently drop it."""
    globs = _package_data_globs()
    uncovered: list[str] = []
    for pkg_init in SCRIPTS_DIR.glob("*/__init__.py"):
        pkg_dir = pkg_init.parent
        for name in _DATA_DIR_NAMES:
            data_dir = pkg_dir / name
            if not data_dir.is_dir():
                continue
            for f in data_dir.rglob("*"):
                if (
                    not f.is_file()
                    or f.suffix in (".py", ".pyc")
                    or "__pycache__" in f.parts
                ):
                    continue
                rel = f.relative_to(SCRIPTS_DIR)
                if not _covered(rel, globs):
                    uncovered.append(str(rel))
    assert not uncovered, f"not declared as package-data: {uncovered}"


def test_required_list_matches_package_data_globs() -> None:
    globs = _package_data_globs()
    for package, rel in REQUIRED_DATA_FILES:
        assert _covered(Path(package, rel), globs), f"{package}/{rel}"


def test_cen_cogen_reference_loads() -> None:
    names, prefixes = load_cen_cogen_reference()
    assert len(names) + len(prefixes) > 0
    assert "CMPC_BUCALEMU" in prefixes
    assert is_cen_cogen("cmpc_bucalemu_2")
    assert not is_cen_cogen("")
    assert not is_cen_cogen("DEFINITELY_NOT_A_COGEN_UNIT")


def test_cen_cogen_empty_csv_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.csv"
    p.write_text("name_upper,kind,source,tech\n")
    with pytest.raises(ValueError, match="no entries"):
        read_cen_cogen_csv(p)


def test_cen_cogen_missing_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    load_cen_cogen_reference.cache_clear()
    monkeypatch.setattr(resources, "_package_dir", lambda _pkg: tmp_path)
    try:
        with pytest.raises(MissingDataFileError, match="cen_chile_cogen.csv"):
            load_cen_cogen_reference()
    finally:
        load_cen_cogen_reference.cache_clear()
