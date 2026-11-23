# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for plexos2gtopt._defaults bundled-file resolution (portability).

Regression guards for two historical bugs:

* a hardcoded ``/home/<user>/.../share/gtopt`` candidate that only
  resolved on one developer machine, and
* the repo-root ``share/gtopt/emissions`` lookup that silently returned
  ``None`` (→ fall back to the IPCC defaults) in wheel installs.

The CEN-Chile overlay now ships as ``gtopt_shared`` package data and a
missing file is a hard error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gtopt_shared
from gtopt_shared import resources
from gtopt_shared.country import load_country
from gtopt_shared.resources import MissingDataFileError
from plexos2gtopt._defaults import resolve_default_emissions_file


def test_resolve_default_emissions_file_is_package_data() -> None:
    """The CEN-Chile emissions file resolves inside the gtopt_shared package."""
    path = resolve_default_emissions_file()
    assert path.is_file()
    assert path.name == "cen_chile.json"
    pkg_dir = Path(gtopt_shared.__file__).resolve().parent
    assert path.resolve().is_relative_to(pkg_dir), (
        f"{path} must resolve inside the gtopt_shared package {pkg_dir}"
    )


def test_resolve_default_emissions_file_missing_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken install (file absent) raises instead of falling back."""
    load_country("cl")  # country file read before the install "breaks"
    monkeypatch.setattr(resources, "_package_dir", lambda _pkg: tmp_path)
    with pytest.raises(MissingDataFileError, match="cen_chile.json"):
        resolve_default_emissions_file()
