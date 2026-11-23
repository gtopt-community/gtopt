# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The Python naming-dialect data comes from the package, never a share dir.

Mirror of the C++ ``test_dialects_resolution.cpp`` regression: a stale
``naming_dialects.json`` in ``$HOME/.local/share/gtopt`` or
``$XDG_DATA_HOME/gtopt`` (lacking the ``annual_loss`` rename) must not
change what the Python tools see.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import gtopt_shared
from gtopt_shared import json_contracts as jc

_STALE = {
    "version": 1,
    "aliases": [
        {"class": "generator", "canonical": "pmax", "alt": "max_power"},
    ],
}


@pytest.fixture(name="stale_home")
def fixture_stale_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    for share in (tmp_path / ".local" / "share", tmp_path / "xdg"):
        (share / "gtopt").mkdir(parents=True)
        (share / "gtopt" / "naming_dialects.json").write_text(
            json.dumps(_STALE), encoding="utf-8"
        )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    jc.load_snapshot.cache_clear()
    yield tmp_path
    jc.load_snapshot.cache_clear()


def test_stale_share_dir_does_not_shadow_package_data(stale_home: Path) -> None:
    snap = jc.load_snapshot()
    aliases = snap["dialect_aliases"]
    assert "annual_loss" in aliases
    assert "battery_annual_loss" in aliases
    assert jc.find_schema_problems(
        {"system": {"battery_array": [{"annual_loss": 0.1}]}}
    ), "annual_loss must still be reported as an alias"
    pkg_dir = Path(gtopt_shared.__file__).resolve().parent
    snapshot = jc.snapshot_path()
    assert snapshot.is_relative_to(pkg_dir)
    assert not snapshot.is_relative_to(stale_home)


def test_packaged_aliases_match_source_dictionary() -> None:
    """The packaged alias snapshot equals the in-tree ``share/gtopt`` file."""
    root = jc.find_repo_root()
    if root is None:  # installed wheel: no source tree to compare with
        pytest.skip("not running from a source checkout")
    dialects = root / "share" / "gtopt" / "naming_dialects.json"
    jc.load_snapshot.cache_clear()
    assert jc.load_snapshot()["dialect_aliases"] == jc.parse_dialect_aliases(dialects)
